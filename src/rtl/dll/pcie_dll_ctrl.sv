// Data link control of pcie_dll (wiki/src/rtl/dll/MAS.md#control): the
// control states DL_Inactive, FC_INIT1, FC_INIT2 and DL_Active
// (wiki/src/arch/dll/HAS.md#data-link-control-states) and the InitFC1 and
// InitFC2 groups they send (wiki/src/arch/dll/FLOW_CONTROL.md#initialisation).
// The partner's credits are recorded by the flow-control block, which
// reports when all three classes are.
`default_nettype none

`include "common_macros.svh"

module pcie_dll_ctrl (
  input  logic                    clk_i,
  input  logic                    rst_i,
  input  logic                    link_up_i,       // phy LinkUp
  input  pcie_dll_pkg::t_fc_vals  adv_i,           // the credits this side advertises
  input  pcie_dll_pkg::t_rx_dllp  rx_dllp_i,       // a good received DLLP
  input  logic                    rx_tlp_good_i,   // a good received TLP (#213; 0 until then)
  input  logic                    fc_recorded_i,   // all three classes recorded
  input  logic                    tx_busy_i,       // a packet is on the phy stream
  input  logic                    initfc_take_i,   // the arbiter takes initfc_req_o
  output pcie_dll_pkg::t_dllp_req initfc_req_o,    // the next InitFC of the group
  output pcie_dll_pkg::t_dl_state state_o,
  output logic                    flush_o,         // DL_Inactive values everywhere
  output logic                    enter_active_o   // this edge enters DL_Active
);

  // DL_Inactive, the state reset and a link-down hold.
  localparam pcie_dll_pkg::t_ctrl_state CTRL_RESET = '{
    st:        pcie_dll_pkg::DL_INACTIVE,
    grp_left:  2'd0,
    grp_timer: 7'd0,
    done:      1'b0
  };
  // A fresh group on entry to FC_INIT1 or FC_INIT2.
  localparam logic [1:0] GROUP_SIZE  = 2'd3;
  localparam logic [6:0] GROUP_LAST  = 7'd127;  // the 128th cycle starts the next group

  pcie_dll_pkg::t_ctrl_state state_q, state_d;

  logic                    in_init;      // FC_INIT1 or FC_INIT2
  logic                    group_idle;   // no InitFC of the group left or on the stream
  logic                    rx_done;      // this cycle's DLLP or TLP ends FC_INIT2
  pcie_dll_pkg::t_fc_class grp_cls;      // class of the next InitFC
  logic [7:0]              grp_base;     // InitFC1 or InitFC2

  assign in_init    = state_q.st == pcie_dll_pkg::DL_INIT1 || state_q.st == pcie_dll_pkg::DL_INIT2;
  assign group_idle = state_q.grp_left == 2'd0 && !tx_busy_i;
  assign rx_done    = rx_tlp_good_i
                   || (rx_dllp_i.valid && (rx_dllp_i.kind == pcie_dll_pkg::RX_INITFC2
                                        || rx_dllp_i.kind == pcie_dll_pkg::RX_UPDATEFC));
  assign grp_cls    = pcie_dll_pkg::t_fc_class'(GROUP_SIZE - state_q.grp_left);
  assign grp_base   = state_q.st == pcie_dll_pkg::DL_INIT2 ? pcie_dll_pkg::DLLP_INITFC2
                                                          : pcie_dll_pkg::DLLP_INITFC1;

  always_comb begin
    state_d = state_q;
    if (in_init && state_q.grp_timer != GROUP_LAST) begin
      state_d.grp_timer = state_q.grp_timer + 7'd1;
    end
    if (initfc_take_i) begin
      state_d.grp_left = state_q.grp_left - 2'd1;
    end
    if (state_q.st == pcie_dll_pkg::DL_INIT2 && rx_done) begin
      state_d.done = 1'b1;
    end
    if (!link_up_i) begin
      state_d = CTRL_RESET;
    end else begin
      case (state_q.st)
        pcie_dll_pkg::DL_INACTIVE: begin
          state_d.st        = pcie_dll_pkg::DL_INIT1;
          state_d.grp_left  = GROUP_SIZE;
          state_d.grp_timer = 7'd0;
        end
        pcie_dll_pkg::DL_INIT1, pcie_dll_pkg::DL_INIT2: begin
          if (group_idle && ((state_q.st == pcie_dll_pkg::DL_INIT1 && fc_recorded_i)
                          || (state_q.st == pcie_dll_pkg::DL_INIT2 && state_q.done))) begin
            // All recorded (FC_INIT1) or done (FC_INIT2), and the group
            // being sent is complete: the next state, with a fresh group
            // for FC_INIT2.
            state_d.st        = state_q.st == pcie_dll_pkg::DL_INIT1 ? pcie_dll_pkg::DL_INIT2
                                                                    : pcie_dll_pkg::DL_ACTIVE;
            state_d.grp_left  = state_q.st == pcie_dll_pkg::DL_INIT1 ? GROUP_SIZE : 2'd0;
            state_d.grp_timer = 7'd0;
            state_d.done      = 1'b0;
          end else if (state_q.grp_left == 2'd0 && state_q.grp_timer == GROUP_LAST) begin
            // The resend: a new group every 128 cycles from the last start.
            state_d.grp_left  = GROUP_SIZE;
            state_d.grp_timer = 7'd0;
          end
        end
        default: begin
        end
      endcase
    end
  end

  `COMMON_DFF_RST_VAL(state_q, state_d, clk_i, rst_i, CTRL_RESET)

  assign initfc_req_o.valid = in_init && state_q.grp_left != 2'd0;
  assign initfc_req_o.body  = pcie_dll_pkg::fc_body(grp_base, grp_cls,
                                                    pcie_dll_pkg::fc_get(adv_i, grp_cls));
  assign state_o            = state_q.st;
  assign flush_o            = state_q.st == pcie_dll_pkg::DL_INACTIVE || !link_up_i;
  assign enter_active_o     = state_q.st != pcie_dll_pkg::DL_ACTIVE
                           && state_d.st == pcie_dll_pkg::DL_ACTIVE;

  // Only the kind of a received DLLP matters here.
  `COMMON_UNUSED_BITS(unused_rx_dllp, {rx_dllp_i.cls, rx_dllp_i.val, rx_dllp_i.seq})

  // A group is taken only while one is pending, and never outside DL_Init.
  `COMMON_ASSERT_NEVER(a_take_without_group, clk_i, rst_i, initfc_take_i && !initfc_req_o.valid)
  // FC_INIT1 is left only with every class recorded.
  `COMMON_ASSERT_NEVER(a_init2_unrecorded, clk_i, rst_i,
                       state_q.st == pcie_dll_pkg::DL_INIT1
                       && state_d.st == pcie_dll_pkg::DL_INIT2 && !fc_recorded_i)

endmodule

`default_nettype wire
