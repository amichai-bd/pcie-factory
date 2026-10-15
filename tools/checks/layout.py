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
- ``rtl/common/`` holds filelists only directly in it or one folder down in
  a group folder ``rtl/common/<group>/``, each a shared block
  ``common_<name>.rtl.f``, and ``dv/common/`` none but ``common.dv.f`` and
  ``common.sim.f`` (the shared unit);
- with the group rules of ``cfg/checks.yaml`` (``layout.shared_groups``:
  group folder name to block-name patterns), every group folder is a listed
  group, a block in a group folder matches one of its group's patterns, and
  a block matching a group's pattern lives in that group's folder (so, for
  example, every crossing cell is in its group); the same holds for every
  ``.sv`` under ``rtl/common/`` by its file stem, and no file sits deeper
  than one group folder;
- an IP is a sub-IP only when its id is ``<parent>_<name>`` and the
  parent's ``.rtl.f`` reaches it (:attr:`build.ip.Ip.parents`); any other IP
  is top-level, even when another IP's filelist includes it;
- an id ``<other>_<name>`` claims IP ``<other>`` as its parent, so that
  IP's ``.rtl.f`` must reach it (include it with ``-F``, or rename it).
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from build.ip import KINDS, SHARED_DIR, TOP_LEVEL_KINDS, Ip, block_group, block_name
from checks.common import COMMON_PREFIX, CheckConfigError, CheckResult, rel

CONFIG = "cfg/checks.yaml"
_GROUP_NAME = re.compile(r"\A[a-z][a-z0-9_]*\Z")


def load_groups(repo_root: Path, path: str = CONFIG) -> dict[str, tuple[str, ...]]:
    """``layout.shared_groups`` from ``cfg/checks.yaml``: each shared-block
    group folder name mapped to its block-name patterns (``fnmatch``)."""
    p = Path(repo_root) / path
    try:
        data = yaml.safe_load(p.read_text())
    except OSError as exc:
        raise CheckConfigError(f"{p}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise CheckConfigError(f"{p}: invalid YAML: {exc}") from None
    c = data.get("layout") if isinstance(data, dict) else None
    if not isinstance(c, dict) or set(c) != {"shared_groups"}:
        raise CheckConfigError(f"{p}: 'layout' must have 'shared_groups' only")
    g = c["shared_groups"]
    if not isinstance(g, dict) or not g:
        raise CheckConfigError(f"{p}: layout.shared_groups must be a non-empty mapping")
    out = {}
    for name, pats in g.items():
        if not isinstance(name, str) or not _GROUP_NAME.match(name) or name == SHARED_DIR:
            raise CheckConfigError(f"{p}: layout.shared_groups: bad group folder name {name!r}")
        if not isinstance(pats, list) or not pats or not all(
                isinstance(x, str) and x.startswith(COMMON_PREFIX) for x in pats):
            raise CheckConfigError(f"{p}: layout.shared_groups.{name} must be a non-empty list "
                                   f"of block-name patterns starting with {COMMON_PREFIX!r}")
        out[name] = tuple(pats)
    return out


def _group_of(name: str, groups: dict[str, tuple[str, ...]]) -> list[str]:
    """The groups whose patterns match block ``name``."""
    return [g for g, pats in groups.items() if any(fnmatchcase(name, x) for x in pats)]


def _filelists(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*.f")
                  if p.is_file() and not any(x.startswith(".") for x in p.relative_to(d).parts))


def _shared_findings(d: Path, kind: str, src_root: Path,
                     groups: dict[str, tuple[str, ...]] | None) -> list[str]:
    """Filelist names in ``rtl/common/`` (shared blocks, directly or in a
    group folder) and ``dv/common/`` (the shared unit), and, with
    ``groups``, the group rules."""
    out = []
    for f in _filelists(d):
        if kind == "dv" and f.parent == d and f.name in (f"{SHARED_DIR}.dv.f",
                                                          f"{SHARED_DIR}.sim.f"):
            continue
        if kind == "rtl" and f.parent in (d, d / f.parent.name) and \
                f.name.startswith(COMMON_PREFIX) and f.name.endswith(".rtl.f"):
            if groups is not None:
                out += _group_findings(f, "block", block_name(f), src_root, groups)
            continue
        want = (f"{COMMON_PREFIX}<name>.rtl.f directly in it or in a group folder <group>/ "
                "(one per shared block)"
                if kind == "rtl" else f"{SHARED_DIR}.dv.f and {SHARED_DIR}.sim.f")
        out.append(f"{rel(f, src_root)}: {kind}/{SHARED_DIR}/ holds no filelist but {want}")
    if kind == "rtl":
        # Every other file: at most one group folder down, and with the
        # group rules, each source in the group its file stem matches (a
        # cell's source cannot sit apart from its block).
        for f in sorted(p for p in d.rglob("*") if p.is_file() and not p.name.endswith(".f")
                        and not any(x.startswith(".") for x in p.relative_to(d).parts)):
            if len(f.relative_to(d).parts) > 2:
                out.append(f"{rel(f, src_root)}: {kind}/{SHARED_DIR}/ holds files only directly "
                           "in it or in a group folder <group>/")
            elif groups is not None and f.suffix == ".sv":
                out += _group_findings(f, "source", f.stem, src_root, groups)
    if kind == "rtl" and groups is not None:
        listed = ", ".join(g + "/" for g in groups)
        for sub in sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if sub.name not in groups:
                out.append(f"{rel(sub, src_root)}/: not a shared-block group folder ({listed}; "
                           f"{CONFIG} layout.shared_groups)")
    return out


def _group_findings(f: Path, what: str, name: str, src_root: Path,
                    groups: dict[str, tuple[str, ...]]) -> list[str]:
    """A block filelist or a source (``what``) lives in the group folder
    whose patterns match ``name`` (the block name or the file stem), and one
    in a group folder matches that group."""
    here = block_group(f)
    want = _group_of(name, groups)
    if len(want) > 1:
        return [f"{rel(f, src_root)}: {what} {name} matches more than one group "
                f"({', '.join(want)}); fix {CONFIG} layout.shared_groups"]
    if want and here != want[0]:
        return [f"{rel(f, src_root)}: {what} {name} belongs in rtl/{SHARED_DIR}/{want[0]}/ "
                f"(it matches {', '.join(groups[want[0]])})"]
    if here is not None and here in groups and not want:
        return [f"{rel(f, src_root)}: {what} {name} does not belong in group {here}/ "
                f"(its blocks match {', '.join(groups[here])})"]
    return []


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None,
        groups: dict[str, tuple[str, ...]] | None = None) -> CheckResult:
    """``groups``: the shared-block group rules (:func:`load_groups`); None
    checks the shared-folder shape without them."""
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
                result.findings += _shared_findings(d, kind, src_root, groups)
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
