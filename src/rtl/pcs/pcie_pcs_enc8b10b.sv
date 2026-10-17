// Two-symbol 8b/10b encoder with the transmit running disparity: two bytes
// and K flags per clk_i cycle in, two 10-bit symbols out two cycles later.
// Interfaces and behavior: wiki/src/rtl/pcs/MAS.md#encoder; the code:
// wiki/src/arch/pcs/8B10B.md.
`default_nettype none

`include "common_macros.svh"

module pcie_pcs_enc8b10b (
  input  logic                   clk_i,
  input  logic                   rst_i,    // synchronous, active high: RD negative, nothing in flight
  input  logic                   valid_i,  // a word to send; 0: no word, RD holds
  input  pcie_pcs_pkg::t_tx_word word_i,   // two symbols, sym0 first on the line; TxCompliance
  output logic                   valid_o,  // valid_i two cycles later
  output pcie_pcs_pkg::t_word    data_o    // [9:0] first, bit 0 first on the line
);

  // Stage 1: input register.
  logic                   in_valid_q;
  pcie_pcs_pkg::t_tx_word in_word_q;

  `COMMON_DFF_RST(in_valid_q, valid_i, clk_i, rst_i)
  `COMMON_DFF(in_word_q, word_i, clk_i)

  // Stage 2: encode both lanes, lane 0 first, and register the symbols.
  logic                 rd_q;      // transmit running disparity, 1 positive
  logic                 rd_lane0;  // RD before the sym0 symbol
  logic                 rd_mid;    // RD between the two symbols
  logic                 rd_next;   // RD after the sym1 symbol
  pcie_pcs_pkg::t_sym10 code0;
  pcie_pcs_pkg::t_sym10 code1;
  pcie_pcs_pkg::t_word  codes;
  pcie_pcs_pkg::t_k_byte sym0;
  pcie_pcs_pkg::t_k_byte sym1;
  logic                 out_valid_q;
  pcie_pcs_pkg::t_word  out_data_q;

  assign rd_lane0 = in_word_q.compliance ? 1'b0 : rd_q;
  assign sym0     = in_word_q.sym0;
  assign sym1     = in_word_q.sym1;

  pcie_pcs_enc8b10b_sym u_sym0 (
    .sym_i  (sym0),
    .rd_i   (rd_lane0),
    .code_o (code0),
    .rd_o   (rd_mid)
  );

  // Lane 1 is encoded at both running disparities in parallel with lane 0,
  // and lane 0's RD picks one: the RD chain is a select, not a second table
  // in series (MAS, Timing).
  logic                 rd_lo1;    // RD after sym1 sent at RD-
  logic                 rd_hi1;    // RD after sym1 sent at RD+
  pcie_pcs_pkg::t_sym10 code_lo1;
  pcie_pcs_pkg::t_sym10 code_hi1;
  logic                 rd_neg;
  logic                 rd_pos;

  assign rd_neg = 1'b0;
  assign rd_pos = 1'b1;

  pcie_pcs_enc8b10b_sym u_sym1_lo (
    .sym_i  (sym1),
    .rd_i   (rd_neg),
    .code_o (code_lo1),
    .rd_o   (rd_lo1)
  );

  pcie_pcs_enc8b10b_sym u_sym1_hi (
    .sym_i  (sym1),
    .rd_i   (rd_pos),
    .code_o (code_hi1),
    .rd_o   (rd_hi1)
  );

  assign code1   = rd_mid ? code_hi1 : code_lo1;
  assign rd_next = rd_mid ? rd_hi1 : rd_lo1;

  assign codes = {code1, code0};

  `COMMON_DFF_RST_EN(rd_q, rd_next, clk_i, in_valid_q, rst_i, 1'b0)
  `COMMON_DFF_RST(out_valid_q, in_valid_q, clk_i, rst_i)
  `COMMON_DFF_EN(out_data_q, codes, clk_i, in_valid_q)

  assign valid_o = out_valid_q;
  assign data_o  = out_data_q;

  // Invariants of the code (wiki/src/arch/pcs/8B10B.md): a symbol holds five
  // ones and keeps RD, or six ones from RD- to RD+, or four from RD+ to RD-.
  `COMMON_ASSERT_CLOCKED_WHEN(a_lane0_disparity, clk_i, rst_i, in_valid_q,
    (($countones(code0) == 5) && (rd_mid == rd_lane0))
    || (($countones(code0) == 6) && !rd_lane0 && rd_mid)
    || (($countones(code0) == 4) && rd_lane0 && !rd_mid))
  `COMMON_ASSERT_CLOCKED_WHEN(a_lane1_disparity, clk_i, rst_i, in_valid_q,
    (($countones(code1) == 5) && (rd_next == rd_mid))
    || (($countones(code1) == 6) && !rd_mid && rd_next)
    || (($countones(code1) == 4) && rd_mid && !rd_next))

  // No run of more than five equal bits across the two symbols.
  logic run6;
  always_comb begin
    run6 = 1'b0;
    for (int unsigned i = 0; i <= 14; i++) begin
      if ((codes[i +: 6] == 6'b000000) || (codes[i +: 6] == 6'b111111)) begin
        run6 = 1'b1;
      end
    end
  end
  `COMMON_ASSERT_CLOCKED_WHEN(a_run_length, clk_i, rst_i, in_valid_q, !run6)

endmodule

`default_nettype wire
