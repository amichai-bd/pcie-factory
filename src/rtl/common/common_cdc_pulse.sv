`default_nettype none

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

  always_ff @(posedge src_clk_i) begin
    if (src_rst_i) begin
      toggle_q <= 1'b0;
    end else begin
      toggle_q <= toggle_d;
    end
  end

  common_cdc_sync2 #(
    .WIDTH(1)
  ) u_sync (
    .dst_clk_i (dst_clk_i),
    .dst_rst_i (dst_rst_i),
    .src_data_i(toggle_q),
    .dst_data_o(sync)
  );

  // Destination domain: a change of the synchronized level is one pulse.
  always_ff @(posedge dst_clk_i) begin
    if (dst_rst_i) begin
      seen_q <= 1'b0;
    end else begin
      seen_q <= sync;
    end
  end

  assign dst_pulse_o = sync ^ seen_q;

endmodule

`default_nettype wire
