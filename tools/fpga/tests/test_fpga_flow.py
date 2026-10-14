"""Project generation, planning, tool discovery and the run loop, without
Quartus: fake quartus_* scripts replay the hand-made fixture reports."""

import json
import os
import stat
from pathlib import Path

import pytest

from build.ip import Ip, discover, find_ip
from cli.main import main
from fpga import flow
from fpga.flow import FpgaError

FIX = Path(__file__).resolve().parent / "fixtures"
TOP = "widget_board"  # matches the fixture report names

WRAPPER = f"""`default_nettype none
module {TOP} (
  input  logic clk_i,
  output logic led_o
);
  widget u_widget (.clk_i(clk_i), .q_o(led_o));
endmodule
`default_nettype wire
"""


@pytest.fixture
def src(tmp_path) -> Path:
    """A synthetic tree: IP ``widget`` (FPGA target, one include dir and one
    define) and IP ``plain`` (no fpga/)."""
    root = tmp_path / "src"
    rtl = root / "widget" / "rtl"
    (rtl / "inc").mkdir(parents=True)
    (rtl / "widget_pkg.sv").write_text("package widget_pkg; endpackage\n")
    (rtl / "widget.sv").write_text("module widget(input logic clk_i, output logic q_o); endmodule\n")
    (rtl / "widget.rtl.f").write_text(
        "+incdir+inc\n+define+WIDTH=8\n+define+FAST\nwidget_pkg.sv\nwidget.sv\n")
    fpga_dir = root / "widget" / "fpga"
    fpga_dir.mkdir()
    (fpga_dir / f"{TOP}.sv").write_text(WRAPPER)
    (fpga_dir / "widget.sdc").write_text("create_clock -period 20 [get_ports clk_i]\n")
    (fpga_dir / "de2_115.qsf.tcl").write_text("set_global_assignment -name DEVICE EP4CE115F29C7\n")
    (root / "plain" / "rtl").mkdir(parents=True)
    (root / "plain" / "rtl" / "plain.rtl.f").write_text("plain.sv\n")
    (root / "plain" / "rtl" / "plain.sv").write_text("module plain; endmodule\n")
    return root


def widget(src: Path) -> Ip:
    return find_ip(discover(src), "widget")


def make_plan(src: Path, tmp_path: Path, **kw) -> flow.Plan:
    return flow.plan(widget(src), tmp_path / "work", "de2_115", **kw)


# --------------------------------------------------------------------------
# Planning and generated Tcl


def test_plan_resolves_inputs(src, tmp_path):
    p = make_plan(src, tmp_path)
    assert p.top == TOP
    assert [s.name for s in p.sources] == ["widget_pkg.sv", "widget.sv", f"{TOP}.sv"]
    assert p.out_dir == tmp_path / "work" / "widget" / "fpga"
    assert p.defines == {"WIDTH": "8", "FAST": None}
    assert [name for name, _ in p.steps] == ["project", "map", "fit", "sta", "worst_path"]


def test_project_tcl(src, tmp_path):
    p = make_plan(src, tmp_path, part="EP4CE22F17C6", processors=3)
    tcl = flow.project_tcl(p)
    lines = tcl.splitlines()
    assert f"project_new {{{TOP}}} -revision {{{TOP}}} -overwrite" in lines
    assert f"set_global_assignment -name TOP_LEVEL_ENTITY {{{TOP}}}" in lines
    assert "set_global_assignment -name NUM_PARALLEL_PROCESSORS 3" in lines
    assert f"set_global_assignment -name SEARCH_PATH {{{src / 'widget/rtl/inc'}}}" in lines
    assert "set_global_assignment -name VERILOG_MACRO {WIDTH=8}" in lines
    assert "set_global_assignment -name VERILOG_MACRO {FAST}" in lines
    files = [ln for ln in lines if "SYSTEMVERILOG_FILE" in ln]
    assert [Path(f.split("{")[1].rstrip("}")).name for f in files] == [
        "widget_pkg.sv", "widget.sv", f"{TOP}.sv"]
    # Board file after the sources, --part after the board file.
    i_src = lines.index(f"source {{{src / 'widget/fpga/de2_115.qsf.tcl'}}}")
    i_part = lines.index("set_global_assignment -name DEVICE EP4CE22F17C6")
    assert lines.index(files[-1]) < i_src < i_part
    assert lines[-2:] == ["export_assignments", "project_close"]


def test_no_part_override_by_default(src, tmp_path):
    assert "-name DEVICE" not in flow.project_tcl(make_plan(src, tmp_path))


def test_worst_path_tcl(src, tmp_path):
    tcl = flow.worst_path_tcl(make_plan(src, tmp_path))
    assert f"project_open {{{TOP}}} -revision {{{TOP}}}" in tcl
    assert "get_timing_paths -setup -npaths 1" in tcl
    assert "worst_path.txt" in tcl


def test_generate_is_deterministic(src, tmp_path):
    p = make_plan(src, tmp_path)
    flow.generate(p)
    first = {f.name: f.read_bytes() for f in p.out_dir.iterdir() if f.is_file()}
    (p.out_dir / "stale.txt").write_text("old")
    flow.generate(p)
    second = {f.name: f.read_bytes() for f in p.out_dir.iterdir() if f.is_file()}
    assert first == second and set(first) == {"project.tcl", "worst_path.tcl"}


def test_lint_command(src, tmp_path):
    cmd = flow.lint_command(make_plan(src, tmp_path), "verilator")
    assert cmd[:5] == ["verilator", "--lint-only", "-Wall", "--top-module", TOP]
    assert "+define+WIDTH=8" in cmd and "+define+FAST" in cmd
    assert cmd[-1].endswith(f"{TOP}.sv")


def test_fpga_ips(src):
    assert [ip.id for ip in flow.fpga_ips(discover(src))] == ["widget"]


@pytest.mark.parametrize("mutate, message", [
    (lambda f: (f / "de2_115.qsf.tcl").unlink(), "board settings file not found"),
    (lambda f: (f / "extra.sdc").write_text(""), "exactly one timing constraints file"),
    (lambda f: (f / "widget.sdc").unlink(), r"timing constraints file \(\*\.sdc\), found none"),
    (lambda f: (f / "helper.sv").write_text(""), "exactly one board wrapper"),
    (lambda f: (f / f"{TOP}.sv").write_text("module other; endmodule\n"),
     f"does not declare module {TOP}"),
])
def test_plan_input_errors(src, tmp_path, mutate, message):
    mutate(src / "widget" / "fpga")
    with pytest.raises(FpgaError, match=message):
        make_plan(src, tmp_path)


def test_plan_wrapper_in_rtl_f(src, tmp_path):
    with open(src / "widget/rtl/widget.rtl.f", "a") as fh:
        fh.write(f"../fpga/{TOP}.sv\n")
    with pytest.raises(FpgaError, match="must not be in widget.rtl.f"):
        make_plan(src, tmp_path)


def test_plan_not_fpga_target(src, tmp_path):
    with pytest.raises(FpgaError, match="no fpga/ directory"):
        flow.plan(find_ip(discover(src), "plain"), tmp_path, "de2_115")


def test_plan_bad_part(src, tmp_path):
    with pytest.raises(FpgaError, match="--part"):
        make_plan(src, tmp_path, part="EP4CE; rm -rf")


# --------------------------------------------------------------------------
# Configuration and discovery


def write_cfg(d: Path, fpga_yaml: str, quartus_root: str = "") -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "fpga.yaml").write_text(fpga_yaml)
    (d / "host.yaml").write_text(
        f"hosts:\n  omarchy:\n    quartus_root: {quartus_root}\n")
    return d


def test_load_config_repo():
    cfg = flow.load_config(Path(flow.REPO_ROOT) / "cfg")
    assert cfg["board"] and cfg["step_timeout_s"] > 0 and cfg["processors"] >= 1


@pytest.mark.parametrize("text, message", [
    ("board: a b\nstep_timeout_s: 1\nprocessors: 1\n", "'board'"),
    ("board: x\nstep_timeout_s: -1\nprocessors: 1\n", "step_timeout_s"),
    ("board: x\nstep_timeout_s: 1\nprocessors: 0\n", "processors"),
    ("board: [\n", "cannot read"),
])
def test_load_config_errors(tmp_path, text, message):
    with pytest.raises(FpgaError, match=message):
        flow.load_config(write_cfg(tmp_path / "cfg", text))


def fake_exe(path: Path, body: str = "exit 0") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_find_quartus_order(tmp_path, monkeypatch):
    on_path = fake_exe(tmp_path / "a" / "bin" / "quartus_sh").parent
    env_root = tmp_path / "b"
    fake_exe(env_root / "bin" / "quartus_sh")
    host_root = tmp_path / "c"
    fake_exe(host_root / "bin" / "quartus_sh")
    cfg = write_cfg(tmp_path / "cfg", "", str(host_root))
    monkeypatch.setenv("PATH", f"{on_path}{os.pathsep}/usr/bin:/bin")
    monkeypatch.setenv("QUARTUS_ROOTDIR", str(env_root))
    assert flow.find_quartus(cfg) == on_path
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert flow.find_quartus(cfg) == env_root / "bin"
    monkeypatch.delenv("QUARTUS_ROOTDIR")
    assert flow.find_quartus(cfg) == host_root / "bin"
    cfg = write_cfg(tmp_path / "cfg2", "")
    assert flow.find_quartus(cfg) is None
    assert "hosts.omarchy.quartus_root (empty)" in flow.quartus_not_found(cfg)


# --------------------------------------------------------------------------
# Running with fake Quartus


def fake_quartus(bin_dir: Path, fail: str | None = None) -> Path:
    """quartus_* scripts that log a closing line and copy the fixtures."""
    ok = 'echo "Info: Quartus Prime X was successful. 0 errors, 2 warnings"'
    copy_reports = f"mkdir -p output_files && cp {FIX}/output_files/* output_files/"
    bodies = {
        "quartus_sh": ok,
        "quartus_map": ok,
        "quartus_fit": ok,
        "quartus_sta": (f'if [ "$1" = "-t" ]; then cp {FIX}/worst_path.txt .; '
                        f'else {copy_reports}; fi\n{ok}'),
    }
    for name, body in bodies.items():
        fake_exe(bin_dir / name, "echo boom; exit 3" if name == fail else body)
    return bin_dir


def test_run_writes_metrics(src, tmp_path, capsys):
    p = make_plan(src, tmp_path)
    assert flow.run(p, fake_quartus(tmp_path / "bin"), 30, None) == 0
    m = json.loads((p.out_dir / "metrics.json").read_text())
    assert m["ip"] == "widget" and m["board"] == "de2_115"
    assert m["resources"]["logic_elements"]["used"] == 1234
    assert m["timing"]["met"] is True
    assert m["flow"]["lint"] == "skipped"
    assert set(m["flow"]["steps"]) == {"project", "map", "fit", "sta", "worst_path"}
    assert all(s["status"] == "pass" and s["warnings"] == 2 for s in m["flow"]["steps"].values())
    assert (p.out_dir / "logs" / "fit.log").read_text().startswith("$ ")
    out = capsys.readouterr().out
    assert "clock CLK_B: fmax 260.42 MHz" in out


def test_run_step_failure(src, tmp_path, capsys):
    p = make_plan(src, tmp_path)
    assert flow.run(p, fake_quartus(tmp_path / "bin", fail="quartus_fit"), 30, None) == 1
    assert not (p.out_dir / "metrics.json").exists()
    out = capsys.readouterr().out
    assert "[fit] fail" in out and "boom" in out
    assert "[sta]" not in out


def test_run_lint_failure_stops(src, tmp_path):
    p = make_plan(src, tmp_path)
    lint = fake_exe(tmp_path / "lintbin" / "verilator", "exit 1")
    assert flow.run(p, fake_quartus(tmp_path / "bin"), 30, lint) == 1
    assert not (p.out_dir / "output_files").exists()


def test_run_timing_not_met(src, tmp_path, monkeypatch):
    p = make_plan(src, tmp_path)
    real = flow.reports.collect

    def failing(*a):
        m = real(*a)
        m["timing"]["met"] = False
        return m
    monkeypatch.setattr(flow.reports, "collect", failing)
    assert flow.run(p, fake_quartus(tmp_path / "bin"), 30, None) == 1
    assert (p.out_dir / "metrics.json").exists()


def test_run_step_timeout_kills_group(tmp_path):
    # A child that spawns a grandchild: the wait must not hang on either.
    script = fake_exe(tmp_path / "slow", "(sleep 5) & sleep 5")
    res = flow.run_step("slow", [str(script)], tmp_path, tmp_path / "slow.log", 0.5)
    assert res.status == "timeout" and res.runtime_s < 4
    assert "timeout after" in (tmp_path / "slow.log").read_text()


def test_quartus_warnings(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("Info: Quartus Prime Fitter was successful. 0 errors, 1 warning\n")
    assert flow.quartus_warnings(log) == 1
    log.write_text("nothing\n")
    assert flow.quartus_warnings(log) is None


# --------------------------------------------------------------------------
# CLI


def test_cli_dry_run(src, tmp_path, capsys):
    argv = ["fpga", "--src-root", str(src), "--workdir", str(tmp_path / "w"),
            "--ip", "widget", "--dry-run"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert f"[map] quartus_map {TOP} -c {TOP}" in out
    assert "[lint] verilator --lint-only" in out
    assert (tmp_path / "w" / "widget" / "fpga" / "project.tcl").is_file()


def test_cli_dry_run_keeps_previous_compile(src, tmp_path):
    out = tmp_path / "w" / "widget" / "fpga"
    (out / "output_files").mkdir(parents=True)
    (out / "metrics.json").write_text("{}")
    argv = ["fpga", "--src-root", str(src), "--workdir", str(tmp_path / "w"),
            "--ip", "widget", "--dry-run"]
    assert main(argv) == 0
    assert (out / "metrics.json").read_text() == "{}"
    assert (out / "output_files").is_dir() and (out / "project.tcl").is_file()


def test_cli_all_selects_fpga_ips(src, tmp_path, capsys):
    argv = ["fpga", "--src-root", str(src), "--workdir", str(tmp_path / "w"),
            "--all", "--dry-run"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "[widget]" in out and "[plain]" not in out


def test_cli_not_fpga_target(src, tmp_path, capsys):
    argv = ["fpga", "--src-root", str(src), "--workdir", str(tmp_path), "--ip", "plain",
            "--dry-run"]
    assert main(argv) == 2
    assert "no fpga/ directory" in capsys.readouterr().err


def test_cli_quartus_missing(src, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("QUARTUS_ROOTDIR", raising=False)
    monkeypatch.setattr(flow, "host_quartus_root", lambda cfg_dir: None)
    argv = ["fpga", "--src-root", str(src), "--workdir", str(tmp_path), "--ip", "widget"]
    assert main(argv) == 2
    assert "quartus_sh not found" in capsys.readouterr().err


def test_cli_requires_ip_or_all():
    with pytest.raises(SystemExit) as exc:
        main(["fpga"])
    assert exc.value.code == 2
