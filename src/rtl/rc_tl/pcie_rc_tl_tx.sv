// pcie_rc_tl transmit arbiter and output stage (u_tx)
// (wiki/src/rtl/rc_tl/REQUESTER.md#transmit): grants the requester's TLP
// when the link shows its credits, latches it, and sends it one beat per
// cycle through an output stage of flops with a skid slot, so tlp_tx_*
// come from flops and tlp_tx_ready_i reaches only the stage. The
// completer's line and the priority between the lines are #375's (MAS,
// Leaf blocks).
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_tx (
  input  logic                    clk_i,
  input  logic                    rst_i,          // lrst: rst_i or dl_up_i 0
  // Credits available (all ones: infinite, which the comparisons pass)
  input  logic [7:0]              fc_ph_avail_i,
  input  logic [11:0]             fc_pd_avail_i,
  input  logic [7:0]              fc_nph_avail_i,
  input  logic [11:0]             fc_npd_avail_i,
  // The requester's TLP, and its grant at this edge
  input  pcie_rc_tl_pkg::t_tx_tlp req_i,
  output logic                    grant_o,
  // The link took a non-posted TLP's sop, or a posted TLP's eop, at this edge
  output logic                    np_sop_o,
  output logic                    p_eop_o,
  // Upper TLP stream, transmit
  output logic                    tlp_tx_valid_o,
  input  logic                    tlp_tx_ready_i,
  output logic [31:0]             tlp_tx_data_o,
  output logic                    tlp_tx_sop_o,
  output logic                    tlp_tx_eop_o
);

  pcie_rc_tl_pkg::t_tx_arb arb_q, arb_d;
  pcie_rc_tl_pkg::t_tx_out so_q, so_d;

  // ------------------------------------------------------ the grant
  // A head is eligible when the link shows 1 header credit and its data
  // credits for its class (a requester TLP is posted or non-posted).
  common_pkg::t_cls cls;
  logic [3:0]       dcred;
  logic             el;

  assign cls   = common_pkg::credit_class(req_i.wdata, req_i.typ);
  assign dcred = common_pkg::data_credits(req_i.wdata, 6'd1);
  assign el    = (cls == common_pkg::CLS_P)
               ? (fc_ph_avail_i != 8'd0) && (fc_pd_avail_i >= {8'd0, dcred})
               : (fc_nph_avail_i != 8'd0) && (fc_npd_avail_i >= {8'd0, dcred});
  assign grant_o = !rst_i && !arb_q.busy && req_i.valid && el;

  // ------------------------------------------------------ the beats
  logic        a_valid;
  logic        a_ready;
  logic        a_sop;
  logic        a_eop;
  logic [31:0] a_data;
  logic        take;

  assign a_valid = arb_q.busy;
  assign a_sop   = arb_q.beat == 2'd0;
  assign a_eop   = arb_q.beat == (arb_q.wdata ? 2'd3 : 2'd2);
  assign take    = a_valid && a_ready;

  always_comb begin
    case (arb_q.beat)
      2'd0:    a_data = arb_q.dw0;
      2'd1:    a_data = arb_q.dw1;
      2'd2:    a_data = arb_q.dw2;
      default: a_data = arb_q.data;
    endcase
  end

  always_comb begin
    arb_d = arb_q;
    if (take) begin
      arb_d.beat = arb_q.beat + 2'd1;
      if (a_eop) begin
        arb_d.busy = 1'b0;
      end
    end
    if (grant_o) begin
      arb_d.busy  = 1'b1;
      arb_d.beat  = 2'd0;
      arb_d.np    = cls != common_pkg::CLS_P;
      arb_d.wdata = req_i.wdata;
      arb_d.dw0   = req_i.dw0;
      arb_d.dw1   = req_i.dw1;
      arb_d.dw2   = req_i.dw2;
      arb_d.data  = req_i.data;
    end
  end

  `COMMON_DFF_RST(arb_q, arb_d, clk_i, rst_i)

  // ------------------------------------------------------ output stage
  // Two beats: out, offered on tlp_tx_*, and skid, which takes the
  // arbiter's beat in a cycle where out is held. The arbiter is ready while
  // skid is empty, a register; the link taking a beat every cycle keeps
  // skid empty, so a TLP goes out with no gap.
  pcie_rc_tl_pkg::t_tx_beat a_beat;
  logic                     out_take;

  assign a_ready  = !so_q.skid.valid;
  // Nothing is taken while the link is down (lrst): the stage is abandoned.
  assign out_take = so_q.out.valid && tlp_tx_ready_i && !rst_i;

  // A beat not taken is all 0, so tlp_tx_* read 0 between beats.
  always_comb begin
    a_beat = '0;
    if (take) begin
      a_beat.valid = 1'b1;
      a_beat.data  = a_data;
      a_beat.sop   = a_sop;
      a_beat.eop   = a_eop;
      a_beat.np    = arb_q.np;
    end
  end

  always_comb begin
    so_d = so_q;
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
  assign tlp_tx_data_o  = so_q.out.data;
  assign tlp_tx_sop_o   = so_q.out.sop;
  assign tlp_tx_eop_o   = so_q.out.eop;

  assign np_sop_o = out_take && so_q.out.sop && so_q.out.np;
  assign p_eop_o  = out_take && so_q.out.eop && !so_q.out.np;

  // An offered beat never changes until taken; skid fills only while out
  // is held.
  `COMMON_ASSERT_STABLE_WHEN(a_out_held, clk_i, rst_i,
                             so_q.out.valid && !tlp_tx_ready_i, so_q.out)
  `COMMON_ASSERT_NEVER(a_skid_unheld, clk_i, rst_i, so_q.skid.valid && !so_q.out.valid)
  // A requester TLP is never a completion.
  `COMMON_ASSERT_NEVER(a_req_cls, clk_i, rst_i, req_i.valid && cls == common_pkg::CLS_CPL)

endmodule

`default_nettype wire
