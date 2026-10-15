// Scrambler and descrambler, two symbols per clk_i cycle: one instance per
// direction (wiki/src/rtl/mac/MAS.md#scrambler; rules in
// wiki/src/arch/mac/SYMBOLS.md#scrambling). Adapted from the seed
// pcie_top_lfsr (same repository, Apache-2.0).
`default_nettype none

`include "common_macros.svh"

module pcie_mac_scrambler (
  input  logic            clk_i,
  input  logic            rst_i,    // synchronous, active high: LFSR to the seed
  input  logic            valid_i,  // a word to process; 0 holds the LFSR
  input  common_pkg::t_word word_i,   // two symbols, [7:0] first on the line
  input  logic [1:0]      scr_i,    // bit i: XOR data symbol i with its key
  output logic            valid_o,  // word_o holds a processed word
  output common_pkg::t_word word_o
);

  localparam int unsigned SYMS   = 2;        // symbols per clk_i cycle, [7:0] first
  localparam int unsigned BYTE_W = pcie_mac_pkg::BYTE_W;
  localparam pcie_mac_pkg::t_lfsr LFSR_SEED = 16'hFFFF;

  logic [15:0] in_data;
  logic [1:0]  in_datak;
  assign in_data  = word_i.data;
  assign in_datak = word_i.datak;

  pcie_mac_pkg::t_lfsr            state_q;     // LFSR before symbol 0
  pcie_mac_pkg::t_lfsr            state_next;  // LFSR after the last symbol
  pcie_mac_pkg::t_lfsr [SYMS:0]   lane_state;  // LFSR before symbol i; [SYMS] after all
  logic [SYMS-1:0]                          sym_com;
  logic [SYMS-1:0]                          sym_skp;
  logic [SYMS-1:0]                          sym_scr;     // data symbols to XOR
  logic [15:0]                              data_d;
  logic                                     valid_q;
  logic [15:0]                              data_q;
  logic [1:0]                               datak_q;

  generate
    for (genvar i = 0; i < SYMS; i++) begin : g_lane
      assign sym_com[i] = in_datak[i] && (in_data[BYTE_W*i +: BYTE_W] == common_pkg::SYM_COM);
      assign sym_skp[i] = in_datak[i] && (in_data[BYTE_W*i +: BYTE_W] == common_pkg::SYM_SKP);
    end
  endgenerate

  // K symbols are never changed, whatever scr_i says.
  assign sym_scr = scr_i & ~in_datak;

  // Symbols in line order: COM loads the seed, SKP holds, every other
  // symbol advances by eight shifts; a scrambled data symbol is XORed with
  // the key of the state before its advance.
  always_comb begin
    lane_state    = '0;
    lane_state[0] = state_q;
    data_d        = in_data;
    for (int unsigned i = 0; i < SYMS; i++) begin
      if (sym_scr[i]) begin
        data_d[BYTE_W*i +: BYTE_W] = in_data[BYTE_W*i +: BYTE_W]
                                   ^ pcie_mac_pkg::lfsr_key(lane_state[i]);
      end
      if (sym_com[i]) begin
        lane_state[i+1] = LFSR_SEED;
      end else if (sym_skp[i]) begin
        lane_state[i+1] = lane_state[i];
      end else begin
        lane_state[i+1] = pcie_mac_pkg::lfsr_advance(lane_state[i]);
      end
    end
  end

  assign state_next = lane_state[SYMS];

  `COMMON_DFF_RST_EN(state_q, state_next, clk_i, valid_i, rst_i, LFSR_SEED)
  `COMMON_DFF_RST(valid_q, valid_i, clk_i, rst_i)
  `COMMON_DFF_EN(data_q, data_d, clk_i, valid_i)
  `COMMON_DFF_EN(datak_q, in_datak, clk_i, valid_i)

  assign valid_o      = valid_q;
  assign word_o.data  = data_q;
  assign word_o.datak = datak_q;

  // The all-zero state is unreachable from the seed.
  `COMMON_ASSERT_NEVER(a_lfsr_nonzero, clk_i, rst_i, state_q == '0)
  // A COM in the last symbol leaves the seed for the next word.
  `COMMON_ASSERT(a_com_reseeds, clk_i, rst_i,
                 valid_i && sym_com[SYMS-1] |=> state_q == LFSR_SEED)
  // A word of SKP symbols only leaves the LFSR unchanged.
  `COMMON_ASSERT(a_skp_holds, clk_i, rst_i, valid_i && (&sym_skp) |=> $stable(state_q))
  // Per symbol, in either lane: a COM loads the seed, a SKP holds, any
  // other symbol advances by eight shifts (the lane chain).
  generate
    for (genvar i = 0; i < SYMS; i++) begin : g_lane_check
      `COMMON_ASSERT_NEVER(a_com_reseeds_lane, clk_i, rst_i,
                           valid_i && sym_com[i] && lane_state[i+1] != LFSR_SEED)
      `COMMON_ASSERT_NEVER(a_skp_holds_lane, clk_i, rst_i,
                           valid_i && sym_skp[i] && lane_state[i+1] != lane_state[i])
      `COMMON_ASSERT_NEVER(a_other_advances_lane, clk_i, rst_i,
                           valid_i && !sym_com[i] && !sym_skp[i]
                           && lane_state[i+1] != pcie_mac_pkg::lfsr_advance(lane_state[i]))
    end
  endgenerate
  // Only data symbols with scr_i set may change.
  `COMMON_ASSERT_NEVER(a_unscrambled_pass, clk_i, rst_i,
                       (sym_scr[0] == 1'b0 && data_d[7:0] != in_data[7:0])
                       || (sym_scr[1] == 1'b0 && data_d[15:8] != in_data[15:8]))

endmodule

`default_nettype wire
