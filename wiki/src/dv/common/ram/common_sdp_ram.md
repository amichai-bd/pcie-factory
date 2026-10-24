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
| The same under random traffic, against the model | `sdp_ram_random` | 1 | seed 96800220; 2000 cycles; unchanged parameters/backend |
| [Parameter limits](../../../rtl/common/ram/common_sdp_ram.md#parameters) | `ram_param_limits` | 1 | shared with the family |
| Random traffic, smallest RAM | `sdp_ram_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `sdp_ram_long_wide` | 2 | 2; 20000 cycles |
| [Intel backend](../../../rtl/common/ram/common_sdp_ram.md#intel-backend) through the stub: separate ports, hold, collision, lanes | `sdp_ram_intel_contract` | 0 | 1; `backend: fpga` |
| Intel backend: legal geometries lint with exactly the expected messages; illegal ones fail with the block's message | `ram_intel_lint` | 0 | 1; 10 lint runs for this backend, 9 for [`common_sync_ram`'s](common_sync_ram.md#plan) |
| Intel backend: the stub accepts each backend's shape (`DUAL_PORT` here, `SINGLE_PORT` for `common_sync_ram`) and rejects each rule alone | `ram_intel_stub_rejects` | 0 | 1; 33 lint runs: the two shapes, 16 `DUAL_PORT` and 15 `SINGLE_PORT` rejections |
| Intel backend under random traffic, against the model | `sdp_ram_intel_random` | 1 | seed 1237643432; 2000 cycles; unchanged parameters/backend |
| Intel backend at the dll receive buffer shape (512 x 34, one lane) | `sdp_ram_intel_rx_buf_shape` | 1 | 1; `backend: fpga` |
| [sky130 backend](../../../rtl/common/ram/common_sdp_ram.md#sky130-backend) on macros, through the DV macro model: separate ports, hold, collision (pending-write bypass), lane enable | `sdp_ram_sky130_contract` | 0 | 1; 72 x 128 (9216 bits): two macros across, low words; `backend: asic` |
| sky130 backend on its portable array (below 8192 bits, or several lanes): separate ports, hold, collision, lanes | `sdp_ram_sky130_flops_contract` | 0 | 1; 16 x 8, two lanes; `backend: asic` |
| sky130 backend: legal geometries (both dll shapes, the macro regression shape, two macros across, the flop shapes, a 12-lane table, the smallest) lint with only `DECLFILENAME`; illegal ones fail with the block's message | `ram_sky130_lint` | 0 | 1; 11 lint runs |
| sky130 backend: [macro selection](../../../rtl/common/ram/common_sdp_ram.md#sky130-backend), linted without the macro model: only one-lane RAMs of at least 8192 bits miss the macro (8192 and 9216 bits do, 8064 bits, a two-lane 16 kbit RAM and smaller ones do not) | `ram_sky130_select` | 0 | 1; 7 lint runs |
| sky130 backend on macros under random traffic, against the model (the macro model is fatal on any physical same-word collision) | `sdp_ram_sky130_random` | 1 | seed 540467899; 2000 cycles; unchanged parameters/backend |
| sky130 backend on its portable array under random traffic | `sdp_ram_sky130_flops_random` | 1 | seed 513956005; 2000 cycles; unchanged parameters/backend |
| sky130 backend at the dll receive buffer shape (512 x 34: two macro rows) | `sdp_ram_sky130_rx_buf_shape` | 1 | 1; `backend: asic` |
| sky130 backend at the dll replay buffer shape (256 x 32: one full macro) | `sdp_ram_sky130_replay_shape` | 1 | 1; `backend: asic` |

**Plain repeat removal (#461).** Each random entry below retains its L1
configuration and assertion set. The removed `_sweep` rows collect no
coverage (`coverage_seeds: 0`); every covered entry and every distinct
geometry/backend remains. Extra random breadth and those rows' nightly
rotations are trimmed.

| Retained L1 entry | Seed | Parameters | Backend |
| --- | --- | --- | --- |
| `sdp_ram_random` | 96800220 | WIDTH=16, ADDR_W=3, LANES=2 | `portable` |
| `sdp_ram_intel_random` | 1237643432 | WIDTH=16, ADDR_W=3, LANES=2 | `fpga` |
| `sdp_ram_sky130_random` | 540467899 | WIDTH=72, ADDR_W=7, LANES=1 | `asic` |
| `sdp_ram_sky130_flops_random` | 513956005 | WIDTH=16, ADDR_W=3, LANES=2 | `asic` |

Known faults remain checked by directed contracts and lint, not by a
sole removed random seed. `tests/test_ram.py::contract` / `directed`
consume no outer testcase RNG: `Ram.word(addr, salt)` constructs fixed
data independently. Historical seed-1 contract evidence below therefore
has identical stimulus at the retained default seed; this is a source
equivalence argument, not a newly measured mutant run.

[PR #179](https://github.com/amichai-bd/pcie-factory/pull/179) records
`sdp_ram_contract` seed 1305197499 catching an ignored read-enable at
cycle 27. Intel [PR #300](https://github.com/amichai-bd/pcie-factory/pull/300)
and [PR #306](https://github.com/amichai-bd/pcie-factory/pull/306) record
M1–M8 at seed 1: hold, byte enables, read address and vendor-pin/latency/
RDW/byte-size faults fail retained `sdp_ram_intel_contract`,
`sdp_ram_intel_rx_buf_shape` or `ram_intel_lint` as listed below.
For sky130 [PR #304](https://github.com/amichai-bd/pcie-factory/pull/304),
M1 hold, M2 bypass, M3 collision suppression and M4 lane-enable fail the
retained contract at seed 1; M5 read-row and M6 write-row decode fail
`sdp_ram_sky130_rx_buf_shape` seed 1927277090 (cycles 768/512), which
remains because the one-row contract cannot replace it.
[PR #350](https://github.com/amichai-bd/pcie-factory/pull/350) records
selection M1/M2 caught by retained `ram_sky130_select` seed 1, and M3
forced macros caught by `sdp_ram_sky130_flops_contract` seed 1 at cycle
22 (lane enable). Macro-model fatal collision checking remains active.

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: a read port that ignores `rd_en_i` fails
`sdp_ram_contract` at the first held edge. For the Intel backend
([#280](https://github.com/amichai-bd/pcie-factory/issues/280)): `rden_b`
tied high fails `sdp_ram_intel_contract` at the first held edge, and a
mixed-port `DONT_CARE` request fails `ram_intel_lint` with the stub's
message. Restoring the one-lane `width_byteena_a = 1`, `byte_size = 8`
setting that Quartus rejected fails `ram_intel_lint` and
`sdp_ram_intel_rx_buf_shape` with the stub's byte-size message.
For the sky130 backend ([#279](https://github.com/amichai-bd/pcie-factory/issues/279)):
a backend that ignores the lane enable failed `sdp_ram_sky130_contract`
during development, and in scratch copies the hold register removed, the
bypass removed and the collision suppression removed each fail the
contract entry (the last with the macro model's fatal collision message).
The two-row tiling is shown at the receive buffer shape: the read row select
fixed to row 0, and the write row decode dropped (every row written), each
fail `sdp_ram_sky130_rx_buf_shape`, which the one-row contract entry cannot
see. The PR records them. For the macro selection
([#296](https://github.com/amichai-bd/pcie-factory/issues/296)): a
threshold one bit higher, and a selection that ignores the lane count, each
fail `ram_sky130_select`; both paths keep the same cycle behavior, so the
contract entries cannot see the selection.
