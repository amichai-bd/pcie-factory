`default_nettype none

`include "common_macros.svh"

// True dual-port synchronous RAM: 2**ADDR_W words of WIDTH bits on one
// clock, two read/write ports a and b, each with LANES write lanes. Every
// edge each port reads the word at its address (one-edge latency) and, with
// its write enable, writes the selected lanes. Every same-edge collision is
// read-first: each port shows the word before the edge, whichever port
// writes it. Both ports writing one lane of one word at one edge is outside
// the contract (assertion a_no_write_collision). No reset and no
// initialization. Spec: wiki/src/rtl/common/ram/common_tdp_ram.md.
//
// Provenance: adapted from nand2anything src/common/rtl/n2a_tdp_ram_portable.sv
// (commit 085ef4b), adapted with the owner's permission. Renamed to the
// common_ vocabulary; depth given as ADDR_W, lane enables and the write
// collision assertion added, vendor backend selection dropped.
module common_tdp_ram #(
  parameter int unsigned WIDTH  = 8,
  parameter int unsigned ADDR_W = 4,
  parameter int unsigned LANES  = 1
) (
  input  logic              clk_i,
  input  logic              a_wr_en_i,
  input  logic [LANES-1:0]  a_wr_lanes_i,
  input  logic [ADDR_W-1:0] a_addr_i,
  input  logic [WIDTH-1:0]  a_wr_data_i,
  output logic [WIDTH-1:0]  a_rd_data_o,
  input  logic              b_wr_en_i,
  input  logic [LANES-1:0]  b_wr_lanes_i,
  input  logic [ADDR_W-1:0] b_addr_i,
  input  logic [WIDTH-1:0]  b_wr_data_i,
  output logic [WIDTH-1:0]  b_rd_data_o
);

  localparam int unsigned DEPTH      = 2 ** ADDR_W;
  localparam int unsigned LANES_SAFE = (LANES == 0) ? 1 : LANES;
  localparam int unsigned LANE_W     = WIDTH / LANES_SAFE;

  `COMMON_ASSERT_PARAM(g_width_check, WIDTH >= 1, "common_tdp_ram: WIDTH must be at least 1")
  `COMMON_ASSERT_PARAM(g_addr_w_check, ADDR_W >= 1, "common_tdp_ram: ADDR_W must be at least 1")
  `COMMON_ASSERT_PARAM(g_lanes_check, LANES >= 1 && WIDTH % LANES_SAFE == 0,
                       "common_tdp_ram: LANES must be at least 1 and divide WIDTH")

  logic [WIDTH-1:0] mem_q [DEPTH];

  // Two read and two write processes on one array: the inferred true
  // dual-port template. Nonblocking order makes every collision read-first;
  // the process order is no promise of a winner for a double write.
  `COMMON_MEM_WRITE_LANES(mem_q, clk_i, a_wr_en_i, a_addr_i, a_wr_data_i, a_wr_lanes_i, LANE_W)
  `COMMON_MEM_READ(a_rd_data_o, mem_q, clk_i, a_addr_i)
  `COMMON_MEM_WRITE_LANES(mem_q, clk_i, b_wr_en_i, b_addr_i, b_wr_data_i, b_wr_lanes_i, LANE_W)
  `COMMON_MEM_READ(b_rd_data_o, mem_q, clk_i, b_addr_i)

  // The RAM has no reset, so the check runs from the first edge.
  `COMMON_ASSERT_NO_RESET(a_no_write_collision, clk_i,
      !(a_wr_en_i && b_wr_en_i && a_addr_i == b_addr_i && |(a_wr_lanes_i & b_wr_lanes_i)))

endmodule

`default_nettype wire
