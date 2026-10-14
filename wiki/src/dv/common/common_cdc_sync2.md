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
  value appears two or three `dst_clk_i` edges after it was driven.
- **Clock ratios:** random tests draw both periods from the seed, with a
  log-uniform ratio up to 8:1 (L1) or 16:1 (L2) either way and a random
  phase; the log prints them.
- **Out of scope:** metastability itself. Verilator is two-state and
  zero-delay, so a late resolution never happens in simulation; the
  latency bound still allows it. Whether crossings use the cells at all is
  the structural CDC check's job ([#70](https://github.com/amichai-bd/pcie-factory/issues/70)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset](../../rtl/common/common_cdc_sync2.md#behavior): zero output in reset with all-ones input; transfer at a fixed 3:5 ratio | `sync2_smoke` | 0 | 1; `WIDTH` 1, 16 updates |
| [Transfer](../../rtl/common/common_cdc_sync2.md#behavior): Gray sequence and latency at random ratios | `sync2_random` | 1 | 3; `WIDTH` 4, 200 updates, ratio up to 8:1 |
| [Transfer](../../rtl/common/common_cdc_sync2.md#behavior), long and wide | `sync2_long` | 2 | 8; `WIDTH` 8, 2000 updates, ratio up to 16:1 |

Every [interface](../../rtl/common/common_cdc_sync2.md#interfaces) port is exercised by all three entries.
