"""pcie_rc_tl tests (wiki/src/dv/rc_tl/DV_PLAN.md).

Wave-1 shell: the smoke test checks the MAS reset values of every output
while the inputs toggle at random, in reset and after it. The unit
testbench (commands against a TLP-level endpoint model on the link side)
replaces it with the requester path (#373). Inputs are driven and outputs
sampled at the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of every output (wiki/src/rtl/rc_tl/MAS.md#reset-and-link-down); the shell
# holds them after reset too, except tlp_rx_ready_o, which is 1 out of reset.
RESET_VALUES = {
    "cmd_ready_o": 0, "rsp_valid_o": 0, "rsp_o": 0, "msi_valid_o": 0, "msi_o": 0,
    "tlp_tx_valid_o": 0, "tlp_tx_data_o": 0, "tlp_tx_sop_o": 0, "tlp_tx_eop_o": 0,
    "fc_rel_p_o": 0, "fc_rel_pd_o": 0, "fc_rel_np_o": 0, "fc_rel_npd_o": 0,
}

# Inputs toggled at random, with their widths (t_rc_cmd is 82 bits).
INPUTS = {
    "cmd_valid_i": 1, "cmd_i": 82, "msi_ready_i": 1, "tlp_tx_ready_i": 1,
    "tlp_rx_valid_i": 1, "tlp_rx_data_i": 32, "tlp_rx_sop_i": 1, "tlp_rx_eop_i": 1,
    "fc_ph_avail_i": 8, "fc_pd_avail_i": 12, "fc_nph_avail_i": 8, "fc_npd_avail_i": 12,
    "fc_cplh_avail_i": 8, "fc_cpld_avail_i": 12, "dl_up_i": 1,
}

# Functional coverage: reset asserted and released, with the link up and
# down.
COV = Covergroup("rc_tl_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("dl_up", bins={"low": 0, "high": 1})
COV.cross("rst_x_dl_up", "rst", "dl_up")


def check_outputs(dut, rst, where):
    for name, want in RESET_VALUES.items():
        got = int(getattr(dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"
    got = int(dut.tlp_rx_ready_o.value)
    assert got == 1 - rst, f"{where}: tlp_rx_ready_o expected {1 - rst} got {got}"


@cocotb.test()
async def smoke(dut):
    """L0: every output holds its reset value in reset and after it, and
    tlp_rx_ready_o is 1 out of reset, with the inputs toggling."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    for name in INPUTS:
        getattr(dut, name).value = 0
    for cycle in range(128):
        rst = int(cycle < 8 or 64 <= cycle < 72)
        dut.rst_i.value = rst
        values = {name: random.getrandbits(width) for name, width in INPUTS.items()}
        for name, value in values.items():
            getattr(dut, name).value = value
        # Sample what was driven, not the signal: writes land after this
        # coroutine yields.
        COV.sample(rst=rst, dl_up=values["dl_up_i"])
        await FallingEdge(dut.clk_i)
        check_outputs(dut, rst, f"cycle {cycle}")
