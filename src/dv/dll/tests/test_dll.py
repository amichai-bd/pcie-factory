"""pcie_dll tests (wiki/src/dv/dll/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every output
while the inputs toggle. The wave-2 tests replace it once the data link
blocks exist. Inputs are driven and outputs sampled at the falling edge of
clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the outputs (wiki/src/arch/dll/HAS.md#reset-values).
RESET_VALUES = {
    "tlp_tx_ready_o": 0, "tlp_rx_valid_o": 0, "tlp_rx_sop_o": 0, "tlp_rx_eop_o": 0,
    "fc_ph_avail_o": 0, "fc_pd_avail_o": 0, "fc_nph_avail_o": 0,
    "fc_npd_avail_o": 0, "fc_cplh_avail_o": 0, "fc_cpld_avail_o": 0,
    "dl_up_o": 0, "dl_state_o": 0, "err_o": 0,
    "pkt_tx_valid_o": 0, "retrain_req_o": 0,
}

# Inputs toggled at random, with their widths.
INPUTS = {
    "tlp_tx_valid_i": 1, "tlp_tx_data_i": 32, "tlp_tx_sop_i": 1, "tlp_tx_eop_i": 1,
    "tlp_rx_ready_i": 1, "pkt_tx_ready_i": 1, "pkt_rx_valid_i": 1,
    "pkt_rx_data_i": 16, "pkt_rx_sop_i": 1, "pkt_rx_eop_i": 1, "pkt_rx_dllp_i": 1,
    "pkt_rx_err_i": 1, "pkt_rx_nullify_i": 1, "link_up_i": 1, "ltssm_state_i": 6,
}

# Functional coverage: reset asserted and released, link up and down.
COV = Covergroup("dll_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("link_up", bins={"down": 0, "up": 1})
COV.cross("rst_x_link_up", "rst", "link_up")


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
        rst = int(cycle % 64 < 8)
        dut.rst_i.value = rst
        for name, width in INPUTS.items():
            getattr(dut, name).value = random.getrandbits(width)
        COV.sample(rst=rst, link_up=int(dut.link_up_i.value))
        await FallingEdge(dut.clk_i)
        check_reset_values(dut, f"cycle {cycle}")
