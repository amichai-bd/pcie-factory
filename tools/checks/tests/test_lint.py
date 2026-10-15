import shutil
from pathlib import Path

import pytest

from build import sim
from build.filelist import resolve
from build.ip import discover
from checks import lint
from checks.common import CheckConfigError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
needs_verilator = pytest.mark.skipif(sim.find_verilator() is None,
                                     reason="verilator not available")

UNUSED = """`default_nettype none
module xy_blk_sub #(
    parameter int W = 8
) (
    input  logic [W-1:0] a_i,
    output logic [W-1:0] y_o
);
  logic [W-1:0] spare;
  assign spare = a_i;
  assign y_o = a_i + W'(1);
endmodule
`default_nettype wire
"""


@pytest.fixture
def good(tmp_path) -> Path:
    dst = tmp_path / "src"
    shutil.copytree(FIXTURES / "good/src", dst)
    return dst


def run(src: Path, ip_ids=None):
    return lint.run(src, discover(src), ip_ids)


def test_parse_output(tmp_path):
    out = (f"%Warning-UNUSEDSIGNAL: {tmp_path}/rtl/a/x.sv:4:9: Signal is not used: 'clk'\n"
           "                                : ... note: In instance 'x'\n"
           "%Warning-MULTITOP: /elsewhere/y.sv:6:8: Multiple top level modules\n"
           "%Error: Specified --top-module 'zz' was not found in design.\n"
           "%Error: Exiting due to 1 error(s)\n"
           "%Warning-BAD FORMAT\n")
    msgs = lint.parse_output(out, tmp_path)
    assert [m.render() for m in msgs] == [
        "rtl/a/x.sv:4: Warning-UNUSEDSIGNAL: Signal is not used: 'clk'",
        "/elsewhere/y.sv:6: Warning-MULTITOP: Multiple top level modules",
        "Error: Specified --top-module 'zz' was not found in design.",
        "Error: unparsed Verilator output: %Warning-BAD FORMAT",
    ]


def test_targets_and_command(tmp_path):
    src = FIXTURES / "good/src"
    ips = {ip.id: ip for ip in discover(src)}
    flat = resolve(ips["blk"].rtl_f)
    ts = lint.targets(ips["blk"], flat, "xy_")
    assert [(t.label, t.top) for t in ts] == [
        ("blk", "xy_blk"), ("blk + fpga/xy_blk_board.sv", "xy_blk_board")]
    assert lint.targets(ips["blk"], flat, "zz_")[0].top is None  # Verilator picks
    sub = lint.targets(ips["blk_sub"], resolve(ips["blk_sub"].rtl_f), "xy_")
    assert [(t.label, t.top) for t in sub] == [("blk_sub", "xy_blk_sub")]
    cmd = lint.command(Path("/v"), flat, list(ts[1].extra), ts[1].top)
    assert cmd[:6] == ["/v", "--lint-only", "-Wall", "-Wno-fatal", "--top-module", "xy_blk_board"]
    assert cmd[6].startswith("+incdir+") and cmd[7].startswith("+incdir+")
    assert cmd[-1].endswith("fpga/blk/xy_blk_board.sv")
    assert [Path(c).name for c in cmd[8:-1]] == [
        "xy_blk_pkg.sv", "common_flop.sv", "xy_blk_sub.sv", "xy_blk.sv"]


def waiver_file(src: Path, text: str) -> None:
    (src / lint.WAIVER_FILE).write_text(text)


@pytest.mark.parametrize("text, msg", [
    ("waivers: {}\n", "'waivers' list"),
    ("waivers:\n  - {file: a.sv, rule: WIDTH, line: 3}\n", "'reason' must be"),
    ("waivers:\n  - {file: a.sv, rule: WIDTH, reason: r}\n", "give 'line' or 'match'"),
    ("waivers:\n  - {file: a.sv, rule: width, line: 1, reason: r}\n", "warning code"),
    ("waivers:\n  - {file: a.sv, rule: WIDTH, line: 0, reason: r}\n", "positive integer"),
    ("waivers:\n  - {file: a.sv, rule: WIDTH, line: 1, reason: r, why: x}\n", "unknown key"),
    ("waivers: [\n", "invalid YAML"),
])
def test_waiver_file_errors(tmp_path, text, msg):
    waiver_file(tmp_path, text)
    with pytest.raises(CheckConfigError, match=msg):
        lint.load_waivers(tmp_path)


def test_no_waiver_file(tmp_path):
    assert lint.load_waivers(tmp_path) == []


def test_waiver_covers():
    m = lint.Message("Warning", "UNUSEDSIGNAL", "a.sv", 6, "Signal is not used: 'spare'")
    assert lint.Waiver("a.sv", "UNUSEDSIGNAL", "r", match="'spare'").covers(m)
    assert lint.Waiver("a.sv", "UNUSEDSIGNAL", "r", line=6).covers(m)
    assert not lint.Waiver("a.sv", "UNUSEDSIGNAL", "r", line=7).covers(m)
    assert not lint.Waiver("b.sv", "UNUSEDSIGNAL", "r", line=6).covers(m)
    assert not lint.Waiver("a.sv", "WIDTH", "r", line=6).covers(m)
    err = lint.Message("Error", "", "a.sv", 6, "boom")
    assert not lint.Waiver("a.sv", "UNUSEDSIGNAL", "r", line=6).covers(err)


def test_no_ips_needs_no_verilator(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: None)
    r = run(tmp_path)
    assert r.passed and r.checked.startswith("0 IP(s), 0 shared block(s), 0 Verilator run(s)")


def test_missing_verilator_is_an_error(good, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: None)
    with pytest.raises(sim.SimError, match="verilator not found"):
        run(good)


@needs_verilator
def test_good_fixture_lints_clean(good):
    r = run(good)
    assert r.passed, r.findings
    assert r.checked == ("2 IP(s), 1 shared block(s), 4 Verilator run(s) with -E audit, "
                         "0 waiver(s), 0 warning(s) waived")


@needs_verilator
def test_group_folder_block_is_linted(good):
    grp = good / "rtl/common/grp"
    grp.mkdir()
    (grp / "common_grouped.sv").write_text(
        "`default_nettype none\nmodule common_grouped (\n  input logic a_i,\n"
        "  output logic b_o\n);\n  assign b_o = 1'b0;\nendmodule\n`default_nettype wire\n")
    (grp / "common_grouped.rtl.f").write_text("common_grouped.sv\n")
    r = run(good)
    assert r.checked.startswith("2 IP(s), 2 shared block(s)")
    assert any(f.startswith("common/common_grouped: rtl/common/grp/common_grouped.sv:")
               and "UNUSEDSIGNAL" in f for f in r.findings), r.findings


@needs_verilator
def test_bad_fixture_fails():
    r = run(FIXTURES / "bad/src")
    assert r.findings[0].startswith("broken: rtl filelist does not resolve")
    assert "blk: rtl/blk/xy_blk.sv:4: Warning-UNUSEDSIGNAL: Signal is not used: 'clk'" in r.findings


@needs_verilator
def test_waiver_used_and_stale(good):
    (good / "rtl/blk_sub/xy_blk_sub.sv").write_text(UNUSED)
    # The sub-IP is linted on its own and inside blk: one warning per run.
    r = run(good)
    assert [f for f in r.findings if "UNUSEDSIGNAL" in f] == [
        "blk: rtl/blk_sub/xy_blk_sub.sv:8: Warning-UNUSEDSIGNAL: Signal is not used: 'spare'",
        "blk + fpga/xy_blk_board.sv: rtl/blk_sub/xy_blk_sub.sv:8: Warning-UNUSEDSIGNAL: "
        "Signal is not used: 'spare'",
        "blk_sub: rtl/blk_sub/xy_blk_sub.sv:8: Warning-UNUSEDSIGNAL: "
        "Signal is not used: 'spare'",
    ]
    waiver_file(good, "waivers:\n"
                "  - file: rtl/blk_sub/xy_blk_sub.sv\n"
                "    rule: UNUSEDSIGNAL\n"
                "    match: \"'spare'\"\n"
                "    reason: reserved input\n"
                "  - {file: rtl/blk/xy_blk.sv, rule: WIDTH, line: 3, reason: old}\n")
    r = run(good)
    assert r.findings == ["lint_waivers.yaml: stale waiver rtl/blk/xy_blk.sv WIDTH line 3: "
                          "matches no warning"]
    assert r.checked.endswith("2 waiver(s), 3 warning(s) waived")
    assert run(good, {"blk_sub"}).passed  # stale waivers are not judged on --ip runs


@needs_verilator
def test_wrapper_lint_failure(good):
    (good / "fpga/blk/xy_blk_board.sv").write_text(
        (good / "fpga/blk/xy_blk_board.sv").read_text().replace(".en_i(1'b1)", ".en_i(2'b11)"))
    r = run(good)
    assert len(r.findings) == 1 and r.findings[0].startswith(
        "blk + fpga/xy_blk_board.sv: fpga/blk/xy_blk_board.sv:")
    assert "WIDTH" in r.findings[0]


def test_non_sv_source_is_not_linted(good, monkeypatch):
    # A Verilator control file in the closure would waive warnings outside
    # the reviewed waiver file, so lint refuses to run that IP.
    monkeypatch.setattr(sim, "find_verilator", lambda: None)  # never reached for blk_sub
    (good / "rtl/blk_sub/waive.vlt").write_text("`verilator_config\nlint_off -rule WIDTH\n")
    f = good / "rtl/blk_sub/blk_sub.rtl.f"
    f.write_text(f.read_text() + "waive.vlt\n")
    r = lint.run(good, discover(good), {"blk_sub"})
    assert r.findings == ["blk_sub: not linted: rtl filelist lists non-.sv/.svh source(s): "
                          "rtl/blk_sub/waive.vlt"]


def test_incdir_outside_rtl_is_not_linted(good, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: None)  # never reached for blk_sub
    f = good / "rtl/blk_sub/blk_sub.rtl.f"
    f.write_text("+incdir+../../dv/blk_sub\n" + f.read_text())
    r = lint.run(good, discover(good), {"blk_sub"})
    assert r.findings == ["blk_sub: not linted: rtl filelist includes from outside rtl/: dv/blk_sub"]


WAIVE_CFG = "`verilator_config\nlint_off -rule UNUSEDSIGNAL\n"
WAIVE_COMMENT = "// verilator lint_off UNUSEDSIGNAL\n"


def test_audit_preprocessed(tmp_path):
    src = tmp_path / "src"
    out = (f'`line 1 "{src}/rtl/a/a.sv" 1\nmodule a;\n'
           f'`line 1 "{src}/rtl/a/../../dv/a/w.txt" 1\n`verilator_config\n'
           "/*verilator lint_off WIDTH*/\n/*verilator warns about prose*/\n"
           f'`line 1 "{tmp_path}/elsewhere.sv" 1\n'
           f'`line 1 "{src}/fpga/a/a_board.sv" 1\n')
    assert lint.audit_preprocessed(out, src) == [
        "preprocessed input from dv/a/w.txt contains `verilator_config",
        "preprocessed input from dv/a/w.txt contains /*verilator lint_off WIDTH*/",
        "verilator reads dv/a/w.txt, which is not below rtl/ in the source root",
        f"verilator reads {tmp_path}/elsewhere.sv, which is not below rtl/ "
        "in the source root",
        "verilator reads fpga/a/a_board.sv, which is not below rtl/ "
        "in the source root",
    ]
    clean = f'`line 1 "{src}/rtl/a/a.sv" 1\nmodule a;\n/*verilator warns about prose*/\n'
    assert lint.audit_preprocessed(clean, src) == []
    wrapper_only = f'`line 1 "{src}/fpga/a/a_board.sv" 1\n'
    assert lint.audit_preprocessed(wrapper_only, src, (src / "fpga/a/a_board.sv",)) == []


def _unused_and_include(good, include_line):
    # An unused signal in the sub-IP, plus a listed, allowed-looking .svh.
    (good / "rtl/blk_sub/xy_blk_sub.sv").write_text(UNUSED)
    (good / "rtl/blk_sub/xy_blk_sub_cfg.svh").write_text(include_line + "\n")
    f = good / "rtl/blk_sub/blk_sub.rtl.f"
    f.write_text("+incdir+.\nxy_blk_sub_cfg.svh\n" + f.read_text())


@needs_verilator
@pytest.mark.parametrize("waiver", [WAIVE_CFG, WAIVE_COMMENT])
def test_include_traversal_from_allowed_incdir_fails(good, waiver):
    # Route (a): +incdir+. is under rtl/, but `include "../../dv/blk_sub/w.txt" leaves it.
    (good / "dv/blk_sub/w.txt").write_text(waiver)
    _unused_and_include(good, '`include "../../dv/blk_sub/w.txt"')
    r = lint.run(good, discover(good), {"blk_sub"})
    assert ("blk_sub: verilator reads dv/blk_sub/w.txt, which is not below rtl/ "
            "in the source root") in r.findings
    assert any(x.startswith("blk_sub: preprocessed input from dv/blk_sub/w.txt contains")
               for x in r.findings)


@needs_verilator
def test_listed_source_outside_rtl_is_seen_by_preprocessor(good):
    # Route (b): a .svh listed from dv/ carrying the waiver.
    (good / "rtl/blk_sub/xy_blk_sub.sv").write_text(UNUSED)
    (good / "dv/blk_sub/cfg.svh").write_text(WAIVE_CFG)
    f = good / "rtl/blk_sub/blk_sub.rtl.f"
    f.write_text("../../dv/blk_sub/cfg.svh\n" + f.read_text())
    r = lint.run(good, discover(good), {"blk_sub"})
    assert ("blk_sub: verilator reads dv/blk_sub/cfg.svh, which is not below rtl/ "
            "in the source root") in r.findings
    assert "blk_sub: preprocessed input from dv/blk_sub/cfg.svh contains `verilator_config" \
        in r.findings


@needs_verilator
def test_good_fixture_preprocessed_view_is_clean(good):
    assert run(good).passed  # the wrapper is allowed for its own run only
