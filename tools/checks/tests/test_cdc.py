"""Tests for the structural clock-domain-crossing check (checks/cdc.py).

Each case builds a small tree in a temporary directory: an IP ``blk`` (top
``xy_blk``) next to a copy of the repository's shared crossing cells, so the
legal crossings run through the real cells.
"""

import shutil
from pathlib import Path

import pytest

from build import sim
from build.ip import REPO_ROOT, discover
from checks import cdc

needs_verilator = pytest.mark.skipif(sim.find_verilator() is None,
                                     reason="verilator not available")

CELLS = REPO_ROOT / "src/rtl/common"

HEAD = """`default_nettype none
module xy_blk (
  input  logic clk_a_i,
  input  logic clk_b_i,
  input  logic rst_a_i,
  input  logic rst_b_i,
  input  logic x_i,
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
    lines = ["-F ../common/common_cdc_sync2.rtl.f", "-F ../common/common_cdc_pulse.rtl.f",
             "-F ../common/common_cdc_fifo.rtl.f"]
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
    assert "1 top-level IP(s), 3 shared block(s), 4 Verilator --json-only run(s)" in r.checked
    assert "3 cdc-cell directive(s)" in r.checked


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
    [f] = blk(run(tree(tmp_path, body)))
    assert "flop b_q (clock clk_b_i" in f and "flop a_q (clock clk_a_i" in f
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
    found, findings = cdc.scan_directives([good, bad, ip], src)
    assert list(found.values()) == [cdc.Directive("common_cdc_ok", "src_clk_i", "dst_clk_i",
                                                  good.resolve(), 1)]
    assert findings == [
        "rtl/common/common_cdc_bad.sv:1: malformed cdc-cell directive (expected "
        "'// cdc-cell: src=<clock port|async> dst=<clock port>')",
        "rtl/common/common_cdc_bad.sv:4: cdc-cell directive is not on the line directly "
        "above a module line",
        "rtl/common/common_cdc_bad.sv:8: malformed cdc-cell directive on common_cdc_same: "
        "src and dst name the same port",
        "rtl/common/common_cdc_bad.sv:14: cdc-cell directive on common_other: only "
        "common_cdc_* modules in rtl/common/ carry one",
        "rtl/common/common_cdc_bad.sv:6: crossing cell common_cdc_gap has no cdc-cell "
        "directive on the line above its module line",
        "rtl/common/common_cdc_bad.sv:11: crossing cell common_cdc_none has no cdc-cell "
        "directive on the line above its module line",
        "rtl/blk/xy_blk.sv:1: cdc-cell directive on common_cdc_mine: only common_cdc_* "
        "modules in rtl/common/ carry one",
    ]


@needs_verilator
def test_missing_directive_fails_and_the_cell_is_not_approved(tmp_path):
    src = tree(tmp_path, SYNC2)
    cell = src / "rtl/common/common_cdc_sync2.sv"
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
    cell = src / "rtl/common/common_cdc_sync2.sv"
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
    cell = src / "rtl/common/common_cdc_sync2.sv"
    cell.write_text(cell.read_text().replace(old, new))
    r = run(src)
    assert any(f"crossing cell common_cdc_sync2: {msg}" in f for f in r.findings), r.findings


@needs_verilator
def test_port_prefix_rule(tmp_path):
    src = tree(tmp_path, SYNC2)
    cell = src / "rtl/common/common_cdc_sync2.sv"
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


@needs_verilator
def test_repository_passes():
    src = REPO_ROOT / "src"
    r = run(src)
    assert r.passed, r.findings
