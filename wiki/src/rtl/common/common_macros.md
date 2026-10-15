# common_macros MAS

`common_macros` is the shared RTL vocabulary: two include headers that
define every register, register-array, memory-port, crossing, unused-bit
and assertion form RTL may use. It is an include-only shared block: its
filelist holds `+incdir+.` and no sources, so it adds no module. The
[RTL BKM](../../../bkm/rtl.md#the-rtl-vocabulary) owns the contract (the
design rules, every form with its argument order, when to use it); this
page owns how the headers implement it and what was adapted.

Provenance: adapted from nand2anything `src/common/rtl/macros.vh` and
`src/common/rtl/assertion_macros.vh` (commit `085ef4b`), adapted with the
owner's permission.

## Interfaces

| Item | Use |
| --- | --- |
| `` `include "common_macros.svh"`` | The one include a design file writes, after `` `default_nettype none``. It defines the `COMMON_DFF*`, `COMMON_MEM*`, `COMMON_UNUSED_BITS`, `COMMON_METAFLOP*` and `COMMON_PULSE_SYNC` forms and includes `common_assertions.svh`. |
| `common_assertions.svh` | The `COMMON_ASSERT*` forms. It includes `common_macros.svh` back (both are guarded), so either include order works. |
| `-F ../common/common_macros.rtl.f` | Puts `src/rtl/common/` on the include path. A user of a crossing macro also includes the cell's filelist. |
| `SYNTHESIS` | Defined only by the FPGA and ASIC flows; removes every assertion form but `COMMON_ASSERT_PARAM`. |
| `VERILATOR` | Defined by Verilator (lint and simulation); keeps the `COMMON_UNUSED_BITS` sink. |

## Behavior

- **Registers and memories:** each form expands to exactly one `always_ff`
  holding only the reset/enable selection and the nonblocking assignment.
  Clock and reset are wrapped in parentheses in the event expression, so an
  expression argument cannot change the event. The array forms loop over
  `$low(MEM)` to `$high(MEM)` with a macro-owned `int common_i`; the lane
  form loops over `$bits(LANE_ENABLE)` with `common_l`.
- **Crossings:** `COMMON_METAFLOP` and `COMMON_METAFLOP_VAL` instantiate
  [`common_cdc_sync2`](common_cdc_sync2.md) with `WIDTH` `$bits(Q)` (and
  `RESET_VALUE`); `COMMON_PULSE_SYNC` instantiates
  [`common_cdc_pulse`](common_cdc_pulse.md). The instance is named by
  `INST`, so the CDC check, waves and constraints see an ordinary cell
  instance. The reset-synchronizer macro arrives with its cell (#119).
- **Assertions:** concurrent forms use `assert property` with
  `@(posedge (CLK))` and `disable iff (RESET)`; clocked forms are an
  `always_ff` with no state whose body runs while `!(RESET)`. Every failure
  action is `$fatal(1, "assertion <NAME> failed in %m")`.
  `COMMON_ASSERT_STABLE_WHEN` declares `NAME_hold_q` (a `COMMON_DFF_RST` of
  `HOLD`) and checks `NAME_hold_q |-> $stable(SIGNAL)`. `COMMON_ASSERT_PARAM`
  is a generate `if (!(CONDITION)) begin : LABEL initial $fatal(1, MESSAGE);
  end`, outside the `SYNTHESIS` guard.
- **Unused bits:** under `VERILATOR`, `logic NAME; assign NAME = ^(BITS);`;
  empty otherwise.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Prefix `COMMON_`, headers `common_*.svh` in `src/rtl/common/` | Design-neutral, the shared-block prefix; `.svh` because the filelist and FPGA rules allow only `.sv`/`.svh` under `rtl/`. | The source's project prefix and `.vh` files; a repository-root include path (`src/common/rtl/macros.vh`). |
| An include-only block reached with `-F` | Consumers wire the include path the same way they include any block; the lint and CDC checks skip a block with no sources. | `+incdir+` of a relative path in every consumer filelist. |
| One severity (`$fatal`) and one guard (`SYNTHESIS`) | Verilator stops on `$error` too, so the source's separate error family and its `DISABLE_ASSERTIONS` switch added nothing here. | Two severities and two guards. |
| `COMMON_ASSERT_PARAM` as `initial $fatal` in a generate block | The source's form, proven in Quartus and Yosys; replaces the FIFO's elaboration `$error` so no design file writes a system task. | An elaboration-time `$error` (not used by the source flows). |
| Crossing macros wrap the cells | One vocabulary for RTL while every crossing stays an approved cell the CDC check recognizes. | Macros that expand to raw flop chains (invisible to the cell rule). |

Forms of the source dropped (named without the source's prefix), with the reason:

| Dropped | Reason |
| --- | --- |
| `COMPAT_*` (registers and assertions), `NBA` | Transition aliases for another project's call sites. |
| `DFF_ARRAY_*_RANGE` | A Quartus workaround for slices of larger arrays; no use. |
| `MEM_WRITE_ARST_GATED`, `MEM_WRITE_RST` | Asynchronous-reset memory and register-file sweeps; no use. |
| `MEM_INIT_FILE`, `MEM_INIT_PLUSARG`, `PLUSARG_INT`, `PLUSARG_CHOICE2` | Memory images and simulation knobs; no ROM or knob yet (add with the RAM family, [#126](https://github.com/amichai-bd/pcie-factory/issues/126)). |
| `ASSERT_PROPERTY*` (default action) | One fatal severity. |
| `ASSERT_ERROR*`, `DISABLE_ASSERTIONS` | One severity, one guard (`COMMON_ASSERT_STABLE_WHEN` keeps the stable-when form). |
| `ASSERT_AFTER_RESET`, `ASSERT_HISTORY_RST`, `ASSERT_INITIAL` | No use yet. |
| `ASSERT_CLOCKED_ERROR*`, `*_FATAL*` names, `*_ARST*`, `*_UNGUARDED` | One severity (`COMMON_ASSERT_CLOCKED*` is fatal), synchronous resets only, no unguarded checks in synthesis. |

## Timing

The headers add no logic of their own: each register form is one flop per
bit, each memory form one RAM port, each crossing form one cell instance.
Assertion forms add simulation-only state (`NAME_hold_q`) and no
synthesized logic.

## Files

| File | Holds |
| --- | --- |
| [`common_macros.svh`](../../../../src/rtl/common/common_macros.svh) | Register, array, memory, crossing and unused-bit forms; includes the assertions. |
| [`common_assertions.svh`](../../../../src/rtl/common/common_assertions.svh) | Assertion forms and the `SYNTHESIS` guard. |
| [`common_macros.rtl.f`](../../../../src/rtl/common/common_macros.rtl.f) | `+incdir+.` only; consumers include it with `-F`. |
