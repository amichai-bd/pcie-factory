# pcie_rc_seq DV plan

The interpreter and its programs
([#376](https://github.com/amichai-bd/pcie-factory/issues/376)) are
verified by this testbench against a stand-in for `rc_tl` that conforms
to the [tl-seq interface](../../arch/rc/TL_SEQ.md). The programs against
the real `rc_tl` and `core` are the `rc` e2e's
([#377](https://github.com/amichai-bd/pcie-factory/issues/377)).

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
| [`MMIO`](../../rtl/rc_seq/PROGRAMS.md#mmio), likewise (`PROGRAM` 1) | `program_mmio` | 1 | 1; 198 runs |
| [`DMA_MSI`](../../rtl/rc_seq/PROGRAMS.md#dma-and-msi), likewise (`PROGRAM` 2) | `program_dma_msi` | 1 | 1; 175 runs |
| [`ERRORS`](../../rtl/rc_seq/PROGRAMS.md#errors-and-recovery): three enumerations, the polls, the refusals, the dropped MSI, the completion timeout, Hot Reset and link down, likewise (`PROGRAM` 3) | `program_errors` | 1 | 1; 141 runs |
| [Constants](../../rtl/rc_seq/PROGRAMS.md#constants) of `ERRORS`: the `HOT_RESET` timeout with a link that ignores the request, the polling limit with error messages that never come (`PROGRAM` 3) | `timeouts_errors` | 1 | 1 |
| The `WAIT_LINK` timeout of the enumeration, the link never training (`PROGRAM` 0) | `timeouts_enum` | 2 | 1; 25 000 000 cycles, most run freely |
| The `WAIT_MSI` timeout of `DMA_MSI`, no MSI (`PROGRAM` 2) | `timeouts_dma_msi` | 2 | 1 |
| `SELFTEST` with random latencies and one random fault (expect, status, wait bound) or none | `selftest_random` | 2 | 4; six runs each |
| Each M4 program over more stand-in seeds, passing, then six random faults of any kind (`PROGRAM` 0 to 3) | `program_random_enum`, `program_random_mmio`, `program_random_dma_msi`, `program_random_errors` | 2 | 2 each; three stand-in seeds per entry seed |

**Level placement.** Every L1 entry takes at most about 11 s of CPU, all
of the programs' faults included (L1 46.6 s in all).
L2 widens the stand-in seeds and fault choices and holds the two long
timeouts, `timeouts_enum` (about 40 s, the simulator running 25 000 000
cycles freely) and `timeouts_dma_msi`.

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

L0 to L2 reach every bin. The package's program builders run at
elaboration only, so line coverage counts them as never executed; the
state machine's `default` arm is unreachable (11 of 16 state codes are
used).

## Gaps

| Feature | Owning issue |
| --- | --- |
| The M4 programs against the real `rc_tl` and `core` | [#377](https://github.com/amichai-bd/pcie-factory/issues/377) |
| Coverage floors | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |
