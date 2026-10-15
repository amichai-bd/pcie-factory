"""common_cdc_sync2 tests (wiki/src/dv/common/cdc/common_cdc_sync2.md).

The cell has no source clock, so the test models the source flop: it
changes src_data_i on a source clock grid, one bit per update (a Gray
sequence), and holds each value for at least two destination periods, as
the MAS requires. dst_data_o is sampled at every falling dst_clk_i edge.
The asynchronous-reset test (ASYNC_RESET 1) stops dst_clk_i and checks the
reset in the same time step it is driven.
"""

import math
import random

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, Timer

from regress.funcov import Covergroup
from tb.cdc_env import (CLOCK_BINS, EdgeLog, GatedClock, clock_ratio, falling_edges, now,
                        random_periods, reset_step, start_clock)

# Functional coverage (DV plan, Strategy): the clock ratio class of each
# update, and how long the update was held, in destination periods.
COV = Covergroup("common_cdc_sync2")
COV.coverpoint("clocks", bins=CLOCK_BINS)
COV.coverpoint("hold", bins={"min": lambda h: h < 3.0, "long": lambda h: h >= 3.0})
COV.cross("clocks_x_hold", "clocks", "hold")
# Asynchronous reset (ASYNC_RESET 1): asserted between edges of a running
# clock, or with the clock stopped and released before it restarts or held
# into the restart.
COV.coverpoint("async_reset", bins=["running", "stopped_pulse", "stopped_held"])


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
        hold = math.ceil((2 * dst_period + rng.randrange(3 * dst_period)) / src_period) * src_period
        COV.sample(clocks=clock_ratio(src_period, dst_period), hold=hold / dst_period)
        await Timer(hold, "ps")
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


@cocotb.test()
async def async_reset(dut):
    """L1, ASYNC_RESET 1: dst_rst_i loads RESET_VALUE in the time step it
    rises, with no dst_clk_i edge: at time zero, between edges of a running
    clock, and with the clock stopped. A pulse wholly inside the stopped
    interval leaves both flops at RESET_VALUE; a reset held into the
    restart releases synchronously. After each release the source value
    shows exactly two dst_clk_i edges later, RESET_VALUE until then."""
    assert int(dut.ASYNC_RESET.value) == 1, "async_reset needs ASYNC_RESET 1"
    width = len(dut.src_data_i)
    assert width >= 2, "async_reset needs WIDTH 2 or more (three distinct values)"
    rv = int(dut.RESET_VALUE.value)
    _, dst = random_periods(random, 8.0)
    half = dst // 2
    others = [v for v in range(1 << width) if v != rv]
    dut._log.info("width %d, RESET_VALUE %#x, dst period %d ps", width, rv, dst)
    clk = GatedClock(dut.dst_clk_i, dst)

    async def expect_now(want, what):
        await ReadOnly()
        got = int(dut.dst_data_o.value)
        assert got == want, f"{what}: dst_data_o expected {want:#x} got {got:#x} at {now()} ps"
        await Timer(1, "ps")  # leave the read-only phase

    async def released(value, what):
        """Released at a falling edge with src_data_i at ``value``: one edge
        later the output is still RESET_VALUE, two edges later ``value``."""
        await FallingEdge(dut.dst_clk_i)
        got = int(dut.dst_data_o.value)
        assert got == rv, f"{what}: one edge after release expected {rv:#x} got {got:#x}"
        await FallingEdge(dut.dst_clk_i)
        got = int(dut.dst_data_o.value)
        assert got == value, (f"{what}: two edges after release expected {value:#x} "
                              f"got {got:#x}")

    async def settle(value):
        dut.src_data_i.value = value
        await falling_edges(dut.dst_clk_i, 4)
        got = int(dut.dst_data_o.value)
        assert got == value, f"transfer: dst_data_o expected {value:#x} got {got:#x}"

    v1, v2 = random.sample(others, 2)
    dut.src_data_i.value = v1
    dut.dst_rst_i.value = 1
    await expect_now(rv, "reset at time zero, before any dst_clk_i edge")
    await Timer(dst + 1, "ps")
    clk.start()
    await reset_step(clk, dut.dst_rst_i)
    await released(v1, "first release")

    # Running clock: assertion between edges.
    await settle(v2)
    await FallingEdge(dut.dst_clk_i)
    await Timer(random.randrange(1, half), "ps")
    dut.dst_rst_i.value = 1
    COV.sample(async_reset="running")
    await expect_now(rv, "assertion between edges")
    dut.src_data_i.value = v1
    await reset_step(clk, dut.dst_rst_i)
    await released(v1, "release after a running-clock reset")

    # Stopped clock, a pulse wholly inside the stop: no edge sees it.
    await settle(v2)
    await clk.stop()
    await Timer(random.randrange(1, 3 * dst), "ps")
    dut.dst_rst_i.value = 1
    COV.sample(async_reset="stopped_pulse")
    await expect_now(rv, "assertion with dst_clk_i stopped")
    dut.src_data_i.value = v1
    await Timer(random.randrange(1, 3 * dst), "ps")
    dut.dst_rst_i.value = 0
    await expect_now(rv, "release with dst_clk_i stopped")
    await Timer(random.randrange(1, 3 * dst), "ps")
    clk.start()
    await Timer(1, "ps")
    # The restart's first rising edge is the first edge after the release.
    await released(v1, "restart after a reset pulse with the clock stopped")

    # Stopped clock, the reset held into the restart, released synchronously.
    await settle(v2)
    await clk.stop()
    await Timer(random.randrange(1, 3 * dst), "ps")
    dut.dst_rst_i.value = 1
    COV.sample(async_reset="stopped_held")
    await expect_now(rv, "assertion with dst_clk_i stopped (held)")
    dut.src_data_i.value = v1
    await Timer(5 * dst, "ps")
    await expect_now(rv, "held reset with dst_clk_i stopped")
    clk.start(random.randrange(2, dst, 2))
    await reset_step(clk, dut.dst_rst_i)
    await released(v1, "release after a reset held into the restart")
