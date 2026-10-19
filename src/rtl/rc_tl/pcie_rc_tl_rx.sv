// pcie_rc_tl receive parser (u_rx): takes every beat the link delivers,
// places it in its TLP with the shared beat tracker common_tlp_rx_track,
// and hands each completion to the requester, its header fields at its third
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

  common_pkg::t_tlp_rx_pos rp;      // the beat's place in its TLP
  logic                    cpl_q, cpl_d;   // the open TLP is a completion
  logic [31:0]             dw0_q;
  logic [31:0]             dw1_q;

  logic        take;
  logic        in_cpl;        // a beat after the sop of a completion
  logic [9:0]  lfield;
  logic [11:0] bcf;

  // While rst_i (lrst) is 1 nothing is parsed: a TLP in progress is
  // dropped with no event (MAS, Reset and link down). The tracker places
  // each beat and checks the dll's whole-TLP contract (dll HAS, upper TLP
  // stream); Fmt bit 0 (byte 0 bit 5) marks a 4-DW header.
  assign take = tlp_rx_valid_i && !rst_i;

  common_tlp_rx_track u_beats (
    .clk_i  (clk_i),
    .rst_i  (rst_i),
    .take_i (take),
    .sop_i  (tlp_rx_sop_i),
    .eop_i  (tlp_rx_eop_i),
    .hdr4_i (tlp_rx_data_i[5]),
    .pos_o  (rp)
  );

  assign in_cpl = rp.mid && cpl_q;
  assign cpl_d  = rp.sop ? common_pkg::is_cpl(tlp_rx_data_i[7:5], tlp_rx_data_i[4:0]) : cpl_q;

  `COMMON_DFF_RST(cpl_q, cpl_d, clk_i, rst_i)
  `COMMON_DFF_EN(dw0_q, tlp_rx_data_i, clk_i, rp.en_dw[0])
  `COMMON_DFF_EN(dw1_q, tlp_rx_data_i, clk_i, rp.en_dw[1])

  // The header fields (TLP.md, Completions): DW 0 and 1 from registers,
  // DW 2 from the stream at the third header DW (a completion's header is
  // 3 DWs, so that beat is its last header DW).
  assign lfield = {dw0_q[17:16], dw0_q[31:24]};
  assign bcf    = {dw1_q[19:16], dw1_q[31:24]};

  always_comb begin
    cpl_o        = '0;
    cpl_o.hdr    = in_cpl && rp.beat == 6'd2;
    cpl_o.dat    = in_cpl && rp.beat > 6'd2;
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
  // digest DW after them is never data. The tracker's other views serve
  // the request side (#375).
  `COMMON_UNUSED_BITS(unused_hdr, {dw0_q[15:7], dw0_q[23], dw0_q[21:18], dw0_q[5],
                                   dw1_q[15:0], dw1_q[20], rp.hdr_last, rp.en_dw[2],
                                   rp.busy, rp.hdr4, rp.hdr_end})

endmodule

`default_nettype wire
