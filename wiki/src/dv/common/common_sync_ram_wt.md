# common_sync_ram_wt DV plan

## Strategy

The [RAM family strategy](common_sync_ram.md#strategy) applies. For this
block the model replaces the written lanes in the read result, so every
write edge checks the bypass, lane by lane; the directed collision step
asserts the new word directly. The functional covergroup
`common_sync_ram_wt` bins write and read edges crossed with the lanes of
each write.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Write-through read, per-lane merge, disabled writes, lanes](../../rtl/common/common_sync_ram_wt.md#behavior) | `sync_ram_wt_contract` | 0 | 1 |
| The same under random traffic (partial-lane writes), against the model | `sync_ram_wt_random` | 1 | 3; 2000 cycles |
| [Parameter limits](../../rtl/common/common_sync_ram_wt.md#parameters) | `ram_param_limits` | 1 | shared with the family |
| Random traffic, smallest RAM | `sync_ram_wt_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `sync_ram_wt_long_wide` | 2 | 2; 20000 cycles |

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: a read-first output fails `sync_ram_wt_contract` at the
first write; a whole-word bypass (any lane written selects every lane)
fails `sync_ram_wt_random` at the first partial-lane write.
