"""Filelist and regression-list check.

- Every ``*.f`` below an ``rtl/`` or ``dv/`` directory under the source root
  resolves (``build.filelist``), and lists no source twice after flattening.
- Every IP has ``rtl/<n>.rtl.f``, ``dv/<n>.dv.f``, ``dv/<n>.sim.f`` and
  ``dv/regress/level{0,1,2}.yaml``, and its regression lists validate
  (``regress.schema``).
- An ``rtl/`` filelist's closure holds only ``.sv``/``.svh`` sources that
  sit below an ``rtl/`` directory, and only include directories below an
  ``rtl/`` directory; below ``rtl/`` only
  ``.sv``, ``.svh``, ``.f`` and ``.md`` files exist (an allowlist, since
  `` `include`` takes any name), and below ``fpga/`` no
  ``.v``/``.vh``/``.vlt``/``.vc``/``.vhd``/``.vhdl`` file. Anything else
  could carry a waiver past the style check and the reviewed waiver file.
- No orphan RTL: every ``.sv``/``.svh`` below an ``rtl/`` directory is reached
  by a filelist that lives in that same ``rtl/`` directory (for an IP, its
  ``.rtl.f``).
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from build.filelist import FilelistError, _parse_line, resolve
from build.ip import Ip
from checks.common import FOREIGN_SUFFIXES, SV_SUFFIXES, CheckResult, rel, rtl_files, selected
from regress.schema import LEVELS, SchemaError, load_ip


def all_filelists(src_root: Path) -> list[Path]:
    """Every ``*.f`` whose path below ``src_root`` contains an ``rtl`` or
    ``dv`` directory."""
    found = []
    for dirpath, dirs, files in os.walk(src_root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        parts = Path(dirpath).relative_to(src_root).parts
        if "rtl" in parts or "dv" in parts:
            found += [Path(dirpath) / f for f in files if f.endswith(".f")]
    return sorted(found)


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


# The only file types allowed anywhere below an rtl/ directory. An allowlist,
# because `include takes any file name: anything else could carry a
# `verilator_config block or a lint pragma past the style check.
RTL_ALLOWED = (".sv", ".svh", ".f", ".md")


def _all_files(src_root: Path, dirname: str) -> list[Path]:
    """Every file below a directory named ``dirname`` (hidden dirs skipped)."""
    found = []
    for dirpath, dirs, files in os.walk(src_root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if dirname in Path(dirpath).relative_to(src_root).parts:
            found += [Path(dirpath) / f for f in files]
    return sorted(found)


def _rtl_dir(path: Path) -> Path:
    """The nearest ancestor directory named ``rtl``."""
    for parent in path.parents:
        if parent.name == "rtl":
            return parent
    raise ValueError(path)


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
        if "rtl" in f.relative_to(src_root).parts:
            for d in resolved[f].incdirs:
                inside = d == src_root or src_root in d.parents
                if not inside or "rtl" not in d.relative_to(src_root).parts:
                    add(f"{rel(f, src_root)}: +incdir+ {rel(d, src_root)} is not under an rtl/ "
                        "directory (a synthesizable closure includes only from rtl/)")
            for source in resolved[f].sources:
                inside = src_root in source.parents
                if not inside or "rtl" not in source.relative_to(src_root).parts[:-1]:
                    add(f"{rel(f, src_root)}: source {rel(source, src_root)} is not below an "
                        "rtl/ directory (a synthesizable closure lists only rtl/ files)")
                if not source.name.endswith(SV_SUFFIXES):
                    add(f"{rel(f, src_root)}: source {rel(source, src_root)} is not .sv/.svh "
                        "(synthesizable closures hold SystemVerilog only)")

    chosen = [ip for ip in ips if ip_ids is None or ip.id in ip_ids]
    for ip in chosen:
        for need in (ip.dv_dir / f"{ip.name}.dv.f", ip.sim_f,
                     *(ip.level_file(lv) for lv in LEVELS)):
            if not need.is_file():
                add(f"{ip.id}: missing {rel(need, src_root)}")
        children = {o.path.name for o in ips if o.path.parent == ip.path}
        try:
            load_ip(ip, children)
        except SchemaError as exc:
            add(f"{ip.id}: regression list: {exc}")

    sources = [f for f in rtl_files(src_root, ("rtl",)) if selected(f, ips, ip_ids)]
    for src in sources:
        home = _rtl_dir(src)
        owners = [r for f, r in resolved.items() if f.parent == home]
        if not any(r.reaches(src) for r in owners):
            add(f"{rel(src, src_root)}: orphan: not reached by a filelist in "
                f"{rel(home, src_root)}/")

    for f in _all_files(src_root, "rtl"):
        if selected(f, ips, ip_ids) and not f.name.endswith(RTL_ALLOWED):
            add(f"{rel(f, src_root)}: file type not allowed under rtl/ "
                f"(only {', '.join(RTL_ALLOWED)})")
    foreign = [f for f in rtl_files(src_root, ("fpga",), FOREIGN_SUFFIXES)
               if selected(f, ips, ip_ids)]
    for f in foreign:
        add(f"{rel(f, src_root)}: {f.suffix} file not allowed under fpga/ "
            "(SystemVerilog .sv/.svh only; waivers go in the waiver file)")

    result.checked = (f"{len(lists)} filelist(s), {len(chosen)} IP(s) "
                      f"with regression lists, {len(sources)} RTL file(s) for orphans")
    return result
