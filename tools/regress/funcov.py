"""Functional coverage for cocotb tests: named bins and crosses.

A small in-house helper (Python standard library only). A test declares
covergroups, samples them while it runs, and the regression tool merges the
per-run results of each IP. See wiki/tools/regress/SPEC.md#functional-coverage.

    from regress.funcov import Covergroup

    cg = Covergroup("ctrl")
    cg.coverpoint("valid", bins={"idle": 0, "busy": 1})
    cg.coverpoint("data", bins={"zero": 0, "max": 0xFF, "mid": range(1, 0xFF)})
    cg.cross("valid_x_data", "valid", "data")
    ...
    cg.sample(valid=1, data=0x3C)

Bins are a mapping from name to a value, a collection of values (``range``,
``list``, ``set``, ``frozenset``) or a predicate ``value -> bool``; a list of
values names each bin after its value. A ``tuple`` is one value, not a
collection, so ``{"a__b": ("A", "B")}`` is hit by the sampled arc
``("A", "B")`` only. Matching is type-strict: a value matches a bin value
of the same type only (``True`` is not ``1``, ``1`` is not ``1.0``), item by
item inside tuples: an ``IntEnum`` member, a numpy integer, a cocotb
``LogicArray`` or a namedtuple never hits a plain ``int`` or ``tuple`` bin.
When a sample hits no bin and no value bin of its coverpoint has its type,
a :class:`CoverageWarning` is issued once per coverpoint and type. A
sampled value hits every bin it matches. A cross has
one bin per combination of its coverpoints' bins and is hit when every one of
its coverpoints is sampled in the same ``sample`` call; combinations declared
``unreachable`` are left out, and sampling one raises CoverageError. Predicates must be
pure functions of the value: ``sample`` reuses the bins computed for an equal
value of the same type.

Sampling always counts; the results are written only when the sim tool asks
for coverage (``./build regress --coverage``), which sets ``FUNCOV_FILE`` to
the run's output path. They are written at interpreter exit, or earlier with
``write()``.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import warnings
from collections.abc import Iterator, Mapping, MappingView, Sequence
from itertools import product
from pathlib import Path

SCHEMA = 1
ENV_FILE = "FUNCOV_FILE"
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
CROSS_SEP = ","
MEMO_LIMIT = 4096  # distinct samples per covergroup whose increments are remembered

_GROUPS: dict[str, "Covergroup"] = {}


class CoverageError(ValueError):
    pass


class CoverageWarning(UserWarning):
    """A sample whose type no value bin of its coverpoint has."""


def _check_name(kind: str, name) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise CoverageError(f"{kind} name {name!r} must match {NAME_RE.pattern}")
    return name


COLLECTIONS = (range, list, set, frozenset)  # a tuple is a single value (an arc)
_NESTED = (tuple, frozenset)  # values whose items' types the memo key keeps


def _same(value, spec) -> bool:
    """Type-strict equality: same type and equal, item by item inside tuples
    and frozensets, so ``True`` is not ``1`` and ``1`` is not ``1.0``."""
    if type(value) is not type(spec):
        return False
    if type(spec) is tuple:
        return len(value) == len(spec) and all(_same(v, s) for v, s in zip(value, spec))
    if type(spec) is frozenset:
        return _memo_key(value) == _memo_key(spec)
    return value == spec


def _reject_class(where: str, spec, what: str = "bin spec") -> None:
    if isinstance(spec, type):
        raise CoverageError(f"{where}: {what} {spec.__name__} is a class, not a value, "
                            f"collection or predicate (use lambda v: isinstance(v, {spec.__name__}))")


def _matcher(where: str, spec):
    """The match function of one bin spec. Classes (also as collection
    items), iterators (generators), mappings, mapping views and sequences
    other than a tuple, ``str``, ``bytes`` or a collection (a ``deque``) are
    rejected: a class is callable but not a predicate of the value, and the
    others are consumed or never equal a sample."""
    _reject_class(where, spec)
    if isinstance(spec, (Iterator, MappingView, Mapping)) or (
            isinstance(spec, Sequence) and not isinstance(spec, (tuple, str, bytes, *COLLECTIONS))):
        raise CoverageError(f"{where}: bin spec of type {type(spec).__name__} is not a value, "
                            f"collection or predicate (use a list, set or range)")
    if callable(spec):
        return spec
    if isinstance(spec, range):
        return lambda v: type(v) is int and v in spec
    if isinstance(spec, COLLECTIONS):
        values = list(spec)
        for s in values:
            _reject_class(where, s, "collection item")
        return lambda v: any(_same(v, s) for s in values)
    return lambda v: _same(v, spec)


def _value_types(specs) -> frozenset[type]:
    """The types the value bins among ``specs`` match (predicates add none)."""
    out: set[type] = set()
    for spec in specs:
        if isinstance(spec, range):
            out.add(int)
        elif isinstance(spec, COLLECTIONS):
            out.update(type(s) for s in spec)
        elif not callable(spec):
            out.add(type(spec))
    return frozenset(out)


def _memo_key(value):
    """A hashable key that keeps item types inside tuples and frozensets;
    raises TypeError for an unhashable value."""
    if type(value) is tuple:
        return (tuple, tuple((type(v), _memo_key(v)) for v in value))
    if type(value) is frozenset:
        return (frozenset, frozenset((type(v), _memo_key(v)) for v in value))
    hash(value)
    return value


class Covergroup:
    """A named set of coverpoints and crosses. Names are unique per process."""

    def __init__(self, name: str):
        _check_name("covergroup", name)
        if name in _GROUPS:
            raise CoverageError(f"covergroup {name!r} already exists")
        self.name = name
        self._points: dict[str, list[tuple[str, object]]] = {}
        self._crosses: dict[str, tuple[str, ...]] = {}
        self._unreachable: dict[str, frozenset[str]] = {}  # cross -> combinations left out
        self._types: dict[str, frozenset[type]] = {}  # coverpoint -> value bin types
        self._warned: set[tuple[str, type]] = set()
        self._plans: dict[tuple, tuple] = {}  # sampled values -> increments
        self.counts: dict[str, dict[str, int]] = {}
        _GROUPS[name] = self

    def coverpoint(self, name: str, bins) -> "Covergroup":
        _check_name("coverpoint", name)
        if name in self.counts:
            raise CoverageError(f"{self.name}: {name!r} already defined")
        if isinstance(bins, dict):
            items = list(bins.items())
        elif isinstance(bins, (list, tuple, range)):
            items = [(str(v), v) for v in bins]
        else:
            raise CoverageError(f"{self.name}.{name}: bins must be a mapping or a list of values")
        if not items:
            raise CoverageError(f"{self.name}.{name}: no bins")
        names = [_check_name("bin", str(b)) for b, _ in items]
        if len(set(names)) != len(names):
            raise CoverageError(f"{self.name}.{name}: duplicate bin names")
        self._points[name] = [(b, _matcher(f"{self.name}.{name}.{b}", spec)) for b, spec in items]
        self._types[name] = _value_types(spec for _, spec in items)
        self.counts[name] = dict.fromkeys(names, 0)
        return self

    def cross(self, name: str, *points: str, unreachable=()) -> "Covergroup":
        """A cross of ``points``. ``unreachable`` lists combinations, each a
        tuple of one bin name per coverpoint, that the stimulus cannot
        produce: they are left out of the cross, so they never count as
        misses, and a sample that hits one raises CoverageError."""
        _check_name("cross", name)
        if name in self.counts:
            raise CoverageError(f"{self.name}: {name!r} already defined")
        if len(points) < 2 or len(set(points)) != len(points):
            raise CoverageError(f"{self.name}.{name}: a cross needs two or more distinct coverpoints")
        for p in points:
            if p not in self._points:
                raise CoverageError(f"{self.name}.{name}: unknown coverpoint {p!r}")
        names = [[b for b, _ in self._points[p]] for p in points]
        skip: set[str] = set()
        for combo in unreachable:
            if (type(combo) is not tuple or len(combo) != len(points)
                    or not all(b in n for b, n in zip(combo, names))):
                raise CoverageError(f"{self.name}.{name}: unreachable combination {combo!r} is "
                                    f"not a tuple of one bin name per coverpoint {points}")
            skip.add(CROSS_SEP.join(combo))
        combos = [CROSS_SEP.join(c) for c in product(*names)]
        if all(c in skip for c in combos):
            raise CoverageError(f"{self.name}.{name}: every combination is unreachable")
        self._crosses[name] = tuple(points)
        self._unreachable[name] = frozenset(skip)
        self.counts[name] = {c: 0 for c in combos if c not in skip}
        return self

    def sample(self, **values) -> None:
        """Count every bin each named coverpoint's value hits, then the
        crosses whose coverpoints were all sampled."""
        try:
            types = tuple(map(type, values.values()))  # so True, 1 and 1.0 differ
            if tuple in types or frozenset in types:
                key = (tuple((k, _memo_key(v)) for k, v in values.items()), types)
            else:
                key = (tuple(values.items()), types)
            plan = self._plans.get(key)
        except TypeError:  # an unhashable value: plan without the memo
            key, plan = None, None
        if plan is None:
            plan = self._plan(values)
            if key is not None and len(self._plans) < MEMO_LIMIT:
                self._plans[key] = plan
        for counts, b in plan:
            counts[b] += 1

    def _plan(self, values: dict) -> tuple[tuple[dict, str], ...]:
        """The (counts, bin) increments one sample makes."""
        hit: dict[str, list[str]] = {}
        out = []
        for point, value in values.items():
            bins = self._points.get(point)
            if bins is None:
                raise CoverageError(f"{self.name}: unknown coverpoint {point!r}")
            hit[point] = [b for b, match in bins if match(value)]
            if not hit[point]:
                self._warn_type(point, value)
            out += [(self.counts[point], b) for b in hit[point]]
        for name, points in self._crosses.items():
            if all(p in hit for p in points):
                for c in product(*[hit[p] for p in points]):
                    b = CROSS_SEP.join(c)
                    if b in self._unreachable[name]:
                        raise CoverageError(f"{self.name}.{name}: sampled the combination {b}, "
                                            f"declared unreachable ({values})")
                    out.append((self.counts[name], b))
        return tuple(out)

    def _warn_type(self, point: str, value) -> None:
        """Warn once per coverpoint and type when ``value`` hit no bin and
        no value bin has its type (type-strict matching cannot hit it)."""
        types, kind = self._types[point], type(value)
        if not types or kind in types or (point, kind) in self._warned:
            return
        self._warned.add((point, kind))
        names = ", ".join(sorted(t.__name__ for t in types))
        warnings.warn(f"{self.name}.{point}: sampled {kind.__name__} {value!r} has the type of "
                      f"none of its value bins ({names}); matching is type-strict, so sample "
                      "plain values (int(sig.value), tuple(arc))", CoverageWarning, stacklevel=4)


def groups() -> dict[str, dict[str, dict[str, int]]]:
    """Counts of every covergroup in this process: group -> item -> bin -> hits."""
    return {g: {i: dict(b) for i, b in cg.counts.items()} for g, cg in sorted(_GROUPS.items())}


def reset() -> None:
    """Forget every covergroup (for unit tests)."""
    _GROUPS.clear()


def write(path: str | os.PathLike | None = None) -> Path | None:
    """Write this process's results to ``path`` (default ``$FUNCOV_FILE``);
    does nothing when neither is set."""
    path = path or os.environ.get(ENV_FILE)
    if not path:
        return None
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": SCHEMA, "groups": groups()}, indent=1, sort_keys=True) + "\n")
    return out


atexit.register(write)


# --------------------------------------------------------- merge and report
def load(path: Path) -> dict:
    """Read one results file; raises CoverageError when malformed."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise CoverageError(f"{path}: unreadable: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("groups"), dict):
        raise CoverageError(f"{path}: not a schema {SCHEMA} functional coverage file")
    for g, items in data["groups"].items():
        if not isinstance(items, dict) or not all(
                isinstance(b, dict) and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0
                                            for n in b.values()) for b in items.values()):
            raise CoverageError(f"{path}: group {g!r} is malformed")
    return data


def merge(results: list[dict]) -> dict:
    """Sum hits per bin over results; the bin set is the union."""
    out: dict = {}
    for r in results:
        for g, items in r["groups"].items():
            for i, bins in items.items():
                dst = out.setdefault(g, {}).setdefault(i, {})
                for b, n in bins.items():
                    dst[b] = dst.get(b, 0) + n
    return {g: {i: dict(sorted(b.items())) for i, b in sorted(items.items())} for g, items in sorted(out.items())}


def summarize(merged: dict) -> dict:
    """Covered and total bins overall, per group and per item, and the
    uncovered bins as ``group.item.bin``."""
    groups_out, holes = {}, []
    covered = total = 0
    for g, items in merged.items():
        gc = gt = 0
        per_item = {}
        for i, bins in items.items():
            c = sum(1 for n in bins.values() if n > 0)
            per_item[i] = {"covered": c, "total": len(bins)}
            gc, gt = gc + c, gt + len(bins)
            holes += [f"{g}.{i}.{b}" for b, n in bins.items() if n == 0]
        groups_out[g] = {"covered": gc, "total": gt, "items": per_item}
        covered, total = covered + gc, total + gt
    return {"covered": covered, "total": total, "groups": groups_out, "uncovered": holes}
