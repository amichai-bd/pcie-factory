"""common_cdc_pulse tests (wiki/src/dv/common/cdc/common_cdc_pulse.md).

src_pulse_i is driven at falling src_clk_i edges, so the next rising edge
samples it; dst_pulse_o is sampled at every falling dst_clk_i edge, and each
high sample is one output pulse. Pulses are spaced at least two dst_clk_i
periods apart, as the MAS requires.
"""

import math
import random

import cocotb
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup
from tb.cdc_env import (CLOCK_BINS, EdgeLog, clock_ratio, falling_edges, now, random_periods,
                        start_clock)

# Functional coverage (DV plan, Strategy): the clock ratio class of each
# pulse, and its spacing to the next one: back to back (one source cycle),
# at the two-destination-period minimum, or wider.
COV = Covergroup("common_cdc_pulse")
COV.coverpoint("clocks", bins=CLOCK_BINS)
COV.coverpoint("spacing", bins=["back_to_back", "minimum", "wider"])
COV.cross("clocks_x_spacing", "clocks", "spacing")


async def start(dut, src_period, dst_period, phase):
    """Start both clocks and reset both domains together; dst_pulse_o must
    stay low in reset even while src_pulse_i is high."""
    dut._log.info("src period %d ps, dst period %d ps, dst phase %d ps",
                  src_period, dst_period, phase)
    dut.src_rst_i.value = 1
    dut.dst_rst_i.value = 1
    dut.src_pulse_i.value = 1
    start_clock(dut.src_clk_i, src_period)
    start_clock(dut.dst_clk_i, dst_period, phase)
    for _ in range(4):
        await falling_edges(dut.src_clk_i, 1)
        await falling_edges(dut.dst_clk_i, 1)
        assert int(dut.dst_pulse_o.value) == 0, "dst_pulse_o high in reset"
    dut.src_pulse_i.value = 0
    await falling_edges(dut.src_clk_i, 1)
    dut.src_rst_i.value = 0
    dut.dst_rst_i.value = 0


async def pulses(dut, rng, src_period, dst_period, count, max_extra):
    """Send ``count`` pulses at least two dst periods apart (plus up to
    ``max_extra`` src cycles); check one output pulse per input pulse, in
    order, two or three dst edges after the src edge that sampled it."""
    edges = EdgeLog(dut.dst_clk_i)
    outputs = []

    async def sample():
        while True:
            await FallingEdge(dut.dst_clk_i)
            if int(dut.dst_pulse_o.value):
                outputs.append(edges.last_before(now()))

    cocotb.start_soon(sample())
    await falling_edges(dut.dst_clk_i, 4)
    assert not outputs, f"{len(outputs)} dst_pulse_o pulse(s) with no input pulse"
    min_gap = max(1, math.ceil(2 * dst_period / src_period))
    sampled = []
    half = src_period // 2
    await FallingEdge(dut.src_clk_i)
    for _ in range(count):
        dut.src_pulse_i.value = 1
        await FallingEdge(dut.src_clk_i)
        sampled.append(now() - half)  # the rising edge that sampled it
        gap = min_gap + rng.randrange(max_extra + 1)  # src cycles to the next one
        COV.sample(clocks=clock_ratio(src_period, dst_period),
                   spacing="back_to_back" if gap == 1 else "minimum" if gap == min_gap else "wider")
        if gap > 1:
            dut.src_pulse_i.value = 0
            await falling_edges(dut.src_clk_i, gap - 1)
    dut.src_pulse_i.value = 0
    await falling_edges(dut.dst_clk_i, 5)

    assert len(outputs) == count, (
        f"sent {count} pulses, dst_pulse_o gave {len(outputs)}")
    for i, (t, e) in enumerate(zip(sampled, outputs)):
        edges.check_lag(t, e, f"pulse {i}")


@cocotb.test()
async def smoke(dut):
    """L0: reset, then directed pulses at fixed ratios, fast to slow and
    slow to fast, back to back where the spacing rule allows."""
    await start(dut, 4_000, 10_000, 1_000)
    await pulses(dut, random.Random(1), 4_000, 10_000, 8, 0)


@cocotb.test()
async def slow_to_fast(dut):
    """L0: a source slower than two destination periods may pulse on
    consecutive cycles."""
    await start(dut, 22_000, 10_000, 3_000)
    await pulses(dut, random.Random(1), 22_000, 10_000, 8, 0)


@cocotb.test()
async def random_ratio(dut):
    """L1: random spacing at a random clock ratio up to 8:1 either way."""
    src, dst = random_periods(random, 8.0)
    await start(dut, src, dst, random.randrange(2, dst, 2))
    await pulses(dut, random, src, dst, 100, 4)


@cocotb.test()
async def near_ratio(dut):
    """L1: random spacing with the two clocks within 1.2:1 of each other,
    either side faster, at a random phase: the edges beat slowly past each
    other, so launches land at every offset from the dst_clk_i edges."""
    fast = random.randrange(4_000, 20_000, 2)
    slow = 2 * round(fast * random.uniform(1.0, 1.2) / 2)
    src, dst = (fast, slow) if random.random() < 0.5 else (slow, fast)
    await start(dut, src, dst, random.randrange(2, dst, 2))
    await pulses(dut, random, src, dst, 200, 4)


@cocotb.test()
async def long_ratio(dut):
    """L2: many pulses at a random clock ratio up to 16:1, mostly at the
    minimum spacing."""
    src, dst = random_periods(random, 16.0)
    await start(dut, src, dst, random.randrange(2, dst, 2))
    await pulses(dut, random, src, dst, 1000, 1)
