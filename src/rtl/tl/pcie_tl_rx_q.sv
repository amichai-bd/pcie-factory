// Receive queues of pcie_tl (wiki/src/rtl/tl/MAS.md#receive-queues): the
// posted queue with its data, the two non-posted lines, the posted epochs
// that order every line behind the posted requests received before it,
// the posted requests that leave at the queue head, and the credit
// release (wiki/src/arch/tl/ORDERING.md#receive-queues,
// wiki/src/arch/tl/HAS.md#credit-release).
`default_nettype none

`include "common_macros.svh"

module pcie_tl_rx_q #(
  parameter int unsigned FC_PH  = 8,
  parameter int unsigned FC_PD  = 32,
  parameter int unsigned FC_NPH = 8
) (
  input  logic                    clk_i,
  input  logic                    rst_i,
  // From the receive parser
  input  logic                    pq_push_i,
  input  pcie_tl_pkg::t_pq_ent    pq_ent_i,
  input  logic                    cq_push_i,
  input  pcie_tl_pkg::t_cq_ent    cq_ent_i,
  input  logic                    cfg_push_i,
  input  pcie_tl_pkg::t_cfg_ent   cfg_ent_i,
  input  logic                    pd_push_i,
  input  logic [31:0]             pd_data_i,
  input  logic                    np_mal_i,
  input  logic [3:0]              np_mal_d_i,
  // Posted queue head and its data, to the cq stream and the register engine
  output logic                    pq_valid_o,
  output pcie_tl_pkg::t_pq_ent    pq_head_o,
  input  logic                    pq_pop_cq_i,    // a BAR0 write's eop taken on cq
  input  logic                    pq_pop_reg_i,   // a BAR2 write's last access taken
  output logic                    pd_valid_o,
  output logic [31:0]             pd_head_o,
  input  logic                    pd_pop_i,
  // Non-posted lines: heads and whether every earlier posted request left
  output logic                    cq_valid_o,
  output pcie_tl_pkg::t_cq_ent    cq_head_o,
  output logic                    cq_ok_o,
  input  logic                    cq_pop_i,       // the read taken on cq
  output logic                    cfg_valid_o,
  output pcie_tl_pkg::t_cfg_ent   cfg_head_o,
  output logic                    cfg_ok_o,
  input  logic                    cfg_pop_i,      // the request left (access taken or UR handed on)
  // Slot of the completion former (#291): PME_TO_Ack
  input  logic                    pme_room_i,     // the transmit posted line has room for it
  output logic                    pme_ack_o,      // one PME_Turn_Off left: send PME_TO_Ack
  // Slot of the tracker (#292): the rc line's epoch and the oldest posted
  input  logic                    rc_arrive_i,    // a completion arrives: arms the rc line
  output pcie_tl_pkg::t_ep        rc_ep_o,        // its epoch, with rc_arrive_i
  output logic                    p_none_o,       // no posted request held
  output logic                    p_known_o,      // the oldest one is at the head (pq_head_o.ep)
  // Credit release (HAS, credit release)
  output logic                    fc_rel_p_o,
  output logic [3:0]              fc_rel_pd_o,
  output logic                    fc_rel_np_o,
  output logic [3:0]              fc_rel_npd_o
);

  // Sizes (MAS, receive queues): each queue holds the advertised credits.
  localparam int unsigned PQ_AW = (FC_PH  > 2) ? $clog2(FC_PH)     : 1;
  localparam int unsigned PD_AW = $clog2(FC_PD * 4);
  localparam int unsigned NQ_AW = (FC_NPH > 2) ? $clog2(FC_NPH)    : 1;

  // ---------------------------------------------------------- epochs
  pcie_tl_pkg::t_eps      ep_q, ep_d;       // current epoch per line
  logic [pcie_tl_pkg::NLINE-1:0] arm_q, arm_d;   // an entry of the line arrived since its last step
  pcie_tl_pkg::t_pq_ent   pq_in;
  pcie_tl_pkg::t_cq_ent   cq_in;
  pcie_tl_pkg::t_cfg_ent  cfg_in;

  always_comb begin
    ep_d  = ep_q;
    arm_d = arm_q;
    pq_in = pq_ent_i;
    // A posted entry after an entry of a line starts that line's next epoch.
    if (pq_push_i) begin
      for (int l = 0; l < pcie_tl_pkg::NLINE; l++) begin
        if (arm_q[l]) begin
          ep_d[l]  = ep_q[l] + pcie_tl_pkg::EP_W'(1);
          arm_d[l] = 1'b0;
        end
      end
      pq_in.ep = ep_d;
    end
    if (cq_push_i) begin
      arm_d[pcie_tl_pkg::LINE_CQ] = 1'b1;
    end
    if (cfg_push_i) begin
      arm_d[pcie_tl_pkg::LINE_CFG] = 1'b1;
    end
    if (rc_arrive_i) begin
      arm_d[pcie_tl_pkg::LINE_RC] = 1'b1;
    end
    cq_in     = cq_ent_i;
    cq_in.ep  = ep_q[pcie_tl_pkg::LINE_CQ];
    cfg_in    = cfg_ent_i;
    cfg_in.ep = ep_q[pcie_tl_pkg::LINE_CFG];
  end

  `COMMON_DFF_RST(ep_q, ep_d, clk_i, rst_i)
  `COMMON_DFF_RST(arm_q, arm_d, clk_i, rst_i)

  assign rc_ep_o = ep_q[pcie_tl_pkg::LINE_RC];

  // ---------------------------------------------------------- queues
  logic pq_pop;
  logic pq_empty;
  logic pq_full;
  logic pd_empty;
  logic pd_full;
  logic cq_empty;
  logic cq_full;
  logic cfg_empty;
  logic cfg_full;

  pcie_tl_fifo #(
    .WIDTH  ($bits(pcie_tl_pkg::t_pq_ent)),
    .ADDR_W (PQ_AW)
  ) u_pq (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (pq_push_i),
    .push_data_i  (pq_in),
    .pop_i        (pq_pop),
    .head_valid_o (pq_valid_o),
    .head_o       (pq_head_o),
    .empty_o      (pq_empty),
    .full_o       (pq_full)
  );

  pcie_tl_fifo #(
    .WIDTH  (32),
    .ADDR_W (PD_AW)
  ) u_pd (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (pd_push_i),
    .push_data_i  (pd_data_i),
    .pop_i        (pd_pop_i),
    .head_valid_o (pd_valid_o),
    .head_o       (pd_head_o),
    .empty_o      (pd_empty),
    .full_o       (pd_full)
  );

  pcie_tl_fifo #(
    .WIDTH  ($bits(pcie_tl_pkg::t_cq_ent)),
    .ADDR_W (NQ_AW)
  ) u_npq_cq (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (cq_push_i),
    .push_data_i  (cq_in),
    .pop_i        (cq_pop_i),
    .head_valid_o (cq_valid_o),
    .head_o       (cq_head_o),
    .empty_o      (cq_empty),
    .full_o       (cq_full)
  );

  pcie_tl_fifo #(
    .WIDTH  ($bits(pcie_tl_pkg::t_cfg_ent)),
    .ADDR_W (NQ_AW)
  ) u_npq_cfg (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (cfg_push_i),
    .push_data_i  (cfg_in),
    .pop_i        (cfg_pop_i),
    .head_valid_o (cfg_valid_o),
    .head_o       (cfg_head_o),
    .empty_o      (cfg_empty),
    .full_o       (cfg_full)
  );

  // ------------------------------------------------ posted head, order
  // Posted requests tl does not pass on leave at the head; PME_Turn_Off
  // once the transmit posted line has room for its acknowledgment.
  logic pq_drop;

  assign pq_drop   = pq_valid_o && (pq_head_o.kind == pcie_tl_pkg::PK_DISCARD
                                    || (pq_head_o.kind == pcie_tl_pkg::PK_PME && pme_room_i));
  assign pq_pop    = pq_pop_cq_i || pq_pop_reg_i || pq_drop;
  assign pme_ack_o = pq_drop && pq_head_o.kind == pcie_tl_pkg::PK_PME;

  // The oldest posted request held is the head, which stays in the queue
  // until it leaves; right after a push into an empty queue it is still
  // loading, and no line passes it then.
  assign p_none_o  = pq_empty;
  assign p_known_o = pq_valid_o;
  // A line head that has once seen every earlier posted request leave stays
  // free to go (ok_q) while a later posted request loads into an empty
  // posted queue.
  logic cq_ok_now;
  logic cfg_ok_now;
  logic cq_ok_q, cq_ok_d;
  logic cfg_ok_q, cfg_ok_d;

  assign cq_ok_now  = pq_empty || (pq_valid_o && pcie_tl_pkg::ep_after(
                        pq_head_o.ep[pcie_tl_pkg::LINE_CQ], cq_head_o.ep));
  assign cfg_ok_now = pq_empty || (pq_valid_o && pcie_tl_pkg::ep_after(
                        pq_head_o.ep[pcie_tl_pkg::LINE_CFG], cfg_head_o.ep));
  assign cq_ok_o    = cq_ok_now || cq_ok_q;
  assign cfg_ok_o   = cfg_ok_now || cfg_ok_q;
  assign cq_ok_d    = cq_valid_o && cq_ok_o && !cq_pop_i;
  assign cfg_ok_d   = cfg_valid_o && cfg_ok_o && !cfg_pop_i;

  `COMMON_DFF_RST(cq_ok_q, cq_ok_d, clk_i, rst_i)
  `COMMON_DFF_RST(cfg_ok_q, cfg_ok_d, clk_i, rst_i)

  // --------------------------------------------------------- release
  // Posted: one leaves per cycle at most; the pulse is registered.
  logic       rel_p_q;
  logic [3:0] rel_pd_q;
  logic [3:0] rel_pd_d;

  assign rel_pd_d = pq_pop ? pq_head_o.rel_d : 4'd0;

  `COMMON_DFF_RST(rel_p_q, pq_pop, clk_i, rst_i)
  `COMMON_DFF_RST(rel_pd_q, rel_pd_d, clk_i, rst_i)

  // Non-posted: up to three leave in one cycle (a cq read, the cfg line, a
  // malformed TLP); what cannot go in the next cycle is owed and goes one
  // header per cycle, with up to 8 data credits each. The data owed is
  // never more than 8 per header owed.
  logic [7:0]  owe_h_q, owe_h_d;
  logic [10:0] owe_d_q, owe_d_d;
  logic [1:0]  add_h;
  logic [4:0]  add_d;
  logic        rel_np;
  logic [3:0]  rel_npd;

  assign add_h   = {1'b0, cq_pop_i} + {1'b0, cfg_pop_i} + {1'b0, np_mal_i};
  assign add_d   = (cfg_pop_i ? {1'b0, cfg_head_o.rel_d} : 5'd0)
                 + (np_mal_i ? {1'b0, np_mal_d_i} : 5'd0);
  assign rel_np  = owe_h_q != 8'd0;
  assign rel_npd = (owe_d_q > 11'd8) ? 4'd8 : owe_d_q[3:0];

  always_comb begin
    owe_h_d = owe_h_q - {7'd0, rel_np} + {6'd0, add_h};
    owe_d_d = owe_d_q - {7'd0, rel_npd} + {6'd0, add_d};
  end

  `COMMON_DFF_RST(owe_h_q, owe_h_d, clk_i, rst_i)
  `COMMON_DFF_RST(owe_d_q, owe_d_d, clk_i, rst_i)

  assign fc_rel_p_o   = rel_p_q;
  assign fc_rel_pd_o  = rel_pd_q;
  assign fc_rel_np_o  = rel_np;
  assign fc_rel_npd_o = rel_npd;

  `COMMON_UNUSED_BITS(unused_full, {pq_full, pd_full, pd_empty, cq_full, cq_empty, cfg_full,
                                    cfg_empty})

  // A credit-correct host never fills a queue (ORDERING.md, credits).
  `COMMON_ASSERT_NEVER(a_pq_overflow, clk_i, rst_i, pq_push_i && pq_full)
  `COMMON_ASSERT_NEVER(a_pd_overflow, clk_i, rst_i, pd_push_i && pd_full)
  `COMMON_ASSERT_NEVER(a_cq_overflow, clk_i, rst_i, cq_push_i && cq_full)
  `COMMON_ASSERT_NEVER(a_cfg_overflow, clk_i, rst_i, cfg_push_i && cfg_full)
  // One posted request leaves at a time.
  `COMMON_ASSERT_NEVER(a_one_posted_pop, clk_i, rst_i,
                       (pq_pop_cq_i && pq_pop_reg_i) || ((pq_pop_cq_i || pq_pop_reg_i) && pq_drop))
  `COMMON_ASSERT_NEVER(a_pop_reg_kind, clk_i, rst_i,
                       pq_pop_reg_i && pq_head_o.kind != pcie_tl_pkg::PK_BAR2)
  `COMMON_ASSERT_NEVER(a_pop_cq_kind, clk_i, rst_i,
                       pq_pop_cq_i && pq_head_o.kind != pcie_tl_pkg::PK_CQ)
  // Lines leave only once every earlier posted request has left.
  `COMMON_ASSERT_NEVER(a_cq_order, clk_i, rst_i, cq_pop_i && !cq_ok_o)
  `COMMON_ASSERT_NEVER(a_cfg_order, clk_i, rst_i, cfg_pop_i && !cfg_ok_o)
  // Non-posted credits owed never exceed what the host can have sent.
  `COMMON_ASSERT_NEVER(a_owed_bound, clk_i, rst_i, owe_h_q > 8'(FC_NPH))
  `COMMON_ASSERT_NEVER(a_owed_data, clk_i, rst_i, owe_d_q > {owe_h_q, 3'd0})

endmodule

`default_nettype wire
