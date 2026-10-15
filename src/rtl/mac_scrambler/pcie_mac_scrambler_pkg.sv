// Constants and pure helpers of the scrambler
// (wiki/src/rtl/mac_scrambler/MAS.md; rules in wiki/src/arch/mac/SYMBOLS.md).
// Adapted from the seed pcie_top_lfsr_pkg (same repository, Apache-2.0).
`default_nettype none

package pcie_mac_scrambler_pkg;

  localparam int unsigned LFSR_W = 16;
  localparam int unsigned BYTE_W = 8;

  typedef logic [LFSR_W-1:0] t_lfsr;
  typedef logic [BYTE_W-1:0] t_byte;

  // G(X) = X^16 + X^5 + X^4 + X^3 + 1: bit 15 feeds back into bits 0, 3, 4
  // and 5 [cite: PCIe Base Spec, rev 3.0, §4.2.1.3].
  localparam t_lfsr LFSR_TAPS = 16'h0039;

  // Key byte of a state: key bit i is state bit 15 before shift i.
  function automatic t_byte lfsr_key(t_lfsr state);
    t_lfsr s;
    t_byte key;
    s   = state;
    key = '0;
    for (int unsigned i = 0; i < BYTE_W; i++) begin
      key[i] = s[LFSR_W-1];
      s      = {s[LFSR_W-2:0], 1'b0} ^ (s[LFSR_W-1] ? LFSR_TAPS : '0);
    end
    return key;
  endfunction

  // State after eight shifts (one symbol's advance).
  function automatic t_lfsr lfsr_advance(t_lfsr state);
    t_lfsr s;
    s = state;
    for (int unsigned i = 0; i < BYTE_W; i++) begin
      s = {s[LFSR_W-2:0], 1'b0} ^ (s[LFSR_W-1] ? LFSR_TAPS : '0);
    end
    return s;
  endfunction

endpackage

`default_nettype wire
