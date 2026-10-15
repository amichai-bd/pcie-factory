// Comma alignment and symbol lock in the ser_rx_clk_i domain
// (wiki/src/rtl/pcs_align/MAS.md, wiki/src/arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock).
`default_nettype none

`include "common_macros.svh"

module pcie_pcs_align (
  input  logic                        clk_i,          // ser_rx_clk_i of the pcs
  input  logic                        rst_i,          // RX-domain reset, active high
  input  pcie_pcs_align_pkg::t_word   data_i,         // twenty received bits, bit 0 earliest
  input  logic                        elec_idle_i,    // the line is in electrical idle
  input  logic                        relock_i,       // one-cycle relock request
  output pcie_pcs_align_pkg::t_word   sym_o,          // two symbols at the boundary, [9:0] first
  output logic                        lock_o,         // sym_o is under symbol lock
  output logic                        gen_o           // lock generation, flips at every lock
);

  // The two comma forms, bit 0 first on the line: line order 0011111 and
  // 1100000 (the pcs HAS package rows COMMA_MINUS and COMMA_PLUS,
  // wiki/src/arch/pcs/8B10B.md#comma). Only this module matches commas.
  localparam pcie_pcs_align_pkg::t_comma COMMA_MINUS = 7'b1111100;
  localparam pcie_pcs_align_pkg::t_comma COMMA_PLUS  = 7'b0000011;
  localparam pcie_pcs_align_pkg::t_offset OFFSET_MAX = 4'd9;   // last symbol boundary

  // Input register: the current word and the one before it.
  pcie_pcs_align_pkg::t_word   in_q;
  pcie_pcs_align_pkg::t_word   in_prev_q;
  logic                        idle_q;
  logic                        idle_prev_q;

  // Lock state and the output word.
  logic                        lock_q;
  logic                        lock_d;
  logic                        gen_q;
  logic                        gen_d;
  pcie_pcs_align_pkg::t_offset offset_q;
  pcie_pcs_align_pkg::t_offset offset_d;
  pcie_pcs_align_pkg::t_word   sym_q;
  pcie_pcs_align_pkg::t_word   sym_d;

  pcie_pcs_align_pkg::t_window window;      // in_prev_q then in_q, earliest bit first
  logic [pcie_pcs_align_pkg::WORD_W-1:0] comma_at;   // a comma starts at window bit p
  logic                        found;       // a comma at some offset 0 to 19
  pcie_pcs_align_pkg::t_offset first_off;   // lowest such offset, mod 10
  logic                        keep;        // lock held through this edge
  logic                        hunt_ok;     // neither word of the window is idle
  logic                        acquire;     // lock taken at this edge
  logic [5:0]                  base;        // offset_d as a window bit index

  assign window = {in_q, in_prev_q};

  always_comb begin
    comma_at = '0;
    for (int unsigned p = 0; p < pcie_pcs_align_pkg::WORD_W; p++) begin
      comma_at[p] = (window[p +: pcie_pcs_align_pkg::COMMA_W] == COMMA_MINUS)
                 || (window[p +: pcie_pcs_align_pkg::COMMA_W] == COMMA_PLUS);
    end
  end

  // Lowest offset wins: scan from the top so lower offsets overwrite.
  always_comb begin
    first_off = '0;
    for (int p = pcie_pcs_align_pkg::WORD_W - 1; p >= 0; p--) begin
      if (comma_at[p]) begin
        first_off = pcie_pcs_align_pkg::t_offset'(p % pcie_pcs_align_pkg::SYM_W);
      end
    end
  end

  assign found   = |comma_at;
  assign keep    = lock_q && !relock_i && !idle_q;
  assign hunt_ok = !idle_q && !idle_prev_q;
  assign acquire = !keep && hunt_ok && found;

  always_comb begin
    lock_d   = keep || acquire;
    gen_d    = gen_q ^ acquire;
    offset_d = acquire ? first_off : offset_q;
  end

  assign base  = 6'(offset_d);
  assign sym_d = window[base +: pcie_pcs_align_pkg::WORD_W];

  `COMMON_DFF(in_q, data_i, clk_i)
  `COMMON_DFF(in_prev_q, in_q, clk_i)
  `COMMON_DFF_RST_VAL(idle_q, elec_idle_i, clk_i, rst_i, 1'b1)
  `COMMON_DFF_RST_VAL(idle_prev_q, idle_q, clk_i, rst_i, 1'b1)
  `COMMON_DFF_RST(lock_q, lock_d, clk_i, rst_i)
  `COMMON_DFF_RST(gen_q, gen_d, clk_i, rst_i)
  `COMMON_DFF_RST(offset_q, offset_d, clk_i, rst_i)
  `COMMON_DFF(sym_q, sym_d, clk_i)

  assign sym_o  = sym_q;
  assign lock_o = lock_q;
  assign gen_o  = gen_q;

  // Invariants (MAS, Behavior).
  `COMMON_ASSERT_NEVER(a_offset_in_symbol, clk_i, rst_i,
                       offset_q > OFFSET_MAX)
  `COMMON_ASSERT_NEVER(a_no_lock_from_idle, clk_i, rst_i, acquire && !hunt_ok)
  `COMMON_ASSERT_NEVER(a_relock_drops_lock, clk_i, rst_i, relock_i && lock_d && !acquire)
  `COMMON_ASSERT_NEVER(a_gen_flips_only_on_lock, clk_i, rst_i, (gen_d != gen_q) && !acquire)
  `COMMON_ASSERT_NEVER(a_lock_word_holds_comma, clk_i, rst_i,
                       acquire && !(sym_d[6:0] == COMMA_MINUS
                                 || sym_d[6:0] == COMMA_PLUS
                                 || sym_d[16:10] == COMMA_MINUS
                                 || sym_d[16:10] == COMMA_PLUS))

endmodule

`default_nettype wire
