# common_sdp_ram_fifo DV plan

## Strategy

The shared unit drives the queue directly
([`test_sdp_ram_fifo.py`](../../../../../src/dv/common/tests/test_sdp_ram_fifo.py)).
Inputs change at falling edges; after every rising edge `head_valid_o`,
`empty_o`, `full_o` and `ram_used_o` are compared with the cycle model
[`common_sdp_ram_fifo_model.py`](../../../../../src/dv/common/models/common_sdp_ram_fifo_model.py),
and `head_o` too while the head is valid. The model is written from the
[MAS behavior](../../../rtl/common/ram/common_sdp_ram_fifo.md#behavior), not
the RTL, and has its own pytest unit tests. Stimulus keeps to the
contract (no push while full, no pop with no head), so the block's
assertions stay quiet.

- **Directed (`contract`):** one push into the empty queue reaches the
  head two edges later; the queue fills to 2^`ADDR_W` + 1 entries and
  holds full; a push and a pop at every edge refill the head at each pop;
  the queue drains; a reset with entries in the RAM and at the head
  empties it, and the next push falls through again.
- **Random (`random_ops`):** 3000 cycles in phases of 200 that bias
  towards filling, draining or streaming, with a rare reset. A vacuity
  guard requires pops in a quarter of the cycles, at least one full edge,
  and push-and-pop edges in a twentieth.
- **Coverage:** covergroup `common_sdp_ram_fifo`: the operation at each
  edge (push, pop, both, idle) crossed with the fill before it (empty,
  RAM only, head only, head and RAM, full; the combinations the contract
  forbids left out), and resets with entries held or empty.
- **In its consumers:** every `tl` queue is this block, so the
  [`tl` DV plan](../../tl/DV_PLAN.md#plan) entries exercise it at the
  `tl` shapes and under the `core` entries.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Fall-through, capacity, pop and refill, drain, reset](../../../rtl/common/ram/common_sdp_ram_fifo.md#behavior) | `sdp_ram_fifo_contract` | 0 | 1; 8 x 4 |
| The same under random traffic, against the model | `sdp_ram_fifo_random` / `sdp_ram_fifo_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (1044357706 and 657912816), pinned; 3000 cycles; 16 x 8 |
| Random traffic, smallest queue (1 bit, two RAM entries) | `sdp_ram_fifo_random_min` / `sdp_ram_fifo_random_min_sweep` | 1 / 2 | derived seed 0 / derived seed 1 (1130685727), pinned; 3000 cycles; 1 x 2 |
| Model rules | (model unit test) | - | `python3 -m pytest src/dv/common/models` |

**Level placement**
([#389](https://github.com/amichai-bd/pcie-factory/issues/389)): the full
level 1 of every IP, which `ci-gate` runs for a change to a shared
package, was over its 1200 s CPU budget. `sdp_ram_fifo_random` and
`sdp_ram_fifo_random_min` run their first seed in level 1; their further
seeds run pinned and unchanged in each entry's level 2 `_sweep`, each seed
with its coverage instrumentation, so the merged level 0 to 2 runs are the
same runs. The mutation evidence on this page names no seed for these
entries, and was not rerun at the first seed alone.

Mutation evidence ([#385](https://github.com/amichai-bd/pcie-factory/issues/385)):
the PR records the mutants of the moved queue and which entries, here
and in `tl`, catch them.
