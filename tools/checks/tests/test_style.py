import re
from pathlib import Path

import pytest

from build.ip import REPO_ROOT, SHARED_DIR, discover
from ci.config import load_rules
from checks import style
from checks.style import RULES, check_text, module_ports

FIXTURES = Path(__file__).resolve().parent / "fixtures"

GOOD = """`default_nettype none
`include "common_macros.svh"
// A clean module.
module xy_a_m #(
    parameter int W = xy_a_pkg::W
) (
    input  logic         clk_i,
    input  logic         rst_ni,
    input  logic [W-1:0] d_i,
    output logic [W-1:0] q_o,
    output logic         busy_no,
    inout  logic         pad_io
);
  logic [W-1:0] q_d, q_q;
  logic         rst;
  assign rst = !rst_ni;
  always_comb begin
    q_d = d_i;
  end
  `COMMON_DFF_RST(q_q, q_d, clk_i, rst)
  assign q_o = q_q;
  assign busy_no = 1'b1;
  sub u_sub (.a_i(q_q), .y_o());  // "always" in a string or comment is fine
endmodule
`default_nettype wire
"""

HEADER = "rtl/common/common_macros.svh"
VOCAB = style.Vocabulary(frozenset({HEADER}), frozenset({"common_macros.svh"}),
                         frozenset({"COMMON_MACROS_SVH", "COMMON_DFF_RST", "COMMON_METAFLOP"}),
                         frozenset({"common_cdc_sync2"}),
                         frozenset({"COMMON_DFF_RST", "COMMON_METAFLOP"}))


def rules(findings):
    return sorted({f.split(": ")[1] for f in findings})


def check(body: str, name="xy_a_m.sv", prefix="xy_a"):
    return check_text(name, body, prefix, vocab=VOCAB)


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


# A non-vocabulary include file with one conditional inside its guard.
def svh(cond: str) -> str:
    return f"`ifndef X_SVH\n`define X_SVH\n{cond}\n`define X_Y 1\n`endif\n`endif\n"


DFF = "`COMMON_DFF_RST(q_q, q_d, clk_i, rst)"


@pytest.mark.parametrize("rule, text, name", [
    ("default-nettype", GOOD.replace("`default_nettype none\n", ""), None),
    ("default-nettype", GOOD.replace("`default_nettype wire\n", ""), None),
    ("net-keyword", mutate("logic [W-1:0] q_d, q_q;", "wire [W-1:0] q_d; reg [W-1:0] q_q;"), None),
    ("plain-always", mutate("always_comb begin", "always @* begin"), None),
    ("plain-always", mutate("always_comb begin", "always_latch begin"), None),
    ("interface-union", mutate("logic [W-1:0] q_d, q_q;",
                               "logic [W-1:0] q_d, q_q;\n  union packed {logic a;} u;"), None),
    ("wildcard-connection", mutate("(.a_i(q_q), .y_o())", "(.*)"), None),
    ("sim-ifdef", svh("`ifdef SYNTHESIS"), "x_defs.svh"),
    ("external-ifdef", svh("`ifdef ALTERA_RESERVED_QIS"), "x_defs.svh"),
    ("external-ifdef", svh("`ifndef XY_A_FAST"), "x_defs.svh"),
    ("task-function", mutate("  assign q_o = q_q;",
                             "  function automatic logic f(); return 1'b0; endfunction\n"
                             "  assign q_o = q_q;"), None),
    ("task-function", mutate("  assign q_o = q_q;", "  task t; endtask\n  assign q_o = q_q;"), None),
    ("typedef-outside-package", mutate("  logic [W-1:0] q_d, q_q;",
                                       "  typedef logic [W-1:0] t_w;\n  t_w q_d, q_q;"), None),
    ("port-suffix", mutate("input  logic         clk_i,", "input  logic         clk,"), None),
    ("port-suffix", mutate("output logic         busy_no,", "output logic         busy_ni,"), None),
    ("port-suffix", mutate("inout  logic         pad_io", "inout  logic         pad_o"), None),
    ("tool-pragma", mutate("  always_comb", "  // verilator lint_off WIDTH\n  always_comb"), None),
    ("tool-pragma", mutate("  always_comb", "  /* synthesis translate_off */\n  always_comb"), None),
    ("tool-pragma", mutate("  always_comb", "  /*verilator public*/\n  always_comb"), None),
    ("tool-pragma", mutate("  always_comb", "  // synopsys full_case\n  always_comb"), None),
    ("tool-pragma", mutate("  always_comb", "  // pragma protect begin\n  always_comb"), None),
    ("tool-pragma", mutate("  always_comb", "  // altera message_off 10230\n  always_comb"), None),
    # The four captain rules (RTL BKM "Design rules").
    ("nonblocking-assignment", mutate("    q_d = d_i;", "    q_d <= d_i;"), None),
    ("nonblocking-assignment", mutate("  assign q_o = q_q;",
                                      "  always_comb begin\n    {q_o} <= q_q;\n  end"), None),
    ("raw-always-ff", mutate(DFF, "always_ff @(posedge clk_i) q_q = q_d;"), None),
    ("raw-assertion", mutate(DFF, DFF + "\n  a_x: assert property (@(posedge clk_i) q_q == q_d);"),
     None),
    ("raw-assertion", mutate(DFF, DFF + "\n  if (W < 1) begin : g_w\n    $error(\"W\");\n  end"),
     None),
    ("raw-assertion", mutate("    q_d = d_i;", "    q_d = d_i;\n    assert (q_d == d_i);"), None),
    ("raw-initial", mutate(DFF, DFF + "\n  initial q_q = '0;"), None),
    # Rule 2 also bans the legacy hand-written assertion: $display + $finish.
    ("system-task", mutate("    q_d = d_i;", "    q_d = d_i;\n    if (q_q == '1) begin\n"
                           "      $display(\"bad\");\n      $finish;\n    end"), None),
    ("system-task", mutate("    q_d = d_i;", "    q_d = d_i;\n    if (q_q == '1) $stop;"), None),
    ("system-task", mutate(DFF, DFF + "\n  logic [7:0] m [4];\n  always_comb $readmemh(\"f\", m);"),
     None),
    ("system-task", mutate("    q_d = d_i;", "    q_d = W'($random);"), None),
    # Rule 4: enums and structs only in packages, with or without typedef.
    ("typedef-outside-package", mutate("  logic         rst;",
                                       "  enum logic [1:0] {IDLE, RUN} state_q;\n  logic         rst;"),
     None),
    ("typedef-outside-package", mutate("  logic         rst;",
                                       "  struct packed {logic a; logic b;} s_q;\n  logic         rst;"),
     None),
    # Macros live only in the headers: no token-pasting evasions.
    ("local-define", mutate("  logic         rst;", "`define NB <=\n  logic         rst;"), None),
    ("local-define", mutate("  logic         rst;", "`undef COMMON_DFF_RST\n  logic         rst;"),
     None),
    ("nonblocking-assignment", mutate("  always_comb begin\n    q_d = d_i;\n  end",
                                      "  always_comb q_d <= d_i;"), None),
    ("conditional-compilation", mutate("  assign q_o = q_q;",
                                       "`ifdef SYNTHESIS\n  assign q_o = q_q;\n`endif"), None),
    ("conditional-compilation", mutate("  assign q_o = q_q;",
                                       "`ifdef XY_A_ON\n  assign q_o = q_q;\n`else\n"
                                       "  assign q_o = '0;\n`endif"), None),
    ("conditional-compilation", GOOD.replace("module xy_a_m",
                                             "`ifndef XY_A_M_SV\n`define XY_A_M_SV\nmodule xy_a_m")
     .replace("endmodule", "endmodule\n`endif"), None),
    ("package-import", mutate("module xy_a_m #(", "module xy_a_m\n  import xy_a_pkg::*;\n#("), None),
    ("package-import", mutate("  logic         rst;", "  import xy_a_pkg::W;\n  logic         rst;"),
     None),
    ("include-path", mutate('`include "common_macros.svh"', '`include "rtl/common/common_macros.svh"'),
     None),
    ("include-path", mutate('`include "common_macros.svh"',
                            '`include "common_macros.svh"\n`include "xy_a_defs.svh"'), None),
    ("vocabulary-include", GOOD.replace('`include "common_macros.svh"\n', ""), None),
    # The hidden register: a sampled-value function outside an assertion
    # macro, given a clock by a default clocking block.
    ("sampled-value", mutate("  assign q_o = q_q;", "  assign q_o = $past(d_i);"), None),
    ("sampled-value", mutate("    q_d = d_i;", "    q_d = $rose(d_i[0]) ? d_i : q_q;"), None),
    ("clocking-block", mutate("  logic         rst;", "  default clocking cb @(posedge clk_i);\n"
                              "  endclocking\n  logic         rst;"), None),
    ("clocking-block", mutate("  logic         rst;", "  global clocking gc @(posedge clk_i); "
                              "endclocking\n  logic         rst;"), None),
    ("cdc-cell-macro", mutate("  sub u_sub", "  common_cdc_sync2 #(.WIDTH(1)) u_s (.dst_clk_i(clk_i),"
                              " .dst_rst_i(rst), .src_data_i(q_q[0]), .dst_data_o());\n  sub u_sub"),
     None),
])
def test_rule_fires(rule, text, name):
    assert rule in rules(check(text, name or "xy_a_m.sv"))


def test_every_rule_is_tested_and_documented():
    tested = {"default-nettype", "net-keyword", "plain-always", "interface-union",
              "wildcard-connection", "sim-ifdef", "task-function", "typedef-outside-package",
              "port-suffix", "tool-pragma", "file-name", "verilator-config", "external-ifdef",
              "nonblocking-assignment", "conditional-compilation", "raw-always-ff",
              "raw-assertion", "raw-initial", "package-import", "include-path",
              "vocabulary-include", "cdc-cell-macro", "system-task", "local-define",
              "sampled-value", "clocking-block", "object-macro"}
    assert tested == set(RULES)
    spec = (Path(__file__).resolve().parents[3] / "wiki/tools/checks/SPEC.md").read_text()
    for rule, (anchor, _) in RULES.items():
        assert f"`{rule}`" in spec and f"rtl.md#{anchor}" in spec
    bkm = (Path(__file__).resolve().parents[3] / "wiki/bkm/rtl.md").read_text()
    headings = {re.sub(r"[^a-z0-9 -]", "", h.lower()).strip().replace(" ", "-")
                for h in re.findall(r"^#+ (.*)$", bkm, re.M)}
    for anchor, _ in RULES.values():  # every owner anchor is a BKM heading
        assert anchor in headings, anchor


def test_vocabulary_header_may_hold_reserved_constructs():
    text = ("`ifndef COMMON_MACROS_SVH\n`define COMMON_MACROS_SVH\n`ifdef VERILATOR\n"
            "`define COMMON_X(Q, D, C) always_ff @(posedge C) Q <= D;\n`endif\n"
            "`ifndef SYNTHESIS\n`define COMMON_A(N, C, P) N: assert property (@(posedge C) P);\n"
            "`define COMMON_P(L) initial $fatal(1, L);\n`endif\n"
            '`include "common_macros.svh"\n`endif\n')
    assert check(text, HEADER) == []
    # The same text anywhere else breaks the reserved-construct rules.
    found = rules(check(text, "rtl/a/xy_a_defs.svh"))
    assert {"raw-always-ff", "raw-assertion", "raw-initial", "nonblocking-assignment",
            "sim-ifdef"} <= set(found), found
    # Only SYNTHESIS and VERILATOR are header guards.
    assert rules(check("`ifdef SIMULATION\n`endif\n", HEADER)) == ["sim-ifdef"]


def test_pure_system_functions_pass():
    text = mutate("    q_d = d_i;", "    q_d = d_i + W'($clog2(W)) + W'($bits(q_q)) + W'($countones(d_i))"
                  " + W'($signed(d_i)) + W'($unsigned(d_i)) + W'($size(d_i)) + W'($high(d_i));")
    assert check(text) == []



ASSERT_VOCAB = style.Vocabulary(VOCAB.headers, VOCAB.names,
                                VOCAB.macros | {"COMMON_ASSERT", "COMMON_ASSERT_CLOCKED",
                                                "COMMON_ASSERTIONS_SVH"},
                                VOCAB.cells,
                                VOCAB.functions | {"COMMON_ASSERT", "COMMON_ASSERT_CLOCKED"})


def test_sampled_value_functions_pass_in_assertion_macros():
    text = mutate(DFF, DFF + "\n  `COMMON_ASSERT(a_s, clk_i, rst, $past(q_d) == q_q || $stable(q_q))"
                  "\n  `COMMON_ASSERT(a_e, clk_i, rst,\n    ($rose(d_i[0]) || $fell(d_i[0])) |->"
                  " $changed(d_i) && $sampled(q_q) == q_q)"
                  "\n  `COMMON_ASSERT_CLOCKED(a_c, clk_i, rst, q_q == $past(q_d))")
    assert check_text("xy_a_m.sv", text, "xy_a", vocab=ASSERT_VOCAB) == []


@pytest.mark.parametrize("extra", [
    # After the macro's closing parenthesis.
    "  `COMMON_ASSERT(a_s, clk_i, rst, q_q == q_d) assign busy_x = $past(q_q[0]);",
    # In a non-assertion vocabulary macro's arguments.
    "  `COMMON_DFF_RST(q_x, $past(q_d), clk_i, rst)",
    # In a macro the headers do not define.
    "  `COMMON_ASSERT_X(a_s, clk_i, rst, $stable(q_q))",
    # In plain logic.
    "  logic s;\n  assign s = $sampled(d_i[0]);",
    # After an object-like macro whose name has the assertion prefix: the
    # include guard expands to nothing, so the "(...)" is design text (the
    # #135 review case, a hidden register in a DFF macro's next value).
    "  `COMMON_DFF_RST(q_x, `COMMON_ASSERTIONS_SVH($past(q_d)), clk_i, rst)",
])
def test_sampled_value_outside_assertion_macro_fires(extra):
    text = mutate(DFF, DFF + "\n" + extra)
    found = check_text("xy_a_m.sv", text, "xy_a", vocab=ASSERT_VOCAB)
    assert "sampled-value" in rules(found), found


def test_include_guard_is_not_an_assertion_macro():
    # The #135 review evasion: style, lint and cdc passed while
    # `state_q <= ($past(state_d))` hid a second register stage.
    text = mutate(DFF, "`COMMON_DFF_RST(q_q, `COMMON_ASSERTIONS_SVH($past(q_d)), clk_i, rst)")
    found = check_text("xy_a_m.sv", text, "xy_a", vocab=ASSERT_VOCAB)
    assert rules(found) == ["object-macro", "sampled-value"], found
    # Any object-like vocabulary macro is a finding outside the headers.
    text = mutate("  assign q_o = q_q;", "  assign q_o = q_q `COMMON_MACROS_SVH;")
    assert rules(check(text)) == ["object-macro"]
    # The headers may name their own guards.
    assert check("`ifndef COMMON_MACROS_SVH\n`define COMMON_MACROS_SVH\n"
                 "`define COMMON_Y `COMMON_MACROS_SVH\n`endif\n", HEADER) == []


def test_vocabulary_function_like_macros(tmp_path):
    d = tmp_path / "rtl" / "common"
    d.mkdir(parents=True)
    (d / "common_assertions.svh").write_text(
        "`ifndef COMMON_ASSERTIONS_SVH\n`define COMMON_ASSERTIONS_SVH\n"
        "`define COMMON_ASSERT(N, C, R, P) N: assert property (P);\n"
        "`define COMMON_ASSERT_SPACED (X) X\n`endif\n")
    v = style.vocabulary(tmp_path)
    assert v.macros == {"COMMON_ASSERTIONS_SVH", "COMMON_ASSERT", "COMMON_ASSERT_SPACED"}
    # With a space before "(" the parenthesis is the body, not a parameter list.
    assert v.functions == {"COMMON_ASSERT"}


def test_hidden_register_through_default_clocking():
    # The #128 review evasion: passed style, linted clean and simulated as a
    # one-cycle register outside the COMMON_DFF* macros.
    text = mutate("  assign q_o = q_q;", "  default clocking cb @(posedge clk_i); endclocking\n"
                  "  assign q_o = $past(d_i);")
    found = check_text("xy_a_m.sv", text, "xy_a", vocab=ASSERT_VOCAB)
    assert rules(found) == ["clocking-block", "sampled-value"], found
    assert len(found) == 2, found  # clocking is reported once, not at endclocking
    assert any(":21: clocking-block: clocking" in f for f in found), found
    assert any(":22: sampled-value: $past outside a COMMON_ASSERT* macro argument" in f
               for f in found), found


def test_clocking_block_fires_in_vocabulary_header():
    text = ("`ifndef COMMON_MACROS_SVH\n`define COMMON_MACROS_SVH\n"
            "`define COMMON_X(C) default clocking cb @(posedge C); endclocking\n`endif\n")
    assert rules(check(text, HEADER)) == ["clocking-block"]
    # A header may still use sampled-value functions in its macro bodies.
    text = "`define COMMON_ASSERT_S(N, C, S) N: assert property (@(posedge C) $stable(S));\n"
    assert check(text, HEADER) == []


def test_only_listed_headers_are_exempt(tmp_path):
    d = tmp_path / "rtl" / "common"
    d.mkdir(parents=True)
    (d / "common_macros.svh").write_text("`define COMMON_A(X) X\n")
    (d / "common_extra.svh").write_text("`define COMMON_B(X) X\n")
    v = style.vocabulary(tmp_path)
    assert v.names == {"common_macros.svh"} and v.macros == {"COMMON_A"}
    body = "`define COMMON_X(Q, D, C) always_ff @(posedge C) Q <= D;\n"
    assert check_text("rtl/common/common_macros.svh", body, "common_", vocab=VOCAB) == []
    found = rules(check_text("rtl/common/common_extra.svh", body, "common_", vocab=VOCAB))
    assert {"raw-always-ff", "nonblocking-assignment"} <= set(found), found
    assert style.VOCABULARY_HEADERS == ("common_macros.svh", "common_assertions.svh")


def test_vocabulary_headers_match_ci_config():
    # cfg/ci.yaml names the header files whose change runs the vocabulary
    # tests; it must list exactly the headers the style check exempts.
    rules_ = load_rules(REPO_ROOT / "cfg")
    expected = {f"src/rtl/{SHARED_DIR}/{name}" for name in style.VOCABULARY_HEADERS}
    assert set(rules_.vocabulary_paths) == expected
    assert len(rules_.vocabulary_paths) == len(style.VOCABULARY_HEADERS)


def test_nonblocking_ignores_comparisons():
    text = mutate("    q_d = d_i;", "    q_d = (d_i <= q_q) ? d_i : q_q;\n"
                  "    for (int i = 0; i <= 3; i++) q_d[i] = d_i[i];\n"
                  "    if (d_i <= W'(3)) q_d = '0;")
    assert check(text) == []


def test_vocabulary_from_tree(tmp_path):
    d = tmp_path / "rtl" / "common"
    d.mkdir(parents=True)
    (d / "common_macros.svh").write_text("`define COMMON_A(X) X\n// `define COMMON_NOT\n")
    (d / "common_other.svh").write_text("`define COMMON_OTHER(X) X\n")  # not a listed header
    (d / "common_cell.sv").write_text("")
    v = style.vocabulary(tmp_path)
    assert v.headers == {"rtl/common/common_macros.svh"} and v.names == {"common_macros.svh"}
    assert v.macros == {"COMMON_A"} and v.cells == set()
    (d / "common_macros.svh").write_text("`define COMMON_S(I) common_cell I (); // common_none\n")
    assert style.vocabulary(tmp_path).cells == {"common_cell"}
    assert style.vocabulary(tmp_path / "none") == style.Vocabulary()


def test_repository_vocabulary_covers_the_wrapped_cells():
    # Each crossing cell a vocabulary macro wraps falls under cdc-cell-macro.
    cells = style.vocabulary(REPO_ROOT / "src").cells
    assert {"common_cdc_sync2", "common_cdc_pulse", "common_cdc_rst"} <= cells


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
                  '  // always @* wire reg typedef union .* always_ff assert import `ifdef X\n'
                  '  /* task interface q <= d; initial */\n'
                  '  localparam string S = "always wire typedef <= assert $error";\n'
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
    assert hit == ["xy_a_m.sv:17: plain-always: always"]


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
    assert r.checked.startswith("7 RTL file(s)")


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
    assert "10 other includable or fpga/ file(s)" in r.checked  # all of dv/blk/ is includable


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
    """``cond`` inside an include file's guard (the conditional rules other
    than ``conditional-compilation`` apply to include files)."""
    return svh(cond)


def check_h(text: str, defined=frozenset()):
    return check_text("x_defs.svh", text, "xy_a", defined, VOCAB)


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
    found = [f for f in check_h(_wrap(cond)) if ": external-ifdef: " in f]
    assert any(detail in f for f in found), found


def test_external_ifdef_line_and_text():
    found = check_h(_wrap("`ifdef ALTERA_RESERVED_QIS"))
    assert found == ["x_defs.svh:3: external-ifdef: `ifdef ALTERA_RESERVED_QIS "
                     "is not `define'd by the RTL closure"]


@pytest.mark.parametrize("name", ["XY_A_ON", "SYNTHESIS"])
def test_name_on_next_line_fails_closed(name):
    # The scan is lexical: the name must be on the directive's line. A name
    # on a later line is reported as missing, never silently accepted.
    found = check_h(_wrap(f"`define XY_A_ON\n`ifdef\n  {name}"))
    assert rules(found) == ["external-ifdef"], found
    assert all(f.endswith("`ifdef without a macro name") for f in found), found


def test_macro_name_with_dollar():
    found = check_h(_wrap("`ifdef FOO_$x"))
    assert [f.split(": ", 2)[2] for f in found] == [
        "`ifdef FOO_$x is not `define'd by the RTL closure"]
    assert check_h(_wrap("`define FOO_$x\n`ifdef FOO_$x")) == []


def test_sim_macro_is_not_also_external():
    assert rules(check_h(_wrap("`ifdef VERILATOR"))) == ["sim-ifdef"]
    # Defining a tool macro does not make it internal.
    assert rules(check_h(_wrap("`define SYNTHESIS\n`ifdef SYNTHESIS"))) == ["sim-ifdef"]


def test_include_guard_passes_only_in_include_files():
    assert check_h("`ifndef X_DEFS_SVH\n`define X_DEFS_SVH\n`define X_W 8\n`endif\n") == []
    guarded = GOOD.replace("module xy_a_m", "`ifndef XY_A_M_SV\n`define XY_A_M_SV\nmodule xy_a_m")
    assert rules(check(guarded.replace("endmodule", "endmodule\n`endif"))) == [
        "conditional-compilation", "local-define"]


def test_closure_defined_macro_passes():
    text = _wrap("`ifdef XY_A_ON")
    assert rules(check_h(text)) == ["external-ifdef"]
    assert check_h(text, {"XY_A_ON"}) == []


def test_design_file_conditional_is_not_reported_twice():
    text = mutate("  assign q_o = q_q;", "`ifdef ALTERA_RESERVED_QIS\n  assign q_o = q_q;\n`endif")
    assert check(text) == ["xy_a_m.sv:21: conditional-compilation: `ifdef"]


def test_commented_ifdef_is_ignored():
    assert check(mutate("  assign q_o = q_q;",
                        "  // `ifdef ALTERA_RESERVED_QIS\n  assign q_o = q_q;")) == []


@pytest.mark.parametrize("where, macro, setup", [
    ("rtl/blk/include/w.svh", "XY_BLK_W", None),          # `define in another include file
    ("rtl/blk/include/w.svh", "XY_BLK_EN", "+define+"),   # +define+ in the filelist
    ("fpga/blk/w.svh", "XY_BLK_W", None),                 # wrapper linted with the IP's sources
])
def test_run_closure_macros_pass(tmp_path, where, macro, setup):
    src = _good_copy(tmp_path)
    if setup:
        f = src / "rtl/blk/blk.rtl.f"
        f.write_text(f"+define+{macro}\n" + f.read_text())
    (src / where).write_text(f"`ifdef {macro}\n`endif\n")
    r = style.run(src, discover(src), None)
    assert r.passed, r.findings


def _ifdef_in(path: Path, macro: str) -> None:
    path.write_text(path.read_text().replace(
        "endmodule", f"`ifdef {macro}\n`endif\nendmodule", 1))


def test_run_vendor_and_undefined_macros_fail(tmp_path):
    src = _good_copy(tmp_path)
    _ifdef_in(src / "rtl/blk/xy_blk.sv", "XY_BLK_W")  # even a defined macro, in a .sv
    _ifdef_in(src / "fpga/blk/xy_blk_board.sv", "XY_BLK_UNDEFINED")
    (src / "rtl/blk/include/w.txt").write_text("`ifdef ALTERA_RESERVED_QIS\n`endif\n")
    r = style.run(src, discover(src), None)
    assert sorted((f.split(":")[0], f.split(": ")[1]) for f in r.findings) == [
        ("fpga/blk/xy_blk_board.sv", "conditional-compilation"),
        ("rtl/blk/include/w.txt", "external-ifdef"),
        ("rtl/blk/xy_blk.sv", "conditional-compilation"),
    ]


def test_run_vocabulary_rules_on_tree(tmp_path):
    src = _good_copy(tmp_path)
    flop = src / "rtl/common/common_flop.sv"
    flop.write_text(flop.read_text().replace('`include "common_macros.svh"\n', ""))
    board = src / "fpga/blk/xy_blk_board.sv"
    board.write_text(board.read_text().replace(
        "endmodule", "  logic q;\n  always_ff @(posedge clk_i) q <= 1'b0;\nendmodule"))
    r = style.run(src, discover(src), None)
    assert sorted((f.split(":")[0], f.split(": ")[1]) for f in r.findings) == [
        ("fpga/blk/xy_blk_board.sv", "nonblocking-assignment"),
        ("fpga/blk/xy_blk_board.sv", "raw-always-ff"),
        ("rtl/common/common_flop.sv", "vocabulary-include"),
    ]


def test_cdc_cell_macro_allows_macro_and_shared_folder():
    sync = ("  `COMMON_METAFLOP(u_s, q_s, q_q[0], clk_i, rst)\n  sub u_sub")
    assert check(mutate("  sub u_sub", "  logic q_s;\n" + sync)) == []
    inst = mutate("  sub u_sub", "  common_cdc_sync2 u_s ();\n  sub u_sub")
    assert "cdc-cell-macro" not in rules(check_text("rtl/common/common_x.sv", inst, "common_",
                                                    vocab=VOCAB))
