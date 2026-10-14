"""Verilator lint: ``--lint-only -Wall`` per IP, with reviewed waivers.

For each IP the sources, include directories and defines come from the
resolved ``rtl/<name>.rtl.f``. The top is ``<prefix><ip>`` (``a/b`` ->
``<prefix>a_b``) when a module of that name is declared in those sources;
otherwise Verilator picks it (several candidates raise MULTITOP). Each
``fpga/*.sv`` board wrapper is linted again with the IP's sources plus the
wrapper, with the wrapper (named after its file) as top.

Before each lint run, ``verilator -E`` with the same arguments shows what
Verilator actually reads; that output is audited (``audit_preprocessed``)
so no file outside ``rtl/`` and no ``verilator_config``/metacomment waiver
can reach a lint run by any route.

Waivers live only in ``<src-root>/lint_waivers.yaml`` (see the SPEC).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from build import sim
from build.filelist import FilelistError, Resolved, resolve
from build.ip import Ip
from checks.common import SV_SUFFIXES, CheckConfigError, CheckResult, in_kind, read_prefix, rel

WAIVER_FILE = "lint_waivers.yaml"
LINT_TIMEOUT_S = 600.0

# %Warning-CODE: file:line:col: message   /   %Error: file:line:col: message
_MSG_RE = re.compile(
    r"^%(?P<sev>Warning|Error)(?:-(?P<code>[A-Z0-9_]+))?:\s*"
    r"(?:(?P<file>[^:\s][^:]*):(?P<line>\d+):(?:\d+:)?)?\s*(?P<msg>.*)$"
)


@dataclass(frozen=True)
class Message:
    severity: str  # Warning | Error
    code: str      # warning code, "" for a plain error
    file: str      # relative to the source root when below it, else as printed
    line: int
    text: str

    def render(self) -> str:
        where = f"{self.file}:{self.line}: " if self.file else ""
        tag = f"{self.severity}-{self.code}" if self.code else self.severity
        return f"{where}{tag}: {self.text}"


@dataclass
class Waiver:
    file: str
    rule: str
    reason: str
    line: int | None = None
    match: str | None = None
    used: int = 0

    def covers(self, m: Message) -> bool:
        return (m.severity == "Warning" and m.code == self.rule and m.file == self.file
                and (self.line is None or m.line == self.line)
                and (self.match is None or self.match in m.text))


def load_waivers(src_root: Path) -> list[Waiver]:
    """Read ``<src_root>/lint_waivers.yaml``; a missing file means none."""
    path = Path(src_root) / WAIVER_FILE
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise CheckConfigError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(data, dict) or set(data) != {"waivers"} or not isinstance(data["waivers"], list):
        raise CheckConfigError(f"{path}: top level must be a mapping with only a 'waivers' list")
    out = []
    for i, raw in enumerate(data["waivers"]):
        where = f"{path}: waivers[{i}]"
        if not isinstance(raw, dict):
            raise CheckConfigError(f"{where}: must be a mapping")
        unknown = set(raw) - {"file", "rule", "reason", "line", "match"}
        if unknown:
            raise CheckConfigError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
        for key in ("file", "rule", "reason"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                raise CheckConfigError(f"{where}: '{key}' must be a non-empty string")
        if not re.fullmatch(r"[A-Z0-9_]+", raw["rule"]):
            raise CheckConfigError(f"{where}: 'rule' must be a Verilator warning code")
        line, match = raw.get("line"), raw.get("match")
        if line is None and match is None:
            raise CheckConfigError(f"{where}: give 'line' or 'match' (a signal or message text)")
        if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
            raise CheckConfigError(f"{where}: 'line' must be a positive integer")
        if match is not None and (not isinstance(match, str) or not match):
            raise CheckConfigError(f"{where}: 'match' must be a non-empty string")
        out.append(Waiver(raw["file"], raw["rule"], raw["reason"].strip(), line, match))
    return out


def parse_output(output: str, src_root: Path) -> list[Message]:
    msgs = []
    for line in output.splitlines():
        m = _MSG_RE.match(line)
        if m is None:
            if line.startswith(("%Warning", "%Error")):  # never drop a message
                msgs.append(Message("Error", "", "", 0, f"unparsed Verilator output: {line}"))
            continue
        if m.group("msg").startswith("Exiting due to"):
            continue
        f = m.group("file") or ""
        if f:
            f = rel(Path(f), src_root)
        msgs.append(Message(m.group("sev"), m.group("code") or "", f,
                            int(m.group("line") or 0), m.group("msg").strip()))
    return msgs


_MODULE_RE = re.compile(r"^\s*module\s+(?:(?:automatic|static)\s+)?(\w+)", re.MULTILINE)


def declared_modules(sources: list[Path]) -> set[str]:
    names: set[str] = set()
    for s in sources:
        names.update(_MODULE_RE.findall(s.read_text(errors="replace")))
    return names


def command(verilator: Path, flat: Resolved, extra: list[Path], top: str | None) -> list[str]:
    cmd = [str(verilator), "--lint-only", "-Wall", "-Wno-fatal"]
    if top:
        cmd += ["--top-module", top]
    cmd += [f"+incdir+{d}" for d in flat.incdirs]
    cmd += [f"-D{k}={'' if v is None else v}" for k, v in flat.defines.items()]
    return cmd + [str(s) for s in flat.sources + extra]


def preprocess_command(verilator: Path, flat: Resolved, extra: list[Path]) -> list[str]:
    """``verilator -E`` with the same include dirs, defines and sources."""
    cmd = [str(verilator), "-E"]
    cmd += [f"+incdir+{d}" for d in flat.incdirs]
    cmd += [f"-D{k}={'' if v is None else v}" for k, v in flat.defines.items()]
    return cmd + [str(s) for s in flat.sources + extra]


_LINE_RE = re.compile(r'^`line\s+\d+\s+"(?P<file>[^"]*)"\s+\d+\s*$')
_METACOMMENT_RE = re.compile(r"/\*\s*(verilator\b.*?)\*/", re.IGNORECASE | re.DOTALL)


def audit_preprocessed(output: str, src_root: Path, allowed_extra: tuple[Path, ...] = ()
                       ) -> list[str]:
    """Check Verilator's own view of a lint target (``verilator -E``): every
    file it entered (per `` `line`` markers) must be inside the source root
    and below an ``rtl/`` directory (or be one of ``allowed_extra``, the
    board wrapper), and the text must hold no `` `verilator_config`` and no
    ``/*verilator <directive>*/`` metacomment. ``-E`` turns every comment
    that starts with ``verilator`` into such a metacomment, whatever file or
    include route it came from."""
    from checks.style import _PRAGMA_RE  # one directive list for both checks

    src_root = Path(src_root).resolve()
    allowed = {Path(p).resolve() for p in allowed_extra}
    out: list[str] = []
    entered: dict[Path, None] = {}
    current = "?"
    for line in output.splitlines():
        m = _LINE_RE.match(line)
        if m:
            path = Path(m.group("file")).resolve()
            entered.setdefault(path)
            current = rel(path, src_root)
            continue
        if re.search(r"`verilator_config\b", line):
            out.append(f"preprocessed input from {current} contains `verilator_config")
        for c in _METACOMMENT_RE.finditer(line):
            if _PRAGMA_RE.match(c.group(1).strip()):
                out.append(f"preprocessed input from {current} contains "
                           f"/*{c.group(1).strip()[:60]}*/")
    for path in entered:
        if path in allowed:
            continue
        if not in_kind(path, src_root, ("rtl",)):
            out.append(f"verilator reads {rel(path, src_root)}, which is not below rtl/ "
                       "in the source root")
    return list(dict.fromkeys(out))


@dataclass(frozen=True)
class Target:
    """One Verilator lint run."""

    label: str
    flat: Resolved
    extra: tuple[Path, ...]
    top: str | None


def targets(ip: Ip, flat: Resolved, prefix: str | None) -> list[Target]:
    expected = (prefix or "") + ip.id
    top = expected if expected in declared_modules(flat.sources) else None
    out = [Target(ip.id, flat, (), top)]
    fpga = ip.dir("fpga")
    if fpga.is_dir():
        for w in sorted(fpga.glob("*.sv")):
            out.append(Target(f"{ip.id} + fpga/{w.name}", flat, (w,), w.stem))
    return out


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> CheckResult:
    waivers = load_waivers(src_root)
    prefix = read_prefix(src_root)
    chosen = [ip for ip in ips if ip_ids is None or ip.id in ip_ids]
    result = CheckResult("lint")
    plan: list[Target] = []
    for ip in chosen:
        try:
            flat = resolve(ip.rtl_f)
        except FilelistError as exc:
            result.findings.append(f"{ip.id}: rtl filelist does not resolve: {exc}")
            continue
        foreign = [s for s in flat.sources if not s.name.endswith(SV_SUFFIXES)]
        if foreign:  # e.g. a .vlt control file would waive outside the waiver file
            result.findings.append(
                f"{ip.id}: not linted: rtl filelist lists non-.sv/.svh source(s): "
                + ", ".join(rel(s, src_root) for s in foreign))
            continue
        outside = [d for d in flat.incdirs if not in_kind(d, src_root, ("rtl",))]
        if outside:  # files included from there escape the rtl/ allowlist
            result.findings.append(
                f"{ip.id}: not linted: rtl filelist includes from outside rtl/: "
                + ", ".join(rel(d, src_root) for d in outside))
            continue
        plan += targets(ip, flat, prefix)
    verilator = None
    if plan:
        verilator = sim.find_verilator()
        if verilator is None:
            raise sim.SimError(sim.not_found_message())
    waived = 0
    for t in plan:
        cmd = command(verilator, t.flat, list(t.extra), t.top)
        print(f"[lint] {t.label}: top={t.top or '(verilator picks)'}", flush=True)
        try:
            pre = subprocess.run(preprocess_command(verilator, t.flat, list(t.extra)),
                                 capture_output=True, text=True, timeout=LINT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            result.findings.append(f"{t.label}: verilator -E timed out after {LINT_TIMEOUT_S:.0f}s")
            continue
        if pre.returncode != 0:
            result.findings.append(f"{t.label}: verilator -E exited {pre.returncode}: "
                                   + (pre.stderr.strip().splitlines() or [""])[0])
        result.findings += [f"{t.label}: {x}" for x in
                            audit_preprocessed(pre.stdout, src_root, t.extra)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=LINT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            result.findings.append(f"{t.label}: verilator timed out after {LINT_TIMEOUT_S:.0f}s")
            continue
        msgs = parse_output(proc.stdout + "\n" + proc.stderr, src_root)
        for m in msgs:
            hit = next((w for w in waivers if w.covers(m)), None)
            if hit is not None:
                hit.used += 1
                waived += 1
                continue
            result.findings.append(f"{t.label}: {m.render()}")
        if proc.returncode != 0 and not any(m.severity == "Error" for m in msgs):
            result.findings.append(f"{t.label}: verilator exited {proc.returncode}")
    if ip_ids is None:  # a filtered run cannot tell whether a waiver is stale
        for w in waivers:
            if not w.used:
                result.findings.append(
                    f"{WAIVER_FILE}: stale waiver {w.file} {w.rule}"
                    f"{f' line {w.line}' if w.line else ''}{f' {w.match!r}' if w.match else ''}"
                    ": matches no warning")
    result.checked = (f"{len(chosen)} IP(s), {len(plan)} Verilator run(s) with -E audit, "
                      f"{len(waivers)} waiver(s), {waived} warning(s) waived")
    return result
