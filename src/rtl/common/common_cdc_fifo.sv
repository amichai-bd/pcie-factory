`default_nettype none

`include "common_macros.svh"

// Asynchronous FIFO with Gray-coded pointers: 2**ADDR_W entries of WIDTH
// bits from the src_clk_i domain to the dst_clk_i domain, valid/ready on
// both sides, first-word fall-through on the read side. Each side sees the
// other's pointer through common_cdc_sync2, so src_ready_o and dst_valid_o
// are conservative: they recover two or three edges of their own clock
// after the other side moves. The read side also sees its fill (the words
// whose write has crossed) and the word after the oldest.
// Spec: wiki/src/rtl/common/common_cdc_fifo.md.
// cdc-cell: src=src_clk_i dst=dst_clk_i
module common_cdc_fifo #(
  parameter int unsigned WIDTH  = 8,
  parameter int unsigned ADDR_W = 2
) (
  input  logic             src_clk_i,
  input  logic             src_rst_i,
  input  logic             src_valid_i,
  output logic             src_ready_o,
  input  logic [WIDTH-1:0] src_data_i,
  input  logic             dst_clk_i,
  input  logic             dst_rst_i,
  output logic             dst_valid_o,
  input  logic             dst_ready_i,
  output logic [WIDTH-1:0] dst_data_o,
  output logic [ADDR_W:0]  dst_fill_o,
  output logic [WIDTH-1:0] dst_next_data_o
);

  localparam int unsigned DEPTH = 2 ** ADDR_W;
  localparam int unsigned PTR_W = ADDR_W + 1;
  // Write Gray pointer XOR synchronized read Gray pointer when full: the
  // two top bits differ, the rest are equal.
  localparam logic [PTR_W-1:0] FULL_XOR = PTR_W'(2'b11) << (ADDR_W - 1);

  // The pointer logic needs at least two entries.
  `COMMON_ASSERT_PARAM(g_addr_w_check, ADDR_W >= 1, "common_cdc_fifo: ADDR_W must be at least 1")

  logic [WIDTH-1:0] mem_q [DEPTH];

  // Source (write) side.
  logic              push;
  logic [ADDR_W-1:0] waddr;
  logic [PTR_W-1:0]  wbin_d;
  logic [PTR_W-1:0]  wbin_q;
  logic [PTR_W-1:0]  wgray_d;
  logic [PTR_W-1:0]  wgray_q;
  logic [PTR_W-1:0]  src_rgray;

  // Destination (read) side.
  logic              pop;
  logic [ADDR_W-1:0] raddr;
  logic [PTR_W-1:0]  rbin_d;
  logic [PTR_W-1:0]  rbin_q;
  logic [PTR_W-1:0]  rgray_d;
  logic [PTR_W-1:0]  rgray_q;
  logic [PTR_W-1:0]  dst_wgray;
  logic [PTR_W-1:0]  dst_wbin;
  logic [ADDR_W-1:0] raddr_next;

  assign src_ready_o = (wgray_q ^ src_rgray) != FULL_XOR;
  assign push        = src_valid_i & src_ready_o;
  assign wbin_d      = wbin_q + PTR_W'(push);
  assign wgray_d     = wbin_d ^ (wbin_d >> 1);

  assign waddr       = wbin_q[ADDR_W-1:0];

  `COMMON_DFF_RST(wbin_q, wbin_d, src_clk_i, src_rst_i)
  `COMMON_DFF_RST(wgray_q, wgray_d, src_clk_i, src_rst_i)
  `COMMON_MEM_WRITE(mem_q, src_clk_i, push, waddr, src_data_i)

  assign dst_valid_o = rgray_q != dst_wgray;
  assign pop         = dst_valid_o & dst_ready_i;
  assign rbin_d      = rbin_q + PTR_W'(pop);
  assign rgray_d     = rbin_d ^ (rbin_d >> 1);
  assign raddr       = rbin_q[ADDR_W-1:0];
  assign dst_data_o  = mem_q[raddr];

  // Read-side fill: the synchronized write pointer, back to binary, less
  // the read pointer. The word after the oldest is defined while the fill
  // is 2 or more.
  always_comb begin
    dst_wbin[PTR_W-1] = dst_wgray[PTR_W-1];
    for (int i = PTR_W - 2; i >= 0; i--) begin
      dst_wbin[i] = dst_wbin[i+1] ^ dst_wgray[i];
    end
  end
  assign dst_fill_o      = dst_wbin - rbin_q;
  assign raddr_next      = raddr + ADDR_W'(1);
  assign dst_next_data_o = mem_q[raddr_next];

  `COMMON_DFF_RST(rbin_q, rbin_d, dst_clk_i, dst_rst_i)
  `COMMON_DFF_RST(rgray_q, rgray_d, dst_clk_i, dst_rst_i)

  // Pointer crossings: one Gray bit changes per update, as the synchronizer
  // requires.
  `COMMON_METAFLOP(u_sync_wptr, dst_wgray, wgray_q, dst_clk_i, dst_rst_i)
  `COMMON_METAFLOP(u_sync_rptr, src_rgray, rgray_q, src_clk_i, src_rst_i)

endmodule

`default_nettype wire
