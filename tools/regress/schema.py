"""Regression list (``dv/regress/level<N>.yaml``) and ``cfg/levels.yaml``
loading and validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from build.ip import RESERVED_TEST_NAMES, Ip
from regress.funcov import NAME_RE as COVERGROUP_RE

LEVELS = (0, 1, 2)

_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MODULE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

_REQUIRED = {"name", "module", "top", "timeout_s"}
_OPTIONAL = {"testcase", "seeds", "params", "backend", "coverage_seeds", "rerun_when"}
# Values of `rerun_when` (wiki/tools/regress/SPEC.md, "Reuse of unchanged
# model checks"): run again only when the models the entry checks change.
RERUN_WHEN = ("models_change",)


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
    seeds: int | tuple[int, ...] = 1  # count (derived seeds) or explicit seeds
    params: dict = field(default_factory=dict)
    backend: str | None = None  # a cfg/backends.yaml flow kind (build.backends)
    coverage_seeds: int = 1  # with --coverage, how many of its runs are coverage runs
    rerun_when: str | None = None  # reuse a pass until this changes (RERUN_WHEN)

    @property
    def runs(self) -> int:
        return self.seeds if isinstance(self.seeds, int) else len(self.seeds)

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
    if isinstance(raw.get("name"), str):
        where = f"{where} ({raw['name']})"
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
    if isinstance(seeds, list):
        if not seeds or any(
            isinstance(x, bool) or not isinstance(x, int) or not 0 <= x < 2**31
            for x in seeds
        ):
            raise SchemaError(
                f"{where}: a 'seeds' list must hold integers in [0, 2**31)"
            )
        if len(set(seeds)) != len(seeds):
            raise SchemaError(f"{where}: 'seeds' list has duplicates")
        seeds = tuple(seeds)
    elif isinstance(seeds, bool) or not isinstance(seeds, int) or seeds < 1:
        raise SchemaError(
            f"{where}: 'seeds' must be a count >= 1 or a list of integer seeds"
        )
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
    backend = raw.get("backend")
    if backend is not None and (not isinstance(backend, str) or not _IDENT_RE.match(backend)):
        raise SchemaError(f"{where}: 'backend' must be a flow kind from cfg/backends.yaml")
    runs = seeds if isinstance(seeds, int) else len(seeds)
    cov = raw.get("coverage_seeds", 1)
    if isinstance(cov, bool) or not isinstance(cov, int) or not 0 <= cov <= runs:
        raise SchemaError(f"{where}: 'coverage_seeds' must be a count from 0 to the "
                          f"entry's {runs} run(s)")
    rerun = raw.get("rerun_when")
    if rerun is not None:
        if rerun not in RERUN_WHEN:
            raise SchemaError(f"{where}: 'rerun_when' must be one of {', '.join(RERUN_WHEN)}")
        if cov != 0:
            raise SchemaError(f"{where}: 'rerun_when' needs 'coverage_seeds: 0' (a reused "
                              "pass carries no coverage)")
    return TestEntry(ip, level, name, module, top, float(timeout), testcase, seeds, dict(params),
                     backend, cov, rerun)


FLOOR_KEY = "coverage_floors"
FLOOR_METRICS = ("line", "toggle", "functional")


def _level_data(ip: Ip, level: int) -> dict | None:
    """The validated top level of one level file; None when it is missing."""
    path = ip.level_file(level)
    if not path.is_file():
        return None
    data = _load_yaml(path)
    if not isinstance(data, dict) or "tests" not in data or set(data) - {"tests", FLOOR_KEY}:
        raise SchemaError(
            f"{path}: top level must be a mapping with 'tests' and optionally "
            f"'{FLOOR_KEY}' (an empty level is 'tests: []')"
        )
    if not isinstance(data["tests"], list):
        raise SchemaError(f"{path}: 'tests' must be a list")
    return data


def load_level(ip: Ip, level: int) -> list[TestEntry]:
    """Tests of one IP at one level. A missing file or ``tests: []`` means
    no tests."""
    data = _level_data(ip, level)
    if data is None:
        return []
    path = ip.level_file(level)
    return [parse_entry(raw, ip.id, level, f"{path}: tests[{i}]") for i, raw in enumerate(data["tests"])]


def _percent(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 100


def load_floors(ip: Ip) -> dict[int, dict[str, float | dict[str, float]]]:
    """Coverage floors (percent) per level of one IP; levels without
    ``coverage_floors`` are absent. ``functional`` is a percent over all
    covergroups or a mapping of covergroup name to percent."""
    out = {}
    for level in LEVELS:
        data = _level_data(ip, level)
        if data is None or FLOOR_KEY not in data:
            continue
        where = f"{ip.level_file(level)}: {FLOOR_KEY}"
        raw = data[FLOOR_KEY]
        if not isinstance(raw, dict) or not raw:
            raise SchemaError(f"{where}: must be a non-empty mapping of metric to percent")
        unknown = set(raw) - set(FLOOR_METRICS)
        if unknown:
            raise SchemaError(f"{where}: unknown metric(s): {', '.join(sorted(map(str, unknown)))} "
                              f"(known: {', '.join(FLOOR_METRICS)})")
        floors: dict[str, float | dict[str, float]] = {}
        for metric, value in raw.items():
            if metric == "functional" and isinstance(value, dict):
                if not value:
                    raise SchemaError(f"{where}: functional must be a percentage or a non-empty "
                                      f"mapping of covergroup to percentage")
                for group, pct in value.items():
                    if not isinstance(group, str) or not COVERGROUP_RE.match(group):
                        raise SchemaError(f"{where}: functional: covergroup name {group!r} must match "
                                          f"{COVERGROUP_RE.pattern}")
                    if not _percent(pct):
                        raise SchemaError(f"{where}: functional: {group} must be a percentage in [0, 100]")
                floors[metric] = {g: float(v) for g, v in value.items()}
            elif _percent(value):
                floors[metric] = float(value)
            else:
                raise SchemaError(f"{where}: {metric} must be a percentage in [0, 100]"
                                  + (" or a mapping of covergroup to percentage" if metric == "functional" else ""))
        out[level] = floors
    return out


def load_ip(ip: Ip) -> list[TestEntry]:
    """All tests of one IP across levels; names must be unique per IP (they
    share workdir/<ip>/)."""
    seen: dict[str, int] = {}
    entries = []
    for level in LEVELS:
        for entry in load_level(ip, level):
            if entry.name in seen:
                raise SchemaError(
                    f"{ip.level_file(level)}: test {entry.name!r} already defined "
                    f"in level{seen[entry.name]}.yaml of IP {ip.id}"
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
    extra = set(data) - {f"level{lv}" for lv in LEVELS} - {ROTATION_KEY}
    if extra:
        raise SchemaError(f"{path}: unknown key(s): {', '.join(sorted(extra))}")
    return budgets


ROTATION_KEY = "rotation"
_ROTATION_FIELDS = {"level", "extra_seeds", "budget_s"}
_ROTATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class Rotation:
    """Extra rotated seeds (wiki/tools/regress/SPEC.md, "Seed rotation"):
    ``extra_seeds`` more seeds per entry of ``level``, derived from ``key``,
    started while their CPU time stays under ``budget_s``."""
    key: str
    level: int
    extra_seeds: int
    budget_s: float


def load_rotation(cfg_dir: Path, key: str, extra_seeds: int | None = None) -> Rotation:
    """The ``rotation`` section of ``cfg/levels.yaml`` for rotation ``key``;
    ``extra_seeds`` overrides its count."""
    path = cfg_dir / "levels.yaml"
    if not isinstance(key, str) or not _ROTATION_ID.match(key):
        raise SchemaError(f"--rotate: the key must match {_ROTATION_ID.pattern} "
                          f"(a date or a run id), got {key!r}")
    data = _load_yaml(path) if path.is_file() else None
    raw = data.get(ROTATION_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict) or set(raw) != _ROTATION_FIELDS:
        raise SchemaError(f"{path}: '{ROTATION_KEY}' must have exactly "
                          f"{', '.join(sorted(_ROTATION_FIELDS))}")
    level = raw["level"]
    if isinstance(level, bool) or level not in LEVELS:
        raise SchemaError(f"{path}: {ROTATION_KEY}.level must be one of "
                          f"{', '.join(map(str, LEVELS))}")
    count = raw["extra_seeds"] if extra_seeds is None else extra_seeds
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise SchemaError(f"{path}: {ROTATION_KEY}.extra_seeds (or --extra-seeds) must be "
                          f"a count >= 0")
    b = raw["budget_s"]
    if isinstance(b, bool) or not isinstance(b, (int, float)) or b <= 0:
        raise SchemaError(f"{path}: {ROTATION_KEY}.budget_s must be a positive number")
    return Rotation(key, level, count, float(b))
