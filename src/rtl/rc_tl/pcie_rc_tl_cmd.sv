// pcie_rc_tl front end (u_cmd): the command take, checks 1 to 7, the
// HDR_* and ERR_* commands, the error record, the endpoint commands to the
// requester, and the result beats (wiki/src/rtl/rc_tl/MAS.md#commands).
// Interim, until host memory lands (#375): a HOST_* command that passes
// the checks finishes LOCAL_UR at once (MAS, Leaf blocks).
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_cmd #(
  parameter int unsigned HOST_MEM_ADDR_W = 12   // 8..16, checked by the top
) (
  input  logic                    clk_i,
  input  logic                    rst_i,          // synchronous, active high: rst only
  input  logic                    cmd_valid_i,
  output logic                    cmd_ready_o,
  input  common_pkg::t_rc_cmd     cmd_i,
  input  logic                    dl_up_i,
  input  pcie_rc_tl_pkg::t_hdr_st hdr_st_i,       // the header state, from its flops
  input  logic [31:0]             hdr_rd_data_i,  // the header DW at hdr_acc_o.idx
  output pcie_rc_tl_pkg::t_hdr_acc hdr_acc_o,
  // The requester: an endpoint command to start, its result beats and the
  // error record events at this edge
  output pcie_rc_tl_pkg::t_req_cmd req_o,
  input  pcie_rc_tl_pkg::t_res     res_i,
  input  logic [pcie_rc_tl_pkg::ERR_W-1:0] err_ev_i,
  output logic                    rsp_valid_o,
  output common_pkg::t_rc_rsp     rsp_o
);

  // The host-memory offset bits that must be 0 (check 4).
  localparam logic [31:0] HOST_MASK = ~((32'd1 << HOST_MEM_ADDR_W) - 32'd1);

  pcie_rc_tl_pkg::t_fe_st fe_q, fe_d;

  logic [3:0] op;
  logic [5:0] n;          // cmd_i.len, read outside the helpers
  logic       take;
  logic       ep;         // an endpoint command
  logic       down;       // check 1 fails
  logic       pass;       // checks 2 to 7 pass
  common_pkg::t_rc_status status;
  logic [31:0] data;

  assign op          = cmd_i.op;
  assign n           = cmd_i.len;
  assign cmd_ready_o = !rst_i && !fe_q.busy;
  assign take        = cmd_valid_i && cmd_ready_o;
  assign ep          = pcie_rc_tl_pkg::is_ep(op);
  assign down        = ep && !dl_up_i;

  assign pass = pcie_rc_tl_pkg::op_ok(op)
             && pcie_rc_tl_pkg::len_ok(op, n)
             && pcie_rc_tl_pkg::addr_ok(op, cmd_i.addr, HOST_MASK)
             && pcie_rc_tl_pkg::rd_4k_ok(op, cmd_i.addr[11:2], n)
             && pcie_rc_tl_pkg::be_ok(op, n, cmd_i.addr[2], cmd_i.first_be, cmd_i.last_be)
             && pcie_rc_tl_pkg::route_ok(op, cmd_i.addr[31:15], hdr_st_i.mse, hdr_st_i.sec_bus,
                                       hdr_st_i.mem_base, hdr_st_i.mem_limit);

  // The header access: the DW the command names; an HDR_WR that passes
  // writes at the take.
  always_comb begin
    hdr_acc_o.wr   = take && pass && (op == common_pkg::RC_OP_HDR_WR);
    hdr_acc_o.idx  = cmd_i.addr[11:2];
    hdr_acc_o.be   = cmd_i.first_be;
    hdr_acc_o.data = cmd_i.data;
  end

  // An endpoint command that passes checks 1 to 7 goes to the requester,
  // which returns its result beats.
  always_comb begin
    req_o       = '0;
    req_o.start = take && ep && !down && pass;
    req_o.op    = op[1:0];
    req_o.addr  = cmd_i.addr;
    req_o.dw_n  = n;
    req_o.fbe   = cmd_i.first_be;
    req_o.lbe   = cmd_i.last_be;
    req_o.data  = cmd_i.data;
  end

  // A local command's or a refusal's one result beat. ERR_RD returns the
  // record as it stands before the take's edge.
  always_comb begin
    status = common_pkg::RC_ST_LOCAL_UR;
    data   = 32'd0;
    if (down) begin
      // Check 1: LINK_DOWN outranks checks 2 to 7.
      status = common_pkg::RC_ST_LINK_DOWN;
    end else if (pass) begin
      if (op == common_pkg::RC_OP_HDR_RD) begin
        status = common_pkg::RC_ST_OK;
        data   = hdr_rd_data_i;
      end else if (op == common_pkg::RC_OP_HDR_WR) begin
        status = common_pkg::RC_ST_OK;
      end else if (op == common_pkg::RC_OP_ERR_RD) begin
        status = common_pkg::RC_ST_OK;
        data   = 32'(fe_q.err);
      end else if (op == common_pkg::RC_OP_ERR_CLR) begin
        status = common_pkg::RC_ST_OK;
      end
      // Interim: HOST_* stays LOCAL_UR until #375.
    end
  end

  always_comb begin
    fe_d = fe_q;
    fe_d.beat = 1'b0;
    fe_d.rsp  = '0;
    if (fe_q.beat && fe_q.rsp.last) begin
      fe_d.busy = 1'b0;
    end
    if (take) begin
      fe_d.busy = 1'b1;
    end
    if (take && !req_o.start) begin
      fe_d.beat        = 1'b1;
      fe_d.rsp.status  = status;
      fe_d.rsp.data    = data;
      fe_d.rsp.last    = 1'b1;
    end
    if (res_i.valid) begin
      fe_d.beat = 1'b1;
      fe_d.rsp  = res_i.rsp;
    end
    // The error record: an ERR_CLR that passes clears the bits set in
    // data; an event setting a bit at the same edge wins.
    if (take && pass && !down && op == common_pkg::RC_OP_ERR_CLR) begin
      fe_d.err = fe_q.err & ~cmd_i.data[pcie_rc_tl_pkg::ERR_W-1:0];
    end
    fe_d.err = fe_d.err | err_ev_i;
  end

  `COMMON_DFF_RST(fe_q, fe_d, clk_i, rst_i)

  // rst_i holds every tl-seq output at 0 at once (TL_SEQ.md, Signals).
  assign rsp_valid_o = !rst_i && fe_q.beat;
  assign rsp_o       = rst_i ? '0 : fe_q.rsp;

  // Bus Master Enable gates inbound requests (#375); the requester reads
  // the Primary Bus Number.
  `COMMON_UNUSED_BITS(unused_hdr_st, {hdr_st_i.bme, hdr_st_i.pri_bus})

  // A beat only while a command is in progress; nothing between beats.
  `COMMON_ASSERT(a_beat_in_cmd, clk_i, rst_i, !fe_q.beat || fe_q.busy)
  `COMMON_ASSERT(a_rsp_quiet, clk_i, rst_i, fe_q.beat || (fe_q.rsp == '0))
  // The requester answers only the command in progress, never in the
  // cycle a local beat is made.
  `COMMON_ASSERT_NEVER(a_res_unasked, clk_i, rst_i, res_i.valid && (!fe_q.busy || take))

endmodule

`default_nettype wire
