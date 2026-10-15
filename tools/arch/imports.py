"""Import scan: an architecture model never imports DV, RTL or tools code.

The arch BKM's dependency rule (DV may import arch, never the reverse) is
checked statically: every ``*.py`` under ``src/arch/<ip>/`` is parsed and
each imported top-level module name is compared with

- ``tools`` and ``src`` themselves (``import tools.x``, ``from src.dv ...``),
- the tools' top-level importable names: ``tools/<name>.py``, every folder
  ``tools/<name>/`` (a package or not), and the modules directly in a tool
  folder without ``__init__.py``, which run with that folder on ``sys.path``
  (``tools/<tool>/<module>.py``),
- the Python modules and packages under every other kind folder of the
  source tree (``src/<kind>/...`` for each kind but ``arch``: DV models and
  tests, and any Python beside RTL or flow inputs),
- the DV frameworks (``DV_FRAMEWORKS``).

A name that is also a sibling module in the same ``arch/<ip>/`` folder is
the sibling (it is first on ``sys.path``) and is allowed, and so is a
standard-library name. Imports by
``importlib.import_module`` or ``__import__`` with a constant name count too.
See wiki/tools/arch/SPEC.md#import-rule.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from build.ip import Ip

ARCH = "arch"
TOOLS_ROOT = Path(__file__).resolve().parents[1]
DV_FRAMEWORKS = ("cocotb", "pyuvm")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    module: str
    origin: str  # "tools", "<kind>" (a source kind folder), "DV framework" or "syntax"


def _modules(root: Path) -> set[str]:
    """Top-level importable names anywhere below ``root``: ``*.py`` stems and
    package folders (with ``__init__.py``)."""
    names = set()
    for p in root.rglob("*.py") if root.is_dir() else ():
        if "__pycache__" in p.parts:
            continue
        names.add(p.parent.name if p.name == "__init__.py" else p.stem)
    return names


def _tool_modules(tools_root: Path) -> set[str]:
    names = {"tools"}
    for p in sorted(tools_root.iterdir()) if tools_root.is_dir() else ():
        if p.suffix == ".py" and p.stem != "conftest":
            names.add(p.stem)
        elif p.is_dir() and not p.name.startswith((".", "_")):
            names.add(p.name)
            if not (p / "__init__.py").is_file():  # a script folder: its modules are top-level
                names.update(m.stem for m in p.glob("*.py") if m.stem != "conftest")
    return names


def forbidden_names(src_root: Path, tools_root: Path = TOOLS_ROOT) -> dict[str, str]:
    """``{module name: origin}`` an arch model must not import."""
    out = {name: "DV framework" for name in DV_FRAMEWORKS}
    out["src"] = "src"
    stdlib = set(sys.stdlib_module_names)  # resolves to the standard library first
    for kind_dir in sorted(Path(src_root).iterdir()) if Path(src_root).is_dir() else ():
        if kind_dir.is_dir() and kind_dir.name != ARCH and not kind_dir.name.startswith("."):
            for name in _modules(kind_dir) - stdlib:
                out.setdefault(name, f"src/{kind_dir.name}")
    for name in _tool_modules(tools_root) - stdlib:
        out.setdefault(name, "tools")
    return out


def _imported(tree: ast.AST):
    """``(line, top-level module name)`` of every absolute import."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield node.lineno, a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module.split(".")[0]
        elif isinstance(node, ast.Call) and node.args \
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if name in ("import_module", "__import__"):
                yield node.lineno, node.args[0].value.split(".")[0]


def scan(ip: Ip, tools_root: Path = TOOLS_ROOT) -> list[Finding]:
    """Imports of DV, RTL or tools code by the Python files of ``arch/<ip>/``."""
    d = ip.dir(ARCH)
    files = sorted(p for p in d.rglob("*.py") if "__pycache__" not in p.parts) if d.is_dir() else []
    if not files:
        return []
    banned = forbidden_names(ip.src_root, tools_root)
    siblings = _modules(d)
    found = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            found.append(Finding(path, getattr(exc, "lineno", None) or 0, "", "syntax"))
            continue
        for line, name in sorted(set(_imported(tree))):
            if name in banned and name not in siblings:
                found.append(Finding(path, line, name, banned[name]))
    return found
