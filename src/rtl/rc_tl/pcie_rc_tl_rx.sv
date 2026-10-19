// pcie_rc_tl receive parser (u_rx): takes every beat the link delivers and
// hands each completion to the requester, its header fields at its third
// header DW, then every later beat and its eop
// (wiki/src/rtl/rc_tl/REQUESTER.md#completion-matching,
// wiki/src/rtl/rc_tl/INBOUND.md#receive-checks).
// The request and message side, the receive checks and the posted release
// are #375's: until then every other TLP is taken and dropped with no event
// (MAS, Leaf blocks).
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_rx (
  input  logic                    clk_i,
  input  logic                    rst_i,          // lrst: rst_i or dl_up_i 0
  input  logic                    tlp_rx_valid_i, // taken whenever valid (ready is 1)
  input  logic [31:0]             tlp_rx_data_i,
  input  logic                    tlp_rx_sop_i,
  input  logic                    tlp_rx_eop_i,
  output pcie_rc_tl_pkg::t_cpl_rx cpl_o           // completion beats, to the requester
);

  pcie_rc_tl_pkg::t_rx_st st_q, st_d;
  logic [31:0]            dw0_q;
  logic [31:0]            dw1_q;

  logic       take;
  logic       take_sop;
  logic       take_mid;      // a beat after the sop
  logic       en_dw0;
  logic       en_dw1;
  logic       in_cpl;        // a beat after the sop of a completion
  logic [9:0] lfield;
  logic [11:0] bcf;

  // While rst_i (lrst) is 1 nothing is parsed: a TLP in progress is
  // dropped with no event (MAS, Reset and link down).
  assign take     = tlp_rx_valid_i && !rst_i;
  assign take_sop = take && tlp_rx_sop_i;
  assign take_mid = take && !tlp_rx_sop_i && st_q.busy;
  assign en_dw0   = take_sop;
  assign en_dw1   = take_mid && st_q.beat == 6'd1;
  assign in_cpl   = take_mid && st_q.cpl;

  `COMMON_DFF_EN(dw0_q, tlp_rx_data_i, clk_i, en_dw0)
  `COMMON_DFF_EN(dw1_q, tlp_rx_data_i, clk_i, en_dw1)

  always_comb begin
    st_d = st_q;
    if (take_sop) begin
      st_d.busy = 1'b1;
      st_d.beat = 6'd1;
      st_d.cpl  = common_pkg::is_cpl(tlp_rx_data_i[7:5], tlp_rx_data_i[4:0]);
    end else if (take_mid) begin
      st_d.beat = (st_q.beat == 6'h3F) ? st_q.beat : st_q.beat + 6'd1;
    end
    if (take && tlp_rx_eop_i) begin
      st_d.busy = 1'b0;
    end
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)

  // The header fields (TLP.md, Completions): DW 0 and 1 from registers,
  // DW 2 from the stream at the third header DW.
  assign lfield = {dw0_q[17:16], dw0_q[31:24]};
  assign bcf    = {dw1_q[19:16], dw1_q[31:24]};

  always_comb begin
    cpl_o        = '0;
    cpl_o.hdr    = in_cpl && st_q.beat == 6'd2;
    cpl_o.dat    = in_cpl && st_q.beat > 6'd2;
    cpl_o.eop    = in_cpl && tlp_rx_eop_i;
    cpl_o.data   = tlp_rx_data_i;
    cpl_o.locked = dw0_q[4:0] == common_pkg::T_CPLLK;
    cpl_o.wdata  = dw0_q[6];
    cpl_o.ep     = dw0_q[22];
    cpl_o.status = dw1_q[23:21];
    cpl_o.dw_n   = !dw0_q[6] ? 11'd0 : (lfield == 10'd0) ? 11'd1024 : {1'b0, lfield};
    cpl_o.bc     = bcf;
    cpl_o.rid    = {tlp_rx_data_i[7:0], tlp_rx_data_i[15:8]};
    cpl_o.tag    = tlp_rx_data_i[23:16];
    cpl_o.la     = tlp_rx_data_i[30:24];
  end

  // The completion fields not checked (TLP.md, Completions): TC, Attr, TD,
  // AT, the Completer ID and BCM; the requester counts the data DWs, so a
  // digest DW after them is never data.
  `COMMON_UNUSED_BITS(unused_hdr, {dw0_q[15:7], dw0_q[23], dw0_q[21:18], dw0_q[5],
                                   dw1_q[15:0], dw1_q[20]})

  // The dll delivers whole TLPs (dll HAS, upper TLP stream): a sop starts
  // one, no beat comes outside one, and the eop is no earlier than the
  // third DW.
  `COMMON_ASSERT_NEVER(a_beat_outside_tlp, clk_i, rst_i, take && !tlp_rx_sop_i && !st_q.busy)
  `COMMON_ASSERT_NEVER(a_sop_inside_tlp, clk_i, rst_i, take_sop && st_q.busy)
  `COMMON_ASSERT_NEVER(a_short_tlp, clk_i, rst_i,
                       take && tlp_rx_eop_i && (tlp_rx_sop_i || st_q.beat < 6'd2))

endmodule

`default_nettype wire
