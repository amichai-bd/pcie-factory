"""Deliberately failing and hanging tests for the tools' own unit tests.
No regression level lists these; tools/build/tests uses them directly."""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


@cocotb.test()
async def always_fails(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await RisingEdge(dut.clk)
    assert False, "deliberate failure"


@cocotb.test()
async def hangs(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    while True:
        await RisingEdge(dut.clk)
