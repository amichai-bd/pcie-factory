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
    out = (f"%Warning-UNUSEDSIGNAL: {tmp_path}/a/rtl/x.sv:4:9: Signal is not used: 'clk'\n"
           "                                : ... note: In instance 'x'\n"
           "%Warning-MULTITOP: /elsewhere/y.sv:6:8: Multiple top level modules\n"
           "%Error: Specified --top-module 'zz' was not found in design.\n"
           "%Error: Exiting due to 1 error(s)\n"
           "%Warning-BAD FORMAT\n")
    msgs = lint.parse_output(out, tmp_path)
    assert [m.render() for m in msgs] == [
        "a/rtl/x.sv:4: Warning-UNUSEDSIGNAL: Signal is not used: 'clk'",
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
    sub = lint.targets(ips["blk/sub"], resolve(ips["blk/sub"].rtl_f), "xy_")
    assert [(t.label, t.top) for t in sub] == [("blk/sub", "xy_blk_sub")]
    cmd = lint.command(Path("/v"), flat, list(ts[1].extra), ts[1].top)
    assert cmd[:6] == ["/v", "--lint-only", "-Wall", "-Wno-fatal", "--top-module", "xy_blk_board"]
    assert cmd[6].startswith("+incdir+") and cmd[-1].endswith("fpga/xy_blk_board.sv")
    assert [Path(c).name for c in cmd[7:-1]] == [
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
    assert r.passed and r.checked.startswith("0 IP(s), 0 Verilator run(s)")


def test_missing_verilator_is_an_error(good, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: None)
    with pytest.raises(sim.SimError, match="verilator not found"):
        run(good)


@needs_verilator
def test_good_fixture_lints_clean(good):
    r = run(good)
    assert r.passed, r.findings
    assert r.checked == "2 IP(s), 3 Verilator run(s), 0 waiver(s), 0 warning(s) waived"


@needs_verilator
def test_bad_fixture_fails():
    r = run(FIXTURES / "bad/src")
    assert r.findings[0].startswith("broken: rtl filelist does not resolve")
    assert "blk: blk/rtl/xy_blk.sv:4: Warning-UNUSEDSIGNAL: Signal is not used: 'clk'" in r.findings


@needs_verilator
def test_waiver_used_and_stale(good):
    (good / "blk/sub/rtl/xy_blk_sub.sv").write_text(UNUSED)
    # The sub-IP is linted on its own and inside blk: one warning per run.
    r = run(good)
    assert [f for f in r.findings if "UNUSEDSIGNAL" in f] == [
        "blk: blk/sub/rtl/xy_blk_sub.sv:8: Warning-UNUSEDSIGNAL: Signal is not used: 'spare'",
        "blk + fpga/xy_blk_board.sv: blk/sub/rtl/xy_blk_sub.sv:8: Warning-UNUSEDSIGNAL: "
        "Signal is not used: 'spare'",
        "blk/sub: blk/sub/rtl/xy_blk_sub.sv:8: Warning-UNUSEDSIGNAL: "
        "Signal is not used: 'spare'",
    ]
    waiver_file(good, "waivers:\n"
                "  - file: blk/sub/rtl/xy_blk_sub.sv\n"
                "    rule: UNUSEDSIGNAL\n"
                "    match: \"'spare'\"\n"
                "    reason: reserved input\n"
                "  - {file: blk/rtl/xy_blk.sv, rule: WIDTH, line: 3, reason: old}\n")
    r = run(good)
    assert r.findings == ["lint_waivers.yaml: stale waiver blk/rtl/xy_blk.sv WIDTH line 3: "
                          "matches no warning"]
    assert r.checked.endswith("2 waiver(s), 3 warning(s) waived")
    assert run(good, {"blk/sub"}).passed  # stale waivers are not judged on --ip runs


@needs_verilator
def test_wrapper_lint_failure(good):
    (good / "blk/fpga/xy_blk_board.sv").write_text(
        (good / "blk/fpga/xy_blk_board.sv").read_text().replace(".en_i(1'b1)", ".en_i(2'b11)"))
    r = run(good)
    assert len(r.findings) == 1 and r.findings[0].startswith(
        "blk + fpga/xy_blk_board.sv: blk/fpga/xy_blk_board.sv:")
    assert "WIDTH" in r.findings[0]


def test_non_sv_source_is_not_linted(good, monkeypatch):
    # A Verilator control file in the closure would waive warnings outside
    # the reviewed waiver file, so lint refuses to run that IP.
    monkeypatch.setattr(sim, "find_verilator", lambda: None)  # never reached for blk/sub
    (good / "blk/sub/rtl/waive.vlt").write_text("`verilator_config\nlint_off -rule WIDTH\n")
    f = good / "blk/sub/rtl/sub.rtl.f"
    f.write_text(f.read_text() + "waive.vlt\n")
    r = lint.run(good, discover(good), {"blk/sub"})
    assert r.findings == ["blk/sub: not linted: rtl filelist lists non-.sv/.svh source(s): "
                          "blk/sub/rtl/waive.vlt"]
