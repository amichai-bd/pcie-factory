# common_cdc_sync2 MAS

`common_cdc_sync2` is the two-flop level synchronizer: it brings a level
(or a Gray-coded vector) that a flop in another clock domain drives into
the `dst_clk_i` domain. It is the base crossing cell: the
[pulse synchronizer](common_cdc_pulse.md) and the
[async FIFO](common_cdc_fifo.md) use it for their own crossings. Outside
`src/rtl/common/`, RTL instantiates it only through the vocabulary macros
`COMMON_METAFLOP` (reset to zero), `COMMON_METAFLOP_VAL` (reset to a
value) and `COMMON_METAFLOP_ARST` (reset to zero, asynchronously), which
take `WIDTH` from the destination signal
([crossings](../../../../bkm/rtl.md#clock-domain-crossings)). Like every
shared block it has no HAS; this page owns its interface, behavior and
structure, and the [RTL BKM](../../../../bkm/rtl.md#clock-domain-crossings)
owns when a crossing must use it.

It is not a bus synchronizer: a binary-coded vector whose bits change
together can be captured as a value that was never sent; use the
[async FIFO](common_cdc_fifo.md) for data. It is not a pulse synchronizer
(a pulse shorter than two `dst_clk_i` periods can be missed) and not a reset
synchronizer.

## Interfaces

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `dst_clk_i` | in | 1 | - | Destination clock; both flops update on its rising edge. |
| `dst_rst_i` | in | 1 | `dst_clk_i` | Active-high reset of both flops: synchronous, or asynchronous assertion with `ASYNC_RESET` 1. |
| `src_data_i` | in | `WIDTH` | source (async) | Level from the source domain. Must come straight from a source-domain flop, with no logic between it and this port. |
| `dst_data_o` | out | `WIDTH` | `dst_clk_i` | Synchronized level; driven by the second flop. |

CDC declaration ([convention](../../../../bkm/rtl.md#clock-domain-crossings)):
`// cdc-cell: src=async dst=dst_clk_i`. The cell has no source clock port;
`src_*` ports belong to the source domain, `dst_*` ports to `dst_clk_i`.

## Parameters

| Name | Type | Default | Legal range | Effect |
| --- | --- | --- | --- | --- |
| `WIDTH` | `int unsigned` | 1 | 1 or more | Number of bits, each synchronized on its own. |
| `RESET_VALUE` | `logic [WIDTH-1:0]` | `'0` | any | Value both flops load in reset. |
| `ASYNC_RESET` | `bit` | 0 | 0 or 1 | 1: both flops enter reset asynchronously (`dst_rst_i` must then come from a `COMMON_RESET_SYNC` on `dst_clk_i`); 0: synchronously. |

## Behavior

- **Reset (`ASYNC_RESET` 0):** while `dst_rst_i` is high at a `dst_clk_i`
  edge, both flops load `RESET_VALUE`, so `dst_data_o` is `RESET_VALUE`
  from the first edge in reset; after release it follows `src_data_i` as
  below.
- **Reset (`ASYNC_RESET` 1):** both flops load `RESET_VALUE` as soon as
  `dst_rst_i` rises, with no `dst_clk_i` edge (the clock may be stopped),
  and hold it while `dst_rst_i` is high. `dst_rst_i` must fall
  synchronously to `dst_clk_i` (a `COMMON_RESET_SYNC` output does); a pulse
  that rises and falls with no edge between also leaves both flops at
  `RESET_VALUE`. Either way, with `src_data_i` steady across the release,
  `dst_data_o` is `RESET_VALUE` at the first `dst_clk_i` edge after the
  release and `src_data_i` at the second.
- **Transfer:** a value on `src_data_i` appears on `dst_data_o` after two
  `dst_clk_i` rising edges, or three when the first flop resolves late
  (in silicon) or the change coincides with an edge (in simulation).
- **Source rules** (the user's obligation; the cell cannot check them):
  - hold each value for at least two `dst_clk_i` periods, so that no value
    is skipped;
  - for `WIDTH` > 1, change at most one bit per update (a Gray sequence), so
    that `dst_data_o` only ever shows values that were sent;
  - drive `src_data_i` from a flop, so that it never glitches.

Under these rules `dst_data_o` shows exactly the sequence of source values,
each two or three `dst_clk_i` edges after it was driven.

## Microarchitecture

Two registers per bit, `meta_q` (may go metastable) and `sync_q`, on
`dst_clk_i` with `RESET_VALUE`: `COMMON_DFF_RST_VAL` (generate block
`g_srst`) or, with `ASYNC_RESET` 1, `COMMON_DFF_ARST_VAL` (`g_arst`).
`dst_data_o` is `sync_q`; no logic sits between the flops.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Two flops | The usual structure; one full `dst_clk_i` period to resolve. | A `STAGES` parameter (add it when a fast clock needs more MTBF). |
| Synchronous reset by default; `ASYNC_RESET` selects an asynchronous one | Synchronous is the repository default ([RTL BKM](../../../../bkm/rtl.md#reset-and-clocks)); the reset mux before `meta_q` has a stable select, so it does not glitch. A destination domain reset by a `COMMON_RESET_SYNC` output takes it asynchronously in every flop (one style per net), so the cell follows its domain (#170). | Only one style (a domain on an asynchronous reset could not use the cell without mixing styles on one net); a separate asynchronous cell (two cells for one crossing). |
| `RESET_VALUE` parameter, default zero | `COMMON_METAFLOP_VAL` needs it for levels whose idle value is one; the pulse toggle and FIFO pointers keep zero. | A fixed zero reset (forces an inverted level at the consumer). |
| One level synchronizer: this cell, not a port of nand2anything's scalar `cdc_bit_sync` | The source's `cdc_bit_sync` overlaps it: a scalar `WIDTH` 1 level is this cell, and `RESET_VALUE` and `ASYNC_RESET` now cover its reset value and its asynchronous reset. Its other differences do not fit here: a `STAGES` parameter (no clock needs more than two flops yet) and a Quartus synchronizer attribute (vendor-specific). | Porting `cdc_bit_sync` beside this cell (two cells for one crossing). |

## Timing

- The path from `src_data_i` to `meta_q` is a crossing with no timing
  relationship. The consuming IP's FPGA and ASIC constraints own its
  exception: a max delay of at most one `dst_clk_i` period (a plain false
  path would let a multi-bit Gray vector skew past one update).
- `meta_q` to `sync_q` is a direct flop-to-flop path that gives a whole
  `dst_clk_i` period to resolve.
- Latency: two or three `dst_clk_i` edges.
- Reset: with `ASYNC_RESET` 1, `dst_rst_i` to both flops is an
  asynchronous reset path; its release is timed by the reset recovery and
  removal checks of the `COMMON_RESET_SYNC` that drives it.

## Files

| File | Holds |
| --- | --- |
| [`common_cdc_sync2.sv`](../../../../../src/rtl/common/cdc/common_cdc_sync2.sv) | The module. |
| [`common_cdc_sync2.rtl.f`](../../../../../src/rtl/common/cdc/common_cdc_sync2.rtl.f) | `-F ../common_macros.rtl.f` (the vocabulary), then the module; consumers include it with `-F`. |
