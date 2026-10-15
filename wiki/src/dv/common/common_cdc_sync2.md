# common_cdc_sync2 DV plan

## Strategy

- **Shared unit:** the `common_` cells are tested together as the shared
  unit (`./build sim --ip common ...`); its lists are in
  [`src/dv/common/regress/`](../../../../src/dv/common/regress/level0.yaml)
  ([layout](../../../project/layout.md#shared-blocks)).
- **Tests:** [`tests/test_cdc_sync2.py`](../../../../src/dv/common/tests/test_cdc_sync2.py)
  drives the cell directly. The cell has no source clock, so the test
  models the source flop: it changes `src_data_i` on a source-clock grid,
  one random bit per update (a Gray sequence), holding each value for at
  least two (up to about five) `dst_clk_i` periods, as the [MAS](../../rtl/common/common_cdc_sync2.md#behavior)
  requires. `dst_data_o` is sampled at every falling `dst_clk_i` edge, and
  [`tb/cdc_env.py`](../../../../src/dv/common/tb/cdc_env.py) logs every
  rising edge to measure latency.
- **Oracle, from the MAS:** the sequence of distinct `dst_data_o` values
  equals the sent sequence (nothing skipped, nothing invented), and each
  value appears exactly two `dst_clk_i` edges strictly after it was driven (one when a
  `dst_clk_i` edge coincides with the update and captures it). Zero-delay
  simulation has no late resolution, so the MAS's third edge is not
  accepted, and a three-flop synchronizer fails.
- **Clock ratios:** random tests draw both periods from the seed with
  [`models/common_cdc_clocks.py`](../../../../src/dv/common/models/common_cdc_clocks.py):
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
  ([DV BKM](../../../bkm/dv.md#coverage)).
- **Out of scope:** metastability itself. Verilator is two-state and
  zero-delay, so a late resolution never happens in simulation. Whether crossings use the cells at all is
  the structural CDC check's job ([#70](https://github.com/amichai-bd/pcie-factory/issues/70)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset](../../rtl/common/common_cdc_sync2.md#behavior): zero output in reset with all-ones input; transfer at a fixed 3:5 ratio | `sync2_smoke` | 0 | 1; `WIDTH` 1, 16 updates |
| [Transfer](../../rtl/common/common_cdc_sync2.md#behavior): Gray sequence and latency at random ratios | `sync2_random` | 1 | 3; `WIDTH` 4, 200 updates, ratio up to 8:1 |
| [Transfer](../../rtl/common/common_cdc_sync2.md#behavior), long and wide | `sync2_long` | 2 | 8; `WIDTH` 8, 2000 updates, ratio up to 16:1 |

Every [interface](../../rtl/common/common_cdc_sync2.md#interfaces) port is exercised by all three entries.
