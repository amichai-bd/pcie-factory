`default_nettype none

`include "common_macros.svh"

// Toggle pulse synchronizer: each src_clk_i cycle with src_pulse_i high gives
// one dst_clk_i cycle with dst_pulse_o high. Pulses must be at least two
// dst_clk_i periods apart (wall time); closer pulses may merge or be lost.
// Latency: two or three dst_clk_i edges after the src_clk_i edge that
// samples src_pulse_i. Spec: wiki/src/rtl/common/common_cdc_pulse.md.
// cdc-cell: src=src_clk_i dst=dst_clk_i
module common_cdc_pulse (
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

  `COMMON_DFF_RST(toggle_q, toggle_d, src_clk_i, src_rst_i)

  `COMMON_METAFLOP(u_sync, sync, toggle_q, dst_clk_i, dst_rst_i)

  // Destination domain: a change of the synchronized level is one pulse.
  `COMMON_DFF_RST(seen_q, sync, dst_clk_i, dst_rst_i)

  assign dst_pulse_o = sync ^ seen_q;

endmodule

`default_nettype wire
