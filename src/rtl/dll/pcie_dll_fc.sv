// Flow control of pcie_dll (wiki/src/rtl/dll/MAS.md#flow-control), the part
// the control states need: the partner's credit limits recorded from InitFC
// in FC_INIT1 and updated by UpdateFC (wiki/src/arch/dll/FLOW_CONTROL.md),
// the credits-available outputs, and the UpdateFC this side sends in
// DL_Active, at once on entry and on the 2048-cycle refresh. Until the TLP
// paths exist nothing is consumed or returned, so the available credits are
// the limits and every UpdateFC carries the advertised values; credit
// consumption, the credit gate and returned credits come with #214.
`default_nettype none

`include "common_macros.svh"

module pcie_dll_fc (
  input  logic                    clk_i,
  input  logic                    rst_i,
  input  logic                    flush_i,         // DL_Inactive: clear everything
  input  pcie_dll_pkg::t_dl_state state_i,
  input  logic                    enter_active_i,  // this edge enters DL_Active
  input  pcie_dll_pkg::t_fc_vals  adv_i,           // the credits this side advertises
  input  pcie_dll_pkg::t_rx_dllp  rx_dllp_i,       // a good received DLLP
  input  logic                    updfc_take_i,    // the arbiter takes updfc_req_o
  output pcie_dll_pkg::t_dllp_req updfc_req_o,     // the next due UpdateFC
  output logic                    recorded_o,      // all three classes recorded
  output pcie_dll_pkg::t_fc_vals  avail_o          // credits available (HAS, credits available)
);

  pcie_dll_pkg::t_fc_limit lim_p_q, lim_p_d;
  pcie_dll_pkg::t_fc_limit lim_np_q, lim_np_d;
  pcie_dll_pkg::t_fc_limit lim_cpl_q, lim_cpl_d;
  logic [2:0]              due_q, due_d;       // UpdateFC due, bit per class
  logic [10:0]             ref_p_q, ref_p_d;   // cycles since the class's last UpdateFC
  logic [10:0]             ref_np_q, ref_np_d;
  logic [10:0]             ref_cpl_q, ref_cpl_d;
  pcie_dll_pkg::t_fc_class rr_q, rr_d;         // class that goes first

  logic                    active;
  logic [2:0]              due_now;    // due, or refresh time reached
  logic                    pick_ok;
  pcie_dll_pkg::t_fc_class pick;       // first due class in round-robin order
  pcie_dll_pkg::t_fc_class rr_1;
  pcie_dll_pkg::t_fc_class rr_2;
  logic                    take_p;     // the UpdateFC taken is this class's
  logic                    take_np;
  logic                    take_cpl;

  assign active  = state_i == pcie_dll_pkg::DL_ACTIVE;
  assign due_now = due_q | {ref_cpl_q == pcie_dll_pkg::REFRESH_LAST,
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
      due_d     = 3'b000;
      ref_p_d   = 11'd0;
      ref_np_d  = 11'd0;
      ref_cpl_d = 11'd0;
      rr_d      = pcie_dll_pkg::FC_P;
    end
  end

  `COMMON_DFF_RST(lim_p_q, lim_p_d, clk_i, rst_i)
  `COMMON_DFF_RST(lim_np_q, lim_np_d, clk_i, rst_i)
  `COMMON_DFF_RST(lim_cpl_q, lim_cpl_d, clk_i, rst_i)
  `COMMON_DFF_RST(due_q, due_d, clk_i, rst_i)
  `COMMON_DFF_RST(ref_p_q, ref_p_d, clk_i, rst_i)
  `COMMON_DFF_RST(ref_np_q, ref_np_d, clk_i, rst_i)
  `COMMON_DFF_RST(ref_cpl_q, ref_cpl_d, clk_i, rst_i)
  `COMMON_DFF_RST_VAL(rr_q, rr_d, clk_i, rst_i, pcie_dll_pkg::FC_P)

  // An UpdateFC carries CREDITS_ALLOCATED, the advertised value until
  // credits are returned (#214); an infinite field is advertised, and
  // sent, as 0.
  assign updfc_req_o.valid = active && pick_ok;
  assign updfc_req_o.body  = pcie_dll_pkg::fc_body(pcie_dll_pkg::DLLP_UPDATEFC, pick,
                                                   pcie_dll_pkg::fc_get(adv_i, pick));
  assign recorded_o        = lim_p_q.recorded && lim_np_q.recorded && lim_cpl_q.recorded;
  assign avail_o.p         = pcie_dll_pkg::fc_show(lim_p_q.inf_hdr, lim_p_q.inf_data,
                                                 lim_p_q.limit, active);
  assign avail_o.np        = pcie_dll_pkg::fc_show(lim_np_q.inf_hdr, lim_np_q.inf_data,
                                                 lim_np_q.limit, active);
  assign avail_o.cpl       = pcie_dll_pkg::fc_show(lim_cpl_q.inf_hdr, lim_cpl_q.inf_data,
                                                 lim_cpl_q.limit, active);

  `COMMON_UNUSED_BITS(unused_rx_dllp, {rx_dllp_i.seq})

  `COMMON_ASSERT_NEVER(a_take_without_due, clk_i, rst_i, updfc_take_i && !updfc_req_o.valid)

endmodule

`default_nettype wire
