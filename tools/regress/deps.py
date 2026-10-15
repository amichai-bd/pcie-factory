"""Python files an IP's tests import at run time, found by an import scan.

No filelist shows these dependencies: a DV test imports helpers from the
import root of ``cfg/ci.yaml`` (``import_root``, the tools on ``sys.path``
in every simulation), and an IP's DV or architecture model may import
another IP's architecture model or reference models by bare module name
(each such folder is put on ``sys.path`` by path). ``Imports.reach`` follows
those imports, transitively, from every Python file of an IP's DV and arch
folders, so ``--affected`` can select the IP when a file it reaches changes
(wiki/tools/regress/SPEC.md#selection).

Design-neutral: nothing here names an IP or a tool. Imports are read with
``ast`` (``import a.b``, ``from a.b import c``, and ``import_module`` or
``__import__`` with a constant name); relative imports resolve inside their
package. A file that does not parse contributes no imports (its own test
run reports the error).
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from build.ip import Ip

ARCH = "arch"


def _py_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def imported_names(path: Path, package: str = "") -> set[str]:
    """Dotted module names ``path`` imports. For ``from a import b`` both
    ``a`` and ``a.b`` are listed (``b`` may be a submodule). ``package`` is
    the dotted package of ``path``, for relative imports."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                parts = parts[: len(parts) - (node.level - 1)] if node.level > 1 else parts
                base = ".".join(p for p in (*parts, base) if p)
            if base:
                out.add(base)
                out.update(f"{base}.{a.name}" for a in node.names if a.name != "*")
        elif isinstance(node, ast.Call) and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if name in ("import_module", "__import__"):
                out.add(node.args[0].value)
    return out


class Imports:
    """The import graph of the import root and of every IP's flat model
    folders (``arch/<ip>/`` and ``dv/<ip>/<models>/``)."""

    def __init__(self, ips: list[Ip], import_root: Path, models: str) -> None:
        self.root = Path(import_root)
        self.dotted: dict[str, Path] = {}      # import-root module name -> file
        self.package: dict[Path, str] = {}     # import-root file -> its package
        for p in _py_files(self.root):
            rel = p.relative_to(self.root).with_suffix("")
            if "tests" in rel.parts or rel.name == "conftest":
                continue
            parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
            if parts:
                self.dotted[".".join(parts)] = p
                self.package[p] = ".".join(parts if rel.name == "__init__" else parts[:-1])
        self.bare: dict[str, set[Path]] = defaultdict(set)  # module name -> model files
        for ip in ips:
            for d in (ip.dir(ARCH), ip.dv_dir / models):
                for p in sorted(d.glob("*.py")) if d.is_dir() else ():
                    if not p.name.startswith("test_"):
                        self.bare[p.stem].add(p.resolve())
        self._names: dict[Path, set[str]] = {}

    def _resolve(self, name: str) -> set[Path]:
        out: set[Path] = set()
        parts = name.split(".")
        for i in range(1, len(parts) + 1):  # a package's __init__ runs first
            p = self.dotted.get(".".join(parts[:i]))
            if p is not None:
                out.add(p.resolve())
        out |= self.bare.get(parts[0], set())
        return out

    def _deps(self, path: Path) -> set[Path]:
        if path not in self._names:
            self._names[path] = imported_names(path, self.package.get(path, ""))
        out: set[Path] = set()
        for name in self._names[path]:
            out |= self._resolve(name)
        return out

    def reach(self, ip: Ip) -> tuple[set[Path], set[str]]:
        """Every file, outside the IP's own folders, that the Python of its
        DV and arch folders imports, directly or through other files; and
        every module name those files import (so a deleted module still
        counts)."""
        own = {p.resolve() for d in (ip.dv_dir, ip.dir(ARCH)) for p in _py_files(d)}
        seen: set[Path] = set()
        todo = list(own)
        names: set[str] = set()
        while todo:
            path = todo.pop()
            for dep in self._deps(path):
                if dep not in seen and dep not in own:
                    seen.add(dep)
                    todo.append(dep)
            names |= self._names[path]
        return seen, names

    def module_name(self, path: Path, models: str) -> str | None:
        """The name a changed file is imported by: its dotted name under the
        import root, or its stem in an arch or models folder."""
        path = Path(path)
        try:
            rel = path.relative_to(self.root).with_suffix("")
        except ValueError:
            parts = path.parts
            if path.suffix == ".py" and len(parts) >= 3 and (
                    parts[-3] == ARCH or parts[-2] == models):
                return path.stem
            return None
        if path.suffix != ".py":
            return None
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        return ".".join(parts) or None
