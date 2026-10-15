`default_nettype none

`include "common_macros.svh"

// Single-port synchronous RAM, write-through: as common_sync_ram (one clock,
// LANES write lanes, read every edge with one-edge latency), except that an
// edge that writes shows the new word: the written lanes come from
// wr_data_i, the others from the old word. No reset and no initialization.
// Spec: wiki/src/rtl/common/ram/common_sync_ram_wt.md.
//
// Provenance: adapted from nand2anything src/common/rtl/n2a_sync_ram_wt.sv
// (commit 085ef4b), adapted with the owner's permission. Renamed to the
// common_ vocabulary; depth given as ADDR_W, lane enables added (the bypass
// merges per lane), vendor backend selection dropped.
module common_sync_ram_wt #(
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
  localparam int unsigned LANES_SAFE = (LANES == 0) ? 1 : LANES;
  localparam int unsigned LANE_W     = WIDTH / LANES_SAFE;

  `COMMON_ASSERT_PARAM(g_width_check, WIDTH >= 1, "common_sync_ram_wt: WIDTH must be at least 1")
  `COMMON_ASSERT_PARAM(g_addr_w_check, ADDR_W >= 1, "common_sync_ram_wt: ADDR_W must be at least 1")
  `COMMON_ASSERT_PARAM(g_lanes_check, LANES >= 1 && WIDTH % LANES_SAFE == 0,
                       "common_sync_ram_wt: LANES must be at least 1 and divide WIDTH")

  logic [WIDTH-1:0] mem_q [DEPTH];
  logic [WIDTH-1:0] rd_old_q;     // the read port: the word before the edge
  logic [LANES-1:0] wr_hit;       // lanes this edge writes
  logic [LANES-1:0] wr_hit_q;
  logic [WIDTH-1:0] wr_data_q;
  logic [WIDTH-1:0] wr_mask;      // wr_hit_q widened to bits

  // The array keeps the read-first port pair; the written lanes and data
  // are registered beside it and replace the old lanes after a write edge.
  // A single port always writes the word it reads, so this is exactly
  // write-through.
  assign wr_hit = wr_lanes_i & {LANES{wr_en_i}};

  `COMMON_MEM_WRITE_LANES(mem_q, clk_i, wr_en_i, addr_i, wr_data_i, wr_lanes_i, LANE_W)
  `COMMON_MEM_READ(rd_old_q, mem_q, clk_i, addr_i)
  `COMMON_DFF(wr_hit_q, wr_hit, clk_i)
  `COMMON_DFF(wr_data_q, wr_data_i, clk_i)

  always_comb begin
    wr_mask = '0;
    for (int l = 0; l < int'(LANES_SAFE); l++) begin
      wr_mask[l*LANE_W +: LANE_W] = {LANE_W{wr_hit_q[l]}};
    end
  end

  assign rd_data_o = (wr_data_q & wr_mask) | (rd_old_q & ~wr_mask);

endmodule

`default_nettype wire
