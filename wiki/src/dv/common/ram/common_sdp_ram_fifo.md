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
| The same under random traffic, against the model | `sdp_ram_fifo_random` | 1 | seed 1228444380; same configuration and assertion set |
| Random traffic, smallest queue (1 bit, two RAM entries) | `sdp_ram_fifo_random_min` | 1 | seed 878957961; same configuration and assertion set |
| Model rules | (model unit test) | - | `python3 -m pytest src/dv/common/models` |

**Plain extra-seed removal (#461).** The following L1 entries retain
the same executable, top, parameters and backend as their removed plain
L2 repeats. Each extra seed had `coverage_seeds: 0`; every coverage-enabled
entry remains. This trims random breadth and the removed rows' nightly
rotations, without changing collected coverage inputs or any checker.

| Retained L1 entry | Seed | Configuration |
| --- | --- | --- |
| `sdp_ram_fifo_random` | 1228444380 | `common_sdp_ram_fifo`, WIDTH=16, ADDR_W=3, `portable` |
| `sdp_ram_fifo_random_min` | 878957961 | `common_sdp_ram_fifo`, WIDTH=1, ADDR_W=1, `portable` |

All five recorded queue mutants Q1–Q5 are already caught by retained
L0 `sdp_ram_fifo_contract` seed 763099539 in [PR #390](https://github.com/amichai-bd/pcie-factory/pull/390):
no refill at pop (cycle 7), pop never empties head (18), full one entry
early (4), empty ignoring RAM (0), RAM-used off by head (1). Q4 is
shared-unit-only, so the contract entry remains. Its directed sequence
and fixed per-index data patterns do not depend on the outer seed. The
removed random seeds add no recorded sole fault catch.

Mutation evidence ([#385](https://github.com/amichai-bd/pcie-factory/issues/385)):
the PR records the mutants of the moved queue and which entries, here
and in `tl`, catch them.
