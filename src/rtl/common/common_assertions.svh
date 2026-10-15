// Shared RTL vocabulary: assertion macros. RTL includes common_macros.svh,
// which includes this file. Every simulation-only form is empty when
// SYNTHESIS is defined (the FPGA and ASIC flows define it), so synthesis
// sees neither the check nor its support flops. Each failure is fatal and
// names the assertion label. Contract: the RTL BKM
// (wiki/bkm/rtl.md#assertions); implementation notes:
// wiki/src/rtl/common/common_macros.md.
//
// Provenance: adapted from nand2anything src/common/rtl/assertion_macros.vh
// (commit 085ef4b), adapted with the owner's permission. Renamed to the
// neutral COMMON_ prefix; one fatal severity and one SYNTHESIS guard; the
// error-severity family, its disable switch, the default-action,
// unguarded, asynchronous-reset, armed, history and initial forms and the
// compatibility aliases were dropped (see the MAS page for the reasons).
`ifndef COMMON_ASSERTIONS_SVH
`define COMMON_ASSERTIONS_SVH

`include "common_macros.svh"

`ifndef SYNTHESIS

// Concurrent properties, sampled at posedge CLK, disabled while RESET.
`define COMMON_ASSERT(NAME, CLK, RESET, PROPERTY) \
  NAME: assert property (@(posedge (CLK)) disable iff (RESET) (PROPERTY)) \
    else $fatal(1, "assertion %s failed in %m", `"NAME`");

// Only for a property that holds from the first edge, reset included.
`define COMMON_ASSERT_NO_RESET(NAME, CLK, PROPERTY) \
  NAME: assert property (@(posedge (CLK)) (PROPERTY)) \
    else $fatal(1, "assertion %s failed in %m", `"NAME`");

// HOLD high at one edge means SIGNAL keeps its value at the next edge. The
// hold term is sampled through a flop (NAME_hold_q, assertion state only)
// rather than $past, so it sees HOLD exactly as the held register does.
`define COMMON_ASSERT_STABLE_WHEN(NAME, CLK, RESET, HOLD, SIGNAL) \
  logic NAME``_hold_q; \
  `COMMON_DFF_RST(NAME``_hold_q, (HOLD), CLK, RESET) \
  `COMMON_ASSERT(NAME, CLK, RESET, NAME``_hold_q |-> $stable(SIGNAL))

// Immediate check in the active region at posedge CLK while !RESET, as an
// assert in the non-reset branch of a clocked process would be. The _WHEN
// form keeps the guard a real nested if (not four-state equal to
// !GUARD || CONDITION).
`define COMMON_ASSERT_CLOCKED(NAME, CLK, RESET, CONDITION) \
  always_ff @(posedge (CLK)) begin \
    if (!(RESET)) begin \
      NAME: assert (CONDITION) else $fatal(1, "assertion %s failed in %m", `"NAME`"); \
    end \
  end

`define COMMON_ASSERT_CLOCKED_WHEN(NAME, CLK, RESET, GUARD, CONDITION) \
  always_ff @(posedge (CLK)) begin \
    if (!(RESET)) begin \
      if (GUARD) begin \
        NAME: assert (CONDITION) else $fatal(1, "assertion %s failed in %m", `"NAME`"); \
      end \
    end \
  end

`else

`define COMMON_ASSERT(NAME, CLK, RESET, PROPERTY)
`define COMMON_ASSERT_NO_RESET(NAME, CLK, PROPERTY)
`define COMMON_ASSERT_STABLE_WHEN(NAME, CLK, RESET, HOLD, SIGNAL)
`define COMMON_ASSERT_CLOCKED(NAME, CLK, RESET, CONDITION)
`define COMMON_ASSERT_CLOCKED_WHEN(NAME, CLK, RESET, GUARD, CONDITION)

`endif

`define COMMON_ASSERT_NEVER(NAME, CLK, RESET, CONDITION) \
  `COMMON_ASSERT(NAME, CLK, RESET, !(CONDITION))

`define COMMON_ASSERT_KNOWN(NAME, CLK, RESET, SIGNAL) \
  `COMMON_ASSERT(NAME, CLK, RESET, !$isunknown(SIGNAL))

// Parameter check at module scope, present in every flow. A false
// CONDITION elaborates a block LABEL holding MESSAGE. In the simulation
// view (Verilator lint and simulation) it is an elaboration-time $fatal, so
// lint and the simulation build reject the parameters. Synthesis keeps the
// source's proven form, a time-zero initial $fatal, which Quartus and Yosys
// accept but do not act on: lint, not synthesis, is the gate.
`ifndef SYNTHESIS
`define COMMON_ASSERT_PARAM(LABEL, CONDITION, MESSAGE) \
  if (!(CONDITION)) begin : LABEL \
    $fatal(1, MESSAGE); \
  end
`else
`define COMMON_ASSERT_PARAM(LABEL, CONDITION, MESSAGE) \
  generate \
    if (!(CONDITION)) begin : LABEL \
      initial $fatal(1, MESSAGE); \
    end \
  endgenerate
`endif

`endif
