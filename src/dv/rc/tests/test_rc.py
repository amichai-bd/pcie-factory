"""pcie_rc tests (wiki/src/dv/rc/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every output
while both clocks run and the inputs toggle. The back-to-back e2e against
pcie_core, with the host model as a passive shadow, replaces it once
pcie_rc_tl and pcie_rc_seq are stacked over pcie_link (#377). Inputs are
driven and outputs sampled at the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the sequencer and status outputs
# (wiki/src/arch/rc/HAS.md#clocks-and-reset); the shell has no run, so
# they hold after reset too.
RESET_VALUES = {
    "done_o": 0, "pass_o": 0, "fail_step_o": 0,
    "dl_up_o": 0, "dl_state_o": 0, "dl_err_o": 0,
    "link_up_o": 0, "ltssm_state_o": 0, "rcv_err_o": 0, "ser_tx_elec_idle_o": 1,
}

# Inputs toggled at random, with their widths.
INPUTS = {"start_i": 1, "ser_tx_rxdet_i": 1, "ser_rx_data_i": 20, "ser_rx_elec_idle_i": 1}

# Functional coverage: reset asserted and released, with start_i low and
# high.
COV = Covergroup("rc_shell")
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
    both clocks running and the inputs toggling."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    # The recovered clock runs at its own, slightly offset period.
    cocotb.start_soon(Clock(dut.ser_rx_clk_i, 8002, unit="ps").start())
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
