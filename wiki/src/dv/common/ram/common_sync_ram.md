# common_sync_ram DV plan

## Strategy

This strategy serves the whole RAM family; each block's plan links here.

- **Shared unit:** tested with the other `common_` blocks as the shared
  unit (`--ip common`). Each family block is a regression top of its own;
  one test module, [`tests/test_ram.py`](../../../../../src/dv/common/tests/test_ram.py),
  serves every top, choosing the ports from the top's name and the
  geometry from `WIDTH`, `ADDR_W` and `LANES`.
- **Oracle, from the MAS:**
  [`models/common_ram_model.py`](../../../../../src/dv/common/models/common_ram_model.py),
  a cycle model of each block's [behavior](../../../rtl/common/ram/common_sync_ram.md#behavior):
  each read port captures the word before the edge, enabled writes merge
  per lane, write-through replaces the written lanes, a held read keeps
  its value, and both true dual-port ports read before either write. It
  tracks which bits are defined (written lanes, enabled reads); only those
  are compared. Its pytest tests run in `./build ci unit` (`models:common`).
- **Checks:** inputs change at falling `clk_i` edges; after every rising
  edge every read port is compared with the model, so a read one edge
  early or late, a wrong collision rule, a lost or extra lane, or a broken
  hold fails at that cycle with the inputs printed. The directed test also
  asserts the expected values of the read-back, collision and hold steps
  directly.
- **Vacuity guard:** the random tests fail unless more than half the
  compared bits were defined, more than one edge in twenty was a same-word
  read and real write (at least one lane), and (held and simple
  dual-port) more than one edge in ten held.
- **Parameter limits:** a rejected geometry cannot be simulated, so
  [`tests/test_ram_params.py`](../../../../../src/dv/common/tests/test_ram_params.py)
  lints every family block from its `.rtl.f` with Verilator `-Wall`, as
  `./build check` does: `WIDTH` 0, `ADDR_W` 0, `LANES` 0 and `LANES` 3 with
  `WIDTH` 8 must each stop elaboration with the block's own
  `COMMON_ASSERT_PARAM` message, and the smallest RAM (`WIDTH` 1, `ADDR_W`
  1) and one lane per bit (`WIDTH` 8, `LANES` 8) must lint clean. The
  simulated top only hosts the test.
- **Geometries:** L0 and L1 use `WIDTH` 16, `ADDR_W` 3, `LANES` 2 (eight
  words, so random addresses collide often, and two lanes); L2 runs the
  smallest RAM (`WIDTH` 1, `ADDR_W` 1, `LANES` 1) and a wide one (`WIDTH`
  64, `ADDR_W` 6, `LANES` 8).
- **Functional coverage:** one covergroup per block, `common_<block>`.
  A write counts only when it writes a lane (write enable and a nonzero
  lane mask; for the true dual-port `both_wr*` bins, on both ports).
  - `op`: the operation at each edge (per block: write or read; write,
    read and hold combinations; same-word collisions; the dual-port cases).
  - `lanes`: the lane enables of every write-enabled edge, `none` (write
    enable with no lane), `partial` or `all`; port `a`'s lanes when it is
    write-enabled, else port `b`'s.
  - `wr_op_x_lanes`: each writing operation crossed with the lanes of the
    real write (`partial`, `all`); for the true dual-port `both_wr*` bins,
    the lanes both ports write together, so `both_wr_same_word` with `all`
    means two disjoint writes fill the word.
  - `addr`: the first, last or another word at port `a`'s address, the
    read address of the simple dual-port RAM. Write-address and port-`b`
    extremes are not binned; the directed test fills and reads every word
    on every port.

  The derived seeds of L0 to L2 hit every bin. No coverage floor is set
  yet ([DV BKM](../../../../bkm/dv.md#coverage)).
- **Intel backend:** the same contract tests run on
  [`common_sync_ram`'s Intel backend](../../../rtl/common/ram/common_sync_ram.md#intel-backend)
  through the DV-only `altsyncram` stub (entries with `backend: fpga`), at
  the regression shape and at the `rc_tl` host memory shape (1024 x 32,
  four lanes). The backend's lint test and the stub's rejection test are
  shared with the `common_sdp_ram` backend
  ([its plan](common_sdp_ram.md#plan)): they cover both backends and both
  stub shapes.
- **Out of scope:** reset (the blocks have none); the vendor mapping,
  which only a synthesis flow's fit shows
  ([fitter evidence](../../../rtl/common/ram/common_sync_ram.md#intel-backend));
  contents before the first write, which the MAS leaves undefined.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Read, write, read-first collision, disabled writes, lanes, one-edge latency](../../../rtl/common/ram/common_sync_ram.md#behavior): fill and read back every word, then directed steps | `sync_ram_contract` | 0 | 1 |
| The same under random traffic, against the model | `sync_ram_random` / `sync_ram_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (976709618 and 591279688), pinned; 2000 cycles |
| [Parameter limits](../../../rtl/common/ram/common_sync_ram.md#parameters) of every family block | `ram_param_limits` | 1 | 1; 30 lint runs |
| Random traffic, smallest RAM | `sync_ram_long_min` | 2 | 2; 20000 cycles |
| Random traffic, wide RAM with eight lanes | `sync_ram_long_wide` | 2 | 2; 20000 cycles |
| [Intel backend](../../../rtl/common/ram/common_sync_ram.md#intel-backend) through the stub: read every edge, read-first collision, disabled writes, lanes | `sync_ram_intel_contract` | 0 | 1; `backend: fpga` |
| Intel backend under random traffic, against the model | `sync_ram_intel_random` / `sync_ram_intel_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (1976089761 and 1824648475), pinned; 2000 cycles; `backend: fpga` |
| Intel backend at the `rc_tl` host memory shape (1024 x 32, four 8-bit lanes) | `sync_ram_intel_host_mem_shape` | 1 | 1; `backend: fpga` |
| Intel backend: legal geometries lint with exactly the expected messages, illegal ones fail with the block's message; the stub accepts the `SINGLE_PORT` shape and rejects each rule alone | `ram_intel_lint`, `ram_intel_stub_rejects` | 0 | shared with [`common_sdp_ram`](common_sdp_ram.md#plan) |

**Level placement:** the portable `sync_ram_random` keeps its first seed
at level 1; its further seeds moved to the pinned level 2
`sync_ram_random_sweep` under
[#389](https://github.com/amichai-bd/pcie-factory/issues/389) to reduce the
all-IP level 1 CPU cost. The Intel backend introduced by
[#417](https://github.com/amichai-bd/pcie-factory/issues/417) follows the
same placement: `sync_ram_intel_random` at level 1 and two pinned seeds in
`sync_ram_intel_random_sweep` at level 2. Both sweeps set
`coverage_seeds: 0`, so their additional seeds run without coverage
instrumentation even in a coverage regression. The level 1 entries retain
their configured coverage sampling. The Intel mutants below are caught by
the directed contract or lint entries, independently of the random sweeps.

Mutation evidence ([#126](https://github.com/amichai-bd/pcie-factory/issues/126)),
in a scratch copy: ignoring `wr_lanes_i` fails `sync_ram_contract` ("rd_data
expected ... got ..." at the lane-none write); an extra read register fails
it at the first read-back; dropping the `LANES`-divides-`WIDTH` term from a
block's check fails `ram_param_limits`. For the Intel backend
([#417](https://github.com/amichai-bd/pcie-factory/issues/417)):
`byteena_a` tied to all lanes fails `sync_ram_intel_contract` in the
one-lane writes (cycle 22, "rd_data expected ... got ..."); `rden_a` tied
low fails it at the first edge with the stub's tie message; a same-port
`NEW_DATA_NO_NBE_READ` request fails `ram_intel_lint` and the
`backend: fpga` build with the stub's read-during-write message.
