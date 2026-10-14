// DE2-115 board wrapper for pcie_top (wiki/src/top/HAS.md, FPGA section).
// Board policy only: synchronizes the switches and push buttons to the
// 50 MHz board clock, turns button presses into one-cycle pulses and holds
// the last scrambled byte on the red LEDs. Not part of top.rtl.f.
`default_nettype none

module pcie_top_de2_115
  import pcie_top_lfsr_pkg::*;
(
  input  logic       clk_50_i,  // CLOCK_50: 50 MHz board oscillator
  input  logic [2:0] key_ni,    // KEY[2:0]: push buttons, low while pressed
                                //   [0] reset, [1] send SW[7:0], [2] reseed
  input  t_byte      sw_i,      // SW[7:0]: data byte
  output t_byte      ledr_o,    // LEDR[7:0]: last scrambled byte
  output logic       ledg0_o    // LEDG[0]: a byte has been shown since reset
);

  // Two-flop synchronizers: the buttons and switches are asynchronous to
  // clk_50_i. key_prev_q keeps the previous synchronized level of the two
  // buttons that need edge detection.
  logic [2:0] key_meta_q;
  logic [2:0] key_sync_q;
  logic [2:1] key_prev_q;
  t_byte      sw_meta_q;
  t_byte      sw_sync_q;

  logic       rst;
  logic [2:1] press;
  logic       valid_o;
  t_byte      data_o;
  t_byte      shown_d;
  t_byte      shown_q;
  logic       seen_d;
  logic       seen_q;

  always_ff @(posedge clk_50_i) begin
    key_meta_q <= key_ni;
    key_sync_q <= key_meta_q;
    key_prev_q <= key_sync_q[2:1];
    sw_meta_q  <= sw_i;
    sw_sync_q  <= sw_meta_q;
  end

  // A press is a high-to-low step of the synchronized active-low button.
  assign press = key_prev_q & ~key_sync_q[2:1];
  // Reset is held while KEY[0] is pressed (synchronous, active high).
  assign rst   = ~key_sync_q[0];

  pcie_top u_top (
    .clk_i    (clk_50_i),
    .rst_i    (rst),
    .valid_i  (press[1]),
    .data_i   (sw_sync_q),
    .reseed_i (press[2]),
    .valid_o  (valid_o),
    .data_o   (data_o)
  );

  always_comb begin
    shown_d = shown_q;
    seen_d  = seen_q;
    if (valid_o) begin
      shown_d = data_o;
      seen_d  = 1'b1;
    end
  end

  always_ff @(posedge clk_50_i) begin
    if (rst) seen_q <= 1'b0;
    else     seen_q <= seen_d;
  end

  // The LED byte is qualified by seen_q, so it needs no reset.
  always_ff @(posedge clk_50_i) begin
    shown_q <= shown_d;
  end

  assign ledr_o  = shown_q;
  assign ledg0_o = seen_q;

endmodule

`default_nettype wire
