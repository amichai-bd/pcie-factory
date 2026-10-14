import importlib.util
import json
import os
from pathlib import Path

import pytest

from build import sim
from build.ip import Ip, discover, find_ip

HAVE_SIM = sim.find_verilator() is not None and importlib.util.find_spec("cocotb") is not None
needs_sim = pytest.mark.skipif(not HAVE_SIM, reason="verilator or cocotb not available")


def write_results(path: Path, body: str) -> Path:
    path.write_text(f'<testsuites><testsuite name="t">{body}</testsuite></testsuites>')
    return path


def test_parse_results(tmp_path):
    assert sim.parse_results(tmp_path / "missing.xml")[0] == "error"
    assert sim.parse_results(write_results(tmp_path / "e.xml", ""))[0] == "error"
    ok = write_results(tmp_path / "p.xml", '<testcase name="a"/><testcase name="b"/>')
    assert sim.parse_results(ok) == ("pass", "2 case(s) passed")
    bad = write_results(tmp_path / "f.xml",
                        '<testcase name="a"/><testcase name="b"><failure/></testcase>')
    assert sim.parse_results(bad) == ("fail", "failed: b")
    err = write_results(tmp_path / "x.xml", '<testcase name="c"><error/></testcase>')
    assert sim.parse_results(err)[0] == "fail"


def test_build_key_and_dirs(tmp_path):
    ip = Ip("a/b", tmp_path / "a/b")
    plain = sim.BuildSpec(ip, "top")
    assert plain.key == "top"
    assert plain.build_dir(tmp_path / "w") == tmp_path / "w/a/b/build/top"
    p1 = sim.BuildSpec(ip, "top", {"W": 4})
    p2 = sim.BuildSpec(ip, "top", {"W": 5})
    wv = sim.BuildSpec(ip, "top", {}, waves=True)
    keys = {plain.key, p1.key, p2.key, wv.key}
    assert len(keys) == 4 and all(k.startswith("top") for k in keys)
    assert sim.BuildSpec(ip, "top", {"W": 4}).key == p1.key  # deterministic
    assert sim.test_dir(tmp_path, ip, "t", 3) == tmp_path / "a/b/t/3"


def test_find_verilator_fallbacks(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("VERILATOR_ROOT", raising=False)
    monkeypatch.setattr(sim, "VERILATOR_FALLBACK_DIRS", [])
    assert sim.find_verilator() is None
    exe = tmp_path / "vroot/bin/verilator"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("VERILATOR_ROOT", str(tmp_path / "vroot"))
    assert sim.find_verilator() == exe
    monkeypatch.delenv("VERILATOR_ROOT")
    monkeypatch.setattr(sim, "VERILATOR_FALLBACK_DIRS", [exe.parent])
    assert sim.find_verilator() == exe


# ---------------------------------------------------------------- real sim


@needs_sim
def test_e2e_pass_fail_timeout_and_build_error(fixture_src, tmp_path):
    work = tmp_path / "work"
    verilator = sim.find_verilator()
    ip = find_ip(discover(fixture_src), "counter")
    spec = sim.BuildSpec(ip, "counter")
    assert sim.build(spec, work, verilator).status == "pass"

    ok = sim.run_test(spec, work, verilator, "counts", "tests.test_counter", "counts", 5, 60)
    assert ok.status == "pass", ok.log.read_text()
    tdir = work / "counter/counts/5"
    assert (tdir / "results.xml").is_file() and ok.log == tdir / "sim.log"
    assert "Seeding Python random module with 5" in ok.log.read_text()

    bad = sim.run_test(spec, work, verilator, "neg", "tests.test_negative", "always_fails", 1, 60)
    assert bad.status == "fail" and "always_fails" in bad.detail

    hung = sim.run_test(spec, work, verilator, "hang", "tests.test_negative", "hangs", 1, 3)
    assert hung.status == "timeout"

    (ip.path / "rtl/counter.sv").write_text("module counter(; endmodule\n")
    assert sim.build(sim.BuildSpec(ip, "counter"), work, verilator).status == "error"


@needs_sim
def test_e2e_regress_cli_all_levels(fixture_src, tmp_path, capsys):
    from cli.main import main

    work = tmp_path / "work"
    argv = ["regress", "--src-root", str(fixture_src), "--workdir", str(work),
            "--level", "0", "--level", "1", "--level", "2", "--jobs", "4"]
    assert main(argv) == 0, capsys.readouterr().out
    summary = json.loads((work / "regress/summary.json").read_text())
    assert summary["passed"] is True
    assert {(t["ip"], t["name"]) for t in summary["tests"]} == {
        ("counter", "counts"), ("counter", "wraps_w4"),
        ("counter", "random_enable"), ("counter/inc", "exhaustive"),
    }
    assert summary["totals"]["level2"]["tests"] == 2  # seeds: 2
    for t in summary["tests"]:
        assert (work / t["log"]).is_file()

    # A failing test makes the run fail and is reported in the summary.
    (fixture_src / "counter/dv/regress/level0.yaml").write_text(
        "tests:\n  - {name: neg, module: tests.test_negative, testcase: always_fails,"
        " top: counter, timeout_s: 60}\n")
    argv = ["regress", "--src-root", str(fixture_src), "--workdir", str(work),
            "--level", "0", "--ip", "counter", "--seed", "9"]
    assert main(argv) == 1
    summary = json.loads((work / "regress/summary.json").read_text())
    assert [(t["name"], t["seed"], t["status"]) for t in summary["tests"]] == [("neg", 9, "fail")]


@needs_sim
def test_e2e_sim_cli_waves(fixture_src, tmp_path):
    from cli.main import main

    work = tmp_path / "work"
    argv = ["sim", "--src-root", str(fixture_src), "--workdir", str(work),
            "--ip", "counter/inc", "--test", "exhaustive", "--seed", "3", "--waves"]
    assert main(argv) == 0
    tdir = work / "counter/inc/exhaustive/3"
    assert (tdir / "results.xml").is_file()
    assert any(p.suffix in (".vcd", ".fst") for p in tdir.iterdir()), os.listdir(tdir)
