"""pcie_rc smoke test (wiki/src/dv/rc/DV_PLAN.md).

L0: pcie_rc alone, its line receiving random words with no partner. The
outputs hold their HAS reset values while rst_i is 1; out of reset, with
start_i 0 (no run), the run result stays 0 and the link never comes up
(nothing trains against noise). Inputs are driven and outputs sampled at
the falling edge of clk_i. The functional tests are the back-to-back e2e
(tests/test_rc_e2e.py).
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values (wiki/src/arch/rc/HAS.md#clocks-and-reset).
RESET_VALUES = {
    "done_o": 0, "pass_o": 0, "fail_step_o": 0,
    "dl_up_o": 0, "dl_state_o": 0, "dl_err_o": 0,
    "link_up_o": 0, "ltssm_state_o": 0, "rcv_err_o": 0, "ser_tx_elec_idle_o": 1,
}
# Out of reset with no run and no partner.
IDLE_VALUES = {"done_o": 0, "pass_o": 0, "fail_step_o": 0, "dl_up_o": 0, "link_up_o": 0}

# Inputs toggled at random, with their widths (start_i only in reset).
INPUTS = {"ser_tx_rxdet_i": 1, "ser_rx_data_i": 20, "ser_rx_elec_idle_i": 1}

# Functional coverage: reset asserted and released, with start_i low and
# high (high in reset only: it counts as 0 there).
COV = Covergroup("rc_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("start", bins={"low": 0, "high": 1})
COV.cross("rst_x_start", "rst", "start", unreachable=[("low", "high")])


def check(dut, table, where):
    for name, want in table.items():
        got = int(getattr(dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"


@cocotb.test()
async def smoke(dut):
    """L0: reset values in reset, no run and no link out of it, with both
    clocks running and the inputs toggling."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    # The recovered clock runs at its own, slightly offset period.
    cocotb.start_soon(Clock(dut.ser_rx_clk_i, 8002, unit="ps").start())
    dut.start_i.value = 0
    for name in INPUTS:
        getattr(dut, name).value = 0
    for cycle in range(512):
        rst = int(cycle < 16 or 256 <= cycle < 272)
        dut.rst_i.value = rst
        start = random.getrandbits(1) if rst else 0
        dut.start_i.value = start if rst and cycle not in (15, 271) else 0
        for name, width in INPUTS.items():
            getattr(dut, name).value = random.getrandbits(width)
        COV.sample(rst=rst, start=int(dut.start_i.value))
        await FallingEdge(dut.clk_i)
        if rst and cycle not in (0, 256):
            check(dut, RESET_VALUES, f"cycle {cycle} (in reset)")
        elif not rst:
            check(dut, IDLE_VALUES, f"cycle {cycle} (no run, no partner)")
