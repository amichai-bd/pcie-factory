"""Fixture incrementer sub-IP test."""

import cocotb
from cocotb.triggers import Timer


@cocotb.test()
async def exhaustive(dut):
    for a in range(256):
        dut.a.value = a
        await Timer(1, unit="ns")
        assert int(dut.y.value) == (a + 1) % 256
