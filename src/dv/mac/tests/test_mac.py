"""pcie_mac tests (wiki/src/dv/mac/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every output
while the inputs toggle, in both port roles. The LTSSM tests against the
link partner replace it in wave 2. Inputs are driven and outputs sampled at
the falling edge of clk_i.

The architecture model is imported as a second reference, as the arch BKM
allows (DV may import arch): src/arch/mac is appended to sys.path, located
from this file.
"""

import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "mac"))
import pcie_mac_arch as arch  # noqa: E402

# Reset values of the outputs (wiki/src/arch/mac/HAS.md#reset-values).
RESET_VALUES = {
    "pkt_tx_ready_o": 0,
    "pkt_rx_valid_o": 0,
    "pkt_rx_sop_o": 0,
    "pkt_rx_eop_o": 0,
    "pkt_rx_err_o": 0,
    "pkt_rx_nullify_o": 0,
    "link_up_o": 0,
    "ltssm_state_o": arch.CODES["Detect.Quiet"],
    "rcv_err_o": 0,
    "pipe_reset_no": 0,
    "pipe_tx_detect_rx_o": 0,
    "pipe_tx_elec_idle_o": 1,
    "pipe_tx_compliance_o": 0,
    "pipe_rx_polarity_o": 0,
    "pipe_power_down_o": arch.P1,
}

# Inputs toggled at random, with their widths.
INPUTS = {
    "pkt_tx_valid_i": 1, "pkt_tx_data_i": 16, "pkt_tx_sop_i": 1,
    "pkt_tx_eop_i": 1, "pkt_tx_dllp_i": 1, "pkt_tx_nullify_i": 1,
    "retrain_req_i": 1, "hot_reset_req_i": 1, "disable_req_i": 1,
    "loopback_req_i": 1, "pm_state_req_i": 2, "pipe_rx_data_i": 16,
    "pipe_rx_datak_i": 2, "pipe_rx_valid_i": 1, "pipe_phy_status_i": 1,
    "pipe_rx_elec_idle_i": 1, "pipe_rx_status_i": 3,
}

# Functional coverage: both port roles, reset asserted and released.
COV = Covergroup("mac_shell")
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
    both port roles, with the inputs toggling."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
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
