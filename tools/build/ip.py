"""Repository context and IP discovery.

The source tree is kind-first: ``<src_root>/<kind>/<ip>/`` with ``<kind>``
one of :data:`KINDS`. An IP is a directory ``<src_root>/rtl/<id>/`` that
holds ``<id>.rtl.f``; its id is the directory name. Every IP whose
``.rtl.f`` reaches another's ``.rtl.f`` through ``-F`` is that IP's
ancestor (it integrates it). Sub-IPs are flat siblings: an IP is a sub-IP
of each ancestor ``<parent>`` when its id is ``<parent>_<name>``, and
top-level when it has no such parent, even when another top-level IP
integrates it.
``<kind>/common/`` holds shared blocks, directly or one folder down in a
group folder (``rtl/common/<group>/``), and is never an IP; with its tests in
``dv/common/`` it is the **shared unit**, which ``sim``, ``regress`` and
``clean`` accept like an IP (:func:`units`). IP names are never hard-coded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from build.filelist import FilelistError, resolve

# tools/build/ip.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_SRC_ROOT = "BUILD_SRC_ROOT"
ENV_WORKDIR = "BUILD_WORKDIR"

# Kind folders directly under the source root (and under wiki/src/).
KINDS = ("rtl", "dv", "arch", "fpga", "asic")
# Kinds that only a top-level IP may have.
TOP_LEVEL_KINDS = ("arch", "fpga", "asic")
# Shared-block folder name under rtl/ and dv/; never an IP.
SHARED_DIR = "common"

# Directories under workdir/ that the tools own; an IP may not use these ids.
RESERVED_IP_IDS = {"regress"}
# Directory names inside workdir/<ip>/ that a test may not use.
RESERVED_TEST_NAMES = {"build", "fpga", "fpga-synth", "asic", "asic-synth", "arch", "coverage"}


@dataclass(frozen=True)
class Context:
    """Where the tools read sources and configuration and write output."""

    repo_root: Path
    src_root: Path
    workdir: Path

    @property
    def cfg_dir(self) -> Path:
        return self.repo_root / "cfg"


def make_context(src_root: str | None = None, workdir: str | None = None) -> Context:
    """Build a context from explicit values, then env vars, then defaults."""
    src = src_root or os.environ.get(ENV_SRC_ROOT) or str(REPO_ROOT / "src")
    work = workdir or os.environ.get(ENV_WORKDIR) or str(REPO_ROOT / "workdir")
    return Context(REPO_ROOT, Path(src).resolve(), Path(work).resolve())


@dataclass(frozen=True)
class Ip:
    """One IP: ``id`` names its folder under every kind."""

    id: str
    src_root: Path
    # IPs whose rtl filelist reaches this one's through -F, sorted: its
    # parents and any top-level IP that integrates it.
    ancestors: tuple[str, ...] = ()
    # The shared unit (id ``common``): not an IP, never top-level.
    shared: bool = False

    @property
    def parents(self) -> tuple[str, ...]:
        """Ancestors whose id, with ``_``, prefixes this one's: the IPs this
        is a sub-IP of."""
        return tuple(a for a in self.ancestors if self.id.startswith(a + "_"))

    @property
    def top_level(self) -> bool:
        return not self.parents and not self.shared

    def dir(self, kind: str) -> Path:
        """``<src_root>/<kind>/<id>``, whether or not it exists."""
        return self.src_root / kind / self.id

    @property
    def rtl_dir(self) -> Path:
        return self.dir("rtl")

    @property
    def dv_dir(self) -> Path:
        return self.dir("dv")

    @property
    def rtl_f(self) -> Path:
        return self.rtl_dir / f"{self.id}.rtl.f"

    @property
    def dv_f(self) -> Path:
        return self.dv_dir / f"{self.id}.dv.f"

    @property
    def sim_f(self) -> Path:
        return self.dv_dir / f"{self.id}.sim.f"

    def level_file(self, level: int) -> Path:
        return self.dv_dir / "regress" / f"level{level}.yaml"

    def owns(self, path: Path) -> bool:
        """True when ``path`` is one of this IP's kind folders or below one."""
        path = Path(path).resolve()
        return any(path == d or d in path.parents for d in map(self.dir, KINDS))

    def work_dir(self, workdir: Path) -> Path:
        return workdir / self.id


class DiscoveryError(Exception):
    pass


def locate(src_root: Path, path: Path) -> tuple[str | None, str | None]:
    """``(kind, name)`` for ``<src_root>/<kind>/<name>[/...]``: the kind
    folder and the entry directly in it (an IP or shared folder, or a stray
    file). ``(kind, None)`` for the kind folder itself; ``(None, None)``
    outside every kind folder."""
    try:
        parts = Path(path).resolve().relative_to(Path(src_root).resolve()).parts
    except ValueError:
        return None, None
    if not parts or parts[0] not in KINDS:
        return None, None
    return parts[0], (parts[1] if len(parts) > 1 else None)


def discover(src_root: Path) -> list[Ip]:
    """Return all IPs under ``src_root``, sorted by id, with their ancestors.

    A filelist that does not resolve reaches nothing here; the filelist
    check reports it."""
    src_root = Path(src_root).resolve()
    if not src_root.is_dir():
        raise DiscoveryError(f"source root not found: {src_root}")
    rtl = src_root / "rtl"
    ids = []
    for d in sorted(rtl.iterdir()) if rtl.is_dir() else ():
        if not d.is_dir() or d.name.startswith(".") or d.name == SHARED_DIR:
            continue
        if (d / f"{d.name}.rtl.f").is_file():
            if d.name in RESERVED_IP_IDS:
                raise DiscoveryError(f"IP id {d.name!r} is reserved by the tools")
            ids.append(d.name)
    reach: dict[str, set[Path]] = {}
    for i in ids:
        try:
            reach[i] = set(resolve(rtl / i / f"{i}.rtl.f").filelists)
        except FilelistError:
            reach[i] = set()
    ips = []
    for j in ids:
        own = (rtl / j / f"{j}.rtl.f").resolve()
        ips.append(Ip(j, src_root, tuple(i for i in ids if i != j and own in reach[i])))
    return ips


def shared_blocks(src_root: Path) -> list[Path]:
    """The shared blocks: every filelist directly in ``rtl/common/`` or in
    one of its group folders ``rtl/common/<group>/``, one per block, named
    after the block's top module (``<block>.rtl.f``), sorted by block name.
    Deeper filelists are not blocks (the layout check reports them)."""
    d = Path(src_root).resolve() / "rtl" / SHARED_DIR
    if not d.is_dir():
        return []
    found = [p for p in d.glob("*.rtl.f") if p.is_file()]
    found += [p for p in d.glob("*/*.rtl.f")
              if p.is_file() and not p.parent.name.startswith(".")]
    return sorted(found, key=lambda p: (block_name(p), p))


def block_name(filelist: Path) -> str:
    """``common_x`` for ``rtl/common/[<group>/]common_x.rtl.f``."""
    return Path(filelist).name[: -len(".rtl.f")]


def block_group(filelist: Path) -> str | None:
    """``<group>`` for ``rtl/common/<group>/<block>.rtl.f``, None for a block
    directly in ``rtl/common/``."""
    parent = Path(filelist).resolve().parent
    return None if parent.name == SHARED_DIR and parent.parent.name == "rtl" else parent.name


def block_dir(filelist: Path) -> str:
    """The block's folder below ``<kind>/common/``: ``<group>/`` or empty; the
    wiki places its pages at ``<kind>/common/<block_dir><block>.md``."""
    g = block_group(filelist)
    return f"{g}/" if g else ""


def shared_unit(src_root: Path) -> Ip | None:
    """The shared unit, id ``common``, when ``dv/common/common.sim.f``
    exists: its tests and regression lists live in ``dv/common/`` like an
    IP's, and ``workdir/common/`` holds its output. It is not an IP."""
    unit = Ip(SHARED_DIR, Path(src_root).resolve(), shared=True)
    return unit if unit.sim_f.is_file() else None


def units(src_root: Path) -> list[Ip]:
    """Every IP plus the shared unit when it exists, sorted by id: what
    ``sim``, ``regress`` and ``clean`` select from."""
    shared = shared_unit(src_root)
    return sorted(discover(src_root) + ([shared] if shared else []), key=lambda ip: ip.id)


def find_ip(ips: list[Ip], ip_id: str) -> Ip:
    for ip in ips:
        if ip.id == ip_id:
            return ip
    known = ", ".join(ip.id for ip in ips) or "none"
    raise DiscoveryError(f"unknown IP {ip_id!r} (known: {known})")


def owner_ip(ips: list[Ip], path: Path) -> Ip | None:
    """The IP whose kind folder (``<kind>/<id>/``) contains ``path``."""
    return next((ip for ip in ips if ip.owns(path)), None)
