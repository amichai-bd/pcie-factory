"""Source backends: per-flow implementations of one RTL module.

A shared block may have, beside its portable file ``<dir>/<stem>.sv`` (the
one its filelist lists), a backend file ``<dir>/<stem>_<suffix>.sv`` that
declares the same module for one synthesis flow (for example a vendor RAM
primitive). ``cfg/backends.yaml`` maps a flow kind (``fpga``, ``asic``) to
its backend suffix and to the DV-only stand-ins (``dv_models``) that let
Verilator elaborate that backend's files. No filelist lists a backend file:
the flow's source list selects it in place of the portable file, so no
source list holds two implementations of one module and no file needs an
``ifdef``. Spec: wiki/project/layout.md#source-backends.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CONFIG = "backends.yaml"
_SUFFIX_RE = re.compile(r"^[a-z][a-z0-9]*$")
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class BackendError(Exception):
    """A bad ``cfg/backends.yaml`` or a source list that breaks the rules."""


@dataclass(frozen=True)
class Backend:
    kind: str                    # the flow kind that selects it
    suffix: str                  # backend files are <stem>_<suffix>.sv
    dv_models: tuple[str, ...]   # stand-ins, relative to the source root


def load(cfg_dir: Path) -> dict[str, Backend]:
    """``cfg/backends.yaml`` as ``{kind: Backend}``; empty when absent."""
    path = Path(cfg_dir) / CONFIG
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BackendError(f"cannot read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise BackendError(f"{path}: expected a mapping of flow kind to backend")
    out: dict[str, Backend] = {}
    for kind, entry in data.items():
        where = f"{path}: {kind}"
        if not isinstance(kind, str) or not _KIND_RE.match(kind):
            raise BackendError(f"{path}: flow kind {kind!r} is not a plain name")
        if not isinstance(entry, dict) or set(entry) - {"suffix", "dv_models"}:
            raise BackendError(f"{where}: expected keys 'suffix' and optional 'dv_models'")
        suffix = entry.get("suffix")
        if not isinstance(suffix, str) or not _SUFFIX_RE.match(suffix):
            raise BackendError(f"{where}: 'suffix' must be a lowercase word, got {suffix!r}")
        models = entry.get("dv_models") or []
        if not isinstance(models, list) or not all(
                isinstance(m, str) and m.startswith("dv/") and ".." not in m for m in models):
            raise BackendError(f"{where}: 'dv_models' must list paths below dv/ in the "
                               "source root")
        out[kind] = Backend(kind, suffix, tuple(models))
    suffixes = [b.suffix for b in out.values()]
    if len(set(suffixes)) != len(suffixes):
        raise BackendError(f"{path}: two flow kinds share one backend suffix")
    return out


def portable_of(path: Path, backends: dict[str, Backend]) -> tuple[Path, Backend] | None:
    """``(<dir>/<stem>.sv, backend)`` when ``path`` is named
    ``<stem>_<suffix>.sv`` for a configured suffix and that portable file
    exists; otherwise None."""
    path = Path(path)
    if path.suffix != ".sv":
        return None
    for b in backends.values():
        tail = f"_{b.suffix}"
        if path.stem.endswith(tail) and len(path.stem) > len(tail):
            portable = path.with_name(path.stem[: -len(tail)] + ".sv")
            if portable.is_file():
                return portable, b
    return None


def backend_file(portable: Path, backend: Backend) -> Path:
    """Where ``portable``'s backend file for ``backend`` would be."""
    portable = Path(portable)
    return portable.with_name(f"{portable.stem}_{backend.suffix}.sv")


def select(sources: list[Path], backend: Backend | None,
           backends: dict[str, Backend]) -> list[Path]:
    """The flow's source list: each source with a ``backend`` file beside it
    is replaced by that file, in place. A source that is itself a backend
    file (of any flow) is an error: a filelist never lists one, so the
    result never holds two implementations of one module."""
    out: list[Path] = []
    for src in sources:
        if portable_of(src, backends) is not None:
            raise BackendError(f"{src}: a filelist lists a backend file; only a flow "
                               "selects a backend, in place of its portable file")
        alt = backend_file(src, backend) if backend is not None else None
        out.append(alt.resolve() if alt is not None and alt.is_file() else src)
    return out


def dv_models(backend: Backend, src_root: Path) -> list[Path]:
    """The backend's DV-only stand-ins, resolved; a missing one is an error."""
    out = []
    for m in backend.dv_models:
        p = (Path(src_root) / m).resolve()
        if not p.is_file():
            raise BackendError(f"cfg/{CONFIG}: {backend.kind}: dv model {m} not found")
        out.append(p)
    return out
