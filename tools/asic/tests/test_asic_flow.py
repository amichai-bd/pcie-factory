"""./build asic: config generation, ORFS settings, commands and the run loop.

The run tests use a fake native ORFS root (tests/fixtures/fake_orfs) whose
env.sh puts a fake ``make`` first on PATH. It copies the fixture reports into
WORK_HOME, so the whole run, including metrics.json, is exercised without
ORFS. FAKE_MAKE=fail|sleep switches it to a failure or a hang.
"""

from __future__ import annotations

import json
import shutil
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
    asic = fixture_src / "counter" / "asic"
    asic.mkdir()
    (asic / "sky130hd.mk").write_text(MK)
    (asic / "counter.sdc").write_text(SDC)
    return fixture_src


@pytest.fixture
def ctx(asic_src, tmp_path):
    return make_context(str(asic_src), str(tmp_path / "work"))


def plan_for(ctx, ip_id="counter"):
    return flow.make_plan(ctx, find_ip(discover(ctx.src_root), ip_id), "sky130hd")


def native_env(monkeypatch, mode="ok"):
    for key in (flow.ENV_RUNNER, flow.ENV_IMAGE):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(flow.ENV_ROOT, str(FAKE_ORFS))
    monkeypatch.setenv("FAKE_MAKE", mode)


# --- inputs and config.mk ---

def test_config_mk_flattens_filelist(ctx):
    plan = plan_for(ctx)
    text = flow.render_config_mk(plan)
    src = ctx.src_root / "counter"
    assert f"include {src / 'asic' / 'sky130hd.mk'}" in text
    assert "export PLATFORM = sky130hd" in text
    assert "export DESIGN_NICKNAME = counter" in text
    assert f"export SDC_FILE = {src / 'asic' / 'counter.sdc'}" in text
    # Sub-IP source first, then the top, as the filelist orders them.
    files = text.split("export VERILOG_FILES = ", 1)[1].split("export ", 1)[0]
    assert files.index("inc.sv") < files.index("counter.sv")
    assert f"export VERILOG_INCLUDE_DIRS = {src / 'rtl' / 'include'}" in text
    assert "-DCOUNTER_FIXTURE" in text
    assert plan.design == "counter" and plan.clock_period_ns == 8.5


def test_config_mk_is_deterministic(ctx):
    assert flow.render_config_mk(plan_for(ctx)) == flow.render_config_mk(plan_for(ctx))


def test_sub_ip_nickname(asic_src, tmp_path):
    asic = asic_src / "counter" / "inc" / "asic"
    asic.mkdir()
    (asic / "sky130hd.mk").write_text("DESIGN_NAME = inc\n")
    (asic / "inc.sdc").write_text(SDC)
    c = make_context(str(asic_src), str(tmp_path / "w"))
    plan = plan_for(c, "counter/inc")
    assert plan.nick == "counter_inc"
    assert plan.out == tmp_path / "w" / "counter" / "inc" / "asic"


@pytest.mark.parametrize("bad, message", [
    ("export VERILOG_FILES = a.sv\n", "VERILOG_FILES"),
    ("export SDC_FILE = x.sdc\n", "SDC_FILE"),
    ("PLATFORM ?= sky130hd\n", "PLATFORM"),
])
def test_mk_must_not_set_tool_owned(ctx, bad, message):
    (ctx.src_root / "counter" / "asic" / "sky130hd.mk").write_text(MK + bad)
    with pytest.raises(flow.AsicError, match=message):
        plan_for(ctx)


def test_mk_needs_design_name(ctx):
    (ctx.src_root / "counter" / "asic" / "sky130hd.mk").write_text("CORE_UTILIZATION = 1\n")
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
    (ctx.src_root / "counter" / "asic" / "other.sdc").write_text(SDC)
    with pytest.raises(flow.AsicError, match="exactly one .sdc"):
        plan_for(ctx)


def test_unknown_platform(ctx):
    ip = find_ip(discover(ctx.src_root), "counter")
    with pytest.raises(flow.AsicError, match="unknown platform"):
        flow.make_plan(ctx, ip, "nope")


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


def test_orfs_env_image_overrides_and_drops_commit(tmp_path):
    orfs = flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {flow.ENV_IMAGE: "other:1"})
    assert (orfs.runner, orfs.image, orfs.commit) == ("docker", "other:1", None)


def test_orfs_env_same_image_keeps_commit(tmp_path):
    orfs = flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {flow.ENV_IMAGE: "img:tag@sha256:abc"})
    assert (orfs.source, orfs.commit) == ("env", "c0ffee")


def test_orfs_cfg_native_root(tmp_path):
    orfs = flow.load_orfs(write_cfg(tmp_path, "hosts:\n  h:\n    orfs_root: /o\n"), {})
    assert (orfs.runner, orfs.root) == ("native", "/o")


@pytest.mark.parametrize("body, env, message", [
    ("hosts:\n  h:\n    python: x\n", {}, "no ORFS configured"),
    ("other: 1\n", {flow.ENV_RUNNER: "podman"}, "is not one of"),
    ("other: 1\n", {flow.ENV_RUNNER: "docker"}, "needs an image"),
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
    assert f"--memory={flow.DOCKER_MEMORY}" in argv
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
    assert f"{ctx.workdir}:{ctx.workdir}:rw" in mounts
    assert f"{ctx.repo_root}:{ctx.repo_root}:ro" in mounts
    assert f"{ctx.src_root}:{ctx.src_root}:ro" in mounts  # tmp src is outside the repo
    assert argv[-3:-1] == ["bash", "-c"]


def test_steps_run_finish_then_extra(ctx, tmp_path):
    plan = plan_for(ctx)
    orfs = flow.load_orfs(write_cfg(tmp_path, DOCKER_CFG), {})
    steps = dict(flow.step_scripts(orfs, plan))
    assert list(steps) == ["versions", "flow", "extra"]
    assert f"make -C {flow.IMAGE_ROOT}/flow" in steps["flow"] and steps["flow"].endswith(" finish")
    assert f"DESIGN_CONFIG={plan.config_mk}" in steps["flow"]
    assert f"RUN_SCRIPT={flow.EXTRA_TCL}" in steps["extra"]
    assert steps["extra"].endswith(" run")


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
    assert doc["max_fanout"]["value"] == 25 and doc["logic_depth"]["value"] == 6
    assert doc["toolchain"]["runner"] == "native"
    assert doc["toolchain"]["openroad"] == "26Q4-142-gd4b96380b"
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


def test_fake_orfs_is_executable():
    assert shutil.which("make", path=str(FAKE_ORFS / "bin")) is not None
