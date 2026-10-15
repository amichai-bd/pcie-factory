`default_nettype none

`include "common_macros.svh"

// Single-port synchronous RAM with a read enable and a held output, built on
// common_sync_ram. An edge with rd_en_i high shows the word at addr_i after
// that edge (one-edge latency); with rd_en_i low, rd_data_o keeps the last
// enabled result while the address and writes change. Writes do not depend
// on rd_en_i, and a same-edge enabled read and write of one word returns
// the old word (read-first). rd_data_o is unknown until the first enabled
// read. No reset. Spec: wiki/src/rtl/common/common_sync_ram_held.md.
//
// Provenance: adapted from nand2anything src/common/rtl/n2a_sync_ram_held.sv
// (commit 085ef4b), adapted with the owner's permission. Renamed to the
// common_ vocabulary; depth given as ADDR_W, lane enables passed through.
module common_sync_ram_held #(
  parameter int unsigned WIDTH  = 8,
  parameter int unsigned ADDR_W = 4,
  parameter int unsigned LANES  = 1
) (
  input  logic              clk_i,
  input  logic              wr_en_i,
  input  logic [LANES-1:0]  wr_lanes_i,
  input  logic [ADDR_W-1:0] addr_i,
  input  logic [WIDTH-1:0]  wr_data_i,
  input  logic              rd_en_i,
  output logic [WIDTH-1:0]  rd_data_o
);

  // The inner RAM repeats these; checking here names this block.
  localparam int unsigned LANES_SAFE = (LANES == 0) ? 1 : LANES;

  `COMMON_ASSERT_PARAM(g_width_check, WIDTH >= 1, "common_sync_ram_held: WIDTH must be at least 1")
  `COMMON_ASSERT_PARAM(g_addr_w_check, ADDR_W >= 1, "common_sync_ram_held: ADDR_W must be at least 1")
  `COMMON_ASSERT_PARAM(g_lanes_check, LANES >= 1 && WIDTH % LANES_SAFE == 0,
                       "common_sync_ram_held: LANES must be at least 1 and divide WIDTH")

  logic [WIDTH-1:0] rd_raw;
  logic [WIDTH-1:0] rd_hold_q;
  logic             rd_en_q;

  // The RAM reads every edge; the delayed read enable picks the fresh word
  // or the captured copy, so no cycle is added and the hold is a register.
  common_sync_ram #(
    .WIDTH (WIDTH),
    .ADDR_W(ADDR_W),
    .LANES (LANES)
  ) u_ram (
    .clk_i     (clk_i),
    .wr_en_i   (wr_en_i),
    .wr_lanes_i(wr_lanes_i),
    .addr_i    (addr_i),
    .wr_data_i (wr_data_i),
    .rd_data_o (rd_raw)
  );

  // rd_en_q high: rd_raw is a fresh enabled read; it is shown and captured,
  // so rd_hold_q always holds the last enabled result.
  `COMMON_DFF(rd_en_q, rd_en_i, clk_i)
  `COMMON_DFF_EN(rd_hold_q, rd_raw, clk_i, rd_en_q)

  assign rd_data_o = rd_en_q ? rd_raw : rd_hold_q;

endmodule

`default_nettype wire
