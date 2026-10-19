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
| [Write-through read, per-lane merge, disabled writes, lanes](../../../rtl/common/ram/common_sync_ram_wt.md#behavior) | `sync_ram_wt_contract` | 0 | 1 |
| The same under random traffic (partial-lane writes), against the model | `sync_ram_wt_random` / `sync_ram_wt_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (644655698 and 1063648232), pinned; 2000 cycles |
| [Parameter limits](../../../rtl/common/ram/common_sync_ram_wt.md#parameters) | `ram_param_limits` | 1 | shared with the family |
| Random traffic, smallest RAM | `sync_ram_wt_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `sync_ram_wt_long_wide` | 2 | 2; 20000 cycles |

**Level placement**
([#389](https://github.com/amichai-bd/pcie-factory/issues/389)): the full
level 1 of every IP, which `ci-gate` runs for a change to a shared
package, was over its 1200 s CPU budget. `sync_ram_wt_random` runs one seed
in level 1 (the first, unless the row says otherwise); its further seeds
run pinned and unchanged in the entry's level 2 `_sweep`, each seed with
its coverage instrumentation, so the merged level 0 to 2 runs are the same
runs. Any mutant recorded on this page is still first caught at the seed
it names, or at the entry's first seed, which stays in level 1.

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: a read-first output fails `sync_ram_wt_contract` at the
first write; a whole-word bypass (any lane written selects every lane)
fails `sync_ram_wt_random` at the first partial-lane write.
