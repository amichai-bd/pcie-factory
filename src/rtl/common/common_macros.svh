// Shared RTL vocabulary: register, register-array, memory-port and
// unused-bit macros. Including this header also brings in the assertion
// macros (common_assertions.svh). RTL never writes always_ff, an assertion,
// an initial block or a SYNTHESIS/VERILATOR conditional of its own: those
// live only in these two headers. Contract and argument order: the RTL BKM
// (wiki/bkm/rtl.md#the-rtl-vocabulary); implementation notes:
// wiki/src/rtl/common/common_macros.md.
//
// Provenance: adapted from nand2anything src/common/rtl/macros.vh (commit
// 085ef4b), adapted with the owner's permission. Renamed to the neutral
// COMMON_ prefix; the compatibility, transition-only, plusarg,
// initialization and asynchronous-reset memory forms were dropped (see the
// MAS page for the list and reasons).
`ifndef COMMON_MACROS_SVH
`define COMMON_MACROS_SVH

// Lint-only observation of deliberately unused bits: under Verilator (lint
// and simulation) NAME is a sink that reads BITS; synthesis tools see
// nothing. No functional path may read NAME.
`ifdef VERILATOR
`define COMMON_UNUSED_BITS(NAME, BITS) \
  logic NAME; \
  assign NAME = ^(BITS);
`else
`define COMMON_UNUSED_BITS(NAME, BITS)
`endif

// Registers. Every form expands to exactly one always_ff that owns Q; the
// caller computes D with blocking logic (always_comb or assign). Argument
// order is normative: Q, D, CLK, then ENABLE, then RESET, then VALUE.
// RESET is active high; it wins over ENABLE.

`define COMMON_DFF(Q, D, CLK) \
  always_ff @(posedge (CLK)) begin \
    Q <= D; \
  end

`define COMMON_DFF_EN(Q, D, CLK, ENABLE) \
  always_ff @(posedge (CLK)) begin \
    if (ENABLE) begin \
      Q <= D; \
    end \
  end

`define COMMON_DFF_RST(Q, D, CLK, RESET) \
  always_ff @(posedge (CLK)) begin \
    if (RESET) begin \
      Q <= '0; \
    end else begin \
      Q <= D; \
    end \
  end

`define COMMON_DFF_RST_VAL(Q, D, CLK, RESET, VALUE) \
  always_ff @(posedge (CLK)) begin \
    if (RESET) begin \
      Q <= VALUE; \
    end else begin \
      Q <= D; \
    end \
  end

`define COMMON_DFF_RST_EN(Q, D, CLK, ENABLE, RESET, VALUE) \
  always_ff @(posedge (CLK)) begin \
    if (RESET) begin \
      Q <= VALUE; \
    end else if (ENABLE) begin \
      Q <= D; \
    end \
  end

// Asynchronous assertion, synchronous release by the caller's reset
// synchronizer; only where the owning spec requires it.
`define COMMON_DFF_ARST_VAL(Q, D, CLK, RESET, VALUE) \
  always_ff @(posedge (CLK) or posedge (RESET)) begin \
    if (RESET) begin \
      Q <= VALUE; \
    end else begin \
      Q <= D; \
    end \
  end

`define COMMON_DFF_ARST_EN(Q, D, CLK, ENABLE, RESET, VALUE) \
  always_ff @(posedge (CLK) or posedge (RESET)) begin \
    if (RESET) begin \
      Q <= VALUE; \
    end else if (ENABLE) begin \
      Q <= D; \
    end \
  end

// Register arrays (small buffers, tables): every element is copied from a
// whole next-state array. A flop array, never a RAM inference shape. The
// reset clears every element and wins over ENABLE.
`define COMMON_DFF_ARRAY_EN(MEM, NEXT_MEM, CLK, ENABLE) \
  always_ff @(posedge (CLK)) begin \
    if (ENABLE) begin \
      for (int common_i = $low(MEM); common_i <= $high(MEM); common_i++) begin \
        MEM[common_i] <= NEXT_MEM[common_i]; \
      end \
    end \
  end

`define COMMON_DFF_ARRAY_RST_EN(MEM, NEXT_MEM, CLK, ENABLE, RESET) \
  always_ff @(posedge (CLK)) begin \
    if (RESET) begin \
      for (int common_i = $low(MEM); common_i <= $high(MEM); common_i++) begin \
        MEM[common_i] <= '0; \
      end \
    end else if (ENABLE) begin \
      for (int common_i = $low(MEM); common_i <= $high(MEM); common_i++) begin \
        MEM[common_i] <= NEXT_MEM[common_i]; \
      end \
    end \
  end

// Memory ports: one process per port, one indexed element per edge, the
// conventional inferred-RAM template. Reads and writes of one array live in
// separate processes, so a same-edge, same-address read returns the old
// data. No form resets or copies the array.
`define COMMON_MEM_WRITE(MEM, CLK, WRITE_ENABLE, ADDRESS, DATA) \
  always_ff @(posedge (CLK)) begin \
    if (WRITE_ENABLE) begin \
      MEM[ADDRESS] <= DATA; \
    end \
  end

// Byte (lane) enables: lane l of the word is written when LANE_ENABLE[l].
`define COMMON_MEM_WRITE_LANES(MEM, CLK, WRITE_ENABLE, ADDRESS, DATA, LANE_ENABLE, LANE_WIDTH) \
  always_ff @(posedge (CLK)) begin \
    if (WRITE_ENABLE) begin \
      for (int common_l = 0; common_l < $bits(LANE_ENABLE); common_l++) begin \
        if (LANE_ENABLE[common_l]) begin \
          MEM[ADDRESS][common_l*(LANE_WIDTH) +: (LANE_WIDTH)] <= \
              DATA[common_l*(LANE_WIDTH) +: (LANE_WIDTH)]; \
        end \
      end \
    end \
  end

`define COMMON_MEM_READ(Q, MEM, CLK, ADDRESS) \
  always_ff @(posedge (CLK)) begin \
    Q <= MEM[ADDRESS]; \
  end

`define COMMON_MEM_READ_EN(Q, MEM, CLK, READ_ENABLE, ADDRESS) \
  always_ff @(posedge (CLK)) begin \
    if (READ_ENABLE) begin \
      Q <= MEM[ADDRESS]; \
    end \
  end

// Clock-domain crossings: each form instantiates an approved crossing cell
// (rtl/common/cdc/common_cdc_*), so the crossing stays a cell instance the CDC
// check recognizes. INST names the instance. The caller's filelist includes
// the cell's filelist with -F. Outside rtl/common/ the cells are
// instantiated only through these forms.

// Level synchronizer (two flops on DST_CLK) of any width, reset to '0 by the
// synchronous DST_RST. D comes straight from a source-domain flop; a vector
// changes at most one bit per update (Gray code).
`define COMMON_METAFLOP(INST, Q, D, DST_CLK, DST_RST) \
  common_cdc_sync2 #( \
    .WIDTH($bits(Q)) \
  ) INST ( \
    .dst_clk_i (DST_CLK), \
    .dst_rst_i (DST_RST), \
    .src_data_i(D), \
    .dst_data_o(Q) \
  );

// The same, with both flops reset to VALUE (width of Q).
`define COMMON_METAFLOP_VAL(INST, Q, D, DST_CLK, DST_RST, VALUE) \
  common_cdc_sync2 #( \
    .WIDTH($bits(Q)), \
    .RESET_VALUE(VALUE) \
  ) INST ( \
    .dst_clk_i (DST_CLK), \
    .dst_rst_i (DST_RST), \
    .src_data_i(D), \
    .dst_data_o(Q) \
  );

// Event crossing: each SRC_CLK cycle with D high gives one DST_CLK cycle
// with Q high (common_cdc_pulse spacing rules apply).
`define COMMON_PULSE_SYNC(INST, Q, D, SRC_CLK, SRC_RST, DST_CLK, DST_RST) \
  common_cdc_pulse INST ( \
    .src_clk_i  (SRC_CLK), \
    .src_rst_i  (SRC_RST), \
    .src_pulse_i(D), \
    .dst_clk_i  (DST_CLK), \
    .dst_rst_i  (DST_RST), \
    .dst_pulse_o(Q) \
  );

// Reset synchronizer: Q, the DST_CLK domain's active-high reset, rises as
// soon as D rises (no clock edge) and falls two DST_CLK edges after D
// falls. D comes straight from a flop or a reset input pin. Flops that take
// Q are COMMON_DFF_ARST_* registers.
`define COMMON_RESET_SYNC(INST, Q, D, DST_CLK) \
  common_cdc_rst INST ( \
    .dst_clk_i(DST_CLK), \
    .src_rst_i(D), \
    .dst_rst_o(Q) \
  );

`include "common_assertions.svh"

`endif
