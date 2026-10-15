`default_nettype none

`include "common_macros.svh"

// Two-flop level synchronizer: brings src_data_i into the dst_clk_i domain.
// Each bit is synchronized on its own, so a multi-bit src_data_i must change
// at most one bit per update (a Gray-coded value) and hold each value for at
// least two dst_clk_i periods. src_data_i must come straight from a flop in
// its source domain (no logic between that flop and this cell). Latency: two
// dst_clk_i edges, three when the first flop resolves late. Reset (dst_rst_i)
// loads RESET_VALUE into both flops: synchronously by default, or, with
// ASYNC_RESET 1, at once with no dst_clk_i edge (dst_rst_i must then come
// from a COMMON_RESET_SYNC on dst_clk_i). RTL outside rtl/common/
// instantiates it through COMMON_METAFLOP, COMMON_METAFLOP_VAL or
// COMMON_METAFLOP_ARST. Spec: wiki/src/rtl/common/cdc/common_cdc_sync2.md.
// cdc-cell: src=async dst=dst_clk_i
module common_cdc_sync2 #(
  parameter int unsigned      WIDTH       = 1,
  parameter logic [WIDTH-1:0] RESET_VALUE = '0,
  parameter bit               ASYNC_RESET = 1'b0
) (
  input  logic             dst_clk_i,
  input  logic             dst_rst_i,
  input  logic [WIDTH-1:0] src_data_i,
  output logic [WIDTH-1:0] dst_data_o
);

  logic [WIDTH-1:0] meta_q;
  logic [WIDTH-1:0] sync_q;

  if (ASYNC_RESET) begin : g_arst
    `COMMON_DFF_ARST_VAL(meta_q, src_data_i, dst_clk_i, dst_rst_i, RESET_VALUE)
    `COMMON_DFF_ARST_VAL(sync_q, meta_q, dst_clk_i, dst_rst_i, RESET_VALUE)
  end else begin : g_srst
    `COMMON_DFF_RST_VAL(meta_q, src_data_i, dst_clk_i, dst_rst_i, RESET_VALUE)
    `COMMON_DFF_RST_VAL(sync_q, meta_q, dst_clk_i, dst_rst_i, RESET_VALUE)
  end

  assign dst_data_o = sync_q;

endmodule

`default_nettype wire
