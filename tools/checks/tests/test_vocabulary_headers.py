"""The repository's vocabulary headers (``src/rtl/common/*.svh``, the RTL
BKM's shared vocabulary) behave as the BKM states in Verilator: each
assertion form stops a simulation built with ``--assert`` when its property
fails, and is absent when ``SYNTHESIS`` is defined (as the FPGA and ASIC
flows define it); the parameter check stays in every build.

Each form gets its own trigger bit, chosen at run time with ``+case=<n>``,
so two builds (simulation and synthesis view) cover every form; the
parameter check is proven by a lint run and a preprocessor run.
``COMMON_ASSERT_KNOWN`` cannot fail in two-state Verilator and is checked
only for expansion.

Marked ``vocabulary``: ``./build ci unit --affected`` runs them when a
vocabulary header changes (cfg/ci.yaml ``vocabulary_tests``), besides every
tools run."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from build import sim
from build.ip import REPO_ROOT
from checks import style

needs_verilator = pytest.mark.skipif(sim.find_verilator() is None,
                                     reason="Verilator not installed")
pytestmark = pytest.mark.vocabulary

SRC = REPO_ROOT / "src"
INCDIR = SRC / "rtl" / "common"

# Trigger bit -> the label of the assertion it trips.
CASES = {
    0: "a_assert",
    1: "a_no_reset",
    2: "a_never",
    3: "a_stable",
    4: "a_clocked",
    5: "a_clocked_when",
    6: "a_clocked_arst",
    7: "a_never_arst",
}

DUT = """`default_nettype none
`include "common_macros.svh"
module vh_dut #(
  parameter int unsigned BAD_PARAM = 0
) (
  input  logic       clk_i,
  input  logic       rst_i,
  input  logic       arst_i,
  input  logic [7:0] bad_i
);
  logic [3:0] cnt_d, cnt_q;
  logic       hold, guard;
  assign cnt_d = cnt_q + 4'd1;
  assign hold  = bad_i[3];
  assign guard = bad_i[5];
  `COMMON_DFF_RST(cnt_q, cnt_d, clk_i, rst_i)
  `COMMON_ASSERT(a_assert, clk_i, rst_i, !bad_i[0])
  `COMMON_ASSERT_NO_RESET(a_no_reset, clk_i, !bad_i[1])
  `COMMON_ASSERT_NEVER(a_never, clk_i, rst_i, bad_i[2])
  `COMMON_ASSERT_KNOWN(a_known, clk_i, rst_i, bad_i)
  `COMMON_ASSERT_STABLE_WHEN(a_stable, clk_i, rst_i, hold, cnt_q)
  `COMMON_ASSERT_CLOCKED(a_clocked, clk_i, rst_i, !bad_i[4])
  `COMMON_ASSERT_CLOCKED_WHEN(a_clocked_when, clk_i, rst_i, guard, 1'b0)
  `COMMON_ASSERT_CLOCKED_ARST(a_clocked_arst, clk_i, arst_i, !bad_i[6])
  `COMMON_ASSERT_NEVER_ARST(a_never_arst, clk_i, arst_i, bad_i[7])
  `COMMON_ASSERT_PARAM(g_bad_param, BAD_PARAM == 0, "vh_dut: BAD_PARAM must be 0")
endmodule
`default_nettype wire
"""

TB = """module vh_tb;
  logic clk = 1'b0, rst = 1'b1;
  logic [7:0] bad = '0;
  int unsigned sel;
  vh_dut #(.BAD_PARAM(`BAD_PARAM)) u_dut (.clk_i(clk), .rst_i(rst), .arst_i(rst), .bad_i(bad));
  always #5 clk = ~clk;
  initial begin
    if (!$value$plusargs("case=%d", sel)) sel = 99;
    #22 rst = 1'b0;
    #20 if (sel < 8) bad[sel] = 1'b1;
    #60 $display("vh_tb: end");
    $finish;
  end
endmodule
"""


def _build(tmp: Path, name: str, defines: list[str]) -> Path:
    verilator = sim.find_verilator()
    (tmp / "vh_dut.sv").write_text(DUT)
    (tmp / "vh_tb.sv").write_text(TB)
    obj = tmp / name
    cmd = [str(verilator), "--binary", "--timing", "--assert", "-Wno-fatal",
           "--top-module", "vh_tb", f"+incdir+{INCDIR}", "-Mdir", str(obj), "-o", "sim",
           *defines, str(tmp / "vh_dut.sv"), str(tmp / "vh_tb.sv")]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return obj / "sim"


def _run(binary: Path, case: int) -> subprocess.CompletedProcess:
    return subprocess.run([str(binary), f"+case={case}"], capture_output=True, text=True,
                          timeout=60, cwd=binary.parent)


def test_headers_are_the_vocabulary():
    vocab = style.vocabulary(SRC)
    assert vocab.headers, "no vocabulary headers in src/rtl/common/"
    for form in ("COMMON_DFF", "COMMON_ASSERT", "COMMON_ASSERT_PARAM", "COMMON_METAFLOP"):
        assert form in vocab.macros


@needs_verilator
def test_each_assertion_form_fires_and_compiles_out(tmp_path):
    simulation = _build(tmp_path, "sim_view", ["-DBAD_PARAM=0"])
    synthesis = _build(tmp_path, "synth_view", ["-DBAD_PARAM=0", "-DSYNTHESIS"])
    quiet = _run(simulation, 99)
    assert quiet.returncode == 0 and "vh_tb: end" in quiet.stdout, quiet.stdout + quiet.stderr
    for case, label in CASES.items():
        fired = _run(simulation, case)
        out = fired.stdout + fired.stderr
        assert fired.returncode != 0 and f"assertion {label} failed" in out, (case, out)
        gone = _run(synthesis, case)
        assert gone.returncode == 0 and "vh_tb: end" in gone.stdout, (case, gone.stdout)


@needs_verilator
def test_parameter_check_fails_lint_at_elaboration(tmp_path):
    (tmp_path / "vh_dut.sv").write_text(DUT)
    base = [str(sim.find_verilator()), "--lint-only", "-Wall", "--top-module", "vh_dut",
            f"+incdir+{INCDIR}", str(tmp_path / "vh_dut.sv")]
    good = subprocess.run(base, capture_output=True, text=True, timeout=120)
    assert good.returncode == 0, good.stdout + good.stderr
    bad = subprocess.run(base + ["-GBAD_PARAM=1"], capture_output=True, text=True, timeout=120)
    out = bad.stdout + bad.stderr
    assert bad.returncode != 0 and "USERFATAL" in out and "BAD_PARAM must be 0" in out, out


@needs_verilator
def test_synthesis_view_has_no_assertion(tmp_path):
    (tmp_path / "vh_dut.sv").write_text(DUT)
    cmd = [str(sim.find_verilator()), "-E", "-P", f"+incdir+{INCDIR}", "-DSYNTHESIS",
           str(tmp_path / "vh_dut.sv")]
    text = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True).stdout
    body = text[text.index("module vh_dut"):]
    assert "assert" not in body and "a_stable_hold_q" not in body
    # Only the parameter check is left, in the source's synthesis form.
    assert body.count("$fatal") == 1 and "initial $fatal" in " ".join(body.split())
