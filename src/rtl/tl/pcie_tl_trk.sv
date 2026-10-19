// Completion tracker of pcie_tl (wiki/src/rtl/tl/MAS.md#completion-tracker):
// the application's outstanding reads by tag, matching and checking each
// received completion, the completion timeout, the error events and
// Transactions Pending, and the packets it queues for rc
// (wiki/src/arch/tl/COMPLETIONS.md#requester-completions).
`default_nettype none

`include "common_macros.svh"

module pcie_tl_trk #(
  parameter int unsigned CPL_TIMEOUT_DIV = 1
) (
  input  logic                    clk_i,
  input  logic                    rst_i,
  // Completion beats, from the receive parser
  input  pcie_tl_pkg::t_rx_beat   cpl_beat_i,
  // This function's ID (cfg register state)
  input  logic [7:0]              bus_num_i,
  input  logic [4:0]              dev_num_i,
  // Reads taken on rq, and sent
  input  logic                    rq_rd_take_i,
  input  pcie_tl_pkg::t_rq_ctx    rq_rd_ctx_i,
  input  logic                    rq_sent_i,
  input  logic [2:0]              rq_sent_tag_i,
  input  logic                    cpl_timeout_dis_i,
  // A read's rc_done beat taken (rc stream): its tag is free
  input  logic                    rc_free_i,
  input  logic [2:0]              rc_free_tag_i,
  // Error events (tl_err_o bits 2, 4 to 8) and Transactions Pending
  output logic [8:0]              err_o,
  output logic                    np_pending_o,
  // Packets for rc, and their data DWs
  output logic                    desc_push_o,
  output pcie_tl_pkg::t_rc_desc   desc_o,
  output logic                    data_push_o,
  output logic [31:0]             data_o
);

  // ------------------------------------------------------------ timer
  // A prescaler pulse every 2**21 / (16 * CPL_TIMEOUT_DIV) cycles
  // (COMPLETIONS.md, completion timeout).
  localparam int unsigned PERIOD = (1 << 21) / (16 * CPL_TIMEOUT_DIV);
  localparam int unsigned PRE_W  = $clog2(PERIOD);

  logic [PRE_W-1:0] pre_q, pre_d;
  logic             pulse;
  logic             dis_q;           // cpl_timeout_dis_i, registered

  assign pulse = pre_q == PRE_W'(PERIOD - 1);
  assign pre_d = pulse ? '0 : pre_q + PRE_W'(1);

  `COMMON_DFF_RST(pre_q, pre_d, clk_i, rst_i)
  `COMMON_DFF_RST(dis_q, cpl_timeout_dis_i, clk_i, rst_i)

  // ------------------------------------------------------- reads by tag
  pcie_tl_pkg::t_trk_tag tg_q [pcie_tl_pkg::TAGS];
  pcie_tl_pkg::t_trk_tag tg_d [pcie_tl_pkg::TAGS];
  logic [pcie_tl_pkg::TAGS-1:0] expire;   // times out at this edge
  logic [pcie_tl_pkg::TAGS-1:0] act;

  // ------------------------------------------------ completion header
  pcie_tl_pkg::t_trk_rx  rx_q, rx_d;
  pcie_tl_pkg::t_trk_cur cur_q, cur_d;
  logic        fire_q, fire_d;       // the completion's eop was taken at the last edge
  logic [5:0]  idx;                  // index of this beat in its TLP
  logic        hdr;                  // the third header DW: decide
  logic        wdata;                // Fmt bit 1: the completion carries data
  logic [4:0]  typ;
  logic        ep;
  logic [9:0]  lfield;
  logic [2:0]  status;
  logic [11:0] bcf;
  logic [15:0] rid;
  logic [7:0]  tagf;
  logic [6:0]  la;
  logic [2:0]  t3;
  logic        match;
  logic [10:0] n;
  logic [12:0] bc13;
  logic [12:0] left13;
  logic [12:0] span;                 // bytes from the next byte due to the data's end
  logic        on_rcb;               // the data ends on a 64-byte boundary
  logic        last;
  logic        fits;                 // Byte Count, Lower Address and data end agree with the read
  logic        failed;

  assign idx    = cpl_beat_i.sop ? 6'd0 : rx_q.beat;
  assign hdr    = cpl_beat_i.valid && idx == 6'd2;
  assign wdata  = rx_q.dw0[6];
  assign typ    = rx_q.dw0[4:0];
  assign ep     = rx_q.dw0[22];
  assign lfield = {rx_q.dw0[17:16], rx_q.dw0[31:24]};
  assign status = rx_q.dw1[23:21];
  assign bcf    = {rx_q.dw1[19:16], rx_q.dw1[31:24]};
  assign rid    = {cpl_beat_i.data[7:0], cpl_beat_i.data[15:8]};
  assign tagf   = cpl_beat_i.data[23:16];
  assign la     = cpl_beat_i.data[30:24];
  assign t3     = tagf[2:0];

  // Matching (COMPLETIONS.md, matching): a plain completion for this
  // function, with a tag 0 to 7 whose read is outstanding and sent; a read
  // that times out at this edge has already ended.
  assign match  = typ == common_pkg::T_CPL && rid == {bus_num_i, dev_num_i, 3'd0}
               && tagf[7:3] == 5'd0 && tg_q[t3].act && tg_q[t3].snt && !expire[t3];

  // Checks of a Successful completion against its read (COMPLETIONS.md,
  // checks): data, Byte Count the bytes still due, Lower Address the next
  // byte's, and the data ending at the read's last DW or on a 64-byte
  // boundary before it.
  assign n      = !wdata ? 11'd0 : (lfield == 10'd0) ? 11'd1024 : {1'b0, lfield};
  assign bc13   = (bcf == 12'd0) ? 13'd4096 : {1'b0, bcf};
  assign left13 = {5'd0, tg_q[t3].left};
  assign span   = {n, 2'b00} - {11'd0, tg_q[t3].nxt[1:0]};
  assign on_rcb = (tg_q[t3].nxt[5:2] + n[3:0]) == 4'd0;
  assign last   = span >= left13;
  assign fits   = n != 11'd0 && bc13 == left13 && la == tg_q[t3].nxt
               && (last ? span < left13 + 13'd4 : on_rcb);
  assign failed = tg_q[t3].failed || ep;

  always_comb begin
    rx_d = rx_q;
    if (cpl_beat_i.valid) begin
      if (idx == 6'd0) begin
        rx_d.dw0 = cpl_beat_i.data;
      end
      if (idx == 6'd1) begin
        rx_d.dw1 = cpl_beat_i.data;
      end
      rx_d.beat = (idx == 6'd63) ? 6'd63 : idx + 6'd1;
    end
  end

  `COMMON_DFF_RST(rx_q, rx_d, clk_i, rst_i)

  // Data DWs of a delivered completion go to the rc data queue as they
  // arrive; a digest DW after them is not data.
  assign data_push_o = cpl_beat_i.valid && idx >= 6'd3 && cur_q.deliver
                    && cur_q.wr != cur_q.desc.dlen;
  assign data_o      = cpl_beat_i.data;

  always_comb begin
    cur_d = cur_q;
    if (fire_q) begin
      cur_d.pend = 1'b0;
    end
    if (data_push_o) begin
      cur_d.wr = cur_q.wr + 6'd1;
    end
    if (hdr) begin
      cur_d          = '0;
      cur_d.pend     = 1'b1;
      cur_d.desc.tag = t3;
      if (!match) begin
        cur_d.err[pcie_tl_pkg::E_UNEXP] = 1'b1;
      end else if (status == pcie_tl_pkg::CS_CRS) begin
        cur_d.err[pcie_tl_pkg::E_MAL] = 1'b1;
      end else if (status != pcie_tl_pkg::CS_SC) begin
        // The read ends with one status beat; reserved values read as UR.
        cur_d.push        = 1'b1;
        cur_d.desc.done   = 1'b1;
        if (status == pcie_tl_pkg::CS_CA) begin
          cur_d.err[pcie_tl_pkg::E_RX_CA] = 1'b1;
          cur_d.desc.status = pcie_tl_pkg::RC_CA;
        end else begin
          cur_d.err[pcie_tl_pkg::E_RX_UR] = 1'b1;
          cur_d.desc.status = pcie_tl_pkg::RC_UR;
        end
      end else if (!fits) begin
        cur_d.err[pcie_tl_pkg::E_MAL] = 1'b1;
      end else begin
        cur_d.err[pcie_tl_pkg::E_POIS_CPL] = ep;
        if (last && failed) begin
          cur_d.push        = 1'b1;
          cur_d.desc.done   = 1'b1;
          cur_d.desc.status = pcie_tl_pkg::RC_FAILED;
        end else if (!failed) begin
          cur_d.push        = 1'b1;
          cur_d.deliver     = 1'b1;
          cur_d.desc.done   = last;
          cur_d.desc.status = pcie_tl_pkg::RC_OK;
          cur_d.desc.dlen   = n[5:0];
        end
      end
    end
  end

  assign fire_d = cpl_beat_i.valid && cpl_beat_i.eop && idx >= 6'd2;

  `COMMON_DFF_RST(cur_q, cur_d, clk_i, rst_i)
  `COMMON_DFF_RST(fire_q, fire_d, clk_i, rst_i)

  // ------------------------------------------------- per-tag next state
  logic [7:0] left_nx;

  // Not the last: the span is below the bytes due (at most 128).
  assign left_nx = last ? 8'd0 : tg_q[t3].left - span[7:0];

  always_comb begin
    for (int t = 0; t < pcie_tl_pkg::TAGS; t++) begin
      act[t]    = tg_q[t].act;
      expire[t] = pulse && !dis_q && tg_q[t].act && tg_q[t].snt
               && tg_q[t].cnt == 5'(pcie_tl_pkg::TMO_STEPS);
    end
  end

  always_comb begin
    for (int t = 0; t < pcie_tl_pkg::TAGS; t++) begin
      tg_d[t]   = tg_q[t];
      if (dis_q) begin
        tg_d[t].cnt = 5'd0;
      end else if (pulse && tg_q[t].act && tg_q[t].snt) begin
        tg_d[t].cnt = tg_q[t].cnt + 5'd1;
      end
      if (expire[t]) begin
        tg_d[t].act = 1'b0;
      end
      if (rq_sent_i && rq_sent_tag_i == 3'(t)) begin
        tg_d[t].snt = 1'b1;
        tg_d[t].cnt = 5'd0;
      end
      if (rc_free_i && rc_free_tag_i == 3'(t)) begin
        tg_d[t].held = 1'b0;
      end
      if (rq_rd_take_i && rq_rd_ctx_i.tag == 3'(t)) begin
        tg_d[t].act    = 1'b1;
        tg_d[t].snt    = 1'b0;
        tg_d[t].held   = 1'b1;
        tg_d[t].failed = 1'b0;
        tg_d[t].cnt    = 5'd0;
        tg_d[t].left   = 8'(common_pkg::byte_total({5'd0, rq_rd_ctx_i.dw_len},
                                                    rq_rd_ctx_i.fbe, rq_rd_ctx_i.lbe));
        tg_d[t].nxt    = {rq_rd_ctx_i.addr[4:0], common_pkg::be_lo(rq_rd_ctx_i.fbe)};
      end
    end
    // A matched completion (an edge where none of the above touch its tag).
    if (hdr && match && status != pcie_tl_pkg::CS_CRS) begin
      if (status != pcie_tl_pkg::CS_SC) begin
        tg_d[t3].act = 1'b0;
      end else if (fits) begin
        tg_d[t3].failed = failed;
        tg_d[t3].left   = left_nx;
        tg_d[t3].nxt    = tg_q[t3].nxt + span[6:0];
        if (last) begin
          tg_d[t3].act = 1'b0;
        end
      end
    end
  end

  `COMMON_DFF_ARRAY_RST_EN(tg_q, tg_d, clk_i, 1'b1, rst_i)

  assign np_pending_o = |act;

  // ------------------------------------------------- packets for rc
  // A read that timed out queues its failed beat in a cycle with no
  // completion packet, once no completion of its tag is still arriving,
  // so its data packets go first.
  logic [pcie_tl_pkg::TAGS-1:0] tmo_q, tmo_d;
  logic [pcie_tl_pkg::TAGS-1:0] tmo_ok;
  logic                         tmo_push;
  logic [2:0]                   tmo_tag;
  logic                         cpl_push;

  assign cpl_push = fire_q && cur_q.push;

  always_comb begin
    tmo_ok = tmo_q;
    if (cur_q.pend && cur_q.push) begin
      tmo_ok[cur_q.desc.tag] = 1'b0;
    end
    tmo_tag = 3'd0;
    for (int t = pcie_tl_pkg::TAGS - 1; t >= 0; t--) begin
      if (tmo_ok[t]) begin
        tmo_tag = 3'(t);
      end
    end
  end

  assign tmo_push = !cpl_push && tmo_ok != '0;

  always_comb begin
    tmo_d = (tmo_q | expire);
    if (tmo_push) begin
      tmo_d[tmo_tag] = 1'b0;
    end
  end

  `COMMON_DFF_RST(tmo_q, tmo_d, clk_i, rst_i)

  always_comb begin
    desc_o = cur_q.desc;
    if (!cpl_push) begin
      desc_o        = '0;
      desc_o.tag    = tmo_tag;
      desc_o.status = pcie_tl_pkg::RC_FAILED;
      desc_o.done   = 1'b1;
    end
  end

  assign desc_push_o = cpl_push || tmo_push;

  always_comb begin
    err_o = fire_q ? cur_q.err : 9'd0;
    err_o[pcie_tl_pkg::E_TIMEOUT] = |expire;
  end

  // The application never reuses a tag in use (APP, requester requests); a
  // tag whose rc_done beat is taken at an edge may be taken again at it.
  `COMMON_ASSERT_NEVER(a_tag_in_use, clk_i, rst_i,
                       rq_rd_take_i && tg_q[rq_rd_ctx_i.tag].held
                       && !(rc_free_i && rc_free_tag_i == rq_rd_ctx_i.tag))
  // A read is sent once, after it is taken.
  `COMMON_ASSERT_NEVER(a_sent_unknown, clk_i, rst_i,
                       rq_sent_i && !(tg_q[rq_sent_tag_i].act && !tg_q[rq_sent_tag_i].snt))
  // A completion that passes the checks carries at most the read's 32 DWs.
  `COMMON_ASSERT_NEVER(a_cpl_len, clk_i, rst_i, hdr && match && fits && n > 11'd32)

endmodule

`default_nettype wire
