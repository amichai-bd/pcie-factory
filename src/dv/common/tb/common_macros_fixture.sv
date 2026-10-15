`default_nettype none

`include "common_macros.svh"

// Test top for the shared vocabulary (wiki/src/dv/common/common_macros.md):
// one call of every register, register-array, memory-port, unused-bit,
// crossing and assertion form, on documented ports. Legal stimulus keeps
// every assertion quiet; forbidden_i high trips them.
//
// Provenance: adapted from nand2anything
// src/common/dv/clock_reset/n2a_macros_fixture.sv (commit 085ef4b), adapted
// with the owner's permission; extended to the array, memory, crossing and
// clocked-assertion forms.
module common_macros_fixture (
  input  logic        clk_i,
  input  logic        rst_i,        // synchronous, active high
  input  logic        arst_i,       // asynchronous, active high
  input  logic        en_i,
  input  logic [7:0]  data_i,
  input  logic        we_i,
  input  logic [2:0]  waddr_i,
  input  logic [1:0]  lanes_i,
  input  logic        re_i,
  input  logic [2:0]  raddr_i,
  input  logic        src_clk_i,    // source clock of the pulse crossing
  input  logic        src_rst_i,
  input  logic        pulse_i,
  input  logic        forbidden_i,  // high trips the assertions
  output logic [7:0]  dff_o,
  output logic [7:0]  dff_en_o,
  output logic [7:0]  rst_o,
  output logic [7:0]  rst_val_o,
  output logic [7:0]  rst_en_o,
  output logic [7:0]  arst_val_o,
  output logic [7:0]  arst_en_o,
  output logic [31:0] arr_en_o,      // element k in [8k +: 8]
  output logic [31:0] arr_rst_en_o,
  output logic [7:0]  rd_o,
  output logic [7:0]  rd_en_o,
  output logic [15:0] wrd_o,
  output logic [7:0]  meta_o,
  output logic [7:0]  meta_val_o,
  output logic [7:0]  meta_arst_o,   // COMMON_METAFLOP_ARST on arst_i
  output logic        pulse_o,
  output logic        pulse_arst_o,  // COMMON_PULSE_SYNC_ARST: destination reset arst_i
  output logic        rst_sync_o    // arst_i through the reset synchronizer
);

  localparam int unsigned DEPTH = 8;
  localparam int unsigned ARRAY = 4;
  localparam logic [7:0] RST_VAL = 8'hA5;
  localparam logic [7:0] RST_EN_VAL = 8'h3C;
  localparam logic [7:0] ARST_VAL = 8'h5A;
  localparam logic [7:0] ARST_EN_VAL = 8'hC3;
  localparam logic [7:0] META_VAL = 8'h96;
  localparam int unsigned LANE_W = 8;

  `COMMON_ASSERT_PARAM(g_depth, DEPTH == 8, "common_macros_fixture: DEPTH must be 8")

  // Registers.
  `COMMON_DFF(dff_o, data_i, clk_i)
  `COMMON_DFF_EN(dff_en_o, data_i, clk_i, en_i)
  `COMMON_DFF_RST(rst_o, data_i, clk_i, rst_i)
  `COMMON_DFF_RST_VAL(rst_val_o, data_i, clk_i, rst_i, RST_VAL)
  `COMMON_DFF_RST_EN(rst_en_o, data_i, clk_i, en_i, rst_i, RST_EN_VAL)
  `COMMON_DFF_ARST_VAL(arst_val_o, data_i, clk_i, arst_i, ARST_VAL)
  `COMMON_DFF_ARST_EN(arst_en_o, data_i, clk_i, en_i, arst_i, ARST_EN_VAL)

  // Register arrays: element k loads data_i + k.
  logic [7:0] arr_d        [ARRAY];
  logic [7:0] arr_en_q     [ARRAY];
  logic [7:0] arr_rst_en_q [ARRAY];

  always_comb begin
    for (int unsigned k = 0; k < ARRAY; k++) begin
      arr_d[k] = data_i + 8'(k);
      arr_en_o[8*k +: 8] = arr_en_q[k];
      arr_rst_en_o[8*k +: 8] = arr_rst_en_q[k];
    end
  end

  `COMMON_DFF_ARRAY_EN(arr_en_q, arr_d, clk_i, en_i)
  `COMMON_DFF_ARRAY_RST_EN(arr_rst_en_q, arr_d, clk_i, en_i, rst_i)

  // Memories: one write and one or two read ports each.
  logic [7:0]  mem_q  [DEPTH];
  logic [15:0] wmem_q [DEPTH];
  logic [15:0] wdata;

  assign wdata = {data_i, ~data_i};

  `COMMON_MEM_WRITE(mem_q, clk_i, we_i, waddr_i, data_i)
  `COMMON_MEM_READ(rd_o, mem_q, clk_i, raddr_i)
  `COMMON_MEM_READ_EN(rd_en_o, mem_q, clk_i, re_i, raddr_i)
  `COMMON_MEM_WRITE_LANES(wmem_q, clk_i, we_i, waddr_i, wdata, lanes_i, LANE_W)
  `COMMON_MEM_READ(wrd_o, wmem_q, clk_i, raddr_i)

  // Crossings (data_i stands for a source-domain flop).
  `COMMON_METAFLOP(u_meta, meta_o, data_i, clk_i, rst_i)
  `COMMON_METAFLOP_VAL(u_meta_val, meta_val_o, data_i, clk_i, rst_i, META_VAL)
  `COMMON_METAFLOP_ARST(u_meta_arst, meta_arst_o, data_i, clk_i, arst_i)
  `COMMON_PULSE_SYNC(u_pulse, pulse_o, pulse_i, src_clk_i, src_rst_i, clk_i, rst_i)
  `COMMON_PULSE_SYNC_ARST(u_pulse_arst, pulse_arst_o, pulse_i, src_clk_i, src_rst_i, clk_i, arst_i)
  `COMMON_RESET_SYNC(u_rst_sync, rst_sync_o, arst_i, clk_i)

  `COMMON_UNUSED_BITS(unused_upper, data_i[7:4])

  // Assertions. hold is high while en_i is low: rst_en_o must not change.
  logic hold;

  assign hold = !en_i;

  `COMMON_ASSERT(a_never_forbidden, clk_i, rst_i, !forbidden_i)
  `COMMON_ASSERT_NO_RESET(a_always_true, clk_i, 1'b1)
  `COMMON_ASSERT_NEVER(a_never, clk_i, rst_i, forbidden_i)
  `COMMON_ASSERT_KNOWN(a_known, clk_i, rst_i, data_i)
  `COMMON_ASSERT_STABLE_WHEN(a_stable, clk_i, rst_i, hold, rst_en_o)
  `COMMON_ASSERT_CLOCKED(a_clocked, clk_i, rst_i, !forbidden_i)
  `COMMON_ASSERT_CLOCKED_WHEN(a_clocked_when, clk_i, rst_i, en_i, !forbidden_i)

endmodule

`default_nettype wire
