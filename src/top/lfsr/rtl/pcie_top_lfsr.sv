// Scrambler LFSR: one key byte per state, eight shifts per advance
// (wiki/src/top/lfsr/HAS.md).
`default_nettype none

module pcie_top_lfsr
  import pcie_top_lfsr_pkg::*;
(
  input  logic  clk_i,
  input  logic  rst_i,      // synchronous, active high: state <= LFSR_SEED
  input  logic  reseed_i,   // state <= LFSR_SEED; wins over advance_i
  input  logic  advance_i,  // state <= state shifted eight times
  output t_byte key_o       // key for the current state, bit 0 first
);

  t_lfsr state_q;
  t_lfsr state_d;
  t_lfsr shifted;  // state after eight serial shifts
  t_byte key;

  // Eight serial shifts unrolled; key bit i is state bit 15 before shift i.
  always_comb begin
    shifted = state_q;
    key     = '0;
    for (int unsigned i = 0; i < BYTE_W; i++) begin
      key[i]  = shifted[LFSR_W-1];
      shifted = {shifted[LFSR_W-2:0], 1'b0} ^ (shifted[LFSR_W-1] ? LFSR_TAPS : '0);
    end
  end

  always_comb begin
    state_d = state_q;
    if (reseed_i) begin
      state_d = LFSR_SEED;
    end else if (advance_i) begin
      state_d = shifted;
    end
  end

  always_ff @(posedge clk_i) begin
    if (rst_i) state_q <= LFSR_SEED;
    else       state_q <= state_d;
  end

  assign key_o = key;

endmodule

`default_nettype wire
