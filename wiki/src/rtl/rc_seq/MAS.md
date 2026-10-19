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
bundles are the `common_pkg` structs whose fields that page lists. The
shell has the ports marked "now"; the tl-seq ports arrive with the
`common_pkg` types ([#373](https://github.com/amichai-bd/pcie-factory/issues/373),
which adds them as the tl-seq page says) and the interpreter
([#376](https://github.com/amichai-bd/pcie-factory/issues/376)).

| Port | Dir | Width | Shell | Description |
| --- | --- | --- | --- | --- |
| `clk_i`, `rst_i` | in | 1, 1 | now | Clock (125 MHz) and synchronous active-high reset ([reset](#reset)). |
| `start_i` | in | 1 | now | Run start ([run](#run)). |
| `done_o`, `pass_o` | out | 1, 1 | now | Run result ([run](#run)). |
| `fail_step_o` | out | 16 | now | ROM index of the failing step. |
| `dl_up_i` | in | 1 | now | `link` `dl_up_o`: waited for by `WAIT_LINK`. |
| `link_up_i` | in | 1 | now | `link` `link_up_o`: its fall ends the first phase of `HOT_RESET`. |
| `hot_reset_req_o` | out | 1 | now | To `link` `hot_reset_req_i` ([`HOT_RESET`](#commands)). |
| `cmd_valid_o`, `cmd_ready_i`, `cmd_o` | out, in, out | 1, 1, struct | #376 | Command to `rc_tl` ([commands](../../arch/rc/TL_SEQ.md#commands)). |
| `rsp_valid_i`, `rsp_i` | in, in | 1, struct | #376 | Result beats ([results](../../arch/rc/TL_SEQ.md#results)); always taken. |
| `msi_valid_i`, `msi_ready_o`, `msi_i` | in, out, in | 1, 1, struct | #376 | MSI capture ([MSI capture](../../arch/rc/TL_SEQ.md#msi-capture)). |

## Parameters

| Name | Type | Default | Range | Effect |
| --- | --- | --- | --- | --- |
| `PROGRAM` | `int unsigned` | 1 | a program id of [the table](#programs) | The ROM image every run executes. `rc` passes its own `PROGRAM`. An id with no program stops elaboration (`COMMON_ASSERT_PARAM`, #376). |

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

Let cycle 0 be a step's first execution cycle. A wait with timeout `T`
(`data`) succeeds at the first cycle c ≤ T at which its condition holds,
and fails at cycle T + 1 if it never held; T = 0 checks one cycle.

| `op` | Name | Effect on the `rc_tl` interface and `link` | Fails when |
| --- | --- | --- | --- |
| 5'h00 | `FAIL` | None. | Always: the run ends failing at this step. Every ROM index past a program's last step reads as this word. |
| 5'h01 | `EXPECT` | None. | `(R[idx] ^ data) & mask` is not 0. |
| 5'h02 | `WAIT_MSI` | Waits for `msi_valid_i`; on success `msi_ready_o` is 1 for that one cycle (the capture is taken) and `R[0]`, `R[1]` are written. | The wait times out; nothing is taken. |
| 5'h03 | `WAIT_LINK` | Waits for `dl_up_i` 1. | The wait times out. |
| 5'h04 | `HOT_RESET` | `hot_reset_req_o` 1 from cycle 0; waits for `link_up_i` 0 (timeout `data`); then keeps `hot_reset_req_o` 1 for `addr[15:0]` more cycles and sets it 0 as the step ends. `link` then leaves Hot Reset by its own timer and retrains; `rc_tl` finishes requests with `LINK_DOWN` meanwhile. | `link_up_i` does not fall in time; `hot_reset_req_o` falls as the run ends. |
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

A new test adds a program constant and an id here (ids 5 and up), and a
DV plan row; the interpreter does not change.

## Microarchitecture

The interpreter (#376) is three blocks in `src/rtl/rc_seq/`:

| Block | Holds |
| --- | --- |
| `pcie_rc_seq_pkg` | `t_step`, the op, condition and state enums, `ROM_AW`, the program ids, the step constructors and the program constants. |
| `pcie_rc_seq_rom` | The image `PROGRAM` selects, read at `pc` with a registered output (one cycle), so the FPGA flow may map it to block memory. |
| `pcie_rc_seq` | The top: the state machine below, `pc` (16), `S` (3), `L` (16), a 32-bit wait counter, the run result, and the result registers as one `common_sdp_ram` (32 × 32 bits, [RAM family](../common/ram/common_sdp_ram.md)), written by result beats and MSI captures and read by `EXPECT` and `JUMP`. |

States: `IDLE` (no run), `FETCH` (ROM read), `EXEC` (decode; finish
`FAIL`, `DONE` and `JUMP ALWAYS`, start the rest), `READ` (result register
read for `EXPECT` and data `JUMP`), `REQ_OFFER`, `REQ_WAIT`, `WAIT`
(`WAIT_MSI`, `WAIT_LINK`), `HR_FALL`, `HR_HOLD`. The run result registers
`done_o`, `pass_o` and `fail_step_o`. The result RAM has one write port,
so a `WAIT_MSI` latches the capture in the cycle it takes it and writes
`R[0]` and `R[1]` in the next two cycles; a result beat writes in its own
cycle.

## Decisions

Rows citing OP are the captain's ([0005](../../decisions/0005-m4-grill.md#decisions));
every other row is a firstmate decision taken as the captain's delegate
([#369 decision](https://github.com/amichai-bd/pcie-factory/issues/369#issuecomment-6268649126),
D1 to D12), which the captain may override. D10 and D11 are on the
[programs page](PROGRAMS.md#decisions).

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
| Program ids 0 `ENUM`, 1 `MMIO` (the default), 2 `DMA_MSI`, 3 `ERRORS`, 4 `SELFTEST`, 5 and up for unit programs (D9) | One image per M4 program, so each regression entry selects one and a failure names its program; `MMIO` (enumeration then BAR0 traffic) is what the board build runs (goal item 9), so the default needs no override there. | One combined image (one entry for every goal item, a long run per failure); `ENUM` as the default (the board would not exercise MMIO). |
| The tl-seq ports wait for #373's `common_pkg` types (D12) | The [tl-seq page](../../arch/rc/TL_SEQ.md#signals) gives the types to the RTL that first uses them; two shells adding them in parallel would collide. | Adding the types in this shell. |

## Timing

One clock, no crossing. Outside its waits and its `REQ` round trip, a step
takes at most 4 cycles (fetch, decode, register read, decide). A `REQ`
adds the `rc_tl` latency and one cycle per result beat; a 32-DW read
takes its 32 beats at one per cycle. `fail_step_o`, `pass_o` and `done_o`
are registers.

## Targets

The [`rc` targets](../../arch/rc/HAS.md#targets) hold through the M4
programs (#377). For `rc_seq` itself: every op and failure case of
[the table](#commands) behaves as written against the stand-in (#376,
[DV plan](../../dv/rc_seq/DV_PLAN.md)); the per-step bound under
[timing](#timing); FPGA resources reported with `rc` (#379), no bound.

## Files

| File | Holds |
| --- | --- |
| [`pcie_rc_seq.sv`](../../../../src/rtl/rc_seq/pcie_rc_seq.sv) | The shell: the ports marked "now" with every output at its [reset](#reset) value, every input and `PROGRAM` folded into one `unused_inputs` net. |
| [`rc_seq.rtl.f`](../../../../src/rtl/rc_seq/rc_seq.rtl.f) | `pcie_rc_seq.sv`; [`rc.rtl.f`](../../../../src/rtl/rc/rc.rtl.f) reaches it with `-F`. |

## Open items

| Item | Owner |
| --- | --- |
| The `core` application model's side of the programs: BAR0 as memory, the DMA doorbell and the no-answer DW ([program boundary](PROGRAMS.md#boundary)), and the link-down injection during `ERRORS`. | [#377](https://github.com/amichai-bd/pcie-factory/issues/377) |
| Wait timeouts, Hot Reset hold and loop limits: the package constants and their values. The hold covers at least the cycles from `link_up_o` falling to `dl_up_o` falling, so the `WAIT_LINK` after a `HOT_RESET` cannot see the old `dl_up_o`. | [#376](https://github.com/amichai-bd/pcie-factory/issues/376) |
