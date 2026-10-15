"""Tests for the structural clock-domain-crossing check (checks/cdc.py).

Each case builds a small tree in a temporary directory: an IP ``blk`` (top
``xy_blk``) next to a copy of the repository's shared crossing cells, so the
legal crossings run through the real cells.
"""

import shutil
from pathlib import Path

import pytest

from build import sim
from build.ip import REPO_ROOT, discover, shared_blocks
from checks import cdc

needs_verilator = pytest.mark.skipif(sim.find_verilator() is None,
                                     reason="verilator not available")

CELLS = REPO_ROOT / "src/rtl/common"
# Crossing cells in the copied tree, each a shared block the check analyzes.
N_CELLS = len([f for f in shared_blocks(REPO_ROOT / "src") if f.name.startswith("common_cdc_")])
# Shared blocks the check analyzes: those whose filelist lists a module
# source (not the include-only vocabulary block or a package-only block).
N_BLOCKS = sum(1 for f in shared_blocks(REPO_ROOT / "src")
               if any(ln.strip().endswith(".sv") and not ln.strip().endswith("_pkg.sv")
                      for ln in f.read_text().splitlines()))

HEAD = """`default_nettype none
module xy_blk (
  input  logic clk_a_i,
  input  logic clk_b_i,
  input  logic rst_a_i,  // cdc-domain: clk_a_i
  input  logic rst_b_i,  // cdc-domain: clk_b_i
  input  logic x_i,  // cdc-domain: clk_a_i
  output logic y_o
);
"""
TAIL = """
endmodule
`default_nettype wire
"""
# A flop in each domain: a_q on clk_a_i, b_q on clk_b_i.
A_FLOP = """
  logic a_q;
  always_ff @(posedge clk_a_i) a_q <= x_i;
"""


def tree(tmp_path: Path, body: str, extra: dict[str, str] | None = None) -> Path:
    src = tmp_path / "src"
    (src / "rtl/blk").mkdir(parents=True)
    (src / "README.md").write_text("| Module/package/file prefix | `xy_` |\n")
    shutil.copytree(CELLS, src / "rtl/common",
                    ignore=shutil.ignore_patterns("*.md"))
    (src / "rtl/blk/xy_blk.sv").write_text(HEAD + body + TAIL)
    lines = ["-F ../common/cdc/common_cdc_sync2.rtl.f", "-F ../common/cdc/common_cdc_pulse.rtl.f",
             "-F ../common/cdc/common_cdc_fifo.rtl.f", "-F ../common/cdc/common_cdc_rst.rtl.f"]
    for name, text in (extra or {}).items():
        (src / "rtl/blk" / name).write_text(text)
        lines.append(name)
    (src / "rtl/blk/blk.rtl.f").write_text("\n".join(lines + ["xy_blk.sv"]) + "\n")
    return src


def run(src: Path, ip_ids=None):
    return cdc.run(src, discover(src), ip_ids)


def blk(result) -> list[str]:
    return [f for f in result.findings if f.startswith("blk: ")]


# ---------------------------------------------------------------- passes


@needs_verilator
def test_single_clock_passes(tmp_path):
    body = A_FLOP + """
  logic c_q;
  always_ff @(posedge clk_a_i) begin
    if (rst_a_i) c_q <= 1'b0;
    else         c_q <= c_q ^ a_q;
  end
  assign y_o = c_q;
"""
    r = run(tree(tmp_path, body))
    assert r.passed, r.findings
    assert (f"1 top-level IP(s), {N_BLOCKS} shared block(s), {N_BLOCKS + 1} Verilator "
            "--json-only run(s)") in r.checked
    assert f"{N_CELLS} cdc-cell directive(s)" in r.checked


SYNC2 = A_FLOP + """
  logic s;
  logic b_q;
  common_cdc_sync2 u_sync (
    .dst_clk_i (clk_b_i),
    .dst_rst_i (rst_b_i),
    .src_data_i(a_q),
    .dst_data_o(s)
  );
  always_ff @(posedge clk_b_i) b_q <= s;
  assign y_o = b_q;
"""

PULSE = A_FLOP + """
  logic p;
  logic b_q;
  common_cdc_pulse u_pulse (
    .src_clk_i  (clk_a_i),
    .src_rst_i  (rst_a_i),
    .src_pulse_i(a_q & x_i),  // logic is fine: the cell samples it on src_clk_i
    .dst_clk_i  (clk_b_i),
    .dst_rst_i  (rst_b_i),
    .dst_pulse_o(p)
  );
  always_ff @(posedge clk_b_i) b_q <= p;
  assign y_o = b_q;
"""

FIFO = A_FLOP + """
  logic       ready;
  logic       valid;
  logic [7:0] data;
  logic [7:0] b_q;
  logic [7:0] a_data_q;
  always_ff @(posedge clk_a_i) if (ready) a_data_q <= a_data_q + 8'd1;
  common_cdc_fifo #(.WIDTH(8), .ADDR_W(2)) u_fifo (
    .src_clk_i  (clk_a_i),
    .src_rst_i  (rst_a_i),
    .src_valid_i(a_q),
    .src_ready_o(ready),
    .src_data_i (a_data_q),
    .dst_clk_i  (clk_b_i),
    .dst_rst_i  (rst_b_i),
    .dst_valid_o(valid),
    .dst_ready_i(1'b1),
    .dst_data_o (data)
  );
  always_ff @(posedge clk_b_i) if (valid) b_q <= data;
  assign y_o = ^b_q;
"""


@needs_verilator
@pytest.mark.parametrize("body", [SYNC2, PULSE, FIFO], ids=["sync2", "pulse", "fifo"])
def test_legal_crossing_through_each_cell_passes(tmp_path, body):
    r = run(tree(tmp_path, body))
    assert r.passed, r.findings


@needs_verilator
def test_async_input_through_plain_wires_passes(tmp_path):
    body = SYNC2.replace(".src_data_i(a_q)", ".src_data_i(w)").replace(
        "  logic s;", "  logic s;\n  logic w;\n  assign w = a_q;")
    r = run(tree(tmp_path, body))
    assert r.passed, r.findings


@needs_verilator
def test_async_reset_flop_clock_is_found(tmp_path):
    body = """
  logic a_q;
  always_ff @(posedge clk_a_i or posedge rst_a_i) begin
    if (rst_a_i) a_q <= 1'b0;
    else         a_q <= x_i;
  end
  assign y_o = a_q;
"""
    assert run(tree(tmp_path, body)).passed


# A reset launched by a clk_a_i flop, and a clk_b_i flop with an
# asynchronous reset: through the reset synchronizer it passes, taken
# directly it is a crossing.
RST_SYNC = """
  logic rst_a_q;
  logic rst_b;
  logic b_q;
  always_ff @(posedge clk_a_i) rst_a_q <= rst_a_i;
  common_cdc_rst u_rst (
    .dst_clk_i(clk_b_i),
    .src_rst_i(rst_a_q),
    .dst_rst_o(rst_b)
  );
  always_ff @(posedge clk_b_i or posedge rst_b) begin
    if (rst_b) b_q <= 1'b0;
    else       b_q <= ~b_q;
  end
  assign y_o = b_q;
"""


@needs_verilator
def test_reset_through_the_reset_synchronizer_passes(tmp_path):
    r = run(tree(tmp_path, RST_SYNC))
    assert r.passed, r.findings


@needs_verilator
def test_reset_taken_directly_from_another_domain_fails(tmp_path):
    body = RST_SYNC.replace("posedge rst_b) begin\n    if (rst_b)",
                            "posedge rst_a_q) begin\n    if (rst_a_q)")
    assert body != RST_SYNC
    [f] = blk(run(tree(tmp_path, body)))
    assert f.startswith("blk: flop b_q (clock clk_b_i") and "flop rst_a_q (clock clk_a_i" in f


@needs_verilator
def test_logic_in_front_of_the_reset_synchronizer_fails(tmp_path):
    body = RST_SYNC.replace(".src_rst_i(rst_a_q)", ".src_rst_i(rst_a_q | rst_a_i)")
    [f] = blk(run(tree(tmp_path, body)))
    assert "cell input u_rst.src_rst_i" in f and "must come straight from a flop" in f


# ---------------------------------------------------------------- crossings


@needs_verilator
def test_raw_crossing_names_both_flops_and_clocks(tmp_path):
    body = A_FLOP + """
  logic b_q;
  always_ff @(posedge clk_b_i) b_q <= a_q;
  assign y_o = b_q;
"""
    r = run(tree(tmp_path, body))
    assert not r.passed
    [f] = blk(r)
    assert f.startswith("blk: flop b_q (clock clk_b_i, rtl/blk/xy_blk.sv:")
    assert "samples flop a_q (clock clk_a_i, rtl/blk/xy_blk.sv:" in f
    assert "directly" in f


@needs_verilator
def test_crossing_through_logic_fails(tmp_path):
    body = A_FLOP + """
  logic b_q;
  logic mix;
  always_comb begin
    mix = 1'b0;
    if (x_i) mix = a_q | b_q;
  end
  always_ff @(posedge clk_b_i) begin
    if (rst_b_i) b_q <= 1'b0;
    else         b_q <= mix;
  end
  assign y_o = b_q;
"""
    [f] = blk(run(tree(tmp_path, body)))  # a_q and x_i: one finding for clk_a_i
    assert "flop b_q (clock clk_b_i" in f and "(clock clk_a_i" in f
    assert "through combinational logic" in f


@needs_verilator
def test_crossing_through_hierarchy_fails(tmp_path):
    sub = """`default_nettype none
module xy_blk_reg (
  input  logic c_i,
  input  logic d_i,
  output logic q_o
);
  always_ff @(posedge c_i) q_o <= d_i;
endmodule
`default_nettype wire
"""
    body = A_FLOP + """
  logic m;
  logic n;
  xy_blk_reg u_a (.c_i(clk_a_i), .d_i(a_q), .q_o(m));
  xy_blk_reg u_b (.c_i(clk_b_i), .d_i(m),   .q_o(n));
  assign y_o = n;
"""
    [f] = blk(run(tree(tmp_path, body, {"xy_blk_reg.sv": sub})))
    assert "flop u_b.q_o (clock clk_b_i" in f and "flop u_a.q_o (clock clk_a_i" in f


@needs_verilator
def test_cell_output_used_in_wrong_domain_fails(tmp_path):
    body = SYNC2.replace("always_ff @(posedge clk_b_i) b_q <= s;",
                         "always_ff @(posedge clk_a_i) b_q <= s;")
    [f] = blk(run(tree(tmp_path, body)))
    assert "flop b_q (clock clk_a_i" in f
    assert "cell output u_sync.dst_data_o (clock clk_b_i" in f


@needs_verilator
def test_clocked_cell_input_from_wrong_domain_fails(tmp_path):
    body = PULSE.replace("a_q & x_i", "b_q")
    [f] = blk(run(tree(tmp_path, body)))
    assert f.startswith("blk: cell input u_pulse.src_pulse_i (clock clk_a_i")
    assert "flop b_q (clock clk_b_i" in f


@needs_verilator
def test_generated_clock_is_its_own_domain(tmp_path):
    body = A_FLOP + """
  logic div_q;
  logic g_q;
  always_ff @(posedge clk_a_i) div_q <= ~div_q;
  always_ff @(posedge div_q) g_q <= a_q;
  assign y_o = g_q;
"""
    [f] = blk(run(tree(tmp_path, body)))
    assert "flop g_q (clock div_q (generated)" in f and "(clock clk_a_i" in f


@needs_verilator
def test_clock_gate_is_a_derived_domain(tmp_path):
    icg = """`default_nettype none
module xy_blk_icg (
  input  logic clk_i,
  input  logic en_i,
  output logic gclk_o
);
  logic en_l;
  always_latch if (!clk_i) en_l = en_i;
  assign gclk_o = clk_i & en_l;
endmodule
`default_nettype wire
"""
    body = A_FLOP + """
  logic gclk;
  logic g_q;
  xy_blk_icg u_icg (.clk_i(clk_a_i), .en_i(a_q), .gclk_o(gclk));
  always_ff @(posedge gclk) g_q <= a_q;
  assign y_o = g_q;
"""
    [f] = blk(run(tree(tmp_path, body, {"xy_blk_icg.sv": icg})))
    assert "flop g_q (clock u_icg.gclk_o (derived)" in f and "flop a_q (clock clk_a_i" in f


@needs_verilator
def test_generate_loop_findings_are_reported_once(tmp_path):
    body = A_FLOP + """
  logic [3:0] b_v;
  for (genvar i = 0; i < 4; i++) begin : g
    always_ff @(posedge clk_b_i) b_v[i] <= a_q;
  end
  assign y_o = ^b_v;
"""
    [f] = blk(run(tree(tmp_path, body)))
    assert f.startswith("blk: flop b_v (clock clk_b_i") and "flop a_q (clock clk_a_i" in f


@needs_verilator
def test_one_finding_per_source_domain(tmp_path):
    body = A_FLOP + """
  logic a2_q;
  logic b_q;
  always_ff @(posedge clk_a_i) a2_q <= ~x_i;
  always_ff @(posedge clk_b_i) b_q <= a_q ^ a2_q;
  assign y_o = b_q;
"""
    [f] = blk(run(tree(tmp_path, body)))
    assert "flop b_q (clock clk_b_i" in f and "(clock clk_a_i" in f
    # The other source of that domain is listed too.
    named = f.split("; also from clk_a_i: ")
    assert len(named) == 2
    assert ("flop a2_q" in named[0]) != ("flop a2_q (rtl/blk/xy_blk.sv:" in named[1])
    assert ("flop a_q" in named[0]) != ("flop a_q (rtl/blk/xy_blk.sv:" in named[1])


@needs_verilator
def test_co_sources_are_capped(tmp_path):
    body = A_FLOP + """
  logic [7:0] v_q;
  logic b_q;
  for (genvar i = 0; i < 8; i++) begin : g
    logic r;
    always_ff @(posedge clk_a_i) r <= x_i;
    assign v_q[i] = r;
  end
  always_ff @(posedge clk_b_i) b_q <= ^v_q;
  assign y_o = b_q;
"""
    [f] = blk(run(tree(tmp_path, body)))
    also = f.split("; also from clk_a_i: ")[1]
    assert also.count("flop g[") == cdc.CO_SOURCES and also.endswith(", and more")


# ---------------------------------------------------------------- port domains

ANNOTATED = "  input  logic x_i,  // cdc-domain: clk_a_i\n"
BARE = "  input  logic x_i,\n"


def annotated(tmp_path: Path, body: str, line: str = ANNOTATED) -> Path:
    src = tree(tmp_path, body)
    top = src / "rtl/blk/xy_blk.sv"
    top.write_text(top.read_text().replace(ANNOTATED, line))
    return src


B_SAMPLES_X = """
  logic b_q;
  always_ff @(posedge clk_b_i) b_q <= x_i;
  assign y_o = b_q;
"""


@needs_verilator
def test_annotated_port_sampled_in_another_domain_fails(tmp_path):
    r = run(annotated(tmp_path, B_SAMPLES_X))
    [f] = blk(r)
    assert f.startswith("blk: flop b_q (clock clk_b_i, rtl/blk/xy_blk.sv:")
    assert "samples input port x_i (clock clk_a_i, rtl/blk/xy_blk.sv:7) directly" in f
    assert "3 cdc-domain annotation(s)" in r.checked


@needs_verilator
def test_unannotated_port_has_no_domain_with_one_clock(tmp_path):
    assert run(annotated(tmp_path, B_SAMPLES_X, BARE)).passed


@needs_verilator
def test_unannotated_input_with_two_clocks_fails(tmp_path):
    src = annotated(tmp_path, SYNC2, BARE)
    top = src / "rtl/blk/xy_blk.sv"
    top.write_text(top.read_text().replace("rst_b_i,  // cdc-domain: clk_b_i", "rst_b_i,"))
    assert blk(run(src)) == [
        f"blk: input port {p} (rtl/blk/xy_blk.sv:{n}) has no cdc-domain annotation, and the "
        "top has 2 clock inputs (clk_a_i, clk_b_i)" for p, n in (("rst_b_i", 6), ("x_i", 7))]


@needs_verilator
def test_inout_port_needs_no_annotation(tmp_path):
    src = tree(tmp_path, SYNC2)
    top = src / "rtl/blk/xy_blk.sv"
    top.write_text(top.read_text().replace("  output logic y_o\n",
                                           "  inout  wire  io_b,\n  output logic y_o\n"))
    r = run(src)
    assert r.passed, r.findings


# rst_a_i is annotated with clk_a_i; a clk_b_i flop with an asynchronous
# reset takes it through the reset synchronizer (the cell or its macro), or
# directly.
RST_PORT = """
  logic rst_b;
  logic b_q;
  RESET
  always_ff @(posedge clk_b_i or posedge RST) begin
    if (RST) b_q <= 1'b0;
    else     b_q <= ~b_q;
  end
  assign y_o = b_q;
"""
RST_CELL = "common_cdc_rst u_rst (.dst_clk_i(clk_b_i), .src_rst_i(rst_a_i), .dst_rst_o(rst_b));"
RST_MACRO = "`COMMON_RESET_SYNC(u_rst, rst_b, rst_a_i, clk_b_i)"


def rst_port(tmp_path: Path, reset: str, rst: str) -> Path:
    src = tree(tmp_path, RST_PORT.replace("RESET", reset).replace("RST", rst))
    top = src / "rtl/blk/xy_blk.sv"
    top.write_text(top.read_text().replace("`default_nettype none\n",
                                           "`default_nettype none\n`include \"common_macros.svh\"\n", 1))
    rtl_f = src / "rtl/blk/blk.rtl.f"
    rtl_f.write_text("-F ../common/common_macros.rtl.f\n" + rtl_f.read_text())
    return src


@needs_verilator
@pytest.mark.parametrize("reset", [RST_CELL, RST_MACRO], ids=["cell", "macro"])
def test_annotated_reset_port_through_the_reset_synchronizer_passes(tmp_path, reset):
    r = run(rst_port(tmp_path, reset, "rst_b"))
    assert r.passed, r.findings


@needs_verilator
def test_annotated_reset_port_taken_directly_fails(tmp_path):
    [f] = blk(run(rst_port(tmp_path, "", "rst_a_i")))
    assert f.startswith("blk: flop b_q (clock clk_b_i") and "input port rst_a_i" in f, f


@needs_verilator
@pytest.mark.parametrize("body", [
    SYNC2,
    SYNC2.replace(".src_data_i(a_q)", ".src_data_i(x_i)"),
    PULSE,
], ids=["own-domain-flop", "async-cell-input", "clocked-cell-input"])
def test_annotated_port_through_a_cell_passes(tmp_path, body):
    r = run(annotated(tmp_path, body))
    assert r.passed, r.findings


@needs_verilator
def test_annotated_port_into_wrong_side_of_a_cell_fails(tmp_path):
    [f] = blk(run(annotated(tmp_path, PULSE.replace("a_q & x_i", "a_q").replace(
        ".dst_rst_i  (rst_b_i)", ".dst_rst_i  (rst_b_i | x_i)"))))
    assert f.startswith("blk: cell input u_pulse.dst_rst_i (clock clk_b_i")
    assert "input port x_i (clock clk_a_i" in f and "through combinational logic" in f


def test_scan_port_domains(tmp_path):
    src = tmp_path / "src"
    (src / "rtl/blk").mkdir(parents=True)
    f = src / "rtl/blk/xy_blk.sv"
    f.write_text("module xy_blk (\n"
                 "  input  logic clk_i,\n"
                 "  input  logic [W/2-1:0] d_i,  // cdc-domain: clk_i\n"
                 "  input  logic e_i,  // cdc-domain clk_i\n"
                 "  output logic q_o   // cdc-domain: clk_i\n"
                 ");\n"
                 "  // A prose mention of a cdc-domain annotation is not one.\n"
                 "endmodule\n")
    found, findings = cdc.scan_port_domains([f], src)
    assert list(found.values()) == [cdc.PortDomain("clk_i", f.resolve(), 3)]
    assert findings == [
        "rtl/blk/xy_blk.sv:4: malformed cdc-domain annotation (expected "
        "'// cdc-domain: <clock input port>' ending the line)",
        "rtl/blk/xy_blk.sv:5: cdc-domain annotation is not on an input port declaration line",
    ]


@needs_verilator
@pytest.mark.parametrize("line, msg", [
    ("  input  logic x_i,  // cdc-domain: y_o\n", "y_o is not an input port of xy_blk"),
    ("  input  logic x_i,  // cdc-domain: clk_c_i\n", "clk_c_i is not an input port of xy_blk"),
    ("  input  logic x_i,  // cdc-domain: x_i\n", "names the port itself"),
], ids=["output", "missing", "itself"])
def test_annotation_must_name_another_input(tmp_path, line, msg):
    r = run(annotated(tmp_path, B_SAMPLES_X, line))
    assert r.findings == [f"blk: rtl/blk/xy_blk.sv:7: cdc-domain annotation on xy_blk.x_i: {msg}"]


@needs_verilator
def test_annotation_on_a_submodule_port_is_checked_but_not_applied(tmp_path):
    sub = """`default_nettype none
module xy_blk_reg (
  input  logic c_i,
  input  logic d_i,  // cdc-domain: c_i
  input  logic e_i,  // cdc-domain: f_i
  output logic q_o
);
  always_ff @(posedge c_i) q_o <= d_i ^ e_i;
endmodule
`default_nettype wire
"""
    body = A_FLOP + """
  logic m;
  xy_blk_reg u_a (.c_i(clk_a_i), .d_i(x_i), .e_i(x_i), .q_o(m));
  assign y_o = m;
"""
    r = run(tree(tmp_path, body, {"xy_blk_reg.sv": sub}))
    assert r.findings == ["blk: rtl/blk/xy_blk_reg.sv:5: cdc-domain annotation on "
                          "xy_blk_reg.e_i: f_i is not an input port of xy_blk_reg"]


@needs_verilator
def test_annotation_outside_an_analyzed_port_fails(tmp_path):
    fn = """`default_nettype none
module xy_blk_fn;
  function automatic logic inv(
    input logic v  // cdc-domain: clk_i
  );
    return ~v;
  endfunction
endmodule
`default_nettype wire
"""
    r = run(tree(tmp_path, A_FLOP + "  assign y_o = a_q;\n", {"xy_blk_fn.sv": fn}))
    assert r.findings == ["rtl/blk/xy_blk_fn.sv:4: cdc-domain annotation does not annotate "
                          "an input port of an analyzed module"]


# ---------------------------------------------------------------- src=async rule


@needs_verilator
@pytest.mark.parametrize("conn, extra, at", [
    ("a_q ^ x_i", "", "its connection expression"),
    ("w", "  logic w;\n  assign w = a_q & x_i;\n", "at w"),
    ("w", "  logic w;\n  logic v;\n  assign v = ~a_q;\n  assign w = v;\n", "at v"),
], ids=["in-pin", "assign", "two-levels"])
def test_logic_in_front_of_async_input_fails(tmp_path, conn, extra, at):
    body = SYNC2.replace(".src_data_i(a_q)", f".src_data_i({conn})").replace(
        "  logic s;\n", "  logic s;\n" + extra)
    [f] = blk(run(tree(tmp_path, body)))
    assert f.startswith("blk: cell input u_sync.src_data_i (rtl/blk/xy_blk.sv:")
    assert "combinational logic between it and its launching flop" in f and at in f


# ---------------------------------------------------------------- directives


def test_scan_directives(tmp_path):
    src = tmp_path / "src"
    common = src / "rtl/common"
    common.mkdir(parents=True)
    (src / "rtl/blk").mkdir()
    good = common / "common_cdc_ok.sv"
    good.write_text("// cdc-cell: src=src_clk_i dst=dst_clk_i\nmodule common_cdc_ok;\nendmodule\n")
    bad = common / "common_cdc_bad.sv"
    bad.write_text("// cdc-cell: src=async\nmodule common_cdc_bad;\nendmodule\n"
                   "// cdc-cell: src=async dst=dst_clk_i\n\nmodule common_cdc_gap;\nendmodule\n"
                   "// cdc-cell: src=x_i dst=x_i\nmodule common_cdc_same;\nendmodule\n"
                   "module common_cdc_none;\nendmodule\n"
                   "// A prose mention of a cdc-cell directive is not one.\n"
                   "// cdc-cell: src=async dst=dst_clk_i\nmodule common_other;\nendmodule\n")
    ip = src / "rtl/blk/xy_blk.sv"
    ip.write_text("// cdc-cell: src=async dst=dst_clk_i\nmodule common_cdc_mine;\nendmodule\n")
    # A group folder one level down holds cells too; a deeper folder does not.
    (common / "grp/deep").mkdir(parents=True)
    grouped = common / "grp/common_cdc_grouped.sv"
    grouped.write_text("// cdc-cell: src=async dst=dst_clk_i\nmodule common_cdc_grouped;\n"
                       "endmodule\n")
    deep = common / "grp/deep/common_cdc_deep.sv"
    deep.write_text("// cdc-cell: src=async dst=dst_clk_i\nmodule common_cdc_deep;\nendmodule\n")
    found, findings = cdc.scan_directives([good, bad, ip, grouped, deep], src)
    assert list(found.values()) == [
        cdc.Directive("common_cdc_ok", "src_clk_i", "dst_clk_i", good.resolve(), 1),
        cdc.Directive("common_cdc_grouped", "async", "dst_clk_i", grouped.resolve(), 1)]
    assert findings == [
        "rtl/common/common_cdc_bad.sv:1: malformed cdc-cell directive (expected "
        "'// cdc-cell: src=<clock port|async> dst=<clock port>')",
        "rtl/common/common_cdc_bad.sv:4: cdc-cell directive is not on the line directly "
        "above a module line",
        "rtl/common/common_cdc_bad.sv:8: malformed cdc-cell directive on common_cdc_same: "
        "src and dst name the same port",
        "rtl/common/common_cdc_bad.sv:14: cdc-cell directive on common_other: only "
        "common_cdc_* modules under rtl/common/ carry one",
        "rtl/common/common_cdc_bad.sv:6: crossing cell common_cdc_gap has no cdc-cell "
        "directive on the line above its module line",
        "rtl/common/common_cdc_bad.sv:11: crossing cell common_cdc_none has no cdc-cell "
        "directive on the line above its module line",
        "rtl/blk/xy_blk.sv:1: cdc-cell directive on common_cdc_mine: only common_cdc_* "
        "modules under rtl/common/ carry one",
        "rtl/common/grp/deep/common_cdc_deep.sv:1: cdc-cell directive on common_cdc_deep: "
        "only common_cdc_* modules under rtl/common/ carry one",
    ]


@needs_verilator
def test_missing_directive_fails_and_the_cell_is_not_approved(tmp_path):
    src = tree(tmp_path, SYNC2)
    cell = src / "rtl/common/cdc/common_cdc_sync2.sv"
    cell.write_text(cell.read_text().replace("// cdc-cell: src=async dst=dst_clk_i\n", ""))
    r = run(src)
    assert any("crossing cell common_cdc_sync2 has no cdc-cell directive" in f
               for f in r.findings)
    # Not approved, so its first flop is a raw crossing from a_q.
    assert any("flop u_sync.meta_q (clock clk_b_i" in f and "flop a_q (clock clk_a_i" in f
               for f in blk(r))


@needs_verilator
def test_malformed_directive_fails(tmp_path):
    src = tree(tmp_path, SYNC2)
    cell = src / "rtl/common/cdc/common_cdc_sync2.sv"
    cell.write_text(cell.read_text().replace("src=async dst=dst_clk_i", "src=async"))
    r = run(src)
    assert any("malformed cdc-cell directive" in f for f in r.findings)
    assert any("u_sync.meta_q" in f for f in blk(r))


@needs_verilator
@pytest.mark.parametrize("old, new, msg", [
    ("dst=dst_clk_i", "dst=dst_rst_i",
     "flop meta_q runs on dst_clk_i, not on a clock its cdc-cell directive names"),
    ("src=async dst=dst_clk_i", "src=src_x_i dst=dst_clk_i", "src=src_x_i is not an input port"),
    ("src=async dst=dst_clk_i", "src=dst_clk_i dst=dst_rst_i",
     "src=dst_clk_i does not name a src_* port"),
], ids=["dst-not-a-clock", "src-missing", "src-wrong-side"])
def test_directive_must_match_ports(tmp_path, old, new, msg):
    src = tree(tmp_path, SYNC2)
    cell = src / "rtl/common/cdc/common_cdc_sync2.sv"
    cell.write_text(cell.read_text().replace(old, new))
    r = run(src)
    assert any(f"crossing cell common_cdc_sync2: {msg}" in f for f in r.findings), r.findings


@needs_verilator
def test_port_prefix_rule(tmp_path):
    src = tree(tmp_path, SYNC2)
    cell = src / "rtl/common/cdc/common_cdc_sync2.sv"
    cell.write_text(cell.read_text().replace("dst_rst_i", "rst_i"))
    body = SYNC2.replace(".dst_rst_i (rst_b_i)", ".rst_i (rst_b_i)")
    (src / "rtl/blk/xy_blk.sv").write_text(HEAD + body + TAIL)
    r = run(src)
    assert any("crossing cell common_cdc_sync2: port rst_i is neither src_* nor dst_*" in f
               for f in r.findings)


def test_port_findings_async_output():
    model = cdc.Module({})
    for a, (n, d) in enumerate([("dst_clk_i", "INPUT"), ("src_data_i", "INPUT"),
                                ("src_ack_o", "OUTPUT"), ("dst_data_o", "OUTPUT")]):
        model.vars[str(a)] = cdc.VarInfo(n, "", d)
    dv = cdc.Directive("common_cdc_x", "async", "dst_clk_i", Path("x"), 1)
    assert cdc.port_findings(model, dv) == ["src=async cell has a source-side output src_ack_o"]


@needs_verilator
def test_pass_through_cell_fails(tmp_path):
    src = tree(tmp_path, A_FLOP + "  assign y_o = a_q;\n")
    common = src / "rtl/common"
    (common / "common_cdc_wire.sv").write_text(
        "`default_nettype none\n"
        "// cdc-cell: src=async dst=dst_clk_i\n"
        "module common_cdc_wire (\n"
        "  input  logic dst_clk_i,\n"
        "  input  logic src_d_i,\n"
        "  input  logic dst_e_i,\n"
        "  output logic dst_q_o\n"
        ");\n"
        "  logic t;\n"
        "  assign t = src_d_i & dst_e_i;\n"
        "  assign dst_q_o = t;\n"
        "endmodule\n"
        "`default_nettype wire\n")
    (common / "common_cdc_wire.rtl.f").write_text("common_cdc_wire.sv\n")
    r = run(src)
    assert r.findings == ["common/common_cdc_wire: rtl/common/common_cdc_wire.sv:2: crossing "
                          "cell common_cdc_wire: output dst_q_o has a combinational path from "
                          "input src_d_i, so the cell would hide a crossing"]


def cell_tree(tmp_path: Path, cell: str, uses: str = "") -> Path:
    src = tree(tmp_path, A_FLOP + "  assign y_o = a_q;\n")
    common = src / "rtl/common"
    (common / "common_cdc_bad.sv").write_text(cell)
    (common / "common_cdc_bad.rtl.f").write_text(uses + "common_cdc_bad.sv\n")
    return src


@needs_verilator
def test_flop_then_wire_cell_fails(tmp_path):
    r = run(cell_tree(tmp_path, """`default_nettype none
// cdc-cell: src=src_clk_i dst=dst_clk_i
module common_cdc_bad (
  input  logic src_clk_i,
  input  logic src_d_i,
  input  logic dst_clk_i,
  output logic dst_q_o
);
  logic r_q;
  always_ff @(posedge src_clk_i) r_q <= src_d_i;
  assign dst_q_o = r_q;
endmodule
`default_nettype wire
"""))
    assert r.findings == ["common/common_cdc_bad: rtl/common/common_cdc_bad.sv:2: crossing "
                          "cell common_cdc_bad: output dst_q_o is driven by flop r_q "
                          "(rtl/common/common_cdc_bad.sv:9) on clock src_clk_i, not by its "
                          "own side's clock dst_clk_i"]


@needs_verilator
@pytest.mark.parametrize("index", ["1'b0", "src_a_q"], ids=["constant", "src-address"])
def test_memory_read_needs_an_address_of_the_reading_side(tmp_path, index):
    r = run(cell_tree(tmp_path, f"""`default_nettype none
// cdc-cell: src=src_clk_i dst=dst_clk_i
module common_cdc_bad (
  input  logic src_clk_i,
  input  logic src_d_i,
  input  logic dst_clk_i,
  output logic dst_q_o
);
  logic mem_q [2];
  logic src_a_q;
  always_ff @(posedge src_clk_i) begin
    src_a_q <= ~src_a_q;
    mem_q[src_a_q] <= src_d_i;
  end
  assign dst_q_o = mem_q[{index}];
endmodule
`default_nettype wire
"""))
    assert any("output dst_q_o is driven by flop mem_q" in f for f in r.findings), r.findings


MEM_CELL = """`default_nettype none
// cdc-cell: src=src_clk_i dst=dst_clk_i
module common_cdc_bad (
  input  logic src_clk_i,
  input  logic src_d_i,
  input  logic dst_clk_i,
  output logic dst_q_o
);
{body}endmodule
`default_nettype wire
"""
SYNCED = """  logic m [{depth}];
  logic src_a_q;
  logic ptr;
  logic ra_q;
  always_ff @(posedge src_clk_i) begin
    src_a_q <= ~src_a_q;
    m[{windex}] <= src_d_i;
  end
  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0), .src_data_i(src_a_q),
                        .dst_data_o(ptr));
  always_ff @(posedge dst_clk_i) ra_q <= ptr;
  assign dst_q_o = {read};
"""


@needs_verilator
@pytest.mark.parametrize("body", [
    # One entry and a constant qualifier: a renamed register.
    """  logic m [1];
  logic z_q;
  always_ff @(posedge src_clk_i) m[0] <= src_d_i;
  always_ff @(posedge dst_clk_i) z_q <= 1'b1;
  assign dst_q_o = m[0] & z_q;
""",
    # A free-running read address: no pointer crosses.
    """  logic m [4];
  logic [1:0] wa_q;
  logic [1:0] ra_q;
  always_ff @(posedge src_clk_i) begin
    m[wa_q] <= src_d_i;
    wa_q <= wa_q + 2'd1;
  end
  always_ff @(posedge dst_clk_i) ra_q <= ra_q + 2'd1;
  assign dst_q_o = m[ra_q];
""",
    # A destination flop that only holds its value, used as a select.
    """  logic m [2];
  logic h_q;
  always_ff @(posedge src_clk_i) m[0] <= src_d_i;
  always_ff @(posedge dst_clk_i) h_q <= h_q;
  assign dst_q_o = h_q ? m[1] : m[0];
""",
    # A synchronized pointer, but a one-entry memory.
    SYNCED.format(depth=1, windex="0", read="m[0] & ra_q"),
    # A synchronized pointer gates the output, but the memory is read at a
    # constant address: the pointer does not address the read (#177 enconst).
    SYNCED.format(depth=2, windex="src_a_q", read="m[0] & ra_q"),
    # The same, plus a second read at a free-running destination counter:
    # an index that is not constant, but not a synchronized pointer either.
    SYNCED.format(depth=2, windex="src_a_q", read="m[0] & ra_q") + """  logic fr_q;
  logic x_q;
  always_ff @(posedge dst_clk_i) fr_q <= ~fr_q;
  always_ff @(posedge dst_clk_i) x_q <= m[fr_q];
""",
    # The same, plus a read at the synchronized pointer whose value goes
    # nowhere near the output: only reads in the output's cone count.
    SYNCED.format(depth=2, windex="src_a_q", read="m[0] & ra_q") + """  logic u;
  assign u = m[ra_q];
""",
    # A read at the synchronized pointer and a read at a constant address
    # both reach the output: every read in the output's cone must qualify.
    SYNCED.format(depth=2, windex="src_a_q", read="m[ra_q] ^ m[0]"),
    # A read at a constant address in a condition steers the output: it
    # counts toward what the conditional statement writes.
    "  logic c;\n" + SYNCED.format(depth=2, windex="src_a_q", read="c")
    + "  always_comb begin\n    c = 1'b0;\n    if (m[0]) c = m[ra_q];\n  end\n",
], ids=["one-entry-qualifier", "free-running-address", "hold-select", "one-entry-synced",
        "constant-address-synced-gate", "free-running-second-read", "unused-synced-read",
        "constant-read-beside-synced-read", "constant-read-in-condition"])
def test_memory_read_without_a_synchronized_pointer_fails(tmp_path, body):
    r = run(cell_tree(tmp_path, MEM_CELL.format(body=body), "-F cdc/common_cdc_sync2.rtl.f\n"))
    assert any("output dst_q_o is driven by flop m (" in f for f in r.findings), r.findings


# A plain (not approved) module inside the cell: a wire from input to output.
PASS_MODULE = """`default_nettype none
module bad_pass (
  input  logic i_i,
  output logic o_o
);
  assign o_o = i_i;
endmodule
`default_nettype wire
"""


def synced_via_c(tail: str) -> str:
    """SYNCED with the output driven by ``c``, which ``tail`` drives."""
    return "  logic c;\n" + SYNCED.format(depth=2, windex="src_a_q", read="c") + tail


@needs_verilator
@pytest.mark.parametrize("body,extra", [
    (SYNCED.format(depth=2, windex="src_a_q", read="m[ra_q]"), ""),
    # A read at a constant address whose value does not reach the output is
    # outside the output's cone, so it does not disqualify the memory.
    (SYNCED.format(depth=2, windex="src_a_q", read="m[ra_q]")
     + "  logic u;\n  assign u = m[0];\n", ""),
    # The only read at the pointer sits in a condition: it counts toward the
    # variable that the conditional statement writes. (Verilator folds a
    # plain if/else of one assignment into a ?: assignment, so the default
    # assignment keeps the IF statement.)
    (synced_via_c("  always_comb begin\n    c = 1'b0;\n    if (m[ra_q]) c = 1'b1;\n  end\n"),
     ""),
    # The read at the pointer feeds a plain submodule's input, which drives
    # the output: the instance input is the read's target.
    (synced_via_c("  bad_pass u_p (.i_i(m[ra_q]), .o_o(c));\n"), PASS_MODULE),
], ids=["one-read", "constant-read-off-the-output", "read-in-condition",
        "read-into-instance-pin"])
def test_memory_read_at_a_synchronized_pointer_passes(tmp_path, body, extra):
    r = run(cell_tree(tmp_path, MEM_CELL.format(body=body) + extra,
                      "-F cdc/common_cdc_sync2.rtl.f\n"))
    assert r.passed, r.findings


# How an inner synchronizer's source-side input is tied off: the pin text,
# any declaration it needs, and the finding's wording.
TIE_OFFS = {
    "constant": ("  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0), "
                 ".src_data_i(1'b1),\n", "", "is a constant"),
    "empty-pin": ("  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0), "
                  ".src_data_i(),\n", "", "is unconnected"),
    "omitted-pin": ("  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0),\n",
                    "", "is unconnected"),
    # Verilator folds a continuously assigned constant into the pin, so this
    # one reads as a plain constant.
    "assigned-net": ("  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0), "
                     ".src_data_i(one),\n", "  logic one;\n  assign one = 1'b1;\n",
                     "is a constant"),
    "comb-net": ("  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0), "
                 ".src_data_i(one),\n", "  logic one;\n  always_comb one = 1'b1;\n",
                 "is driven only by constants"),
    "undriven-net": ("  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0), "
                     ".src_data_i(one),\n", "  logic one;\n", "is driven only by constants"),
    # Flops that never change: one loads a constant, one holds its value.
    "constant-flop": ("  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0), "
                      ".src_data_i(one),\n",
                      "  logic one;\n  always_ff @(posedge src_clk_i) one <= 1'b1;\n",
                      "is driven only by constants"),
    "hold-flop": ("  common_cdc_sync2 u_s (.dst_clk_i(dst_clk_i), .dst_rst_i(1'b0), "
                  ".src_data_i(one),\n",
                  "  logic one;\n  always_ff @(posedge src_clk_i) one <= one;\n",
                  "is driven only by constants"),
}


@needs_verilator
@pytest.mark.parametrize("tie", list(TIE_OFFS))
def test_crossing_cell_with_a_constant_source_input_inside_a_cell_fails(tmp_path, tie):
    # A synchronizer of a constant (or of nothing) qualifies the read
    # pointer: it looks like a synchronized pointer but nothing crosses.
    pin, decl, how = TIE_OFFS[tie]
    body = f"""  logic m [4];
  logic [1:0] wa_q;
  logic [1:0] ra_q;
  logic s;
{decl}  always_ff @(posedge src_clk_i) begin
    m[wa_q] <= src_d_i;
    wa_q <= wa_q + 2'd1;
  end
{pin}                        .dst_data_o(s));
  always_ff @(posedge dst_clk_i) ra_q <= s ? ra_q + 2'd1 : ra_q;
  assign dst_q_o = m[ra_q];
"""
    line = 17 + decl.count("\n")
    r = run(cell_tree(tmp_path, MEM_CELL.format(body=body), "-F cdc/common_cdc_sync2.rtl.f\n"))
    assert r.findings == ["common/common_cdc_bad: rtl/common/common_cdc_bad.sv:2: crossing "
                          "cell common_cdc_bad: source-side input of cell u_s.src_data_i "
                          f"(rtl/common/common_cdc_bad.sv:{line}) {how}, so that cell "
                          "crosses nothing"]


@needs_verilator
@pytest.mark.parametrize("tie", list(TIE_OFFS))
def test_ip_top_may_tie_off_a_crossing_cell_source_input(tmp_path, tie):
    # The constant-source rule is for crossing cells analyzed on their own:
    # an IP top that ties off an approved cell's src_* input passes.
    pin, decl, _ = TIE_OFFS[tie]
    decl = decl.replace("src_clk_i", "clk_a_i")
    pin = pin.replace("dst_clk_i(dst_clk_i)", "dst_clk_i(clk_b_i)").replace(
        "dst_rst_i(1'b0)", "dst_rst_i(rst_b_i)")
    body = A_FLOP + f"""  logic s;
  logic b_q;
{decl}{pin}                        .dst_data_o(s));
  always_ff @(posedge clk_b_i) b_q <= s ^ a_q;
  assign y_o = b_q;
"""
    # a_q is sampled raw on clk_b_i only to give the IP a finding to compare
    # against: the tie-off itself adds none.
    r = run(tree(tmp_path, body))
    assert len(blk(r)) == 1 and "samples flop a_q" in blk(r)[0], r.findings


# ---------------------------------------------------------------- selection, tooling


@needs_verilator
def test_ip_selects_top_level_ancestors_and_skips_shared_blocks(tmp_path):
    src = tree(tmp_path, A_FLOP + "  assign y_o = a_q;\n")
    (src / "rtl/blk_sub").mkdir()
    (src / "rtl/blk_sub/xy_blk_sub.sv").write_text("module xy_blk_sub;\nendmodule\n")
    (src / "rtl/blk_sub/blk_sub.rtl.f").write_text("xy_blk_sub.sv\n")
    rtl_f = src / "rtl/blk/blk.rtl.f"
    rtl_f.write_text("-F ../blk_sub/blk_sub.rtl.f\n" + rtl_f.read_text())
    r = run(src, {"blk_sub"})
    assert r.passed, r.findings
    assert r.checked.startswith("1 top-level IP(s), 0 shared block(s), 1 Verilator")


@needs_verilator
def test_package_only_shared_block_is_skipped(tmp_path):
    src = tree(tmp_path, A_FLOP + "  assign y_o = a_q;\n")
    (src / "rtl/common/common_x_pkg.sv").write_text(
        "`default_nettype none\npackage common_x_pkg;\n"
        "  typedef enum logic [1:0] {A = 2'd0} t_a;\nendpackage\n`default_nettype wire\n")
    (src / "rtl/common/common_x_pkg.rtl.f").write_text("common_x_pkg.sv\n")
    r = run(src)
    assert r.passed, r.findings
    n_pkgs = 1 + sum(1 for f in CELLS.glob("*_pkg.rtl.f"))
    assert r.checked.endswith(f"{n_pkgs} package-only block(s) skipped"), r.checked


def test_verilator_missing_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: None)
    with pytest.raises(sim.SimError, match="verilator not found"):
        run(tree(tmp_path, ""))


def test_no_targets_needs_no_verilator(tmp_path, monkeypatch):
    monkeypatch.setattr(sim, "find_verilator", lambda: None)
    (tmp_path / "src").mkdir()
    r = run(tmp_path / "src")
    assert r.passed and r.checked.startswith("0 top-level IP(s), 0 shared block(s)")


@needs_verilator
def test_unpinned_verilator_version_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cdc, "verilator_version", lambda v: "0.000")
    r = run(tree(tmp_path, ""))
    assert r.findings == ["Verilator 0.000 found, but the JSON netlist reader is tied to the "
                          f"pinned version {cdc.pinned_version()} in cfg/host.yaml"]


@pytest.mark.parametrize("text", [None, "verilator: {}\n", "verilator: [\n"],
                         ids=["missing", "no-version", "malformed"])
def test_missing_pin_is_none(tmp_path, monkeypatch, text):
    if text is not None:
        (tmp_path / "cfg").mkdir()
        (tmp_path / "cfg/host.yaml").write_text(text)
    monkeypatch.setattr(cdc, "REPO_ROOT", tmp_path)
    assert cdc.pinned_version() is None


@needs_verilator
def test_missing_pin_fails_without_analysis(tmp_path, monkeypatch):
    monkeypatch.setattr(cdc, "pinned_version", lambda: None)
    r = run(tree(tmp_path, A_FLOP + "  logic b_q;\n  always_ff @(posedge clk_b_i) b_q <= a_q;\n"
                 "  assign y_o = b_q;\n"))
    assert r.findings == [f"Verilator {cdc.verilator_version(sim.find_verilator())} found, but "
                          "the JSON netlist reader is tied to the pinned version (none) in "
                          "cfg/host.yaml"]
    assert ", 0 Verilator --json-only run(s), 0 flop(s)" in r.checked


@needs_verilator
def test_repository_passes():
    src = REPO_ROOT / "src"
    r = run(src)
    assert r.passed, r.findings


@needs_verilator
def test_integrated_top_level_ip_is_analyzed_alone_and_in_its_integrator(tmp_path):
    src = tree(tmp_path, A_FLOP + "  assign y_o = a_q;\n")
    (src / "rtl/oth").mkdir()
    (src / "rtl/oth/xy_oth.sv").write_text("module xy_oth;\nendmodule\n")
    (src / "rtl/oth/oth.rtl.f").write_text("xy_oth.sv\n")
    rtl_f = src / "rtl/blk/blk.rtl.f"
    rtl_f.write_text("-F ../oth/oth.rtl.f\n" + rtl_f.read_text())
    assert run(src).checked.startswith(f"2 top-level IP(s), {N_BLOCKS} shared block(s)")
    assert run(src, {"oth"}).checked.startswith("2 top-level IP(s), 0 shared block(s)")
    assert run(src, {"blk"}).checked.startswith("1 top-level IP(s), 0 shared block(s)")
