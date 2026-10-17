"""Shared-block backends: per-target implementations of one shared block.

Spec: wiki/project/layout.md#backends (files) and
wiki/tools/asic/SPEC.md#backends (selection by the ASIC flow).

A shared block ``common_x`` has its portable file, listed by
``rtl/common/[<group>/]common_x.rtl.f``, and may have backend files
``common_x_<backend>.sv`` that declare the **same module** ``common_x``. Each
backend file is listed by its backend list ``common_x.<backend>.f`` in the
same folder (the vocabulary filelists it needs, then the file). No source
holds a conditional: a flow selects a backend by naming its backend list in
the target's own source list (for example ``asic/<ip>/<platform>.backends.f``),
and :func:`apply` substitutes each backend source for the source that
declares the same module. Simulation and lint of the IP never see a backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from build.filelist import FilelistError, Resolved, resolve
from build.ip import SHARED_DIR

# "<block>.<backend>.f": not a block's own "<block>.rtl.f".
_LIST_RE = re.compile(r"\A(?P<block>[A-Za-z_][A-Za-z0-9_]*)\.(?P<backend>[a-z][a-z0-9]*)\.f\Z")
_MODULE_RE = re.compile(r"^\s*module\s+(?:automatic\s+|static\s+)?([A-Za-z_][A-Za-z0-9_$]*)",
                        re.M)
_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


class BackendError(Exception):
    """A backend list or substitution is invalid."""


@dataclass(frozen=True)
class BackendList:
    path: Path      # rtl/common/[<group>/]<block>.<backend>.f
    block: str      # the module every backend source of this list declares
    backend: str

    @property
    def source(self) -> Path:
        """The backend file this list must list: ``<block>_<backend>.sv``."""
        return self.path.parent / f"{self.block}_{self.backend}.sv"


def parse_list_name(path: Path) -> BackendList | None:
    """The block and backend of a ``<block>.<backend>.f`` path, else None
    (``rtl`` is a block's own filelist, never a backend)."""
    m = _LIST_RE.match(Path(path).name)
    if not m or m.group("backend") == "rtl":
        return None
    return BackendList(Path(path).resolve(), m.group("block"), m.group("backend"))


def backend_lists(src_root: Path) -> list[BackendList]:
    """Every backend list directly in ``rtl/common/`` or in a group folder,
    sorted by path."""
    d = Path(src_root).resolve() / "rtl" / SHARED_DIR
    if not d.is_dir():
        return []
    found = [p for p in list(d.glob("*.f")) + list(d.glob("*/*.f"))
             if p.is_file() and not p.parent.name.startswith(".")]
    return sorted((b for p in found if (b := parse_list_name(p)) is not None),
                  key=lambda b: b.path)


def declared_modules(path: Path) -> list[str]:
    """Module names declared in a source file, in order (comments ignored)."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError as exc:
        raise BackendError(f"{path}: {exc.strerror}") from None
    return _MODULE_RE.findall(_COMMENTS.sub(" ", text))


def apply(resolved: Resolved, backends_f: Path) -> tuple[Resolved, list[tuple[Path, Path]]]:
    """Substitute the sources of ``backends_f`` (a filelist, normally ``-F``
    lines naming backend lists) into ``resolved``.

    Each backend source declares exactly one module, which exactly one source
    of ``resolved`` declares; it replaces that source in place, so filelist
    order is kept. Sources already in ``resolved`` (a shared vocabulary file)
    are skipped; include directories and defines are merged. Returns the new
    ``Resolved`` and the ``(replaced, backend)`` pairs. Any other case is a
    :class:`BackendError` (two backends of one module, a source declaring
    nothing the IP has, a missing file)."""
    try:
        extra = resolve(backends_f)
    except FilelistError as exc:
        raise BackendError(f"{backends_f}: {exc}") from None
    sources = list(resolved.sources)
    declared = {s: declared_modules(s) for s in sources}
    pairs: list[tuple[Path, Path]] = []
    seen: dict[str, Path] = {}
    for src in extra.sources:
        if src in resolved.sources:
            continue
        mods = declared_modules(src)
        if len(mods) != 1:
            raise BackendError(f"{src}: a backend source declares exactly one module, "
                               f"found {len(mods)}")
        mod = mods[0]
        if mod in seen:
            raise BackendError(f"{backends_f}: two backends of module {mod}: "
                               f"{seen[mod]} and {src}")
        seen[mod] = src
        hits = [i for i, s in enumerate(sources) if mod in declared.get(s, [])]
        if len(hits) != 1:
            raise BackendError(f"{src}: backend of module {mod}, which {len(hits)} source(s) "
                               f"of the IP filelist declare (exactly one needed)")
        i = hits[0]
        if len(declared[sources[i]]) != 1:
            raise BackendError(f"{sources[i]}: declares more than module {mod}, so a backend "
                               "cannot replace it")
        pairs.append((sources[i], src))
        declared[src] = [mod]
        sources[i] = src
    incdirs = list(dict.fromkeys(list(resolved.incdirs) + list(extra.incdirs)))
    defines = dict(resolved.defines)
    for k, v in extra.defines.items():
        if k in defines and defines[k] != v:
            raise BackendError(f"{backends_f}: define {k} conflicts with the IP filelist")
        defines[k] = v
    out = Resolved(sources=sources, incdirs=incdirs, defines=defines,
                   filelists=list(dict.fromkeys(list(resolved.filelists) + list(extra.filelists))))
    return out, pairs
