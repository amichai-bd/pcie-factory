"""Selection, seeds and summary logic, without running a simulator."""

import io
import json
from pathlib import Path

import pytest

from build import sim
from build.ip import Context, REPO_ROOT, DiscoveryError
from regress import run as regress
from regress.schema import LevelBudget


def ctx_for(src: Path, tmp_path: Path) -> Context:
    return Context(REPO_ROOT, src, tmp_path / "work")


def names(selection):
    return [(e.level, e.ip, e.name) for e, _ in selection.runs]


def test_levels_are_exclusive(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    assert names(regress.select(ctx, [1])) == [(1, "counter", "wraps_w4")]
    assert names(regress.select(ctx, [0])) == [
        (0, "counter", "counts"), (0, "counter/inc", "exhaustive")]
    assert names(regress.select(ctx, [0, 1])) == [
        (0, "counter", "counts"), (0, "counter/inc", "exhaustive"),
        (1, "counter", "wraps_w4")]


def test_ip_filter_is_flat(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [0], ["counter"])
    assert names(sel) == [(0, "counter", "counts")]  # sub-IP tests not inherited
    assert sel.reasons == {"counter": "--ip"}
    sel = regress.select(ctx, [0], ["counter/inc", "counter/inc"])
    assert names(sel) == [(0, "counter/inc", "exhaustive")]
    with pytest.raises(DiscoveryError, match="unknown IP"):
        regress.select(ctx, [0], ["nope"])


def test_seeds(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    runs = regress.select(ctx, [2]).runs
    seeds = [s for _, s in runs]
    assert len(seeds) == 2 and len(set(seeds)) == 2
    assert seeds == [s for _, s in regress.select(ctx, [2]).runs]  # deterministic
    assert [s for _, s in regress.select(ctx, [2], base_seed=100).runs] == [100, 101]


def test_validation_covers_unselected_ips(fixture_src, tmp_path):
    (fixture_src / "counter/inc/dv/regress/level2.yaml").write_text("tests: 3\n")
    with pytest.raises(Exception, match="must be a list"):
        regress.select(ctx_for(fixture_src, tmp_path), [0], ["counter"])


def test_missing_verilator_is_an_error(fixture_src, tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: None)
    ctx = ctx_for(fixture_src, tmp_path)
    with pytest.raises(sim.SimError, match="verilator not found"):
        regress.run(ctx, regress.select(ctx, [0]), [0])


def test_empty_selection_writes_summary(fixture_src, tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: None)
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.Selection([], {}, [])
    summary = regress.run(ctx, sel, [0], out=io.StringIO())
    assert summary["passed"] is True and summary["totals"]["level0"]["tests"] == 0
    assert json.loads(regress.summary_path(ctx.workdir).read_text()) == summary


def test_summary_and_budget_warning(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [0, 2])
    work = ctx.workdir
    results = [
        sim.Result("pass", 1.5, work / "counter/counts/1/sim.log"),
        sim.Result("pass", 0.5, work / "counter/inc/exhaustive/1/sim.log"),
        sim.Result("fail", 2.0, work / "x.log", "failed: random_enable"),
        sim.Result("timeout", 9.0, work / "y.log", "timed out"),
    ]
    builds = [(("counter", "counter"), sim.Result("pass", 1.0, work / "b.log"))]
    budgets = {0: LevelBudget(0, "d", 1.0), 1: LevelBudget(1, "d", 1), 2: LevelBudget(2, "d", 100.0)}
    summary = regress.make_summary(ctx, sel, [0, 2], budgets, builds, results, 5.0)
    assert summary["passed"] is False
    assert summary["tests"][0] == {
        "ip": "counter", "level": 0, "name": "counts", "seed": sel.runs[0][1],
        "status": "pass", "runtime_s": 1.5, "log": "counter/counts/1/sim.log",
        "detail": "",
    }
    assert summary["totals"]["level0"] == {
        "tests": 2, "passed": 2, "failed": 0, "runtime_s": 2.0,
        "budget_s": 1.0, "over_budget": True}
    assert summary["totals"]["level2"]["failed"] == 2
    assert "level1" not in summary["totals"]
    out, err = io.StringIO(), io.StringIO()
    assert regress.report(summary, out, err) == 1
    assert "warning: level0 runtime 2.0s exceeds its budget of 1s" in err.getvalue()
    assert "FAILED" in out.getvalue()
    path = regress.write_summary(work, summary)
    assert path == work / "regress/summary.json"
    assert json.loads(path.read_text())["tests"][2]["status"] == "fail"
