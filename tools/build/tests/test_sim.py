import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
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
    (tmp_path / "t.xml").write_text("<testsuites><testsuite><testca")
    assert sim.parse_results(tmp_path / "t.xml")[0] == "error"  # truncated


FATAL_LOG = """\
   244.00ns INFO     cocotb.tb   cycle 28: the stall starts
[248000] %Fatal: top.sv:359: Assertion failed in tb.u_dut.a_one: assertion a_one failed in tb.u_dut.a_one
%Error: top.sv:359: Verilog $stop
"""


def test_fatal_label():
    assert sim.fatal_label(FATAL_LOG) == (FATAL_LOG.splitlines()[1].strip(), "a_one")
    assert sim.fatal_label("[10] %Fatal: top.sv:3: plain message\n")[1] is None
    assert sim.fatal_label("all passed\n") == (None, None)


def test_expected_fatal():
    assert sim.expected_fatal(FATAL_LOG, "a_one", "fail", "failed: t") == (
        "pass", "stopped on assertion a_one as expected")
    status, detail = sim.expected_fatal(FATAL_LOG, "a_two", "fail", "failed: t")
    assert status == "fail" and "stopped on: [248000] %Fatal" in detail
    assert sim.expected_fatal("[10] %Fatal: top.sv:3: plain\n", "a_one", "error", "x")[0] == "fail"
    assert sim.expected_fatal("ok\n", "a_one", "pass", "1 case(s) passed") == (
        "fail", "expected a stop on assertion a_one, but the run passed")
    assert sim.expected_fatal("ok\n", "a_one", "fail", "failed: t") == (
        "fail", "expected a stop on assertion a_one, but the run ended without a fatal "
        "(failed: t)")
    assert sim.expected_fatal(FATAL_LOG, "a_one", "timeout", "timed out") == (
        "timeout", "timed out")


needs_proc = pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")


@pytest.fixture
def sleeper(tmp_path, monkeypatch):
    """Make child processes a shell that sleeps (and spawns a grandchild in
    the same process group), so process control is testable without a sim."""
    script = tmp_path / "sleeper.sh"
    script.write_text("#!/bin/sh\nsleep 60 &\nsleep 60\n")
    script.chmod(0o755)
    monkeypatch.setattr(sim.sys, "executable", str(script))
    yield
    sim._STOPPING.clear()


def group_alive(pgid: int) -> bool:
    """True if a live (non-zombie) process is in group ``pgid``. Killed
    grandchildren may linger as zombies until init reaps them."""
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(fields[2]) == pgid and fields[0] != "Z":
            return True
    return False


def cwd_alive(path: Path) -> bool:
    """True if a live (non-zombie) process runs in directory ``path``.
    A zombie has no cwd, so ``readlink`` fails and it is not counted."""
    for proc_dir in Path("/proc").glob("[0-9]*"):
        try:
            if Path(os.readlink(proc_dir / "cwd")).resolve() == path:
                return True
        except OSError:
            pass
    return False


def dies(alive, within_s: float = 30.0) -> bool:
    """Poll until ``alive()`` is false; False if it is still true after
    ``within_s``. ``killpg`` only queues SIGKILL and ``wait()`` reaps only the
    group leader, so a grandchild (such as the simulator executable) can still
    be running for a moment after the code under test returns, especially on
    a loaded host. Poll with a deadline; never assert right after the kill.
    The deadline is far above the kill latency (well under a second), and a
    process that was abandoned instead of killed never dies, so it still
    fails."""
    deadline = time.monotonic() + within_s
    while alive():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def group_dies(pgid: int, within_s: float = 30.0) -> bool:
    """Poll until no live process is left in group ``pgid`` (see ``dies``)."""
    return dies(lambda: group_alive(pgid), within_s)


@needs_proc
def test_build_timeout_kills_group(fixture_src, tmp_path, monkeypatch, sleeper):
    monkeypatch.setattr(sim, "BUILD_TIMEOUT_S", 0.5)
    pids = []
    real_popen = sim.subprocess.Popen

    def popen(*a, **kw):
        proc = real_popen(*a, **kw)
        pids.append(proc.pid)
        return proc

    monkeypatch.setattr(sim.subprocess, "Popen", popen)
    ip = find_ip(discover(fixture_src), "counter")
    res = sim.build(sim.BuildSpec(ip, "counter"), tmp_path / "w", Path("/fake/verilator"))
    assert res.status == "error" and "timed out" in res.detail
    assert res.runtime_s < 30  # 0.5 s timeout plus kill; generous for loaded hosts
    assert pids and group_dies(pids[0])


@needs_proc
def test_kill_all_stops_running_children(tmp_path, sleeper):
    errors = []

    def work():
        try:
            sim._run_child("test", {}, tmp_path / "x/sim.log", timeout_s=None)
        except KeyboardInterrupt as exc:
            errors.append(exc)

    t = threading.Thread(target=work)
    t.start()
    deadline = time.monotonic() + 30
    while not sim._ACTIVE and time.monotonic() < deadline:
        time.sleep(0.05)
    (proc,) = list(sim._ACTIVE)
    assert group_alive(proc.pid)
    sim.kill_all()
    t.join(30)
    assert not t.is_alive() and len(errors) == 1
    assert group_dies(proc.pid) and not sim._ACTIVE
    with pytest.raises(KeyboardInterrupt):  # nothing new starts once stopping
        sim._run_child("test", {}, tmp_path / "y/sim.log", timeout_s=None)


def test_build_key_and_dirs(tmp_path):
    ip = Ip("a_b", tmp_path / "src")
    plain = sim.BuildSpec(ip, "top")
    assert plain.key == "top"
    assert plain.build_dir(tmp_path / "w") == tmp_path / "w/a_b/build/top"
    p1 = sim.BuildSpec(ip, "top", {"W": 4})
    p2 = sim.BuildSpec(ip, "top", {"W": 5})
    wv = sim.BuildSpec(ip, "top", {}, waves=True)
    be = sim.BuildSpec(ip, "top", backend="fpga")
    keys = {plain.key, p1.key, p2.key, wv.key, be.key}
    assert len(keys) == 5 and all(k.startswith("top") for k in keys)
    assert sim.BuildSpec(ip, "top", {"W": 4}).key == p1.key  # deterministic
    assert sim.test_dir(tmp_path, ip, "t", 3) == tmp_path / "a_b/t/3"


def test_sources_for_backend(tmp_path):
    """A backend build swaps each portable file for its backend file and
    appends the backend's DV stand-ins; a bad backend is an error."""
    src = tmp_path / "src"
    rtl = src / "rtl" / "common"
    rtl.mkdir(parents=True)
    a, b = rtl / "a.sv", rtl / "b.sv"
    for p in (a, b, rtl / "b_alt.sv"):
        p.write_text("")
    model = src / "dv" / "common" / "tb" / "prim.sv"
    model.parent.mkdir(parents=True)
    model.write_text("")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "backends.yaml").write_text(
        "fpga:\n  suffix: alt\n  dv_models: [dv/common/tb/prim.sv]\n")
    ip = Ip("common", src, shared=True)
    assert sim.sources_for(sim.BuildSpec(ip, "b"), [a, b], cfg) == [a, b]
    got = sim.sources_for(sim.BuildSpec(ip, "b", backend="fpga"), [a, b], cfg)
    assert got == [a, (rtl / "b_alt.sv").resolve(), model.resolve()]
    with pytest.raises(sim.backends.BackendError, match="not a flow kind"):
        sim.sources_for(sim.BuildSpec(ip, "b", backend="asic"), [a, b], cfg)


def test_build_bad_backend_is_a_build_error(fixture_src, tmp_path):
    ip = Ip("counter", fixture_src)
    res = sim.build(sim.BuildSpec(ip, "counter", backend="nope"), tmp_path / "w",
                    Path("/fake/verilator"), cfg_dir=tmp_path)
    assert res.status == "error" and "not a flow kind" in res.detail


def test_find_verilator_fallbacks(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("VERILATOR_ROOT", raising=False)
    monkeypatch.setattr(sim, "fallback_dirs", lambda: [])
    assert sim.find_verilator() is None
    exe = tmp_path / "vroot/bin/verilator"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("VERILATOR_ROOT", str(tmp_path / "vroot"))
    assert sim.find_verilator() == exe
    monkeypatch.delenv("VERILATOR_ROOT")
    monkeypatch.setattr(sim, "fallback_dirs", lambda: [exe.parent])
    assert sim.find_verilator() == exe


def test_fallback_dir_from_host_cfg():
    import yaml
    from build.ip import REPO_ROOT

    prefix = yaml.safe_load((REPO_ROOT / "cfg/host.yaml").read_text())["verilator"]["prefix"]
    assert sim.fallback_dirs() == [Path(prefix) / "bin"]
    assert str(Path(prefix) / "bin") in sim.not_found_message()


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

    # Builds pass --assert: an RTL assertion stops the simulation.
    fired = sim.run_test(spec, work, verilator, "asrt", "tests.test_negative", "assertion_fires",
                         1, 60)
    assert fired.status in ("fail", "error"), fired.detail
    assert "en_low_in_reset failed" in fired.log.read_text()

    # expect_fatal: only a stop on the named assertion passes.
    def expect(name, case, label):
        return sim.run_test(spec, work, verilator, name, "tests.test_negative", case, 1, 60,
                            expect_fatal=label)
    hit = expect("asrt_ok", "assertion_fires", "en_low_in_reset")
    assert hit.status == "pass", (hit.detail, hit.log.read_text())
    other = expect("asrt_other", "assertion_fires", "a_other")
    assert other.status == "fail" and "stopped on:" in other.detail, other.detail
    clean = sim.run_test(spec, work, verilator, "clean", "tests.test_counter", "counts", 1, 60,
                         expect_fatal="en_low_in_reset")
    assert clean.status == "fail" and clean.detail.endswith("but the run passed"), clean.detail
    failed = expect("failed", "always_fails", "en_low_in_reset")
    assert failed.status == "fail" and "ended without a fatal" in failed.detail, failed.detail

    hung = sim.run_test(spec, work, verilator, "hang", "tests.test_negative", "hangs", 1, 3)
    assert hung.status == "timeout"
    hang_dir = (work / "counter/hang/1").resolve()
    # The simulator was killed, not just abandoned. It is a grandchild in the
    # killed group and may take a moment to exit after run_test returns.
    assert dies(lambda: cwd_alive(hang_dir))

    (ip.rtl_dir / "counter.sv").write_text("module counter(; endmodule\n")
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
        ("counter", "random_enable"), ("counter_inc", "exhaustive"),
    }
    assert summary["totals"]["level2"]["tests"] == 2  # seeds: 2
    for t in summary["tests"]:
        assert (work / t["log"]).is_file()

    # A broken sim.f in one IP is a build error there; the other IP still runs.
    (fixture_src / "dv/counter/counter.dv.f").write_text("missing_tb.sv\n")
    argv0 = ["regress", "--src-root", str(fixture_src), "--workdir", str(work), "--level", "0"]
    assert main(argv0) == 1
    summary = json.loads((work / "regress/summary.json").read_text())
    assert {t["ip"]: t["status"] for t in summary["tests"]} == {
        "counter": "error", "counter_inc": "pass"}
    (fixture_src / "dv/counter/counter.dv.f").write_text("")

    # A failing test makes the run fail and is reported in the summary.
    (fixture_src / "dv/counter/regress/level0.yaml").write_text(
        "tests:\n  - {name: neg, module: tests.test_negative, testcase: always_fails,"
        " top: counter, timeout_s: 60}\n")
    argv = ["regress", "--src-root", str(fixture_src), "--workdir", str(work),
            "--level", "0", "--ip", "counter", "--seed", "9"]
    assert main(argv) == 1
    summary = json.loads((work / "regress/summary.json").read_text())
    assert [(t["name"], t["seed"], t["status"]) for t in summary["tests"]] == [("neg", 9, "fail")]

    # An expect_fatal entry passes only by stopping on its assertion.
    for label, code in (("en_low_in_reset", 0), ("a_other", 1)):
        (fixture_src / "dv/counter/regress/level0.yaml").write_text(
            "tests:\n  - {name: fires, module: tests.test_negative, testcase: assertion_fires,"
            f" top: counter, timeout_s: 60, expect_fatal: {label}, coverage_seeds: 0}}\n")
        assert main(argv) == code
        summary = json.loads((work / "regress/summary.json").read_text())
        assert [t["status"] for t in summary["tests"]] == ["pass" if code == 0 else "fail"]


@needs_sim
def test_e2e_sim_cli_waves(fixture_src, tmp_path):
    from cli.main import main

    work = tmp_path / "work"
    argv = ["sim", "--src-root", str(fixture_src), "--workdir", str(work),
            "--ip", "counter_inc", "--test", "exhaustive", "--seed", "3", "--waves"]
    assert main(argv) == 0
    tdir = work / "counter_inc/exhaustive/3"
    assert (tdir / "results.xml").is_file()
    assert any(p.suffix in (".vcd", ".fst") for p in tdir.iterdir()), os.listdir(tdir)


def test_wait_rusage_counts_the_grandchild_cpu_time():
    # The simulator runs under the cocotb runner, a grandchild of regress:
    # its CPU time must be in the figure the level budgets compare.
    burn = ("import time\nend = time.process_time() + 1.0\n"
            "while time.process_time() < end:\n    pass\n")  # about 1 s of CPU
    parent = f"import subprocess, sys\nsubprocess.run([sys.executable, '-c', {burn!r}])\nsys.exit(3)\n"
    proc = subprocess.Popen([sys.executable, "-c", parent])
    code, cpu = sim._wait_rusage(proc, timeout_s=60)
    assert code == 3 and proc.returncode == 3
    assert cpu > 0.5  # the grandchild's second, not just the parent's start-up


def test_wait_rusage_times_out():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            sim._wait_rusage(proc, timeout_s=0.2)
    finally:
        proc.kill()
        proc.wait()
