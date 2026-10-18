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
        sim.Result("pass", 1.5, work / "counter/counts/1/sim.log", cpu_s=1.25),
        sim.Result("pass", 0.5, work / "counter_inc/exhaustive/1/sim.log", cpu_s=0.25),
        sim.Result("fail", 2.0, work / "x.log", "failed: random_enable"),
        sim.Result("timeout", 9.0, work / "y.log", "timed out"),
    ]
    builds = [(("counter", "counter"), sim.Result("pass", 1.0, work / "b.log"))]
    budgets = {0: LevelBudget(0, "d", 1.0), 1: LevelBudget(1, "d", 1), 2: LevelBudget(2, "d", 100.0)}
    summary = regress.make_summary(ctx, sel, [0, 2], budgets, builds, results, 5.0)
    assert summary["passed"] is False
    assert summary["tests"][0] == {
        "ip": "counter", "level": 0, "name": "counts", "seed": sel.runs[0][1],
        "status": "pass", "runtime_s": 1.5, "cpu_s": 1.25, "log": "counter/counts/1/sim.log",
        "detail": "", "coverage": False, "reused": False,
    }
    assert summary["totals"]["level0"] == {
        "tests": 2, "passed": 2, "reused": 0, "failed": 0, "runtime_s": 2.0, "cpu_s": 1.5,
        "budget_s": 1.0, "over_budget": True}
    assert summary["totals"]["level2"]["failed"] == 2
    assert "level1" not in summary["totals"]
    out, err = io.StringIO(), io.StringIO()
    assert regress.report(summary, out, err) == 1
    assert ("warning: level0 CPU time 1.5s (the sum of its tests' CPU time) "
            "exceeds its budget of 1s") in err.getvalue()
    assert "level0: 2/2 passed, CPU 1.5s (budget 1s), test wall time 2.0s" in out.getvalue()
    assert "FAILED" in out.getvalue()
    path = regress.write_summary(work, summary)
    assert path == work / "regress/summary.json"
    assert json.loads(path.read_text())["tests"][2]["status"] == "fail"


def test_budget_compares_cpu_time_not_wall_time(fixture_src, tmp_path):
    # A loaded host stretches wall time; the budget is CPU time (DevOps BKM).
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [0])
    work = ctx.workdir
    results = [sim.Result("pass", 50.0, work / f"{i}.log", cpu_s=0.4) for i in range(len(sel.runs))]
    budgets = {0: LevelBudget(0, "d", 1.0)}
    summary = regress.make_summary(ctx, sel, [0], budgets, [], results, 60.0)
    t = summary["totals"]["level0"]
    assert t["runtime_s"] > t["budget_s"] >= t["cpu_s"] and t["over_budget"] is False
    err = io.StringIO()
    regress.report(summary, io.StringIO(), err)
    assert "warning" not in err.getvalue()


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
    monkeypatch.setattr(sim, "_run_child", lambda phase, job, log, timeout_s: (0, 0.1, 0.05))
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


def _fake_children(monkeypatch, ran):
    monkeypatch.setattr(sim, "find_verilator", lambda: Path("/fake/verilator"))
    monkeypatch.setattr(sim, "tool_ident", lambda v: {"verilator": "fake"})
    monkeypatch.setattr(sim, "_run_child", lambda phase, job, log, timeout_s: (0, 0.1, 0.05))

    def run_test(spec, workdir, verilator, test, module, testcase, seed, timeout_s, built=None):
        ran.append((test, seed, spec.coverage))
        d = sim.test_dir(workdir, spec.ip, test, seed)
        d.mkdir(parents=True, exist_ok=True)
        return sim.Result("pass", 0.2, d / "sim.log", "1 case(s) passed", 0.1)

    monkeypatch.setattr(sim, "run_test", run_test)


def test_coverage_on_the_first_seeds_only(fixture_src, tmp_path, monkeypatch):
    """--coverage instruments each entry's first coverage_seeds seeds; the
    other seeds run on the plain build and are not merged."""
    lv2 = fixture_src / "dv/counter/regress/level2.yaml"
    lv2.write_text(lv2.read_text().replace("seeds: 2", "seeds: 3"))
    ran = []
    _fake_children(monkeypatch, ran)
    monkeypatch.setattr(regress, "collect_coverage",
                        lambda ctx, sel, lv, res, fl, tool, cov_of: {
                            "ips": {}, "passed": True,
                            "merged": sorted(s for (e, s), c in cov_of.items() if c)})
    monkeypatch.setattr(regress.cov, "find_tool", lambda v: Path("/fake/verilator_coverage"))
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [2], ["counter"])
    summary = regress.run(ctx, sel, [2], out=io.StringIO(), coverage=True)
    first = regress.seeds_for(sel.runs[0][0], None)[0]
    assert sorted((s, c) for _, s, c in ran) == sorted(
        (s, s == first) for s in regress.seeds_for(sel.runs[0][0], None))
    assert summary["coverage"]["merged"] == [first]
    assert {b["build"].split("-")[0] for b in summary["builds"]} == {"counter"}
    assert len(summary["builds"]) == 2  # the coverage build and the plain one
    assert [t["coverage"] for t in summary["tests"]].count(True) == 1


def test_model_check_pass_is_reused_until_its_inputs_change(fixture_src, tmp_path, monkeypatch):
    from build import cache as build_cache

    lv2 = fixture_src / "dv/counter/regress/level2.yaml"
    lv2.write_text(lv2.read_text().replace(
        "seeds: 2", "seeds: 2\n    coverage_seeds: 0\n    rerun_when: models_change"))
    ran = []
    _fake_children(monkeypatch, ran)
    sim.set_cache(build_cache.Cache(tmp_path / "cache"))
    ctx = ctx_for(fixture_src, tmp_path)

    def once():
        ran.clear()
        out = io.StringIO()
        summary = regress.run(ctx, regress.select(ctx, [2], ["counter"]), [2], out=out)
        return summary, out.getvalue()

    summary, _ = once()
    assert len(ran) == 2 and summary["totals"]["level2"]["reused"] == 0
    summary, text = once()  # nothing changed: both seeds reused, nothing built
    assert ran == [] and summary["totals"]["level2"]["reused"] == 2
    assert summary["passed"] and summary["builds"] == []
    assert "PASS (reused: models unchanged since" in text
    assert all(t["reused"] and t["cpu_s"] == 0 for t in summary["tests"])
    # An RTL change is not a model change ...
    rtl = next((fixture_src / "rtl/counter").glob("*.sv"))
    rtl.write_text(rtl.read_text() + "\n// rtl only\n")
    assert once()[0]["totals"]["level2"]["reused"] == 2
    # ... a testbench or model change is.
    tb = next((fixture_src / "dv/counter/tests").glob("test_*.py"))
    tb.write_text(tb.read_text() + "\n# changed\n")
    summary, _ = once()
    assert len(ran) == 2 and summary["totals"]["level2"]["reused"] == 0
    sim.set_cache(None)  # cache off: always run
    summary, _ = once()
    assert len(ran) == 2 and summary["build_cache"] is None
