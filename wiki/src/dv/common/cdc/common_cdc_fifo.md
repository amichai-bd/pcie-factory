# common_cdc_fifo DV plan

## Strategy

- **Shared unit:** tested with the other `common_` cells as the shared
  unit (`--ip common`); see the [sync2 plan](common_cdc_sync2.md#strategy).
- **Tests:** [`tests/test_cdc_fifo.py`](../../../../../src/dv/common/tests/test_cdc_fifo.py)
  drives each side at the falling edge of its own clock. The writer offers
  random words at a set rate and records each word taken (offered while
  `src_ready_o` is high); the reader asserts `dst_ready_i` at a set rate
  and compares every word taken with the scoreboard queue. At every
  destination cycle it also checks `dst_fill_o` (non-zero exactly with
  `dst_valid_o`, never above the words written and not read) and, at a fill
  of 2 or more, `dst_next_data_o` against the second queued word. The depth
  comes from the `ADDR_W` parameter.
- **Oracle, from the [MAS](../../../rtl/common/cdc/common_cdc_fifo.md#behavior):**
  order and integrity of every word; reset state (empty and not full);
  exactly `2**ADDR_W` words taken before `src_ready_o` drops; `dst_valid_o`
  drops right after the last word; the far flag (`dst_valid_o` after the
  first write, `src_ready_o` after the first read from full) rises exactly
  two edges of its clock later (one when an edge coincides, as in the
  [sync2 plan](common_cdc_sync2.md#strategy)); a drained FIFO is empty and
  not full, with `dst_fill_o` 0; a full FIFO shows `dst_fill_o` of
  `2**ADDR_W`; a reset of both sides with words in flight empties it.
- **Run length:** each random rate phase lasts a number of cycles of the
  faster clock, and each round's full phase writes only until the FIFO is
  full (failing if it is not full after `2 * 2**ADDR_W + 8` source
  cycles). The run's clock edges, and so its wall time, therefore do not
  grow with the ratio in either direction; the edge log bisects, so long
  runs stay linear.
- **Coverage guard:** the random tests force a full phase every round and
  fail unless the run reached full, reached empty after data and wrapped
  the pointers (more than `2 * 2**ADDR_W` words).
- **Functional coverage:** the `common_cdc_fifo` covergroup bins the clock
  ratio as in the [sync2 plan](common_cdc_sync2.md#strategy), each source
  cycle (push, stalled by full, idle) and each destination cycle (pop,
  stalled by empty, word held, idle), crossed with the ratio, a reset
  with words in flight, and the read-side fill (empty, one word, more,
  full). The derived seeds of L0 to L2 hit every bin. No
  coverage floor is set yet
  ([DV BKM](../../../../bkm/dv.md#coverage)).
- **Clock ratios:** fixed 2:5 in L0; random in L1 and L2 as in the sync2
  plan, and in the directed full/empty tests with the faster side fixed by
  the test (ratio up to 8:1).
- **Out of scope:** reset of one side alone, and words offered during
  reset (undefined or dropped by the MAS);
  metastability, as in the sync2 plan.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset state, order](../../../rtl/common/cdc/common_cdc_fifo.md#behavior): one word, then a burst at a fixed 2:5 ratio | `fifo_smoke` | 0 | 1; `ADDR_W` 2 |
| [Full, empty, wrap, flag latency](../../../rtl/common/cdc/common_cdc_fifo.md#behavior), source faster | `fifo_full_empty_src_fast` | 1 | 2; five fill/drain rounds |
| [Full, empty, wrap, flag latency](../../../rtl/common/cdc/common_cdc_fifo.md#behavior), destination faster | `fifo_full_empty_dst_fast` | 1 | 2; five fill/drain rounds |
| [Reset](../../../rtl/common/cdc/common_cdc_fifo.md#behavior) of both sides with words in flight, three times, then checked traffic | `fifo_reset_in_flight` | 1 | 2; ratio up to 8:1 |
| [Order and capacity](../../../rtl/common/cdc/common_cdc_fifo.md#behavior) under random rates and ratios; read-side fill and look-ahead every cycle | `fifo_random` | 1 | 3; `ADDR_W` 2, 40 rounds, ratio up to 8:1 |
| Random traffic, smallest depth | `fifo_long_depth2` | 2 | 4; `ADDR_W` 1, `WIDTH` 4, 400 rounds, ratio up to 16:1 |
| Random traffic, deep and wide | `fifo_long_depth16` | 2 | 4; `ADDR_W` 4, `WIDTH` 16, 400 rounds, ratio up to 16:1 |

Every [interface](../../../rtl/common/cdc/common_cdc_fifo.md#interfaces) port is exercised by every entry; `dst_fill_o` and `dst_next_data_o` are checked every destination cycle of every entry.

Mutation evidence ([#84](https://github.com/amichai-bd/pcie-factory/issues/84)),
in a scratch copy: `dst_fill_o` off by one fails `fifo_random` ("dst_fill_o 1
with dst_valid_o 0"); `dst_next_data_o` reading the oldest word fails it
("dst_next_data_o expected ... got ...").
