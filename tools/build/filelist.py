"""Verilator-style filelist (.f) resolver.

Accepted syntax, one entry per line:

    // comment                 (whole line, or trailing after an entry)
    <path>                     source file, relative to this filelist
    -F <path>                  include another filelist, relative to this one
    +incdir+<dir>              include directory, relative to this filelist
    +define+NAME[=VALUE]       preprocessor define

Anything else (other options, globs, environment variables, several entries
on one line) is rejected with an error naming ``file:line``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class FilelistError(Exception):
    """A filelist could not be parsed or resolved."""


_DEFINE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GLOB_CHARS = set("*?[]{}")


@dataclass
class Resolved:
    """Flattened result of a filelist and everything it includes."""

    sources: list[Path] = field(default_factory=list)
    incdirs: list[Path] = field(default_factory=list)
    defines: dict[str, str | None] = field(default_factory=dict)
    filelists: list[Path] = field(default_factory=list)

    def reaches(self, path: Path) -> bool:
        """True if ``path`` is a source, a filelist in the include tree, or a
        file directly inside one of the include directories."""
        path = Path(path).resolve()
        return (
            path in self.sources
            or path in self.filelists
            or path.parent in self.incdirs
        )


def _strip_comment(line: str) -> str:
    idx = line.find("//")
    if idx >= 0:
        line = line[:idx]
    return line.strip()


def _check_path_token(token: str, where: str) -> None:
    if any(c in _GLOB_CHARS for c in token):
        raise FilelistError(f"{where}: globs are not allowed: {token!r}")
    if "$" in token or token.startswith("~"):
        raise FilelistError(
            f"{where}: environment variables and '~' are not allowed: {token!r}"
        )


def _parse_line(line: str, where: str) -> tuple[str, str] | None:
    """Return (kind, value) for one line, or None for blank/comment lines."""
    text = _strip_comment(line)
    if not text:
        return None
    tokens = text.split()
    if tokens[0] == "-F":
        if len(tokens) != 2:
            raise FilelistError(f"{where}: '-F' takes exactly one path")
        _check_path_token(tokens[1], where)
        return ("filelist", tokens[1])
    tok = tokens[0]
    if tok.startswith("-") and tok != "-F":
        raise FilelistError(
            f"{where}: unsupported option {tok!r} "
            "(only -F, +incdir+ and +define+ are allowed)"
        )
    if len(tokens) != 1:
        raise FilelistError(
            f"{where}: one entry per line (paths may not contain spaces): {text!r}"
        )
    if tok.startswith("+incdir+"):
        value = tok[len("+incdir+"):]
        if not value or "+" in value:
            raise FilelistError(f"{where}: '+incdir+' takes exactly one directory")
        _check_path_token(value, where)
        return ("incdir", value)
    if tok.startswith("+define+"):
        value = tok[len("+define+"):]
        name = value.split("=", 1)[0]
        if not _DEFINE_RE.match(name) or "+" in value:
            raise FilelistError(
                f"{where}: '+define+' takes exactly one NAME[=VALUE]: {tok!r}"
            )
        return ("define", value)
    if tok.startswith("+"):
        raise FilelistError(
            f"{where}: unsupported option {tok!r} "
            "(only -F, +incdir+ and +define+ are allowed)"
        )
    _check_path_token(tok, where)
    return ("source", tok)


def resolve(filelist: Path | str) -> Resolved:
    """Parse ``filelist`` and its ``-F`` includes into a :class:`Resolved`.

    Order is depth-first in file order; duplicates keep their first
    occurrence. Raises :class:`FilelistError` on syntax errors, missing
    files, ``-F`` cycles and conflicting define values.
    """
    result = Resolved()
    root = Path(filelist).resolve()
    if not root.is_file():
        raise FilelistError(f"filelist not found: {filelist}")
    _resolve_into(root, result, stack=[])
    return result


def _resolve_into(path: Path, result: Resolved, stack: list[Path]) -> None:
    if path in stack:
        chain = " -> ".join(str(p) for p in stack + [path])
        raise FilelistError(f"-F cycle: {chain}")
    if path in result.filelists:
        return  # already flattened through another include path
    result.filelists.append(path)
    stack = stack + [path]
    base = path.parent
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        where = f"{path}:{lineno}"
        parsed = _parse_line(line, where)
        if parsed is None:
            continue
        kind, value = parsed
        if kind == "define":
            name, _, val = value.partition("=")
            val_or_none = val if "=" in value else None
            if name in result.defines and result.defines[name] != val_or_none:
                raise FilelistError(
                    f"{where}: define {name} conflicts with an earlier value "
                    f"{result.defines[name]!r}"
                )
            result.defines[name] = val_or_none
            continue
        target = (base / value).resolve()
        if kind == "filelist":
            if not target.is_file():
                raise FilelistError(f"{where}: filelist not found: {value}")
            _resolve_into(target, result, stack)
        elif kind == "incdir":
            if not target.is_dir():
                raise FilelistError(f"{where}: include directory not found: {value}")
            if target not in result.incdirs:
                result.incdirs.append(target)
        else:
            if not target.is_file():
                raise FilelistError(f"{where}: source not found: {value}")
            if target not in result.sources:
                result.sources.append(target)
