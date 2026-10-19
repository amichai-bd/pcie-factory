`default_nettype none

// Test top for the TLP helpers of common_pkg
// (wiki/src/dv/common/common_pkg.md): each helper called once on
// documented inputs, its result on an output. Combinational, no clock.
module common_pkg_tlp_fixture (
  input  logic [2:0]  fmt_i,
  input  logic [4:0]  typ_i,
  input  logic        with_data_i,
  input  logic [10:0] dw_len_i,
  input  logic [3:0]  fbe_i,
  input  logic [3:0]  lbe_i,
  input  logic        addr_b2_i,
  input  logic [9:0]  addr_dw_i,     // DW address bits 11:2
  input  logic [7:0]  code_i,
  input  logic [31:0] dw_i,
  input  logic [5:0]  n_i,
  output logic        kind_known_o,
  output logic        is_cpl_o,
  output logic [1:0]  credit_class_o,
  output logic [3:0]  data_credits_o,
  output logic [3:0]  dw_credits_o,
  output logic        be_ok_o,
  output logic        cross_4k_o,
  output logic        msg_tc0_o,
  output logic [1:0]  be_lo_o,
  output logic [1:0]  be_hi_o,
  output logic [12:0] byte_total_o,
  output logic [31:0] be_mask_o,
  output logic [31:0] be_word_o
);

  assign kind_known_o   = common_pkg::kind_known(fmt_i, typ_i);
  assign is_cpl_o       = common_pkg::is_cpl(fmt_i, typ_i);
  assign credit_class_o = common_pkg::credit_class(with_data_i, typ_i);
  assign data_credits_o = common_pkg::data_credits(with_data_i, n_i);
  assign dw_credits_o   = common_pkg::dw_credits(n_i);
  assign be_ok_o        = common_pkg::be_ok(dw_len_i, fbe_i, lbe_i, addr_b2_i);
  assign cross_4k_o     = common_pkg::cross_4k(addr_dw_i, dw_len_i);
  assign msg_tc0_o      = common_pkg::msg_tc0(code_i);
  assign be_lo_o        = common_pkg::be_lo(fbe_i);
  assign be_hi_o        = common_pkg::be_hi(fbe_i);
  assign byte_total_o   = common_pkg::byte_total(dw_len_i, fbe_i, lbe_i);
  assign be_mask_o      = common_pkg::be_mask(fbe_i);
  assign be_word_o      = common_pkg::be_word(dw_i);

endmodule

`default_nettype wire
