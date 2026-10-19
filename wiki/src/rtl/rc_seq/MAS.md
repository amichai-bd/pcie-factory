# pcie_rc_seq MAS

`pcie_rc_seq` is the command-ROM test sequencer of [`pcie_rc`](../../arch/rc/HAS.md):
with no CPU (P2), it runs one program of steps from a ROM, asks
[`pcie_rc_tl`](../../arch/rc/TL_SEQ.md) for every configuration, memory,
header, host-memory and error-record access, checks the results with
masked expects, waits for MSIs and link-up, requests Hot Reset, and
reports done, pass and the failing step (decision record
[0005](../../decisions/0005-m4-grill.md#decisions), OP1, OP4). New tests are
new programs, not new RTL. It knows no TLP format and holds no root-port
state. It is a sub-IP of `rc` (OP1), so this page also owns its purpose,
interfaces, parameters, behavior and targets; its programs are on the
[programs page](PROGRAMS.md), its verification in the
[DV plan](../../dv/rc_seq/DV_PLAN.md).

## Position

`rc` wires `rc_seq` to `rc_tl` by the [tl-seq interface](../../arch/rc/TL_SEQ.md),
which this page implements exactly, and to `link`'s `dl_up_o`, `link_up_o`
and `hot_reset_req_i` ([downstream role](../../arch/rc/HAS.md#downstream-role)).
`start_i`, `done_o`, `pass_o` and `fail_step_o` are `rc` ports
([sequencer run](../../arch/rc/HAS.md#sequencer-run)).

## Interfaces

All ports are synchronous to `clk_i`. The tl-seq ports carry the
[tl-seq](../../arch/rc/TL_SEQ.md#signals) nets of the same names; their
bundles are the `common_pkg` structs `t_rc_cmd`, `t_rc_rsp` and `t_rc_msi`
whose fields that page lists (D12). Every output is a register or decoded
from registers, except `msi_ready_o`, which also follows `msi_valid_i` in
the same cycle ([`WAIT_MSI`](#commands)).

| Port | Dir | Width | Description |
| --- | --- | --- | --- |
| `clk_i`, `rst_i` | in | 1, 1 | Clock (125 MHz) and synchronous active-high reset ([reset](#reset)). |
| `start_i` | in | 1 | Run start ([run](#run)). |
| `done_o`, `pass_o` | out | 1, 1 | Run result ([run](#run)). |
| `fail_step_o` | out | 16 | ROM index of the failing step. |
| `dl_up_i` | in | 1 | `link` `dl_up_o`: waited for by `WAIT_LINK`. |
| `link_up_i` | in | 1 | `link` `link_up_o`: its fall ends the first phase of `HOT_RESET`. |
| `hot_reset_req_o` | out | 1 | To `link` `hot_reset_req_i` ([`HOT_RESET`](#commands)). |
| `cmd_valid_o`, `cmd_ready_i`, `cmd_o` | out, in, out | 1, 1, `t_rc_cmd` | Command to `rc_tl` ([commands](../../arch/rc/TL_SEQ.md#commands)); `cmd_o` is meaningful while `cmd_valid_o` is 1. |
| `rsp_valid_i`, `rsp_i` | in, in | 1, `t_rc_rsp` | Result beats ([results](../../arch/rc/TL_SEQ.md#results)); always taken. |
| `msi_valid_i`, `msi_ready_o`, `msi_i` | in, out, in | 1, 1, `t_rc_msi` | MSI capture ([MSI capture](../../arch/rc/TL_SEQ.md#msi-capture)). |

## Parameters

| Name | Type | Default | Range | Effect |
| --- | --- | --- | --- | --- |
| `PROGRAM` | `int unsigned` | 1 | a program id of [the table](#programs) | The ROM image every run executes. `rc` passes its own `PROGRAM`. An id with no program, or a program longer than the ROM, stops elaboration (`COMMON_ASSERT_PARAM`). |

## Behavior

### Reset

`rst_i` (at least 8 cycles) sets: no run, `done_o`, `pass_o`,
`fail_step_o`, `hot_reset_req_o`, `cmd_valid_o` and `msi_ready_o` 0. The
[result registers](#result-registers) are not cleared. Link down resets
nothing ([`rc` link down](../../arch/rc/HAS.md#link-down)): a run goes on
through the Hot Reset it requests.

### Run

A run starts as the [`rc` HAS](../../arch/rc/HAS.md#sequencer-run) says
(rising `start_i`, none in progress) with the step index `pc` 0, the last
status `S` set to `OK` and the loop count `L` 0, then executes one step at
a time, in ROM order unless a `JUMP` is taken. It ends at a `DONE` step
(pass) or at the first step that fails (fail, `fail_step_o` = that
step's `pc`); `done_o`, `pass_o` and `fail_step_o` then behave as the HAS
says. A run never ends with a command outstanding: every failure is
decided after the last result beat or outside a `REQ`. `rc_seq` relies on
`rc_tl` finishing every command (completion timeout, `LINK_DOWN`) and so
has no timeout of its own on a `REQ`.

### Result registers

32 registers `R[0]` to `R[31]` of 32 bits hold the data a step reads. A
`REQ` writes its result beat k's `rsp.data` to `R[k]`, so a `MEM_RD` of
`len` DWs fills `R[0]` to `R[len-1]` in address order and every other
command writes `R[0]` (0 for a write); a `WAIT_MSI` writes the capture's
`msi.data` to `R[0]` and its `msi.offset`, zero-extended, to `R[1]`.
Other registers keep their value; a register never written since reset
is undefined, and a program reads only what it wrote.

### Step format

A step is one 128-bit ROM word, `pcie_rc_seq_pkg::t_step`. Fields a step's
op does not use are ignored; the package's step constructors write them 0.
The struct member of `len` is `n_dw`: Quartus Lite takes a member named
`len` inside a function for the string method
([RTL BKM](../../../bkm/rtl.md#systemverilog-rules)).

| Bits | Field | Width | Use |
| --- | --- | --- | --- |
| 127 | reserved | 1 | Ignored; the constructors write 0. |
| 126:122 | `op` | 5 | Below. |
| 121:119 | `status` | 3 | A [result status](../../arch/rc/TL_SEQ.md#results) code: `REQ` with `chk`, `JUMP` on status. |
| 118 | `chk` | 1 | `REQ`: 1 fails the step unless its status equals `status`. |
| 117:115 | `cond` | 3 | `JUMP` condition, below. |
| 114:110 | `idx` | 5 | `EXPECT`, `JUMP` on data: the register `R[idx]`. |
| 109:104 | `len` | 6 | `REQ`: `cmd.len`. |
| 103:100, 99:96 | `first_be`, `last_be` | 4, 4 | `REQ`: `cmd.first_be`, `cmd.last_be`. |
| 95:64 | `addr` | 32 | `REQ`: `cmd.addr`. `JUMP`: target `[15:0]`, loop limit `[31:16]`. `HOT_RESET`: hold `[15:0]`. |
| 63:32 | `data` | 32 | `REQ`: `cmd.data`. `EXPECT`, `JUMP` on data: the value. Waits and `HOT_RESET`: the timeout in cycles. |
| 31:0 | `mask` | 32 | `EXPECT`, `JUMP` on data: the bits compared. |

### Commands

Let cycle 0 be a step's first execution cycle, the cycle after its decode
([timing](#timing)). A wait with timeout `T` (`data`) checks its condition
at cycles 0 to T: it succeeds at the first cycle c ≤ T at which the
condition holds, and otherwise fails at cycle T, so `done_o` rises at
cycle T + 1; T = 0 checks one cycle.

| `op` | Name | Effect on the `rc_tl` interface and `link` | Fails when |
| --- | --- | --- | --- |
| 5'h00 | `FAIL` | None. | Always: the run ends failing at this step. Every ROM index past a program's last step reads as this word. |
| 5'h01 | `EXPECT` | None. | `(R[idx] ^ data) & mask` is not 0. |
| 5'h02 | `WAIT_MSI` | Waits for `msi_valid_i`; on success `msi_ready_o` is 1 in that same cycle, and only then (the capture is taken), and `R[0]`, `R[1]` are written in the next two cycles. | The wait times out; nothing is taken. |
| 5'h03 | `WAIT_LINK` | Waits for `dl_up_i` 1. | The wait times out. |
| 5'h04 | `HOT_RESET` | `hot_reset_req_o` 1 from cycle 0; waits for `link_up_i` 0 (timeout `data`); if it falls at cycle c, keeps `hot_reset_req_o` 1 for `addr[15:0]` more cycles (c + 1 to c + `addr[15:0]`) and sets it 0 as the step ends. `link` then leaves Hot Reset by its own timer and retrains; `rc_tl` finishes requests with `LINK_DOWN` meanwhile. | `link_up_i` does not fall in time; `hot_reset_req_o` falls as the run ends. |
| 5'h05 | `JUMP` | None. `cond`: 0 `ALWAYS`; 1 `DATA_EQ`, 2 `DATA_NE` on `(R[idx] ^ data) & mask` being 0 or not; 3 `STATUS_EQ`, 4 `STATUS_NE` on `S` equal to `status` or not. Taken: `pc` = target. A taken conditional jump backward (target ≤ `pc`) increments `L`; a taken one forward and one not taken set `L` 0; `ALWAYS` leaves `L`. So `L` counts the takes of the current polling loop only, and the limit applies to backward jumps only. A polling loop holds exactly one conditional jump, its backward one: another conditional jump in its body would reset `L` on every pass and unbound the loop. | `cond` 5 to 7; a conditional jump that would be taken backward with `L` equal to the limit (a polling loop out of budget). |
| 5'h06 | `DONE` | None. | Never: the run ends passing. |
| 5'h07 to 5'h0F | reserved | None. | Always, as `FAIL`. |
| 5'h10 + t | `REQ` | Offers `cmd` with `op` t (the [tl-seq op](../../arch/rc/TL_SEQ.md#commands): 0 `CFG_RD` to 9 `ERR_CLR`) and `addr`, `len`, `first_be`, `last_be`, `data` as written, holding `cmd_valid_o` until `cmd_ready_i`; takes every result beat from that cycle on, writing the [result registers](#result-registers), until the beat with `rsp.last`, whose status becomes `S`. Fields go out unchecked: `rc_tl`'s field checks answer `LOCAL_UR`, and t 10 to 15 reach `rc_tl` as illegal ops. | `chk` is 1 and `S` is not `status`. |

OP4's commands map as: configuration read and write, memory read and
write: `REQ` with `CFG_RD`, `CFG_WR`, `MEM_RD`, `MEM_WR` (and the header,
host-memory and error-record ops); expect with a mask: `EXPECT` (and
`REQ` `chk` for the status); wait for MSI, link-up: `WAIT_MSI`,
`WAIT_LINK`; Hot Reset: `HOT_RESET`; jump: `JUMP`; done: `DONE`.
Retrain, Disabled and loopback have no command
([`rc` decisions](../../arch/rc/HAS.md#decisions)).

## Programs

**Format.** The ROM is `2^ROM_AW` steps (`pcie_rc_seq_pkg::ROM_AW`, 8:
256 steps); `pc` is 16 bits and indexes at or above `2^ROM_AW` read as
`FAIL`. Every program is a constant of `pcie_rc_seq_pkg`: steps in order
from index 0, built with the package's pure step-constructor functions
(one per op, plus the `CFG_*` address packer), padded with `FAIL`. A
section shared by several programs (the enumeration) is written once and
placed by the package; `JUMP` targets are derived from section lengths,
never hand-counted. The values a program checks are those of `rc` and
`core` at their default parameters; another build needs its own program.

**Selection.** `PROGRAM` picks the image at elaboration: a regression
entry sets it with `params: {PROGRAM: <id>}` on `pcie_rc_seq` or `pcie_rc`,
and a board wrapper with a parameter override. Any wrapper between the
test and `pcie_rc` (an SV e2e top of #377, a board wrapper) forwards
`PROGRAM` unchanged. The flow needs no new tool.

| Id | Name | Runs | Used by |
| --- | --- | --- | --- |
| 0 | `ENUM` | [Enumeration](PROGRAMS.md#enumeration). | #377 e2e |
| 1 | `MMIO` | Enumeration, then [MMIO](PROGRAMS.md#mmio). The default and the board build's (goal item 9). | #377, [#378](https://github.com/amichai-bd/pcie-factory/issues/378) |
| 2 | `DMA_MSI` | Enumeration, then [DMA and MSI](PROGRAMS.md#dma-and-msi). | #377 |
| 3 | `ERRORS` | [Errors and recovery](PROGRAMS.md#errors-and-recovery), re-enumerating after a Hot Reset and a link down. | #377 |
| 4 | `SELFTEST` | [Self-test](PROGRAMS.md#self-test): every op against the `rc_seq` testbench's stand-in for `rc_tl`. | #376 |
| 5 | `INJECT` | Enumeration, then [injected errors](PROGRAMS.md#injected-errors): results and record bits of the errors the e2e injects on the line. | #377 |

A new test adds a program constant and an id here (ids 6 and up), and a
DV plan row; the interpreter does not change.

## Microarchitecture

The interpreter is three blocks in `src/rtl/rc_seq/`:

| Block | Holds |
| --- | --- |
| `pcie_rc_seq_pkg` | `t_step`, the op, condition and state enums, `ROM_AW`, the program ids, the step constructors and the program constants. |
| `pcie_rc_seq_rom` | The image `PROGRAM` selects, read at `pc` with a registered output (one cycle), so the FPGA flow may map it to block memory. |
| `pcie_rc_seq` | The top: the state machine below, `pc` (16), `S` (3), `L` (16), a 32-bit wait counter, the run result, and the result registers as one `common_sdp_ram` (32 × 32 bits, [RAM family](../common/ram/common_sdp_ram.md)), written by result beats and MSI captures and read by `EXPECT` and `JUMP`. |

States (`pcie_rc_seq_pkg::t_state`): `IDLE` (no run), `FETCH` (ROM read
at `pc`), `EXEC` (decode; finish `FAIL`, the reserved ops, `DONE`,
`JUMP ALWAYS`, the status `JUMP`s and a bad condition, start the rest;
issue the result-register read of `R[idx]`), `READ` (decide `EXPECT` and
the data `JUMP`s on the register read), `REQ_OFFER`, `REQ_WAIT`, `WAIT`
(`WAIT_MSI`, `WAIT_LINK`), `MSI_R0`, `MSI_R1`, `HR_FALL`, `HR_HOLD`. `pc`,
`S`, `L`, the wait counter and the beat count are one `t_ctl` register;
`done_o`, `pass_o` and `fail_step_o` one `t_result` register. The result
RAM has one write port, so a `WAIT_MSI` latches the capture in the cycle
it takes it (`msi_ready_o` 1) and writes `R[0]` in `MSI_R0` and `R[1]` in
`MSI_R1`, the next two cycles; a result beat writes in its own cycle,
`R[k]` for its beat number k below 32. Writes and the register read never
meet: the last write of a step is in its last cycle, the read in the next
step's `EXEC`.

The outputs: `cmd_valid_o` is the state `REQ_OFFER` and `cmd_o` the ROM
word's `REQ` fields (the ROM keeps reading the same `pc`, so they hold);
`hot_reset_req_o` is the states `HR_FALL` and `HR_HOLD`; `msi_ready_o` is
the state `WAIT` on a `WAIT_MSI` step with `msi_valid_i` 1.

**Programs in the package.** Each image is a package constant built at
elaboration by pure functions: a builder appends constructor words to a
`t_prog` (the image and the next index), which starts all FAIL. A forward
`JUMP` target (the `ERRORS` pass selection, the `SELFTEST` padding) comes
from a first build of the same function, whose step count does not depend
on the target values; both builds run inside one function
(`prog_errors_final`, `prog_selftest_final`). `program_of(PROGRAM)`
selects the `t_prog` in `pcie_rc_seq_rom`, which takes the image and the
length by part-select of the packed `{rom, n}` and checks that layout with
a parameter check. No constant expression selects a member of a struct
localparam: Quartus Lite rejects that (error 10742, "hierarchical
identifier"; [#379](https://github.com/amichai-bd/pcie-factory/issues/379)).

## Decisions

Rows citing OP are the captain's ([0005](../../decisions/0005-m4-grill.md#decisions));
rows citing D1 to D12 are firstmate decisions taken as the captain's
delegate ([#369 decision](https://github.com/amichai-bd/pcie-factory/issues/369#issuecomment-6268649126)),
which the captain may override; rows citing #376 are implementation
choices of the interpreter
([#376](https://github.com/amichai-bd/pcie-factory/issues/376)) where this
page was silent. D10 and D11 are on the [programs page](PROGRAMS.md#decisions).

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Command-ROM interpreter, programs in the ROM (OP4) | New tests are new programs, not new RTL. | A hard-wired state machine per test. |
| Fixed 128-bit step, one step per ROM index (D1) | `fail_step_o` names a step directly; decode is a field select; 256 × 128 bits fits a few FPGA memory blocks. | Variable-length steps in 32-bit words (an index that is not a step, a multi-cycle fetch). |
| One `REQ` op carrying the tl-seq op, fields passed verbatim (D2) | One encoding for the ten tl-seq ops; `rc_tl` alone owns the field checks, and programs can test them. | A sequencer op per tl-seq op (two encodings to keep aligned). |
| Result registers plus `EXPECT` with a mask, status checked in `REQ` (D3) | One expect form for every read, a 32-DW read included, and for MSI captures. | Expected value and mask inside each read step (a 224-bit step; no multi-DW check). |
| `JUMP` with data and status conditions and a loop limit on backward takes (D4) | Polling (error messages, link down) and re-enumeration need a branch; the limit makes every polling loop end with a failing step instead of hanging a board; counting backward takes only keeps a forward skip from eating a later loop's budget. | Unconditional jump only (no polling); call and return (a stack for no M4 need); one count over every taken jump (a skip before a loop cuts its budget). |
| One command at a time (D5) | Programs check DW by DW; throughput is information only ([`rc` targets](../../arch/rc/HAS.md#targets)). | Pipelined commands (result tracking in `rc_seq`). |
| Programs as package constants selected by `PROGRAM` (D6) | No system task or init file in RTL ([RTL BKM](../../../bkm/rtl.md#assertions)), Verilator and Quartus read the same source, and regression `params` select it with no new tool. | `$readmemh` of a hex file (a forbidden system task); a program assembler tool (not needed while programs stay short). |
| `FAIL` is op 0 and pads the ROM (D7) | Running off a program or jumping outside it fails instead of passing. | Op 0 as `DONE`. |
| A 256-step ROM, `ROM_AW` 8 a package constant; `pc` and `fail_step_o` 16 bits (D8) | Every M4 program fits (about 60 to 140 steps) with room for growth; one constant to raise; 256 × 128 bits is a few FPGA memory blocks. | A depth per program (a ROM leaf per image size); a depth that follows `fail_step_o` (64 Ki steps of mostly padding). |
| Program ids 0 `ENUM`, 1 `MMIO` (the default), 2 `DMA_MSI`, 3 `ERRORS`, 4 `SELFTEST`, 5 and up for unit programs (D9); 5 is `INJECT`, the e2e's injected-error program, which the programs page reserved for #377 (D11) | One image per M4 program, so each regression entry selects one and a failure names its program; `MMIO` (enumeration then BAR0 traffic) is what the board build runs (goal item 9), so the default needs no override there. | One combined image (one entry for every goal item, a long run per failure); `ENUM` as the default (the board would not exercise MMIO). |
| The tl-seq ports wait for the interpreter (D12); their `common_pkg` types came with the [`rc_tl` shell](../rc_tl/MAS.md#interfaces) | Two shells adding the types in parallel would collide, so only the `rc_tl` shell, whose MAS ports use them, added them. | Adding the types in this shell. |
| `msi_ready_o` follows `msi_valid_i` in the waiting cycle (#376) | The capture is taken in the cycle it is seen, so a wait's cycle count is exact and needs no extra cycle. | A registered ready (one cycle later, or a take the cycle after `msi_valid_i` fell). |
| A `WAIT` or `HOT_RESET` timeout fails at cycle T, `done_o` at T + 1 (#376) | The counter stops at T, never at T + 1, so `T` may be any 32-bit value with no overflow. | Failing at T + 1 (a 33-bit compare). |
| Images built by package functions, forward targets by a first build (#376) | One source for Verilator and Quartus, targets derived from section lengths (Programs), no tool. | Hand-counted targets; a generated include file. |
| Program constants: `WAIT_LINK` 25 000 000 cycles (200 ms), `WAIT_MSI` 1 250 000 (10 ms), `HOT_RESET` 125 000 (1 ms) then a hold of 64, polling loops 4096 takes (#376; [programs page](PROGRAMS.md#constants)) | `WAIT_LINK` covers link training on a board at `TIMEOUT_DIV` 1 with retries; the hold outlasts `link_up_o` falling to `dl_up_o` falling (a few cycles in `link`), so the `WAIT_LINK` after a Hot Reset cannot see the old `dl_up_o`; every M4 poll ends in far fewer takes. | One timeout for every wait (200 ms for an MSI hides a lost one for long on a board); a hold of 0 (the `WAIT_LINK` could pass on the old `dl_up_o`). |

## Timing

One clock, no crossing. Cycle by cycle (n is the cycle of the event; the
outputs named change at the edge that ends it):

| Event | Cycles |
| --- | --- |
| Run start | A rising `start_i` at cycle s in `IDLE`: `FETCH` of step 0 at s + 1, when `done_o`, `pass_o` and `fail_step_o` are 0. |
| Every step | `FETCH` then `EXEC`, one cycle each. `FAIL`, the reserved ops, `DONE`, `JUMP ALWAYS`, the status `JUMP`s and a bad condition end in `EXEC`: 2 cycles. `EXPECT` and the data `JUMP`s end in `READ`: 3 cycles. |
| Cycle 0 | The cycle after `EXEC`: the first `REQ_OFFER`, `WAIT` or `HR_FALL` cycle. |
| `REQ` | `cmd_valid_o` from cycle 0 to the take (`cmd_ready_i` 1); beats are taken from the take cycle on, one per cycle at most; the step ends in the cycle of the beat with `rsp.last`. A step after a `REQ` has its cycle 0 three cycles after that last beat. |
| `WAIT_LINK` | Ends in the first cycle c ≤ T with `dl_up_i` 1, or fails at T. |
| `WAIT_MSI` | Takes the capture in the first cycle c ≤ T with `msi_valid_i` 1 (`msi_ready_o` 1 in c), writes `R[0]` in c + 1 and `R[1]` in c + 2, which ends the step; or fails at T. |
| `HOT_RESET` | `hot_reset_req_o` 1 from cycle 0; `link_up_i` 0 first seen at c ≤ T: the step ends at c + `addr[15:0]` and `hot_reset_req_o` is 0 from the next cycle (`FETCH`); not seen by T: fails at T, `hot_reset_req_o` 0 with `done_o`. |
| Next step | `FETCH` in the cycle after a step ends. |
| Run end | A step ending the run at cycle e: `done_o`, `pass_o`, `fail_step_o` from e + 1. |

So a step takes at most 3 cycles outside its waits and its `REQ` round
trip, and a 32-DW read takes its 32 beats at one per cycle at most.
`done_o`, `pass_o` and `fail_step_o` are registers; the ROM read is
registered and the result-register read is the RAM's registered read.

## Targets

The [`rc` targets](../../arch/rc/HAS.md#targets) hold through the M4
programs (#377). For `rc_seq` itself: every op and failure case of
[the table](#commands) behaves as written against the stand-in (#376,
[DV plan](../../dv/rc_seq/DV_PLAN.md)); the per-step bound under
[timing](#timing); FPGA resources reported with `rc` (#379), no bound.

## Files

| File | Holds |
| --- | --- |
| [`pcie_rc_seq_pkg.sv`](../../../../src/rtl/rc_seq/pcie_rc_seq_pkg.sv) | The step format, the op, condition and state types, the control and result structs, the constants, the step constructors, the program builders and the images. |
| [`pcie_rc_seq_rom.sv`](../../../../src/rtl/rc_seq/pcie_rc_seq_rom.sv) | The ROM: the image `PROGRAM` selects, a registered read at `pc`, FAIL at and above `ROM_STEPS`. |
| [`pcie_rc_seq.sv`](../../../../src/rtl/rc_seq/pcie_rc_seq.sv) | The top: the state machine, the run result, the result RAM, the outputs and the interface assertions. |
| [`rc_seq.rtl.f`](../../../../src/rtl/rc_seq/rc_seq.rtl.f) | The vocabulary, `common_pkg`, `common_sdp_ram`, then the three files; [`rc.rtl.f`](../../../../src/rtl/rc/rc.rtl.f) reaches it with `-F`. |

## Open items

| Item | Owner |
| --- | --- |
| `ERRORS` on hardware: at `CPL_TIMEOUT_DIV` 1 (about 16.8 ms) `rc_tl`'s completion timeout ends R12's outstanding read before a link down reaches Detect (the 24 ms Recovery timeout), so R12 ends `TIMEOUT` and the run fails ([programs boundary](PROGRAMS.md#boundary)). | [#409](https://github.com/amichai-bd/pcie-factory/issues/409) |
| Quartus Lite on the images: each is a 32 768-bit packed constant built by package functions, which Verilator accepts; the FPGA flow of `rc` first runs them through Quartus, and maps the ROM to block memory or reports why not. | [#379](https://github.com/amichai-bd/pcie-factory/issues/379) |
