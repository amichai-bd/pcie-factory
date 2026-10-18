"""The nightly record: assemble whitelisted sections and scan for leaks.

Every value in the record is built field by field from a known source; no
input document is copied whole. ``forbidden`` is the last line of defence:
the record is refused if any key or string looks like an email address, an
absolute or home path, a token, a network address, or names this host or
user.
"""

from __future__ import annotations

import getpass
import json
import math
import os
import platform
import re
import socket
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from . import github
from .config import Config, MetricsError
from .tree import FileStat, TreeReader, git

SCHEMA_VERSION = 1
STEP_OUTCOMES = ("pass", "fail", "skipped")
_SAFE_STR = re.compile(r"^[A-Za-z0-9 _.,:;+()\[\]|/<>=-]{0,120}$")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:|\[\]-][A-Za-z0-9_.:|/\[\]-]{0,119}$")
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,119}$")
_VERSION = re.compile(r"\d+(?:\.\d+)+[0-9A-Za-z.+-]*")

_FORBIDDEN = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("absolute path", re.compile(r"(?:^|[\s=:\"'(,])(?:/|~/?)[\w.-]")),
    ("windows path", re.compile(r"[A-Za-z]:\\|\\\\")),
    ("token", re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{8,}")),
    ("access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{16,}")),
    ("ip address", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("ipv6 address", re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])"
                                r"|(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?"
                                r"|(?<![0-9A-Fa-f:])::(?:[0-9A-Fa-f]{1,4}:){0,6}[0-9A-Fa-f]{1,4}")),
    ("domain name", re.compile(r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|net|org|edu|gov|io|dev|ai|"
                               r"app|cloud|co|us|uk|de|il|local|lan|home|internal|intra|corp|localdomain)\b",
                               re.I)),
    ("url", re.compile(r"[a-z][a-z0-9+.-]*://", re.I)),
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- publishability scan ------------------------------------------------------

def host_terms() -> list[str]:
    """This machine's host name and user names, to keep them out of records."""
    terms = set()
    for fn in (socket.gethostname, getpass.getuser, platform.node):
        try:
            terms.add(fn())
        except Exception:  # noqa: BLE001 - best effort
            pass
    for var in ("USER", "LOGNAME", "USERNAME", "HOSTNAME"):
        if os.environ.get(var):
            terms.add(os.environ[var])
    home = os.environ.get("HOME")
    if home:
        terms.add(Path(home).name)
    out = set()
    for t in terms:
        out.add(t.lower())
        out.add(t.split(".")[0].lower())  # short host name
    return sorted(t for t in out if len(t) >= 3)


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if t}


def forbidden(record, terms: list[str] = ()) -> list[str]:
    """Problems found in any key or string of ``record``, as ``path: reason``."""
    problems = []
    term_set = {t.lower() for t in terms}

    def check(path: str, s: str) -> None:
        for reason, rx in _FORBIDDEN:
            if rx.search(s):
                problems.append(f"{path}: {reason}")
        low = s.lower()
        tokens = _tokens(s)
        # Whole tokens for short terms; for longer ones also a term joined to
        # other letters or digits (``runner01``), and any non-alphanumeric term.
        hit = {t for t in term_set if t in tokens
               or (len(t) >= 4 and any(t in tok for tok in tokens))
               or (not t.isalnum() and t in low)}
        if hit:
            problems.append(f"{path}: names this host or user")

    def walk(path: str, v) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                check(f"{path}.{k}", str(k))
                walk(f"{path}.{k}", x)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(f"{path}[{i}]", x)
        elif isinstance(v, str):
            check(path, v)

    walk("$", record)
    return problems


# --- whitelisting -------------------------------------------------------------

def safe_value(v):
    """Numbers, booleans and null pass; strings only when short and neutral."""
    if v is None or isinstance(v, bool) or isinstance(v, (int, float)):
        return True, v
    if isinstance(v, str) and _SAFE_STR.match(v) and not v.startswith("/"):
        return True, v
    return False, None


def pick(doc, fields) -> dict:
    """Copy only the dotted ``fields`` (``*`` = any one key) of ``doc``."""
    out: dict = {}

    def put(path: list[str], value) -> None:
        d = out
        for k in path[:-1]:
            d = d.setdefault(k, {})
        d[path[-1]] = value

    def walk(node, parts: list[str], path: list[str]) -> None:
        if not parts:
            keep, value = safe_value(node)
            if keep:
                put(path, value)
            return
        if not isinstance(node, dict):
            return
        head, rest = parts[0], parts[1:]
        keys = sorted(node) if head == "*" else ([head] if head in node else [])
        for k in keys:
            if isinstance(k, str) and _SAFE_KEY.match(k):
                walk(node[k], rest, path + [k])

    for f in fields:
        walk(doc, f.split("."), [])
    return out


# --- sections -----------------------------------------------------------------

def tests_section(summary_path: Path, cfg: Config) -> dict:
    if not summary_path.is_file():
        return {"status": "unavailable", "reason": "missing"}
    try:
        s = json.loads(summary_path.read_text())
        runs = []
        for t in s["tests"]:
            if t.get("extra") is True:
                continue  # extra rotated seeds: outside the series (regress SPEC, Seed rotation)
            if not (isinstance(t["ip"], str) and _NAME.match(t["ip"])
                    and isinstance(t["name"], str) and _NAME.match(t["name"])):
                raise ValueError("bad test identity")
            runs.append({"ip": t["ip"], "level": int(t["level"]), "name": t["name"],
                         "seed": int(t["seed"]), "status": str(t["status"])[:16],
                         "runtime_s": float(t["runtime_s"])})
        totals = {}
        for key, t in s["totals"].items():
            if not re.fullmatch(r"level\d", key):
                raise ValueError("bad level key")
            totals[key] = {k: t[k] for k in ("tests", "passed", "failed", "runtime_s",
                                             "budget_s", "over_budget")}
        builds = s.get("builds", [])
        levels = [int(x) for x in s["levels"]]
        passed = bool(s["passed"])
        wall = float(s["wall_s"])
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {"status": "unavailable", "reason": "invalid"}
    runs.sort(key=lambda r: (r["ip"], r["level"], r["name"], r["seed"]))
    ips: dict = {}
    for r in runs:
        b = ips.setdefault(r["ip"], {}).setdefault(
            f"level{r['level']}", {"tests": 0, "passed": 0, "failed": 0, "runtime_s": 0.0})
        b["tests"] += 1
        b["passed" if r["status"] == "pass" else "failed"] += 1
        b["runtime_s"] = round(b["runtime_s"] + r["runtime_s"], 3)
    slowest = sorted(runs, key=lambda r: (-r["runtime_s"], r["ip"], r["name"], r["seed"]))
    return {
        "status": "ok",
        "levels": levels,
        "passed": passed,
        "wall_s": wall,
        "totals": totals,
        "builds": {"total": len(builds),
                   "failed": sum(b.get("status") != "pass" for b in builds)},
        "ips": ips,
        "slowest": slowest[:cfg.slowest],
        "runs": runs,
    }


COVERAGE_METRICS = ("line", "toggle", "functional")


def coverage_section(summary_path: Path) -> dict:
    """Per-IP merged coverage from a ``regress --coverage`` summary."""
    if not summary_path.is_file():
        return {"status": "unavailable", "reason": "missing"}
    try:
        cov = json.loads(summary_path.read_text()).get("coverage")
        if cov is None:
            return {"status": "unavailable", "reason": "disabled"}
        ips = {}
        for ip, e in sorted(cov["ips"].items()):
            if not (isinstance(ip, str) and _NAME.match(ip)):
                raise ValueError("bad IP id")
            row = {}
            for m in COVERAGE_METRICS:
                pct = e[m]["pct"]
                row[m] = {"covered": int(e[m]["covered"]), "total": int(e[m]["total"]),
                          "pct": None if pct is None else float(pct)}
            row["floors"] = {m: float(e["floors"][m]) for m in COVERAGE_METRICS if m in e["floors"]}
            row["floors_passed"] = not e["failures"]
            ips[ip] = row
        passed = bool(cov["passed"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return {"status": "unavailable", "reason": "invalid"}
    return {"status": "ok", "passed": passed, "ips": ips}


def flow_section(workdir: Path, ip_ids: list[str], kind: str, fields) -> dict:
    """``workdir/<ip>/<kind>/metrics.json`` of each IP, whitelisted."""
    ips = {}
    for ip in ip_ids:
        path = workdir / ip / kind / "metrics.json"
        if not path.is_file():
            continue
        try:
            ips[ip] = {"status": "ok", **pick(json.loads(path.read_text()), fields)}
        except (OSError, ValueError):
            ips[ip] = {"status": "invalid"}
    if not ips:
        return {"status": "unavailable", "reason": "missing"}
    return {"status": "ok", "ips": ips}


ARCH_STATUSES = ("pass", "fail", "error")
_ARCH_NAME = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")  # a scenario (the arch SPEC)
_METRIC = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def _arch_ip(doc) -> dict:
    """The whitelisted part of one ``perf.json``: the IP status, and per
    scenario its status and the values of the metrics its targets name."""
    status = doc["status"]
    if status not in ARCH_STATUSES:
        raise ValueError("bad status")
    scenarios = {}
    for sc in doc["scenarios"]:
        name, st = sc["name"], sc["status"]
        if not (isinstance(name, str) and _ARCH_NAME.match(name)) or st not in ARCH_STATUSES:
            raise ValueError("bad scenario")
        targets = {}
        for t in sc.get("targets") or []:
            metric, value = t["metric"], t["value"]
            if not (isinstance(metric, str) and _METRIC.match(metric)):
                raise ValueError("bad metric name")
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                raise ValueError("bad metric value")
            targets[metric] = value
        scenarios[name] = {"status": st, "targets": dict(sorted(targets.items()))}
    return {"status": status, "scenarios": dict(sorted(scenarios.items()))}


def arch_section(workdir: Path, ip_ids: list[str]) -> dict:
    """``workdir/<ip>/arch/perf.json`` of each IP (``./build arch``), whitelisted."""
    ips = {}
    for ip in ip_ids:
        path = workdir / ip / "arch" / "perf.json"
        if not path.is_file():
            continue
        try:
            ips[ip] = _arch_ip(json.loads(path.read_text()))
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            ips[ip] = {"status": "invalid"}
    if not ips:
        return {"status": "unavailable", "reason": "missing"}
    return {"status": "ok", "ips": ips}


def _version(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = _VERSION.search(out)
    return m.group(0) if m else None


def tool_versions() -> dict:
    from build.sim import find_verilator  # noqa: PLC0415 - optional dependency
    v = find_verilator()
    out = {
        "python": platform.python_version(),
        "git": _version(["git", "--version"]),
        "gh": _version(["gh", "--version"]),
        "verilator": _version([str(v), "--version"]) if v else None,
    }
    for name, dist in (("cocotb", "cocotb"), ("pytest", "pytest"), ("pyyaml", "PyYAML")):
        try:
            out[name] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            out[name] = None
    return {k: (x if x and _VERSION.fullmatch(x) else None) for k, x in out.items()}


def file_name(start: datetime) -> str:
    """A run's record file name, `<date>T<HHMMSS>Z.json` of its start (UTC):
    one file per run, sorting in run order."""
    return f"{start.astimezone(timezone.utc):%Y-%m-%dT%H%M%SZ}.json"


def meta_section(repo: Path, now: datetime, steps: dict[str, str], versions: dict,
                 started: datetime | None = None, run: tuple = (None, None)) -> dict:
    sha = git(repo, "rev-parse", "HEAD").decode().strip()
    cdate = git(repo, "show", "-s", "--format=%cI", "HEAD").decode().strip()
    result = None
    if steps:
        result = "fail" if any(v == "fail" for v in steps.values()) else "pass"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now),
        "started_at": iso(started) if started else None,
        "run_id": run[0],
        "run_attempt": run[1],
        "date": now.strftime("%Y-%m-%d"),
        "commit": sha,
        "commit_short": sha[:12],
        "commit_date": iso(datetime.fromisoformat(cdate)),
        "tools": versions,
        "nightly": {"steps": dict(sorted(steps.items())), "result": result,
                    "duration_s": round((now - started).total_seconds(), 1) if started else None},
    }


def repo_counts(repo: Path) -> dict:
    commits = int(git(repo, "rev-list", "--count", "HEAD").decode().strip())
    authors = {line.strip().lower() for line in
               git(repo, "log", "--format=%ae", "HEAD").decode().splitlines() if line.strip()}
    return {"commits": commits, "contributors": len(authors)}


def code_section(stats: dict, counts: dict, raw: dict | None) -> dict:
    size = None
    if raw is not None and github.ok(raw, "repo"):
        size = raw["repo"].get("size")
    return {"status": "ok", **stats, **counts, "repo_size_kb": size}


def file_distributions(files: list[FileStat]) -> dict:
    return {"file_bytes": sorted(f.bytes for f in files),
            "file_lines": sorted(f.lines for f in files)}


def build_record(*, repo: Path, workdir: Path, ip_ids: list[str], cfg: Config, now: datetime,
                 raw: dict | None, steps: dict[str, str], versions: dict,
                 started: datetime | None = None, reader: TreeReader | None = None,
                 run: tuple = (None, None)) -> dict:
    """Assemble the record; ``raw`` is None when GitHub collection is off."""
    reader = reader or TreeReader(repo, cfg)
    stats, files = reader.stats("HEAD")
    tests = tests_section(workdir / "regress" / "summary.json", cfg)
    off = {"status": "unavailable", "reason": "disabled"}
    if raw is None:
        gh_sections = {"issues": off, "pulls": off, "ci": off}
        dist_gh, health = {}, off
    else:
        gh_sections = {"issues": github.issues_section(raw, cfg, now),
                       "pulls": github.pulls_section(raw, cfg, now),
                       "ci": github.ci_section(raw, cfg, now)}
        dist_gh, health = github.distributions(raw, cfg), github.health(raw, cfg, now)
    dist = {**dist_gh, **file_distributions(files),
            "test_runtime_s": sorted(r["runtime_s"] for r in tests["runs"])
            if tests["status"] == "ok" else None}
    return {
        "schema_version": SCHEMA_VERSION,
        "meta": meta_section(repo, now, steps, versions, started, run),
        "code": code_section(stats, repo_counts(repo), raw),
        "github": gh_sections,
        "health": health,
        "tests": tests,
        "coverage": coverage_section(workdir / "regress" / "summary.json"),
        "fpga": flow_section(workdir, ip_ids, "fpga", cfg.fpga_fields),
        "asic": flow_section(workdir, ip_ids, "asic", cfg.asic_fields),
        "arch": arch_section(workdir, ip_ids),
        "distributions": dict(sorted(dist.items())),
    }


def dumps(record: dict) -> str:
    return json.dumps(record, indent=1, sort_keys=True) + "\n"


def write_checked(record: dict, out: Path, terms: list[str]) -> None:
    problems = forbidden(record, terms)
    if problems:
        shown = "; ".join(problems[:5])
        raise MetricsError(f"record refused, not publishable ({len(problems)} problems): {shown}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(record))
