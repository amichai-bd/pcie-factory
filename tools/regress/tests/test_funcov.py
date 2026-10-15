"""The functional-coverage helper: bins, crosses, sampling, files, merge."""

import collections
import enum
import json
import warnings

import pytest

from regress import funcov
from regress.funcov import CoverageError, CoverageWarning, Covergroup


@pytest.fixture(autouse=True)
def clean_registry():
    funcov.reset()
    yield
    funcov.reset()


def make_group():
    cg = Covergroup("g")
    cg.coverpoint("en", bins={"off": 0, "on": 1})
    cg.coverpoint("v", bins={"zero": 0, "small": range(1, 4), "odd": lambda x: x % 2 == 1})
    cg.cross("en_x_v", "en", "v")
    return cg


def test_bins_and_crosses_count_every_match():
    cg = make_group()
    cg.sample(en=1, v=3)  # v hits small and odd
    cg.sample(en=0, v=0)
    cg.sample(en=0, v=0)  # repeated sample (memoized path)
    cg.sample(v=7)        # odd only; no cross without en
    c = funcov.groups()["g"]
    assert c["en"] == {"off": 2, "on": 1}
    assert c["v"] == {"zero": 2, "small": 1, "odd": 2}
    assert c["en_x_v"] == {"off,zero": 2, "off,small": 0, "off,odd": 0,
                           "on,zero": 0, "on,small": 1, "on,odd": 1}


def test_list_bins_are_named_after_values_and_unhashable_values_work():
    cg = Covergroup("l")
    cg.coverpoint("x", [0, 1, 2])
    cg.coverpoint("w", bins={"empty": lambda v: len(v) == 0, "some": lambda v: len(v) > 0})
    cg.sample(x=2, w=[])
    cg.sample(x=2, w=[1])
    assert funcov.groups()["l"] == {"x": {"0": 0, "1": 0, "2": 2}, "w": {"empty": 1, "some": 1}}


def test_tuple_bin_is_one_value_an_arc():
    cg = Covergroup("arcs")
    cg.coverpoint("arc", bins={"a__b": ("A", "B"), "b__a": ("B", "A"),
                               "into_b": [("A", "B"), ("C", "B")]})  # a list stays a collection
    with pytest.warns(CoverageWarning, match=r"arcs\.arc: sampled str 'B' .*\(tuple\)"):
        cg.sample(arc="B")     # a bare state hits no arc
    cg.sample(arc=("A", "B"))
    cg.sample(arc=("A", "B"))  # memoized path
    cg.sample(arc=("C", "B"))
    assert funcov.groups()["arcs"]["arc"] == {"a__b": 2, "b__a": 0, "into_b": 3}


def test_memo_tells_bool_int_and_float_apart():
    cg = Covergroup("t")
    cg.coverpoint("v", bins={"flag": lambda v: isinstance(v, bool), "int": lambda v: type(v) is int,
                             "pair_flag": lambda v: isinstance(v, tuple) and isinstance(v[0], bool)})
    for v in (True, 1, 1.0, (True, 0), (1, 0), True, 1):
        cg.sample(v=v)
    assert funcov.groups()["t"]["v"] == {"flag": 2, "int": 2, "pair_flag": 1}


def test_matching_is_type_strict():
    cg = Covergroup("s")
    cg.coverpoint("v", bins={"one": 1, "one_l": [1], "r2": range(2), "flt": 1.0, "yes": True,
                             "pair": (1, 0), "fs": [frozenset({1})]})
    cg.sample(v=True)
    cg.sample(v=1)
    cg.sample(v=1.0)
    cg.sample(v=(True, 0))
    cg.sample(v=(1, 0))
    cg.sample(v=frozenset({True}))
    cg.sample(v=frozenset({1}))
    assert funcov.groups()["s"]["v"] == {"one": 1, "one_l": 1, "r2": 1, "flt": 1, "yes": 1,
                                         "pair": 1, "fs": 1}


def test_frozenset_memo_keeps_item_types():
    cg = Covergroup("f")
    cg.coverpoint("v", bins={"has_bool": lambda v: any(type(x) is bool for x in v)})
    for v in (frozenset({True}), frozenset({1}), frozenset({True})):
        cg.sample(v=v)
    assert funcov.groups()["f"]["v"] == {"has_bool": 2}


@pytest.mark.parametrize("spec, message", [
    (str, "bin spec str is a class"),
    (int, "bin spec int is a class"),
    (enum.Enum("Colour", "RED BLUE"), "bin spec Colour is a class"),
    ((x for x in (1, 2)), "bin spec of type generator "),
    (iter([1, 2]), "bin spec of type list_iterator "),
    ({1: 2}.keys(), "bin spec of type dict_keys "),
    ({1: 2}.values(), "bin spec of type dict_values "),
    ({1: 2}.items(), "bin spec of type dict_items "),
    ({1: 2}, "bin spec of type dict "),
    (collections.deque([1, 2]), "bin spec of type deque "),
    (bytearray(b"ab"), "bin spec of type bytearray "),
    ([str, int], "collection item str is a class"),
    ({1, int}, "collection item int is a class"),
])
def test_bin_specs_that_could_never_mean_a_value_are_rejected(spec, message):
    with pytest.raises(CoverageError, match=r"^c\.p\.b: " + message):
        Covergroup("c").coverpoint("p", bins={"b": spec})


def test_type_mismatch_warns_once_per_coverpoint_and_type():
    State = enum.IntEnum("State", "IDLE BUSY")
    Arc = collections.namedtuple("Arc", "src dst")
    cg = Covergroup("w")
    cg.coverpoint("v", bins={"zero": 0, "low": range(1, 4), "pair": [0, 1]})
    cg.coverpoint("arc", bins={"a__b": ("A", "B")})
    cg.coverpoint("p", bins={"any": lambda v: False})  # predicates only: no value types
    with pytest.warns(CoverageWarning) as got:
        cg.sample(v=State.IDLE, arc=Arc("A", "B"), p=1)
        cg.sample(v=State.BUSY)   # same coverpoint and type: no second warning
        cg.sample(v=False)
    assert [str(w.message).split(":")[0] for w in got] == ["w.v", "w.arc", "w.v"]
    assert "sampled bool False" in str(got[2].message)
    assert funcov.groups()["w"]["v"] == {"zero": 0, "low": 0, "pair": 0}


def test_no_warning_for_a_hit_or_a_matching_type():
    cg = Covergroup("n")
    cg.coverpoint("v", bins={"zero": 0, "odd": lambda v: v % 2 == 1})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cg.sample(v=2)                 # a hole, but int is a value bin's type
        cg.sample(v=True)              # bool hits the predicate
    assert funcov.groups()["n"]["v"] == {"zero": 0, "odd": 1}


@pytest.mark.parametrize("build, message", [
    (lambda: Covergroup("bad name"), "covergroup name"),
    (lambda: (Covergroup("a"), Covergroup("a")), "already exists"),
    (lambda: Covergroup("a").coverpoint("p", {}), "no bins"),
    (lambda: Covergroup("a").coverpoint("p", 5), "mapping or a list"),
    (lambda: Covergroup("a").coverpoint("p", {"x y": 1}), "bin name"),
    (lambda: Covergroup("a").coverpoint("p", [1, "1"]), "duplicate bin"),
    (lambda: Covergroup("a").coverpoint("p", [1]).coverpoint("p", [2]), "already defined"),
    (lambda: Covergroup("a").coverpoint("p", [1]).cross("c", "p"), "two or more"),
    (lambda: Covergroup("a").coverpoint("p", [1]).cross("c", "p", "q"), "unknown coverpoint"),
    (lambda: Covergroup("a").coverpoint("p", [1]).sample(q=1), "unknown coverpoint"),
])
def test_definition_errors(build, message):
    with pytest.raises(CoverageError, match=message):
        build()


def test_write_only_when_asked(tmp_path, monkeypatch):
    make_group().sample(en=1, v=0)
    monkeypatch.delenv(funcov.ENV_FILE, raising=False)
    assert funcov.write() is None
    out = tmp_path / "run/funcov.json"
    monkeypatch.setenv(funcov.ENV_FILE, str(out))
    assert funcov.write() == out
    data = funcov.load(out)
    assert data["schema"] == funcov.SCHEMA and data["groups"]["g"]["en"] == {"off": 0, "on": 1}
    first = out.read_bytes()
    funcov.write()
    assert out.read_bytes() == first  # deterministic


@pytest.mark.parametrize("text", [
    "not json", "[]", '{"schema": 99, "groups": {}}', '{"schema": 1}',
    '{"schema": 1, "groups": {"g": {"p": {"b": -1}}}}',
    '{"schema": 1, "groups": {"g": {"p": {"b": true}}}}',
    '{"schema": 1, "groups": {"g": [1]}}',
])
def test_load_rejects_malformed(tmp_path, text):
    path = tmp_path / "f.json"
    path.write_text(text)
    with pytest.raises(CoverageError, match="f.json"):
        funcov.load(path)


def test_merge_sums_and_unions_and_summarize_lists_holes():
    a = {"schema": 1, "groups": {"g": {"p": {"x": 1, "y": 0}}}}
    b = {"schema": 1, "groups": {"g": {"p": {"x": 2, "z": 0}}, "h": {"q": {"k": 1}}}}
    merged = funcov.merge([a, b])
    assert merged == {"g": {"p": {"x": 3, "y": 0, "z": 0}}, "h": {"q": {"k": 1}}}
    s = funcov.summarize(merged)
    assert (s["covered"], s["total"]) == (2, 4)
    assert s["uncovered"] == ["g.p.y", "g.p.z"]
    assert s["groups"]["g"]["items"]["p"] == {"covered": 1, "total": 3}
    assert funcov.summarize({}) == {"covered": 0, "total": 0, "groups": {}, "uncovered": []}
    json.dumps(s)  # serializable


def test_unreachable_cross_combinations_are_left_out():
    cg = Covergroup("u")
    cg.coverpoint("clk", bins=["fast", "slow"])
    cg.coverpoint("w", bins=["short", "long"])
    cg.cross("clk_x_w", "clk", "w", unreachable=[("fast", "short")])
    cg.sample(clk="slow", w="short")
    cg.sample(clk="fast", w="long")
    assert funcov.groups()["u"]["clk_x_w"] == {"fast,long": 1, "slow,short": 1, "slow,long": 0}
    s = funcov.summarize(funcov.merge([{"groups": funcov.groups()}]))
    assert s["groups"]["u"]["items"]["clk_x_w"] == {"covered": 2, "total": 3}
    with pytest.raises(CoverageError, match=r"u\.clk_x_w: sampled the combination fast,short"):
        cg.sample(clk="fast", w="short")
    cg.sample(clk="fast")  # no cross without w
    assert funcov.groups()["u"]["clk"] == {"fast": 2, "slow": 1}


@pytest.mark.parametrize("unreachable, message", [
    ([("fast",)], "one bin name per coverpoint"),
    ([("fast", "huge")], "one bin name per coverpoint"),
    (["fast,short"], "one bin name per coverpoint"),
    ([("fast", "short"), ("fast", "long"), ("slow", "short"), ("slow", "long")],
     "every combination is unreachable"),
])
def test_unreachable_definition_errors(unreachable, message):
    cg = Covergroup("u")
    cg.coverpoint("clk", bins=["fast", "slow"])
    cg.coverpoint("w", bins=["short", "long"])
    with pytest.raises(CoverageError, match=message):
        cg.cross("clk_x_w", "clk", "w", unreachable=unreachable)
