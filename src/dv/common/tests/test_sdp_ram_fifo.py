"""common_sdp_ram_fifo tests (wiki/src/dv/common/ram/common_sdp_ram_fifo.md).

Inputs change at falling clk_i edges; after every rising edge every output
is compared with models/common_sdp_ram_fifo_model.py: head_valid_o,
empty_o, full_o and ram_used_o always, head_o while the head is valid. A
fall-through that lands one edge early or late, a pop that loses or
repeats an entry, a wrong capacity or a flag off by one fails at that
cycle. Stimulus keeps to the contract (no push while full, no pop with no
head), so the block's own assertions stay quiet.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from models.common_sdp_ram_fifo_model import FifoModel
from regress.funcov import Covergroup

CLK_NS = 10

# Functional coverage (DV plan, Strategy): the operation at each edge
# against the fill before it, and resets with entries held.
COV = Covergroup("common_sdp_ram_fifo")
COV.coverpoint("op", bins=["push", "pop", "push_pop", "idle"])
COV.coverpoint("fill", bins=["empty", "ram_only", "head_only", "some", "full"])
COV.coverpoint("reset", bins=["held", "empty"])
COV.cross("op_x_fill", "op", "fill",
          unreachable=[("pop", "empty"), ("push_pop", "empty"), ("pop", "ram_only"),
                       ("push_pop", "ram_only"), ("push", "full"), ("push_pop", "full")])


class Fifo:
    """Drives one queue, keeps the model and checks every output."""

    def __init__(self, dut):
        self.dut = dut
        self.width = int(dut.WIDTH.value)
        self.model = FifoModel(int(dut.ADDR_W.value))
        self.cap = self.model.cap
        self.cycle = 0
        self.popped = 0
        self.full_edges = 0
        self.both_edges = 0

    async def start(self):
        d = self.dut
        d.push_i.value = 0
        d.push_data_i.value = 0
        d.pop_i.value = 0
        d.rst_i.value = 1
        cocotb.start_soon(Clock(d.clk_i, CLK_NS, unit="ns").start())
        await FallingEdge(d.clk_i)
        await self.reset()

    async def reset(self):
        d = self.dut
        COV.sample(reset="empty" if self.model.empty else "held")
        d.push_i.value = 0
        d.pop_i.value = 0
        d.rst_i.value = 1
        await RisingEdge(d.clk_i)
        self.model.reset()
        await ReadOnly()
        self._check("reset")
        await FallingEdge(d.clk_i)
        d.rst_i.value = 0

    def _fill(self) -> str:
        m = self.model
        if m.empty:
            return "empty"
        if m.full:
            return "full"
        if not m.head_valid:
            return "ram_only"
        return "head_only" if m.ram_used == 0 else "some"

    def _check(self, what: str) -> None:
        d, m = self.dut, self.model
        got = (int(d.head_valid_o.value), int(d.empty_o.value), int(d.full_o.value),
               int(d.ram_used_o.value))
        want = (int(m.head_valid), int(m.empty), int(m.full), m.ram_used)
        if got != want:
            raise AssertionError(
                f"cycle {self.cycle} after {what}: (head_valid, empty, full, ram_used) "
                f"expected {want} got {got}")
        if m.head_valid and int(d.head_o.value) != m.head:
            raise AssertionError(f"cycle {self.cycle} after {what}: head expected "
                                 f"{m.head:#x} got {int(d.head_o.value):#x}")

    async def step(self, push: bool, data: int, pop: bool) -> None:
        """Drive at the falling edge, take the rising edge, check outputs."""
        d, m = self.dut, self.model
        push = push and not m.full
        pop = pop and m.head_valid
        op = ("push_pop" if push and pop else "push" if push else "pop" if pop else "idle")
        COV.sample(op=op, fill=self._fill())
        self.full_edges += m.full
        self.both_edges += push and pop
        self.popped += pop
        d.push_i.value = int(push)
        d.push_data_i.value = data
        d.pop_i.value = int(pop)
        await RisingEdge(d.clk_i)
        m.edge(push, data, pop)
        await ReadOnly()
        self._check(op)
        self.cycle += 1
        await FallingEdge(d.clk_i)

    def word(self, i: int) -> int:
        return random.Random(i * 7919 + 1).getrandbits(self.width)


@cocotb.test()
async def contract(dut):
    """Directed: two-edge fall-through, fill to capacity, pop and refill
    at one edge, push and pop together at steady state, drain, reset with
    entries held."""
    f = Fifo(dut)
    await f.start()
    # One push into the empty queue: head two edges later.
    await f.step(True, f.word(0), False)
    assert not f.model.head_valid
    await f.step(False, 0, False)
    assert f.model.head_valid
    # Fill: the RAM plus the head, then full.
    n = 1
    while not f.model.full:
        await f.step(True, f.word(n), False)
        n += 1
    assert n == f.cap + 1, f"capacity {n}, expected {f.cap + 1}"
    await f.step(True, f.word(n), False)   # refused by step: held full
    # Pop every cycle while pushing: the head refills at each pop.
    for i in range(2 * f.cap):
        await f.step(True, f.word(n + i), True)
    # Drain with a pop every cycle.
    while not f.model.empty:
        await f.step(False, 0, True)
    # Reset with entries in the RAM and the head.
    for i in range(3):
        await f.step(True, f.word(100 + i), False)
    await f.step(False, 0, False)
    await f.reset()
    await f.step(True, f.word(200), False)
    await f.step(False, 0, False)
    await f.step(False, 0, True)
    assert f.popped >= 3 * f.cap and f.both_edges >= f.cap and f.full_edges >= 1


async def random_run(dut, cycles: int) -> None:
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    f = Fifo(dut)
    await f.start()
    # Phases of push and pop bias, so the queue fills, drains and idles.
    phase = 0
    for c in range(cycles):
        if c % 200 == 0:
            phase = rng.randrange(3)
        p_push, p_pop = ((0.8, 0.3), (0.3, 0.8), (0.6, 0.6))[phase]
        await f.step(rng.random() < p_push, rng.getrandbits(f.width), rng.random() < p_pop)
        if rng.random() < 0.002:
            await f.reset()
    # Vacuity guard: entries went through, the queue was full and pushed
    # and popped at one edge.
    assert f.popped > cycles // 4, f"only {f.popped} pops"
    assert f.full_edges > 0, "never full"
    assert f.both_edges > cycles // 20, f"only {f.both_edges} push-and-pop edges"


@cocotb.test()
async def random_ops(dut):
    """Random legal traffic against the model."""
    await random_run(dut, 3000)
