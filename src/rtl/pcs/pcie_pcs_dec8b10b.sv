// 8b/10b decoder of the pcs receive path: RxPolarity inversion, then two
// symbols per clock decoded with the running disparity, code and disparity
// errors and EDB substitution. Interface and behavior:
// wiki/src/rtl/pcs/MAS.md#decoder; the code: wiki/src/arch/pcs/8B10B.md.
`default_nettype none

`include "common_macros.svh"

module pcie_pcs_dec8b10b (
  input  logic        clk_i,
  input  logic        rst_i,           // synchronous, active high: RD unknown
  input  logic        in_valid_i,      // a word is presented this cycle
  input  logic [19:0] in_sym_i,        // [9:0] earlier symbol, bit 0 first on the line
  input  logic [1:0]  in_lock_i,       // per symbol: received under symbol lock
  input  logic [1:0]  in_gen_i,        // per symbol: lock-generation bit
  input  logic        polarity_i,      // invert every received bit this cycle
  output logic [15:0] out_data_o,      // [7:0] is the earlier byte; EDB on a code error
  output logic [1:0]  out_k_o,
  output logic [1:0]  out_code_err_o,
  output logic [1:0]  out_disp_err_o
);

  // Running-disparity state, after the last symbol of the last word.
  logic rd_known_q;   // RD is known (the last symbol was locked)
  logic rd_known_d;
  logic rd_pos_q;     // RD positive
  logic rd_pos_d;
  logic gen_q;        // lock generation of the last symbol
  logic gen_d;
  logic pol_q;        // polarity_i in the previous cycle
  logic pol_d;

  logic                              pol_change;
  pcie_pcs_pkg::t_sym10     sym0;
  pcie_pcs_pkg::t_sym10     sym1;
  logic                              known0;
  logic                              known1;
  pcie_pcs_pkg::t_dec       dec0;
  pcie_pcs_pkg::t_dec       dec1;

  // RxPolarity: inversion applies in the cycle polarity_i is 1; a change
  // makes the RD unknown from that cycle's word.
  assign pol_change = polarity_i != pol_q;
  assign sym0 = in_sym_i[9:0]   ^ {10{polarity_i}};
  assign sym1 = in_sym_i[19:10] ^ {10{polarity_i}};

  // The RD is known for a locked symbol whose predecessor was a locked
  // symbol of the same lock generation, with no polarity change between.
  assign known0 = rd_known_q && !pol_change && in_lock_i[0] && (in_gen_i[0] == gen_q);
  assign known1 = in_lock_i[0] && in_lock_i[1] && (in_gen_i[1] == in_gen_i[0]);

  assign dec0 = pcie_pcs_pkg::decode(sym0, known0, rd_pos_q);
  assign dec1 = pcie_pcs_pkg::decode(sym1, known1, dec0.rd_pos);

  assign out_data_o     = {dec1.data, dec0.data};
  assign out_k_o        = {dec1.k, dec0.k};
  assign out_code_err_o = {dec1.code_err, dec0.code_err};
  assign out_disp_err_o = {dec1.disp_err, dec0.disp_err};

  always_comb begin
    rd_known_d = rd_known_q && !pol_change;
    rd_pos_d   = rd_pos_q;
    gen_d      = gen_q;
    if (in_valid_i) begin
      rd_known_d = in_lock_i[1];
      rd_pos_d   = dec1.rd_pos;
      gen_d      = in_gen_i[1];
    end
  end

  assign pol_d = polarity_i;

  `COMMON_DFF_RST(rd_known_q, rd_known_d, clk_i, rst_i)
  `COMMON_DFF(rd_pos_q, rd_pos_d, clk_i)
  `COMMON_DFF(gen_q, gen_d, clk_i)
  `COMMON_DFF_RST(pol_q, pol_d, clk_i, rst_i)

  // Invariants (MAS, Behavior): one error class per symbol; a code error
  // always yields EDB as K; a disparity error needs a known RD.
  `COMMON_ASSERT_NEVER(a_one_error_class, clk_i, rst_i,
                       |(out_code_err_o & out_disp_err_o))
  `COMMON_ASSERT_NEVER(a_code_err_edb, clk_i, rst_i,
                       (dec0.code_err && (dec0.data != pcie_pcs_pkg::K_EDB || !dec0.k)) ||
                       (dec1.code_err && (dec1.data != pcie_pcs_pkg::K_EDB || !dec1.k)))
  `COMMON_ASSERT_NEVER(a_disp_err_known_rd, clk_i, rst_i,
                       (dec0.disp_err && !known0) || (dec1.disp_err && !known1))

endmodule

`default_nettype wire
