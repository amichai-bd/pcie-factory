# common_cdc_sync2 DV plan

## Strategy

- **Shared unit:** the `common_` cells are tested together as the shared
  unit (`./build sim --ip common ...`); its lists are in
  [`src/dv/common/regress/`](../../../../../src/dv/common/regress/level0.yaml)
  ([layout](../../../../project/layout.md#shared-blocks)).
- **Tests:** [`tests/test_cdc_sync2.py`](../../../../../src/dv/common/tests/test_cdc_sync2.py)
  drives the cell directly. The cell has no source clock, so the test
  models the source flop: it changes `src_data_i` on a source-clock grid,
  one random bit per update (a Gray sequence), holding each value for at
  least two (up to about five) `dst_clk_i` periods, as the [MAS](../../../rtl/common/cdc/common_cdc_sync2.md#behavior)
  requires. `dst_data_o` is sampled at every falling `dst_clk_i` edge, and
  [`tb/cdc_env.py`](../../../../../src/dv/common/tb/cdc_env.py) logs every
  rising edge to measure latency.
- **Oracle, from the MAS:** the sequence of distinct `dst_data_o` values
  equals the sent sequence (nothing skipped, nothing invented), and each
  value appears exactly two `dst_clk_i` edges strictly after it was driven (one when a
  `dst_clk_i` edge coincides with the update and captures it). Zero-delay
  simulation has no late resolution, so the MAS's third edge is not
  accepted, and a three-flop synchronizer fails.
- **Clock ratios:** random tests draw both periods from the seed with
  [`models/common_cdc_clocks.py`](../../../../../src/dv/common/models/common_cdc_clocks.py):
  a ratio log-uniform in [1, 8] (L1) or [1, 16] (L2), a fast period
  uniform in [2 ns, 64 ns / ratio], and the fast clock on a random side;
  the destination clock gets a random phase, and the log prints all three.
  Its pytest unit tests (`models:common` in `./build ci unit`) prove that
  the draws reach the claimed ratio in both directions, are log-uniform
  and do not pile up on the period limits.
- **Functional coverage:** the `common_cdc_sync2` covergroup, sampled per
  update, bins the clock ratio (`dst` period / `src` period: source 4x or
  more faster, faster, near 1:1 within 1.25, destination faster, 4x or more
  faster) and the hold (under three or at least three `dst_clk_i` periods),
  crossed. `dst_fast_4x,min` is unreachable: a source period of four or
  more destination periods holds every update at least four of them.
  `dst_fast,min` is reachable but rare, and the derived seeds miss it: it
  needs a source period in [1.25, 1.5) or [2, 3) destination periods (a
  whole number of source periods under three destination periods, but at
  least two). The derived seeds hit every other bin. The
  pulse and FIFO plans reuse the same ratio bins (`CLOCK_BINS` in
  `tb/cdc_env.py`). No coverage floor is set yet
  ([DV BKM](../../../../bkm/dv.md#coverage)).
- **Asynchronous reset (`ASYNC_RESET` 1):** `async_reset` drives
  `dst_clk_i` from a clock it can stop low (`GatedClock` in
  `tb/cdc_env.py`) and checks `dst_data_o` in the same time step
  `dst_rst_i` rises (read-only phase): at time zero, between the edges of a
  running clock, and with the clock stopped. A reset pulse wholly inside a
  stop must leave both flops at `RESET_VALUE`; a reset held into the
  restart is released at a falling edge (synchronously). After every
  release, with `src_data_i` steady, `dst_data_o` is `RESET_VALUE` at the
  first edge and the source value at the second. The entry uses `WIDTH` 4
  and `RESET_VALUE` 9, so the reset value, the old and the new source
  value all differ. The `async_reset` coverpoint bins the three reset
  cases (running, stopped pulse, stopped held).
- **Out of scope:** metastability itself. Verilator is two-state and
  zero-delay, so a late resolution never happens in simulation. Whether crossings use the cells at all is
  the structural CDC check's job ([#70](https://github.com/amichai-bd/pcie-factory/issues/70)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset](../../../rtl/common/cdc/common_cdc_sync2.md#behavior): zero output in reset with all-ones input; transfer at a fixed 3:5 ratio | `sync2_smoke` | 0 | 1; `WIDTH` 1, 16 updates |
| [Transfer](../../../rtl/common/cdc/common_cdc_sync2.md#behavior): Gray sequence and latency at random ratios | `sync2_random` / `sync2_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (1836129780 and 1954040910), pinned; `WIDTH` 4, 200 updates, ratio up to 8:1; coverage on the same seeds as before |
| [Transfer](../../../rtl/common/cdc/common_cdc_sync2.md#behavior), long and wide | `sync2_long` | 2 | 8; `WIDTH` 8, 2000 updates, ratio up to 16:1; coverage on the first 8 (`coverage_seeds`) |
| [Asynchronous reset](../../../rtl/common/cdc/common_cdc_sync2.md#behavior): assertion with no edge (time zero, running clock, stopped clock), a pulse inside a stop, synchronous release two edges before the source value | `sync2_arst` / `sync2_arst_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (1313951933), pinned; `WIDTH` 4, `RESET_VALUE` 9, `ASYNC_RESET` 1, ratio up to 8:1 |

**Level placement**
([#389](https://github.com/amichai-bd/pcie-factory/issues/389)): the full
level 1 of every IP, which `ci-gate` runs for a change to a shared
package, was over its 1200 s CPU budget. `sync2_random` and `sync2_arst`
run their first seed in level 1; their further seeds run pinned and
unchanged in each entry's level 2 `_sweep`, each seed with its coverage
instrumentation, so the merged level 0 to 2 runs are the same runs. The
mutation evidence on this page names no seed for these entries, and was
not rerun at the first seed alone.

Every [interface](../../../rtl/common/cdc/common_cdc_sync2.md#interfaces) port is exercised by all three entries.
A nonzero `RESET_VALUE`, and the `COMMON_METAFLOP`, `COMMON_METAFLOP_VAL`
and `COMMON_METAFLOP_ARST` wrappers, are covered by the
[macro plan](../common_macros.md) (`macros_contract`, `macros_random`).

Coverage runs ([#327](https://github.com/amichai-bd/pcie-factory/issues/327)): `--coverage` instruments each entry's first seed only, except where the plan row names more: those entries' later seeds reach clock-ratio bins (the 4:1 ratios, a faster destination) the first does not, on the merged level 0 to 2 coverage of `005836f`, so the merged coverage is unchanged bin for bin ([regress SPEC](../../../../tools/regress/SPEC.md#coverage)).
