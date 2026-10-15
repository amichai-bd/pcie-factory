"""Filelist and regression-list check.

- Every ``*.f`` below ``rtl/`` or ``dv/`` under the source root resolves
  (``build.filelist``), and lists no source twice after flattening.
- Every IP has ``rtl/<id>/<id>.rtl.f``, ``dv/<id>/<id>.dv.f``,
  ``dv/<id>/<id>.sim.f`` and ``dv/<id>/regress/level{0,1,2}.yaml``, and its
  regression lists validate (``regress.schema``).
- An ``rtl/`` filelist's closure holds only ``.sv``/``.svh`` sources below
  ``rtl/``, and only include directories below ``rtl/``; below ``rtl/`` only
  ``.sv``, ``.svh``, ``.f`` and ``.md`` files exist (an allowlist, since
  `` `include`` takes any name), and below ``fpga/`` no
  ``.v``/``.vh``/``.vlt``/``.vc``/``.vhd``/``.vhdl`` file. Anything else
  could carry a waiver past the style check and the reviewed waiver file.
- The shared unit: when ``rtl/common/`` holds a shared block (a
  ``<block>.rtl.f``) or ``dv/common/common.sim.f`` exists, ``dv/common/``
  has ``common.dv.f``, ``common.sim.f`` and ``regress/level{0,1,2}.yaml``,
  its regression lists validate, and its ``common.sim.f`` reaches every
  block's filelist, so every shared block is tested.
- No orphan RTL: every ``.sv``/``.svh`` below ``rtl/<name>/`` is reached by a
  filelist directly in ``rtl/<name>/`` (for an IP, its ``.rtl.f``).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from build.filelist import FilelistError, _parse_line, resolve
from build.ip import SHARED_DIR, Ip, shared_blocks, shared_unit
from checks.common import (
    FOREIGN_SUFFIXES,
    SV_SUFFIXES,
    CheckResult,
    in_kind,
    kind_files,
    rel,
    selected,
)
from regress.schema import LEVELS, SchemaError, load_floors, load_ip


def all_filelists(src_root: Path) -> list[Path]:
    """Every ``*.f`` below ``<src_root>/rtl/`` or ``<src_root>/dv/``."""
    return kind_files(src_root, ("rtl", "dv"), (".f",))


def duplicates(filelist: Path) -> list[str]:
    """Sources listed more than once across the flattened ``-F`` tree. Each
    filelist counts once even when reached twice (a diamond is fine); a
    source named by two filelists, or twice in one, is a duplicate."""
    flat = resolve(filelist)
    counts: Counter[Path] = Counter()
    for f in flat.filelists:
        for lineno, line in enumerate(f.read_text().splitlines(), start=1):
            parsed = _parse_line(line, f"{f}:{lineno}")
            if parsed and parsed[0] == "source":
                counts[(f.parent / parsed[1]).resolve()] += 1
    return [str(p) for p, n in counts.items() if n > 1]


# The only file types allowed anywhere below src/rtl/ (every IP and common/). An allowlist,
# because `include takes any file name: anything else could carry a
# `verilator_config block or a lint pragma past the style check.
RTL_ALLOWED = (".sv", ".svh", ".f", ".md")


def _home(path: Path, src_root: Path) -> Path:
    """``<src_root>/rtl/<name>``: the IP or shared folder holding ``path``."""
    parts = Path(path).resolve().relative_to(Path(src_root).resolve()).parts
    return Path(src_root).resolve() / parts[0] / parts[1]


def _shared_unit_findings(src_root: Path) -> list[str]:
    """The shared unit's files, regression lists and block coverage."""
    unit = Ip(SHARED_DIR, Path(src_root).resolve(), shared=True)  # files may be missing
    blocks = shared_blocks(src_root)
    out = []
    for need in (unit.dv_f, unit.sim_f, *(unit.level_file(lv) for lv in LEVELS)):
        if not need.is_file():
            out.append(f"{SHARED_DIR}: missing {rel(need, src_root)}")
    try:
        load_ip(unit)
        load_floors(unit)
    except SchemaError as exc:
        out.append(f"{SHARED_DIR}: regression list: {exc}")
    if unit.sim_f.is_file():
        try:
            reached = resolve(unit.sim_f)
        except FilelistError:
            return out  # reported above as a filelist that does not resolve
        for b in blocks:
            if not reached.reaches(b):
                out.append(f"{rel(b, src_root)}: shared block not reached by "
                           f"{rel(unit.sim_f, src_root)} (every shared block is tested)")
    return out


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> CheckResult:
    result = CheckResult("filelist")
    add = result.findings.append
    lists = [f for f in all_filelists(src_root) if selected(f, ips, ip_ids)]
    resolved = {}
    for f in lists:
        try:
            resolved[f] = resolve(f)
        except FilelistError as exc:
            add(f"{rel(f, src_root)}: does not resolve: {exc}")
            continue
        for dup in duplicates(f):
            add(f"{rel(f, src_root)}: lists {rel(Path(dup), src_root)} more than once after flattening")
        if in_kind(f, src_root, ("rtl",)):
            for d in resolved[f].incdirs:
                if not in_kind(d, src_root, ("rtl",)):
                    add(f"{rel(f, src_root)}: +incdir+ {rel(d, src_root)} is not below rtl/ "
                        "(a synthesizable closure includes only from rtl/)")
            for source in resolved[f].sources:
                if not in_kind(source, src_root, ("rtl",)):
                    add(f"{rel(f, src_root)}: source {rel(source, src_root)} is not below "
                        "rtl/ (a synthesizable closure lists only rtl/ files)")
                if not source.name.endswith(SV_SUFFIXES):
                    add(f"{rel(f, src_root)}: source {rel(source, src_root)} is not .sv/.svh "
                        "(synthesizable closures hold SystemVerilog only)")

    chosen = [ip for ip in ips if ip_ids is None or ip.id in ip_ids]
    for ip in chosen:
        for need in (ip.dv_f, ip.sim_f, *(ip.level_file(lv) for lv in LEVELS)):
            if not need.is_file():
                add(f"{ip.id}: missing {rel(need, src_root)}")
        try:
            load_ip(ip)
            load_floors(ip)
        except SchemaError as exc:
            add(f"{ip.id}: regression list: {exc}")

    shared = ip_ids is None and bool(shared_blocks(src_root) or shared_unit(src_root))
    if shared:
        result.findings += _shared_unit_findings(src_root)

    sources = [f for f in kind_files(src_root, ("rtl",)) if selected(f, ips, ip_ids)]
    for src in sources:
        home = _home(src, src_root)
        owners = [r for f, r in resolved.items() if f.parent == home]
        if not any(r.reaches(src) for r in owners):
            add(f"{rel(src, src_root)}: orphan: not reached by a filelist in "
                f"{rel(home, src_root)}/")

    for f in kind_files(src_root, ("rtl",), None):
        if selected(f, ips, ip_ids) and not f.name.endswith(RTL_ALLOWED):
            add(f"{rel(f, src_root)}: file type not allowed under rtl/ "
                f"(only {', '.join(RTL_ALLOWED)})")
    foreign = [f for f in kind_files(src_root, ("fpga",), FOREIGN_SUFFIXES)
               if selected(f, ips, ip_ids)]
    for f in foreign:
        add(f"{rel(f, src_root)}: {f.suffix} file not allowed under fpga/ "
            "(SystemVerilog .sv/.svh only; waivers go in the waiver file)")

    result.checked = (f"{len(lists)} filelist(s), {len(chosen)} IP(s)"
                      f"{' and the shared unit' if shared else ''} with regression lists, {len(sources)} RTL file(s) for orphans")
    return result
