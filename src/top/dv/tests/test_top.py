"""pcie_top end-to-end tests, one testcase per regression level
(wiki/src/top/DV_PLAN.md).

Inputs are driven and outputs sampled at the falling clock edge; the RTL
updates on the rising edge. The oracle is models/pcie_top_model.py, which
reuses the lfsr sub-IP model.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from models.pcie_top_model import Scrambler


async def start(dut):
    """Start the clock and hold reset for two edges; return at a falling edge."""
    cocotb.start_soon(Clock(dut.clk_i, 10, unit="ns").start())
    dut.rst_i.value = 1
    dut.valid_i.value = 0
    dut.data_i.value = 0
    dut.reseed_i.value = 0
    for _ in range(3):
        await FallingEdge(dut.clk_i)


async def step(dut, model, idx, rst=0, valid=0, data=0, reseed=0):
    """Check outputs against the model, drive one cycle, return at the next
    falling edge."""
    got_v = int(dut.valid_o.value)
    assert got_v == model.valid_o, (
        f"cycle {idx}: valid_o expected {model.valid_o} got {got_v}")
    if got_v:
        got_d = int(dut.data_o.value)
        assert got_d == model.data_o, (
            f"cycle {idx}: data_o expected {model.data_o:#04x} got {got_d:#04x}")
    dut.rst_i.value = rst
    dut.valid_i.value = valid
    dut.data_i.value = data
    dut.reseed_i.value = reseed
    model.clock(rst, valid, data, reseed)
    await FallingEdge(dut.clk_i)


async def sample(dut):
    return int(dut.valid_o.value), int(dut.data_o.value)


@cocotb.test()
async def smoke(dut):
    """L0: zero bytes give the published key bytes, bubbles hold the
    LFSR, re-seed applies after its own byte, reset clears valid_o."""
    await start(dut)
    model = Scrambler()
    assert int(dut.valid_o.value) == 0, "valid_o high out of reset"
    outs = []
    for i in range(3):
        await step(dut, model, i, valid=1, data=0x00)
        outs.append(await sample(dut))
    assert outs == [(1, 0xFF), (1, 0x17), (1, 0xC0)], f"zero stream: {outs}"
    await step(dut, model, 3)  # bubble
    assert int(dut.valid_o.value) == 0, "valid_o high after a bubble"
    await step(dut, model, 4, valid=1, data=0x5A, reseed=1)
    assert await sample(dut) == (1, 0x14 ^ 0x5A), "byte on the re-seed cycle"
    await step(dut, model, 5, valid=1, data=0x00)
    assert await sample(dut) == (1, 0xFF), "first byte after re-seed"
    await step(dut, model, 6, rst=1, valid=1, data=0x00)
    assert int(dut.valid_o.value) == 0, "valid_o high after reset"
    await step(dut, model, 7, valid=1, data=0x00)
    assert await sample(dut) == (1, 0xFF), "first byte after reset"
    await step(dut, model, 8)


async def random_run(dut, cycles, p_valid, p_reseed, p_rst):
    """Random bytes, bubbles, re-seeds and resets compared every cycle with
    the model; fails if any control was never exercised."""
    dut._log.info("random seed %d, %d cycles", cocotb.RANDOM_SEED, cycles)
    await start(dut)
    model = Scrambler()
    counts = {"bytes": 0, "bubbles": 0, "reseed": 0, "rst": 0}
    for i in range(cycles):
        rst = int(random.random() < p_rst)
        reseed = int(random.random() < p_reseed)
        valid = int(random.random() < p_valid)
        counts["rst"] += rst
        counts["reseed"] += reseed
        counts["bytes"] += valid
        counts["bubbles"] += 1 - valid
        await step(dut, model, i, rst, valid, random.getrandbits(8), reseed)
    await step(dut, model, cycles)  # check the last byte
    dut._log.info("controls exercised: %s", counts)
    assert all(counts.values()), f"vacuous run, a control never fired: {counts}"


@cocotb.test()
async def random_vs_model(dut):
    """L1: random stream against the model."""
    await random_run(dut, 4000, p_valid=0.8, p_reseed=0.02, p_rst=0.005)


@cocotb.test()
async def long_random(dut):
    """L2: long random stream with rare re-seeds and resets, so each key
    run spans thousands of bytes."""
    await random_run(dut, 150000, p_valid=0.9, p_reseed=0.0001, p_rst=0.0001)
