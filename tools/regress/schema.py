"""Regression list (``dv/regress/level<N>.yaml``) and ``cfg/levels.yaml``
loading and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from build.ip import RESERVED_TEST_NAMES, Ip

LEVELS = (0, 1, 2)

_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

_REQUIRED = {"name", "module", "top", "timeout_s"}
_OPTIONAL = {"testcase", "seeds", "params"}


class SchemaError(Exception):
    pass


@dataclass(frozen=True)
class TestEntry:
    ip: str
    level: int
    name: str
    module: str
    top: str
    timeout_s: float
    testcase: str | None = None
    seeds: int = 1
    params: dict = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash((self.ip, self.level, self.name))


def _load_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise SchemaError(f"{path}: invalid YAML: {exc}") from None


def parse_entry(raw, ip: str, level: int, where: str) -> TestEntry:
    if not isinstance(raw, dict):
        raise SchemaError(f"{where}: a test entry must be a mapping")
    keys = set(raw)
    missing = _REQUIRED - keys
    if missing:
        raise SchemaError(f"{where}: missing key(s): {', '.join(sorted(missing))}")
    unknown = keys - _REQUIRED - _OPTIONAL
    if unknown:
        raise SchemaError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")

    name = raw["name"]
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise SchemaError(f"{where}: 'name' must match {_NAME_RE.pattern}")
    if name in RESERVED_TEST_NAMES:
        raise SchemaError(f"{where}: test name {name!r} is reserved")
    where = f"{where} ({name})"
    module = raw["module"]
    if not isinstance(module, str) or not _MODULE_RE.match(module):
        raise SchemaError(f"{where}: 'module' must be a dotted Python module name")
    top = raw["top"]
    if not isinstance(top, str) or not _IDENT_RE.match(top):
        raise SchemaError(f"{where}: 'top' must be an HDL module name")
    testcase = raw.get("testcase")
    if testcase is not None and (not isinstance(testcase, str) or not _IDENT_RE.match(testcase)):
        raise SchemaError(f"{where}: 'testcase' must be a Python function name")
    seeds = raw.get("seeds", 1)
    if isinstance(seeds, bool) or not isinstance(seeds, int) or seeds < 1:
        raise SchemaError(f"{where}: 'seeds' must be an integer >= 1")
    timeout = raw["timeout_s"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise SchemaError(f"{where}: 'timeout_s' must be a positive number")
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise SchemaError(f"{where}: 'params' must be a mapping")
    for k, v in params.items():
        if not isinstance(k, str) or not _IDENT_RE.match(k):
            raise SchemaError(f"{where}: param name {k!r} is not an identifier")
        if isinstance(v, bool) or not isinstance(v, (int, str)):
            raise SchemaError(f"{where}: param {k} must be an integer or string")
    return TestEntry(ip, level, name, module, top, float(timeout), testcase, seeds, dict(params))


def load_level(ip: Ip, level: int) -> list[TestEntry]:
    """Tests of one IP at one level; a missing file means no tests."""
    path = ip.level_file(level)
    if not path.is_file():
        return []
    data = _load_yaml(path)
    if data is None:
        return []
    if not isinstance(data, dict) or set(data) != {"tests"}:
        raise SchemaError(f"{path}: top level must be a mapping with only 'tests'")
    tests = data["tests"] or []
    if not isinstance(tests, list):
        raise SchemaError(f"{path}: 'tests' must be a list")
    return [parse_entry(raw, ip.id, level, f"{path}: tests[{i}]") for i, raw in enumerate(tests)]


def load_ip(ip: Ip, child_names: set[str] = frozenset()) -> list[TestEntry]:
    """All tests of one IP across levels; names must be unique per IP and
    must not collide with a sub-IP directory name (they share workdir/<ip>/)."""
    seen: dict[str, int] = {}
    entries = []
    for level in LEVELS:
        for entry in load_level(ip, level):
            if entry.name in seen:
                raise SchemaError(
                    f"{ip.level_file(level)}: test {entry.name!r} already defined "
                    f"in level{seen[entry.name]}.yaml of IP {ip.id}"
                )
            if entry.name in child_names:
                raise SchemaError(
                    f"{ip.level_file(level)}: test name {entry.name!r} collides with "
                    f"sub-IP directory {ip.id}/{entry.name}"
                )
            seen[entry.name] = level
            entries.append(entry)
    return entries


@dataclass(frozen=True)
class LevelBudget:
    level: int
    description: str
    budget_s: float


def load_budgets(cfg_dir: Path) -> dict[int, LevelBudget]:
    path = cfg_dir / "levels.yaml"
    if not path.is_file():
        raise SchemaError(f"{path}: not found")
    data = _load_yaml(path)
    if not isinstance(data, dict):
        raise SchemaError(f"{path}: top level must be a mapping")
    budgets = {}
    for level in LEVELS:
        key = f"level{level}"
        raw = data.get(key)
        if not isinstance(raw, dict) or set(raw) != {"description", "budget_s"}:
            raise SchemaError(f"{path}: '{key}' must have exactly description and budget_s")
        b = raw["budget_s"]
        if isinstance(b, bool) or not isinstance(b, (int, float)) or b <= 0:
            raise SchemaError(f"{path}: {key}.budget_s must be a positive number")
        budgets[level] = LevelBudget(level, str(raw["description"]), float(b))
    extra = set(data) - {f"level{lv}" for lv in LEVELS}
    if extra:
        raise SchemaError(f"{path}: unknown key(s): {', '.join(sorted(extra))}")
    return budgets
