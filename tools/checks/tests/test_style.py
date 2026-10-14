from pathlib import Path

import pytest

from build.ip import discover
from checks import style
from checks.style import RULES, check_text, module_ports

FIXTURES = Path(__file__).resolve().parent / "fixtures"

GOOD = """`default_nettype none
// A clean module.
module xy_a_m
  import xy_a_pkg::*;
#(
    parameter int W = 8
) (
    input  logic         clk_i,
    input  logic         rst_ni,
    input  logic [W-1:0] d_i,
    output logic [W-1:0] q_o,
    output logic         busy_no,
    inout  logic         pad_io
);
  logic [W-1:0] q_d, q_q;
  always_comb begin
    q_d = d_i;
  end
  always_ff @(posedge clk_i) begin
    if (!rst_ni) q_q <= '0;
    else q_q <= q_d;
  end
  assign q_o = q_q;
  assign busy_no = 1'b1;
  sub u_sub (.a_i(q_q), .y_o());  // "always" in a string or comment is fine
endmodule
`default_nettype wire
"""


def rules(findings):
    return sorted({f.split(": ")[1] for f in findings})


def check(body: str, name="xy_a_m.sv", prefix="xy_a"):
    return check_text(name, body, prefix)


def test_good_module_is_clean():
    assert check(GOOD) == []


def test_good_package_is_clean():
    text = ("`default_nettype none\npackage xy_a_pkg;\n  typedef logic [3:0] t_x;\n"
            "  function automatic t_x f(input t_x a);\n    return a;\n  endfunction\n"
            "endpackage\n`default_nettype wire\n")
    assert check(text, "xy_a_pkg.sv") == []


@pytest.mark.parametrize("name", ["xy_a_waive.svh", "xy_a_m.sv"])
def test_verilator_config_block_rejected(name):
    # A listed include file carrying a config block would waive warnings
    # outside the reviewed waiver file.
    text = "`verilator_config\nlint_off -rule UNUSEDSIGNAL -file \"*\"\n"
    found = check(text, name)
    assert any(f.startswith(f"{name}:1: verilator-config: `verilator_config") for f in found), found


def test_include_header_needs_no_nettype():
    assert check("`ifndef X_SVH\n`define X_SVH\n`endif\n", "x_defs.svh") == []


def mutate(old: str, new: str) -> str:
    assert old in GOOD
    return GOOD.replace(old, new, 1)


@pytest.mark.parametrize("rule, text", [
    ("default-nettype", GOOD.replace("`default_nettype none\n", "")),
    ("default-nettype", GOOD.replace("`default_nettype wire\n", "")),
    ("net-keyword", mutate("logic [W-1:0] q_d, q_q;", "wire [W-1:0] q_d; reg [W-1:0] q_q;")),
    ("plain-always", mutate("always_comb begin", "always @* begin")),
    ("plain-always", mutate("always_comb begin", "always_latch begin")),
    ("interface-union", mutate("logic [W-1:0] q_d, q_q;",
                               "logic [W-1:0] q_d, q_q;\n  union packed {logic a;} u;")),
    ("wildcard-connection", mutate("(.a_i(q_q), .y_o())", "(.*)")),
    ("sim-ifdef", mutate("  assign q_o = q_q;", "`ifdef SYNTHESIS\n  assign q_o = q_q;\n`endif")),
    ("external-ifdef", mutate("  assign q_o = q_q;",
                              "`ifdef ALTERA_RESERVED_QIS\n  assign q_o = q_q;\n`endif")),
    ("external-ifdef", mutate("  assign q_o = q_q;",
                              "`ifndef XY_A_FAST\n  assign q_o = q_q;\n`endif")),
    ("task-function", mutate("  assign q_o = q_q;",
                             "  function automatic logic f(); return 1'b0; endfunction\n"
                             "  assign q_o = q_q;")),
    ("task-function", mutate("  assign q_o = q_q;", "  task t; endtask\n  assign q_o = q_q;")),
    ("typedef-outside-package", mutate("  logic [W-1:0] q_d, q_q;",
                                       "  typedef logic [W-1:0] t_w;\n  t_w q_d, q_q;")),
    ("port-suffix", mutate("input  logic         clk_i,", "input  logic         clk,")),
    ("port-suffix", mutate("output logic         busy_no,", "output logic         busy_ni,")),
    ("port-suffix", mutate("inout  logic         pad_io", "inout  logic         pad_o")),
    ("tool-pragma", mutate("  always_comb", "  // verilator lint_off WIDTH\n  always_comb")),
    ("tool-pragma", mutate("  always_comb", "  /* synthesis translate_off */\n  always_comb")),
    ("tool-pragma", mutate("  always_comb", "  /*verilator public*/\n  always_comb")),
    ("tool-pragma", mutate("  always_comb", "  // synopsys full_case\n  always_comb")),
    ("tool-pragma", mutate("  always_comb", "  // pragma protect begin\n  always_comb")),
    ("tool-pragma", mutate("  always_comb", "  // altera message_off 10230\n  always_comb")),
])
def test_rule_fires(rule, text):
    assert rule in rules(check(text))


def test_every_rule_is_tested_and_documented():
    tested = {"default-nettype", "net-keyword", "plain-always", "interface-union",
              "wildcard-connection", "sim-ifdef", "task-function", "typedef-outside-package",
              "port-suffix", "tool-pragma", "file-name", "verilator-config", "external-ifdef"}
    assert tested == set(RULES)
    spec = (Path(__file__).resolve().parents[3] / "wiki/tools/checks/SPEC.md").read_text()
    for rule, (anchor, _) in RULES.items():
        assert f"`{rule}`" in spec and f"rtl.md#{anchor}" in spec


@pytest.mark.parametrize("name, prefix, body, detail", [
    ("xy_a_x.sv", "xy_a", GOOD, "module xy_a_m in file xy_a_x.sv"),
    ("xy_b_m.sv", "xy_b", GOOD.replace("xy_a_m", "xy_b_m").replace("xy_a_pkg", "xy_b_pkg"),
     None),
    ("zz_m.sv", "xy_a", GOOD.replace("xy_a_m", "zz_m"), "zz_m must start with xy_a"),
    ("xy_a_mm.sv", "xy_a", GOOD.replace("xy_a_m", "xy_a_mm"), None),
    ("xy_am.sv", "xy_a", GOOD.replace("xy_a_m", "xy_am"), "xy_am must start with xy_a"),
    ("xy_a_m.sv", None, GOOD, "src/README.md declares no module prefix"),
    ("xy_a_m.sv", "xy_a", GOOD.replace("endmodule", "endmodule\nmodule xy_a_n; endmodule"),
     "expected exactly one module or package, found 2"),
    ("xy_a_k.sv", "xy_a", "`default_nettype none\npackage xy_a_k;\nendpackage\n`default_nettype wire\n",
     "package xy_a_k must end in _pkg"),
])
def test_file_name(name, prefix, body, detail):
    found = [f for f in check(body, name, prefix) if ": file-name: " in f]
    if detail is None:
        assert found == []
    else:
        assert any(f.endswith(detail) for f in found), found


def test_comments_and_strings_do_not_trigger():
    text = mutate("  assign q_o = q_q;",
                  '  // always @* wire reg typedef union .*\n'
                  '  /* task interface */\n'
                  '  localparam string S = "always wire typedef";\n'
                  "  assign q_o = q_q;")
    assert check(text) == []


@pytest.mark.parametrize("prose", [
    "// Synthesis infers a flop here.",
    "// Verilator warns if this is unused.",
    "/* Pragma-free by design. */",
    "// synopsys tools are not used",
])
def test_tool_pragma_ignores_prose(prose):
    assert check(mutate("  always_comb", f"  {prose}\n  always_comb")) == []


def test_line_numbers():
    text = mutate("always_comb begin", "always @* begin")
    hit = [f for f in check(text) if "plain-always" in f]
    assert hit == ["xy_a_m.sv:16: plain-always: always"]


def test_module_ports_parsing():
    text = ("module m #(parameter int W = $clog2(4), parameter int D[2] = '{1, 2})"
            " (input logic [W-1:0] a_i, b_i, output logic [1:0] c_o [2], d);")
    assert [(d, n) for _, d, n in module_ports(text)] == [
        ("input", "a_i"), ("input", "b_i"), ("output", "c_o"), ("output", "d")]
    assert [(d, n) for _, d, n in module_ports("module m (a, b); endmodule")] == [
        (None, "a"), (None, "b")]
    assert list(module_ports("module m; endmodule")) == []


def test_run_good_fixture_clean():
    src = FIXTURES / "good/src"
    r = style.run(src, discover(src), None)
    assert r.passed, r.findings
    assert r.checked.startswith("6 RTL file(s)")


def test_run_bad_fixture_and_ip_filter():
    src = FIXTURES / "bad/src"
    ips = discover(src)
    r = style.run(src, ips, None)
    assert rules(r.findings) == ["default-nettype", "net-keyword", "plain-always",
                                 "port-suffix", "tool-pragma"]
    assert style.run(src, ips, {"broken"}).passed


def test_expected_prefix(tmp_path):
    src = FIXTURES / "good/src"
    ips = discover(src)
    assert style.expected_prefix(src / "rtl/blk_sub/xy_blk_sub.sv", src, ips, "xy_") == "xy_blk_sub"
    assert style.expected_prefix(src / "fpga/blk/xy_blk_board.sv", src, ips, "xy_") == "xy_blk"
    assert style.expected_prefix(src / "rtl/common/common_flop.sv", src, ips, "xy_") == "common_"
    assert style.expected_prefix(src / "dv/common/common_tb.sv", src, ips, "xy_") == "common_"
    assert style.expected_prefix(src / "rtl/blk/xy_blk.sv", src, ips, "xy_") == "xy_blk"


def test_run_flags_listed_include_with_verilator_config(tmp_path):
    import shutil
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "good/src", src)
    inc = src / "rtl/blk/include/xy_blk_defs.svh"
    inc.write_text(inc.read_text() + "`verilator_config\nlint_off -rule WIDTH\n")
    r = style.run(src, discover(src), None)
    assert r.findings == [
        "rtl/blk/include/xy_blk_defs.svh:5: verilator-config: `verilator_config"]


def _good_copy(tmp_path):
    import shutil
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "good/src", src)
    return src


@pytest.mark.parametrize("text, rule", [
    ("`verilator_config\nlint_off -rule UNUSEDSIGNAL\n", "verilator-config: `verilator_config"),
    ("// verilator lint_off UNUSEDSIGNAL\n", "tool-pragma: verilator lint_off UNUSEDSIGNAL"),
])
def test_includable_file_of_any_type_is_scanned(tmp_path, text, rule):
    # w.txt via an incdir under rtl/, and via +incdir+ dv/blk: both are
    # reachable by `include "w.txt", so both are scanned for directives.
    src = _good_copy(tmp_path)
    (src / "rtl/blk/include/w.txt").write_text(text)
    (src / "dv/blk/w.txt").write_text(text)
    f = src / "rtl/blk/blk.rtl.f"
    f.write_text("+incdir+../../dv/blk\n" + f.read_text())
    r = style.run(src, discover(src), None)
    assert sorted(r.findings) == [f"dv/blk/w.txt:1: {rule}", f"rtl/blk/include/w.txt:1: {rule}"]
    assert "9 other includable or fpga/ file(s)" in r.checked  # all of dv/blk/ is includable


def test_every_fpga_text_file_is_scanned(tmp_path):
    src = _good_copy(tmp_path)
    (src / "fpga/blk/board.qsf.tcl").write_text("set_global_assignment -name X Y\n`verilator_config\n")
    (src / "fpga/blk/logo.bin").write_bytes(b"\0`verilator_config")
    r = style.run(src, discover(src), None)
    assert r.findings == ["fpga/blk/board.qsf.tcl:2: verilator-config: `verilator_config"]


def test_dv_files_outside_rtl_incdirs_are_not_scanned(tmp_path):
    src = _good_copy(tmp_path)
    (src / "dv/blk/notes.txt").write_text("// verilator lint_off WIDTH\n")
    assert style.run(src, discover(src), None).passed


def _wrap(cond: str) -> str:
    return mutate("  assign q_o = q_q;", f"{cond}\n  assign q_o = q_q;\n`endif")


@pytest.mark.parametrize("cond, detail", [
    # A vendor macro: code the vendor flow synthesizes but lint never sees.
    ("`ifdef ALTERA_RESERVED_QIS", "`ifdef ALTERA_RESERVED_QIS"),
    # A macro nothing defines.
    ("`ifndef XY_A_FAST", "`ifndef XY_A_FAST"),
    ("`ifdef XY_A_ON\n`define XY_A_X\n`elsif XY_A_SLOW", "`elsif XY_A_SLOW"),
    ("`ifdef (XY_A_ON && !OTHER_TOOL)", "`ifdef OTHER_TOOL"),
    ("`ifdef (XY_A_ON && (OTHER_A) || OTHER_B)", "`ifdef OTHER_B"),
    ("`ifdef", "`ifdef without a macro name"),
])
def test_external_ifdef_fires(cond, detail):
    found = [f for f in check(_wrap(cond)) if ": external-ifdef: " in f]
    assert any(detail in f for f in found), found


def test_external_ifdef_line_and_text():
    found = check(_wrap("`ifdef ALTERA_RESERVED_QIS"))
    assert found == ["xy_a_m.sv:23: external-ifdef: `ifdef ALTERA_RESERVED_QIS "
                     "is not `define'd by the RTL closure"]


@pytest.mark.parametrize("name", ["XY_A_ON", "SYNTHESIS"])
def test_name_on_next_line_fails_closed(name):
    # The scan is lexical: the name must be on the directive's line. A name
    # on a later line is reported as missing, never silently accepted.
    text = _wrap(f"`define XY_A_ON\n`ifdef\n  {name}")
    found = check(text)
    assert rules(found) == ["external-ifdef"], found
    assert all(f.endswith("`ifdef without a macro name") for f in found), found


def test_macro_name_with_dollar():
    found = check(_wrap("`ifdef FOO_$x"))
    assert [f.split(": ", 2)[2] for f in found] == [
        "`ifdef FOO_$x is not `define'd by the RTL closure"]
    assert check(_wrap("`define FOO_$x\n`ifdef FOO_$x")) == []


def test_sim_macro_is_not_also_external():
    assert rules(check(_wrap("`ifdef VERILATOR"))) == ["sim-ifdef"]
    # Defining a tool macro does not make it internal.
    assert rules(check(_wrap("`define SYNTHESIS\n`ifdef SYNTHESIS"))) == ["sim-ifdef"]


@pytest.mark.parametrize("name, text", [
    ("x_defs.svh", "`ifndef X_DEFS_SVH\n`define X_DEFS_SVH\n`define X_W 8\n`endif\n"),
    ("xy_a_m.sv", GOOD.replace("module xy_a_m", "`ifndef XY_A_M_SV\n`define XY_A_M_SV\nmodule xy_a_m")
     .replace("endmodule", "endmodule\n`endif")),
])
def test_include_guard_passes(name, text):
    assert check(text, name) == []


def test_closure_defined_macro_passes():
    text = _wrap("`ifdef XY_A_ON")
    assert rules(check(text)) == ["external-ifdef"]
    assert check_text("xy_a_m.sv", text, "xy_a", {"XY_A_ON"}) == []


def test_commented_ifdef_is_ignored():
    assert check(mutate("  assign q_o = q_q;",
                        "  // `ifdef ALTERA_RESERVED_QIS\n  assign q_o = q_q;")) == []


def _ifdef_in(path: Path, macro: str) -> None:
    path.write_text(path.read_text().replace(
        "endmodule", f"`ifdef {macro}\n`endif\nendmodule", 1))


@pytest.mark.parametrize("where, macro, setup", [
    ("rtl/blk/xy_blk.sv", "XY_BLK_W", None),            # `define in an include file
    ("rtl/blk/xy_blk.sv", "XY_BLK_EN", "+define+"),     # +define+ in the filelist
    ("rtl/blk_sub/xy_blk_sub.sv", "XY_BLK_W", None),    # parent closure reaches the sub-IP
    ("fpga/blk/xy_blk_board.sv", "XY_BLK_W", None),     # wrapper linted with the IP's sources
])
def test_run_closure_macros_pass(tmp_path, where, macro, setup):
    src = _good_copy(tmp_path)
    if setup:
        f = src / "rtl/blk/blk.rtl.f"
        f.write_text(f"+define+{macro}\n" + f.read_text())
    _ifdef_in(src / where, macro)
    r = style.run(src, discover(src), None)
    assert r.passed, r.findings


def test_run_vendor_and_undefined_macros_fail(tmp_path):
    src = _good_copy(tmp_path)
    _ifdef_in(src / "rtl/blk/xy_blk.sv", "ALTERA_RESERVED_QIS")
    _ifdef_in(src / "fpga/blk/xy_blk_board.sv", "XY_BLK_UNDEFINED")
    (src / "rtl/blk/include/w.txt").write_text("`ifdef ALTERA_RESERVED_QIS\n`endif\n")
    r = style.run(src, discover(src), None)
    assert sorted((f.split(":")[0], f.split(": ")[1]) for f in r.findings) == [
        ("fpga/blk/xy_blk_board.sv", "external-ifdef"),
        ("rtl/blk/include/w.txt", "external-ifdef"),
        ("rtl/blk/xy_blk.sv", "external-ifdef"),
    ]
    assert any("`ifdef XY_BLK_UNDEFINED" in f for f in r.findings)
