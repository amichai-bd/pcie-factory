"""common_cdc_rst tests (wiki/src/dv/common/cdc/common_cdc_rst.md).

The test drives src_rst_i at times that never coincide with a rising
dst_clk_i edge (source changes on odd picoseconds, destination edges on
even ones), records every change of dst_rst_o and every rising dst_clk_i
edge, and compares the changes with the reference model
(models/common_cdc_rst_model.py). Each assertion is also checked in the
same time step it is driven, in the read-only phase, with no clock edge.
"""

import math
import random

import cocotb
from cocotb.triggers import ReadOnly, Timer, ValueChange

from models.common_cdc_rst_model import expected_output, release_edge
from regress.funcov import Covergroup
from tb.cdc_env import CLOCK_BINS, EdgeLog, clock_ratio, now, random_periods, start_clock

# Functional coverage (DV plan, Strategy): per pulse, the clock ratio class,
# its width against one destination period, and whether the next pulse
# comes while the release is still under way. A pulse launched on the
# source grid lasts at least one source cycle, which is longer than a
# destination period when the destination is faster, so those two crosses
# are unreachable: left out, and a sample of one fails the test.
COV = Covergroup("common_cdc_rst")
COV.coverpoint("clocks", bins=CLOCK_BINS)
COV.coverpoint("width", bins={"under_period": lambda w: w < 1.0,
                              "over_period": lambda w: w >= 1.0})
COV.coverpoint("next", bins=["mid_release", "released"])
COV.cross("clocks_x_width", "clocks", "width",
          unreachable=[("dst_fast", "under_period"), ("dst_fast_4x", "under_period")])
COV.cross("width_x_next", "width", "next")


class Env:
    """Driver, monitors and checker of one test."""

    def __init__(self, dut):
        self.dut = dut
        self.stages = int(dut.STAGES.value)
        self.edges = EdgeLog(dut.dst_clk_i)
        self.src: list[tuple[int, int]] = []   # (time, value) driven on src_rst_i
        self.out: list[tuple[int, int]] = []   # (time, value) changes of dst_rst_o
        dut.src_rst_i.value = 0
        cocotb.start_soon(self._monitor())

    async def _monitor(self):
        while True:
            await ValueChange(self.dut.dst_rst_o)
            self.out.append((now(), int(self.dut.dst_rst_o.value)))

    async def drive(self, value: int) -> None:
        """Drive src_rst_i; on assertion, dst_rst_o must be high in this
        same time step (no dst_clk_i edge happens at a source change)."""
        dut = self.dut
        dut.src_rst_i.value = value
        self.src.append((now(), value))
        await ReadOnly()
        if value:
            got = int(dut.dst_rst_o.value)
            assert got == 1, (f"assertion {len(self.src) // 2} at {now()} ps: dst_rst_o "
                              f"expected 1 in the same time step, got {got}")

    async def pulse(self, width: int, gap: int) -> None:
        await self.drive(1)
        await Timer(width, "ps")
        await self.drive(0)
        await Timer(gap, "ps")

    def check(self, ratio: float | None = None, dst_period: int | None = None) -> None:
        """Compare dst_rst_o with the model and sample coverage per pulse."""
        expected = expected_output(self.src, self.edges.times, self.stages)
        if self.out != expected:
            i = next((i for i, (a, b) in enumerate(zip(self.out, expected)) if a != b),
                     min(len(self.out), len(expected)))
            near = [t for t, _ in self.src if i < len(expected) and t <= expected[i][0]][-2:]
            raise AssertionError(
                f"dst_rst_o change {i}: expected "
                f"{expected[i] if i < len(expected) else 'none'}, got "
                f"{self.out[i] if i < len(self.out) else 'none'} as (ps, value); "
                f"{len(expected)} expected, {len(self.out)} seen; STAGES {self.stages}; "
                f"src_rst_i changes just before: {near}")
        if dst_period is None:
            return
        rises = [t for t, v in self.src if v]
        falls = [t for t, v in self.src if not v]
        for i, (r, f) in enumerate(zip(rises, falls)):
            rel = release_edge(f, self.edges.times, self.stages)
            mid = i + 1 < len(rises) and (rel is None or rises[i + 1] < rel)
            values = {"width": (f - r) / dst_period,
                      "next": "mid_release" if mid else "released"}
            if ratio is not None:  # no source clock in the glitch test
                values["clocks"] = ratio
            COV.sample(**values)

    def check_release_within(self, cycles: int) -> None:
        """Each release ends within ``cycles`` dst_clk_i edges after the
        fall that starts it (the HAS contract the default meets)."""
        falls = [t for t, v in self.src if not v]
        for t, v in self.out:
            if v:
                continue
            f = max(x for x in falls if x < t)
            n = sum(f < e <= t for e in self.edges.times)
            assert n <= cycles, (f"release at {t} ps, {n} dst_clk_i edges after the fall at "
                                 f"{f} ps; the contract allows {cycles}")


async def random_pulses(env, rng, src_period, dst_period, count):
    """``count`` pulses launched on a source-clock grid: widths of one
    source cycle up to past the release time, gaps from one source cycle
    (re-asserting mid-release) to past the release time."""
    stages = env.stages
    release = math.ceil((stages + 1) * dst_period / src_period)  # src cycles
    env.dut._log.info("src period %d ps, dst period %d ps, STAGES %d, %d pulses",
                      src_period, dst_period, stages, count)
    for _ in range(count):
        width = 1 if rng.random() < 0.3 else rng.randrange(1, release + 2)
        gap = (rng.randrange(1, release + 1) if rng.random() < 0.5
               else release + rng.randrange(3))
        await env.pulse(width * src_period, gap * src_period)
    await Timer((stages + 2) * dst_period, "ps")


async def start(dut, dst_period, src_period=None):
    """The environment, the destination clock at a random even phase, and
    the source grid at an odd offset from it."""
    env = Env(dut)
    start_clock(dut.dst_clk_i, dst_period, random.randrange(0, dst_period, 2))
    await Timer(2 * random.randrange(dst_period // 2) + 1, "ps")
    return env


@cocotb.test()
async def smoke(dut):
    """L0: assertion and hold with the clock stopped, release once it
    starts, then the HAS reset contract: a 125 MHz destination clock that
    toggles in reset, a source 500 ppm slower, resets of 8 source cycles,
    release within 3 destination cycles."""
    env = Env(dut)
    await Timer(1_001, "ps")
    await env.drive(1)
    await Timer(20_000, "ps")
    await env.drive(0)
    await Timer(50_000, "ps")
    assert env.out == [(1_001, 1)], f"dst_rst_o changed with no dst_clk_i edge: {env.out}"
    src, dst = 8_004, 8_000
    start_clock(dut.dst_clk_i, dst, 2_000)
    await Timer(3 * dst + 1, "ps")
    for width in (8, 8, 9, 12):
        await env.pulse(width * src, 6 * src)
    env.check(clock_ratio(src, dst), dst)
    env.check_release_within(3)


@cocotb.test()
async def glitch(dut):
    """L0: pulses shorter than one destination period, down to 2 ps, at
    random offsets from the destination edges, some back to back within
    the release; each asserts at once and releases STAGES edges after its
    fall."""
    dst = 10_000
    env = await start(dut, dst)
    for _ in range(60):
        width = 2 * random.randrange(1, dst // 2)
        gap = 2 * random.randrange(1, 2 * dst)
        await env.pulse(width, gap)
    await Timer((env.stages + 2) * dst, "ps")
    env.check(None, dst)


@cocotb.test()
async def random_ratio(dut):
    """L1: random pulses at a random clock ratio up to 8:1 either way."""
    src, dst = random_periods(random, 8.0)
    env = await start(dut, dst)
    await random_pulses(env, random, src, dst, 100)
    env.check(clock_ratio(src, dst), dst)


@cocotb.test()
async def long_ratio(dut):
    """L2: many random pulses at a random clock ratio up to 16:1."""
    src, dst = random_periods(random, 16.0)
    env = await start(dut, dst)
    await random_pulses(env, random, src, dst, 1000)
    env.check(clock_ratio(src, dst), dst)
