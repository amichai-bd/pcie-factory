// Types and constants of the scrambler LFSR (wiki/src/rtl/top_lfsr/MAS.md).
`default_nettype none

package pcie_top_lfsr_pkg;

  localparam int unsigned LFSR_W = 16;
  localparam int unsigned BYTE_W = 8;

  typedef logic [LFSR_W-1:0] t_lfsr;
  typedef logic [BYTE_W-1:0] t_byte;

  // G(X) = X^16 + X^5 + X^4 + X^3 + 1: bit 15 feeds back into bits 0, 3, 4, 5.
  localparam t_lfsr LFSR_TAPS = 16'h0039;
  localparam t_lfsr LFSR_SEED = 16'hFFFF;

endpackage

`default_nettype wire
