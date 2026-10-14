// Byte-stream scrambler: data_o = data_i ^ LFSR key, one cycle later
// (wiki/src/top/HAS.md).
`default_nettype none

module pcie_top
  import pcie_top_lfsr_pkg::*;
(
  input  logic  clk_i,
  input  logic  rst_i,     // synchronous, active high
  input  logic  valid_i,   // data_i holds a byte to scramble; advances the LFSR
  input  t_byte data_i,
  input  logic  reseed_i,  // re-seed the LFSR after this cycle (COM-style)
  output logic  valid_o,
  output t_byte data_o
);

  t_byte key;
  t_byte data_d;
  t_byte data_q;
  logic  valid_q;

  pcie_top_lfsr u_lfsr (
    .clk_i     (clk_i),
    .rst_i     (rst_i),
    .reseed_i  (reseed_i),
    .advance_i (valid_i),
    .key_o     (key)
  );

  assign data_d = data_i ^ key;

  always_ff @(posedge clk_i) begin
    if (rst_i) valid_q <= 1'b0;
    else       valid_q <= valid_i;
  end

  // Data is qualified by valid_o, so it needs no reset.
  always_ff @(posedge clk_i) begin
    if (valid_i) data_q <= data_d;
  end

  assign valid_o = valid_q;
  assign data_o  = data_q;

endmodule

`default_nettype wire
