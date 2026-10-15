// Types and widths of the comma aligner (wiki/src/rtl/pcs_align/MAS.md).
`default_nettype none

package pcie_pcs_align_pkg;

  localparam int unsigned SYM_W    = 10;            // one 10-bit symbol
  localparam int unsigned WORD_W   = 2 * SYM_W;     // two symbols per ser_rx_clk_i cycle
  localparam int unsigned WINDOW_W = 2 * WORD_W;    // previous word, then the current one
  localparam int unsigned COMMA_W  = 7;             // bits a to g of a symbol
  localparam int unsigned OFFSET_W = 4;             // symbol boundary 0 to 9

  typedef logic [SYM_W-1:0]    t_sym10;
  typedef logic [WORD_W-1:0]   t_word;
  typedef logic [WINDOW_W-1:0] t_window;
  typedef logic [COMMA_W-1:0]  t_comma;
  typedef logic [OFFSET_W-1:0] t_offset;

endpackage

`default_nettype wire
