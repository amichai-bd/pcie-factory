`default_nettype none

`include "common_macros.svh"

// common_sdp_ram, sky130 backend: the module, parameters, ports and cycle
// behavior of the portable common_sdp_ram.sv (one-edge read, hold with
// rd_en_i low, read-first collision), built from sky130 OpenRAM SRAM macros
// instead of an inferred array. Only ASIC source lists select it, through
// common_sdp_ram.sky130.f; simulation and the FPGA flow keep the portable
// file. Spec: wiki/src/rtl/common/ram/common_sdp_ram.md#sky130-backend.
//
// Macro: sky130_sram_1rw1r_64x256_8, 256 words of 64 bits, port 0 read or
// write, port 1 read, both sampling at the rising edge; port 0 is the write
// port, port 1 the read port. Tiling: ceil(WIDTH / 64) macros across and
// 2**(ADDR_W - 8) deep (one row, low words only, when ADDR_W <= 8); unused
// bits are written as zero.
//
// - Collision: the macro defines no same-word write and read in one edge.
//   A write is held for one edge in a pending register and commits at the
//   next edge; a read of the pending word disables the macro read and takes
//   the pending data instead. No physical collision occurs, and a read
//   returns the word before this edge's write (read-first).
// - Hold: the macro drives its read data after the falling edge that follows
//   the read edge, and does not hold it through a cycle without a read. The
//   read flag, the row read and a hold register (loaded from the macros at
//   the edge after a read) keep rd_data_o on the last word read.
//
// Provenance: the pattern (one module name per backend file, selected by
// the source list only, a pending write with a collision bypass, tiling and
// a DV-only macro model for lint) is adapted from nand2anything
// src/common/rtl/n2a_sdp_ram_ihp_edge.sv (commit 8bb70d2), adapted with the
// owner's permission; rewritten for the sky130 macro, the common_ ports and
// the read-enable hold.
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

  localparam int unsigned M_W   = 64;                                   // macro word width
  localparam int unsigned M_AW  = 8;                                    // macro address width
  localparam int unsigned COLS  = (WIDTH + M_W - 1) / M_W;
  localparam int unsigned ROWS  = (ADDR_W > M_AW) ? 2 ** (ADDR_W - M_AW) : 1;
  localparam int unsigned ROW_W = (ADDR_W > M_AW) ? ADDR_W - M_AW : 1;
  localparam int unsigned LO_W  = (ADDR_W > M_AW) ? M_AW : ADDR_W;

  `COMMON_ASSERT_PARAM(g_width_check, WIDTH >= 1, "common_sdp_ram: WIDTH must be at least 1")
  `COMMON_ASSERT_PARAM(g_addr_w_check, ADDR_W >= 1, "common_sdp_ram: ADDR_W must be at least 1")
  `COMMON_ASSERT_PARAM(g_lanes_check, LANES == 1,
                       "common_sdp_ram: the sky130 backend supports LANES == 1 only")

  // ------------------------------------------------------------ pending write

  logic              pend_q;        // a write waits to commit at this edge
  logic [ADDR_W-1:0] pend_addr_q;
  logic [WIDTH-1:0]  pend_data_q;

  `COMMON_DFF(pend_q, wr_en_i, clk_i)
  `COMMON_DFF(pend_addr_q, wr_addr_i, clk_i)
  `COMMON_DFF(pend_data_q, wr_data_i, clk_i)

  logic                collide;     // this edge reads the pending word
  logic                mac_rd;      // this edge reads the macros
  logic [COLS*M_W-1:0] wr_word;     // the pending data, zero-extended to whole macros
  logic [M_AW-1:0]     wr_lo;       // word address inside a macro
  logic [M_AW-1:0]     rd_lo;
  logic [ROW_W-1:0]    wr_row;      // macro row
  logic [ROW_W-1:0]    rd_row;

  assign collide = pend_q && pend_addr_q == rd_addr_i;
  assign mac_rd  = rd_en_i && !collide;
  assign wr_word = (COLS*M_W)'(pend_data_q);
  assign wr_lo   = M_AW'(pend_addr_q[LO_W-1:0]);
  assign rd_lo   = M_AW'(rd_addr_i[LO_W-1:0]);

  generate
    if (ADDR_W > M_AW) begin : g_rows
      assign wr_row = pend_addr_q[ADDR_W-1:M_AW];
      assign rd_row = rd_addr_i[ADDR_W-1:M_AW];
    end else begin : g_one_row
      assign wr_row = '0;
      assign rd_row = '0;
    end
  endgenerate

  // ------------------------------------------------------------ macros

  logic [COLS*M_W-1:0]      row_data [ROWS];   // port 1 read data of each row
  logic [ROWS*COLS*M_W-1:0] dout0_all;         // port 0 read data, never read

  genvar r, c;

  generate
    for (r = 0; r < ROWS; r++) begin : g_row
      for (c = 0; c < COLS; c++) begin : g_col
        sky130_sram_1rw1r_64x256_8 u_macro (
          .clk0   (clk_i),
          .csb0   (!(pend_q && wr_row == ROW_W'(r))),
          .web0   (1'b0),
          .wmask0 (8'hFF),
          .addr0  (wr_lo),
          .din0   (wr_word[c*M_W +: M_W]),
          .dout0  (dout0_all[(r*COLS+c)*M_W +: M_W]),
          .clk1   (clk_i),
          .csb1   (!(mac_rd && rd_row == ROW_W'(r))),
          .addr1  (rd_lo),
          .dout1  (row_data[r][c*M_W +: M_W])
        );
      end
    end
  endgenerate

  // One lane (asserted above), and port 0 only writes.
  `COMMON_UNUSED_BITS(unused_lanes, wr_lanes_i)
  `COMMON_UNUSED_BITS(unused_dout0, dout0_all)

  // ------------------------------------------------------------ read data

  logic             fresh_q;        // the last edge read the macros
  logic [ROW_W-1:0] row_q;          // the macro row addressed at the last edge
  logic             byp_q;          // the last enabled read took the pending word
  logic [WIDTH-1:0] byp_data_q;     // that word
  logic [WIDTH-1:0] mac_data;       // the macro row's read data
  logic [WIDTH-1:0] hold_q;         // the last macro word, for cycles without a read

  `COMMON_DFF(fresh_q, mac_rd, clk_i)
  `COMMON_DFF(row_q, rd_row, clk_i)
  `COMMON_DFF_EN(byp_q, collide, clk_i, rd_en_i)
  `COMMON_DFF_EN(byp_data_q, pend_data_q, clk_i, rd_en_i)

  assign mac_data = row_data[row_q][WIDTH-1:0];

  `COMMON_DFF_EN(hold_q, mac_data, clk_i, fresh_q)

  assign rd_data_o = byp_q ? byp_data_q : (fresh_q ? mac_data : hold_q);

endmodule

`default_nettype wire
