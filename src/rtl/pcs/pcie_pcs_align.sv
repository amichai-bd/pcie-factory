// Comma alignment and symbol lock in the ser_rx_clk_i domain
// (wiki/src/rtl/pcs/MAS.md#aligner, wiki/src/arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock).
`default_nettype none

`include "common_macros.svh"

module pcie_pcs_align (
  input  logic                   clk_i,        // ser_rx_clk_i of the pcs
  input  logic                   rst_i,        // RX-domain reset, active high
  input  pcie_pcs_pkg::t_word    data_i,       // twenty received bits, bit 0 earliest
  input  logic                   elec_idle_i,  // the line is in electrical idle
  input  logic                   relock_i,     // one-cycle relock request
  output pcie_pcs_pkg::t_rx_pair rx_o          // two symbols at the boundary, sym0 first,
                                               // both tagged with the word's lock and generation
);

  // The two comma forms, bit 0 first on the line: line order 0011111 and
  // 1100000 (the pcs HAS package rows COMMA_MINUS and COMMA_PLUS,
  // wiki/src/arch/pcs/8B10B.md#comma). Only this module matches commas.
  localparam pcie_pcs_pkg::t_comma  COMMA_MINUS = 7'b1111100;
  localparam pcie_pcs_pkg::t_comma  COMMA_PLUS  = 7'b0000011;
  localparam pcie_pcs_pkg::t_offset OFFSET_MAX  = 4'd9;   // last symbol boundary

  // Input register: the current word and the one before it.
  pcie_pcs_pkg::t_word   in_q;
  pcie_pcs_pkg::t_word   in_prev_q;
  logic                  idle_q;
  logic                  idle_prev_q;

  // Lock state and the output word.
  pcie_pcs_pkg::t_align  state_q;
  pcie_pcs_pkg::t_align  state_d;
  pcie_pcs_pkg::t_word   sym_q;
  pcie_pcs_pkg::t_word   sym_d;

  pcie_pcs_pkg::t_window window;      // in_prev_q then in_q, earliest bit first
  logic [pcie_pcs_pkg::WORD_W-1:0] comma_at;   // a comma starts at window bit p
  logic                  found;       // a comma at some offset 0 to 19
  pcie_pcs_pkg::t_offset first_off;   // lowest such offset, mod 10
  logic                  keep;        // lock held through this edge
  logic                  hunt_ok;     // neither word of the window is idle
  logic                  acquire;     // lock taken at this edge
  logic [5:0]            base;        // state_d.offset as a window bit index

  assign window = {in_q, in_prev_q};

  always_comb begin
    comma_at = '0;
    for (int unsigned p = 0; p < pcie_pcs_pkg::WORD_W; p++) begin
      comma_at[p] = (window[p +: pcie_pcs_pkg::COMMA_W] == COMMA_MINUS)
                 || (window[p +: pcie_pcs_pkg::COMMA_W] == COMMA_PLUS);
    end
  end

  // Lowest offset wins: scan from the top so lower offsets overwrite.
  always_comb begin
    first_off = '0;
    for (int p = pcie_pcs_pkg::WORD_W - 1; p >= 0; p--) begin
      if (comma_at[p]) begin
        first_off = pcie_pcs_pkg::t_offset'(p % pcie_pcs_pkg::SYM_W);
      end
    end
  end

  assign found   = |comma_at;
  assign keep    = state_q.lock && !relock_i && !idle_q;
  assign hunt_ok = !idle_q && !idle_prev_q;
  assign acquire = !keep && hunt_ok && found;

  always_comb begin
    state_d.lock   = keep || acquire;
    state_d.gen    = state_q.gen ^ acquire;
    state_d.offset = acquire ? first_off : state_q.offset;
  end

  assign base  = 6'(state_d.offset);
  assign sym_d = window[base +: pcie_pcs_pkg::WORD_W];

  `COMMON_DFF(in_q, data_i, clk_i)
  `COMMON_DFF(in_prev_q, in_q, clk_i)
  `COMMON_DFF_RST_VAL(idle_q, elec_idle_i, clk_i, rst_i, 1'b1)
  `COMMON_DFF_RST_VAL(idle_prev_q, idle_q, clk_i, rst_i, 1'b1)
  `COMMON_DFF_RST(state_q, state_d, clk_i, rst_i)
  `COMMON_DFF(sym_q, sym_d, clk_i)

  assign rx_o = '{sym1: '{lock: state_q.lock, gen: state_q.gen, code: sym_q[19:10]},
                  sym0: '{lock: state_q.lock, gen: state_q.gen, code: sym_q[9:0]}};

  // The lowest comma offset, scanned upwards, for the assertion that lock
  // is taken at the lowest one (the priority scan above runs downwards).
  logic                  low_found;
  pcie_pcs_pkg::t_offset low_off;

  always_comb begin
    low_found = 1'b0;
    low_off   = '0;
    for (int unsigned p = 0; p < pcie_pcs_pkg::WORD_W; p++) begin
      if (comma_at[p] && !low_found) begin
        low_found = 1'b1;
        low_off   = pcie_pcs_pkg::t_offset'(p % pcie_pcs_pkg::SYM_W);
      end
    end
  end

  // Invariants (MAS, Behavior).
  `COMMON_ASSERT_NEVER(a_lock_at_lowest_comma, clk_i, rst_i,
                       acquire && (!low_found || (state_d.offset != low_off)))
  `COMMON_ASSERT_NEVER(a_offset_in_symbol, clk_i, rst_i,
                       state_q.offset > OFFSET_MAX)
  `COMMON_ASSERT_NEVER(a_no_lock_from_idle, clk_i, rst_i, acquire && !hunt_ok)
  `COMMON_ASSERT_NEVER(a_relock_drops_lock, clk_i, rst_i, relock_i && state_d.lock && !acquire)
  `COMMON_ASSERT_NEVER(a_gen_flips_only_on_lock, clk_i, rst_i, (state_d.gen != state_q.gen) && !acquire)
  `COMMON_ASSERT_NEVER(a_lock_word_holds_comma, clk_i, rst_i,
                       acquire && !(sym_d[6:0] == COMMA_MINUS
                                 || sym_d[6:0] == COMMA_PLUS
                                 || sym_d[16:10] == COMMA_MINUS
                                 || sym_d[16:10] == COMMA_PLUS))

endmodule

`default_nettype wire
