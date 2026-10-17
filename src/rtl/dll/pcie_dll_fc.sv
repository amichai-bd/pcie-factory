// Flow control of pcie_dll (wiki/src/rtl/dll/MAS.md#flow-control): the
// partner's credit limits recorded from InitFC in FC_INIT1 and updated by
// UpdateFC (wiki/src/arch/dll/FLOW_CONTROL.md), CREDITS_CONSUMED and the
// transmit credit gate, the credits-available outputs, the receive-side
// CREDITS_ALLOCATED and CREDITS_RECEIVED with the overflow check, and the
// UpdateFC this side sends in DL_Active: at once on entry, when credits
// were returned and on the 2048-cycle refresh, classes in round robin.
`default_nettype none

`include "common_macros.svh"

module pcie_dll_fc (
  input  logic                     clk_i,
  input  logic                     rst_i,
  input  logic                     flush_i,         // DL_Inactive: clear everything
  input  pcie_dll_pkg::t_dl_state  state_i,
  input  logic                     enter_active_i,  // this edge enters DL_Active
  input  pcie_dll_pkg::t_fc_vals   adv_i,           // the credits this side advertises
  input  pcie_dll_pkg::t_rx_dllp   rx_dllp_i,       // a good received DLLP
  input  logic                     updfc_take_i,    // the arbiter takes updfc_req_o
  output pcie_dll_pkg::t_dllp_req  updfc_req_o,     // the next due UpdateFC
  output logic                     recorded_o,      // all three classes recorded
  output pcie_dll_pkg::t_fc_vals   avail_o,         // credits available (HAS, credits available)
  // Transmit: the gate covers the TLP whose first DW is offered; a taken
  // sop consumes its credits.
  input  logic [31:0]              tx_data_i,       // tlp_tx_data_i
  input  logic                     tx_sop_take_i,   // a sop taken from the upper stream
  output logic                     tx_ok_o,
  // Receive: the decision on an expected good-LCRC TLP (#213), whether it
  // fits, and the upper receive stream whose taken eop returns credits.
  input  pcie_dll_pkg::t_fc_rx_tlp rx_tlp_i,
  output logic                     rx_fits_o,
  input  pcie_dll_pkg::t_tlp_beat  rx_beat_i,
  output logic                     overflow_o       // Receiver overflow pulse
);

  pcie_dll_pkg::t_fc_limit lim_p_q, lim_p_d;
  pcie_dll_pkg::t_fc_limit lim_np_q, lim_np_d;
  pcie_dll_pkg::t_fc_limit lim_cpl_q, lim_cpl_d;
  pcie_dll_pkg::t_fc_vals  used_q, used_d;     // CREDITS_CONSUMED
  pcie_dll_pkg::t_fc_vals  alloc_q, alloc_d;   // CREDITS_ALLOCATED
  pcie_dll_pkg::t_fc_vals  rcvd_q, rcvd_d;     // CREDITS_RECEIVED
  pcie_dll_pkg::t_fc_vals  sent_q, sent_d;     // the values last sent in UpdateFC
  pcie_dll_pkg::t_fc_cost  up_cost_q;          // the TLP being taken on the receive stream
  logic                    ovf_q;
  logic [2:0]              due_q, due_d;       // entry UpdateFC due, bit per class
  logic [10:0]             ref_p_q, ref_p_d;   // cycles since the class's last UpdateFC
  logic [10:0]             ref_np_q, ref_np_d;
  logic [10:0]             ref_cpl_q, ref_cpl_d;
  pcie_dll_pkg::t_fc_class rr_q, rr_d;         // class that goes first

  logic                    active;
  pcie_dll_pkg::t_fc_vals  rx_base;    // CREDITS_ALLOCATED in DL_Inactive
  pcie_dll_pkg::t_fc_vals  upd;        // what an UpdateFC of each class carries now
  logic [2:0]              changed;    // credits returned since the class's last UpdateFC
  logic [2:0]              due_now;    // due, refresh time reached or credits returned
  logic                    pick_ok;
  pcie_dll_pkg::t_fc_class pick;       // first due class in round-robin order
  pcie_dll_pkg::t_fc_class rr_1;
  pcie_dll_pkg::t_fc_class rr_2;
  logic                    take_p;     // the UpdateFC taken is this class's
  logic                    take_np;
  logic                    take_cpl;
  pcie_dll_pkg::t_fc_val   upd_pick;
  pcie_dll_pkg::t_fc_cost  tx_cost;    // the offered TLP's credits
  pcie_dll_pkg::t_fc_limit tx_lim;
  logic                    tx_consume; // a TLP's sop taken: CREDITS_CONSUMED
  pcie_dll_pkg::t_fc_cost  rx_cost;    // the decided TLP's credits
  logic                    rx_store;   // stored: CREDITS_RECEIVED
  logic                    rx_ovf;     // does not fit: Receiver overflow
  logic                    rx_mal;     // malformed: both counters at once
  pcie_dll_pkg::t_fc_cost  beat_cost;  // credits of the TLP whose sop is on the receive stream
  pcie_dll_pkg::t_fc_cost  ret_cost;   // credits returned by a taken eop
  logic                    ret;
  logic                    up_sop;
  pcie_dll_pkg::t_fc_vals  alloc_mal;

  assign active  = state_i == pcie_dll_pkg::DL_ACTIVE;
  assign rx_base = pcie_dll_pkg::fc_rx_base(adv_i);

  // ------------------------------------------------------------ transmit

  assign tx_cost = pcie_dll_pkg::tlp_cost(tx_data_i[6], tx_data_i[4:0],
                                    {tx_data_i[17:16], tx_data_i[31:24]});

  always_comb begin
    case (tx_cost.cls)
      pcie_dll_pkg::FC_NP:  tx_lim = lim_np_q;
      pcie_dll_pkg::FC_CPL: tx_lim = lim_cpl_q;
      default:              tx_lim = lim_p_q;
    endcase
  end

  assign tx_ok_o    = active && pcie_dll_pkg::fc_gate(tx_lim.inf_hdr, tx_lim.inf_data,
                                                      tx_lim.limit,
                                                      pcie_dll_pkg::fc_get(used_q, tx_cost.cls),
                                                      tx_cost.val);
  assign tx_consume = tx_sop_take_i;

  // ------------------------------------------------------------ receive

  always_comb begin
    rx_cost = pcie_dll_pkg::tlp_cost(rx_tlp_i.dw0[6], rx_tlp_i.dw0[4:0],
                                     {rx_tlp_i.dw0[17:16], rx_tlp_i.dw0[31:24]});
    if (rx_tlp_i.no_hdr) begin
      rx_cost.val = '0;
    end
  end

  assign rx_fits_o = pcie_dll_pkg::fc_fits(pcie_dll_pkg::fc_get(alloc_q, rx_cost.cls),
                                           pcie_dll_pkg::fc_get(rcvd_q, rx_cost.cls),
                                           rx_cost.val);
  assign rx_store  = rx_tlp_i.valid && !rx_tlp_i.malformed && rx_fits_o;
  assign rx_ovf    = rx_tlp_i.valid && !rx_tlp_i.malformed && !rx_fits_o;
  assign rx_mal    = rx_tlp_i.valid && rx_tlp_i.malformed;

  // A delivered TLP's credits return when the upper layer takes its eop
  // beat; its cost is read from its sop beat.
  assign beat_cost = pcie_dll_pkg::tlp_cost(rx_beat_i.data[6], rx_beat_i.data[4:0],
                                      {rx_beat_i.data[17:16], rx_beat_i.data[31:24]});
  assign up_sop    = rx_beat_i.take && rx_beat_i.sop;
  assign ret       = rx_beat_i.take && rx_beat_i.eop;
  assign ret_cost  = rx_beat_i.sop ? beat_cost : up_cost_q;

  `COMMON_DFF_EN(up_cost_q, beat_cost, clk_i, up_sop)

  // ------------------------------------------------------------ UpdateFC

  assign upd     = pcie_dll_pkg::fc_upd_vals(alloc_q, adv_i);
  assign changed = {upd.cpl != sent_q.cpl, upd.np != sent_q.np, upd.p != sent_q.p};
  assign due_now = due_q | changed
                 | {ref_cpl_q == pcie_dll_pkg::REFRESH_LAST,
                    ref_np_q == pcie_dll_pkg::REFRESH_LAST,
                    ref_p_q == pcie_dll_pkg::REFRESH_LAST};
  assign rr_1    = pcie_dll_pkg::fc_next(rr_q);
  assign rr_2    = pcie_dll_pkg::fc_next(rr_1);

  always_comb begin
    pick_ok = 1'b1;
    pick    = rr_q;
    if (due_now[rr_q]) begin
      pick = rr_q;
    end else if (due_now[rr_1]) begin
      pick = rr_1;
    end else if (due_now[rr_2]) begin
      pick = rr_2;
    end else begin
      pick_ok = 1'b0;
    end
  end

  assign take_p   = updfc_take_i && pick == pcie_dll_pkg::FC_P;
  assign take_np  = updfc_take_i && pick == pcie_dll_pkg::FC_NP;
  assign take_cpl = updfc_take_i && pick == pcie_dll_pkg::FC_CPL;
  assign upd_pick = pcie_dll_pkg::fc_get(upd, pick);

  // ------------------------------------------------------------ state

  always_comb begin
    lim_p_d   = pcie_dll_pkg::fc_record(lim_p_q, rx_dllp_i.valid, rx_dllp_i.kind,
                                          rx_dllp_i.cls, rx_dllp_i.val,
                                          pcie_dll_pkg::FC_P, state_i);
    lim_np_d  = pcie_dll_pkg::fc_record(lim_np_q, rx_dllp_i.valid, rx_dllp_i.kind,
                                          rx_dllp_i.cls, rx_dllp_i.val,
                                          pcie_dll_pkg::FC_NP, state_i);
    lim_cpl_d = pcie_dll_pkg::fc_record(lim_cpl_q, rx_dllp_i.valid, rx_dllp_i.kind,
                                          rx_dllp_i.cls, rx_dllp_i.val,
                                          pcie_dll_pkg::FC_CPL, state_i);
    used_d    = pcie_dll_pkg::fc_charge(used_q, tx_consume, tx_cost);
    rcvd_d    = pcie_dll_pkg::fc_charge(rcvd_q, rx_store || rx_mal, rx_cost);
    alloc_mal = pcie_dll_pkg::fc_charge(alloc_q, rx_mal, rx_cost);
    alloc_d   = pcie_dll_pkg::fc_charge(alloc_mal, ret, ret_cost);
    sent_d    = sent_q;
    if (take_p) begin
      sent_d.p = upd_pick;
    end
    if (take_np) begin
      sent_d.np = upd_pick;
    end
    if (take_cpl) begin
      sent_d.cpl = upd_pick;
    end
    due_d     = due_q & ~{take_cpl, take_np, take_p};
    ref_p_d   = active ? pcie_dll_pkg::refresh_count(ref_p_q, take_p) : 11'd0;
    ref_np_d  = active ? pcie_dll_pkg::refresh_count(ref_np_q, take_np) : 11'd0;
    ref_cpl_d = active ? pcie_dll_pkg::refresh_count(ref_cpl_q, take_cpl) : 11'd0;
    rr_d      = updfc_take_i ? pcie_dll_pkg::fc_next(pick) : rr_q;
    if (enter_active_i) begin
      // Every class, infinite ones included, gets an UpdateFC at once.
      due_d = 3'b111;
    end
    if (flush_i) begin
      lim_p_d   = '0;
      lim_np_d  = '0;
      lim_cpl_d = '0;
      used_d    = '0;
      rcvd_d    = '0;
      alloc_d   = rx_base;
      sent_d    = adv_i;
      due_d     = 3'b000;
      ref_p_d   = 11'd0;
      ref_np_d  = 11'd0;
      ref_cpl_d = 11'd0;
      rr_d      = pcie_dll_pkg::FC_P;
    end
  end

  // The counters are loaded with their DL_Inactive values by flush_i,
  // which reset forces (the state resets to DL_Inactive).
  `COMMON_DFF_RST(lim_p_q, lim_p_d, clk_i, rst_i)
  `COMMON_DFF_RST(lim_np_q, lim_np_d, clk_i, rst_i)
  `COMMON_DFF_RST(lim_cpl_q, lim_cpl_d, clk_i, rst_i)
  `COMMON_DFF_RST(used_q, used_d, clk_i, rst_i)
  `COMMON_DFF_RST(alloc_q, alloc_d, clk_i, rst_i)
  `COMMON_DFF_RST(rcvd_q, rcvd_d, clk_i, rst_i)
  `COMMON_DFF_RST(sent_q, sent_d, clk_i, rst_i)
  `COMMON_DFF_RST(ovf_q, rx_ovf, clk_i, rst_i)
  `COMMON_DFF_RST(due_q, due_d, clk_i, rst_i)
  `COMMON_DFF_RST(ref_p_q, ref_p_d, clk_i, rst_i)
  `COMMON_DFF_RST(ref_np_q, ref_np_d, clk_i, rst_i)
  `COMMON_DFF_RST(ref_cpl_q, ref_cpl_d, clk_i, rst_i)
  `COMMON_DFF_RST_VAL(rr_q, rr_d, clk_i, rst_i, pcie_dll_pkg::FC_P)

  // ------------------------------------------------------------ outputs

  assign updfc_req_o.valid = active && pick_ok;
  assign updfc_req_o.body  = pcie_dll_pkg::fc_body(pcie_dll_pkg::DLLP_UPDATEFC, pick, upd_pick);
  assign recorded_o        = lim_p_q.recorded && lim_np_q.recorded && lim_cpl_q.recorded;
  assign avail_o.p         = pcie_dll_pkg::fc_show(lim_p_q.inf_hdr, lim_p_q.inf_data,
                                                 lim_p_q.limit, used_q.p, active);
  assign avail_o.np        = pcie_dll_pkg::fc_show(lim_np_q.inf_hdr, lim_np_q.inf_data,
                                                 lim_np_q.limit, used_q.np, active);
  assign avail_o.cpl       = pcie_dll_pkg::fc_show(lim_cpl_q.inf_hdr, lim_cpl_q.inf_data,
                                                 lim_cpl_q.limit, used_q.cpl, active);
  assign overflow_o        = ovf_q;

  // Header bits other than Fmt bit 6, Type and Length do not set the
  // credits; the gate does not read the limit's record flag.
  `COMMON_UNUSED_BITS(unused_bits, {rx_dllp_i.seq, tx_lim.recorded,
                                       tx_data_i[23:18], tx_data_i[15:7],
                                       tx_data_i[5], rx_tlp_i.dw0[23:18],
                                       rx_tlp_i.dw0[15:7], rx_tlp_i.dw0[5],
                                       rx_beat_i.data[23:18], rx_beat_i.data[15:7],
                                       rx_beat_i.data[5]})

  `COMMON_ASSERT_NEVER(a_take_without_due, clk_i, rst_i, updfc_take_i && !updfc_req_o.valid)
  // The TLP source accepts only TLPs the gate covers (HAS, transmit).
  `COMMON_ASSERT_NEVER(a_consume_without_credit, clk_i, rst_i, tx_consume && !tx_ok_o)
  // TLPs are decided only in FC_INIT2 and DL_Active (retry, receiving TLPs).
  `COMMON_ASSERT_NEVER(a_rx_tlp_outside_dl_up, clk_i, rst_i,
                       rx_tlp_i.valid && state_i != pcie_dll_pkg::DL_INIT2 && !active)
  // Credits are returned only for TLPs stored: the free credits of each
  // class never exceed its receive space.
  `COMMON_ASSERT_NEVER(a_free_within_space, clk_i, rst_i,
                       !flush_i
                       && !(pcie_dll_pkg::fc_free_ok(alloc_q.p, rcvd_q.p, rx_base.p)
                            && pcie_dll_pkg::fc_free_ok(alloc_q.np, rcvd_q.np, rx_base.np)
                            && pcie_dll_pkg::fc_free_ok(alloc_q.cpl, rcvd_q.cpl, rx_base.cpl)))

endmodule

`default_nettype wire
