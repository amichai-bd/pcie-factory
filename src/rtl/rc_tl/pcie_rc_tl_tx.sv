// pcie_rc_tl transmit arbiter and output stage (u_tx)
// (wiki/src/rtl/rc_tl/REQUESTER.md#transmit): two lines, the requester's
// TLP and the completer's completion. A head is eligible when the link
// shows its class's credits; the arbiter grants one, latches it, and sends
// it one beat per cycle through the shared output stage common_skid_stage
// (flops with a skid slot), so tlp_tx_* come from flops and tlp_tx_ready_i
// reaches only the stage. Priority: a requester posted write, then a
// completion, then a requester non-posted request; a completion is never
// granted while a posted write waits for credits.
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
  input  logic [7:0]              fc_cplh_avail_i,
  input  logic [11:0]             fc_cpld_avail_i,
  // The requester's TLP, and its grant at this edge
  input  pcie_rc_tl_pkg::t_tx_tlp req_i,
  output logic                    grant_o,
  // The completer's completion, its grant, and its data from the data queue
  input  pcie_rc_tl_pkg::t_cpl_tx cpl_i,
  output logic                    cpl_grant_o,
  input  logic [31:0]             cpl_data_i,
  output logic                    cpl_pop_o,
  // The link took a non-posted request's sop, a posted write's eop or a
  // completion's eop at this edge
  output logic                    np_sop_o,
  output logic                    p_eop_o,
  output logic                    cpl_eop_o,
  // Upper TLP stream, transmit
  output logic                    tlp_tx_valid_o,
  input  logic                    tlp_tx_ready_i,
  output logic [31:0]             tlp_tx_data_o,
  output logic                    tlp_tx_sop_o,
  output logic                    tlp_tx_eop_o
);

  pcie_rc_tl_pkg::t_tx_arb arb_q, arb_d;

  // ------------------------------------------------------ the grant
  // A head is eligible when the link shows 1 header credit and its data
  // credits for its class (a requester TLP is posted or non-posted).
  common_pkg::t_cls cls;
  logic [3:0]       dcred;
  logic             el;
  logic [3:0]       cpl_dcred;
  logic             cpl_el;
  logic             req_p;     // the requester's head is a posted write

  assign cls   = common_pkg::credit_class(req_i.wdata, req_i.typ);
  assign dcred = common_pkg::data_credits(req_i.wdata, 6'd1);
  assign el    = (cls == common_pkg::CLS_P)
               ? (fc_ph_avail_i != 8'd0) && (fc_pd_avail_i >= {8'd0, dcred})
               : (fc_nph_avail_i != 8'd0) && (fc_npd_avail_i >= {8'd0, dcred});
  assign req_p = req_i.valid && (cls == common_pkg::CLS_P);

  assign cpl_dcred = common_pkg::dw_credits(cpl_i.dw_n);
  assign cpl_el    = (fc_cplh_avail_i != 8'd0) && (fc_cpld_avail_i >= {8'd0, cpl_dcred});

  // A posted write first; a completion then, but never past a waiting
  // posted write; a non-posted request when no completion may go.
  assign cpl_grant_o = !rst_i && !arb_q.busy && cpl_i.valid && cpl_el && !req_p;
  assign grant_o     = !rst_i && !arb_q.busy && req_i.valid && el
                    && (req_p || !(cpl_i.valid && cpl_el));

  // ------------------------------------------------------ the beats
  logic        a_valid;
  logic        a_ready;
  logic        a_sop;
  logic        a_eop;
  logic        a_dat;          // a data beat
  logic [31:0] a_data;
  logic        take;

  assign a_valid = arb_q.busy;
  assign a_sop   = arb_q.beat == 6'd0;
  assign a_dat   = arb_q.beat > 6'd2;
  assign a_eop   = arb_q.beat == 6'd2 + arb_q.dat_n;
  assign take    = a_valid && a_ready;

  always_comb begin
    case (arb_q.beat)
      6'd0:    a_data = arb_q.dw0;
      6'd1:    a_data = arb_q.dw1;
      6'd2:    a_data = arb_q.dw2;
      default: a_data = arb_q.cpl ? cpl_data_i : arb_q.data;
    endcase
  end

  assign cpl_pop_o = take && a_dat && arb_q.cpl;

  always_comb begin
    arb_d = arb_q;
    if (take) begin
      arb_d.beat = arb_q.beat + 6'd1;
      if (a_eop) begin
        arb_d.busy = 1'b0;
      end
    end
    if (grant_o) begin
      arb_d.busy  = 1'b1;
      arb_d.beat  = 6'd0;
      arb_d.np    = cls != common_pkg::CLS_P;
      arb_d.cpl   = 1'b0;
      arb_d.dat_n = {5'd0, req_i.wdata};
      arb_d.dw0   = req_i.dw0;
      arb_d.dw1   = req_i.dw1;
      arb_d.dw2   = req_i.dw2;
      arb_d.data  = req_i.data;
    end
    if (cpl_grant_o) begin
      arb_d.busy  = 1'b1;
      arb_d.beat  = 6'd0;
      arb_d.np    = 1'b0;
      arb_d.cpl   = 1'b1;
      arb_d.dat_n = cpl_i.dw_n;
      arb_d.dw0   = cpl_i.dw0;
      arb_d.dw1   = cpl_i.dw1;
      arb_d.dw2   = cpl_i.dw2;
      arb_d.data  = 32'd0;
    end
  end

  `COMMON_DFF_RST(arb_q, arb_d, clk_i, rst_i)

  // ------------------------------------------------------ output stage
  // The arbiter offers a beat while busy and the stage takes it while its
  // skid slot is empty. A beat not taken is all 0, so tlp_tx_* read 0
  // between beats. Nothing is taken while the link is down (lrst): the
  // stage is cleared, abandoning a TLP in progress.
  pcie_rc_tl_pkg::t_tx_dw a_dw;
  pcie_rc_tl_pkg::t_tx_dw out_dw;
  logic                   out_valid;
  logic                   out_ready;
  logic                   out_take;
  logic                   out_new;

  always_comb begin
    a_dw = '0;
    if (take) begin
      a_dw.data = a_data;
      a_dw.sop  = a_sop;
      a_dw.eop  = a_eop;
      a_dw.np   = arb_q.np;
      a_dw.cpl  = arb_q.cpl;
    end
  end

  assign out_ready = tlp_tx_ready_i && !rst_i;

  common_skid_stage #(
    .WIDTH ($bits(pcie_rc_tl_pkg::t_tx_dw))
  ) u_out (
    .clk_i       (clk_i),
    .rst_i       (rst_i),
    .in_valid_i  (a_valid),
    .in_ready_o  (a_ready),
    .in_data_i   (a_dw),
    .out_valid_o (out_valid),
    .out_ready_i (out_ready),
    .out_data_o  (out_dw),
    .out_take_o  (out_take),
    .out_new_o   (out_new)
  );

  assign tlp_tx_valid_o = out_valid;
  assign tlp_tx_data_o  = out_dw.data;
  assign tlp_tx_sop_o   = out_dw.sop;
  assign tlp_tx_eop_o   = out_dw.eop;

  assign np_sop_o  = out_take && out_dw.sop && out_dw.np;
  assign p_eop_o   = out_take && out_dw.eop && !out_dw.np && !out_dw.cpl;
  assign cpl_eop_o = out_take && out_dw.eop && out_dw.cpl;

  `COMMON_UNUSED_BITS(unused_out, out_new)

  // A requester TLP is never a completion; one grant at a time.
  `COMMON_ASSERT_NEVER(a_req_cls, clk_i, rst_i, req_i.valid && cls == common_pkg::CLS_CPL)
  `COMMON_ASSERT_NEVER(a_two_grants, clk_i, rst_i, grant_o && cpl_grant_o)
  `COMMON_ASSERT_NEVER(a_cpl_len, clk_i, rst_i, cpl_i.valid && cpl_i.dw_n > 6'd32)

endmodule

`default_nettype wire
