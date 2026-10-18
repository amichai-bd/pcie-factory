// Transmit arbiter of pcie_tl (wiki/src/rtl/tl/MAS.md#transmit-arbiter):
// picks the next TLP among the heads of the posted line, the non-posted
// line and the completion queues, round robin, each head only when the
// ordering rules let it go and the link shows its credits available
// (wiki/src/arch/tl/ORDERING.md), forms its header (wiki/src/arch/tl/TLP.md)
// and sends it on the upper TLP stream, one beat per cycle, through a
// registered output stage with a skid slot, so tlp_tx_* come from flops and
// tlp_tx_ready_i reaches no arbiter logic.
`default_nettype none

`include "common_macros.svh"

module pcie_tl_tx (
  input  logic                        clk_i,
  input  logic                        rst_i,
  input  logic [7:0]                  bus_num_i,
  input  logic [4:0]                  dev_num_i,
  // Credits available (all ones: infinite, which >= passes)
  input  logic [7:0]                  fc_ph_avail_i,
  input  logic [11:0]                 fc_pd_avail_i,
  input  logic [7:0]                  fc_nph_avail_i,
  input  logic [11:0]                 fc_npd_avail_i,
  input  logic [7:0]                  fc_cplh_avail_i,
  input  logic [11:0]                 fc_cpld_avail_i,
  // Posted line and data
  input  logic                        p_valid_i,
  input  logic                        p_ready_i,
  input  pcie_tl_pkg::t_txp_ent       p_head_i,
  output logic                        p_pop_o,
  input  logic                        pd_valid_i,
  input  logic [31:0]                 pd_head_i,
  output logic                        pd_pop_o,
  // Non-posted line
  input  logic                        np_valid_i,
  input  pcie_tl_pkg::t_txnp_ent      np_head_i,
  output logic                        np_pop_o,
  // Completion queues: from cc, from cfg records
  input  logic                        cc_valid_i,
  input  pcie_tl_pkg::t_cpl_desc      cc_head_i,
  output logic                        cc_pop_o,
  input  logic                        ccd_valid_i,
  input  logic [31:0]                 ccd_head_i,
  output logic                        ccd_pop_o,
  input  logic                        cf_valid_i,
  input  pcie_tl_pkg::t_cpl_desc      cf_head_i,
  output logic                        cf_pop_o,
  input  logic                        cfd_valid_i,
  input  logic [31:0]                 cfd_head_i,
  output logic                        cfd_pop_o,
  // Upper TLP stream, transmit
  output logic                        tlp_tx_valid_o,
  input  logic                        tlp_tx_ready_i,
  output logic [31:0]                 tlp_tx_data_o,
  output logic                        tlp_tx_sop_o,
  output logic                        tlp_tx_eop_o,
  // An rq read's sop beat taken by the link (to the tracker)
  output logic                        rq_sent_o,
  output logic [2:0]                  rq_sent_tag_o
);

  pcie_tl_pkg::t_tx_state st_q, st_d;
  pcie_tl_pkg::t_tx_out   so_q, so_d;

  // The arbiter's beat, into the output stage.
  logic        a_valid;
  logic [31:0] a_data;
  logic        a_sop;
  logic        a_eop;
  logic        a_ready;

  // ------------------------------------------------------ eligibility
  // A line head may go once every posted request taken before it has gone:
  // the posted line is empty or its head is of a later epoch on that line.
  logic       ord_np;
  logic       ord_cc;
  logic       ord_cf;
  logic [3:0] p_dcred;
  logic       el_p;
  logic       el_np;
  logic       el_cc;
  logic       el_cf;
  logic       el_cpl;
  logic       pick_cf;

  assign ord_np  = !p_valid_i || pcie_tl_pkg::ep_after(p_head_i.ep[pcie_tl_pkg::TX_NP], np_head_i.ep);
  assign ord_cc  = !p_valid_i || pcie_tl_pkg::ep_after(p_head_i.ep[pcie_tl_pkg::TX_CC], cc_head_i.ep);
  assign ord_cf  = !p_valid_i || pcie_tl_pkg::ep_after(p_head_i.ep[pcie_tl_pkg::TX_CF], cf_head_i.ep);
  assign p_dcred = (p_head_i.kind == pcie_tl_pkg::PX_MSG) ? 4'd0
                 : pcie_tl_pkg::dw_credits(p_head_i.dw_len);
  // A TLP is offered only with 1 header credit and its data credits.
  assign el_p    = p_valid_i && p_ready_i && fc_ph_avail_i != 8'd0
                && fc_pd_avail_i >= {8'd0, p_dcred};
  assign el_np   = np_valid_i && ord_np && fc_nph_avail_i != 8'd0;
  assign el_cc   = cc_valid_i && ord_cc && fc_cplh_avail_i != 8'd0
                && fc_cpld_avail_i >= {8'd0, pcie_tl_pkg::dw_credits(cc_head_i.dlen)};
  assign el_cf   = cf_valid_i && ord_cf && fc_cplh_avail_i != 8'd0
                && fc_cpld_avail_i >= {8'd0, pcie_tl_pkg::dw_credits(cf_head_i.dlen)};
  assign el_cpl  = el_cc || el_cf;
  assign pick_cf = el_cf && (!el_cc || st_q.cf_turn);

  // Round robin over posted, non-posted and completions, from st_q.rr.
  pcie_tl_pkg::t_txsel pick;
  logic                any_el;
  logic [1:0]          win;

  assign any_el = el_p || el_np || el_cpl;

  always_comb begin
    case (st_q.rr)
      2'd0:    win = el_p ? 2'd0 : (el_np ? 2'd1 : 2'd2);
      2'd1:    win = el_np ? 2'd1 : (el_cpl ? 2'd2 : 2'd0);
      default: win = el_cpl ? 2'd2 : (el_p ? 2'd0 : 2'd1);
    endcase
    case (win)
      2'd0:    pick = pcie_tl_pkg::TS_P;
      2'd1:    pick = pcie_tl_pkg::TS_NP;
      default: pick = pick_cf ? pcie_tl_pkg::TS_CF : pcie_tl_pkg::TS_CC;
    endcase
  end

  // --------------------------------------------------------- the TLP
  pcie_tl_pkg::t_txsel   sel;
  pcie_tl_pkg::t_cpl_desc cd;
  logic                  offering;
  logic                  h4;            // a 4-DW header
  logic [2:0]            hlen;
  logic [5:0]            dlen;
  logic [5:0]            nbeats;
  logic [61:0]           addr;
  logic [31:0]           dw0;
  logic [31:0]           dw1;
  logic [31:0]           dw2;
  logic [31:0]           dw3;
  logic [31:0]           hdw;
  logic [31:0]           ddw;
  logic                  d_ok;
  logic                  hdr_beat;
  logic                  take;
  logic                  last;

  assign sel      = st_q.busy ? st_q.sel : pick;
  assign offering = st_q.busy || any_el;
  assign cd       = (sel == pcie_tl_pkg::TS_CF) ? cf_head_i : cc_head_i;
  assign addr     = (sel == pcie_tl_pkg::TS_NP) ? np_head_i.addr : p_head_i.addr;

  always_comb begin
    h4   = 1'b0;
    dlen = 6'd0;
    dw0  = '0;
    dw1  = '0;
    dw2  = '0;
    dw3  = '0;
    ddw  = '0;
    d_ok = 1'b0;
    case (sel)
      pcie_tl_pkg::TS_P: begin
        if (p_head_i.kind == pcie_tl_pkg::PX_MSG) begin
          // A message: 4-DW header, TC 0, no data, bytes 8 to 15 zero.
          h4  = 1'b1;
          dw0 = pcie_tl_pkg::tx_dw0(3'b001, {2'b10, p_head_i.rrr}, 3'd0, 2'd0, 10'd0);
          dw1 = {p_head_i.code, 8'd0, 16'd0};
        end else begin
          // A memory write: 3-DW header when address bits 63:32 are 0.
          h4   = addr[61:30] != 32'd0;
          dlen = p_head_i.dw_len;
          dw0  = pcie_tl_pkg::tx_dw0({2'b01, h4}, pcie_tl_pkg::T_MEM, 3'd0, 2'd0, {4'd0, dlen});
          dw1  = pcie_tl_pkg::tx_dw1_req(16'd0, 8'd0, p_head_i.lbe, p_head_i.fbe);
          dw2  = h4 ? pcie_tl_pkg::be_word(addr[61:30]) : pcie_tl_pkg::be_word({addr[29:0], 2'b00});
          dw3  = pcie_tl_pkg::be_word({addr[29:0], 2'b00});
        end
        ddw  = (p_head_i.kind == pcie_tl_pkg::PX_IRQ) ? p_head_i.data : pd_head_i;
        d_ok = p_head_i.kind == pcie_tl_pkg::PX_IRQ || pd_valid_i;
      end
      pcie_tl_pkg::TS_NP: begin
        h4  = addr[61:30] != 32'd0;
        dw0 = pcie_tl_pkg::tx_dw0({2'b00, h4}, pcie_tl_pkg::T_MEM, 3'd0, 2'd0,
                                  {4'd0, np_head_i.dw_len});
        dw1 = pcie_tl_pkg::tx_dw1_req(16'd0, {5'd0, np_head_i.tag}, np_head_i.lbe, np_head_i.fbe);
        dw2 = h4 ? pcie_tl_pkg::be_word(addr[61:30]) : pcie_tl_pkg::be_word({addr[29:0], 2'b00});
        dw3 = pcie_tl_pkg::be_word({addr[29:0], 2'b00});
      end
      default: begin
        // A completion, with or without data; the locked kind for a
        // refused locked read.
        dlen = cd.dlen;
        dw0  = pcie_tl_pkg::tx_dw0({1'b0, cd.dlen != 6'd0, 1'b0},
                                   cd.locked ? pcie_tl_pkg::T_CPLLK : pcie_tl_pkg::T_CPL,
                                   cd.id.tc, cd.id.attr, {4'd0, cd.dlen});
        dw1  = {cd.bc[7:0], cd.status, 1'b0, cd.bc[11:8], 16'd0};
        dw2  = {1'b0, cd.la, cd.id.tag, cd.id.rid[7:0], cd.id.rid[15:8]};
        ddw  = (sel == pcie_tl_pkg::TS_CF) ? cfd_head_i : ccd_head_i;
        d_ok = (sel == pcie_tl_pkg::TS_CF) ? cfd_valid_i : ccd_valid_i;
      end
    endcase
  end

  assign hlen     = h4 ? 3'd4 : 3'd3;
  assign nbeats   = {3'd0, hlen} + dlen;
  assign hdr_beat = st_q.beat < {3'd0, hlen};

  always_comb begin
    case (st_q.beat[1:0])
      2'd0:    hdw = dw0;
      2'd1:    hdw = dw1;
      2'd2:    hdw = dw2;
      default: hdw = dw3;
    endcase
  end

  // A beat, once offered to the output stage, stays offered, unchanged,
  // until taken: credits do not fall while the TLP waits, and its data is
  // all queued. DW 1's ID bits are filled in by the output stage.
  assign a_valid = offering && (hdr_beat || d_ok);
  assign a_data  = hdr_beat ? hdw : ddw;
  assign a_sop   = st_q.beat == 6'd0;
  assign last    = st_q.beat == nbeats - 6'd1;
  assign a_eop   = last;
  assign take    = a_valid && a_ready;

  always_comb begin
    st_d = st_q;
    if (!st_q.busy) st_d.sel = pick;
    st_d.busy = offering && !(take && last);
    if (take) begin
      st_d.beat = last ? 6'd0 : st_q.beat + 6'd1;
    end
    // The next round starts after the line just started; the completion
    // queue not chosen goes first next time.
    if (take && a_sop) begin
      case (sel)
        pcie_tl_pkg::TS_P:  st_d.rr = 2'd1;
        pcie_tl_pkg::TS_NP: st_d.rr = 2'd2;
        default: begin
          st_d.rr      = 2'd0;
          st_d.cf_turn = sel == pcie_tl_pkg::TS_CC;
        end
      endcase
    end
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)

  assign p_pop_o   = take && last && sel == pcie_tl_pkg::TS_P;
  assign np_pop_o  = take && last && sel == pcie_tl_pkg::TS_NP;
  assign cc_pop_o  = take && last && sel == pcie_tl_pkg::TS_CC;
  assign cf_pop_o  = take && last && sel == pcie_tl_pkg::TS_CF;
  assign pd_pop_o  = take && !hdr_beat && sel == pcie_tl_pkg::TS_P
                  && p_head_i.kind == pcie_tl_pkg::PX_RQ;
  assign ccd_pop_o = take && !hdr_beat && sel == pcie_tl_pkg::TS_CC;
  assign cfd_pop_o = take && !hdr_beat && sel == pcie_tl_pkg::TS_CF;

  // ------------------------------------------------------ output stage
  // Two beats: out, offered on tlp_tx_*, and skid, which takes the
  // arbiter's beat in a cycle where out is held. The arbiter is ready
  // while skid is empty, a register, so tlp_tx_ready_i reaches no arbiter
  // logic; the link taking a beat every cycle keeps skid empty, so a TLP
  // still goes out with no gap. The ID (bus_num_i, dev_num_i) is captured
  // as a sop beat is first offered on tlp_tx_* and goes into DW 1, which
  // is offered only after the sop is taken.
  pcie_tl_pkg::t_tx_beat a_beat;
  logic                  out_take;
  logic                  out_first;

  assign a_ready  = !so_q.skid.valid;
  assign a_beat   = '{valid: take, data: a_data, sop: a_sop, eop: a_eop,
                      idw: st_q.beat == 6'd1,
                      rq: a_sop && sel == pcie_tl_pkg::TS_NP,
                      tag: np_head_i.tag};
  assign out_take  = so_q.out.valid && tlp_tx_ready_i;
  assign out_first = so_q.out.valid && so_q.out.sop && !so_q.held;

  always_comb begin
    so_d      = so_q;
    so_d.held = so_q.out.valid && !tlp_tx_ready_i;
    if (out_first) so_d.id = {bus_num_i, dev_num_i, 3'd0};
    if (!so_q.out.valid || out_take) begin
      // out is free at this edge: the skid beat first, else the arbiter's.
      if (so_q.skid.valid) begin
        so_d.out        = so_q.skid;
        so_d.skid.valid = 1'b0;
      end else begin
        so_d.out = a_beat;
      end
    end else if (take) begin
      so_d.skid = a_beat;
    end
  end

  `COMMON_DFF_RST(so_q, so_d, clk_i, rst_i)

  assign tlp_tx_valid_o = so_q.out.valid;
  assign tlp_tx_data_o  = {so_q.out.data[31:16],
                           so_q.out.idw ? {so_q.id[7:0], so_q.id[15:8]} : so_q.out.data[15:0]};
  assign tlp_tx_sop_o   = so_q.out.sop;
  assign tlp_tx_eop_o   = so_q.out.eop;

  assign rq_sent_o     = out_take && so_q.out.rq;
  assign rq_sent_tag_o = so_q.out.tag;

  // rq reads carry no data: no non-posted data credit is needed. The
  // epoch of a completion is read only by the ordering above.
  `COMMON_UNUSED_BITS(unused_tx, {fc_npd_avail_i, cd.ep})

  // An offered beat never changes until taken; skid fills only while out
  // is held.
  `COMMON_ASSERT_STABLE_WHEN(a_out_held, clk_i, rst_i,
                             so_q.out.valid && !tlp_tx_ready_i, so_q.out)
  `COMMON_ASSERT_NEVER(a_skid_unheld, clk_i, rst_i,
                       so_q.skid.valid && !so_q.out.valid)

  // The line being sent keeps its head until its eop (one consumer each).
  `COMMON_ASSERT_NEVER(a_head_lost, clk_i, rst_i,
                       st_q.busy && ((st_q.sel == pcie_tl_pkg::TS_P && !p_valid_i)
                                     || (st_q.sel == pcie_tl_pkg::TS_NP && !np_valid_i)
                                     || (st_q.sel == pcie_tl_pkg::TS_CC && !cc_valid_i)
                                     || (st_q.sel == pcie_tl_pkg::TS_CF && !cf_valid_i)))

endmodule

`default_nettype wire
