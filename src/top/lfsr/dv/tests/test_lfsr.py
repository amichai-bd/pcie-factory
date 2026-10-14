"""pcie_top_lfsr tests, one testcase per regression level
(wiki/src/top/lfsr/DV_PLAN.md).

Inputs are driven and key_o is sampled at the falling clock edge; the RTL
updates on the rising edge.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from models.pcie_top_lfsr_model import Lfsr

# Published scrambler key bytes after a re-seed (see the model unit tests).
KAT = [0xFF, 0x17, 0xC0, 0x14, 0xB2]


async def start(dut):
    """Start the clock and hold reset for two edges; return at a falling edge."""
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_i.value = 1
    dut.reseed_i.value = 0
    dut.advance_i.value = 0
    for _ in range(3):
        await FallingEdge(dut.clk_i)


async def step(dut, model, idx, rst=0, reseed=0, advance=0):
    """Check key_o against the model, drive one cycle, return at the next
    falling edge."""
    got = int(dut.key_o.value)
    assert got == model.key, (
        f"cycle {idx}: key_o expected {model.key:#04x} got {got:#04x} "
        f"(model state {model.state:#06x})")
    dut.rst_i.value = rst
    dut.reseed_i.value = reseed
    dut.advance_i.value = advance
    model.clock(rst, reseed, advance)
    await FallingEdge(dut.clk_i)


@cocotb.test()
async def smoke(dut):
    """L0: seed value, known-answer advance, hold, re-seed priority, reset."""
    await start(dut)
    model = Lfsr()
    seen = []
    for i in range(len(KAT)):
        seen.append(int(dut.key_o.value))
        await step(dut, model, i, advance=1)
    assert seen == KAT, f"key bytes after reset: expected {KAT} got {seen}"
    held = int(dut.key_o.value)
    await step(dut, model, 10)  # no advance: hold
    assert int(dut.key_o.value) == held, "key_o changed without advance_i"
    await step(dut, model, 11, reseed=1, advance=1)  # re-seed wins
    assert int(dut.key_o.value) == 0xFF, "re-seed did not restore the seed"
    await step(dut, model, 12, advance=1)
    await step(dut, model, 13, rst=1, advance=1)  # reset wins
    assert int(dut.key_o.value) == 0xFF, "reset did not restore the seed"
    await step(dut, model, 14)


async def random_run(dut, model, cycles, p_reseed, p_rst):
    """Random advance/re-seed/reset traffic compared every cycle with the
    model; fails if any control was never exercised."""
    dut._log.info("random seed %d, %d cycles", cocotb.RANDOM_SEED, cycles)
    counts = {"advance": 0, "reseed": 0, "rst": 0}
    for i in range(cycles):
        rst = int(random.random() < p_rst)
        reseed = int(random.random() < p_reseed)
        advance = int(random.random() < 0.75)
        counts["rst"] += rst
        counts["reseed"] += reseed
        counts["advance"] += advance
        await step(dut, model, i, rst, reseed, advance)
    await step(dut, model, cycles)  # check the last update
    dut._log.info("controls exercised: %s", counts)
    assert all(counts.values()), f"vacuous run, a control never fired: {counts}"


@cocotb.test()
async def random_vs_model(dut):
    """L1: random controls against the reference model."""
    await start(dut)
    await random_run(dut, Lfsr(), 4000, p_reseed=0.02, p_rst=0.005)


PERIOD = 2**16 - 1  # key bytes before the sequence repeats


@cocotb.test()
async def long_random(dut):
    """L2: one full period of advances (every state checked, and the key
    must return to the seed byte exactly at the wrap), then long random
    traffic."""
    await start(dut)
    model = Lfsr()
    for i in range(PERIOD):
        await step(dut, model, i, advance=1)
    assert model.key == 0xFF and int(dut.key_o.value) == 0xFF, (
        f"after {PERIOD} advances key_o is {int(dut.key_o.value):#04x}, "
        "expected the seed byte 0xff")
    await random_run(dut, model, 100000, p_reseed=0.001, p_rst=0.0002)
