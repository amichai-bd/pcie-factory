// pcie_rc_tl requester (u_req): the one endpoint request
// (wiki/src/rtl/rc_tl/REQUESTER.md): forms its TLP for the transmit
// arbiter, holds the rolling tag, matches and checks its completions,
// streams its result beats to the front end, and ends it by completion,
// completion timeout or link down (MAS, Reset and link down).
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_req #(
  parameter int unsigned CPL_TIMEOUT_DIV = 1   // 1..1024, checked by the top
) (
  input  logic                     clk_i,
  input  logic                     rst_i,       // rst only: link down ends the request
  input  logic                     dl_up_i,
  input  pcie_rc_tl_pkg::t_req_cmd cmd_i,       // an endpoint command taken at this edge
  input  logic [7:0]               pri_bus_i,   // Primary Bus Number, from the header's flops
  // Transmit
  output pcie_rc_tl_pkg::t_tx_tlp  tx_o,
  input  logic                     grant_i,
  input  logic                     np_sop_i,    // the link took the request's sop
  input  logic                     p_eop_i,     // the link took the posted write's eop
  // Completions, from the receive parser
  input  pcie_rc_tl_pkg::t_cpl_rx  cpl_i,
  // Results and error record events at this edge, to the front end
  output pcie_rc_tl_pkg::t_res     res_o,
  output logic [pcie_rc_tl_pkg::ERR_W-1:0] err_o
);

  // Completion timeout: N = floor(2**21 / CPL_TIMEOUT_DIV) cycles, 2048 or
  // more (REQUESTER.md, Completion timeout).
  // A divider of 0 is refused by the top's parameter check; the guard only
  // keeps elaboration from dividing by 0 before that check reports it.
  localparam int unsigned TMO_D = (CPL_TIMEOUT_DIV == 0) ? 1 : CPL_TIMEOUT_DIV;
  localparam int unsigned TMO_N = (1 << 21) / TMO_D;
  localparam int unsigned TMO_W = $clog2(TMO_N);

  pcie_rc_tl_pkg::t_req_st rq_q, rq_d;
  pcie_rc_tl_pkg::t_cpl_st cp_q, cp_d;
  logic [4:0]              tag_q, tag_d;
  logic [TMO_W-1:0]        cnt_q, cnt_d;

  logic is_cfg;
  logic is_wr;
  logic posted;

  assign is_cfg = !rq_q.op[1];
  assign is_wr  = rq_q.op[0];
  assign posted = rq_q.op == 2'd3;

  // ------------------------------------------------------ the TLP
  // REQUESTER.md, Requests: a 3-DW header; CFG_* as Type 0 with Length 1,
  // MEM_RD with Length len, MEM_WR with Length 1 and the data DW.
  always_comb begin
    tx_o       = '0;
    tx_o.valid = rq_q.act && rq_q.txq;
    tx_o.wdata = is_wr;
    tx_o.typ   = is_cfg ? common_pkg::T_CFG0 : common_pkg::T_MEM;
    tx_o.dw0   = pcie_rc_tl_pkg::req_dw0({1'b0, is_wr, 1'b0}, tx_o.typ,
                                         (rq_q.op == 2'd2) ? {4'd0, rq_q.dw_n} : 10'd1);
    tx_o.dw1   = pcie_rc_tl_pkg::req_dw1(rq_q.rid, posted ? 5'd0 : rq_q.tag,
                                         (rq_q.op == 2'd2) ? rq_q.lbe : 4'd0, rq_q.fbe);
    tx_o.dw2   = is_cfg ? pcie_rc_tl_pkg::cfg_dw2(rq_q.addr[27:2])
                        : common_pkg::be_word({rq_q.addr[31:2], 2'b00});
    tx_o.data  = rq_q.data;
  end

  // ------------------------------------------------------ completion checks
  // At the third header DW (REQUESTER.md, Completion matching).
  logic       match;
  logic [8:0] rem_dw;        // DWs from the next byte's DW to the last byte's
  logic       lastc;
  logic       past;          // data past the read's last DW
  logic       short_bad;     // ends before it, not on a 64-byte boundary
  logic       mal;
  logic [7:0] span;          // bytes from the next byte to the data's end
  pcie_rc_tl_pkg::t_cpl_st dec;

  assign match     = rq_q.act && rq_q.sent && !cpl_i.locked && cpl_i.rid == rq_q.rid
                  && cpl_i.tag == {3'd0, rq_q.tag};
  assign rem_dw    = ({1'b0, rq_q.left} + {7'd0, rq_q.nxt[1:0]} + 9'd3) >> 2;
  assign lastc     = {2'd0, rem_dw} == cpl_i.dw_n;
  assign past      = cpl_i.dw_n > {2'd0, rem_dw};
  assign short_bad = !lastc && !past && (rq_q.nxt[5:2] + cpl_i.dw_n[3:0]) != 4'd0;
  assign span      = {cpl_i.dw_n[5:0], 2'b00} - {6'd0, rq_q.nxt[1:0]};

  always_comb begin
    if (is_cfg) begin
      // A configuration read carries 1 DW, a configuration write none.
      mal = is_wr ? cpl_i.wdata : (!cpl_i.wdata || cpl_i.dw_n != 11'd1);
    end else begin
      mal = !cpl_i.wdata || cpl_i.bc != {4'd0, rq_q.left} || cpl_i.la != rq_q.nxt
         || past || short_bad;
    end
  end

  always_comb begin
    dec        = '0;
    dec.on     = 1'b1;
    dec.match  = match;
    dec.status = common_pkg::RC_ST_OK;
    if (!match) begin
      dec.err[pcie_rc_tl_pkg::ERR_UNEXP] = 1'b1;
    end else if (cpl_i.status == pcie_rc_tl_pkg::CS_CRS) begin
      dec.fin = 1'b1;
      if (is_cfg) begin
        dec.status = common_pkg::RC_ST_CRS;
        dec.err[pcie_rc_tl_pkg::ERR_CRS] = 1'b1;
      end else begin
        dec.status = common_pkg::RC_ST_BAD;
        dec.err[pcie_rc_tl_pkg::ERR_MAL] = 1'b1;
      end
    end else if (cpl_i.status == pcie_rc_tl_pkg::CS_UR) begin
      dec.fin    = 1'b1;
      dec.status = common_pkg::RC_ST_UR;
      dec.err[pcie_rc_tl_pkg::ERR_UR] = 1'b1;
    end else if (cpl_i.status == pcie_rc_tl_pkg::CS_CA) begin
      dec.fin    = 1'b1;
      dec.status = common_pkg::RC_ST_CA;
      dec.err[pcie_rc_tl_pkg::ERR_CA] = 1'b1;
    end else if (cpl_i.status != pcie_rc_tl_pkg::CS_SC || mal) begin
      dec.fin    = 1'b1;
      dec.status = common_pkg::RC_ST_BAD;
      dec.err[pcie_rc_tl_pkg::ERR_MAL] = 1'b1;
    end else if (cpl_i.ep) begin
      dec.fin    = 1'b1;
      dec.status = common_pkg::RC_ST_BAD;
      dec.err[pcie_rc_tl_pkg::ERR_POIS] = 1'b1;
    end else if (is_wr) begin
      // A configuration write's Successful completion: one OK beat at its eop.
      dec.fin = 1'b1;
    end else begin
      dec.dlv   = 1'b1;
      dec.cnt   = cpl_i.dw_n[5:0];
      dec.lastc = is_cfg || lastc;
    end
  end

  // ------------------------------------------------------ the request's end
  pcie_rc_tl_pkg::t_cpl_st now;     // the completion's decision at its eop
  logic down;
  logic beat_dat;                   // a data DW goes out
  logic dat_last;
  logic hold;                       // a matched completion is being received
  logic reach;
  logic expire;
  logic wr_done;

  assign now      = cpl_i.hdr ? dec : cp_q;
  assign down     = rq_q.act && !dl_up_i;
  assign beat_dat = cpl_i.dat && cp_q.on && cp_q.dlv && cp_q.cnt != 6'd0;
  assign dat_last = beat_dat && cp_q.lastc && cp_q.cnt == 6'd1;
  // The counter does not expire from a matched completion's third header
  // DW to its eop, edges included: its last data beat may be due after the
  // eop, so a timeout comes at the next edge at the earliest.
  assign hold     = (cpl_i.hdr && dec.match) || (cp_q.on && cp_q.match);
  assign reach    = cnt_q == TMO_W'(TMO_N - 1);
  assign expire   = rq_q.act && rq_q.sent && reach && !hold && !down;
  assign wr_done  = rq_q.act && posted && p_eop_i;

  always_comb begin
    res_o       = '0;
    res_o.valid = 1'b1;
    if (down) begin
      res_o.rsp.status = common_pkg::RC_ST_LINK_DOWN;
      res_o.rsp.last   = 1'b1;
    end else if (beat_dat) begin
      res_o.rsp.status = common_pkg::RC_ST_OK;
      res_o.rsp.data   = cpl_i.data;
      res_o.rsp.last   = dat_last;
    end else if (cpl_i.eop && now.fin) begin
      res_o.rsp.status = now.status;
      res_o.rsp.last   = 1'b1;
    end else if (expire) begin
      res_o.rsp.status = common_pkg::RC_ST_TIMEOUT;
      res_o.rsp.last   = 1'b1;
    end else if (wr_done) begin
      res_o.rsp.status = common_pkg::RC_ST_OK;
      res_o.rsp.last   = 1'b1;
    end else begin
      res_o.valid = 1'b0;
    end
  end

  always_comb begin
    err_o = cpl_i.eop ? now.err : '0;
    err_o[pcie_rc_tl_pkg::ERR_TMO] = expire;
  end

  // ------------------------------------------------------ next state
  always_comb begin
    rq_d  = rq_q;
    cp_d  = cp_q;
    tag_d = tag_q;
    cnt_d = cnt_q;
    if (rq_q.act && rq_q.sent && !reach) begin
      cnt_d = cnt_q + TMO_W'(1);
    end
    if (grant_i) begin
      rq_d.txq = 1'b0;
    end
    if (np_sop_i) begin
      // The tag steps as the link takes a non-posted request's sop.
      rq_d.sent = 1'b1;
      tag_d     = tag_q + 5'd1;
      cnt_d     = '0;
    end
    // The completion in progress.
    if (cpl_i.hdr) begin
      cp_d = dec;
      if (dec.dlv && !is_cfg) begin
        rq_d.left = rq_q.left - span;
        rq_d.nxt  = rq_q.nxt + span[6:0];
      end
    end
    if (beat_dat) begin
      cp_d.cnt = cp_q.cnt - 6'd1;
    end
    if (cpl_i.eop || !dl_up_i) begin
      // Link down drops a completion in progress with the parser (lrst).
      cp_d.on = 1'b0;
    end
    if (res_o.valid && res_o.rsp.last) begin
      rq_d.act = 1'b0;
    end
    if (cmd_i.start) begin
      rq_d.act  = 1'b1;
      rq_d.txq  = 1'b1;
      rq_d.sent = 1'b0;
      rq_d.op   = cmd_i.op;
      rq_d.addr = cmd_i.addr;
      rq_d.dw_n = cmd_i.dw_n;
      rq_d.fbe  = cmd_i.fbe;
      rq_d.lbe  = cmd_i.lbe;
      rq_d.data = cmd_i.data;
      rq_d.rid  = {pri_bus_i, 8'd0};
      rq_d.tag  = tag_q;
      rq_d.left = 8'(common_pkg::byte_total({5'd0, cmd_i.dw_n}, cmd_i.fbe, cmd_i.lbe));
      rq_d.nxt  = pcie_rc_tl_pkg::first_byte(cmd_i.addr[6:2], cmd_i.fbe);
    end
  end

  // At its eop only a completion's end and error bits matter.
  `COMMON_UNUSED_BITS(unused_now, {now.on, now.match, now.dlv, now.lastc, now.cnt})

  `COMMON_DFF_RST(rq_q, rq_d, clk_i, rst_i)
  `COMMON_DFF_RST(cp_q, cp_d, clk_i, rst_i)
  `COMMON_DFF_RST(tag_q, tag_d, clk_i, rst_i)
  `COMMON_DFF_RST(cnt_q, cnt_d, clk_i, rst_i)

  // One request at a time: a new one only when none is outstanding; the
  // link takes only the outstanding request's TLP; a result only for it.
  `COMMON_ASSERT_NEVER(a_start_busy, clk_i, rst_i, cmd_i.start && rq_q.act)
  `COMMON_ASSERT_NEVER(a_res_idle, clk_i, rst_i, res_o.valid && !rq_q.act)
  `COMMON_ASSERT_NEVER(a_sop_unsent, clk_i, rst_i, np_sop_i && !(rq_q.act && !rq_q.sent))
  // A completion delivering data carries at most the read's 32 DWs, and
  // none arrives at the third header DW.
  `COMMON_ASSERT_NEVER(a_dlv_len, clk_i, rst_i, cpl_i.hdr && dec.dlv && cpl_i.dw_n > 11'd32)
  `COMMON_ASSERT_NEVER(a_dat_at_hdr, clk_i, rst_i, cpl_i.hdr && cpl_i.dat)

endmodule

`default_nettype wire
