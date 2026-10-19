// pcie_rc_tl host memory (u_mem) (wiki/src/rtl/rc_tl/INBOUND.md#host-memory):
// one common_sync_ram, 32 bits wide with four byte lanes, one access per
// cycle. A received write DW always wins the port; the front end's HOST_*
// and the completer's reads take turns when both wait, the one not granted
// most recently first (the front end first after rst). A read's data is in
// the cycle after its edge. Host memory is never reset.
`default_nettype none

`include "common_macros.svh"

module pcie_rc_tl_mem #(
  parameter int unsigned HOST_MEM_ADDR_W = 12   // 8..16, checked by the top
) (
  input  logic                      clk_i,
  input  logic                      rst_i,         // rst only
  input  pcie_rc_tl_pkg::t_mem_wr   wr_i,          // a received write DW, from u_rx's flop
  input  pcie_rc_tl_pkg::t_host_acc host_i,        // a HOST_* waiting, from u_cmd
  output logic                      host_gnt_o,    // granted at this edge
  output logic                      host_rd_vld_o, // a HOST_RD's data is rd_data_o
  input  logic                      cpl_req_i,     // the completer waits to read
  input  logic [pcie_rc_tl_pkg::HADW_W-1:0] cpl_adw_i,
  output logic                      cpl_gnt_o,
  output logic                      cpl_rd_vld_o,  // a completer read's data is rd_data_o
  output logic [31:0]               rd_data_o
);

  localparam int unsigned AW = HOST_MEM_ADDR_W - 2;

  pcie_rc_tl_pkg::t_mem_st st_q, st_d;

  logic          both;
  logic          we;
  logic [3:0]    lanes;
  logic [AW-1:0] addr;
  logic [31:0]   wdata;

  // The turn: a received write first, then whoever waits, the one not
  // granted most recently when both wait.
  assign both       = host_i.valid && cpl_req_i;
  assign host_gnt_o = !wr_i.valid && host_i.valid && !(both && st_q.host_last);
  assign cpl_gnt_o  = !wr_i.valid && cpl_req_i && !(both && !st_q.host_last);

  always_comb begin
    we    = wr_i.valid || (host_gnt_o && host_i.wr);
    lanes = wr_i.valid ? wr_i.be : host_i.be;
    wdata = wr_i.valid ? wr_i.data : host_i.data;
    if (wr_i.valid) begin
      addr = wr_i.adw[AW-1:0];
    end else if (host_gnt_o) begin
      addr = host_i.adw[AW-1:0];
    end else begin
      addr = cpl_adw_i[AW-1:0];
    end
  end

  common_sync_ram #(
    .WIDTH  (32),
    .ADDR_W (AW),
    .LANES  (4)
  ) u_ram (
    .clk_i      (clk_i),
    .wr_en_i    (we),
    .wr_lanes_i (lanes),
    .addr_i     (addr),
    .wr_data_i  (wdata),
    .rd_data_o  (rd_data_o)
  );

  always_comb begin
    st_d = st_q;
    if (host_gnt_o) begin
      st_d.host_last = 1'b1;
    end else if (cpl_gnt_o) begin
      st_d.host_last = 1'b0;
    end
    st_d.rd_host = host_gnt_o && !host_i.wr;
    st_d.rd_cpl  = cpl_gnt_o;
  end

  `COMMON_DFF_RST(st_q, st_d, clk_i, rst_i)

  assign host_rd_vld_o = st_q.rd_host;
  assign cpl_rd_vld_o  = st_q.rd_cpl;

  // Offset bits above host memory: base bits of an address inside it.
  `COMMON_UNUSED_BITS(unused_adw, {wr_i.adw, host_i.adw, cpl_adw_i})

  // Forward progress (INBOUND.md, Host memory): one TLP writes at most 32
  // DWs in a row and every TLP brings at least 3 header beats that write
  // nothing, so a waiting access, losing at most one turn, is granted
  // within 34 cycles: it never waits 34.
  logic [5:0] host_wait_q, host_wait_d;
  logic [5:0] cpl_wait_q, cpl_wait_d;

  assign host_wait_d = (host_i.valid && !host_gnt_o) ? host_wait_q + 6'd1 : 6'd0;
  assign cpl_wait_d  = (cpl_req_i && !cpl_gnt_o) ? cpl_wait_q + 6'd1 : 6'd0;

  `COMMON_DFF_RST(host_wait_q, host_wait_d, clk_i, rst_i)
  `COMMON_DFF_RST(cpl_wait_q, cpl_wait_d, clk_i, rst_i)

  `COMMON_ASSERT_NEVER(a_host_progress, clk_i, rst_i, host_i.valid && !host_gnt_o && host_wait_q >= 6'd33)
  `COMMON_ASSERT_NEVER(a_cpl_progress, clk_i, rst_i, cpl_req_i && !cpl_gnt_o && cpl_wait_q >= 6'd33)

endmodule

`default_nettype wire
