// Flow control of pcie_dll (wiki/src/rtl/dll/MAS.md#flow-control): the
// partner's credit limits recorded from InitFC in FC_INIT1 and updated by
// UpdateFC (wiki/src/arch/dll/FLOW_CONTROL.md), CREDITS_CONSUMED and the
// transmit credit gate, the credits-available outputs, the receive-side
// CREDITS_ALLOCATED and CREDITS_RECEIVED with the overflow check, and the
// UpdateFC this side sends in DL_Active: at once on entry, when credits
// were returned and on the 2048-cycle refresh, classes in round robin.
// With RELEASE, finite posted and non-posted credits return on the release
// port instead of at delivery (FLOW_CONTROL.md, credit release).
`default_nettype none

`include "common_macros.svh"

module pcie_dll_fc #(
  parameter logic RELEASE = 1'b0  // 1: P and NP finite credits return on rel_*_i
) (
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
  // Receive: the TLP receive path's events (MAS, receive events for
  // credit accounting); rx_fit_o answers rx_chk_i in the same cycle.
  input  pcie_dll_pkg::t_rx_credit rx_chk_i,        // a TLP to store: does it fit?
  output logic                     rx_fit_o,
  input  pcie_dll_pkg::t_rx_credit rx_stored_i,     // stored: CREDITS_RECEIVED
  input  pcie_dll_pkg::t_rx_credit rx_malformed_i,  // malformed: both counters
  input  pcie_dll_pkg::t_rx_credit rx_taken_i,      // eop taken upward: CREDITS_ALLOCATED
  input  pcie_dll_pkg::t_rx_credit rel_p_i,         // release port, posted (with RELEASE)
  input  pcie_dll_pkg::t_rx_credit rel_np_i,        // release port, non-posted (with RELEASE)
  output logic                     overflow_o       // Receiver overflow pulse
);

  pcie_dll_pkg::t_fc_limit lim_p_q, lim_p_d;
  pcie_dll_pkg::t_fc_limit lim_np_q, lim_np_d;
  pcie_dll_pkg::t_fc_limit lim_cpl_q, lim_cpl_d;
  pcie_dll_pkg::t_fc_vals  used_q, used_d;     // CREDITS_CONSUMED
  pcie_dll_pkg::t_fc_vals  alloc_q, alloc_d;   // CREDITS_ALLOCATED
  pcie_dll_pkg::t_fc_vals  rcvd_q, rcvd_d;     // CREDITS_RECEIVED
  pcie_dll_pkg::t_fc_vals  sent_q, sent_d;     // the values last sent in UpdateFC
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
  pcie_dll_pkg::t_rx_credit tx_cost;   // the offered TLP's class and credits
  pcie_dll_pkg::t_fc_limit tx_lim;
  logic                    tx_consume; // a TLP's sop taken: CREDITS_CONSUMED
  logic                    rx_ovf;     // a TLP to store does not fit: Receiver overflow
  logic                    rx_mal;     // malformed with credits (a stale class has none)
  pcie_dll_pkg::t_fc_vals  alloc_mal;
  pcie_dll_pkg::t_fc_vals  alloc_dlv;
  pcie_dll_pkg::t_fc_vals  alloc_rel;
  pcie_dll_pkg::t_fc_vals  rcvd_mal;
  logic                    taken_rel;  // a delivered P or NP TLP whose finite credits wait
  pcie_dll_pkg::t_fc_val   taken_ret;  // what a delivery returns now
  pcie_dll_pkg::t_fc_val   rel_p_ret;  // what a release returns: its finite fields
  pcie_dll_pkg::t_fc_val   rel_np_ret;

  assign active  = state_i == pcie_dll_pkg::DL_ACTIVE;
  assign rx_base = pcie_dll_pkg::fc_rx_base(adv_i);

  // ------------------------------------------------------------ transmit

  assign tx_cost = pcie_dll_pkg::tlp_credit(tx_data_i[6], tx_data_i[4:0],
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
                                                      tx_cost.cred);
  assign tx_consume = tx_sop_take_i;

  // ------------------------------------------------------------ receive

  // A malformed TLP under 5 beats has no header: its credits are 0 and its
  // class is stale, so it counts nothing and its class is not read.
  assign rx_fit_o = pcie_dll_pkg::fc_fits(pcie_dll_pkg::fc_get(alloc_q, rx_chk_i.cls),
                                          pcie_dll_pkg::fc_get(rcvd_q, rx_chk_i.cls),
                                          rx_chk_i.cred);
  assign rx_ovf   = rx_chk_i.valid && !rx_fit_o;
  assign rx_mal   = rx_malformed_i.valid && rx_malformed_i.cred != '0;

  // Credit release (FLOW_CONTROL.md, credit release): with RELEASE, a
  // delivered posted or non-posted TLP returns only its infinite fields
  // (the reserve frees at delivery); its finite credits return when the
  // upper layer releases them. Completions always return at delivery.
  assign taken_rel  = RELEASE && rx_taken_i.cls != pcie_dll_pkg::FC_CPL;
  assign taken_ret  = taken_rel
                    ? pcie_dll_pkg::fc_keep(rx_taken_i.cred,
                                            pcie_dll_pkg::fc_get(adv_i, rx_taken_i.cls), 1'b0)
                    : rx_taken_i.cred;
  assign rel_p_ret  = pcie_dll_pkg::fc_keep(rel_p_i.cred, adv_i.p, 1'b1);
  assign rel_np_ret = pcie_dll_pkg::fc_keep(rel_np_i.cred, adv_i.np, 1'b1);

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
    used_d    = pcie_dll_pkg::fc_charge(used_q, tx_consume, tx_cost.cls, tx_cost.cred);
    rcvd_mal  = pcie_dll_pkg::fc_charge(rcvd_q, rx_mal, rx_malformed_i.cls,
                                        rx_malformed_i.cred);
    rcvd_d    = pcie_dll_pkg::fc_charge(rcvd_mal, rx_stored_i.valid, rx_stored_i.cls,
                                        rx_stored_i.cred);
    alloc_mal = pcie_dll_pkg::fc_charge(alloc_q, rx_mal, rx_malformed_i.cls,
                                        rx_malformed_i.cred);
    alloc_dlv = pcie_dll_pkg::fc_charge(alloc_mal, rx_taken_i.valid, rx_taken_i.cls,
                                        taken_ret);
    alloc_rel = pcie_dll_pkg::fc_charge(alloc_dlv, RELEASE && rel_p_i.valid,
                                        pcie_dll_pkg::FC_P, rel_p_ret);
    alloc_d   = pcie_dll_pkg::fc_charge(alloc_rel, RELEASE && rel_np_i.valid,
                                        pcie_dll_pkg::FC_NP, rel_np_ret);
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
  // credits; the gate does not read the limit's record flag, nor the
  // offered TLP's valid flag (always 1 from tlp_credit).
  // The release events' class is fixed by their port.
  `COMMON_UNUSED_BITS(unused_bits, {rx_dllp_i.seq, tx_lim.recorded, tx_cost.valid,
                                    tx_data_i[23:18], tx_data_i[15:7], tx_data_i[5],
                                    rel_p_i.cls, rel_np_i.cls})

  `COMMON_ASSERT_NEVER(a_take_without_due, clk_i, rst_i, updfc_take_i && !updfc_req_o.valid)
  // The TLP source accepts only TLPs the gate covers (HAS, transmit).
  `COMMON_ASSERT_NEVER(a_consume_without_credit, clk_i, rst_i, tx_consume && !tx_ok_o)
  // TLPs are decided only in FC_INIT2 and DL_Active (retry, receiving TLPs),
  // and only one that fits is stored.
  `COMMON_ASSERT_NEVER(a_rx_chk_outside_dl_up, clk_i, rst_i,
                       rx_chk_i.valid && state_i != pcie_dll_pkg::DL_INIT2 && !active)
  `COMMON_ASSERT_NEVER(a_stored_without_fit, clk_i, rst_i,
                       rx_stored_i.valid && !(rx_chk_i.valid && rx_fit_o))
  // Credits are returned only for TLPs stored: the free credits of each
  // class never exceed its receive space.
  `COMMON_ASSERT_NEVER(a_free_within_space, clk_i, rst_i,
                       !flush_i
                       && !(pcie_dll_pkg::fc_free_ok(alloc_q.p, rcvd_q.p, rx_base.p)
                            && pcie_dll_pkg::fc_free_ok(alloc_q.np, rcvd_q.np, rx_base.np)
                            && pcie_dll_pkg::fc_free_ok(alloc_q.cpl, rcvd_q.cpl, rx_base.cpl)))

endmodule

`default_nettype wire
