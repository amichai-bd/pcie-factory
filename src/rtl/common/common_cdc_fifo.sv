`default_nettype none

// Asynchronous FIFO with Gray-coded pointers: 2**ADDR_W entries of WIDTH
// bits from the src_clk_i domain to the dst_clk_i domain, valid/ready on
// both sides, first-word fall-through on the read side. Each side sees the
// other's pointer through common_cdc_sync2, so src_ready_o and dst_valid_o
// are conservative: they recover two or three edges of their own clock
// after the other side moves. Spec: wiki/src/rtl/common/common_cdc_fifo.md.
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
  output logic [WIDTH-1:0] dst_data_o
);

  localparam int unsigned DEPTH = 2 ** ADDR_W;
  localparam int unsigned PTR_W = ADDR_W + 1;
  // Write Gray pointer XOR synchronized read Gray pointer when full: the
  // two top bits differ, the rest are equal.
  localparam logic [PTR_W-1:0] FULL_XOR = PTR_W'(2'b11) << (ADDR_W - 1);

  logic [WIDTH-1:0] mem_q [DEPTH];

  // Source (write) side.
  logic             push;
  logic [PTR_W-1:0] wbin_d;
  logic [PTR_W-1:0] wbin_q;
  logic [PTR_W-1:0] wgray_d;
  logic [PTR_W-1:0] wgray_q;
  logic [PTR_W-1:0] src_rgray;

  // Destination (read) side.
  logic             pop;
  logic [PTR_W-1:0] rbin_d;
  logic [PTR_W-1:0] rbin_q;
  logic [PTR_W-1:0] rgray_d;
  logic [PTR_W-1:0] rgray_q;
  logic [PTR_W-1:0] dst_wgray;

  assign src_ready_o = (wgray_q ^ src_rgray) != FULL_XOR;
  assign push        = src_valid_i & src_ready_o;
  assign wbin_d      = wbin_q + PTR_W'(push);
  assign wgray_d     = wbin_d ^ (wbin_d >> 1);

  always_ff @(posedge src_clk_i) begin
    if (src_rst_i) begin
      wbin_q  <= '0;
      wgray_q <= '0;
    end else begin
      wbin_q  <= wbin_d;
      wgray_q <= wgray_d;
    end
  end

  always_ff @(posedge src_clk_i) begin
    if (push) begin
      mem_q[wbin_q[ADDR_W-1:0]] <= src_data_i;
    end
  end

  assign dst_valid_o = rgray_q != dst_wgray;
  assign pop         = dst_valid_o & dst_ready_i;
  assign rbin_d      = rbin_q + PTR_W'(pop);
  assign rgray_d     = rbin_d ^ (rbin_d >> 1);
  assign dst_data_o  = mem_q[rbin_q[ADDR_W-1:0]];

  always_ff @(posedge dst_clk_i) begin
    if (dst_rst_i) begin
      rbin_q  <= '0;
      rgray_q <= '0;
    end else begin
      rbin_q  <= rbin_d;
      rgray_q <= rgray_d;
    end
  end

  // Pointer crossings: one Gray bit changes per update, as the synchronizer
  // requires.
  common_cdc_sync2 #(
    .WIDTH(PTR_W)
  ) u_sync_wptr (
    .dst_clk_i (dst_clk_i),
    .dst_rst_i (dst_rst_i),
    .src_data_i(wgray_q),
    .dst_data_o(dst_wgray)
  );

  common_cdc_sync2 #(
    .WIDTH(PTR_W)
  ) u_sync_rptr (
    .dst_clk_i (src_clk_i),
    .dst_rst_i (src_rst_i),
    .src_data_i(rgray_q),
    .dst_data_o(src_rgray)
  );

endmodule

`default_nettype wire
