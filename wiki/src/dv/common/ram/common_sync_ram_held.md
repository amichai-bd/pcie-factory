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
| The same under random traffic, against the model | `sync_ram_held_random` | 1 | 3; 2000 cycles |
| [Parameter limits](../../../rtl/common/ram/common_sync_ram_held.md#parameters) | `ram_param_limits` | 1 | shared with the family |
| Random traffic, smallest RAM | `sync_ram_held_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `sync_ram_held_long_wide` | 2 | 2; 20000 cycles |

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: `rd_data_o` tied to the always-reading RAM (no hold)
fails `sync_ram_held_contract` at the first held edge.
