"""pcie_link tests (wiki/src/dv/link/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every output
while both clocks run and the inputs toggle, in both port roles. The
back-to-back e2e replaces it once pcie_dll and pcie_phy are stacked.
Inputs are driven and outputs sampled at the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the outputs (wiki/src/arch/link/HAS.md#reset-values).
RESET_VALUES = {
    "tlp_tx_ready_o": 0, "tlp_rx_valid_o": 0, "tlp_rx_sop_o": 0, "tlp_rx_eop_o": 0,
    "fc_ph_avail_o": 0, "fc_pd_avail_o": 0, "fc_nph_avail_o": 0,
    "fc_npd_avail_o": 0, "fc_cplh_avail_o": 0, "fc_cpld_avail_o": 0,
    "dl_up_o": 0, "dl_state_o": 0, "dl_err_o": 0,
    "link_up_o": 0, "ltssm_state_o": 0, "rcv_err_o": 0, "ser_tx_elec_idle_o": 1,
}

# Inputs toggled at random, with their widths (not port_role_i: a strap).
INPUTS = {
    "tlp_tx_valid_i": 1, "tlp_tx_data_i": 32, "tlp_tx_sop_i": 1, "tlp_tx_eop_i": 1,
    "tlp_rx_ready_i": 1, "retrain_req_i": 1, "hot_reset_req_i": 1, "disable_req_i": 1,
    "loopback_req_i": 1, "ser_tx_rxdet_i": 1, "ser_rx_data_i": 20, "ser_rx_elec_idle_i": 1,
}

# Functional coverage: both port roles, reset asserted and released.
COV = Covergroup("link_shell")
COV.coverpoint("port_role", bins={"downstream": 0, "upstream": 1})
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.cross("role_x_rst", "port_role", "rst")


def check_reset_values(dut, where):
    for name, want in RESET_VALUES.items():
        got = int(getattr(dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"


@cocotb.test()
async def smoke(dut):
    """L0: every output holds its reset value in reset and after it, in
    both port roles, with both clocks running and the inputs toggling."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    # The recovered clock runs at its own, slightly offset period.
    cocotb.start_soon(Clock(dut.ser_rx_clk_i, 8002, unit="ps").start())
    for name in INPUTS:
        getattr(dut, name).value = 0
    for role in (0, 1):
        dut.port_role_i.value = role
        for cycle in range(64):
            rst = int(cycle < 8)
            dut.rst_i.value = rst
            for name, width in INPUTS.items():
                getattr(dut, name).value = random.getrandbits(width)
            COV.sample(port_role=role, rst=rst)
            await FallingEdge(dut.clk_i)
            check_reset_values(dut, f"role {role} cycle {cycle}")
