// Transmit request lines of pcie_tl (wiki/src/rtl/tl/MAS.md#transmit-lines):
// takes the application's requests on rq, the messages cfg asks for and
// the PME_TO_Ack the receive path asks for, into the transmit posted line
// (in the order taken) and non-posted line, and keeps the transmit epochs
// that order the non-posted line and the two completion queues behind the
// posted requests taken before them (wiki/src/arch/tl/ORDERING.md#ordering).
`default_nettype none

`include "common_macros.svh"

module pcie_tl_txq (
  input  logic                        clk_i,
  input  logic                        rst_i,
  input  logic                        bus_master_en_i,
  input  logic [1:0]                  d_state_i,
  // rq stream (APP)
  input  logic                        rq_valid_i,
  output logic                        rq_ready_o,
  input  logic                        rq_sop_i,
  input  logic                        rq_eop_i,
  input  logic [31:0]                 rq_data_i,
  input  logic                        rq_write_i,
  input  logic [63:0]                 rq_addr_i,
  input  logic [5:0]                  rq_len_i,
  input  logic [3:0]                  rq_first_be_i,
  input  logic [3:0]                  rq_last_be_i,
  input  logic [2:0]                  rq_tag_i,
  // Messages from cfg (TL_CFG)
  input  logic                        msg_valid_i,
  output logic                        msg_ready_o,
  input  logic [1:0]                  msg_kind_i,
  input  logic [63:0]                 msg_addr_i,
  input  logic [31:0]                 msg_data_i,
  // PME_TO_Ack, from the receive queues
  input  logic                        pme_ack_i,
  output logic                        pme_room_o,
  // The completion lines arm at their entries; their epochs
  input  logic                        arm_cc_i,
  input  logic                        arm_cf_i,
  output pcie_tl_pkg::t_ep            ep_cc_o,
  output pcie_tl_pkg::t_ep            ep_cf_o,
  // Posted line and its data, to the transmit arbiter
  output logic                        p_valid_o,     // the line holds an entry
  output logic                        p_ready_o,     // its head's data is all queued
  output pcie_tl_pkg::t_txp_ent       p_head_o,
  input  logic                        p_pop_i,
  output logic                        pd_valid_o,
  output logic [31:0]                 pd_head_o,
  input  logic                        pd_pop_i,
  // Non-posted line
  output logic                        np_valid_o,
  output pcie_tl_pkg::t_txnp_ent      np_head_o,
  input  logic                        np_pop_i,
  // An rq read taken (to the tracker)
  output logic                        rq_rd_take_o,
  output pcie_tl_pkg::t_rq_ctx        rq_rd_ctx_o
);

  localparam int unsigned D     = pcie_tl_pkg::TXP_D;
  localparam int unsigned PI_W  = $clog2(D);
  localparam int unsigned PD_AW = 6;     // posted data: two longest writes
  localparam int unsigned NP_AW = 2;

  // ------------------------------------------------------------ epochs
  pcie_tl_pkg::t_txeps           ep_q, ep_d;
  logic [pcie_tl_pkg::NTX-1:0]   arm_q, arm_d;
  logic                          p_push_any;
  logic                          np_push;

  always_comb begin
    ep_d  = ep_q;
    arm_d = arm_q;
    // A posted entry after an entry of a line starts that line's next epoch.
    if (p_push_any) begin
      for (int l = 0; l < pcie_tl_pkg::NTX; l++) begin
        if (arm_q[l]) begin
          ep_d[l]  = ep_q[l] + pcie_tl_pkg::EP_W'(1);
          arm_d[l] = 1'b0;
        end
      end
    end
    if (np_push) begin
      arm_d[pcie_tl_pkg::TX_NP] = 1'b1;
    end
    if (arm_cc_i) begin
      arm_d[pcie_tl_pkg::TX_CC] = 1'b1;
    end
    if (arm_cf_i) begin
      arm_d[pcie_tl_pkg::TX_CF] = 1'b1;
    end
  end

  `COMMON_DFF_RST(ep_q, ep_d, clk_i, rst_i)
  `COMMON_DFF_RST(arm_q, arm_d, clk_i, rst_i)

  assign ep_cc_o = ep_q[pcie_tl_pkg::TX_CC];
  assign ep_cf_o = ep_q[pcie_tl_pkg::TX_CF];

  // --------------------------------------------------- posted line, room
  // A flop queue: up to three entries go in at one edge (an rq write's sop
  // beat, a message, a PME_TO_Ack) and its head shows at the next. Each
  // source checks room for itself and the sources after it, so all three
  // fit together (MAS, transmit lines).
  pcie_tl_pkg::t_txp_ent p_mem_q [D];
  pcie_tl_pkg::t_txp_ent p_mem_d [D];
  logic [PI_W-1:0]       p_wr_q, p_wr_d;
  logic [PI_W-1:0]       p_rd_q, p_rd_d;
  logic [PI_W:0]         p_cnt_q, p_cnt_d;
  logic                  p_we;

  logic                  pd_full;
  logic                  pd_empty;
  logic [PD_AW:0]        pd_used_ram;
  logic [PD_AW+1:0]      pd_used;      // in the RAM and the head
  logic                  np_full;
  logic                  np_empty;
  logic [NP_AW:0]        np_used;

  logic                  allowed;
  logic                  rq_p_room;
  logic                  rq_np_room;

  assign allowed    = bus_master_en_i && d_state_i == pcie_tl_pkg::D0;
  assign rq_p_room  = p_cnt_q <= (PI_W + 1)'(D - 3)
                   && pd_used_ram <= (PD_AW + 1)'((1 << PD_AW) - pcie_tl_pkg::CPL_MAX);
  assign rq_np_room = !np_full;
  assign msg_ready_o = p_cnt_q <= (PI_W + 1)'(D - 2);
  assign pme_room_o  = p_cnt_q <= (PI_W + 1)'(D - 1);

  // --------------------------------------------------------- rq stream
  // A request is taken on its sop beat only while allowed and with room
  // in its line; a write's later beats are always taken.
  pcie_tl_pkg::t_rq_state rq_q, rq_d;
  logic                   rq_take;
  logic                   rq_sop_take;
  logic                   rq_wr_push;   // a write's entry goes in at its sop beat
  logic                   pd_push;

  assign rq_ready_o  = rq_q.busy || (allowed && (rq_write_i ? rq_p_room : rq_np_room));
  assign rq_take     = rq_valid_i && rq_ready_o;
  assign rq_sop_take = rq_take && !rq_q.busy;
  assign rq_wr_push  = rq_sop_take && rq_write_i;
  assign np_push     = rq_sop_take && !rq_write_i;
  assign pd_push     = rq_take && (rq_q.busy || rq_write_i);

  always_comb begin
    rq_d = rq_q;
    if (rq_sop_take) begin
      rq_d.busy   = rq_write_i && !rq_eop_i;
      rq_d.beat   = 6'd1;
      rq_d.dw_len = rq_len_i;
    end else if (rq_take) begin
      rq_d.busy = !rq_eop_i;
      rq_d.beat = rq_q.beat + 6'd1;
    end
  end

  `COMMON_DFF_RST(rq_q, rq_d, clk_i, rst_i)

  // -------------------------------------------------- posted entries
  logic                  msg_push;
  logic                  pme_push;
  pcie_tl_pkg::t_txp_ent e_rq;
  pcie_tl_pkg::t_txp_ent e_msg;
  pcie_tl_pkg::t_txp_ent e_pme;
  logic [PI_W-1:0]       i_msg;
  logic [PI_W-1:0]       i_pme;
  logic [1:0]            n_push;

  assign msg_push   = msg_valid_i && msg_ready_o;
  assign pme_push   = pme_ack_i;
  assign p_push_any = rq_wr_push || msg_push || pme_push;
  assign n_push     = {1'b0, rq_wr_push} + {1'b0, msg_push} + {1'b0, pme_push};
  // Taken together, they go in as rq, message, PME_TO_Ack.
  assign i_msg      = p_wr_q + PI_W'(rq_wr_push);
  assign i_pme      = p_wr_q + PI_W'(rq_wr_push) + PI_W'(msg_push);

  always_comb begin
    e_rq        = '0;
    e_rq.kind   = pcie_tl_pkg::PX_RQ;
    e_rq.addr   = rq_addr_i[63:2];
    e_rq.dw_len = rq_len_i;
    e_rq.fbe    = rq_first_be_i;
    e_rq.lbe    = rq_last_be_i;
    e_rq.ep     = ep_d;
    // An interrupt write is a 1-DW write of msg_data to msg_addr, all bytes
    // enabled; an error message has its kind's code, routed to the Root
    // Complex (TLP.md).
    e_msg       = '0;
    e_msg.ep    = ep_d;
    if (msg_kind_i == 2'd0) begin
      e_msg.kind   = pcie_tl_pkg::PX_IRQ;
      e_msg.addr   = msg_addr_i[63:2];
      e_msg.dw_len = 6'd1;
      e_msg.fbe    = 4'hF;
      e_msg.data   = msg_data_i;
    end else begin
      e_msg.kind = pcie_tl_pkg::PX_MSG;
      e_msg.rrr  = pcie_tl_pkg::R_TO_RC;
      case (msg_kind_i)
        2'd1:    e_msg.code = pcie_tl_pkg::MSG_ERR_COR;
        2'd2:    e_msg.code = pcie_tl_pkg::MSG_ERR_NONFATAL;
        default: e_msg.code = pcie_tl_pkg::MSG_ERR_FATAL;
      endcase
    end
    e_pme      = '0;
    e_pme.kind = pcie_tl_pkg::PX_MSG;
    e_pme.code = pcie_tl_pkg::MSG_PME_TO_ACK;
    e_pme.rrr  = pcie_tl_pkg::R_GATHER;
    e_pme.ep   = ep_d;
  end

  always_comb begin
    for (int i = 0; i < D; i++) begin
      p_mem_d[i] = p_mem_q[i];
    end
    if (rq_wr_push) begin
      p_mem_d[p_wr_q] = e_rq;
    end
    if (msg_push) begin
      p_mem_d[i_msg] = e_msg;
    end
    if (pme_push) begin
      p_mem_d[i_pme] = e_pme;
    end
    p_wr_d  = p_wr_q + PI_W'(n_push);
    p_rd_d  = p_rd_q + PI_W'(p_pop_i);
    p_cnt_d = p_cnt_q + (PI_W + 1)'(n_push) - (PI_W + 1)'(p_pop_i);
  end

  assign p_we = p_push_any;

  `COMMON_DFF_ARRAY_EN(p_mem_q, p_mem_d, clk_i, p_we)
  `COMMON_DFF_RST(p_wr_q, p_wr_d, clk_i, rst_i)
  `COMMON_DFF_RST(p_rd_q, p_rd_d, clk_i, rst_i)
  `COMMON_DFF_RST(p_cnt_q, p_cnt_d, clk_i, rst_i)

  assign p_valid_o = p_cnt_q != '0;
  assign p_head_o  = p_mem_q[p_rd_q];
  // An rq write's entry goes in at its sop beat; it may go out once all
  // its data is queued (the posted data queue holds only its data first).
  assign pd_used   = {1'b0, pd_used_ram} + (PD_AW + 2)'(pd_valid_o);
  assign p_ready_o = p_head_o.kind != pcie_tl_pkg::PX_RQ
                  || pd_used >= (PD_AW + 2)'(p_head_o.dw_len);

  pcie_tl_fifo #(
    .WIDTH  (32),
    .ADDR_W (PD_AW)
  ) u_pd (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (pd_push),
    .push_data_i  (rq_data_i),
    .pop_i        (pd_pop_i),
    .head_valid_o (pd_valid_o),
    .head_o       (pd_head_o),
    .empty_o      (pd_empty),
    .full_o       (pd_full),
    .ram_used_o   (pd_used_ram)
  );

  // ----------------------------------------------------- non-posted line
  pcie_tl_pkg::t_txnp_ent np_in;

  always_comb begin
    np_in        = '0;
    np_in.addr   = rq_addr_i[63:2];
    np_in.dw_len = rq_len_i;
    np_in.fbe    = rq_first_be_i;
    np_in.lbe    = rq_last_be_i;
    np_in.tag    = rq_tag_i;
    np_in.ep     = ep_q[pcie_tl_pkg::TX_NP];
  end

  pcie_tl_fifo #(
    .WIDTH  ($bits(pcie_tl_pkg::t_txnp_ent)),
    .ADDR_W (NP_AW)
  ) u_np (
    .clk_i        (clk_i),
    .rst_i        (rst_i),
    .push_i       (np_push),
    .push_data_i  (np_in),
    .pop_i        (np_pop_i),
    .head_valid_o (np_valid_o),
    .head_o       (np_head_o),
    .empty_o      (np_empty),
    .full_o       (np_full),
    .ram_used_o   (np_used)
  );

  assign rq_rd_take_o = np_push;
  always_comb begin
    rq_rd_ctx_o        = '0;
    rq_rd_ctx_o.tag    = rq_tag_i;
    rq_rd_ctx_o.addr   = rq_addr_i[11:2];
    rq_rd_ctx_o.dw_len = rq_len_i;
    rq_rd_ctx_o.fbe    = rq_first_be_i;
    rq_rd_ctx_o.lbe    = rq_last_be_i;
  end

  `COMMON_UNUSED_BITS(unused_q, {pd_full, pd_empty, np_empty, np_used, rq_addr_i[1:0],
                                 msg_addr_i[1:0]})

  // Application obligations (APP): a request is 1 to 32 DWs and stays in
  // one 4 KiB block; a read is one beat; a write's eop is its last DW; a
  // packet starts with sop.
  logic [12:0] rq_end;
  assign rq_end = {1'b0, rq_addr_i[11:0]} + {5'd0, rq_len_i, 2'b00};
  `COMMON_ASSERT_NEVER(a_rq_len, clk_i, rst_i,
                       rq_sop_take && (rq_len_i == 6'd0 || rq_len_i > 6'd32))
  `COMMON_ASSERT_NEVER(a_rq_4k, clk_i, rst_i, rq_sop_take && rq_end > 13'd4096)
  `COMMON_ASSERT_NEVER(a_rq_read_one_beat, clk_i, rst_i, rq_sop_take && !rq_write_i && !rq_eop_i)
  `COMMON_ASSERT_NEVER(a_rq_sop, clk_i, rst_i, rq_take && rq_sop_i == rq_q.busy)
  `COMMON_ASSERT_NEVER(a_rq_eop, clk_i, rst_i,
                       rq_take && rq_write_i
                       && rq_eop_i != (rq_q.busy ? rq_q.beat == rq_q.dw_len - 6'd1
                                                 : rq_len_i == 6'd1))
  // Room as each source checked it: the posted line never overflows.
  `COMMON_ASSERT_NEVER(a_p_overflow, clk_i, rst_i, p_cnt_d > (PI_W + 1)'(D))
  `COMMON_ASSERT_NEVER(a_pme_room, clk_i, rst_i, pme_ack_i && !pme_room_o)
  `COMMON_ASSERT_NEVER(a_p_pop_empty, clk_i, rst_i, p_pop_i && !p_valid_o)
  `COMMON_ASSERT_NEVER(a_pd_overflow, clk_i, rst_i, pd_push && pd_full)

endmodule

`default_nettype wire
