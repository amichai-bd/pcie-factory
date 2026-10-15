`default_nettype none

// Two-flop level synchronizer: brings src_data_i into the dst_clk_i domain.
// Each bit is synchronized on its own, so a multi-bit src_data_i must change
// at most one bit per update (a Gray-coded value) and hold each value for at
// least two dst_clk_i periods. src_data_i must come straight from a flop in
// its source domain (no logic between that flop and this cell). Latency: two
// dst_clk_i edges, three when the first flop resolves late. Spec:
// wiki/src/rtl/common/common_cdc_sync2.md.
// cdc-cell: src=async dst=dst_clk_i
module common_cdc_sync2 #(
  parameter int unsigned WIDTH = 1
) (
  input  logic             dst_clk_i,
  input  logic             dst_rst_i,
  input  logic [WIDTH-1:0] src_data_i,
  output logic [WIDTH-1:0] dst_data_o
);

  logic [WIDTH-1:0] meta_q;
  logic [WIDTH-1:0] sync_q;

  always_ff @(posedge dst_clk_i) begin
    if (dst_rst_i) begin
      meta_q <= '0;
      sync_q <= '0;
    end else begin
      meta_q <= src_data_i;
      sync_q <= meta_q;
    end
  end

  assign dst_data_o = sync_q;

endmodule

`default_nettype wire
