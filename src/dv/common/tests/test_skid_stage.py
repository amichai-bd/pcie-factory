"""common_skid_stage tests (wiki/src/dv/common/common_skid_stage.md).

Inputs change at falling clk_i edges. In every cycle, once the inputs
settle, every output is compared with models/common_skid_stage_model.py:
in_ready_o, out_valid_o, out_take_o and out_new_o always, out_data_o while
out_valid_o is high. A scoreboard also checks that the beats leave in the
order taken, none lost or repeated. A gap while downstream takes every
beat, a beat that changes or leaves while held, a skid beat taken out of
order, a ready that ignores the skid or a wrong new flag fails at that
cycle. The block's own assertions (a held beat is stable, the skid holds
a beat only behind out) stop the run if they ever fire.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from models.common_skid_stage_model import SkidStageModel
from regress.funcov import Covergroup

CLK_NS = 10

# Functional coverage (DV plan, Strategy): the beats held before each edge
# against what happens at it, and resets with beats held.
COV = Covergroup("common_skid_stage")
COV.coverpoint("held", bins=["none", "out", "out_skid"])
COV.coverpoint("op", bins=["in", "out", "in_out", "stall", "idle"])
COV.coverpoint("reset", bins=["held", "empty"])
COV.cross("held_x_op", "held", "op",
          unreachable=[("none", "out"), ("none", "in_out"), ("none", "stall"),
                       ("out_skid", "in"), ("out_skid", "in_out")])


class Stage:
    """Drives one stage, keeps the model and checks every output."""

    def __init__(self, dut):
        self.dut = dut
        self.width = int(dut.WIDTH.value)
        self.model = SkidStageModel()
        self.cycle = 0
        self.sent = []           # beats taken upstream, in order
        self.got = []            # beats taken downstream, in order
        self.skid_edges = 0      # edges with two beats held
        self.stream_edges = 0    # edges with a beat in and a beat out

    async def start(self):
        d = self.dut
        d.in_valid_i.value = 0
        d.in_data_i.value = 0
        d.out_ready_i.value = 0
        cocotb.start_soon(Clock(d.clk_i, CLK_NS, unit="ns").start())
        await FallingEdge(d.clk_i)
        await self.reset()

    async def reset(self):
        d = self.dut
        COV.sample(reset="held" if self.model.out_valid else "empty")
        d.in_valid_i.value = 0
        d.out_ready_i.value = 0
        d.rst_i.value = 1
        await RisingEdge(d.clk_i)
        self.model.reset()
        # Beats held at the reset are dropped; the scoreboard restarts.
        self.sent = self.got[:]
        await FallingEdge(d.clk_i)
        d.rst_i.value = 0

    def _check(self, out_ready: bool, what: str) -> None:
        d, m = self.dut, self.model
        got = (int(d.in_ready_o.value), int(d.out_valid_o.value), int(d.out_take_o.value),
               int(d.out_new_o.value))
        want = (int(m.in_ready), int(m.out_valid), int(m.out_take(out_ready)), int(m.out_new))
        if got != want:
            raise AssertionError(
                f"cycle {self.cycle} {what}: (in_ready, out_valid, out_take, out_new) "
                f"expected {want} got {got}")
        if m.out_valid and int(d.out_data_o.value) != m.out_data:
            raise AssertionError(f"cycle {self.cycle} {what}: out_data expected "
                                 f"{m.out_data:#x} got {int(d.out_data_o.value):#x}")

    async def step(self, in_valid: bool, data: int, out_ready: bool) -> None:
        """Drive at the falling edge, check the settled outputs, take the
        rising edge."""
        d, m = self.dut, self.model
        held = ("none", "out", "out_skid")[len(m.beats)]
        accept = in_valid and m.in_ready
        take = m.out_take(out_ready)
        op = ("in_out" if accept and take else "in" if accept else "out" if take
              else "stall" if m.out_valid else "idle")
        COV.sample(held=held, op=op)
        d.in_valid_i.value = int(in_valid)
        d.in_data_i.value = data
        d.out_ready_i.value = int(out_ready)
        await ReadOnly()
        self._check(out_ready, op)
        await RisingEdge(d.clk_i)
        if accept:
            self.sent.append(data)
        self.skid_edges += held == "out_skid"
        self.stream_edges += op == "in_out"
        t = m.edge(in_valid, data, out_ready)
        if t is not None:
            self.got.append(t)
            n = len(self.got)
            if self.got != self.sent[:n]:
                raise AssertionError(f"cycle {self.cycle}: beat {n - 1} out of order: "
                                     f"got {t:#x}, expected {self.sent[n - 1]:#x}")
        self.cycle += 1
        await FallingEdge(d.clk_i)

    def word(self, i: int) -> int:
        return random.Random(i * 7919 + 3).getrandbits(self.width)


@cocotb.test()
async def contract(dut):
    """Directed: a stream with no gap while downstream takes every beat, a
    one-cycle stall that fills the skid and drops ready, a long stall that
    holds the beat, drain, and a reset with both beats held."""
    s = Stage(dut)
    await s.start()
    # Stream: ready every cycle; the stage is never full.
    for i in range(8):
        await s.step(True, s.word(i), True)
        assert s.model.in_ready
    # One stall: the skid fills, ready drops, then both leave in order.
    await s.step(True, s.word(8), False)
    assert not s.model.in_ready
    await s.step(True, s.word(9), True)        # refused: not ready
    await s.step(True, s.word(10), True)
    # A long stall: the offered beat stays, new only in its first cycle.
    for _ in range(5):
        await s.step(True, s.word(11), False)
    # Drain.
    while s.model.out_valid:
        await s.step(False, 0, True)
    assert len(s.got) == len(s.sent) and len(s.got) >= 10
    # Reset with both beats held, then one beat through again.
    await s.step(True, s.word(20), False)
    await s.step(True, s.word(21), False)
    assert len(s.model.beats) == 2
    await s.reset()
    await s.step(True, s.word(22), True)
    await s.step(False, 0, True)
    await s.step(False, 0, True)
    assert s.got[-1] == s.word(22) and s.skid_edges > 0 and s.stream_edges >= 7


async def random_run(dut, cycles: int) -> None:
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    s = Stage(dut)
    await s.start()
    # Phases bias towards streaming, stalling downstream or starving it.
    phase = 0
    for c in range(cycles):
        if c % 100 == 0:
            phase = rng.randrange(3)
        p_in, p_out = ((0.9, 0.9), (0.9, 0.3), (0.3, 0.9))[phase]
        await s.step(rng.random() < p_in, rng.getrandbits(s.width), rng.random() < p_out)
        if rng.random() < 0.002:
            await s.reset()
    while s.model.out_valid:
        await s.step(False, 0, True)
    # Vacuity guard: beats went through, the skid was used, and beats
    # streamed in and out at one edge.
    assert len(s.got) > cycles // 4, f"only {len(s.got)} beats out"
    assert s.skid_edges > cycles // 50, f"only {s.skid_edges} edges with the skid full"
    assert s.stream_edges > cycles // 10, f"only {s.stream_edges} streaming edges"


@cocotb.test()
async def random_ops(dut):
    """Random legal traffic against the model."""
    await random_run(dut, 3000)
