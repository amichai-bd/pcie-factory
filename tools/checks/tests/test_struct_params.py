"""The struct-param-select style rule (Quartus Lite error 10742) on neutral
fixtures: a member select of a struct-typed localparam or parameter inside a
constant expression."""

import shutil
from pathlib import Path

import pytest

from build.ip import discover
from checks import style, struct_params
from checks.common import strip_comments_and_strings

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PKG = """`default_nettype none
package xy_a_pkg;
  typedef struct packed {
    logic [3:0] n;
    logic [3:0] m;
  } t_cfg;
  typedef t_cfg t_cfg_alias;
  typedef logic [7:0] t_byte;
  localparam t_cfg CFG = '{n: 4'd4, m: 4'd2};
  localparam t_cfg_alias ALT = CFG;
  localparam t_cfg ONE = CFG, TWO = CFG;
  localparam t_byte PLAIN = 8'd3;
  localparam int unsigned W = 8;
{body}
endpackage
`default_nettype wire
"""

MOD = """`default_nettype none
`include "common_assertions.svh"
module xy_a_m #(
    parameter int P = 1{params}
) (
    input  logic       clk_i,
    input  logic [7:0] d_i,
    output logic [7:0] q_o
);
  localparam xy_a_pkg::t_cfg L = xy_a_pkg::CFG;
  localparam int unsigned N = 4;
  genvar i;
{body}
endmodule
`default_nettype wire
"""


def selects(text: str, extra: tuple[str, ...] = ()):
    texts = [strip_comments_and_strings(t) for t in (text, *extra)]
    table = struct_params.collect(texts)
    return [(sel, ctx) for _, sel, ctx in struct_params.struct_param_selects(texts[0], table)]


def pkg(body: str) -> str:
    return PKG.replace("{body}", body)


def mod(body: str, params: str = "") -> str:
    return MOD.replace("{body}", body).replace("{params}", params)


def test_symbol_table():
    t = struct_params.collect([strip_comments_and_strings(pkg(""))])
    assert {"xy_a_pkg::t_cfg", "xy_a_pkg::t_cfg_alias"} <= t.types
    assert "xy_a_pkg::t_byte" not in t.types  # a width alias is not a struct
    assert t.params == {"xy_a_pkg::CFG", "xy_a_pkg::ALT", "xy_a_pkg::ONE", "xy_a_pkg::TWO"}


# --- positive cases: one per constant context ---------------------------------

@pytest.mark.parametrize("body, found", [
    # localparam initialisers, as #413 hit them (a two-pass build)
    ("  localparam int unsigned B = CFG.m;", [("CFG.m", "initialiser")]),
    ("  localparam t_cfg C = f(CFG.n, ALT.m);",
     [("CFG.n", "initialiser"), ("ALT.m", "initialiser")]),
    ("  localparam t_byte D = TWO.n;", [("TWO.n", "initialiser")]),   # second declarator
    ("  parameter int unsigned E = CFG . n;", [("CFG.n", "initialiser")]),
    ("  localparam int unsigned F = xy_a_pkg::CFG.n;", [("xy_a_pkg::CFG.n", "initialiser")]),
    # a range or width inside the package
    ("  typedef logic [CFG.n-1:0] t_w;", [("CFG.n", "range")]),
])
def test_package_contexts_fire(body, found):
    assert selects(pkg(body)) == found


@pytest.mark.parametrize("body, found", [
    ("  localparam int unsigned B = L.m;", [("L.m", "initialiser")]),
    ("  localparam int unsigned B = xy_a_pkg::CFG.m;", [("xy_a_pkg::CFG.m", "initialiser")]),
    ('  `COMMON_ASSERT_PARAM(g_c, L.n != 4\'d0, "no length")',
     [("L.n", "COMMON_ASSERT_PARAM")]),
    ('  `COMMON_ASSERT_PARAM(g_c, xy_a_pkg::CFG.n <= 4\'(N), "too long")',
     [("xy_a_pkg::CFG.n", "COMMON_ASSERT_PARAM")]),
    ("  generate\n    if (L.n > 4'd1) begin : g_x\n      assign q_o = d_i;\n    end\n  endgenerate",
     [("L.n", "generate condition")]),
    ("  generate\n    for (i = 0; i < L.n; i++) begin : g_f\n    end\n  endgenerate",
     [("L.n", "generate condition")]),
    ("  generate\n    case (L.m)\n      default: begin : g_d\n      end\n    endcase\n"
     "  endgenerate", [("L.m", "generate condition")]),
    ("  generate\n    if (P > 0) begin : g_o\n      if (L.m == 4'd2) begin : g_i\n      end\n"
     "    end\n  endgenerate", [("L.m", "generate condition")]),       # nested generate
    ("  logic [L.n-1:0] w;", [("L.n", "range")]),
    ("  assign q_o = d_i[L.n +: 4];", [("L.n", "range")]),
    ("  assign q_o = {d_i[7:L.m], 2'b00};", [("L.m", "range")]),
    ("  assign q_o = {L.n{2'b01}};", [("L.n", "replication")]),
    ("  assign q_o = 8'((L.n)'(d_i));", [("L.n", "size cast")]),
    ("  assign q_o = 8'(L.n'(d_i));", [("L.n", "size cast")]),      # unparenthesised
    ("  assign q_o = 8'(xy_a_pkg::CFG . m '(d_i));", [("xy_a_pkg::CFG.m", "size cast")]),
    # an instance's parameter values
    ("  xy_a_sub #(.W(L.n), .D(4)) u_sub (.d_i(d_i), .q_o(q_o));",
     [("L.n", "parameter value")]),
    ("  xy_a_sub #(\n    .W (xy_a_pkg::CFG.m)\n  ) u_sub (.d_i(d_i));",
     [("xy_a_pkg::CFG.m", "parameter value")]),
    # unpacked dimensions and sizes without a ":"
    ("  logic [7:0] mem [L.n];", [("L.n", "dimension")]),
    ("  logic flags [4][L.m];", [("L.m", "dimension")]),
    ("  localparam int unsigned T [L.n] = '{default: 0};", [("L.n", "dimension")]),
    ("  xy_a_pkg::t_cfg tab [xy_a_pkg::CFG.n];", [("xy_a_pkg::CFG.n", "dimension")]),
    # generate case item labels
    ("  generate\n    case (P)\n      L.n: begin : g_a\n      end\n"
     "      xy_a_pkg::CFG.m, 2: begin : g_b\n      end\n      default: begin : g_d\n"
     "      end\n    endcase\n  endgenerate",
     [("L.n", "generate case label"), ("xy_a_pkg::CFG.m", "generate case label")]),
    ("  generate\n    case (P)\n      1: assign q_o = d_i;\n      L.m: assign q_o = '0;\n"
     "    endcase\n  endgenerate", [("L.m", "generate case label")]),
])
def test_module_contexts_fire(body, found):
    assert selects(mod(body), (pkg(""),)) == found


def test_parameter_port_initialiser_fires():
    text = mod("", ",\n    parameter xy_a_pkg::t_cfg C2 = xy_a_pkg::CFG,\n"
                   "    parameter int unsigned M = C2.m")
    assert selects(text, (pkg(""),)) == [("C2.m", "initialiser")]


# --- negative cases -----------------------------------------------------------

@pytest.mark.parametrize("body", [
    # a local variable or a function argument selected inside a function
    "  function automatic t_cfg f(input t_cfg c);\n    t_cfg l;\n    l = c;\n"
    "    l.n = c.m;\n    return l;\n  endfunction\n  localparam t_cfg G = f(CFG);",
    # an argument named like a struct parameter shadows it, even in a range
    "  function automatic t_byte g(input t_cfg CFG);\n    logic [CFG.n-1:0] v;\n"
    "    v = '0;\n    return t_byte'(v);\n  endfunction",
    # a local declared like a struct parameter shadows it
    "  function automatic t_byte h();\n    t_cfg ALT;\n    ALT = '0;\n"
    "    return t_byte'({ALT.n{1'b1}});\n  endfunction",
    # a part-select of the packed struct
    "  localparam logic [3:0] PN = CFG[3:0];\n  localparam logic [3:0] PM = CFG[7:4];",
    # a select of a parameter that is not a struct
    "  localparam int unsigned Q = PLAIN[2:0];",
    # a select of a struct parameter in ordinary logic inside a function
    "  function automatic logic [3:0] k();\n    return CFG.n;\n  endfunction",
])
def test_package_negatives_pass(body):
    assert selects(pkg(body)) == []


@pytest.mark.parametrize("body", [
    # ordinary logic: Quartus accepts a member select there
    "  assign q_o = {4'd0, L.n};",
    "  always_comb begin\n    q_o = d_i;\n    if (L.n != 4'd0) q_o = '0;\n"
    "    for (int j = 0; j < 8; j++) q_o[j] = xy_a_pkg::CFG.m[0];\n  end",
    # a struct-typed signal, not a parameter
    "  xy_a_pkg::t_cfg s;\n  assign s = L;\n  logic [7:0] w;\n  assign w = d_i[s.n:0];",
    # a part-select of the packed struct
    "  localparam logic [3:0] LN = L[3:0];",
    # an assignment pattern key is not a select
    "  localparam xy_a_pkg::t_cfg K = '{n: 4'd1, m: 4'd1};",
    # a conditional index is not a range, and a package item is not a ":"
    "  assign q_o = {7'd0, d_i[P > 0 ? L.n : 4'd0]};",
    "  assign q_o = {7'd0, d_i[xy_a_pkg::W - L.n]};",
    # an index in ordinary logic is not a dimension
    "  logic [7:0] w;\n  assign w[L.n] = 1'b0;\n  always_comb begin\n    q_o = w;\n"
    "    q_o[L.m] = d_i[L.n];\n  end",
    # a procedural case label is ordinary logic
    "  always_comb begin\n    q_o = d_i;\n    case (d_i[3:0])\n      L.n: q_o = '0;\n"
    "      default: ;\n    endcase\n  end",
])
def test_module_negatives_pass(body):
    assert selects(mod(body), (pkg(""),)) == []


CLOSED = "; its type is not resolved, so the rule fails closed"


def test_unresolved_types_fail_closed():
    # Without the package's source, L's type xy_a_pkg::t_cfg is unknown: the
    # rule fails closed and says so.
    assert selects(mod("  localparam int unsigned B = L.m;")) == [("L.m", "initialiser" + CLOSED)]
    # a package the table has not seen
    assert selects(mod("  localparam int unsigned B = xy_b_pkg::X.m;"), (pkg(""),)) == [
        ("xy_b_pkg::X.m", "initialiser" + CLOSED)]
    # a seen package's name that is not a parameter (a nonsense select) passes
    assert selects(mod("  localparam int unsigned B = xy_a_pkg::t_cfg.m;"), (pkg(""),)) == []
    # an untyped parameter, or one of a type no package declares
    assert selects(pkg("  localparam U = CFG;\n  localparam int unsigned B = U.n;")) == [
        ("U.n", "initialiser" + CLOSED)]
    assert selects(pkg("  localparam t_none V = CFG;\n  typedef logic [V.n:0] t_v;")) == [
        ("V.n", "range" + CLOSED)]
    # a known non-struct type, a built-in type or an array of a named type
    assert selects(pkg("  localparam int unsigned B = PLAIN.n + W.n;")) == []
    assert selects(pkg("  localparam t_cfg [1:0] AR = '0;\n  localparam int B = AR.n;")) == []


# --- the style rule -----------------------------------------------------------

def test_check_text_reports_the_rule():
    text = pkg("  localparam int unsigned B = CFG.m;")
    found = style.check_text("xy_a_pkg.sv", text, "xy_a")
    assert found == ["xy_a_pkg.sv:14: struct-param-select: member select CFG.m of a "
                     "struct-typed parameter in a constant expression (initialiser); select "
                     "inside a function from a local or an argument, or part-select the "
                     "packed struct"], found


def test_run_uses_every_package_even_with_an_ip_filter(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "good/src", src)
    p = src / "rtl/blk/xy_blk_pkg.sv"
    p.write_text(p.read_text().replace(
        "  typedef logic [W-1:0] t_count;",
        "  typedef logic [W-1:0] t_count;\n"
        "  typedef struct packed {\n    logic [3:0] n;\n  } t_cfg;\n"
        "  localparam t_cfg CFG = '{n: 4'd8};"))
    assert style.run(src, discover(src), None).passed
    s = src / "rtl/blk_sub/xy_blk_sub.sv"
    s.write_text(s.read_text().replace(
        "  assign y_o", "  localparam int unsigned N = xy_blk_pkg::CFG.n;\n  assign y_o"))
    for ids in (None, {"blk_sub"}):
        r = style.run(src, discover(src), ids)
        assert [f.split(": ")[1] for f in r.findings] == ["struct-param-select"], r.findings
        assert r.findings[0].startswith("rtl/blk_sub/xy_blk_sub.sv:10:")
