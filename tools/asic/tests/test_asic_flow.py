"""./build asic: config generation, ORFS settings, commands and the run loop.

The run tests use a fake native ORFS root (tests/fixtures/fake_orfs) whose
env.sh puts a fake ``make`` first on PATH. It copies the fixture reports into
WORK_HOME, so the whole run, including metrics.json, is exercised without
ORFS. FAKE_MAKE=fail|faillog|sleep switches it to a failure, a failure that
prints fixtures/flow_fail.log, or a hang.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from asic import flow
from build.ip import discover, find_ip, make_context
from cli.main import main

HERE = Path(__file__).resolve().parent
FAKE_ORFS = HERE / "fixtures" / "fake_orfs"

MK = """\
# fixture knobs
export DESIGN_NAME = counter
export SYNTH_HDL_FRONTEND = slang
export CORE_UTILIZATION = 30
"""
SDC = "create_clock -name clk -period 8.5 [get_ports clk_i]\n"


@pytest.fixture
def asic_src(fixture_src):
    asic = fixture_src / "asic" / "counter"
    asic.mkdir(parents=True)
    (asic / "sky130hd.mk").write_text(MK)
    (asic / "counter.sdc").write_text(SDC)
    return fixture_src


@pytest.fixture
def ctx(asic_src, tmp_path):
    return make_context(str(asic_src), str(tmp_path / "work"))


def plan_for(ctx, ip_id="counter"):
    return flow.make_plan(ctx, find_ip(discover(ctx.src_root), ip_id), "sky130hd")


def native_env(monkeypatch, mode="ok"):
    for key in (flow.ENV_RUNNER,):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(flow.ENV_ROOT, str(FAKE_ORFS))
    monkeypatch.setenv("FAKE_MAKE", mode)


# --- inputs and config.mk ---

def test_config_mk_flattens_filelist(ctx):
    plan = plan_for(ctx)
    text = flow.render_config_mk(plan)
    src = ctx.src_root
    assert f"include {src / 'asic' / 'counter' / 'sky130hd.mk'}" in text
    assert "export PLATFORM = sky130hd" in text
    assert "export DESIGN_NICKNAME = counter" in text
    assert f"export SDC_FILE = {src / 'asic' / 'counter' / 'counter.sdc'}" in text
    # Sub-IP source first, then the top, as the filelist orders them.
    files = text.split("export VERILOG_FILES = ", 1)[1].split("export ", 1)[0]
    assert files.index("inc.sv") < files.index("counter.sv")
    assert f"export VERILOG_INCLUDE_DIRS = {src / 'rtl' / 'counter' / 'include'}" in text
    # The flow adds SYNTHESIS: the assertion macros compile out.
    assert "export VERILOG_DEFINES = -DCOUNTER_FIXTURE -DSYNTHESIS" in text
    assert plan.design == "counter" and plan.clock_period_ns == 8.5


def test_config_mk_is_deterministic(ctx):
    assert flow.render_config_mk(plan_for(ctx)) == flow.render_config_mk(plan_for(ctx))


def test_nickname_and_output_follow_the_ip_id(ctx, tmp_path):
    plan = plan_for(ctx)
    assert plan.nick == "counter"
    assert plan.out == tmp_path / "work" / "counter" / "asic"


@pytest.mark.parametrize("bad, message", [
    ("export VERILOG_FILES = a.sv\n", "VERILOG_FILES"),
    ("export SDC_FILE = x.sdc\n", "SDC_FILE"),
    ("PLATFORM ?= sky130hd\n", "PLATFORM"),
    ("override export VERILOG_DEFINES += -DX\n", "VERILOG_DEFINES"),
    ("define SDC_FILE\nx.sdc\nendef\n", "SDC_FILE"),
    ("export RESULTS_DIR = /tmp\n", "RESULTS_DIR"),
    ("DESIGN_CONFIG := x\n", "DESIGN_CONFIG"),
    ("NUM_CORES = 64\n", "NUM_CORES"),
    ("export ASIC_INPUT_DIR = /tmp\n", "ASIC_INPUT_DIR"),
])
def test_mk_must_not_set_tool_owned(ctx, bad, message):
    (ctx.src_root / "asic" / "counter" / "sky130hd.mk").write_text(MK + bad)
    with pytest.raises(flow.AsicError, match=message):
        plan_for(ctx)


def test_mk_needs_design_name(ctx):
    (ctx.src_root / "asic" / "counter" / "sky130hd.mk").write_text("CORE_UTILIZATION = 1\n")
    with pytest.raises(flow.AsicError, match="DESIGN_NAME is required"):
        plan_for(ctx)


def test_mk_continuation_lines_are_read(tmp_path):
    mk = tmp_path / "x.mk"
    mk.write_text("export A = 1 \\\n  2\n# B = 3\nC := 4\n")
    assert flow.read_design_mk(mk) == {"A": "1    2", "C": "4"}


def test_missing_asic_dir(fixture_src, tmp_path):
    c = make_context(str(fixture_src), str(tmp_path))
    with pytest.raises(flow.AsicError, match="no ASIC config"):
        plan_for(c)


def test_needs_exactly_one_sdc(ctx):
    (ctx.src_root / "asic" / "counter" / "other.sdc").write_text(SDC)
    with pytest.raises(flow.AsicError, match="exactly one .sdc"):
        plan_for(ctx)


def test_unknown_platform(ctx):
    ip = find_ip(discover(ctx.src_root), "counter")
    with pytest.raises(flow.AsicError, match="unknown platform"):
        flow.make_plan(ctx, ip, "nope")


def test_config_mk_exports_the_input_dir_before_the_knobs(ctx):
    text = flow.render_config_mk(plan_for(ctx))
    asic = ctx.src_root / "asic" / "counter"
    assert f"export ASIC_INPUT_DIR = {asic}\n" in text
    assert text.index("export ASIC_INPUT_DIR") < text.index(f"include {asic / 'sky130hd.mk'}")


# --- source backends (cfg/backends.yaml: asic -> suffix sky130) ---

BACKEND_SV = """\
module inc (input logic clk_i);
endmodule
"""


def test_backend_file_replaces_its_portable_file_in_place(ctx):
    rtl = ctx.src_root / "rtl" / "counter_inc"
    alt = rtl / "inc_sky130.sv"
    alt.write_text(BACKEND_SV)
    plan = plan_for(ctx)
    assert plan.backends == [((rtl / "inc.sv").resolve(), alt.resolve())]
    assert (rtl / "inc.sv").resolve() not in plan.sources
    files = flow.render_config_mk(plan).split("export VERILOG_FILES = ", 1)[1].split("export ", 1)[0]
    # Filelist order is kept: the backend sits where the portable file was.
    assert files.index("inc_sky130.sv") < files.index("counter.sv")


def test_no_backend_file_keeps_the_filelist(ctx):
    plan = plan_for(ctx)
    assert plan.backends == []
    assert all(not p.stem.endswith("_sky130") for p in plan.sources)


def test_other_flows_backend_is_ignored(ctx):
    rtl = ctx.src_root / "rtl" / "counter_inc"
    (rtl / "inc_intel.sv").write_text(BACKEND_SV)
    assert plan_for(ctx).backends == []


def test_listed_backend_file_is_an_error(ctx):
    rtl = ctx.src_root / "rtl" / "counter_inc"
    (rtl / "inc_sky130.sv").write_text(BACKEND_SV)
    f = rtl / "counter_inc.rtl.f"
    f.write_text(f.read_text() + "inc_sky130.sv\n")
    with pytest.raises(flow.AsicError, match="lists a backend file"):
        plan_for(ctx)


# --- ORFS settings ---

def write_cfg(tmp_path, body):
    cfg = tmp_path / "cfg"
    cfg.mkdir(exist_ok=True)
    (cfg / "host.yaml").write_text(body)
    return cfg


DOCKER_CFG = """\
hosts:
  laptop:
    python: x
  ci:
    orfs_root:
    orfs_image: "img:tag@sha256:abc"
    orfs_commit: c0ffee
"""


def test_orfs_from_cfg(tmp_path):
    orfs = flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {})
    assert orfs == flow.Orfs("docker", flow.IMAGE_ROOT, "img:tag@sha256:abc", "cfg", "c0ffee")


def test_orfs_env_root_selects_native(tmp_path):
    orfs = flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {flow.ENV_ROOT: "/x"})
    assert (orfs.runner, orfs.root, orfs.source, orfs.commit) == ("native", "/x", "env", None)


def test_orfs_env_image_is_ignored(tmp_path):
    orfs = flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {"ORFS_IMAGE": "other:1"})
    assert (orfs.image, orfs.commit, orfs.source) == ("img:tag@sha256:abc", "c0ffee", "cfg")


def test_orfs_cores(tmp_path):
    assert flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {}).cores == flow.DEFAULT_CORES
    body = DOCKER_CFG + "    orfs_cores: 6\n"
    assert flow.load_orfs(write_cfg(tmp_path, body), {}).cores == 6


def test_orfs_cfg_native_root(tmp_path):
    orfs = flow.load_orfs(write_cfg(tmp_path, "hosts:\n  h:\n    orfs_root: /o\n"), {})
    assert (orfs.runner, orfs.root) == ("native", "/o")


@pytest.mark.parametrize("body, env, message", [
    ("hosts:\n  h:\n    python: x\n", {}, "no ORFS configured"),
    ("other: 1\n", {flow.ENV_RUNNER: "podman"}, "is not one of"),
    ("other: 1\n", {flow.ENV_RUNNER: "docker"}, "needs orfs_image"),
    ("hosts:\n  h:\n    orfs_image: img:tag\n", {}, "not pinned by digest"),
    ("hosts:\n  h:\n    orfs_image: i@sha256:a\n    orfs_cores: 0\n", {}, "orfs_cores"),
    ("other: 1\n", {flow.ENV_RUNNER: "native"}, "needs the ORFS root"),
    ("hosts: [1, 2]\n", {}, "must be a mapping"),
    ("hosts:\n  a:\n    orfs_image: x\n  b:\n    orfs_root: /y\n", {}, "more than one host"),
])
def test_orfs_config_errors(tmp_path, body, env, message):
    with pytest.raises(flow.AsicError, match=message):
        flow.load_orfs(write_cfg(tmp_path, body), env)


def test_repo_cfg_pins_an_image_by_digest():
    from build.ip import REPO_ROOT
    orfs = flow.load_orfs(REPO_ROOT / "cfg", {})
    assert orfs.runner == "docker" and "@sha256:" in orfs.image and orfs.commit


# --- commands ---

def test_docker_wrap_mounts_and_image(ctx, tmp_path):
    plan = plan_for(ctx)
    orfs = flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {})
    argv = flow.wrap(orfs, plan, ctx, "true", "n")
    assert argv[:2] == ["docker", "run"] and "img:tag@sha256:abc" in argv
    for flag in ("--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                 f"--pids-limit={flow.DOCKER_PIDS}", f"--cpus={flow.DEFAULT_CORES}",
                 f"--memory={flow.DOCKER_MEMORY}"):
        assert flag in argv
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert f"{ctx.workdir}:{ctx.workdir}:rw" in mounts
    assert f"{ctx.repo_root}:{ctx.repo_root}:ro" in mounts
    assert f"{ctx.src_root}:{ctx.src_root}:ro" in mounts  # tmp src is outside the repo
    assert argv[-3:-1] == ["bash", "-c"]


def test_steps_run_finish_then_extra_then_clocks(ctx, tmp_path):
    plan = plan_for(ctx)
    orfs = flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {})
    steps = dict(flow.step_scripts(orfs, plan))
    assert list(steps) == ["versions", "flow", "extra", "clocks"]
    assert f"make -C {flow.IMAGE_ROOT}/flow" in steps["flow"] and steps["flow"].endswith(" finish")
    assert f"DESIGN_CONFIG={plan.config_mk}" in steps["flow"]
    assert f"NUM_CORES={flow.DEFAULT_CORES}" in steps["flow"]
    assert f"RUN_SCRIPT={flow.EXTRA_TCL}" in steps["extra"]
    assert steps["extra"].endswith(" run")
    assert f"RUN_SCRIPT={flow.CLOCKS_TCL}" in steps["clocks"] and steps["clocks"].endswith(" run")
    assert "RUN_LOG_NAME_STEM=asic_clocks" in steps["clocks"]


def test_dry_run_writes_config_only(asic_src, tmp_path, capsys, monkeypatch):
    native_env(monkeypatch)
    work = tmp_path / "work"
    argv = ["asic", "--src-root", str(asic_src), "--workdir", str(work), "--ip", "counter",
            "--dry-run"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert (work / "counter" / "asic" / "config.mk").is_file()
    assert not (work / "counter" / "asic" / "metrics.json").exists()
    assert "[asic] flow: bash -c" in out and " finish" in out
    # Printed paths are placeholders: CI job logs are public.
    assert str(tmp_path) not in out and str(asic_src) not in out
    assert "<workdir>/counter/asic/config.mk" in out


def test_cli_unknown_ip(asic_src, tmp_path, capsys):
    assert main(["asic", "--src-root", str(asic_src), "--workdir", str(tmp_path),
                 "--ip", "nope", "--dry-run"]) == 2
    assert "unknown IP" in capsys.readouterr().err


def test_cli_config_error_exits_2(fixture_src, tmp_path, capsys):
    assert main(["asic", "--src-root", str(fixture_src), "--workdir", str(tmp_path),
                 "--ip", "counter", "--dry-run"]) == 2
    assert "no ASIC config" in capsys.readouterr().err


# --- run loop with the fake ORFS ---

def test_run_writes_metrics(asic_src, tmp_path, capsys, monkeypatch):
    native_env(monkeypatch)
    work = tmp_path / "work"
    stale = work / "counter" / "asic" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    assert main(["asic", "--src-root", str(asic_src), "--workdir", str(work),
                 "--ip", "counter"]) == 0
    assert not stale.exists()  # each run starts clean
    doc = json.loads((work / "counter" / "asic" / "metrics.json").read_text())
    assert doc["status"] == "pass"
    assert doc["design"] == "counter" and doc["clock_period_ns"] == 8.5
    assert doc["timing"]["wns_ns"] == 0.0 and doc["timing"]["setup_ws_ns"] == 6.01343
    assert doc["max_fanout"]["value"] == 25 and doc["logic_depth"]["value"] == 4
    assert doc["toolchain"]["runner"] == "native"
    assert doc["work_home"] == "counter/asic/run"
    assert doc["toolchain"]["openroad"] == "26Q4-142-gd4b96380b"
    assert (work / "counter" / "asic" / "worst_path.txt").read_text().startswith(
        "worst setup path (clk)")
    log = (work / "counter" / "asic" / "flow.log").read_text()
    assert "fake make" in log and "### step extra" in log
    assert "[asic] pass: WNS 0.0 ns" in capsys.readouterr().out


def test_run_failure_is_exit_1(ctx, monkeypatch):
    native_env(monkeypatch, "fail")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(__import__("os").environ))
    code, doc = flow.run(ctx, plan, orfs, timeout_s=60)
    assert code == 1 and doc["status"] == "flow: fail (exit 3)"
    assert plan.metrics_json.is_file()
    assert "timing.setup_ws_ns" in flow.asic_metrics.missing(doc)


FAIL_LOG = HERE / "fixtures" / "flow_fail.log"


def test_run_failure_prints_every_error_line_then_tail(ctx, monkeypatch, capsys):
    native_env(monkeypatch, "faillog")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(__import__("os").environ))
    code, doc = flow.run(ctx, plan, orfs, timeout_s=60)
    assert code == 1 and doc["status"] == "flow: fail (exit 2)"
    fixture = FAIL_LOG.read_text().splitlines()
    errors = [line for line in fixture if flow.ERROR_RE.search(line)]
    # The fixture's tool errors sit before the last 30 lines: a tail-only
    # excerpt misses them.
    assert len(errors) == 5 and not any(e in fixture[-30:] for e in errors[:3])
    out = capsys.readouterr().out.splitlines()
    head = out.index("--- error lines in step flow in <workdir>/counter/asic/flow.log: 5 ---")
    tail = out.index("--- last 30 lines of step flow in <workdir>/counter/asic/flow.log ---")
    assert out[head + 1:head + 6] == errors
    assert head < tail and out[tail + 1:tail + 31] == fixture[-30:]
    assert not any(str(ctx.workdir) in line for line in out)


def test_run_failure_without_error_lines_says_so(ctx, monkeypatch, capsys):
    native_env(monkeypatch, "fail")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(__import__("os").environ))
    flow.run(ctx, plan, orfs, timeout_s=60)
    out = capsys.readouterr().out
    assert "--- error lines in step flow in <workdir>/counter/asic/flow.log: none matched ---" in out
    assert "--- last 1 lines of step flow" in out


def test_run_timeout_is_exit_1(ctx, monkeypatch):
    native_env(monkeypatch, "sleep")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(__import__("os").environ))
    code, doc = flow.run(ctx, plan, orfs, timeout_s=2)
    assert code == 1 and doc["status"] == "flow: timeout after 2s"


def test_run_missing_metric_is_exit_1(ctx, monkeypatch):
    native_env(monkeypatch, "noextra")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(__import__("os").environ))
    code, doc = flow.run(ctx, plan, orfs, timeout_s=60)
    assert code == 1
    assert doc["status"] == "incomplete: missing max_fanout.value, logic_depth.value"


def test_run_empty_clocks_is_incomplete(ctx, monkeypatch, capsys):
    native_env(monkeypatch, "noclocks")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(__import__("os").environ))
    code, doc = flow.run(ctx, plan, orfs, timeout_s=60)
    assert code == 1 and doc["status"] == "incomplete: missing timing.clocks"
    assert json.loads(plan.metrics_json.read_text())["status"] == doc["status"]
    assert doc["timing"]["clocks"] is None and doc["max_fanout"]["value"] == 25
    assert doc["logic_depth"]["value"] == 4
    assert doc["warnings"] == [flow.asic_metrics.clock_warning({"clocks": {}})]
    assert "[asic] warning: timing.clocks missing (required, run incomplete): " \
        "clock_slacks.tcl wrote no clock" in capsys.readouterr().out


def test_run_clocks_step_failure_fails_run(ctx, monkeypatch, capsys):
    native_env(monkeypatch, "clocksfail")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(__import__("os").environ))
    code, doc = flow.run(ctx, plan, orfs, timeout_s=60)
    assert code == 1 and doc["status"] == "clocks: fail (exit 3)"
    assert json.loads(plan.metrics_json.read_text())["status"] == doc["status"]
    # The other metrics are still recorded.
    assert doc["max_fanout"]["value"] == 25 and doc["logic_depth"]["value"] == 4
    assert doc["timing"]["clocks"] is None and doc["timing"]["setup_ws_ns"] is not None
    assert flow.asic_metrics.missing(doc) == ["timing.clocks"]
    assert len(doc["warnings"]) == 1 and "asic_clocks.json is missing" in doc["warnings"][0]
    assert "[asic] warning: timing.clocks missing (required, run incomplete)" \
        in capsys.readouterr().out


def test_run_clocks_step_timeout_fails_run(ctx, monkeypatch):
    native_env(monkeypatch, "clockssleep")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(__import__("os").environ))
    t0 = time.monotonic()
    code, doc = flow.run(ctx, plan, orfs, timeout_s=8)
    assert time.monotonic() - t0 < 60
    assert code == 1 and doc["status"] == "clocks: timeout after 8s"
    assert doc["max_fanout"]["value"] == 25 and doc["timing"]["clocks"] is None


def test_fake_orfs_is_executable():
    assert shutil.which("make", path=str(FAKE_ORFS / "bin")) is not None


def test_container_names_are_unique(ctx):
    plan = plan_for(ctx)
    a, b = flow.container_name(plan), flow.container_name(plan)
    assert a != b and a.startswith("asic-counter-")


def test_sigterm_kills_step_and_restores_handler(ctx, monkeypatch):
    import os
    import signal
    import threading
    native_env(monkeypatch, "sleep")
    plan = plan_for(ctx)
    orfs = flow.load_orfs(ctx.cfg_dir, dict(os.environ))
    before = signal.getsignal(signal.SIGTERM)
    threading.Timer(1.0, os.kill, (os.getpid(), signal.SIGTERM)).start()
    with pytest.raises(KeyboardInterrupt):
        flow.run(ctx, plan, orfs, timeout_s=60)
    assert signal.getsignal(signal.SIGTERM) is before


def test_redact_paths(tmp_path):
    from build.ip import Context
    repo = tmp_path / "repo"
    c = Context(repo, repo / "src", repo / "workdir")
    text = f"-v {repo}:{repo}:ro -v {repo}/workdir:{repo}/workdir:rw {repo}/src/a.sv"
    assert flow.redact(text, c) == ("-v <repo>:<repo>:ro -v <repo>/workdir:<repo>/workdir:rw "
                                    "<repo>/src/a.sv")
    other = Context(repo, tmp_path / "s", tmp_path / "w")
    assert flow.redact(f"{tmp_path}/w/x {tmp_path}/s/y", other) == "<workdir>/x <src>/y"


# Synthesis is independent evidence, never a substitute for routed metrics.
def synth_plan(ctx):
    return flow.make_plan(ctx, find_ip(discover(ctx.src_root), "counter"),
                          "sky130hd", synth_only=True)


def test_synth_only_steps_and_isolated_output(ctx):
    plan = synth_plan(ctx)
    orfs = flow.Orfs("native", str(FAKE_ORFS))
    assert plan.out == ctx.workdir / "counter/asic-synth"
    steps = dict(flow.step_scripts(orfs, plan))
    assert list(steps) == ["versions", "synth"]
    assert steps["synth"].endswith(" synth")


def test_synth_only_cli_preserves_full_metrics(ctx, monkeypatch):
    native_env(monkeypatch)
    full = plan_for(ctx)
    full.out.mkdir(parents=True)
    full.metrics_json.write_text("existing routed evidence")
    assert main(["asic", "--ip", "counter", "--synth-only", "--src-root", str(ctx.src_root),
                 "--workdir", str(ctx.workdir)]) == 0
    plan = synth_plan(ctx)
    doc = json.loads(plan.synthesis_json.read_text())
    assert doc["stage"] == "synthesis" and doc["status"] == "pass"
    assert len(doc["outputs"]) == 3
    assert not {"timing", "area", "worst_path", "clock_period_ns"} & doc.keys()
    assert not plan.metrics_json.exists() and not plan.worst_path_txt.exists()
    assert full.metrics_json.read_text() == "existing routed evidence"


@pytest.mark.parametrize("mode, status", [
    ("fail", "synth: fail (exit 3)"),
    ("missing", "incomplete: missing synthesis outputs"),
    ("empty", "incomplete: missing synthesis outputs 1_synth.odb"),
    ("errorzero", "synth: fail (error in log)"),
    ("sleep", "synth: timeout after 1s"),
])
def test_synth_only_fails_without_fresh_complete_evidence(ctx, monkeypatch, mode, status):
    native_env(monkeypatch, mode)
    plan = synth_plan(ctx)
    # Even valid-looking previous outputs must not make an incomplete run pass.
    results = plan.work_home / "results/sky130hd/counter/base"
    results.mkdir(parents=True)
    for name in ("1_2_yosys.v", "1_synth.odb", "1_synth.sdc"):
        (results / name).write_text("stale")
    plan.synthesis_json.write_text('{"status":"pass"}')
    code, doc = flow.run(ctx, plan, flow.Orfs("native", str(FAKE_ORFS)),
                         timeout_s=1 if mode == "sleep" else 60)
    assert code == 1 and doc["status"].startswith(status)
    assert json.loads(plan.synthesis_json.read_text())["status"] == doc["status"]
    assert not plan.metrics_json.exists()
    assert all((results / name).read_text() != "stale"
               for name in ("1_2_yosys.v", "1_synth.odb", "1_synth.sdc")
               if (results / name).exists())


# --- explicit clock studies ---

STUDY_SDC = """\
# build-asic: study-clock-period
set period 8.5
if {[info exists study_clock_period_ns]} {
  set period $study_clock_period_ns
}
create_clock -name clk -period $period [get_ports clk_i]
create_clock -name aux -period $period [get_ports aux_i]
"""


def study_args(ctx, period="10"):
    return ["asic", "--src-root", str(ctx.src_root), "--workdir", str(ctx.workdir),
            "--ip", "counter", f"--study-clock-period={period}"]


@pytest.mark.parametrize("period", ["0", "-1", "nan", "inf", "-inf"])
def test_invalid_study_period_preserves_existing_outputs(ctx, monkeypatch, capsys, period):
    native_env(monkeypatch)
    sentinel = ctx.workdir / "counter" / "asic-study" / "10ns" / "metrics.json"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("existing study")
    assert main(study_args(ctx, period)) == 2
    assert "finite and > 0" in capsys.readouterr().err
    assert sentinel.read_text() == "existing study"
    assert not (ctx.workdir / "counter" / "asic").exists()


def test_study_requires_explicit_sdc_opt_in(ctx, monkeypatch, capsys):
    native_env(monkeypatch)
    assert main(study_args(ctx)) == 2
    assert "does not support --study-clock-period" in capsys.readouterr().err
    assert not ctx.workdir.exists()


def test_study_disallows_synthesis_only(ctx, monkeypatch, capsys):
    native_env(monkeypatch)
    assert main(study_args(ctx) + ["--synth-only"]) == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert not ctx.workdir.exists()


def test_study_is_isolated_and_records_original_target(ctx, monkeypatch):
    native_env(monkeypatch)
    original = plan_for(ctx).sdc
    original.write_text(STUDY_SDC)
    default = plan_for(ctx)
    assert default.clock_period_ns == 8.5
    assert default.effective_sdc == original
    default.out.mkdir(parents=True)
    default.metrics_json.write_text("default evidence")
    assert main(study_args(ctx)) == 0
    result = ctx.workdir / "counter" / "asic-study" / "10ns"
    doc = json.loads((result / "metrics.json").read_text())
    assert doc["clock_period_ns"] == 10
    assert doc["study"] == {"kind": "clock-period", "clock_period_ns": 10,
                            "target_clock_period_ns": 8.5}
    assert doc["work_home"] == "counter/asic-study/10ns/run"
    assert default.metrics_json.read_text() == "default evidence"
    assert original.read_text() == STUDY_SDC
    generated = (result / "study.sdc").read_text()
    assert "set study_clock_period_ns 10.0\n" in generated
    assert STUDY_SDC in generated
    assert "foreach study_clk [all_clocks]" in generated
    assert "get_property $study_clk period" in generated
    assert f"export SDC_FILE = {result / 'study.sdc'}" in (result / "config.mk").read_text()
    # Same period spelling shares only its own output; another study is isolated.
    other = result.parent / "12ns" / "metrics.json"
    other.parent.mkdir()
    other.write_text("another study")
    assert main(study_args(ctx, "10.0") + ["--dry-run"]) == 0
    assert other.read_text() == "another study"
    assert default.metrics_json.read_text() == "default evidence"
    assert (result / "study.sdc").read_text() == generated


def test_study_rejects_zero_exit_constraint_error(ctx, monkeypatch):
    native_env(monkeypatch, "studyerrorzero")
    plan_for(ctx).sdc.write_text(STUDY_SDC)
    assert main(study_args(ctx)) == 1
    path = ctx.workdir / "counter" / "asic-study" / "10ns" / "metrics.json"
    assert json.loads(path.read_text())["status"] == "flow: fail (error in log)"
