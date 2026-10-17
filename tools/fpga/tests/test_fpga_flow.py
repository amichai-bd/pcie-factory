"""Project generation, planning, tool discovery and the run loop, without
Quartus: fake quartus_* scripts replay the hand-made fixture reports."""

import json
import os
import re
import stat
from pathlib import Path

import pytest

from build.filelist import FilelistError
from build.ip import Ip, discover, find_ip
from cli.main import main
from build import backends
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
    rtl = root / "rtl" / "widget"
    (rtl / "inc").mkdir(parents=True)
    (rtl / "widget_pkg.sv").write_text("package widget_pkg; endpackage\n")
    (rtl / "widget.sv").write_text("module widget(input logic clk_i, output logic q_o); endmodule\n")
    (rtl / "widget.rtl.f").write_text(
        "+incdir+inc\n+define+WIDTH=8\n+define+FAST\nwidget_pkg.sv\nwidget.sv\n")
    fpga_dir = root / "fpga" / "widget"
    fpga_dir.mkdir(parents=True)
    (fpga_dir / f"{TOP}.sv").write_text(WRAPPER)
    (fpga_dir / "widget.sdc").write_text("create_clock -period 20 [get_ports clk_i]\n")
    (fpga_dir / "de2_115.qsf.tcl").write_text("set_global_assignment -name DEVICE EP4CE115F29C7\n")
    (root / "rtl" / "plain").mkdir(parents=True)
    (root / "rtl" / "plain" / "plain.rtl.f").write_text("plain.sv\n")
    (root / "rtl" / "plain" / "plain.sv").write_text("module plain; endmodule\n")
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
    # The flow adds SYNTHESIS: the assertion macros compile out.
    assert p.defines == {"WIDTH": "8", "FAST": None, "SYNTHESIS": None}
    assert [name for name, _ in p.steps] == ["project", "map", "fit", "sta", "worst_path"]


def test_project_tcl(src, tmp_path):
    p = make_plan(src, tmp_path, part="EP4CE22F17C6", processors=3)
    tcl = flow.project_tcl(p)
    lines = tcl.splitlines()
    assert f"project_new {{{TOP}}} -revision {{{TOP}}} -overwrite" in lines
    assert f"set_global_assignment -name TOP_LEVEL_ENTITY {{{TOP}}}" in lines
    assert "set_global_assignment -name NUM_PARALLEL_PROCESSORS 3" in lines
    assert f"set_global_assignment -name SEARCH_PATH {{{src / 'rtl/widget/inc'}}}" in lines
    assert "set_global_assignment -name VERILOG_MACRO {WIDTH=8}" in lines
    assert "set_global_assignment -name VERILOG_MACRO {FAST}" in lines
    assert "set_global_assignment -name VERILOG_MACRO {SYNTHESIS}" in lines
    files = [ln for ln in lines if "SYSTEMVERILOG_FILE" in ln]
    assert [Path(f.split("{")[1].rstrip("}")).name for f in files] == [
        "widget_pkg.sv", "widget.sv", f"{TOP}.sv"]
    # Board file after the sources, --part after the board file.
    i_src = lines.index(f"source {{{src / 'fpga/widget/de2_115.qsf.tcl'}}}")
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
    assert cmd[:6] == ["verilator", "--lint-only", "-Wall", "-Wno-fatal", "--top-module", TOP]
    assert "+define+WIDTH=8" in cmd and "+define+FAST" in cmd
    assert not any("SYNTHESIS" in c for c in cmd)  # lint sees the simulation view
    assert cmd[-1].endswith(f"{TOP}.sv")


def with_backend(src: Path) -> dict:
    """Give widget.sv an ``alt`` fpga backend file; return cfg/backends.yaml."""
    rtl = src / "rtl" / "widget"
    (rtl / "widget_alt.sv").write_text(
        "module widget(input logic clk_i, output logic q_o); endmodule\n")
    return {"fpga": backends.Backend("fpga", "alt", ()),
            "asic": backends.Backend("asic", "other", ())}


def test_plan_selects_backend(src, tmp_path):
    known = with_backend(src)
    p = make_plan(src, tmp_path, known=known)
    # Quartus gets the backend file in place; lint keeps the portable view.
    assert [s.name for s in p.sources] == ["widget_pkg.sv", "widget_alt.sv", f"{TOP}.sv"]
    assert [s.name for s in p.lint_sources] == ["widget_pkg.sv", "widget.sv", f"{TOP}.sv"]
    assert [(a.name, b.name) for a, b in p.swapped] == [("widget.sv", "widget_alt.sv")]
    assert "widget_alt.sv" in flow.project_tcl(p)
    assert "widget_alt.sv" not in " ".join(flow.lint_command(p, "verilator"))
    # Without a configured backend nothing is swapped.
    assert [s.name for s in make_plan(src, tmp_path).sources][1] == "widget.sv"


def test_plan_rejects_listed_backend(src, tmp_path):
    known = with_backend(src)
    (src / "rtl" / "widget" / "widget.rtl.f").write_text("widget_pkg.sv\nwidget.sv\nwidget_alt.sv\n")
    with pytest.raises(FpgaError, match="lists a backend file"):
        make_plan(src, tmp_path, known=known)


def test_load_config_backends(src, tmp_path):
    with_backend(src)
    cfg = tmp_path / "repo" / "cfg"
    cfg.mkdir(parents=True)
    for name in ("fpga.yaml", "host.yaml"):
        (cfg / name).write_text((Path(flow.REPO_ROOT) / "cfg" / name).read_text())
    (cfg / "backends.yaml").write_text("fpga:\n  suffix: alt\n")
    p = flow.plan(widget(src), tmp_path / "w", "de2_115",
                  known=flow.load_config(cfg)["backends"])
    assert [b.name for _, b in p.swapped] == ["widget_alt.sv"]


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
    mutate(src / "fpga" / "widget")
    with pytest.raises(FpgaError, match=message):
        make_plan(src, tmp_path)


def test_plan_wrapper_in_rtl_f(src, tmp_path):
    with open(src / "rtl/widget/widget.rtl.f", "a") as fh:
        fh.write(f"../../fpga/widget/{TOP}.sv\n")
    with pytest.raises(FpgaError, match="must not be in widget.rtl.f"):
        make_plan(src, tmp_path)


def test_plan_not_fpga_target(src, tmp_path):
    with pytest.raises(FpgaError, match="no fpga/plain/ folder"):
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
    assert cfg["backends"]["fpga"].suffix  # cfg/backends.yaml names the fpga backend


@pytest.mark.parametrize("text, message", [
    ("board: a b\nstep_timeout_s: 1\nprocessors: 1\n", "'board'"),
    ("board: x\nstep_timeout_s: -1\nprocessors: 1\n", "step_timeout_s"),
    ("board: x\nstep_timeout_s: 1\nprocessors: 0\n", "processors"),
    ("board: [\n", "cannot read"),
    ("board: x\nstep_timeout_s: 1\nprocessors: 1\nknown_warnings: [1, 1]\n", "known_warnings"),
    ("board: x\nstep_timeout_s: 1\nprocessors: 1\nknown_warnings: 10036\n", "known_warnings"),
    ("board: x\nstep_timeout_s: 1\nprocessors: 1\nknown_warnings: ['10036']\n", "known_warnings"),
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


def fake_quartus(bin_dir: Path, fail: str | None = None, fail_body: str = "echo boom; exit 3") -> Path:
    """quartus_* scripts that log a closing line and copy the fixtures; the
    ``fail`` one runs ``fail_body`` instead."""
    ok = 'echo "Info: Quartus Prime X was successful. 0 errors, 2 warnings"'
    copy_reports = f"mkdir -p output_files && cp {FIX}/output_files/* output_files/"
    bodies = {
        "quartus_sh": ok,
        "quartus_map": f"cat {FIX}/map_warnings.log",
        "quartus_fit": ok,
        "quartus_sta": (f'if [ "$1" = "-t" ]; then cp {FIX}/worst_path.txt .; '
                        f'else {copy_reports}; fi\n{ok}'),
    }
    for name, body in bodies.items():
        fake_exe(bin_dir / name, fail_body if name == fail else body)
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
    steps = m["flow"]["steps"]
    assert all(s["status"] == "pass" for s in steps.values())
    assert steps["fit"]["warnings"] == 2 and steps["fit"]["warning_ids"] == {}
    assert steps["map"]["warnings"] == 7
    assert steps["map"]["warning_ids"] == {"10036": 2, "15610": 1, "21074": 1, "99999": 1,
                                           "292013": 1, "332012": 1}
    assert (p.out_dir / "logs" / "fit.log").read_text().startswith("$ ")
    out = capsys.readouterr().out
    assert "clock CLK_B: fmax 260.42 MHz" in out
    # No known IDs passed: every map warning is unknown; fit has a count only.
    assert "  warnings map: known 0, unknown 7 (closing line: 7)" in out
    assert "  warnings fit: known 0, unknown 0 (closing line: 2)" in out
    assert "    unknown 10036 (x2): Verilog HDL or VHDL warning at widget.sv(12):" in out


def test_run_step_failure(src, tmp_path, capsys):
    p = make_plan(src, tmp_path)
    assert flow.run(p, fake_quartus(tmp_path / "bin", fail="quartus_fit"), 30, None) == 1
    assert not (p.out_dir / "metrics.json").exists()
    out = capsys.readouterr().out
    assert "[fit] fail" in out and "boom" in out
    assert "[sta]" not in out


def test_run_step_failure_prints_every_error_line_then_tail(src, tmp_path, capsys):
    p = make_plan(src, tmp_path)
    fixture = FIX / "map_fail.log"
    bin_dir = fake_quartus(tmp_path / "bin", fail="quartus_map",
                           fail_body=f"cat {fixture}; exit 3")
    assert flow.run(p, bin_dir, 30, None) == 1
    log_lines = (p.out_dir / "logs" / "map.log").read_text().splitlines()
    errors = [line for line in log_lines if flow.ERROR_RE.search(line)]
    # One critical warning and three errors, all before the last 30 lines.
    assert [e.split(" (")[0] for e in errors] == ["Critical Warning"] + ["Error"] * 3
    assert not any(e in log_lines[-30:] for e in errors)
    out = capsys.readouterr().out.splitlines()
    log = p.out_dir / "logs" / "map.log"
    head = out.index(f"--- error lines in {log}: 4 ---")
    tail = out.index(f"--- last 30 lines of {log} ---")
    assert out[head + 1:head + 5] == errors
    assert head < tail and out[tail + 1:] == log_lines[-30:]


def test_run_lint_failure_stops(src, tmp_path):
    p = make_plan(src, tmp_path)
    lint = fake_exe(tmp_path / "lintbin" / "verilator", "exit 1")
    assert flow.run(p, fake_quartus(tmp_path / "bin"), 30, lint) == 1
    assert not (p.out_dir / "output_files").exists()


LINT_WARNING = (f"%Warning-SYNCASYNCNET: {{src}}/fpga/widget/{TOP}.sv:3:9: "
                f"Signal flopped as both synchronous and async: '{TOP}.rst'")


def warning_verilator(path: Path, src: Path) -> Path:
    """A fake Verilator that prints one warning and exits 0, as -Wno-fatal does."""
    return fake_exe(path, f"echo \"{LINT_WARNING.format(src=src)}\" >&2")


def test_run_lint_unwaived_warning_fails(src, tmp_path, capsys):
    p = make_plan(src, tmp_path)
    lint = warning_verilator(tmp_path / "lintbin" / "verilator", src)
    assert flow.run(p, fake_quartus(tmp_path / "bin"), 30, lint) == 1
    assert not (p.out_dir / "output_files").exists()
    assert "[lint] fail" in capsys.readouterr().out


def test_run_lint_applies_waivers(src, tmp_path):
    (src / "lint_waivers.yaml").write_text(
        f"waivers:\n  - file: fpga/widget/{TOP}.sv\n    rule: SYNCASYNCNET\n"
        f"    match: \"'{TOP}.rst'\"\n    reason: test\n")
    p = make_plan(src, tmp_path)
    lint = warning_verilator(tmp_path / "lintbin" / "verilator", src)
    assert flow.run(p, fake_quartus(tmp_path / "bin"), 30, lint) == 0
    m = json.loads((p.out_dir / "metrics.json").read_text())
    assert m["flow"]["lint"] == "pass"


def test_run_lint_bad_waiver_file_fails(src, tmp_path):
    (src / "lint_waivers.yaml").write_text("waivers: 3\n")
    p = make_plan(src, tmp_path)
    lint = fake_exe(tmp_path / "lintbin" / "verilator", "exit 0")
    assert flow.run(p, fake_quartus(tmp_path / "bin"), 30, lint) == 1


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


def test_run_classifies_known_warnings(src, tmp_path, capsys):
    p = make_plan(src, tmp_path)
    known = flow.load_config(Path(flow.REPO_ROOT) / "cfg")["known_warnings"]
    assert flow.run(p, fake_quartus(tmp_path / "bin"), 30, None, known) == 0
    out = capsys.readouterr().out
    assert "  warnings map: known 3, unknown 4 (closing line: 7)" in out
    unknown = [l for l in out.splitlines() if l.startswith("    unknown ")]
    assert [l.split()[1] for l in unknown] == ["15610", "21074", "99999", "332012"]
    assert all(len(l.split(": ", 1)[1]) <= flow.MESSAGE_CHARS for l in unknown)
    assert "/var/lib" not in out and "/opt/" not in out  # names: see test_sanitize_*


def test_warning_ids(tmp_path):
    ids, first = flow.warning_ids(FIX / "map_warnings.log")
    assert list(ids) == ["10036", "15610", "21074", "99999", "292013", "332012"]
    assert sum(ids.values()) == 7 == flow.quartus_warnings(FIX / "map_warnings.log")
    assert first[10036].endswith('"dbg_q" assigned a value but never read')
    assert flow.warning_ids(tmp_path / "missing.log") == ({}, {})
    log = tmp_path / "x.log"
    log.write_text("Info (1): Warning (2): not a warning line\nWarning: no ID\n")
    assert flow.warning_ids(log) == ({}, {})


ROOT = Path("/var/lib/ci/work/checkout")
NAMES = ["build-host-07", "ci-user"]


def test_sanitize_paths_names_and_length():
    _, first = flow.warning_ids(FIX / "map_warnings.log")
    msg = flow.sanitize(first[99999], ROOT, NAMES)
    assert msg.startswith("Settings written by <name> on <name> to .../widget_board.ini "
                          "and copied to workdir/widget/fpga/db/widget_board.ini")
    assert len(msg) == flow.MESSAGE_CHARS and msg.endswith("...")
    assert "/var" not in msg and "/opt" not in msg and "ci-user" not in msg
    assert flow.sanitize(first[15610], ROOT, NAMES) == (
        'No output dependent on input pin "key_i" File: '
        "src/fpga/widget/widget_board.sv Line: 4")
    assert flow.sanitize(first[332012], ROOT, NAMES) == (
        "Constraint file workdir/widget/fpga/extra.sdc was not found")


def test_sanitize_home_email_address():
    home = "/" + "home/" + "someone/proj/x.sv"  # built so the publishability scan never sees it
    mail = "someone" + "@" + "example.org"
    ip = ".".join(["10", "1", "2", "3"])
    msg = flow.sanitize(f"at {home}({3}) by {mail} from {ip}, see widget.sv(4)", ROOT, [])
    assert msg == "at .../x.sv(3) by <email> from <ip>, see widget.sv(4)"
    assert flow.sanitize("a  b\tc", ROOT, []) == "a b c"
    assert flow.sanitize("x" * 200, ROOT, [], limit=10) == "xxxxxxx..."
    # A name is replaced only as a whole word.
    assert flow.sanitize("ci-user ci-users xci-user", ROOT, ["ci-user"]) == "<name> ci-users xci-user"


def test_warning_lines_skip_quiet_steps():
    steps = {"project": {"warnings": 0, "warning_ids": {}},
             "fit": {"warnings": 1, "warning_ids": {"169177": 1}}}
    assert flow.warning_lines(steps, frozenset({169177}), {}, ROOT, []) == [
        "  warnings fit: known 1, unknown 0 (closing line: 1)"]


def bkm_known_ids() -> list[int]:
    """IDs in the FPGA BKM's "Known Quartus warnings" table."""
    text = (Path(flow.REPO_ROOT) / "wiki/bkm/fpga.md").read_text()
    section = text.split("## Known Quartus warnings", 1)[1].split("\n## ", 1)[0]
    return [int(m) for m in re.findall(r"^\| (\d+) \|", section, re.M)]


def test_known_warnings_match_bkm_table():
    known = flow.load_config(Path(flow.REPO_ROOT) / "cfg")["known_warnings"]
    ids = bkm_known_ids()
    assert ids and len(ids) == len(set(ids))
    assert set(ids) == known, "cfg/fpga.yaml known_warnings must equal the FPGA BKM table"


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
    assert "no fpga/plain/ folder" in capsys.readouterr().err


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


def test_filelist_may_not_define_synthesis(src, tmp_path):
    f = widget(src).rtl_f
    f.write_text(f.read_text() + "+define+SYNTHESIS\n")
    with pytest.raises(FilelistError, match="only the synthesis flows set"):
        make_plan(src, tmp_path)


@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_ci_cancellation_kills_quartus_and_writing_grandchild(src, tmp_path, signame):
    """Real wrapper/CLI/process groups: no vendor process can outlive cancellation."""
    import shlex
    import signal
    import subprocess
    import sys
    import time

    from build.ip import REPO_ROOT

    # The fake compiler starts a worker in its own group, just as a vendor
    # command can. Its heartbeat detects a live orphan even if the CLI exits.
    worker = tmp_path / "worker.py"
    worker.write_text("""import pathlib, time
heartbeat = pathlib.Path('heartbeat')
while True:
    with heartbeat.open('a') as f:
        f.write('tick\\n')
    time.sleep(0.01)
""")
    compiler = tmp_path / "compiler.py"
    compiler.write_text("""import json, os, pathlib, subprocess, sys
child = subprocess.Popen([sys.executable, sys.argv[1]])
pathlib.Path('pids.json').write_text(json.dumps([os.getpid(), child.pid]))
child.wait()
""")
    bin_dir = fake_quartus(tmp_path / "bin", fail="quartus_map",
                           fail_body=f"exec {shlex.join([sys.executable, str(compiler), str(worker)])}")
    fake_exe(bin_dir / "verilator")  # lint succeeds; no real simulator/toolchain needed
    work = tmp_path / "work"
    out = work / "widget/fpga"
    pids = []
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    with (tmp_path / "ci.log").open("w") as log:
        proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "build"), "ci", "vendor",
                                 "--kind", "fpga", "--src-root", str(src),
                                 "--workdir", str(work)], env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if (out / "pids.json").exists() and (out / "heartbeat").exists():
                    pids = json.loads((out / "pids.json").read_text())
                    break
                assert proc.poll() is None, (tmp_path / "ci.log").read_text()
                time.sleep(0.01)
            assert pids, "fake Quartus and its grandchild did not start"
            proc.send_signal(getattr(signal, signame))
            assert proc.wait(timeout=10) == 130
            # Linux may retain a dead orphan as a zombie until PID 1 reaps it.
            for pid in pids:
                stat_file = Path(f"/proc/{pid}/stat")
                assert not stat_file.exists() or stat_file.read_text().split(") ")[1][0] == "Z"
            size = (out / "heartbeat").stat().st_size
            time.sleep(0.15)
            assert (out / "heartbeat").stat().st_size == size
            assert not (out / "logs/fit.log").exists()
            assert not (out / "metrics.json").exists()
            assert not (work / "ci/vendor-fpga.json").exists()
            assert "cancelled; process group killed" in (out / "logs/map.log").read_text()
        finally:
            # Also clean up when testing a regression in the cancellation code.
            if not pids and (out / "pids.json").exists():
                pids = json.loads((out / "pids.json").read_text())
            if pids:
                try:
                    os.killpg(pids[0], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)


@pytest.mark.parametrize("timeout", [False, True])
def test_run_step_restores_signal_handlers(tmp_path, timeout):
    import signal

    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    script = fake_exe(tmp_path / "step", "sleep 5" if timeout else "exit 0")
    flow.run_step("test", [str(script)], tmp_path, tmp_path / "step.log", 0.1)
    assert all(signal.getsignal(sig) == handler for sig, handler in before.items())


@pytest.mark.parametrize("when", ["before", "after"])
def test_cancellation_during_process_creation_does_not_lose_child(tmp_path, monkeypatch, when):
    import signal

    popen = flow.subprocess.Popen
    children = []
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    def interrupted_popen(*args, **kwargs):
        if when == "before":
            os.kill(os.getpid(), signal.SIGTERM)
        child = popen(*args, **kwargs)
        children.append(child)
        if when == "after":
            os.kill(os.getpid(), signal.SIGTERM)
        return child
    monkeypatch.setattr(flow.subprocess, "Popen", interrupted_popen)
    script = fake_exe(tmp_path / "slow", "exec sleep 60")
    try:
        with pytest.raises(KeyboardInterrupt):
            flow.run_step("test", [str(script)], tmp_path, tmp_path / "step.log", 10)
        assert len(children) == 1 and children[0].returncode == -signal.SIGKILL
        assert all(signal.getsignal(sig) == handler for sig, handler in before.items())
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
