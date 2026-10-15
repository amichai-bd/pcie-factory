`default_nettype none

`include "common_macros.svh"

// Reset synchronizer: dst_rst_o asserts asynchronously, in the same delta
// as src_rst_i and with no dst_clk_i edge, and releases synchronously at
// the STAGES-th dst_clk_i rising edge after src_rst_i falls (STAGES + 1
// when the first flop resolves late). src_rst_i must be glitch-free: a flop
// output or a reset input pin, with no logic between. Every flop that takes
// dst_rst_o is a COMMON_DFF_ARST_* register (the CDC check's reset= rule).
// RTL outside rtl/common/ instantiates it through COMMON_RESET_SYNC or
// COMMON_RESET_SYNC_STAGES. Spec:
// wiki/src/rtl/common/cdc/common_cdc_rst.md.
//
// Provenance: adapted from nand2anything src/common/rtl/n2a_reset_sync.sv
// (commit 085ef4b, same owner, adapted with the owner's permission);
// the module and its ports renamed for the cdc-cell convention, and its
// vendor attribute dropped.
// cdc-cell: src=async dst=dst_clk_i reset=dst_rst_o
module common_cdc_rst #(
  parameter int unsigned STAGES = 2
) (
  input  logic dst_clk_i,
  input  logic src_rst_i,
  output logic dst_rst_o
);

  // Elaboration-time guard: one flop alone gives no time to resolve.
  `COMMON_ASSERT_PARAM(g_stages_check, STAGES >= 2, "common_cdc_rst: STAGES must be at least 2")

  // The first STAGES-1 stages, and the last stage on its own: out_q drives
  // dst_rst_o and is read by no flop of this cell, so the net that the
  // consumers take as an asynchronous reset is never also flop data here.
  logic [STAGES-2:0] sync_d;
  logic [STAGES-2:0] sync_q;
  logic              out_q;

  // Release: a zero enters at the bottom and shifts up one stage per edge.
  assign sync_d = sync_q << 1;

  // Assertion: src_rst_i sets every stage at once, with no clock edge.
  `COMMON_DFF_ARST_VAL(sync_q, sync_d, dst_clk_i, src_rst_i, '1)
  `COMMON_DFF_ARST_VAL(out_q, sync_q[STAGES-2], dst_clk_i, src_rst_i, 1'b1)

  assign dst_rst_o = out_q;

endmodule

`default_nettype wire
