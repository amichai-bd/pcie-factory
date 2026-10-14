"""Repository context and IP discovery.

An IP is any directory ``D`` under the source root that contains
``rtl/<basename(D)>.rtl.f``. Its id is ``D`` relative to the source root
(``a`` or ``a/b`` for a sub-IP). IP names are never hard-coded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# tools/build/ip.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[2]

ENV_SRC_ROOT = "BUILD_SRC_ROOT"
ENV_WORKDIR = "BUILD_WORKDIR"

# Directories under workdir/ that the tools own; an IP may not use these ids.
RESERVED_IP_IDS = {"regress"}
# Directory names inside workdir/<ip>/ that a test may not use.
RESERVED_TEST_NAMES = {"build", "fpga", "asic", "arch"}

# Per-IP directories that never hold sub-IPs.
_NON_IP_DIRS = {"arch", "rtl", "dv", "fpga", "asic"}


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
    id: str
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def rtl_f(self) -> Path:
        return self.path / "rtl" / f"{self.name}.rtl.f"

    @property
    def sim_f(self) -> Path:
        return self.path / "dv" / f"{self.name}.sim.f"

    @property
    def dv_dir(self) -> Path:
        return self.path / "dv"

    def level_file(self, level: int) -> Path:
        return self.path / "dv" / "regress" / f"level{level}.yaml"

    def work_dir(self, workdir: Path) -> Path:
        return workdir / self.id


class DiscoveryError(Exception):
    pass


def discover(src_root: Path) -> list[Ip]:
    """Return all IPs under ``src_root``, sorted by id."""
    src_root = Path(src_root).resolve()
    if not src_root.is_dir():
        raise DiscoveryError(f"source root not found: {src_root}")
    ips = []
    for dirpath, dirnames, _ in os.walk(src_root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in _NON_IP_DIRS
        )
        d = Path(dirpath)
        if d == src_root:
            continue
        if (d / "rtl" / f"{d.name}.rtl.f").is_file():
            ip_id = d.relative_to(src_root).as_posix()
            if ip_id in RESERVED_IP_IDS:
                raise DiscoveryError(f"IP id {ip_id!r} is reserved by the tools")
            ips.append(Ip(ip_id, d))
    return sorted(ips, key=lambda ip: ip.id)


def find_ip(ips: list[Ip], ip_id: str) -> Ip:
    for ip in ips:
        if ip.id == ip_id:
            return ip
    known = ", ".join(ip.id for ip in ips) or "none"
    raise DiscoveryError(f"unknown IP {ip_id!r} (known: {known})")


def owner_ip(ips: list[Ip], path: Path) -> Ip | None:
    """The innermost IP whose directory contains ``path``."""
    path = Path(path).resolve()
    best = None
    for ip in ips:
        if path == ip.path or ip.path in path.parents:
            if best is None or len(ip.path.parts) > len(best.path.parts):
                best = ip
    return best
