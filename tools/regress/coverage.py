"""``./build regress --coverage``: merge each IP's line, toggle and
functional coverage, report it and check its floors.

Each passing run of a coverage build leaves ``coverage.dat`` (Verilator line
and toggle points) and, when its test samples a covergroup, ``funcov.json``
(:mod:`regress.funcov`) in its test directory. Per IP, ``verilator_coverage``
merges the ``.dat`` files and this module sums the functional bins, into
``workdir/<ip>/coverage/``. See wiki/tools/regress/SPEC.md#coverage.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from build import sim
from regress import funcov
from regress.funcov import CoverageError

METRICS = ("line", "toggle", "functional")
# Verilator point type (the `t` field of a coverage.dat key) -> metric.
POINT_TYPES = {"line": "line", "branch": "line", "toggle": "toggle"}
COVERAGE_DIR = "coverage"  # workdir/<ip>/coverage/
MERGE_TIMEOUT_S = 600.0


def find_tool(verilator: Path) -> Path:
    """``verilator_coverage``, installed next to ``verilator``."""
    tool = Path(verilator).parent / "verilator_coverage"
    if not tool.is_file():
        raise CoverageError(f"verilator_coverage not found next to {verilator}")
    return tool


def parse_dat(path: Path) -> dict[str, int]:
    """Point key -> hit count of a Verilator ``coverage.dat``."""
    points: dict[str, int] = {}
    for n, line in enumerate(Path(path).read_text(errors="replace").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        key, sep, count = line.rpartition("' ")
        if not line.startswith("C '") or not sep or not count.strip().isdigit():
            raise CoverageError(f"{path}:{n}: not a coverage point")
        points[key[3:]] = points.get(key[3:], 0) + int(count)
    return points


def point_type(key: str) -> str | None:
    """The ``t`` field of a point key (fields are \\x01name\\x02value)."""
    for item in key.split("\x01"):
        name, sep, value = item.partition("\x02")
        if sep and name == "t":
            return value
    return None


def code_metrics(points: dict[str, int]) -> dict[str, dict]:
    """Covered and total points per code metric."""
    out = {m: {"covered": 0, "total": 0} for m in ("line", "toggle")}
    for key, count in points.items():
        metric = POINT_TYPES.get(point_type(key) or "")
        if metric:
            out[metric]["total"] += 1
            out[metric]["covered"] += count > 0
    return out


def pct(covered: int, total: int) -> float | None:
    return None if total == 0 else 100.0 * covered / total


def merge_dat(tool: Path, inputs: list[Path], out: Path) -> None:
    """Merge with ``verilator_coverage --write``; also write an lcov
    ``.info`` for annotation tools."""
    cmd = [str(tool), "--write", str(out), "--write-info", str(out.with_suffix(".info"))]
    try:
        proc = subprocess.run(cmd + [str(p) for p in inputs], capture_output=True, text=True,
                              timeout=MERGE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CoverageError(f"verilator_coverage failed: {exc}") from None
    if proc.returncode != 0 or not out.is_file():
        raise CoverageError(f"verilator_coverage exited with {proc.returncode}: "
                            f"{(proc.stderr or proc.stdout).strip()[:500]}")


def merge_ip(ip_dir: Path, test_dirs: list[Path], tool: Path | None) -> dict:
    """Merge one IP's runs into ``ip_dir`` (replaced) and return its entry."""
    shutil.rmtree(ip_dir, ignore_errors=True)
    ip_dir.mkdir(parents=True, exist_ok=True)
    dats = [d / sim.COVERAGE_DAT for d in test_dirs if (d / sim.COVERAGE_DAT).is_file()]
    fcov = [d / sim.FUNCOV_JSON for d in test_dirs if (d / sim.FUNCOV_JSON).is_file()]
    entry: dict = {"runs": len(test_dirs), "dat_files": len(dats), "funcov_files": len(fcov)}
    code = {m: {"covered": 0, "total": 0} for m in ("line", "toggle")}
    if dats:
        if tool is None:
            raise CoverageError("verilator_coverage is needed to merge coverage.dat files")
        merged = ip_dir / "merged.dat"
        merge_dat(tool, dats, merged)
        code = code_metrics(parse_dat(merged))
    for m in ("line", "toggle"):
        entry[m] = {**code[m], "pct": _round(pct(code[m]["covered"], code[m]["total"]))}
    merged_f = funcov.merge([funcov.load(p) for p in fcov])
    fsum = funcov.summarize(merged_f)
    (ip_dir / "functional.json").write_text(
        json.dumps({"schema": funcov.SCHEMA, "groups": merged_f, "summary": fsum},
                   indent=1, sort_keys=True) + "\n")
    entry["functional"] = {"covered": fsum["covered"], "total": fsum["total"],
                           "pct": _round(pct(fsum["covered"], fsum["total"])),
                           "uncovered": len(fsum["uncovered"])}
    return entry


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def check_floors(entry: dict, floors: dict[str, tuple[float, str]]) -> list[str]:
    """Failure messages for every floor ``entry`` misses. ``floors`` maps a
    metric to (percent, the level file that set it). A metric with no points
    misses any floor."""
    failures = []
    for metric in METRICS:
        if metric not in floors:
            continue
        floor, where = floors[metric]
        m = entry[metric]
        value = pct(m["covered"], m["total"])
        if value is None:
            failures.append(f"{metric} has no coverage points, below its floor of {floor:g}% ({where})")
        elif value < floor:
            failures.append(f"{metric} {value:.2f}% ({m['covered']}/{m['total']}) is below its floor "
                            f"of {floor:g}% ({where})")
    return failures


def effective_floors(level_floors: dict[int, dict[str, float]], levels: list[int],
                     names: dict[int, str]) -> dict[str, tuple[float, str]]:
    """The highest floor per metric over the selected levels."""
    out: dict[str, tuple[float, str]] = {}
    for level in sorted(levels):
        for metric, value in level_floors.get(level, {}).items():
            if metric not in out or value > out[metric][0]:
                out[metric] = (value, names[level])
    return out


def report_lines(cov: dict) -> list[str]:
    """The summary table, one row per IP."""
    def cell(m):
        return "n/a" if m["pct"] is None else f"{m['pct']:.1f}% ({m['covered']}/{m['total']})"
    rows = [["coverage", *METRICS, "floors"]]
    for ip, e in sorted(cov["ips"].items()):
        floors = "none" if not e["floors"] else ("FAIL" if e["failures"] else "ok")
        rows.append([ip, *(cell(e[m]) for m in METRICS), floors])
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
