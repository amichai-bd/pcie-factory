"""Shared types and helpers for the static checks."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from build.ip import Ip, owner_ip

SV_SUFFIXES = (".sv", ".svh")
# HDL and tool-control files that must not appear in synthesizable trees:
# only .sv/.svh are style-checked, and a Verilator control file (.vlt) or
# command file would bypass the reviewed waiver file.
FOREIGN_SUFFIXES = (".v", ".vh", ".vlt", ".vc", ".vhd", ".vhdl")


class CheckConfigError(Exception):
    """A check's own configuration (cfg/checks.yaml, the waiver file) is
    invalid. Reported as a usage/configuration error (exit 2)."""


@dataclass
class CheckResult:
    """Outcome of one check: what it looked at, and every violation."""

    name: str
    checked: str = ""
    findings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.findings


def rel(path: Path, root: Path) -> str:
    """``path`` relative to ``root`` when below it, else absolute."""
    path = Path(path)
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return str(path)


# ``| Module/package/file prefix | `xy_` |`` in src/README.md
_PREFIX_RE = re.compile(r"^\|\s*Module/package/file prefix\s*\|\s*`([A-Za-z][A-Za-z0-9_]*)`",
                        re.MULTILINE)
COMMON_PREFIX = "common_"


def read_prefix(src_root: Path) -> str | None:
    """The module prefix declared in ``<src_root>/README.md``, or None."""
    readme = Path(src_root) / "README.md"
    try:
        m = _PREFIX_RE.search(readme.read_text())
    except OSError:
        return None
    return m.group(1) if m else None


def ip_stem(ip: Ip) -> str:
    """``a/b`` -> ``a_b``: the IP part of its module names."""
    return ip.id.replace("/", "_")


def rtl_files(src_root: Path, dirnames: tuple[str, ...] = ("rtl",),
              suffixes: tuple[str, ...] = SV_SUFFIXES) -> list[Path]:
    """Every file ending in one of ``suffixes`` below a directory named in
    ``dirnames`` under ``src_root`` (hidden directories skipped), sorted."""
    found = []
    for dirpath, dirs, files in os.walk(src_root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        parts = Path(dirpath).relative_to(src_root).parts
        if not any(p in dirnames for p in parts):
            continue
        found += [Path(dirpath) / f for f in files if f.endswith(suffixes)]
    return sorted(found)


def selected(path: Path, ips: list[Ip], ip_ids: set[str] | None) -> bool:
    """True when no IP filter is given, or ``path``'s owning IP is selected."""
    if ip_ids is None:
        return True
    owner = owner_ip(ips, path)
    return owner is not None and owner.id in ip_ids


def strip_comments_and_strings(text: str) -> str:
    """Blank comments and string literals, keeping line structure and length.

    Provenance: adapted from nand2anything tools/style/rtl_style.py
    (commit 085ef4b, same owner, no license file).
    """
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c == '"':
            j = i + 1
            while j < n and text[j] not in '"\n':
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append('"' + " " * max(j - i - 2, 0) + ('"' if j - i >= 2 else ""))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1
