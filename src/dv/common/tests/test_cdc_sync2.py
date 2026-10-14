"""common_cdc_sync2 tests (wiki/src/dv/common/common_cdc_sync2.md).

The cell has no source clock, so the test models the source flop: it
changes src_data_i on a source clock grid, one bit per update (a Gray
sequence), and holds each value for at least two destination periods, as
the MAS requires. dst_data_o is sampled at every falling dst_clk_i edge.
"""

import math
import random

import cocotb
from cocotb.triggers import FallingEdge, Timer

from tb.cdc_env import EdgeLog, falling_edges, now, random_periods, start_clock


async def reset(dut):
    """Reset with src_data_i all ones (the output must stay zero), then
    release with src_data_i zero."""
    width = len(dut.src_data_i)
    dut.dst_rst_i.value = 1
    dut.src_data_i.value = (1 << width) - 1
    await falling_edges(dut.dst_clk_i, 3)
    got = int(dut.dst_data_o.value)
    assert got == 0, f"in reset: dst_data_o expected 0 got {got:#x}"
    dut.src_data_i.value = 0
    await falling_edges(dut.dst_clk_i, 1)
    dut.dst_rst_i.value = 0
    await falling_edges(dut.dst_clk_i, 3)
    got = int(dut.dst_data_o.value)
    assert got == 0, f"after reset: dst_data_o expected 0 got {got:#x}"


async def stream(dut, rng, src_period, dst_period, changes):
    """Send ``changes`` one-bit updates; check that dst_data_o shows exactly
    the sent sequence and each value two or three dst edges after it was
    driven."""
    width = len(dut.src_data_i)
    dut._log.info("width %d, src period %d ps, dst period %d ps, %d changes",
                  width, src_period, dst_period, changes)
    edges = EdgeLog(dut.dst_clk_i)
    samples = []

    async def sample():
        while True:
            await FallingEdge(dut.dst_clk_i)
            samples.append((now(), int(dut.dst_data_o.value)))

    cocotb.start_soon(sample())
    await Timer(rng.randrange(2, src_period + 1, 2), "ps")  # source clock phase
    value = 0
    sent = []
    for _ in range(changes):
        value ^= 1 << rng.randrange(width)
        dut.src_data_i.value = value
        sent.append((now(), value))
        hold = 2 * dst_period + rng.randrange(3 * dst_period)
        await Timer(math.ceil(hold / src_period) * src_period, "ps")
    await falling_edges(dut.dst_clk_i, 4)

    seen = [v for i, (_, v) in enumerate(samples) if i == 0 or v != samples[i - 1][1]]
    expected = [0] + [v for _, v in sent]
    if seen != expected:
        i = next((i for i, (a, b) in enumerate(zip(seen, expected)) if a != b),
                 min(len(seen), len(expected)))
        raise AssertionError(
            f"dst_data_o value {i} of the sequence: expected "
            f"{expected[i] if i < len(expected) else 'nothing'}, got "
            f"{seen[i] if i < len(seen) else 'nothing'} "
            f"({len(expected)} values sent, {len(seen)} seen)")
    k = 0
    for i, (t, v) in enumerate(sent):
        while not (samples[k][0] > t and samples[k][1] == v):
            k += 1
        edges.check_lag(t, edges.last_before(samples[k][0]), f"update {i} ({v:#x})")


async def start(dut, dst_period):
    start_clock(dut.dst_clk_i, dst_period)
    await reset(dut)


@cocotb.test()
async def smoke(dut):
    """L0: reset value, then directed updates at a fixed 3:5 clock ratio."""
    await start(dut, 10_000)
    await stream(dut, random.Random(1), 6_000, 10_000, 16)


@cocotb.test()
async def random_ratio(dut):
    """L1: random Gray updates at a random clock ratio up to 8:1 either way."""
    src, dst = random_periods(random, 8.0)
    await start(dut, dst)
    await stream(dut, random, src, dst, 200)


@cocotb.test()
async def long_ratio(dut):
    """L2: long random Gray stream at a random clock ratio up to 16:1."""
    src, dst = random_periods(random, 16.0)
    await start(dut, dst)
    await stream(dut, random, src, dst, 2000)
