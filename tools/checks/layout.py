"""Source layout check: the source tree is kind-first.

Rules, owned by the layout page (wiki/project/layout.md):

- every directory directly under the source root is a kind folder
  (``rtl/``, ``dv/``, ``arch/``, ``fpga/``, ``asic/``);
- a kind folder holds only folders, each named after an IP (an
  ``rtl/<id>/<id>.rtl.f`` exists) or, under ``rtl/`` and ``dv/``, the shared
  folder ``common/``;
- ``arch/``, ``fpga/`` and ``asic/`` hold top-level IPs only;
- an IP's ``rtl/<id>/`` holds no filelist but ``<id>.rtl.f``, and its
  ``dv/<id>/`` none but ``<id>.dv.f`` and ``<id>.sim.f`` (sub-IPs are flat
  siblings, never nested);
- an IP is a sub-IP only when its id is ``<parent>_<name>`` and the
  parent's ``.rtl.f`` reaches it (:attr:`build.ip.Ip.parents`); any other IP
  is top-level, even when another IP's filelist includes it;
- an id ``<other>_<name>`` claims IP ``<other>`` as its parent, so that
  IP's ``.rtl.f`` must reach it (include it with ``-F``, or rename it).
"""

from __future__ import annotations

from pathlib import Path

from build.ip import KINDS, SHARED_DIR, TOP_LEVEL_KINDS, Ip
from checks.common import CheckResult, rel


def _filelists(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*.f")
                  if p.is_file() and not any(x.startswith(".") for x in p.relative_to(d).parts))


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> CheckResult:
    src_root = Path(src_root).resolve()
    by_id = {ip.id: ip for ip in ips}
    result = CheckResult("layout")
    add = result.findings.append
    folders = 0

    if ip_ids is None:
        for d in sorted(src_root.iterdir()):
            if d.is_dir() and not d.name.startswith(".") and d.name not in KINDS:
                add(f"{rel(d, src_root)}/: not a kind folder "
                    f"({', '.join(k + '/' for k in KINDS)}); sources live in <kind>/<ip>/")

    for kind in KINDS:
        kdir = src_root / kind
        for d in sorted(kdir.iterdir()) if kdir.is_dir() else ():
            if d.name.startswith("."):
                continue
            if ip_ids is not None and d.name not in ip_ids:
                continue
            where = rel(d, src_root)
            if not d.is_dir():
                add(f"{where}: a kind folder holds only IP folders")
                continue
            folders += 1
            if d.name == SHARED_DIR and kind in ("rtl", "dv"):
                continue
            ip = by_id.get(d.name)
            if ip is None:
                add(f"{where}/: names no IP (no rtl/{d.name}/{d.name}.rtl.f)")
                continue
            if kind in TOP_LEVEL_KINDS and not ip.top_level:
                add(f"{where}/: {kind}/ is for top-level IPs only "
                    f"({ip.id} is a sub-IP of {', '.join(ip.parents)})")
            allowed = {"rtl": {ip.rtl_f}, "dv": {ip.dv_f, ip.sim_f}}.get(kind)
            if allowed is not None:
                for f in _filelists(d):
                    if f not in allowed:
                        add(f"{rel(f, src_root)}: only "
                            f"{', '.join(sorted(p.name for p in allowed))} may live in "
                            f"{where}/ (a sub-IP is a flat sibling {kind}/{ip.id}_<sub>/)")

    for ip in ips:
        if ip_ids is not None and ip.id not in ip_ids:
            continue
        for other in ips:
            if ip.id.startswith(other.id + "_") and other.id not in ip.ancestors:
                add(f"rtl/{ip.id}/: id names {other.id} as its parent, but "
                    f"rtl/{other.id}/{other.id}.rtl.f does not reach it; include it there "
                    f"with -F ../{ip.id}/{ip.id}.rtl.f, or rename it")

    result.checked = f"{folders} IP or shared folder(s) under {', '.join(k + '/' for k in KINDS)}"
    return result
