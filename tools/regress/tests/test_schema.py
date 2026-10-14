from pathlib import Path

import pytest

from build.ip import Ip, REPO_ROOT, discover
from regress.schema import SchemaError, load_budgets, load_ip, load_level, parse_entry

GOOD = {"name": "t", "module": "tests.test_x", "top": "top", "timeout_s": 10}


def test_defaults():
    e = parse_entry(dict(GOOD), "a", 0, "w")
    assert (e.seeds, e.testcase, e.params, e.timeout_s) == (1, None, {}, 10.0)


def test_explicit_seed_list():
    e = parse_entry(dict(GOOD, seeds=[5, 0, 7]), "a", 0, "w")
    assert e.seeds == (5, 0, 7) and e.runs == 3


def test_all_fields():
    e = parse_entry(dict(GOOD, testcase="tc", seeds=3, params={"W": 4, "S": "x"}), "a", 2, "w")
    assert (e.level, e.seeds, e.testcase, e.params) == (2, 3, "tc", {"W": 4, "S": "x"})


@pytest.mark.parametrize(
    "change, message",
    [
        ({"name": None}, "missing key"),
        ({"bogus": 1}, "unknown key"),
        ({"name": "has space"}, "'name' must match"),
        ({"name": "build"}, "reserved"),
        ({"name": "asic"}, "reserved"),
        ({"name": "arch"}, "reserved"),
        ({"module": "tests/test_x.py"}, "dotted Python module"),
        ({"top": "1top"}, "HDL module name"),
        ({"testcase": "a.b"}, "Python function name"),
        ({"seeds": 0}, "'seeds'"),
        ({"seeds": True}, "'seeds'"),
        ({"seeds": []}, "'seeds' list"),
        ({"seeds": [1, "2"]}, "'seeds' list"),
        ({"seeds": [-1]}, "'seeds' list"),
        ({"seeds": [3, 3]}, "duplicates"),
        ({"timeout_s": -1}, "'timeout_s'"),
        ({"timeout_s": "10"}, "'timeout_s'"),
        ({"params": [1]}, "'params' must be a mapping"),
        ({"params": {"W": 1.5}}, "integer or string"),
        ({"params": {"bad-name": 1}}, "not an identifier"),
    ],
)
def test_entry_errors(change, message):
    raw = dict(GOOD)
    for k, v in change.items():
        if v is None:
            raw.pop(k)
        else:
            raw[k] = v
    with pytest.raises(SchemaError, match=message):
        parse_entry(raw, "a", 0, "file.yaml: tests[0]")


def test_errors_name_the_test():
    with pytest.raises(SchemaError, match=r"tests\[0\] \(t\): unknown key"):
        parse_entry(dict(GOOD, bogus=1), "a", 0, "f.yaml: tests[0]")
    with pytest.raises(SchemaError, match=r"tests\[0\] \(t\): missing key"):
        parse_entry({"name": "t"}, "a", 0, "f.yaml: tests[0]")


def test_entry_must_be_mapping():
    with pytest.raises(SchemaError, match="must be a mapping"):
        parse_entry(["x"], "a", 0, "w")


def make_ip(tmp_path: Path, levels: dict) -> Ip:
    ip = Ip("a", tmp_path / "a")
    (ip.path / "dv/regress").mkdir(parents=True)
    for level, text in levels.items():
        ip.level_file(level).write_text(text)
    return ip


def test_level_files(tmp_path):
    ip = make_ip(tmp_path, {1: "tests: []\n",
                            2: "tests:\n  - {name: t, module: m, top: t, timeout_s: 1}\n"})
    assert load_level(ip, 0) == [] and load_level(ip, 1) == []  # missing, empty
    assert [e.name for e in load_level(ip, 2)] == ["t"]
    assert [e.name for e in load_ip(ip)] == ["t"]


@pytest.mark.parametrize(
    "text, message",
    [
        ("- a\n", "only 'tests'"),
        ("[]\n", "an empty level is 'tests: \\[\\]'"),
        ("", "only 'tests'"),
        ("tests:\n", "must be a list"),
        ("tests: []\nother: 1\n", "only 'tests'"),
        ("tests: {a: 1}\n", "must be a list"),
        ("tests: [\n", "invalid YAML"),
        ("tests:\n  - {name: t}\n", r"level0.yaml: tests\[0\] \(t\): missing key"),
    ],
)
def test_level_errors(tmp_path, text, message):
    ip = make_ip(tmp_path, {0: text})
    with pytest.raises(SchemaError, match=message):
        load_level(ip, 0)


def test_duplicate_name_across_levels(tmp_path):
    entry = "tests:\n  - {name: t, module: m, top: t, timeout_s: 1}\n"
    ip = make_ip(tmp_path, {0: entry, 2: entry})
    with pytest.raises(SchemaError, match="already defined in level0.yaml"):
        load_ip(ip)


def test_name_collides_with_sub_ip(tmp_path):
    ip = make_ip(tmp_path, {0: "tests:\n  - {name: sub, module: m, top: t, timeout_s: 1}\n"})
    with pytest.raises(SchemaError, match="collides with sub-IP"):
        load_ip(ip, {"sub"})


def test_fixture_lists_valid(fixture_src):
    for ip in discover(fixture_src):
        assert load_ip(ip)


def test_repo_budgets():
    budgets = load_budgets(REPO_ROOT / "cfg")
    assert sorted(budgets) == [0, 1, 2]
    assert all(b.budget_s > 0 and b.description for b in budgets.values())


@pytest.mark.parametrize(
    "text, message",
    [
        ("level0: {description: d, budget_s: 1}\n", "'level1' must have"),
        ("level0: {description: d, budget_s: 0}\nlevel1: {description: d, budget_s: 1}\n"
         "level2: {description: d, budget_s: 1}\n", "positive"),
        ("level0: {description: d, budget_s: 1}\nlevel1: {description: d, budget_s: 1}\n"
         "level2: {description: d, budget_s: 1}\nlevel3: {}\n", "unknown key"),
    ],
)
def test_budget_errors(tmp_path, text, message):
    (tmp_path / "levels.yaml").write_text(text)
    with pytest.raises(SchemaError, match=message):
        load_budgets(tmp_path)
