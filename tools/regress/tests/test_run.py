"""Selection, seeds and summary logic, without running a simulator."""

import io
import json
import zlib
from pathlib import Path

import pytest

from build import sim
from build.ip import Context, REPO_ROOT, DiscoveryError
from regress import run as regress
from regress.schema import LevelBudget, Rotation, SchemaError, load_rotation


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
        "detail": "", "coverage": False, "reused": False, "extra": False,
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

    def run_test(spec, workdir, verilator, test, module, testcase, seed, timeout_s, built=None,
                 expect_fatal=None):
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


# --- seed rotation (wiki/tools/regress/SPEC.md, "Seed rotation") -------------

def rot(key="2026-10-18", n=2, budget=1e9, level=2):
    return Rotation(key, level, n, budget)


def test_rotated_seeds_are_derived_from_the_key(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    entry = regress.select(ctx, [2]).runs[0][0]
    a = regress.rotated_seeds(entry, "2026-10-18", 3, [])
    assert a == regress.rotated_seeds(entry, "2026-10-18", 3, [])  # deterministic
    assert a[0] == zlib.crc32(f"rotate:2026-10-18:{entry.ip}:{entry.name}:0".encode()) & 0x7FFFFFFF
    assert len(set(a)) == 3 and all(0 <= s < 2**31 for s in a)
    assert regress.rotated_seeds(entry, "2026-10-19", 3, []) != a  # the next night rotates
    assert regress.rotated_seeds(entry, "run-12345", 3, []) != a   # a run id works too
    assert regress.rotated_seeds(entry, "2026-10-18", 2, []) == a[:2]  # N is a prefix
    assert regress.rotated_seeds(entry, "2026-10-18", 0, []) == []


def test_rotated_seeds_skip_pinned_seeds(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    entry = regress.select(ctx, [2]).runs[0][0]
    a = regress.rotated_seeds(entry, "k", 3, [])
    b = regress.rotated_seeds(entry, "k", 3, [a[0]])
    assert a[0] not in b and b[:2] == a[1:]


def test_rotation_adds_extra_runs_on_the_rotation_level_only(fixture_src, tmp_path):
    ctx = ctx_for(fixture_src, tmp_path)
    plain = regress.select(ctx, [0, 1, 2])
    sel = regress.select(ctx, [0, 1, 2], rotation=rot(n=2))
    assert sel.runs == plain.runs and plain.extra == []  # pinned runs unchanged
    l2 = [e for e, _ in plain.runs if e.level == 2]
    assert len(sel.extra) == 2 * len(set(l2)) and {e.level for e, _ in sel.extra} == {2}
    pinned = {s for _, s in plain.runs}
    assert not pinned & {s for _, s in sel.extra}
    assert sel.extra == regress.select(ctx, [0, 1, 2], rotation=rot(n=2)).extra
    assert regress.select(ctx, [0, 1], rotation=rot(n=2)).extra == []  # level not selected
    assert regress.select(ctx, [2], ["counter_inc"], rotation=rot()).extra == []


def test_seed_independent_keeps_pinned_coverage_and_other_rotation(fixture_src, tmp_path):
    path = fixture_src / "dv/counter/regress/level2.yaml"
    path.write_text("""tests:
  - {name: fixed, module: tests.t, top: counter, timeout_s: 10,
     seeds: [73], seed_independent: true}
  - {name: random, module: tests.t, top: counter, timeout_s: 10, seeds: [81]}
""")
    ctx = ctx_for(fixture_src, tmp_path)
    plain = regress.select(ctx, [2])
    for key in ("night-one", "night-two"):
        selected = regress.select(ctx, [2], rotation=rot(key=key, n=3))
        assert selected.runs == plain.runs
        assert [(e.name, seed, e.coverage_seeds) for e, seed in selected.runs] == [
            ("fixed", 73, 1), ("random", 81, 1)]
        assert len(selected.extra) == 3
        assert all(e.name == "random" and seed != 81 for e, seed in selected.extra)


def test_load_rotation(tmp_path):
    from build.ip import REPO_ROOT as root
    r = load_rotation(root / "cfg", "2026-10-18")
    assert r.key == "2026-10-18" and r.level == 2 and r.extra_seeds >= 1 and r.budget_s > 0
    assert load_rotation(root / "cfg", "123", extra_seeds=0).extra_seeds == 0
    for bad in ["", "a b", "x" * 65, "../x", "-x"]:
        with pytest.raises(SchemaError, match="--rotate"):
            load_rotation(root / "cfg", bad)
    with pytest.raises(SchemaError, match="extra_seeds"):
        load_rotation(root / "cfg", "k", extra_seeds=-1)
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "levels.yaml").write_text("rotation: {level: 2, extra_seeds: 1}\n")
    with pytest.raises(SchemaError, match="must have exactly"):
        load_rotation(cfg, "k")
    (cfg / "levels.yaml").write_text("rotation: {level: 5, extra_seeds: 1, budget_s: 9}\n")
    with pytest.raises(SchemaError, match="level must be"):
        load_rotation(cfg, "k")
    (cfg / "levels.yaml").write_text("rotation: {level: 2, extra_seeds: 1, budget_s: 0}\n")
    with pytest.raises(SchemaError, match="budget_s"):
        load_rotation(cfg, "k")


def test_extra_runs_are_plain_marked_and_outside_the_level_totals(fixture_src, tmp_path,
                                                                  monkeypatch):
    ran = []
    _fake_children(monkeypatch, ran)
    monkeypatch.setattr(regress, "collect_coverage",
                        lambda ctx, sel, lv, res, fl, tool, cov_of: {"ips": {}, "passed": True})
    monkeypatch.setattr(regress.cov, "find_tool", lambda v: Path("/fake/verilator_coverage"))
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [2], ["counter"], rotation=rot(n=1))
    summary = regress.run(ctx, sel, [2], out=io.StringIO(), coverage=True)
    (extra_entry, extra_seed), = sel.extra
    assert (extra_entry.name, extra_seed, False) in ran  # never a coverage run
    rows = [t for t in summary["tests"] if t["extra"]]
    assert [(t["seed"], t["coverage"]) for t in rows] == [(extra_seed, False)]
    assert summary["totals"]["level2"]["tests"] == len(sel.runs)
    assert summary["rotation"] == {
        "key": "2026-10-18", "level": 2, "extra_seeds": 1, "budget_s": 1e9, "runs": 1,
        "passed": 1, "failed": 0, "cpu_s": 0.1, "skipped": []}
    out = io.StringIO()
    regress.report(summary, out, io.StringIO())
    assert "rotation 2026-10-18: 1/1 extra runs passed" in out.getvalue()
    no_rot = regress.run(ctx, regress.select(ctx, [2], ["counter"]), [2], out=io.StringIO())
    assert no_rot["rotation"] is None


def test_rotation_budget_stops_starting_extra_runs(fixture_src, tmp_path, monkeypatch):
    ran = []
    _fake_children(monkeypatch, ran)  # each run takes 0.1 s of CPU
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [2], ["counter"], rotation=rot(n=3, budget=0.15))
    summary = regress.run(ctx, sel, [2], jobs=1, out=io.StringIO())
    r = summary["rotation"]
    assert r["runs"] == 2 and len(r["skipped"]) == 1  # the 2nd starts at 0.1 < 0.15
    assert r["skipped"] == [{"ip": "counter", "name": sel.extra[2][0].name,
                             "seed": sel.extra[2][1]}]
    assert len([t for t in summary["tests"] if not t["extra"]]) == len(sel.runs)
    assert summary["passed"] is True  # a run not started is not a failure


def test_failing_extra_seed_fails_the_run_with_its_repro(fixture_src, tmp_path, monkeypatch):
    ran = []
    _fake_children(monkeypatch, ran)
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [2], ["counter"], rotation=rot(n=1))
    (entry, bad), = sel.extra
    good = sim.run_test

    def run_test(spec, workdir, verilator, test, module, testcase, seed, *a, **k):
        res = good(spec, workdir, verilator, test, module, testcase, seed, *a, **k)
        return sim.Result("fail", 0.2, res.log, "failed: x", 0.1) if seed == bad else res

    monkeypatch.setattr(sim, "run_test", run_test)
    summary = regress.run(ctx, sel, [2], out=io.StringIO())
    assert summary["passed"] is False and summary["rotation"]["failed"] == 1
    assert summary["totals"]["level2"]["failed"] == 0  # pinned seeds all passed
    err = io.StringIO()
    assert regress.report(summary, io.StringIO(), err) == 1
    assert (f"error: extra seed fail: counter:{entry.name} seed={bad}; reproduce: "
            f"./build sim --ip counter --test {entry.name} --seed {bad}") in err.getvalue()


def test_pinned_runs_start_first_then_extras_in_key_order(fixture_src, tmp_path, monkeypatch):
    lv2 = fixture_src / "dv/counter/regress/level2.yaml"
    lv2.write_text(lv2.read_text() + "  - {name: second, module: tests.test_counter, top: counter,"
                   " seeds: 1, timeout_s: 60}\n")
    ran = []
    _fake_children(monkeypatch, ran)
    got = {}

    def collect(ctx, sel, lv, results, fl, tool, cov_of):
        got["seeds"] = [int(r.log.parent.name) for r in results]
        return {"ips": {}, "passed": True}

    monkeypatch.setattr(regress, "collect_coverage", collect)
    monkeypatch.setattr(regress.cov, "find_tool", lambda v: Path("/fake/verilator_coverage"))
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [2], ["counter"], rotation=rot(n=3))
    regress.run(ctx, sel, [2], jobs=1, out=io.StringIO(), coverage=True)
    started = [(t, s) for t, s, _ in ran]
    pinned = [(e.name, s) for e, s in sel.runs]
    assert started[:len(pinned)] == pinned  # every pinned run starts before any extra
    extras = started[len(pinned):]
    assert len(extras) == 6
    by_key = sorted(sel.extra, key=lambda r: regress.rotation_order("2026-10-18", r))
    assert extras == [(e.name, s) for e, s in by_key]
    assert extras != sorted(extras, key=lambda r: (r[0], r[1]))  # shuffled, not sorted
    other = sorted(sel.extra, key=lambda r: regress.rotation_order("2026-10-19", r))
    assert other != by_key  # another key, another order
    assert got["seeds"] == [s for _, s in sel.runs]  # coverage sees pinned results only


def test_extra_only_builds_are_marked(fixture_src, tmp_path, monkeypatch):
    ran = []
    _fake_children(monkeypatch, ran)
    monkeypatch.setattr(regress, "collect_coverage",
                        lambda ctx, sel, lv, res, fl, tool, cov_of: {"ips": {}, "passed": True})
    monkeypatch.setattr(regress.cov, "find_tool", lambda v: Path("/fake/verilator_coverage"))
    lv2 = fixture_src / "dv/counter/regress/level2.yaml"
    lv2.write_text(lv2.read_text().replace("seeds: 2", "seeds: 1"))
    ctx = ctx_for(fixture_src, tmp_path)
    sel = regress.select(ctx, [2], ["counter"], rotation=rot(n=1))
    summary = regress.run(ctx, sel, [2], out=io.StringIO(), coverage=True)
    # The single pinned seed uses the coverage build; the plain one exists for the extra.
    assert sorted(b["extra"] for b in summary["builds"]) == [False, True]
    plain = regress.run(ctx, regress.select(ctx, [2], ["counter"]), [2], out=io.StringIO())
    assert [b["extra"] for b in plain["builds"]] == [False]
