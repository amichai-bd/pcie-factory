// Types and constants of the SKP elastic buffer
// (wiki/src/rtl/pcs_elastic/MAS.md).
`default_nettype none

package pcie_pcs_elastic_pkg;

  // One raw 10-bit symbol, bit 0 first on the line.
  localparam int unsigned SYM_W = 10;
  typedef logic [SYM_W-1:0] t_sym10;

  // Raw codes of COM (K28.5) and SKP (K28.0), bit 0 first, in both columns
  // (wiki/src/arch/pcs/8B10B.md, K symbols). The two columns are each
  // other's complement, so these four values also match an inverted lane.
  localparam t_sym10 COM_RD_MINUS = 10'h17C;  // line order 0011111010
  localparam t_sym10 COM_RD_PLUS  = 10'h283;  // line order 1100000101
  localparam t_sym10 SKP_RD_MINUS = 10'h0BC;  // line order 0011110100
  localparam t_sym10 SKP_RD_PLUS  = 10'h343;  // line order 1100001011

  // One FIFO word (25 bits). sym0 is the earlier symbol; ovf marks the
  // first word written after a lost one and belongs to sym0.
  typedef struct packed {
    logic       ovf;     // a word was lost just before this one
    logic [1:0] rem;     // rem[i]: a SKP was removed just before sym i
    logic       gen;     // lock generation
    logic       lock;    // both symbols are under the same lock
    t_sym10     sym1;
    t_sym10     sym0;
  } t_eb_word;

  // One symbol on the read side, with the flags of its word.
  typedef struct packed {
    logic   ovf;
    logic   rem;
    logic   gen;
    logic   lock;
    t_sym10 sym;
  } t_eb_sym;

  function automatic logic is_com(t_sym10 s);
    return (s == COM_RD_MINUS) || (s == COM_RD_PLUS);
  endfunction

  function automatic logic is_skp(t_sym10 s);
    return (s == SKP_RD_MINUS) || (s == SKP_RD_PLUS);
  endfunction

endpackage

`default_nettype wire
