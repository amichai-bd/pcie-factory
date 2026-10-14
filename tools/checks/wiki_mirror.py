"""Wiki mirror check: ``wiki/src/`` mirrors the source tree.

The wiki tree is ``<src_root>/../wiki/src`` (in the repository, ``src/`` and
``wiki/src/`` are siblings under the root). Rules, owned by the wiki BKM's
page set:

- every top-level IP (an IP directly under the source root) has
  ``arch/HAS.md``; sub-IPs have no ``arch/``;
- every IP and sub-IP has ``rtl/MAS.md`` and ``dv/DV_PLAN.md``;
- an IP with ``fpga/`` has ``fpga/FPGA.md``, one with ``asic/`` has
  ``asic/ASIC.md`` (and, by the next rule, an IP without them has neither);
- every folder under the wiki tree has a source counterpart, except a
  top-level IP's ``arch/``, whose source folder is optional for now;
- every file under the wiki tree sits in a mirror folder (``arch/``,
  ``rtl/``, ``dv/``, ``fpga/``, ``asic/``) of an IP, or below one.
"""

from __future__ import annotations

import os
from pathlib import Path

from build.ip import Ip, owner_ip
from checks.common import CheckResult

MIRROR_DIRS = ("arch", "rtl", "dv", "fpga", "asic")
# Pages required in a mirror folder; fpga/ and asic/ only when src has them.
PAGES = {"arch": "HAS.md", "rtl": "MAS.md", "dv": "DV_PLAN.md",
         "fpga": "FPGA.md", "asic": "ASIC.md"}


def wiki_root(src_root: Path) -> Path:
    """The wiki tree that mirrors ``src_root``."""
    return Path(src_root).resolve().parent / "wiki" / "src"


def is_top_level(ip: Ip) -> bool:
    return "/" not in ip.id


def required_pages(ip: Ip) -> list[str]:
    """Pages ``ip`` needs, relative to its wiki folder."""
    dirs = (["arch"] if is_top_level(ip) else []) + ["rtl", "dv"]
    dirs += [d for d in ("fpga", "asic") if (ip.path / d).is_dir()]
    return [f"{d}/{PAGES[d]}" for d in dirs]


def _mirror_dir(rel: Path, src_root: Path, ips: list[Ip]) -> tuple[Ip | None, str | None]:
    """The IP owning wiki folder ``rel`` and the mirror folder it is in (or
    below), else ``(ip, None)``."""
    ip = owner_ip(ips, src_root / rel)
    if ip is None:
        return None, None
    below = rel.parts[len(Path(ip.id).parts):]
    return ip, (below[0] if below and below[0] in MIRROR_DIRS else None)


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> CheckResult:
    src_root = Path(src_root).resolve()
    wiki = wiki_root(src_root)
    label = lambda p: Path(os.path.relpath(p, src_root.parent)).as_posix()  # noqa: E731
    result = CheckResult("wiki")
    pages = folders = 0

    def chosen(ip: Ip | None) -> bool:
        return ip_ids is None or (ip is not None and ip.id in ip_ids)

    for ip in ips:
        if not chosen(ip):
            continue
        for page in required_pages(ip):
            pages += 1
            if not (wiki / ip.id / page).is_file():
                result.findings.append(f"{label(wiki / ip.id / page)}: missing "
                                       f"(required for IP {ip.id})")

    for dirpath, dirs, files in os.walk(wiki) if wiki.is_dir() else ():
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        here = Path(dirpath)
        rel = here.relative_to(wiki)
        ip, mirror = _mirror_dir(rel, src_root, ips)
        if not chosen(ip):
            continue
        if rel.parts:
            folders += 1
            if mirror == "arch" and not is_top_level(ip):
                result.findings.append(f"{label(here)}/: arch/ is for top-level IPs only "
                                       f"({ip.id} is a sub-IP)")
                dirs[:] = []
                continue
            # A top-level IP's arch/ may precede its source folder.
            arch_exempt = mirror == "arch" and len(rel.parts) == len(Path(ip.id).parts) + 1
            if not (src_root / rel).is_dir() and not arch_exempt:
                result.findings.append(f"{label(here)}/: no source counterpart "
                                       f"{label(src_root / rel)}/")
                dirs[:] = []
                continue
        if mirror is None:
            for f in sorted(f for f in files if not f.startswith(".")):
                result.findings.append(f"{label(here / f)}: not in an "
                                       f"{', '.join(d + '/' for d in MIRROR_DIRS[:-1])} "
                                       f"or {MIRROR_DIRS[-1]}/ folder of an IP")

    scope = f"--ip {' '.join(sorted(ip_ids))}" if ip_ids is not None else "all IPs"
    result.checked = (f"{pages} required page(s), {folders} wiki folder(s) "
                      f"under {label(wiki)}/ ({scope})")
    return result
