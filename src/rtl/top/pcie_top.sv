// Byte-stream scrambler: data_o = data_i ^ LFSR key, one cycle later.
// Behavior: wiki/src/arch/top/HAS.md; structure: wiki/src/rtl/top/MAS.md.
`default_nettype none

`include "common_macros.svh"

module pcie_top (
  input  logic                     clk_i,
  input  logic                     rst_i,     // synchronous, active high
  input  logic                     valid_i,   // data_i holds a byte to scramble; advances the LFSR
  input  pcie_top_lfsr_pkg::t_byte data_i,
  input  logic                     reseed_i,  // seed key from the next byte; this cycle's byte uses the current key
  output logic                     valid_o,
  output pcie_top_lfsr_pkg::t_byte data_o
);

  pcie_top_lfsr_pkg::t_byte key;
  pcie_top_lfsr_pkg::t_byte data_d;
  pcie_top_lfsr_pkg::t_byte data_q;
  logic                     valid_q;

  pcie_top_lfsr u_lfsr (
    .clk_i     (clk_i),
    .rst_i     (rst_i),
    .reseed_i  (reseed_i),
    .advance_i (valid_i),
    .key_o     (key)
  );

  assign data_d = data_i ^ key;

  `COMMON_DFF_RST(valid_q, valid_i, clk_i, rst_i)

  // Data is qualified by valid_o, so it needs no reset.
  `COMMON_DFF_EN(data_q, data_d, clk_i, valid_i)

  assign valid_o = valid_q;
  assign data_o  = data_q;

endmodule

`default_nettype wire
