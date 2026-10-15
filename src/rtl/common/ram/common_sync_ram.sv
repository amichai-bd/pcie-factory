`default_nettype none

`include "common_macros.svh"

// Single-port synchronous RAM: 2**ADDR_W words of WIDTH bits on one clock,
// split into LANES equal write lanes. Every edge reads the word at addr_i
// (one-edge latency, read every edge); an edge with wr_en_i writes the lanes
// selected by wr_lanes_i. A same-edge write and read of one word returns the
// old word (read-first). No reset and no initialization: a word reads as
// unknown until written. Spec: wiki/src/rtl/common/ram/common_sync_ram.md.
//
// Provenance: adapted from nand2anything src/common/rtl/n2a_sync_ram.sv
// (commit 085ef4b), adapted with the owner's permission. Renamed to the
// common_ vocabulary; depth given as ADDR_W (always a power of two), lane
// enables added, vendor backend selection dropped.
module common_sync_ram #(
  parameter int unsigned WIDTH  = 8,
  parameter int unsigned ADDR_W = 4,
  parameter int unsigned LANES  = 1
) (
  input  logic              clk_i,
  input  logic              wr_en_i,
  input  logic [LANES-1:0]  wr_lanes_i,
  input  logic [ADDR_W-1:0] addr_i,
  input  logic [WIDTH-1:0]  wr_data_i,
  output logic [WIDTH-1:0]  rd_data_o
);

  localparam int unsigned DEPTH      = 2 ** ADDR_W;
  // Guarded so an illegal LANES reaches its parameter check, not a division
  // by zero.
  localparam int unsigned LANES_SAFE = (LANES == 0) ? 1 : LANES;
  localparam int unsigned LANE_W     = WIDTH / LANES_SAFE;

  `COMMON_ASSERT_PARAM(g_width_check, WIDTH >= 1, "common_sync_ram: WIDTH must be at least 1")
  `COMMON_ASSERT_PARAM(g_addr_w_check, ADDR_W >= 1, "common_sync_ram: ADDR_W must be at least 1")
  `COMMON_ASSERT_PARAM(g_lanes_check, LANES >= 1 && WIDTH % LANES_SAFE == 0,
                       "common_sync_ram: LANES must be at least 1 and divide WIDTH")

  logic [WIDTH-1:0] mem_q [DEPTH];

  // One write and one read process on one array: the inferred single-port
  // RAM template. Nonblocking order makes a collision read-first.
  `COMMON_MEM_WRITE_LANES(mem_q, clk_i, wr_en_i, addr_i, wr_data_i, wr_lanes_i, LANE_W)
  `COMMON_MEM_READ(rd_data_o, mem_q, clk_i, addr_i)

endmodule

`default_nettype wire
