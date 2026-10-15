"""pcie_pcs tests (wiki/src/dv/pcs/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every output
while both clocks run and the inputs toggle. The wave-2 tests replace it
once the sub-IPs are integrated. Inputs are driven and outputs sampled at
the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the outputs (wiki/src/arch/pcs/HAS.md#reset-values).
RESET_VALUES = {
    "pipe_rx_valid_o": 0,
    "pipe_phy_status_o": 1,
    "pipe_rx_elec_idle_o": 1,
    "pipe_rx_status_o": 0,
    "ser_tx_elec_idle_o": 1,
}

# Inputs toggled at random, with their widths.
INPUTS = {
    "pipe_reset_ni": 1, "pipe_tx_data_i": 16, "pipe_tx_datak_i": 2,
    "pipe_tx_detect_rx_i": 1, "pipe_tx_elec_idle_i": 1,
    "pipe_tx_compliance_i": 1, "pipe_rx_polarity_i": 1,
    "pipe_power_down_i": 2, "ser_tx_rxdet_i": 1, "ser_rx_data_i": 20,
    "ser_rx_elec_idle_i": 1,
}

# Functional coverage: reset asserted and released, every PowerDown value.
COV = Covergroup("pcs_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("power_down", bins={"p0": 0, "p0s": 1, "p1": 2, "p2": 3})
COV.cross("rst_x_power_down", "rst", "power_down")


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
    cocotb.start_soon(Clock(dut.ser_rx_clk_i, 7996, unit="ps").start())
    for name in INPUTS:
        getattr(dut, name).value = 0
    for cycle in range(128):
        rst = int(cycle % 64 < 8)
        dut.rst_i.value = rst
        for name, width in INPUTS.items():
            getattr(dut, name).value = random.getrandbits(width)
        COV.sample(rst=rst, power_down=int(dut.pipe_power_down_i.value))
        await FallingEdge(dut.clk_i)
        check_reset_values(dut, f"cycle {cycle}")
