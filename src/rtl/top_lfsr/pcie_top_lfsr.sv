// Scrambler LFSR: one key byte per state, eight shifts per advance
// (wiki/src/rtl/top_lfsr/MAS.md).
`default_nettype none

`include "common_macros.svh"

module pcie_top_lfsr (
  input  logic  clk_i,
  input  logic  rst_i,      // synchronous, active high: state <= LFSR_SEED
  input  logic  reseed_i,   // state <= LFSR_SEED; wins over advance_i
  input  logic  advance_i,  // state <= state shifted eight times
  output pcie_top_lfsr_pkg::t_byte key_o       // key for the current state, bit 0 first
);

  pcie_top_lfsr_pkg::t_lfsr state_q;
  pcie_top_lfsr_pkg::t_lfsr state_d;
  pcie_top_lfsr_pkg::t_lfsr shifted;  // state after eight serial shifts
  pcie_top_lfsr_pkg::t_byte key;

  // Eight serial shifts unrolled; key bit i is state bit 15 before shift i.
  always_comb begin
    shifted = state_q;
    key     = '0;
    for (int unsigned i = 0; i < pcie_top_lfsr_pkg::BYTE_W; i++) begin
      key[i]  = shifted[pcie_top_lfsr_pkg::LFSR_W-1];
      shifted = {shifted[pcie_top_lfsr_pkg::LFSR_W-2:0], 1'b0}
              ^ (shifted[pcie_top_lfsr_pkg::LFSR_W-1] ? pcie_top_lfsr_pkg::LFSR_TAPS : '0);
    end
  end

  always_comb begin
    state_d = state_q;
    if (reseed_i) begin
      state_d = pcie_top_lfsr_pkg::LFSR_SEED;
    end else if (advance_i) begin
      state_d = shifted;
    end
  end

  `COMMON_DFF_RST_VAL(state_q, state_d, clk_i, rst_i, pcie_top_lfsr_pkg::LFSR_SEED)

  assign key_o = key;

endmodule

`default_nettype wire
