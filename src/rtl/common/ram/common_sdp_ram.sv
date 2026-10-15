`default_nettype none

`include "common_macros.svh"

// Simple dual-port synchronous RAM: 2**ADDR_W words of WIDTH bits on one
// clock, one write port (LANES write lanes) and one read port with a read
// enable. An edge with rd_en_i reads the word at rd_addr_i (one-edge
// latency); with rd_en_i low, rd_data_o holds. A same-edge write and read of
// one word returns the old word (read-first). No reset and no
// initialization. Spec: wiki/src/rtl/common/ram/common_sdp_ram.md.
//
// Provenance: adapted from nand2anything src/common/rtl/n2a_sdp_ram_portable.sv
// (commit 085ef4b), adapted with the owner's permission. Renamed to the
// common_ vocabulary; its read-enabled module kept (an always-on read ties
// rd_en_i high), depth given as ADDR_W, lane enables added, vendor backend
// selection dropped.
module common_sdp_ram #(
  parameter int unsigned WIDTH  = 8,
  parameter int unsigned ADDR_W = 4,
  parameter int unsigned LANES  = 1
) (
  input  logic              clk_i,
  input  logic              wr_en_i,
  input  logic [LANES-1:0]  wr_lanes_i,
  input  logic [ADDR_W-1:0] wr_addr_i,
  input  logic [WIDTH-1:0]  wr_data_i,
  input  logic              rd_en_i,
  input  logic [ADDR_W-1:0] rd_addr_i,
  output logic [WIDTH-1:0]  rd_data_o
);

  localparam int unsigned DEPTH      = 2 ** ADDR_W;
  localparam int unsigned LANES_SAFE = (LANES == 0) ? 1 : LANES;
  localparam int unsigned LANE_W     = WIDTH / LANES_SAFE;

  `COMMON_ASSERT_PARAM(g_width_check, WIDTH >= 1, "common_sdp_ram: WIDTH must be at least 1")
  `COMMON_ASSERT_PARAM(g_addr_w_check, ADDR_W >= 1, "common_sdp_ram: ADDR_W must be at least 1")
  `COMMON_ASSERT_PARAM(g_lanes_check, LANES >= 1 && WIDTH % LANES_SAFE == 0,
                       "common_sdp_ram: LANES must be at least 1 and divide WIDTH")

  logic [WIDTH-1:0] mem_q [DEPTH];

  // One write and one read process on one array: the inferred simple
  // dual-port template. Nonblocking order makes a collision read-first.
  `COMMON_MEM_WRITE_LANES(mem_q, clk_i, wr_en_i, wr_addr_i, wr_data_i, wr_lanes_i, LANE_W)
  `COMMON_MEM_READ_EN(rd_data_o, mem_q, clk_i, rd_en_i, rd_addr_i)

endmodule

`default_nettype wire
