import json
import sys
from pathlib import Path

import pytest

from arch import perf
from build.ip import discover, find_ip
from cli.main import main

MODEL = """\
def perf(workload, seed):
    if workload.get("boom"):
        raise RuntimeError("boom")
    if workload.get("bad"):
        return {"x": "nan"}
    return {"rate": workload.get("rate", 1.0), "lat": 1, "seed": seed}
"""
SCENARIO = """\
description: Full rate.
seed: 3
workload: {rate: 1.0}
targets:
  rate: {min: 1.0}
  lat: {max: 1}
"""


def make_ip(root: Path, ip_id: str, reaches: str = "") -> None:
    d = root / "src/rtl" / ip_id
    d.mkdir(parents=True)
    (d / f"{ip_id}.rtl.f").write_text(f"-F ../{reaches}/{reaches}.rtl.f\n" if reaches else "")


@pytest.fixture
def tree(tmp_path):
    """IP blk (with sub-IP blk_sub) whose arch/blk/ has a model and one scenario."""
    make_ip(tmp_path, "blk_sub")
    make_ip(tmp_path, "blk", reaches="blk_sub")
    arch = tmp_path / "src/arch/blk"
    (arch / "perf").mkdir(parents=True)
    (arch / "xy_blk_arch.py").write_text(MODEL)
    (arch / "perf/full.yaml").write_text(SCENARIO)
    return tmp_path


def ip(root: Path, ip_id: str = "blk"):
    return find_ip(discover(root / "src"), ip_id)


def scenario(root: Path, name: str, text: str) -> None:
    (root / "src/arch/blk/perf" / f"{name}.yaml").write_text(text)


def result(root: Path) -> dict:
    return json.loads((root / "work/blk/arch/perf.json").read_text())


def test_pass_writes_perf_json(tree, capsys):
    assert perf.run(ip(tree), tree / "work", out=sys.stdout) == 0
    doc = result(tree)
    assert doc["status"] == "pass" and doc["ip"] == "blk" and doc["model"] == "xy_blk_arch.py"
    (sc,) = doc["scenarios"]
    assert sc["name"] == "full" and sc["seed"] == 3 and sc["status"] == "pass"
    assert sc["metrics"] == {"lat": 1, "rate": 1.0, "seed": 3}
    assert sc["targets"] == [
        {"metric": "lat", "max": 1, "value": 1, "status": "pass"},
        {"metric": "rate", "min": 1.0, "value": 1.0, "status": "pass"},
    ]
    out = capsys.readouterr().out
    assert "[arch] full: PASS" in out and "rate = 1.0 (min 1.0) PASS" in out


def test_output_is_deterministic(tree):
    perf.run(ip(tree), tree / "work")
    first = (tree / "work/blk/arch/perf.json").read_bytes()
    perf.run(ip(tree), tree / "work")
    assert (tree / "work/blk/arch/perf.json").read_bytes() == first


def test_missed_target_fails(tree, capsys):
    scenario(tree, "slow", SCENARIO.replace("rate: 1.0}", "rate: 0.5}"))
    assert perf.run(ip(tree), tree / "work", out=sys.stdout) == 1
    doc = result(tree)
    assert doc["status"] == "fail"
    assert [s["status"] for s in doc["scenarios"]] == ["pass", "fail"]
    assert "rate = 0.5 (min 1.0) FAIL" in capsys.readouterr().out


@pytest.mark.parametrize("workload, detail", [
    ("{boom: 1}", "perf() raised RuntimeError: boom"),
    ("{bad: 1}", "mapping of metric name -> finite number"),
])
def test_model_failures_are_scenario_errors(tree, workload, detail):
    scenario(tree, "full", SCENARIO.replace("{rate: 1.0}", workload))
    assert perf.run(ip(tree), tree / "work") == 1
    (sc,) = result(tree)["scenarios"]
    assert sc["status"] == "error" and detail in sc["detail"]


def test_target_on_unreported_metric_is_an_error(tree):
    scenario(tree, "full", SCENARIO + "  power: {max: 1}\n")
    assert perf.run(ip(tree), tree / "work") == 1
    assert "did not report: ['power']" in result(tree)["scenarios"][0]["detail"]


def test_scenario_selection(tree):
    scenario(tree, "other", SCENARIO)
    assert perf.run(ip(tree), tree / "work", "other") == 0
    assert [s["name"] for s in result(tree)["scenarios"]] == ["other"]
    with pytest.raises(perf.ArchError, match="no scenario 'nope' \\(known: full, other\\)"):
        perf.run(ip(tree), tree / "work", "nope")


@pytest.mark.parametrize("text, msg", [
    ("[1]", "top level must be a mapping"),
    ("a: [", "invalid YAML"),
    ("seed: 1\nworkload: {}\n", "missing key\\(s\\) \\['description'\\]"),
    (SCENARIO + "extra: 1\n", "unknown key\\(s\\) \\['extra'\\]"),
    (SCENARIO.replace("seed: 3", "seed: -1"), "seed: expected an integer"),
    (SCENARIO.replace("seed: 3", "seed: true"), "seed: expected an integer"),
    (SCENARIO.replace("description: Full rate.", "description: ''"), "description"),
    (SCENARIO.replace("workload: {rate: 1.0}", "workload: [1]"), "workload: expected a mapping"),
    (SCENARIO.replace("{min: 1.0}", "{least: 1.0}"), "targets.rate: expected a mapping"),
    (SCENARIO.replace("{min: 1.0}", "{min: fast}"), "targets.rate.min: expected a number"),
    (SCENARIO.replace("{min: 1.0}", "{min: 2, max: 1}"), "targets.rate: min > max"),
])
def test_malformed_scenario(tree, text, msg):
    scenario(tree, "full", text)
    with pytest.raises(perf.ArchError, match=msg):
        perf.run(ip(tree), tree / "work")


def test_bad_scenario_name(tree):
    scenario(tree, "Bad-Name", SCENARIO)
    with pytest.raises(perf.ArchError, match="scenario name 'Bad-Name'"):
        perf.run(ip(tree), tree / "work")


def test_sub_ip_is_rejected(tree):
    with pytest.raises(perf.ArchError, match="blk_sub is a sub-IP"):
        perf.run(ip(tree, "blk_sub"), tree / "work")


def test_model_lookup(tree):
    model = tree / "src/arch/blk/xy_blk_arch.py"
    (tree / "src/arch/blk/test_xy_blk_arch.py").write_text("")  # tests do not count
    assert perf.find_model(ip(tree)) == model
    (tree / "src/arch/blk/zz_blk_arch.py").write_text(MODEL)
    with pytest.raises(perf.ArchError, match="exactly one model.*xy_blk_arch.py, zz_blk_arch.py"):
        perf.find_model(ip(tree))
    for p in (model, tree / "src/arch/blk/zz_blk_arch.py"):
        p.unlink()
    with pytest.raises(perf.ArchError, match="found: none"):
        perf.find_model(ip(tree))


def test_missing_arch_dir_and_scenarios(tree):
    (tree / "src/arch/blk/perf/full.yaml").unlink()
    with pytest.raises(perf.ArchError, match="no perf scenarios"):
        perf.run(ip(tree), tree / "work")
    make_ip(tree, "other")
    with pytest.raises(perf.ArchError, match="IP other has no arch/other/ directory"):
        perf.run(ip(tree, "other"), tree / "work")


@pytest.mark.parametrize("source, msg", [
    ("import nonexistent_mod\n", "cannot import the model: ModuleNotFoundError"),
    ("x = 1\n", "defines no perf\\(workload, seed\\) function"),
])
def test_bad_model_module(tree, source, msg):
    (tree / "src/arch/blk/xy_blk_arch.py").write_text(source)
    with pytest.raises(perf.ArchError, match=msg):
        perf.run(ip(tree), tree / "work")


def test_model_may_import_siblings(tree):
    (tree / "src/arch/blk/xy_blk_helper.py").write_text("RATE = 1.0\n")
    (tree / "src/arch/blk/xy_blk_arch.py").write_text(
        "import xy_blk_helper\n\ndef perf(w, s):\n    return {'rate': xy_blk_helper.RATE, 'lat': 1}\n")
    assert perf.run(ip(tree), tree / "work") == 0
    assert str(tree / "src/arch/blk") not in sys.path


def test_cli(tree, capsys):
    common = ["--src-root", str(tree / "src"), "--workdir", str(tree / "work")]
    assert main(["arch", "--ip", "blk", *common]) == 0
    assert main(["arch", "--ip", "blk", "--scenario", "full", *common]) == 0
    assert main(["arch", "--ip", "blk_sub", *common]) == 2
    assert main(["arch", "--ip", "nope", *common]) == 2
    err = capsys.readouterr().err
    assert "error: IP blk_sub is a sub-IP" in err and "unknown IP 'nope'" in err
