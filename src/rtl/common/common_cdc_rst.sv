`default_nettype none

// Reset synchronizer: dst_rst_o asserts asynchronously, in the same delta
// as src_rst_i and with no dst_clk_i edge, and releases synchronously at
// the STAGES-th dst_clk_i rising edge after src_rst_i falls (STAGES + 1
// when the first flop resolves late). src_rst_i must be glitch-free: a flop
// output or a reset input pin, with no logic between. Every flop that takes
// dst_rst_o lists it in its sensitivity list. Spec:
// wiki/src/rtl/common/common_cdc_rst.md.
//
// Provenance: adapted from nand2anything src/common/rtl/n2a_reset_sync.sv
// (commit 085ef4b, same owner, adapted with the owner's permission);
// the module and its ports renamed for the cdc-cell convention, and its
// vendor attribute dropped.
// cdc-cell: src=async dst=dst_clk_i
module common_cdc_rst #(
  parameter int unsigned STAGES = 2
) (
  input  logic dst_clk_i,
  input  logic src_rst_i,
  output logic dst_rst_o
);

  // Elaboration-time guard: one flop alone gives no time to resolve.
  if (STAGES < 2) begin : g_stages_check
    $error("common_cdc_rst: STAGES must be at least 2");
  end

  logic [STAGES-1:0] sync_d;
  logic [STAGES-1:0] sync_q;

  // Release: a zero enters at the bottom and shifts up one stage per edge.
  assign sync_d = {sync_q[STAGES-2:0], 1'b0};

  always_ff @(posedge dst_clk_i or posedge src_rst_i) begin
    if (src_rst_i) begin
      sync_q <= '1;
    end else begin
      sync_q <= sync_d;
    end
  end

  assign dst_rst_o = sync_q[STAGES-1];

endmodule

`default_nettype wire
