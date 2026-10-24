# common_skid_stage DV plan

## Strategy

The shared unit drives the stage directly
([`test_skid_stage.py`](../../../../src/dv/common/tests/test_skid_stage.py)).
Inputs change at falling edges. In every cycle, once they settle,
`in_ready_o`, `out_valid_o`, `out_take_o` and `out_new_o` are compared
with the cycle model
[`common_skid_stage_model.py`](../../../../src/dv/common/models/common_skid_stage_model.py),
and `out_data_o` too while `out_valid_o` is high. A scoreboard checks that
beats leave in the order taken, none lost or repeated. The model is
written from the [MAS behavior](../../rtl/common/common_skid_stage.md#behavior)
as a two-beat queue, not from the RTL's registers, and has its own pytest
unit tests. The block's assertions stop the run if they fire; no input can
make them fire, so no test breaks them on purpose.

- **Directed (`contract`):** a stream with downstream ready every cycle
  (ready never drops, no gap); a one-cycle stall that fills the skid and
  drops ready, then both beats leave in order; a long stall where the
  beat stays offered and is new only in its first cycle; drain; a reset
  with both beats held, then one beat through again.
- **Random (`random_ops`):** 3000 cycles in phases of 100 that bias
  towards streaming, stalling downstream or starving it, with a rare
  reset. A vacuity guard requires a quarter of the cycles to move a beat
  out, a skid full at a fiftieth of the edges and beats in and out at one
  edge at a tenth.
- **Coverage:** covergroup `common_skid_stage`: the beats held before
  each edge (none, out, out and skid) crossed with what happens at it (a
  beat in, a beat out, both, a stall, idle), and resets with beats held or
  empty. Seven combinations are unreachable by construction and are
  declared in the cross's `unreachable` list
  ([regress SPEC](../../../tools/regress/SPEC.md#functional-coverage)), so
  they are not bins and a sample of one fails the test: nothing leaves an
  empty stage (`none` with `out`, `in_out` or `stall`); nothing enters a
  full one, as `in_ready_o` is low (`out_skid` with `in` or `in_out`); and
  a held beat is offered (`out_valid_o` high), so an edge that moves
  nothing with a beat held is a stall, never idle (`out` or `out_skid`
  with `idle`, [#232](https://github.com/amichai-bd/pcie-factory/issues/232)).
  The derived seeds of L0 to L2 hit every other bin.
- **In its consumers:** `tl`'s transmit output stage is this block, so the
  [`tl` DV plan](../tl/DV_PLAN.md#plan) entries exercise it at `tl`'s
  beat width and under the `core` entries.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [No-gap stream, skid fill and ready, held beat, new flag, drain, reset](../../rtl/common/common_skid_stage.md#behavior) | `skid_stage_contract` | 0 | 1; `WIDTH` 8 |
| The same under random traffic, against the model | `skid_stage_random` | 1 | seed 1744405145; same configuration and assertion set |
| Random traffic, one-bit beats | `skid_stage_random_min` | 1 | seed 1728099862; same configuration and assertion set |
| Model rules | (model unit test) | - | `python3 -m pytest src/dv/common/models` |

**Plain extra-seed removal (#461).** The following L1 entries retain
the same executable, top, parameters and backend as their removed plain
L2 repeats. Each extra seed had `coverage_seeds: 0`; every coverage-enabled
entry remains. This trims random breadth and the removed rows' nightly
rotations, without changing collected coverage inputs or any checker.

| Retained L1 entry | Seed | Configuration |
| --- | --- | --- |
| `skid_stage_random` | 1744405145 | `common_skid_stage`, WIDTH=16, `portable` |
| `skid_stage_random_min` | 1728099862 | `common_skid_stage`, WIDTH=1, `portable` |

All ten recorded stage mutants S1–S10 are caught by retained L0
`skid_stage_contract` seed 1036188790 in [PR #402](https://github.com/amichai-bd/pcie-factory/pull/402):
ready ignoring skid (cycle 9), skid skipped (10), held never set (9),
output valid not reset (20), skid not emptied (10), take ignoring ready
(8), new ignoring held (9), skid overwritten by an unaccepted beat (17),
no refill at take (2), held set on empty stall (19). S3/S7 have no listed
`tl` catch; their shared-unit contract remains. Its directed sequence and
fixed data patterns do not depend on the outer seed. The #445 coverage
classification mutant also remains caught by this contract at the same
seed (135 ns). The removed random seeds add no recorded sole fault catch.

Mutation evidence ([#400](https://github.com/amichai-bd/pcie-factory/issues/400)):
the PR records the mutants of the moved stage and which entries, here and
in `tl`, catch them.
