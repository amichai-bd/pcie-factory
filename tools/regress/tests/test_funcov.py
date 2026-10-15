"""The functional-coverage helper: bins, crosses, sampling, files, merge."""

import json

import pytest

from regress import funcov
from regress.funcov import CoverageError, Covergroup


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
    cg.sample(arc="B")         # a bare state hits no arc
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
