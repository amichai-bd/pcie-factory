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
  beat in, a beat out, both, a stall, idle; the combinations the ready
  rule forbids left out), and resets with beats held or empty.
- **In its consumers:** `tl`'s transmit output stage is this block, so the
  [`tl` DV plan](../tl/DV_PLAN.md#plan) entries exercise it at `tl`'s
  beat width and under the `core` entries.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [No-gap stream, skid fill and ready, held beat, new flag, drain, reset](../../rtl/common/common_skid_stage.md#behavior) | `skid_stage_contract` | 0 | 1; `WIDTH` 8 |
| The same under random traffic, against the model | `skid_stage_random` / `skid_stage_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (285127183 and 167240629), pinned; 3000 cycles; `WIDTH` 16 |
| Random traffic, one-bit beats | `skid_stage_random_min` / `skid_stage_random_min_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (268928640), pinned; 3000 cycles; `WIDTH` 1 |
| Model rules | (model unit test) | - | `python3 -m pytest src/dv/common/models` |

**Level placement**
([#389](https://github.com/amichai-bd/pcie-factory/issues/389)): the full
level 1 of every IP, which `ci-gate` runs for a change to a shared
package, was over its 1200 s CPU budget. `skid_stage_random`,
`skid_stage_random_min` run one seed in level 1 (the first, unless the row
says otherwise); their further seeds run pinned and unchanged in the
entry's level 2 `_sweep`, each seed with its coverage instrumentation, so
the merged level 0 to 2 runs are the same runs. Any mutant recorded on
this page is still first caught at the seed it names, or at the entry's
first seed, which stays in level 1.

Mutation evidence ([#400](https://github.com/amichai-bd/pcie-factory/issues/400)):
the PR records the mutants of the moved stage and which entries, here and
in `tl`, catch them.
