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
  crossed. Four combinations are unreachable by construction and are
  declared in the cross's `unreachable` list ([regress SPEC](../../../../tools/regress/SPEC.md#functional-coverage)),
  so they are not bins, and a sample of one fails the test
  ([#232](https://github.com/amichai-bd/pcie-factory/issues/232)). The
  minimum spacing is `ceil(2 * dst / src)` source cycles (at least one),
  and back to back means one source cycle:
  - `src_fast_4x,back_to_back`, `src_fast,back_to_back` and
    `near_1to1,back_to_back`: back to back needs the minimum to be one
    source cycle, so a source period of at least two destination periods
    (`dst / src` at most 0.5); these bins all have `dst / src` above 0.8.
  - `dst_fast_4x,minimum`: with `dst / src` at most 0.25 the minimum is
    one source cycle, which the sample counts as back to back.

  The derived seeds of L0 to L2 hit every other bin. No coverage floor is set yet ([DV BKM](../../../../bkm/dv.md#coverage)).
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
| [Transfer](../../../rtl/common/cdc/common_cdc_pulse.md#behavior) at random ratios and spacing | `pulse_random` / `pulse_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (313476785 and 195434251), pinned; 100 pulses, ratio up to 8:1 |
| [Transfer](../../../rtl/common/cdc/common_cdc_pulse.md#behavior) near 1:1, every launch offset | `pulse_near_1to1` / `pulse_near_1to1_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (1562228821), pinned; 200 pulses, ratio 1:1 to 1.2:1 either way |
| [Transfer](../../../rtl/common/cdc/common_cdc_pulse.md#behavior), long, mostly minimum spacing | `pulse_long` | 2 | 8; 1000 pulses, ratio up to 16:1; coverage on the first 6 (`coverage_seeds`) |
| [Asynchronous side](../../../rtl/common/cdc/common_cdc_pulse.md#behavior), both sides: assertion with the clocks stopped, a pulse inside the stop, no spurious pulse, transfer after | `pulse_arst` / `pulse_arst_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (1141078041), pinned; `SRC_ASYNC_RESET` 1, `DST_ASYNC_RESET` 1, ratio up to 8:1 |
| [Asynchronous side](../../../rtl/common/cdc/common_cdc_pulse.md#behavior), destination only (the `COMMON_PULSE_SYNC_ARST` configuration) | `pulse_arst_dst` / `pulse_arst_dst_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (391930980), pinned; `DST_ASYNC_RESET` 1, ratio up to 8:1 |

**Level placement**
([#389](https://github.com/amichai-bd/pcie-factory/issues/389)): the full
level 1 of every IP, which `ci-gate` runs for a change to a shared
package, was over its 1200 s CPU budget. `pulse_random`,
`pulse_near_1to1`, `pulse_arst` and `pulse_arst_dst` run their first seed
in level 1; their further seeds run pinned and unchanged in each entry's
level 2 `_sweep`, each seed with its coverage instrumentation, so the
merged level 0 to 2 runs are the same runs. The mutation evidence on this
page names no seed for these entries, and was not rerun at the first seed
alone.

Every [interface](../../../rtl/common/cdc/common_cdc_pulse.md#interfaces) port is exercised by every entry.

Coverage runs ([#327](https://github.com/amichai-bd/pcie-factory/issues/327)): `--coverage` instruments each entry's first seed only, except where the plan row names more: those entries' later seeds reach clock-ratio bins (the 4:1 ratios, a faster destination) the first does not, on the merged level 0 to 2 coverage of `005836f`, so the merged coverage is unchanged bin for bin ([regress SPEC](../../../../tools/regress/SPEC.md#coverage)).
