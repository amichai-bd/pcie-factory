# common_sdp_ram DV plan

## Strategy

The [RAM family strategy](common_sync_ram.md#strategy) applies. For this
block the directed test also writes one word while reading another, and
holds the output for four edges while the read address changes and the
shown word is rewritten; random traffic draws the write and read
addresses independently (equal three times in ten) and drops the read
enable on about three edges in ten. The functional covergroup
`common_sdp_ram` bins (by read address; a write counting only with a nonzero lane mask) same-word collisions, write with read, write with
hold, read and hold.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Separate ports, read enable and hold, read-first collision, lanes](../../../rtl/common/ram/common_sdp_ram.md#behavior) | `sdp_ram_contract` | 0 | 1 |
| The same under random traffic, against the model | `sdp_ram_random` | 1 | 3; 2000 cycles |
| [Parameter limits](../../../rtl/common/ram/common_sdp_ram.md#parameters) | `ram_param_limits` | 1 | shared with the family |
| Random traffic, smallest RAM | `sdp_ram_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `sdp_ram_long_wide` | 2 | 2; 20000 cycles |
| [Intel backend](../../../rtl/common/ram/common_sdp_ram.md#intel-backend) through the stub: separate ports, hold, collision, lanes | `sdp_ram_intel_contract` | 0 | 1; `backend: fpga` |
| Intel backend: legal geometries lint with exactly the expected messages; illegal ones fail with the block's message | `ram_intel_lint` | 0 | 1; 10 lint runs per backend |
| Intel backend: the stub accepts the backend shape and rejects each rule alone | `ram_intel_stub_rejects` | 0 | 1; 16 lint runs |
| Intel backend under random traffic, against the model | `sdp_ram_intel_random` | 1 | 3; 2000 cycles; `backend: fpga` |
| Intel backend at the dll receive buffer shape (512 x 34, one lane) | `sdp_ram_intel_rx_buf_shape` | 1 | 1; `backend: fpga` |

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: a read port that ignores `rd_en_i` fails
`sdp_ram_contract` at the first held edge. For the Intel backend
([#280](https://github.com/amichai-bd/pcie-factory/issues/280)): `rden_b`
tied high fails `sdp_ram_intel_contract` at the first held edge, and a
mixed-port `DONT_CARE` request fails `ram_intel_lint` with the stub's
message. Restoring the one-lane `width_byteena_a = 1`, `byte_size = 8`
setting that Quartus rejected fails `ram_intel_lint` and
`sdp_ram_intel_rx_buf_shape` with the stub's byte-size message.
