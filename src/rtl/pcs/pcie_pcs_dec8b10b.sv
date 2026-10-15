// 8b/10b decoder of the pcs receive path: RxPolarity inversion, then two
// symbols per clock decoded with the running disparity, code and disparity
// errors and EDB substitution. Interface and behavior:
// wiki/src/rtl/pcs/MAS.md#decoder; the code: wiki/src/arch/pcs/8B10B.md.
`default_nettype none

`include "common_macros.svh"

module pcie_pcs_dec8b10b (
  input  logic                    clk_i,
  input  logic                    rst_i,       // synchronous, active high: RD unknown
  input  logic                    in_valid_i,  // a word is presented this cycle
  input  pcie_pcs_pkg::t_rx_pair  in_i,        // two raw symbols with their lock tags
  input  logic                    polarity_i,  // invert every received bit this cycle
  output pcie_pcs_pkg::t_dec_pair out_o        // sym0 the earlier; EDB on a code error
);

  // Running-disparity state, after the last symbol of the last word.
  pcie_pcs_pkg::t_dec_state state_q;
  pcie_pcs_pkg::t_dec_state state_d;

  logic                 pol_change;
  pcie_pcs_pkg::t_sym10 sym0;
  pcie_pcs_pkg::t_sym10 sym1;
  logic                 known0;
  logic                 known1;
  pcie_pcs_pkg::t_dec   dec0;
  pcie_pcs_pkg::t_dec   dec1;

  // RxPolarity: inversion applies in the cycle polarity_i is 1; a change
  // makes the RD unknown from that cycle's word.
  assign pol_change = polarity_i != state_q.pol;
  assign sym0 = in_i.sym0.code ^ {10{polarity_i}};
  assign sym1 = in_i.sym1.code ^ {10{polarity_i}};

  // The RD is known for a locked symbol whose predecessor was a locked
  // symbol of the same lock generation, with no polarity change between.
  assign known0 = state_q.rd_known && !pol_change && in_i.sym0.lock
                  && (in_i.sym0.gen == state_q.gen);
  assign known1 = in_i.sym0.lock && in_i.sym1.lock && (in_i.sym1.gen == in_i.sym0.gen);

  // Lane 1 is decoded for both running disparities in parallel with lane 0,
  // and lane 0's RD picks one: the RD chain is a select, not a second decode
  // in series (MAS, Timing).
  pcie_pcs_pkg::t_dec   dec1_neg;  // sym1 decoded after an RD- symbol
  pcie_pcs_pkg::t_dec   dec1_pos;  // sym1 decoded after an RD+ symbol

  assign dec0     = pcie_pcs_pkg::decode(sym0, known0, state_q.rd_pos);
  assign dec1_neg = pcie_pcs_pkg::decode(sym1, known1, 1'b0);
  assign dec1_pos = pcie_pcs_pkg::decode(sym1, known1, 1'b1);
  assign dec1     = dec0.rd_pos ? dec1_pos : dec1_neg;

  assign out_o = '{sym1: dec1.sym, sym0: dec0.sym};

  always_comb begin
    state_d          = state_q;
    state_d.rd_known = state_q.rd_known && !pol_change;
    if (in_valid_i) begin
      state_d.rd_known = in_i.sym1.lock;
      state_d.rd_pos   = dec1.rd_pos;
      state_d.gen      = in_i.sym1.gen;
    end
    state_d.pol = polarity_i;
  end

  `COMMON_DFF_RST(state_q, state_d, clk_i, rst_i)

  // Invariants (MAS, Decoder): one error class per symbol; a code error
  // always yields EDB as K; a disparity error needs a known RD.
  `COMMON_ASSERT_NEVER(a_one_error_class, clk_i, rst_i,
                       (dec0.sym.code_err && dec0.sym.disp_err) ||
                       (dec1.sym.code_err && dec1.sym.disp_err))
  `COMMON_ASSERT_NEVER(a_code_err_edb, clk_i, rst_i,
                       (dec0.sym.code_err && (dec0.sym.sym.data != common_pkg::SYM_EDB || !dec0.sym.sym.k)) ||
                       (dec1.sym.code_err && (dec1.sym.sym.data != common_pkg::SYM_EDB || !dec1.sym.sym.k)))
  // After a word whose second symbol is unlocked, the RD is unknown.
  `COMMON_ASSERT(a_rd_unknown_after_unlocked, clk_i, rst_i,
                 (in_valid_i && !in_i.sym1.lock) |=> !state_q.rd_known)
  `COMMON_ASSERT_NEVER(a_disp_err_known_rd, clk_i, rst_i,
                       (dec0.sym.disp_err && !known0) || (dec1.sym.disp_err && !known1))

endmodule

`default_nettype wire
