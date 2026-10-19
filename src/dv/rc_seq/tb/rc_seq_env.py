"""pcie_rc_seq cocotb environment (wiki/src/dv/rc_seq/DV_PLAN.md).

Each clk_i cycle, at the falling edge, the stand-in for rc_tl
(``models/pcie_rc_seq_standin.py``) sees rc_seq's registered outputs and
drives the cycle's inputs; after the inputs settle the environment samples
every output and compares it with the reference model
(``models/pcie_rc_seq_model.py``), which runs the program the model builds
from the programs page while the RTL runs its package image. So every
cycle checks ``cmd_valid_o`` and the command, ``msi_ready_o``,
``hot_reset_req_o``, ``done_o``, ``pass_o`` and ``fail_step_o``, and a
difference in the RTL's program image shows as a mismatch at the step that
differs. Steps the model finishes are sampled into the functional
coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, Timer

from regress.funcov import Covergroup

_DV = Path(__file__).resolve().parents[1]
sys.path.append(str(_DV / "models"))
import pcie_rc_seq_model as m  # noqa: E402
import pcie_rc_seq_standin as sd  # noqa: E402

RESET_CYCLES = 8

# ------------------------------------------------------------- coverage

STEP_BINS = ["FAIL:fail", "RSV:fail", "EXPECT:ok", "EXPECT:fail", "WAIT_MSI:ok", "WAIT_MSI:fail",
             "WAIT_LINK:ok", "WAIT_LINK:fail", "HOT_RESET:ok", "HOT_RESET:fail", "JUMP:ok",
             "JUMP:fail", "DONE:pass", "REQ:ok", "REQ:fail"]
STEPS = Covergroup("rc_seq_steps")
STEPS.coverpoint("step", bins={b.replace(":", "_"): b for b in STEP_BINS})
STEPS.coverpoint("tl_op", bins={**{n: n for n in m.TL_OP_NAMES}, "ILLEGAL": "ILLEGAL"})
STEPS.coverpoint("status", bins={n: n for n in m.STATUS_NAMES})

JUMPS = Covergroup("rc_seq_jumps")
JUMPS.coverpoint("cond", bins={n: n for n in ("ALWAYS", "DATA_EQ", "DATA_NE", "STATUS_EQ",
                                              "STATUS_NE", "bad")})
JUMPS.coverpoint("outcome", bins={"taken": "taken", "not_taken": "not taken",
                                  "taken_back": "taken back", "limit": "limit", "bad": "bad"})

RUN = Covergroup("rc_seq_run")
RUN.coverpoint("event", bins={b: b for b in (
    "start_through_reset", "start_pulse", "start_during_run", "restart_after_done",
    "reset_mid_run", "beat_in_take_cycle", "multi_beat_gaps")})


def sample_event(ev: tuple) -> None:
    pc, op, outcome, detail = ev
    if op >= m.REQ:
        t = op - m.REQ
        STEPS.sample(step=f"REQ:{outcome}", tl_op=m.TL_OP_NAMES[t] if t < 10 else "ILLEGAL")
        name = detail.removeprefix("status ")
        if name in m.STATUS_NAMES:
            STEPS.sample(status=name)
        return
    name = m.OP_NAMES.get(op, "RSV")
    STEPS.sample(step=f"{name}:{outcome}")
    if op == m.JUMP:
        if detail == "bad condition":
            JUMPS.sample(cond="bad", outcome="bad")
        else:
            cond, outcome = detail.split(" ", 1)
            JUMPS.sample(cond=cond, outcome=outcome)


# -------------------------------------------------------------- packing

def pack_rsp(status: int, data: int, last: int) -> int:
    return status << 33 | data << 1 | last


def pack_msi(offset: int, data: int) -> int:
    return offset << 32 | data


def unpack_cmd(v: int) -> tuple:
    return (v >> 78 & 0xF, v >> 46 & 0xFFFF_FFFF, v >> 40 & 0x3F, v >> 36 & 0xF, v >> 32 & 0xF,
            v & 0xFFFF_FFFF)


class SeqError(AssertionError):
    pass


class Env:
    """One DUT and its model for the whole test; a fresh stand-in per run.
    The model, like the DUT, is reset only by rst_i, so it keeps the
    result registers across runs as the RTL keeps its RAM."""

    def __init__(self, dut, prog: int):
        self.dut = dut
        self.prog = prog
        self.asm = m.program(prog)
        self.rom = self.asm.rom()
        self.n = 0
        self.rst = 1
        self.start = 0
        self.model = m.RcSeqModel(self.rom)
        self.si = None
        self.trace: list[m.Outputs] = []      # outputs of the current run, per cycle
        self.fast = False                     # fast_forward through long quiet waits
        self.skipped = 0                      # cycles fast_forward skipped
        self.checked = False                  # the first edge has passed
        cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
        for name in ("rst_i", "start_i", "dl_up_i", "link_up_i", "cmd_ready_i", "rsp_valid_i",
                     "rsp_i", "msi_valid_i", "msi_i"):
            getattr(dut, name).value = 0
        dut.rst_i.value = 1

    def label(self, name: str) -> int:
        return self.asm.labels[name]

    def new_run(self, standin: sd.StandIn) -> None:
        """A new stand-in (a world just out of reset); the DUT and the
        model keep their state."""
        self.si = standin
        self.trace = []

    async def cycle(self) -> m.Outputs:
        dut = self.dut
        await FallingEdge(dut.clk_i)
        cmd_valid = int(dut.cmd_valid_o.value)
        cmd = unpack_cmd(int(dut.cmd_o.value))
        hot = int(dut.hot_reset_req_o.value)
        i = self.si.drive(self.n, self.rst, cmd_valid, cmd, hot)
        i.rst, i.start = self.rst, self.start
        dut.rst_i.value = i.rst
        dut.start_i.value = i.start
        dut.dl_up_i.value = i.dl_up
        dut.link_up_i.value = i.link_up
        dut.cmd_ready_i.value = i.cmd_ready
        dut.rsp_valid_i.value = i.rsp_valid
        dut.rsp_i.value = pack_rsp(i.rsp_status, i.rsp_data, i.rsp_last)
        dut.msi_valid_i.value = i.msi_valid
        dut.msi_i.value = pack_msi(i.msi_offset, i.msi_data)
        await ReadOnly()
        got = m.Outputs(cmd_valid=cmd_valid, cmd=cmd, msi_ready=int(dut.msi_ready_o.value),
                        hot_reset_req=hot, done=int(dut.done_o.value),
                        passed=int(dut.pass_o.value), fail_step=int(dut.fail_step_o.value))
        want = self.model.outputs(i)
        if self.checked:
            self._compare(got, want)
        if i.rsp_valid and i.cmd_ready:
            RUN.sample(event="beat_in_take_cycle")
        self.si.commit(self.n, i, got.msi_ready)
        nev = len(self.model.events)
        self.model.clock(i)
        for ev in self.model.events[nev:]:
            sample_event(ev)
        self.trace.append(got)
        self.checked = True
        self.n += 1
        return got

    def _compare(self, got: m.Outputs, want: m.Outputs) -> None:
        bad = []
        for f in ("cmd_valid", "msi_ready", "hot_reset_req", "done", "passed", "fail_step"):
            if getattr(got, f) != getattr(want, f):
                bad.append(f"{f} expected {getattr(want, f)} got {getattr(got, f)}")
        if want.cmd_valid and got.cmd_valid and got.cmd != want.cmd:
            bad.append(f"cmd expected {fmt_cmd(want.cmd)} got {fmt_cmd(got.cmd)}")
        if bad:
            raise SeqError(f"cycle {self.n} (model pc {self.model.pc}, state {self.model.state}, "
                           f"step {fmt_step(self.model.word())}): " + "; ".join(bad))

    async def fast_forward(self) -> None:
        """In a wait whose condition stays false with nothing pending at the
        stand-in, let the simulator run freely to 8 cycles before the
        timeout: the inputs hold, the outputs cannot change, and the model's
        counter advances by the cycles skipped. Then cycle by cycle again,
        so the timeout itself is compared."""
        # cnt 1 or more: the condition was seen false, and with nothing
        # pending the inputs that made it false hold.
        if (self.model.state not in ("WAIT", "HR_FALL") or self.model.cnt == 0
                or not self.si.quiet()):
            return
        left = self.model.word().data - self.model.cnt
        if left <= 64:
            return
        k = left - 8
        await Timer(8 * k - 2, unit="ns")
        self.model.cnt += k - 1
        self.n += k - 1
        self.skipped += k - 1

    async def cycles(self, k: int) -> None:
        for _ in range(k):
            await self.cycle()

    async def reset(self, standin: sd.StandIn, cycles: int = RESET_CYCLES, start: int = 0) -> None:
        """rst_i for ``cycles`` cycles with ``start_i`` at ``start``, then
        released, with a new stand-in."""
        self.new_run(standin)
        self.rst, self.start = 1, start
        await self.cycles(cycles)
        self.rst = 0

    async def wait_done(self, max_cycles: int = 100_000) -> tuple[int, int]:
        """Cycles until done_o rises; (pass_o, fail_step_o)."""
        for _ in range(max_cycles):
            o = await self.cycle()
            if o.done and not self.model.running:
                return o.passed, o.fail_step
            if self.fast:
                await self.fast_forward()
        raise SeqError(f"no done_o in {max_cycles} cycles (model pc {self.model.pc}, "
                       f"state {self.model.state})")

    async def run(self, standin: sd.StandIn, max_cycles: int = 100_000) -> tuple[int, int]:
        """A run as the regression runs it: reset with start_i held 1 through
        its release, then until done_o."""
        await self.reset(standin, start=1)
        return await self.wait_done(max_cycles)

    async def expect_run(self, standin: sd.StandIn, fail_step: int | None, what: str) -> None:
        passed, step = await self.run(standin)
        if fail_step is None:
            if not passed:
                raise SeqError(f"{what}: expected pass, failed at step {step} "
                               f"({self.model.events[-1]})")
        elif passed or step != fail_step:
            raise SeqError(f"{what}: expected fail at step {fail_step}, got "
                           f"{'pass' if passed else f'fail at step {step}'}")
        self.dut._log.info("%s: %s at cycle %d", what,
                           "pass" if passed else f"fail at step {step}", self.n)


def fmt_cmd(c: tuple) -> str:
    op, addr, n, fbe, lbe, data = c
    name = m.TL_OP_NAMES[op] if op < 10 else f"OP{op}"
    return f"{name} addr {addr:#010x} len {n} be {fbe:x}/{lbe:x} data {data:#010x}"


def fmt_step(s: m.Step) -> str:
    return f"{m.op_name(s.op)}"


standin = sd.standin
mode_of = sd.mode_of
reference_run = sd.reference_run
expect_corruptions = sd.expect_corruptions
