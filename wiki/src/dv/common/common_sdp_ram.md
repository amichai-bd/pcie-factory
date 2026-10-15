# common_sdp_ram DV plan

## Strategy

The [RAM family strategy](common_sync_ram.md#strategy) applies. For this
block the directed test also writes one word while reading another, and
holds the output for four edges while the read address changes and the
shown word is rewritten; random traffic draws the write and read
addresses independently (equal three times in ten) and drops the read
enable on about three edges in ten. The functional covergroup
`common_sdp_ram` bins same-word collisions, write with read, write with
hold, read and hold.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Separate ports, read enable and hold, read-first collision, lanes](../../rtl/common/common_sdp_ram.md#behavior) | `sdp_ram_contract` | 0 | 1 |
| The same under random traffic, against the model | `sdp_ram_random` | 1 | 3; 2000 cycles |
| [Parameter limits](../../rtl/common/common_sdp_ram.md#parameters) | `ram_param_limits` | 1 | shared with the family |
| Random traffic, smallest RAM | `sdp_ram_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `sdp_ram_long_wide` | 2 | 2; 20000 cycles |

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: a read port that ignores `rd_en_i` fails
`sdp_ram_contract` at the first held edge.
