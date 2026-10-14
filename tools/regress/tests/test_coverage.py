"""Coverage merge, floors, summary and report, without a simulator."""

import io
import json
from pathlib import Path

import pytest

from build import sim
from build.ip import REPO_ROOT, Context, discover, find_ip
from regress import coverage as cov
from regress import run as regress
from regress.schema import SchemaError, load_floors, load_ip

HEADER = "# SystemC::Coverage-3\n"


def point(kind: str, line: int, count: int, extra: str = "") -> str:
    key = f"\x01f\x02/abs/a.sv\x01l\x02{line}\x01t\x02{kind}\x01page\x02v_{kind}/a\x01o\x02{extra}\x01h\x02a"
    return f"C '{key}' {count}\n"


def dat(path: Path, *points: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "".join(points))
    return path


@pytest.fixture
def fake_tool(tmp_path) -> Path:
    """A stand-in for verilator_coverage --write OUT --write-info INFO IN...:
    concatenates its inputs (the parser sums repeated keys, as a merge does)."""
    tool = tmp_path / "bin/verilator_coverage"
    tool.parent.mkdir()
    tool.write_text('#!/bin/sh\nout=$2; info=$4; shift 4\n'
                    'echo "# SystemC::Coverage-3" > "$out"\n'
                    'for f in "$@"; do grep -v "^#" "$f" >> "$out"; done\n: > "$info"\n')
    tool.chmod(0o755)
    (tmp_path / "bin/verilator").write_text("")
    return tool


def test_parse_and_classify_points(tmp_path):
    path = dat(tmp_path / "c.dat", point("line", 1, 3), point("branch", 2, 0),
               point("toggle", 3, 1, "q:0->1"), point("toggle", 3, 0, "q:1->0"),
               point("user", 4, 0), point("line", 1, 2))
    points = cov.parse_dat(path)
    assert len(points) == 5 and max(points.values()) == 5  # repeated key summed
    assert cov.code_metrics(points) == {"line": {"covered": 1, "total": 2},
                                        "toggle": {"covered": 1, "total": 2}}
    (tmp_path / "bad.dat").write_text(HEADER + "garbage\n")
    with pytest.raises(cov.CoverageError, match="bad.dat:2"):
        cov.parse_dat(tmp_path / "bad.dat")


def test_find_tool(tmp_path, fake_tool):
    assert cov.find_tool(fake_tool.parent / "verilator") == fake_tool
    with pytest.raises(cov.CoverageError, match="verilator_coverage not found"):
        cov.find_tool(tmp_path / "verilator")


def run_dir(tmp_path: Path, name: str, line_hits: int, toggle_hits: int, fbins: dict | None) -> Path:
    d = tmp_path / "runs" / name
    dat(d / sim.COVERAGE_DAT, point("line", 1, line_hits), point("line", 2, 1),
        point("toggle", 3, toggle_hits, "q:0->1"))
    if fbins is not None:
        (d / sim.FUNCOV_JSON).write_text(json.dumps({"schema": 1, "groups": {"g": {"p": fbins}}}))
    return d


def test_merge_ip_sums_runs(tmp_path, fake_tool):
    a = run_dir(tmp_path, "a", 0, 0, {"x": 1, "y": 0})
    b = run_dir(tmp_path, "b", 4, 0, {"x": 0, "y": 0, "z": 2})
    out = tmp_path / "w/ip/coverage"
    out.mkdir(parents=True)
    (out / "stale.txt").write_text("old")
    e = cov.merge_ip(out, [a, b], fake_tool)
    assert not (out / "stale.txt").exists()
    assert e["runs"] == 2 and e["dat_files"] == 2 and e["funcov_files"] == 2
    assert e["line"] == {"covered": 2, "total": 2, "pct": 100.0}
    assert e["toggle"] == {"covered": 0, "total": 1, "pct": 0.0}
    assert e["functional"] == {"covered": 2, "total": 3, "pct": 66.67, "uncovered": 1}
    detail = json.loads((out / "functional.json").read_text())
    assert detail["summary"]["uncovered"] == ["g.p.y"]
    assert (out / "merged.dat").is_file()


def test_merge_ip_without_data(tmp_path):
    e = cov.merge_ip(tmp_path / "cov", [], None)
    assert e["line"]["pct"] is None and e["functional"] == {
        "covered": 0, "total": 0, "pct": None, "uncovered": 0}


def entry(line=(8, 10), toggle=(1, 4), functional=(0, 0)):
    return {m: {"covered": c, "total": t} for m, (c, t) in
            zip(cov.METRICS, (line, toggle, functional))}


def test_floor_below_passes_and_above_fails_with_message():
    e = entry()
    assert cov.check_floors(e, {"line": (80.0, "level1.yaml"), "toggle": (25.0, "level1.yaml")}) == []
    fails = cov.check_floors(e, {"line": (80.5, "level2.yaml")})
    assert fails == ["line 80.00% (8/10) is below its floor of 80.5% (level2.yaml)"]
    fails = cov.check_floors(e, {"functional": (0.0, "level0.yaml")})
    assert fails == ["functional has no coverage points, below its floor of 0% (level0.yaml)"]


def test_effective_floors_take_the_highest_selected_level():
    floors = {0: {"line": 50.0}, 2: {"line": 90.0, "toggle": 10.0}}
    names = {0: "level0.yaml", 1: "level1.yaml", 2: "level2.yaml"}
    assert cov.effective_floors(floors, [0, 1], names) == {"line": (50.0, "level0.yaml")}
    assert cov.effective_floors(floors, [0, 2], names) == {
        "line": (90.0, "level2.yaml"), "toggle": (10.0, "level2.yaml")}
    assert cov.effective_floors(floors, [1], names) == {}


# ------------------------------------------------------------ level files

def set_floors(src: Path, level: int, text: str) -> None:
    path = src / f"dv/counter/regress/level{level}.yaml"
    path.write_text(path.read_text() + text)


def test_floors_in_level_files(fixture_src):
    ip = find_ip(discover(fixture_src), "counter")
    assert load_floors(ip) == {}
    set_floors(fixture_src, 2, "coverage_floors: {line: 90, functional: 12.5}\n")
    assert load_floors(ip) == {2: {"line": 90.0, "functional": 12.5}}
    assert [e.name for e in load_ip(ip)] == ["counts", "wraps_w4", "random_enable"]


@pytest.mark.parametrize("text, message", [
    ("coverage_floors: {}\n", "non-empty mapping"),
    ("coverage_floors: [line]\n", "non-empty mapping"),
    ("coverage_floors: {branch: 5}\n", "unknown metric"),
    ("coverage_floors: {line: 101}\n", r"percentage in \[0, 100\]"),
    ("coverage_floors: {line: true}\n", "percentage"),
    ("floors: {line: 5}\n", "optionally 'coverage_floors'"),
])
def test_floor_errors(fixture_src, text, message):
    set_floors(fixture_src, 1, text)
    ip = find_ip(discover(fixture_src), "counter")
    with pytest.raises(SchemaError, match=message):
        load_floors(ip)


def test_unknown_level_key_fails_test_loading_too(fixture_src):
    set_floors(fixture_src, 1, "floors: {line: 5}\n")
    with pytest.raises(SchemaError, match="optionally 'coverage_floors'"):
        load_ip(find_ip(discover(fixture_src), "counter"))


# ------------------------------------------------------------- run + report

def fake_run(fixture_src, tmp_path, monkeypatch, fake_tool, levels, floors_text=None):
    """regress.run with coverage, children faked: every test passes and
    leaves a coverage.dat (2/3 line points) and a funcov.json (1/2 bins)."""
    if floors_text:
        set_floors(fixture_src, levels[-1], floors_text)
    monkeypatch.setattr(sim, "find_verilator", lambda: fake_tool.parent / "verilator")
    monkeypatch.setattr(sim, "_run_child", lambda phase, job, log, timeout_s: (0, 0.1))
    specs = []

    def run_test(spec, workdir, verilator, test, module, testcase, seed, timeout_s):
        specs.append(spec)
        d = sim.test_dir(workdir, spec.ip, test, seed)
        dat(d / sim.COVERAGE_DAT, point("line", 1, 1), point("line", 2, 1), point("line", 3, 0))
        (d / sim.FUNCOV_JSON).write_text(json.dumps({"schema": 1, "groups": {"g": {"p": {"a": 1, "b": 0}}}}))
        return sim.Result("pass", 0.2, d / "sim.log")

    monkeypatch.setattr(sim, "run_test", run_test)
    ctx = Context(REPO_ROOT, fixture_src, tmp_path / "work")
    out, err = io.StringIO(), io.StringIO()
    summary = regress.run(ctx, regress.select(ctx, levels, ["counter"]), levels, out=out, coverage=True)
    assert all(s.coverage for s in specs) and specs
    code = regress.report(summary, out, err)
    return summary, code, out.getvalue(), err.getvalue(), ctx


def test_run_with_coverage_and_floor_below(fixture_src, tmp_path, monkeypatch, fake_tool):
    summary, code, out, err, ctx = fake_run(
        fixture_src, tmp_path, monkeypatch, fake_tool, [0], "coverage_floors: {line: 60, functional: 50}\n")
    c = summary["coverage"]["ips"]["counter"]
    assert c["line"] == {"covered": 2, "total": 3, "pct": 66.67}
    assert c["functional"]["pct"] == 50.0 and c["floors"] == {"functional": 50.0, "line": 60.0}
    assert c["failures"] == [] and c["dir"] == "counter/coverage"
    assert code == 0 and summary["passed"] is True
    assert "counter   66.7% (2/3)" in out and "ok" in out.splitlines()[-2]
    assert (ctx.workdir / "counter/coverage/merged.dat").is_file()
    assert json.loads(regress.summary_path(ctx.workdir).read_text())["coverage"] == summary["coverage"]


def test_run_with_floor_above_fails(fixture_src, tmp_path, monkeypatch, fake_tool):
    summary, code, out, err, _ = fake_run(
        fixture_src, tmp_path, monkeypatch, fake_tool, [0], "coverage_floors: {line: 70}\n")
    assert code == 1 and summary["passed"] is False and summary["coverage"]["passed"] is False
    assert ("error: coverage floor: counter: line 66.67% (2/3) is below its floor of 70% "
            "(level0.yaml)") in err
    assert "FAIL" in out and "FAILED" in out


def test_floor_of_an_unselected_level_is_not_checked(fixture_src, tmp_path, monkeypatch, fake_tool):
    set_floors(fixture_src, 2, "coverage_floors: {line: 99}\n")
    summary, code, *_ = fake_run(fixture_src, tmp_path, monkeypatch, fake_tool, [0])
    assert code == 0 and summary["coverage"]["ips"]["counter"]["floors"] == {}


def test_coverage_off_leaves_summary_unchanged(fixture_src, tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: Path("/fake/verilator"))
    monkeypatch.setattr(sim, "_run_child", lambda phase, job, log, timeout_s: (0, 0.1))
    monkeypatch.setattr(sim, "run_test", lambda spec, *a: sim.Result("pass", 0.2, Path("x.log")))
    set_floors(fixture_src, 0, "coverage_floors: {line: 100}\n")  # not checked without --coverage
    ctx = Context(REPO_ROOT, fixture_src, tmp_path / "work")
    summary = regress.run(ctx, regress.select(ctx, [0]), [0], out=io.StringIO())
    assert summary["coverage"] is None and summary["passed"] is True
    out = io.StringIO()
    assert regress.report(summary, out, io.StringIO()) == 0 and "coverage" not in out.getvalue()


def test_coverage_build_key_differs():
    ip = find_ip(discover(Path(__file__).resolve().parents[2] / "build/tests/fixtures/src"), "counter")
    plain, covd = sim.BuildSpec(ip, "counter"), sim.BuildSpec(ip, "counter", coverage=True)
    assert plain.key == "counter" and covd.key.startswith("counter-") and covd.key != plain.key


def test_reserved_coverage_test_name(fixture_src):
    from regress.schema import parse_entry
    with pytest.raises(SchemaError, match="reserved"):
        parse_entry({"name": "coverage", "module": "m", "top": "t", "timeout_s": 1}, "a", 0, "w")


# ---------------------------------------------------------------- real sim

HAVE_SIM = sim.find_verilator() is not None and __import__("importlib").util.find_spec("cocotb") is not None


@pytest.mark.skipif(not HAVE_SIM, reason="verilator or cocotb not available")
def test_e2e_regress_coverage_and_floors(fixture_src, tmp_path, capsys):
    from cli.main import main

    work = tmp_path / "work"
    argv = ["regress", "--src-root", str(fixture_src), "--workdir", str(work),
            "--level", "0", "--ip", "counter", "--coverage"]
    assert main(argv) == 0, capsys.readouterr()
    c = json.loads((work / "regress/summary.json").read_text())["coverage"]["ips"]["counter"]
    assert c["dat_files"] == 1 and c["funcov_files"] == 1
    assert c["line"]["total"] > 0 and c["toggle"]["total"] > 0 and c["line"]["covered"] > 0
    assert c["functional"] == {"covered": 1, "total": 2, "pct": 50.0, "uncovered": 1}
    assert (work / "counter/coverage/merged.dat").is_file()
    assert (work / "counter/coverage/merged.info").is_file()
    assert "counter" in capsys.readouterr().out

    set_floors(fixture_src, 0, "coverage_floors: {functional: 60, line: 1}\n")
    assert main(argv) == 1
    err = capsys.readouterr().err
    assert "error: coverage floor: counter: functional 50.00% (1/2) is below its floor of 60%" in err
