"""Wiki mirror check: ``wiki/src/`` mirrors the kind-first source tree.

The wiki tree is ``<src_root>/../wiki/src`` (in the repository, ``src/`` and
``wiki/src/`` are siblings under the root). Rules, owned by the wiki BKM's
page set:

- every IP has ``rtl/<id>/MAS.md`` and ``dv/<id>/DV_PLAN.md``;
- every top-level IP has ``arch/<id>/HAS.md``; no sub-IP has ``arch/<id>/``;
- an IP with ``src/fpga/<id>/`` has ``fpga/<id>/FPGA.md``, one with
  ``src/asic/<id>/`` has ``asic/<id>/ASIC.md`` (and, by the next rule, an IP
  without them has neither);
- every folder under the wiki tree is a kind folder or below one, names an
  IP directly below its kind folder, and has a source counterpart
  (``src/<kind>/<id>/...``), except a top-level IP's ``arch/<id>/`` while its
  HAS defines no targets;
- every file sits in an IP folder ``<kind>/<id>/`` or below one;
- a top-level IP whose ``arch/<id>/HAS.md`` has a ``## Targets`` section has
  ``src/arch/<id>/`` with one model ``*<id>_arch.py`` and at least one
  ``perf/*.yaml`` scenario (the arch BKM).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from arch.perf import ARCH_DIR, PERF_DIR, ArchError, find_model
from build.ip import KINDS, Ip
from checks.common import CheckResult

# The page each IP folder under a kind holds.
PAGES = {"arch": "HAS.md", "rtl": "MAS.md", "dv": "DV_PLAN.md",
         "fpga": "FPGA.md", "asic": "ASIC.md"}


def wiki_root(src_root: Path) -> Path:
    """The wiki tree that mirrors ``src_root``."""
    return Path(src_root).resolve().parent / "wiki" / "src"


# A HAS defines performance targets in a level-2 "Targets" section.
_TARGETS = re.compile(r"^##[ \t]+Targets[ \t]*$", re.M)


def defines_targets(has: Path) -> bool:
    try:
        return bool(_TARGETS.search(has.read_text(encoding="utf-8")))
    except OSError:
        return False


def arch_findings(ip: Ip, wiki: Path, label) -> list[str]:
    """Source-side arch/ rules for one top-level IP (the layout check
    rejects arch/ for a sub-IP)."""
    if not ip.top_level:
        return []
    src_arch = ip.dir(ARCH_DIR)
    has = wiki / ARCH_DIR / ip.id / PAGES[ARCH_DIR]
    if not src_arch.is_dir():
        return ([f"{label(src_arch)}/: missing ({label(has)} defines targets; "
                 f"the arch model reports against them)"] if defines_targets(has) else [])
    found = []
    try:
        find_model(ip)
    except ArchError:
        found.append(f"{label(src_arch)}/: no single model <prefix>{ip.id}_{ARCH_DIR}.py")
    if not any((src_arch / PERF_DIR).glob("*.yaml")):
        found.append(f"{label(src_arch)}/: no perf scenario {PERF_DIR}/*.yaml")
    return found


def required_pages(ip: Ip) -> list[str]:
    """Pages ``ip`` needs, relative to the wiki tree."""
    kinds = (["arch"] if ip.top_level else []) + ["rtl", "dv"]
    kinds += [k for k in ("fpga", "asic") if ip.dir(k).is_dir()]
    return [f"{k}/{ip.id}/{PAGES[k]}" for k in kinds]


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> CheckResult:
    src_root = Path(src_root).resolve()
    wiki = wiki_root(src_root)
    label = lambda p: Path(os.path.relpath(p, src_root.parent)).as_posix()  # noqa: E731
    by_id = {ip.id: ip for ip in ips}
    result = CheckResult("wiki")
    add = result.findings.append
    pages = folders = 0

    def chosen(ip_id: str | None) -> bool:
        return ip_ids is None or ip_id in ip_ids

    for ip in ips:
        if not chosen(ip.id):
            continue
        for page in required_pages(ip):
            pages += 1
            if not (wiki / page).is_file():
                add(f"{label(wiki / page)}: missing (required for IP {ip.id})")
        result.findings += arch_findings(ip, wiki, label)

    kinds = ", ".join(k + "/" for k in KINDS)
    for dirpath, dirs, files in os.walk(wiki) if wiki.is_dir() else ():
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        files = sorted(f for f in files if not f.startswith("."))
        here = Path(dirpath)
        rel = here.relative_to(wiki)
        parts = rel.parts
        ip_id = parts[1] if len(parts) > 1 else None
        if len(parts) > 1 and not chosen(ip_id):
            dirs[:] = []
            continue
        if ip_ids is not None and len(parts) < 2:
            continue  # a filtered run looks only inside the chosen IPs' folders
        if len(parts) == 1 and parts[0] not in KINDS:
            add(f"{label(here)}/: not a kind folder ({kinds})")
            dirs[:] = []
            continue
        if len(parts) < 2:
            for f in files:
                add(f"{label(here / f)}: not in an IP folder <kind>/<ip>/ ({kinds})")
            continue
        if len(parts) == 2:
            folders += 1
            ip = by_id.get(ip_id)
            if ip is None:
                add(f"{label(here)}/: names no IP (no {label(src_root / 'rtl' / ip_id)}/"
                    f"{ip_id}.rtl.f)")
                dirs[:] = []
                continue
            if parts[0] == ARCH_DIR:
                if not ip.top_level:
                    add(f"{label(here)}/: arch/ is for top-level IPs only "
                        f"({ip.id} is a sub-IP of {', '.join(ip.ancestors)})")
                    dirs[:] = []
                # A top-level IP's HAS may precede its arch model; the HAS
                # targets rule above requires the source once targets exist.
                continue
        else:
            folders += 1
        if not (src_root / rel).is_dir():
            add(f"{label(here)}/: no source counterpart {label(src_root / rel)}/")
            dirs[:] = []

    scope = f"--ip {' '.join(sorted(ip_ids))}" if ip_ids is not None else "all IPs"
    result.checked = (f"{pages} required page(s), {folders} wiki folder(s) "
                      f"under {label(wiki)}/ ({scope})")
    return result
