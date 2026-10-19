"""pcie_rc_seq tests (wiki/src/dv/rc_seq/DV_PLAN.md).

Shell: the smoke test checks the MAS reset values of every output while
the inputs toggle. The interpreter tests against a stand-in for the
pcie_rc_tl command interface replace it (#376). Inputs are driven and
outputs sampled at the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values (wiki/src/rtl/rc_seq/MAS.md#reset); the shell has no run,
# so they hold after reset too.
RESET_VALUES = {"done_o": 0, "pass_o": 0, "fail_step_o": 0, "hot_reset_req_o": 0}

# Inputs toggled at random, with their widths.
INPUTS = {"start_i": 1, "dl_up_i": 1, "link_up_i": 1}

# Functional coverage: reset asserted and released, with start_i low and
# high.
COV = Covergroup("rc_seq_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("start", bins={"low": 0, "high": 1})
COV.cross("rst_x_start", "rst", "start")


def check_reset_values(dut, where):
    for name, want in RESET_VALUES.items():
        got = int(getattr(dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"


@cocotb.test()
async def smoke(dut):
    """L0: every output holds its reset value in reset and after it, with
    the inputs toggling."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    for name in INPUTS:
        getattr(dut, name).value = 0
    for cycle in range(128):
        rst = int(cycle < 8 or 64 <= cycle < 72)
        dut.rst_i.value = rst
        for name, width in INPUTS.items():
            getattr(dut, name).value = random.getrandbits(width)
        COV.sample(rst=rst, start=int(dut.start_i.value))
        await FallingEdge(dut.clk_i)
        check_reset_values(dut, f"cycle {cycle}")
