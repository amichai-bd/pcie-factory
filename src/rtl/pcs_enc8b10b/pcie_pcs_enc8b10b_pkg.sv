// Types and constants of the 8b/10b encoder
// (wiki/src/rtl/pcs_enc8b10b/MAS.md). K_EDB is the EDB row of the K-symbol
// table in wiki/src/arch/pcs/8B10B.md#k-symbols; the decoder's package
// holds the same value by design (#133 tracks a shared package-only block).
`default_nettype none

package pcie_pcs_enc8b10b_pkg;

  typedef logic [9:0] t_sym10;   // one symbol, bit 0 (line bit a) first

  localparam logic [7:0] K_EDB = 8'hFE;  // K30.7: sent for a K flag on no valid K code

endpackage

`default_nettype wire
