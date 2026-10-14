// DE2-115 board wrapper for pcie_top (wiki/src/top/fpga/FPGA.md).
// Board policy only: holds reset for a short count after configuration,
// synchronizes the switches and push buttons to the 50 MHz board clock,
// turns button presses into one-cycle pulses and holds the last scrambled
// byte on the red LEDs. Not part of top.rtl.f.
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

  // Power-on reset: por_cnt_q starts at 0 after configuration (power-up
  // don't-care is disabled in de2_115.qsf.tcl) and counts to all ones;
  // reset is held until then.
  localparam int unsigned POR_W = 4;

  logic [POR_W-1:0] por_cnt_d;
  logic [POR_W-1:0] por_cnt_q;
  logic             por_done;

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
  logic       top_valid;
  t_byte      top_data;
  t_byte      shown_d;
  t_byte      shown_q;
  logic       seen_d;
  logic       seen_q;

  assign por_done = &por_cnt_q;

  always_comb begin
    por_cnt_d = por_cnt_q;
    if (!por_done) por_cnt_d = por_cnt_q + POR_W'(1);
  end

  always_ff @(posedge clk_50_i) begin
    por_cnt_q <= por_cnt_d;
  end

  always_ff @(posedge clk_50_i) begin
    key_meta_q <= key_ni;
    key_sync_q <= key_meta_q;
    key_prev_q <= key_sync_q[2:1];
    sw_meta_q  <= sw_i;
    sw_sync_q  <= sw_meta_q;
  end

  // A press is a high-to-low step of the synchronized active-low button.
  assign press = key_prev_q & ~key_sync_q[2:1];
  // Reset (synchronous, active high): power-on count, or KEY[0] held.
  assign rst   = ~por_done | ~key_sync_q[0];

  pcie_top u_top (
    .clk_i    (clk_50_i),
    .rst_i    (rst),
    .valid_i  (press[1]),
    .data_i   (sw_sync_q),
    .reseed_i (press[2]),
    .valid_o  (top_valid),
    .data_o   (top_data)
  );

  always_comb begin
    shown_d = shown_q;
    seen_d  = seen_q;
    if (top_valid) begin
      shown_d = top_data;
      seen_d  = 1'b1;
    end
  end

  // Reset clears the LEDs.
  always_ff @(posedge clk_50_i) begin
    if (rst) begin
      shown_q <= '0;
      seen_q  <= 1'b0;
    end else begin
      shown_q <= shown_d;
      seen_q  <= seen_d;
    end
  end

  assign ledr_o  = shown_q;
  assign ledg0_o = seen_q;

endmodule

`default_nettype wire
