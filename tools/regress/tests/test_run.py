"""Selection, seeds and summary logic, without running a simulator."""

import io
import json
from pathlib import Path

import pytest

from build import sim
from build.ip import Context, REPO_ROOT, DiscoveryError
from regress import run as regress
from regress.schema import LevelBudget, SchemaError


def ctx_for(src: Path, tmp_path: Path) -> Context:
    return Context(REPO_ROOT, src, tmp_path / "work")


def names(selection):
    return [(e.level, e.ip, e.name) for e, _ in selection.runs]


def test_levels_are_exclusive(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    assert names(regress.select(ctx, [1])) == [(1, "counter", "wraps_w4")]
    assert names(regress.select(ctx, [0])) == [
        (0, "counter", "counts"), (0, "counter_inc", "exhaustive")]
    assert names(regress.select(ctx, [0, 1])) == [
        (0, "counter", "counts"), (0, "counter_inc", "exhaustive"),
        (1, "counter", "wraps_w4")]


def test_ip_filter_is_flat(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [0], ["counter"])
    assert names(sel) == [(0, "counter", "counts")]  # sub-IP tests not inherited
    assert sel.reasons == {"counter": "--ip"}
    sel = regress.select(ctx, [0], ["counter_inc", "counter_inc"])
    assert names(sel) == [(0, "counter_inc", "exhaustive")]
    with pytest.raises(DiscoveryError, match="unknown IP"):
        regress.select(ctx, [0], ["nope"])


def shared_unit(src: Path, level0: str) -> None:
    d = src / "dv/common"
    (d / "regress").mkdir(parents=True)
    (d / "common.sim.f").write_text("")
    (d / "regress/level0.yaml").write_text(level0)


def test_shared_unit_is_selected_like_an_ip(fixture_src, tmp_path):
    shared_unit(fixture_src, "tests: [{name: cell, module: tests.t, top: common_c, timeout_s: 9}]\n")
    ctx = ctx_for(fixture_src, tmp_path)
    assert names(regress.select(ctx, [0])) == [
        (0, "common", "cell"), (0, "counter", "counts"), (0, "counter_inc", "exhaustive")]
    sel = regress.select(ctx, [0], ["common"])
    assert names(sel) == [(0, "common", "cell")] and sel.reasons == {"common": "--ip"}


def test_shared_unit_lists_are_validated(fixture_src, tmp_path):
    shared_unit(fixture_src, "tests: 3\n")
    with pytest.raises(SchemaError, match="dv/common/regress/level0.yaml"):
        regress.select(ctx_for(fixture_src, tmp_path), [0], ["counter"])


def test_seeds(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    runs = regress.select(ctx, [2]).runs
    seeds = [s for _, s in runs]
    assert len(seeds) == 2 and len(set(seeds)) == 2
    assert seeds == [s for _, s in regress.select(ctx, [2]).runs]  # deterministic
    assert [s for _, s in regress.select(ctx, [2], base_seed=100).runs] == [100, 101]


def test_validation_covers_unselected_ips(fixture_src, tmp_path):
    (fixture_src / "dv/counter_inc/regress/level2.yaml").write_text("tests: 3\n")
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
        sim.Result("pass", 0.5, work / "counter_inc/exhaustive/1/sim.log"),
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


def test_explicit_seed_list(fixture_src, tmp_path):
    (fixture_src / "dv/counter/regress/level2.yaml").write_text(
        "tests:\n  - {name: r, module: tests.test_counter, top: counter,"
        " seeds: [11, 4], timeout_s: 5}\n")
    ctx = ctx_for(fixture_src, tmp_path)
    assert [s for _, s in regress.select(ctx, [2]).runs] == [4, 11]  # sorted
    assert [s for _, s in regress.select(ctx, [2], base_seed=7).runs] == [7, 8]


def test_filelist_error_is_a_per_ip_build_error(fixture_src, tmp_path, monkeypatch):
    """A sim.f that does not resolve fails that IP's build; other IPs still
    run and the summary is written with exit code 1."""
    (fixture_src / "dv/counter/counter.dv.f").write_text("missing_tb.sv\n")
    monkeypatch.setattr(sim, "find_verilator", lambda: Path("/fake/verilator"))
    monkeypatch.setattr(sim, "_run_child", lambda phase, job, log, timeout_s: (0, 0.1))
    monkeypatch.setattr(sim, "run_test", lambda spec, *a: sim.Result("pass", 0.2, Path("x.log")))
    ctx = ctx_for(fixture_src, tmp_path)
    out = io.StringIO()
    summary = regress.run(ctx, regress.select(ctx, [0]), [0], out=out)
    status = {t["ip"]: (t["status"], t["detail"]) for t in summary["tests"]}
    assert status["counter_inc"] == ("pass", "")
    assert status["counter"][0] == "error"
    assert "counter.dv.f:1: source not found: missing_tb.sv" in status["counter"][1]
    assert summary["passed"] is False
    assert regress.report(summary, io.StringIO(), io.StringIO()) == 1
    assert json.loads(regress.summary_path(ctx.workdir).read_text())["passed"] is False
    build_log = ctx.workdir / "counter/build/counter/build.log"
    assert "source not found" in build_log.read_text()
