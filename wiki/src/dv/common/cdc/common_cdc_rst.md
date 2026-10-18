# common_cdc_rst DV plan

## Strategy

- **Shared unit:** tested with the other `common_` cells as the shared
  unit (`--ip common`); see the [sync2 plan](common_cdc_sync2.md#strategy).
- **Tests:** [`tests/test_cdc_rst.py`](../../../../../src/dv/common/tests/test_cdc_rst.py)
  drives `src_rst_i` and records every change of `dst_rst_o` and every
  rising `dst_clk_i` edge ([`tb/cdc_env.py`](../../../../../src/dv/common/tb/cdc_env.py)).
  Source changes fall on odd picoseconds and destination edges on even
  ones, so a change never coincides with an edge (a race in zero-delay
  simulation; see the [MAS](../../../rtl/common/cdc/common_cdc_rst.md#behavior)).
- **Oracle, from the MAS:** the reference model
  [`models/common_cdc_rst_model.py`](../../../../../src/dv/common/models/common_cdc_rst_model.py)
  turns the driven changes and the recorded edges into the exact list of
  `dst_rst_o` changes, and the test compares the two lists. Under the model:
  - `dst_rst_o` rises at the time `src_rst_i` rises;
  - it falls exactly at the `STAGES`-th rising edge after the last fall,
    unless `src_rst_i` rises first;
  - it changes at no other time.

  On top of that, every assertion is checked in the read-only phase of the
  time step that drives it, with no `dst_clk_i` edge in that step. Zero-delay
  simulation has no late resolution, so `STAGES` + 1 is not accepted. The
  model has its own pytest unit tests (`models:common` in
  `./build ci unit`).
- **Directed L0:**
  - `smoke` asserts and deasserts with `dst_clk_i` stopped: `dst_rst_o`
    must rise and then hold with no edge. It then starts the clock and
    checks the release.
  - `smoke` then runs the HAS reset contract: a 125 MHz destination clock
    that toggles through reset, a source clock 500 ppm slower, resets of 8
    to 12 source cycles, and each release within 3 destination cycles.
  - `glitch` drives 60 pulses of 2 ps up to one destination period, at
    random offsets and gaps, some inside the previous release.
- **Clock ratios:** random tests draw a source and a destination period
  with [`models/common_cdc_clocks.py`](../../../../../src/dv/common/models/common_cdc_clocks.py),
  with ratios up to 8:1 (L1) or 16:1 (L2) as in the sync2 plan. The source
  launches pulses on its own clock grid, at a random offset from the
  destination clock's random phase.
  - Widths: 30% are one source cycle; the rest are uniform from one cycle
    up to past the release time, `STAGES` + 1 destination periods.
  - Gaps: half are short enough to re-assert during the release; half are
    longer than the release.
- **Parameters:** `STAGES` 2 (default) in every level, 3 in L1, 4 in L2.
- **Functional coverage:** the `common_cdc_rst` covergroup is sampled once
  per pulse. It bins:
  - the clock ratio, as in the [sync2 plan](common_cdc_sync2.md#strategy);
    the glitch test has no source clock, so its pulses skip this bin;
  - the pulse width: under one destination period, or at least one;
  - the next pulse: during the release (`mid_release`) or after it
    (`released`).

  It crosses ratio with width, and width with next. `dst_fast,under_period`
  and `dst_fast_4x,under_period` are unreachable: random pulses last at
  least one source cycle, which is already longer than a destination
  period when the destination is faster. They are declared in the cross's
  `unreachable` list ([regress SPEC](../../../../tools/regress/SPEC.md#functional-coverage)),
  so they are not bins and do not count as misses, and a sample of one
  fails the test (#139). The `glitch` test covers sub-period pulses for
  any phase instead. The derived seeds of L0 to L2 hit every bin. No
  coverage floor is set yet ([DV BKM](../../../../bkm/dv.md#coverage)).
- **Mutation evidence** (local, not a regression entry): `rst_smoke`,
  `rst_glitch` and `rst_random` each fail on every one of these mutants:
  - no synchronizer flops (`dst_rst_o = src_rst_i`, a combinational
    release);
  - one stage (`dst_rst_o = sync_q[0]`);
  - synchronous assertion (no `posedge src_rst_i`);
  - logic on the output (`src_rst_i | sync_q[last]` with synchronous
    flops).

  `macros_contract` also fails on the first three. The CDC check rejects
  the first and the last structurally: `dst_rst_o` then has a
  combinational path from `src_rst_i`. These were recorded on the
  single-vector chain; with the last stage split out as `out_q` (#170),
  the one-stage, synchronous-`out_q` and `out_q`-reset-0 mutants were
  re-run and fail `rst_smoke`, `rst_glitch` and `rst_random` (PR evidence
  of #170).
- **Out of scope:** metastability and late resolution (as in the sync2
  plan), and a source change at the time of a destination edge, which is a
  race in zero-delay simulation. Whether consumers take the reset through
  the cell, and take it asynchronously, is the structural CDC check's job,
  and its unit tests cover it. `STAGES` through
  `COMMON_RESET_SYNC_STAGES` is covered by the
  [macros plan](../common_macros.md).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Assertion and hold](../../../rtl/common/cdc/common_cdc_rst.md#behavior) with no clock; [release](../../../rtl/common/cdc/common_cdc_rst.md#behavior) within the 3-cycle contract | `rst_smoke` | 0 | 1; clock stopped, then 8004:8000 ps |
| [Assertion](../../../rtl/common/cdc/common_cdc_rst.md#behavior) by pulses shorter than a destination period; [re-assertion during release](../../../rtl/common/cdc/common_cdc_rst.md#behavior) | `rst_glitch` | 0 | 1; 60 pulses, 2 ps up to one period |
| [Release](../../../rtl/common/cdc/common_cdc_rst.md#behavior) latency and re-assertion at random ratios | `rst_random` | 1 | 3; 100 pulses, ratio up to 8:1; coverage on the first 2 (`coverage_seeds`) |
| Same, [`STAGES`](../../../rtl/common/cdc/common_cdc_rst.md#parameters) 3 | `rst_random_stages3` | 1 | 2; 100 pulses, ratio up to 8:1; coverage on the first 2 (`coverage_seeds`) |
| Long random pulses | `rst_long` | 2 | 8; 1000 pulses, ratio up to 16:1; coverage on the first 4 (`coverage_seeds`) |
| Same, [`STAGES`](../../../rtl/common/cdc/common_cdc_rst.md#parameters) 4 | `rst_long_stages4` | 2 | 4; 1000 pulses, ratio up to 16:1 |

Every [interface](../../../rtl/common/cdc/common_cdc_rst.md#interfaces) port is exercised by every entry.

Coverage runs ([#327](https://github.com/amichai-bd/pcie-factory/issues/327)): `--coverage` instruments each entry's first seed only, except where the plan row names more: those entries' later seeds reach clock-ratio bins (the 4:1 ratios, a faster destination) the first does not, on the merged level 0 to 2 coverage of `005836f`, so the merged coverage is unchanged bin for bin ([regress SPEC](../../../../tools/regress/SPEC.md#coverage)).
