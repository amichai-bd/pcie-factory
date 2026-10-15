# common_tdp_ram DV plan

## Strategy

The [RAM family strategy](common_sync_ram.md#strategy) applies, with both
ports compared every edge. The directed test reads each word back on both
ports in opposite orders, has each port read the word the other writes,
writes different words on both ports at one edge, and (two or more lanes)
writes disjoint lanes of one word from both ports. Random traffic shares
the address between the ports three times in ten and clears port `b`'s
common lanes when both would write one word, so it stays inside the
contract. The functional covergroup `common_tdp_ram` bins (a write counting only on a port with a nonzero lane mask) each port
writing the word the other reads, both writing different words, both
writing one word, one write and reads only.

- **Write collision assertion:** a regression cannot pass on a simulator
  stop, so `a_no_write_collision` is proven once by hand (below) rather
  than in a regression; the model also rejects such stimulus.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Two ports, read-first collisions within and across ports, disjoint-lane double write, lanes](../../rtl/common/common_tdp_ram.md#behavior) | `tdp_ram_contract` | 0 | 1 |
| The same under random traffic, against the model | `tdp_ram_random` | 1 | 3; 2000 cycles |
| [Parameter limits](../../rtl/common/common_tdp_ram.md#parameters) | `ram_param_limits` | 1 | shared with the family |
| Random traffic, smallest RAM | `tdp_ram_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `tdp_ram_long_wide` | 2 | 2; 20000 cycles |

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: port `b` reading at `a_addr_i` fails `tdp_ram_contract`
at the first read-back. With the random test's lane clearing removed and
the model step delayed, `tdp_ram_random` stops on
`assertion a_no_write_collision failed`.
