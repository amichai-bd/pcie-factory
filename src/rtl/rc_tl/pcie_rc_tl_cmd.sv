// pcie_rc_tl front end (u_cmd): the command take, checks 2 to 7, the
// HDR_* commands and the result beats (wiki/src/rtl/rc_tl/MAS.md#commands).
// Interim, until the requester (#373) and host memory (#375) land: a
// command that passes the checks and has no block to serve it finishes at
// once, sending nothing (MAS, Leaf blocks).
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
  output logic                    rsp_valid_o,
  output common_pkg::t_rc_rsp     rsp_o
);

  // The host-memory offset bits that must be 0 (check 4).
  localparam logic [31:0] HOST_MASK = ~((32'd1 << HOST_MEM_ADDR_W) - 32'd1);

  pcie_rc_tl_pkg::t_fe_st fe_q, fe_d;

  logic [3:0] op;
  logic [5:0] n;          // cmd_i.len, read outside the helpers
  logic       take;
  logic       pass;       // checks 2 to 7 pass
  common_pkg::t_rc_status status;
  logic [31:0] data;

  assign op          = cmd_i.op;
  assign n           = cmd_i.len;
  assign cmd_ready_o = !rst_i && !fe_q.busy;
  assign take        = cmd_valid_i && cmd_ready_o;

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

  // The command's one result beat (every command has one in this build).
  always_comb begin
    status = common_pkg::RC_ST_LOCAL_UR;
    data   = 32'd0;
    if (pass) begin
      if (pcie_rc_tl_pkg::is_ep(op)) begin
        // Interim: no requester yet (#373).
        status = dl_up_i ? common_pkg::RC_ST_LOCAL_UR : common_pkg::RC_ST_LINK_DOWN;
      end else if (op == common_pkg::RC_OP_HDR_RD) begin
        status = common_pkg::RC_ST_OK;
        data   = hdr_rd_data_i;
      end else if (op == common_pkg::RC_OP_HDR_WR) begin
        status = common_pkg::RC_ST_OK;
      end
      // Interim: HOST_* (#375) and ERR_* (#373) stay LOCAL_UR.
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
      fe_d.busy        = 1'b1;
      fe_d.beat        = 1'b1;
      fe_d.rsp.status  = status;
      fe_d.rsp.data    = data;
      fe_d.rsp.last    = 1'b1;
    end
  end

  `COMMON_DFF_RST(fe_q, fe_d, clk_i, rst_i)

  // rst_i holds every tl-seq output at 0 at once (TL_SEQ.md, Signals).
  assign rsp_valid_o = !rst_i && fe_q.beat;
  assign rsp_o       = rst_i ? '0 : fe_q.rsp;

  // Bus Master Enable gates inbound requests (#375); the Primary Bus
  // Number is the Requester ID's bus (#373).
  `COMMON_UNUSED_BITS(unused_hdr_st, {hdr_st_i.bme, hdr_st_i.pri_bus})

  // A beat only while a command is in progress; nothing between beats.
  `COMMON_ASSERT(a_beat_in_cmd, clk_i, rst_i, !fe_q.beat || fe_q.busy)
  `COMMON_ASSERT(a_rsp_quiet, clk_i, rst_i, fe_q.beat || (fe_q.rsp == '0))

endmodule

`default_nettype wire
