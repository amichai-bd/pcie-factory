# pcie_rc_seq DV plan

The interpreter and its programs
([#376](https://github.com/amichai-bd/pcie-factory/issues/376)) are
verified by this testbench against a stand-in for `rc_tl` that conforms
to the [tl-seq interface](../../arch/rc/TL_SEQ.md). The programs against
the real `rc_tl` and `core` are the `rc` e2e's
([`rc` DV plan](../rc/DV_PLAN.md)).

## Strategy

- **No SV testbench:** cocotb drives `pcie_rc_seq` directly through its
  ports (`rc_seq.dv.f` is empty); each entry sets `PROGRAM`.
- **Environment:** [`tb/rc_seq_env.py`](../../../../src/dv/rc_seq/tb/rc_seq_env.py),
  one step per `clk_i` cycle. At the falling edge the stand-in reads the
  registered outputs and drives the cycle's inputs; once they settle the
  environment reads every output and compares it with the reference
  model: `cmd_valid_o` and `cmd_o` (while valid), `msi_ready_o`,
  `hot_reset_req_o`, `done_o`, `pass_o`, `fail_step_o`, every cycle,
  reset included. A mismatch names the cycle, the model's step and the
  field.
- **Reference model:** [`models/pcie_rc_seq_model.py`](../../../../src/dv/rc_seq/models/pcie_rc_seq_model.py),
  written from the [MAS](../../rtl/rc_seq/MAS.md) and the
  [programs page](../../rtl/rc_seq/PROGRAMS.md): the step format, an
  assembler with label targets, every program built from the programs
  page, and the interpreter cycle by cycle per the
  [MAS timing](../../rtl/rc_seq/MAS.md#timing). The RTL runs its package
  image and the model the image it builds itself, so a wrong word in an
  image fails at the step that differs.
- **Stand-in:** [`models/pcie_rc_seq_standin.py`](../../../../src/dv/rc_seq/models/pcie_rc_seq_standin.py),
  for `rc_tl` and the endpoint behind it: random ready delays, result
  beats in command order with random gaps (a local command's first beat
  possibly in the take cycle, a `MEM_RD` as `len` beats), the field checks
  and routing answering `LOCAL_UR`, `LINK_DOWN` while DL_Down, the error
  record, the MSI capture (depth 4, overflow sets bit 12); behind it the
  endpoint at `core` defaults as the [program boundary](../../rtl/rc_seq/PROGRAMS.md#boundary)
  needs (configuration registers, BAR0 memory, the DMA doorbell, the
  no-answer DW, Unsupported Requests and their messages) and a link that
  trains, takes a Hot Reset and, in `ERRORS`, goes down once under
  traffic. For `SELFTEST` the link and MSI follow the program's kick
  writes, and tests set guard DWs, poll answers, a status, a wrong beat or
  capture, and the cycle a wait's condition first holds.
- **Faults that make the image's checked fields matter:** an `EXPECT`
  mask, a `REQ`'s `chk` and `status`, or a data `JUMP`'s value and mask
  that differ between image and model change no output while the stand-in
  answers as expected, so the cycle comparison alone cannot see them. From
  the program's passing run on the model alone, the environment builds
  one run per fault ([`corruptions`](../../../../src/dv/rc_seq/models/pcie_rc_seq_standin.py)):
  - **expect:** for each `EXPECT`, the lowest and the highest bit of its
    mask that no step since the register's source (the `REQ` or
    `WAIT_MSI` that wrote it, with only `EXPECT`s and `JUMP`s between)
    reads, flipped in that beat or in the capture's data or offset; the
    run must fail at that `EXPECT`;
  - **status:** for each `REQ` with `chk` 1, its last beat answered with
    another status; the run must fail at that `REQ`;
  - **jump:** for each data `JUMP`, at its first and last occurrence, the
    lowest and highest free bit of its mask flipped likewise, which turns
    it; the run must end where the model's run with the same fault ends
    (a poll runs once more, a skip falls into what it skips).

  Model unit tests check that every `EXPECT` and every `chk` 1 `REQ` of
  every program is reached, and every polling `JUMP`. A data `JUMP` whose
  mask bits an earlier `JUMP` on the same read already decides (the
  `ERRORS` pass selection, the `SELFTEST` jump tests on `R[0]`), or whose
  register comes from a step further back (`DATA_EQ` on `R[5]`), gets no
  fault: its value and target are seen through the path of the passing
  runs instead.
- **Long waits:** the `timeouts_*` entries let a program's wait run out
  (`WAIT_LINK` 25 000 000 cycles, `WAIT_MSI` 1 250 000, `HOT_RESET`
  125 000) and a poll take 4096 times. Once a wait has seen its condition
  false and the stand-in has nothing pending, the environment lets the
  simulator run freely to 8 cycles before the timeout and advances the
  model's counter by the cycles skipped; the cycles around the timeout are
  compared, so the image's constant must equal the programs page's.
- **Assertions:** the RTL's own, on the interface (a beat only for a
  taken command, at most 32 beats, the command held while offered, a
  capture taken only when offered).
- **Model unit tests:** [`models/test_pcie_rc_seq_model.py`](../../../../src/dv/rc_seq/models/test_pcie_rc_seq_model.py)
  (pytest, no simulator): field positions, constructor defaults, every
  program passing on six stand-in seeds and failing at corrupted
  `EXPECT`s, wait bounds, the loop limit, out-of-ROM and reserved ops,
  start and reset, the stand-in's refusals and capture overflow, every
  `chk` 1 `REQ` failing at its status, every jump fault ending; and a
  second semantic reference: the `rc` [architecture model](../../arch/rc/HAS.md#architecture-model)'s
  `RcSeq`, fed the same commands' results and captures as a model run,
  issues the same commands and ends the same way, on the passing run and
  twelve faults of each kind per program; its program builders match the
  model's, its own timeouts, hold and limits aside.

## Plan

All entries run `pcie_rc_seq`, with `PROGRAM` 4 (`SELFTEST`) unless the
row names another.

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| A passing `SELFTEST` run, start held through reset, the result holding | `smoke` | 0 | 1 |
| [Every op](../../rtl/rc_seq/MAS.md#commands), every `JUMP` condition taken and not taken, a `REQ` of every tl-seq op and two undefined ones, the 32-DW read, an MSI, Hot Reset, on random latencies | `selftest` | 1 | 2; two runs each |
| `EXPECT` mismatch and data `JUMP` turns: every `EXPECT` and data `JUMP` fault of `SELFTEST` (a wrong bit in a 1-DW read, a beat of the 32-DW read, the MSI capture's data or offset) | `expect_mismatch` | 1 | 1; 101 runs |
| `REQ` `chk`: every `chk` 1 `REQ` of `SELFTEST` answered with another status fails there, each status answered where another is expected; `chk` 0 does not fail | `status_mismatch` | 1 | 1; 73 runs |
| Wait bounds: `WAIT_LINK`, `WAIT_MSI`, `HOT_RESET` with the condition first at cycle 0, T (pass) and T + 1 (fail at the step); the Hot Reset request's length and its fall as a run fails | `wait_bounds` | 1 | 1; nine runs |
| `FAIL`, reserved ops 0x07 and 0x0F, conditions 5 and 7, the FAIL padding past the program, a `pc` above the ROM (`fail_step_o` 0x0123) | `jump_traps` | 1 | 1; seven runs |
| [`L` and the loop limit](../../rtl/rc_seq/MAS.md#commands): exactly the limit passes, one take over fails at the jump, `ALWAYS` leaves `L`, a forward conditional sets it 0 | `loop_limit` | 1 | 1; five runs |
| [Run](../../rtl/rc_seq/MAS.md#run) and [reset](../../rtl/rc_seq/MAS.md#reset): a pulse starts, `start_i` in a run is ignored, the result holds until a new rising edge, a restart drops `done_o`, reset mid-run, `start_i` held through reset | `start_reset` | 1 | 1 |
| [`ENUM`](../../rtl/rc_seq/PROGRAMS.md#enumeration) passing against the stand-in, then each of its faults: every `EXPECT` made wrong, every `chk` 1 `REQ` answered with another status, every data `JUMP` turned (`PROGRAM` 0) | `program_enum` | 1 | 1; 84 runs |
| [`MMIO`](../../rtl/rc_seq/PROGRAMS.md#mmio), likewise (`PROGRAM` 1) | `program_mmio` | 2 | 1; 198 runs |
| [`DMA_MSI`](../../rtl/rc_seq/PROGRAMS.md#dma-and-msi), likewise (`PROGRAM` 2) | `program_dma_msi` | 2 | 1; 175 runs |
| [`ERRORS`](../../rtl/rc_seq/PROGRAMS.md#errors-and-recovery): three enumerations, the polls, the refusals, the dropped MSI, the completion timeout, Hot Reset and link down, likewise (`PROGRAM` 3) | `program_errors` | 2 | 1; 141 runs |
| [Constants](../../rtl/rc_seq/PROGRAMS.md#constants) of `ERRORS`: the `HOT_RESET` timeout with a link that ignores the request, the polling limit with error messages that never come (`PROGRAM` 3) | `timeouts_errors` | 1 | 1 |
| The `WAIT_LINK` timeout of the enumeration, the link never training (`PROGRAM` 0) | `timeouts_enum` | 2 | 1; 25 000 000 cycles, most run freely |
| The `WAIT_MSI` timeout of `DMA_MSI`, no MSI (`PROGRAM` 2) | `timeouts_dma_msi` | 2 | 1 |
| `SELFTEST` with random latencies and one random fault (expect, status, wait bound) or none | `selftest_random` | 2 | 4; six runs each |
| Each M4 program over more stand-in seeds, passing, then six random faults of any kind (`PROGRAM` 0 to 3) | `program_random_enum` | 2 | 2; three stand-in seeds per entry seed |
| [`INJECT`](../../rtl/rc_seq/PROGRAMS.md#injected-errors): the stand-in answers the first traffic of each injected offset as the e2e's line makes it (Completer Abort, `BAD` twice, a stale copy setting bit 4, ERR_FATAL), then three stand-in seeds passing and six random faults each (`PROGRAM` 5) | `program_random_inject` | 1 | derived seed 0; three stand-in seeds |
| The same for `MMIO`, `DMA_MSI` and `ERRORS`: each program's random fault sample under `ci-gate` | `program_random_mmio` / `program_random_mmio_sweep`, `program_random_dma_msi` / `program_random_dma_msi_sweep`, `program_random_errors` / `program_random_errors_sweep` | 1 / 2 | derived seed 0 / derived seed 1, pinned (298234656, 1770673933, 1956525912); three stand-in seeds per entry seed |

**Level placement.** L2 widens the stand-in seeds and fault choices and
holds the two long timeouts, `timeouts_enum` (about 40 s, the simulator
running 25 000 000 cycles freely) and `timeouts_dma_msi`.

Every-fault runs ([#389](https://github.com/amichai-bd/pcie-factory/issues/389)):
the full level 1 of every IP, which `ci-gate` runs for a change to a
shared package, was over its 1200 s CPU budget, and the four every-fault
program entries were 55.5 s of it on the `ci-gate` runner (`program_enum`
5.0 s, `program_mmio` 17.2, `program_dma_msi` 15.6, `program_errors`
17.7). Level 1 keeps `program_enum`: of the image mutants of
[#396](https://github.com/amichai-bd/pcie-factory/pull/396) it alone
catches two (M21 and M28), more than any other program, and it is the
cheapest. `program_mmio`, `program_dma_msi` and `program_errors` run in
level 2, unchanged and at the same seeds. Each of those three programs
keeps a random fault sample in level 1, so every program still runs under
`ci-gate`: derived seed 0 of `program_random_mmio`,
`program_random_dma_msi` and `program_random_errors` (three stand-in
seeds, each passing, then six random faults), with their derived seed 1
pinned in the entry's level 2 `_sweep`. The merged level 0 to 2 runs are
the same runs, with the same coverage instrumentation. The model unit
tests stay in pytest (`./build ci unit`), among them the reachability of
every `EXPECT` and every `chk` 1 `REQ` of every image.

The mutants whose recorded catch moved to level 2 were rerun one at a
time against levels 0 and 1 (`--jobs 1`), at `1d585ad` for M28 to M30 and
at `1d240e3` for the others, each also against its level 2 entry, which
fails as #396 recorded:

| Mutant | First catch in levels 0 and 1 | Level 2 catch |
| --- | --- | --- |
| M8 `chk` ignored, status always checked (interpreter) | `smoke` (level 0, seed 1467269013, cycle 779); in level 1 `expect_mismatch` (seed 1878443167) first, and also `loop_limit`, `program_random_errors`, `selftest`, `start_reset`, `status_mismatch`, `wait_bounds` | `program_errors` |
| M18 `MMIO` image: M3 reads BAR0 + 0x3C | `program_random_mmio` (seed 1723958198, cycle 917) | `program_mmio` |
| M19 `ERRORS` image: R2 targets swapped | `program_random_errors` (seed 60360654, cycle 1992) | `program_errors` |
| M20 `DMA_MSI` image: D3 checks R[0] only | none: level 2 only | `program_dma_msi` (seed 898750186, cycle 49322) |
| M27 Hot Reset hold 63 | `program_random_errors` (seed 60360654, cycle 1365) | `program_errors` |
| M28 `ENUM` image: E4 `CFG_RD` DW 0 with `chk` 0 | `program_enum` (level 1, seed 1005197596, cycle 1479) | - |
| M29 `ERRORS` image: R5 poll value and mask 0x180 | none: level 2 only | `program_errors` (seed 728949876, cycle 46931) |
| M30 `MMIO` image: M5 `EXPECT` mask 0x0FFF | none: level 2 only | `program_mmio` (seed 156425704, cycle 115899) |

So the interpreter mutant M8 keeps level 0 and level 1 catches, and three
image mutants (M20, M29, M30), each a wrong field the random sample did
not draw, are caught nightly rather than under `ci-gate`.

## Functional coverage

Sampled from the steps the model finishes, which the cycle comparison
holds equal to the RTL's:

- `rc_seq_steps`: each op with its outcome (`FAIL`, reserved, `EXPECT`,
  the waits, `HOT_RESET`, `JUMP`, `DONE`, `REQ`, passing and failing
  where it can), each tl-seq op of a `REQ` and the undefined ones, each
  final status;
- `rc_seq_jumps`: each condition, bad ones included, and each outcome
  (taken forward, not taken, taken back, at the limit, bad);
- `rc_seq_run`: start held through reset, a start pulse, start during a
  run, a restart after done, reset mid-run, a beat in the take cycle, a
  32-DW read with gaps.

L0 to L2 reach every bin; the floors and every code point they leave are
in [coverage holes](#coverage-holes).

## Gaps

None: every feature of the MAS has a test.

## Coverage holes

Floors in [`level2.yaml`](../../../../src/dv/rc_seq/regress/level2.yaml),
checked by every `--coverage` run that selects level 2 (the nightly runs
levels 0 to 2): line 59.3, toggle 72.4, functional 100. Merged over levels
0 to 2 ([#380](https://github.com/amichai-bd/pcie-factory/issues/380),
re-measured in [#426](https://github.com/amichai-bd/pcie-factory/issues/426)):
line 59.22% (106 of 179 points), toggle 72.93% (4758 of 6524), functional
100% (52 of 52 bins in `rc_seq_steps`, `rc_seq_jumps` and `rc_seq_run`).
The code floors are the merged values less 0.5 points, rounded down to a
tenth ([floor margin](../../../bkm/dv.md#coverage)). Verilator keeps one
copy of the ROM per `PROGRAM` (six), each a different constant image.

The 73 line points not hit, by cause:

| Cause | Points | Where |
| --- | ---: | --- |
| Elaboration only | 66 | `pcie_rc_seq_pkg.sv`: the step encoders (`step_*`), the program builders, the two-pass wrappers `prog_errors_final` and `prog_selftest_final` and `program_of`, called only for `localparam` values (the ROM image), which Verilator folds at elaboration. |
| Hit in another `PROGRAM` copy | 5 | `pcie_rc_seq_rom.sv`: each image's copy reaches only its own arms. |
| Unreachable by construction | 2 | The state machine's `default` arm (11 of 16 state codes are used) and the lane-disable branch of the result RAM's `common_sdp_ram` (`LANES` 1). |

The 1766 toggle points not hit, by cause:

| Cause | Points | Signals |
| --- | ---: | --- |
| Hit in another `PROGRAM` copy | 715 | `pcie_rc_seq_rom.sv`: a word bit that toggles in another program's image. |
| Step fields constant over the six programs | 744 | The fetched word (`word_d`, `word_q`, `word_o` in the ROM, `word` in the top: 612 and 132), its fields as `cmd_o.addr`, `cmd_o.len`, `jump_target`, `jump_limit` and `hold_cycles`, and the reserved bits (`rsvd`, `unused_rsvd`): address, length and limit bits that no step of any program sets, since the images are fixed at elaboration ([programs](../../rtl/rc_seq/PROGRAMS.md)). |
| PC top bits | 168 | Bits 9 to 15 of `pc_i`, `rom_pc`, `ctl_*.pc`, `fail_step_o` and `res_*.fail_step`: the ROM holds 256 steps (`ROM_AW` 8) and a jump past a program's end traps (bit 8 toggles in `expect_mismatch` and `jump_traps`), so no PC reaches 512. |
| Counter top bits | 44 | `ctl_*.cnt` bits 25 to 31 and `ctl_*.loops` bits 13 to 15, and the fall of `cnt` bit 24 and `loops` bit 12: only the longest wait (`timeouts_enum`, past 2^24 cycles) and the longest loop (`timeouts_errors`, past 4096) set those bits, and their test ends there. |
| MSI capture data and offset | 93 | `msi_i` and `msi_q` data and offset bits: the MSI data and offsets the programs' captures carry, and the selftest's corruptions of them. The capture is a register copied into the result RAM, compared bit for bit with the model; more data patterns would toggle storage bits only, which was rejected as low test value. |
| Constant by construction | 2 | The result RAM's lane enable (`wr_lanes_i`, `ram_wlane`; `LANES` 1). |

How the points were sorted, so a re-measurement reproduces the counts: a
line point is keyed by file, line, column and kind, a toggle point by
file, signal bit and direction (any copy). A point not hit is "another
copy" when its key is hit anywhere in the run; the rest go by file and
signal name as the rows list them.
