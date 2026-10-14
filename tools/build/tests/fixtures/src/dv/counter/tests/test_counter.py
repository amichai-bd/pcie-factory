"""Fixture counter tests: one per regression level."""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly

from regress.funcov import Covergroup

COV = Covergroup("counter_enable")
COV.coverpoint("en", bins={"off": 0, "on": 1})


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst.value = 1
    dut.en.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst.value = 0


@cocotb.test()
async def counts(dut):
    """Level 0: counts enabled cycles from reset."""
    await reset(dut)
    dut.en.value = 1
    COV.sample(en=1)
    await ClockCycles(dut.clk, 5)
    await ReadOnly()
    assert int(dut.q.value) == 5


@cocotb.test()
async def wraps(dut):
    """Level 1: wraps at 2**W (run with W overridden by params)."""
    await reset(dut)
    width = len(dut.q)
    dut.en.value = 1
    await ClockCycles(dut.clk, 2**width + 3)
    await ReadOnly()
    assert int(dut.q.value) == 3


@cocotb.test()
async def random_enable(dut):
    """Level 2: random enable pattern against a model (seeded by cocotb)."""
    await reset(dut)
    width = len(dut.q)
    expected = 0
    for _ in range(200):
        en = random.randint(0, 1)
        dut.en.value = en
        COV.sample(en=en)
        await RisingEdge(dut.clk)
        expected = (expected + en) % (2**width)
    await ReadOnly()
    assert int(dut.q.value) == expected
