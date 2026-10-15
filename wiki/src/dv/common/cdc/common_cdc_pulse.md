# common_cdc_pulse DV plan

## Strategy

- **Shared unit:** tested with the other `common_` cells as the shared
  unit (`--ip common`); see the [sync2 plan](common_cdc_sync2.md#strategy).
- **Tests:** [`tests/test_cdc_pulse.py`](../../../../../src/dv/common/tests/test_cdc_pulse.py)
  drives `src_pulse_i` at falling `src_clk_i` edges (the next rising edge
  samples it) and samples `dst_pulse_o` at every falling `dst_clk_i` edge;
  each high sample is one output pulse. Pulses are spaced by the
  [MAS spacing rule](../../../rtl/common/cdc/common_cdc_pulse.md#behavior) (two
  `dst_clk_i` periods) plus a random number of extra source cycles.
- **Oracle, from the MAS:** no pulse in reset (with `src_pulse_i` held
  high) or before the first event; one output pulse per event, in order;
  each exactly two `dst_clk_i` edges after the source edge that sampled
  it (one when a `dst_clk_i` edge coincides with it), as in the
  [sync2 plan](common_cdc_sync2.md#strategy).
- **Clock ratios:** fixed in L0 (fast-to-slow 2:5 and slow-to-fast 11:5,
  where the source pulses on consecutive cycles); random in L1 and L2 as in
  the sync2 plan; and a directed L1 entry within 1.2:1, either side
  faster, at a random phase, where the edges beat slowly past each other
  (the random draws reach that range only by chance).
- **Functional coverage:** the `common_cdc_pulse` covergroup, sampled per
  pulse, bins the clock ratio as in the [sync2 plan](common_cdc_sync2.md#strategy)
  and the spacing to the next pulse (back to back, the minimum, wider),
  crossed. Four cross bins are unreachable: back to back needs a source
  period of at least two destination periods, so not with `src_fast_4x`,
  `src_fast` or `near_1to1`; and with `dst_fast_4x` the minimum spacing is
  one source cycle, which counts as back to back. The derived seeds of L0
  to L2 hit every other bin. No coverage floor is set yet ([DV BKM](../../../../bkm/dv.md#coverage)).
- **Asynchronous reset:** `async_reset` (`DST_ASYNC_RESET` 1, with
  `SRC_ASYNC_RESET` 1 or 0) drives both clocks from clocks it can stop low
  (`GatedClock`). Both clocks stop, both resets rise, and `dst_pulse_o`
  must be low in that same time step: once while an output pulse is high
  (the reset is then held into the restart and released at a falling edge
  of each clock), once after an event has fully crossed (toggle and both
  destination flops at one; with `SRC_ASYNC_RESET` 1 the reset is a pulse
  wholly inside the stop, with 0 the source reset is held over a source
  edge, as the MAS requires of a synchronous side). After each release no
  pulse may appear without an event for 8 destination edges, and 20
  events then cross with the exact latency. The `async_reset` coverpoint
  bins the two cases.
- **Out of scope:** pulses that break the spacing rule (undefined by the
  MAS); metastability, as in the sync2 plan.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset and transfer](../../../rtl/common/cdc/common_cdc_pulse.md#behavior), fast source | `pulse_smoke` | 0 | 1; 8 pulses at the minimum spacing |
| [Spacing rule](../../../rtl/common/cdc/common_cdc_pulse.md#behavior): slow source pulsing on consecutive cycles | `pulse_slow_to_fast` | 0 | 1; 8 back-to-back pulses |
| [Transfer](../../../rtl/common/cdc/common_cdc_pulse.md#behavior) at random ratios and spacing | `pulse_random` | 1 | 3; 100 pulses, ratio up to 8:1 |
| [Transfer](../../../rtl/common/cdc/common_cdc_pulse.md#behavior) near 1:1, every launch offset | `pulse_near_1to1` | 1 | 2; 200 pulses, ratio 1:1 to 1.2:1 either way |
| [Transfer](../../../rtl/common/cdc/common_cdc_pulse.md#behavior), long, mostly minimum spacing | `pulse_long` | 2 | 8; 1000 pulses, ratio up to 16:1 |
| [Asynchronous side](../../../rtl/common/cdc/common_cdc_pulse.md#behavior), both sides: assertion with the clocks stopped, a pulse inside the stop, no spurious pulse, transfer after | `pulse_arst` | 1 | 2; `SRC_ASYNC_RESET` 1, `DST_ASYNC_RESET` 1, ratio up to 8:1 |
| [Asynchronous side](../../../rtl/common/cdc/common_cdc_pulse.md#behavior), destination only (the `COMMON_PULSE_SYNC_ARST` configuration) | `pulse_arst_dst` | 1 | 2; `DST_ASYNC_RESET` 1, ratio up to 8:1 |

Every [interface](../../../rtl/common/cdc/common_cdc_pulse.md#interfaces) port is exercised by every entry.
