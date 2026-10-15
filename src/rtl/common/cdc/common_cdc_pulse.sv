`default_nettype none

`include "common_macros.svh"

// Toggle pulse synchronizer: each src_clk_i cycle with src_pulse_i high gives
// one dst_clk_i cycle with dst_pulse_o high. Pulses must be at least two
// dst_clk_i periods apart (wall time); closer pulses may merge or be lost.
// Latency: two or three dst_clk_i edges after the src_clk_i edge that
// samples src_pulse_i. Each side resets synchronously by default;
// SRC_ASYNC_RESET or DST_ASYNC_RESET 1 makes that side's flops reset at once,
// with no edge of its clock (its reset must then come from a
// COMMON_RESET_SYNC on that clock). RTL outside rtl/common/ instantiates it
// through COMMON_PULSE_SYNC or COMMON_PULSE_SYNC_ARST.
// Spec: wiki/src/rtl/common/cdc/common_cdc_pulse.md.
// cdc-cell: src=src_clk_i dst=dst_clk_i
module common_cdc_pulse #(
  parameter bit SRC_ASYNC_RESET = 1'b0,
  parameter bit DST_ASYNC_RESET = 1'b0
) (
  input  logic src_clk_i,
  input  logic src_rst_i,
  input  logic src_pulse_i,
  input  logic dst_clk_i,
  input  logic dst_rst_i,
  output logic dst_pulse_o
);

  logic toggle_d;
  logic toggle_q;
  logic sync;
  logic seen_q;

  // Source domain: every pulse flips the toggle level.
  assign toggle_d = toggle_q ^ src_pulse_i;

  generate
    if (SRC_ASYNC_RESET) begin : g_src_arst
      `COMMON_DFF_ARST_VAL(toggle_q, toggle_d, src_clk_i, src_rst_i, 1'b0)
    end else begin : g_src_srst
      `COMMON_DFF_RST(toggle_q, toggle_d, src_clk_i, src_rst_i)
    end
  endgenerate

  common_cdc_sync2 #(
    .WIDTH      (1),
    .ASYNC_RESET(DST_ASYNC_RESET)
  ) u_sync (
    .dst_clk_i (dst_clk_i),
    .dst_rst_i (dst_rst_i),
    .src_data_i(toggle_q),
    .dst_data_o(sync)
  );

  // Destination domain: a change of the synchronized level is one pulse.
  generate
    if (DST_ASYNC_RESET) begin : g_dst_arst
      `COMMON_DFF_ARST_VAL(seen_q, sync, dst_clk_i, dst_rst_i, 1'b0)
    end else begin : g_dst_srst
      `COMMON_DFF_RST(seen_q, sync, dst_clk_i, dst_rst_i)
    end
  endgenerate

  assign dst_pulse_o = sync ^ seen_q;

endmodule

`default_nettype wire
