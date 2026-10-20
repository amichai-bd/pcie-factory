# common_sync_ram_held DV plan

## Strategy

The [RAM family strategy](common_sync_ram.md#strategy) applies: the shared
test module and model, the per-cycle comparison, the vacuity guard and the
geometries. For this block the directed test also holds the output for
four edges while the address changes and writes rewrite the shown word,
and checks the value never moves; random traffic drops the read enable on
about three edges in ten. The functional covergroup
`common_sync_ram_held` bins write or no write crossed with read or hold.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Enabled read, hold, write independent of the enable, read-first collision, lanes](../../../rtl/common/ram/common_sync_ram_held.md#behavior) | `sync_ram_held_contract` | 0 | 1 |
| The same under random traffic, against the model | `sync_ram_held_random` | 1 | seed 590588091; 2000 cycles; unchanged parameters/backend |
| [Parameter limits](../../../rtl/common/ram/common_sync_ram_held.md#parameters) | `ram_param_limits` | 1 | shared with the family |
| Random traffic, smallest RAM | `sync_ram_held_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `sync_ram_held_long_wide` | 2 | 2; 20000 cycles |

**Plain repeat removal (#461).** Each random entry below retains its L1
configuration and assertion set. The removed `_sweep` rows collect no
coverage (`coverage_seeds: 0`); every covered entry and every distinct
geometry/backend remains. Extra random breadth and those rows' nightly
rotations are trimmed.

| Retained L1 entry | Seed | Parameters | Backend |
| --- | --- | --- | --- |
| `sync_ram_held_random` | 590588091 | WIDTH=16, ADDR_W=3, LANES=2 | `portable` |

Known faults remain checked by directed contracts and lint, not by a
sole removed random seed. `tests/test_ram.py::contract` / `directed`
consume no outer testcase RNG: `Ram.word(addr, salt)` constructs fixed
data independently. Historical seed-1 contract evidence below therefore
has identical stimulus at the retained default seed; this is a source
equivalence argument, not a newly measured mutant run.

[PR #179](https://github.com/amichai-bd/pcie-factory/pull/179) records
`sync_ram_held_contract` seed 1103837002 failing at cycle 27 when the
read output ignores hold (`rd_data_o = rd_raw`). This is a real RTL
hold mutant caught by the directed contract, not a random model mutant.
The contract and shared `ram_param_limits` entry remain unchanged.

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: `rd_data_o` tied to the always-reading RAM (no hold)
fails `sync_ram_held_contract` at the first held edge.
