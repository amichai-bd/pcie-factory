"""pcie_tl tests (wiki/src/dv/tl/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every output
while the clock runs and the inputs toggle. The receive, transmit and
completion-tracking tests of wave 2 replace it. Inputs are driven and
outputs sampled at the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the qualified outputs (wiki/src/arch/tl/HAS.md#reset);
# data outputs are don't care.
RESET_VALUES = {
    "tlp_tx_valid_o": 0, "tlp_rx_ready_o": 0, "fc_rel_p_o": 0, "fc_rel_np_o": 0,
    "reg_req_valid_o": 0, "tl_err_o": 0, "tl_np_pending_o": 0, "msg_ready_o": 0,
    "cq_valid_o": 0, "cc_ready_o": 0, "rq_ready_o": 0, "rc_valid_o": 0,
}

# Inputs toggled at random, with their widths.
INPUTS = {
    "tlp_tx_ready_i": 1,
    "tlp_rx_valid_i": 1, "tlp_rx_data_i": 32, "tlp_rx_sop_i": 1, "tlp_rx_eop_i": 1,
    "fc_ph_avail_i": 8, "fc_pd_avail_i": 12, "fc_nph_avail_i": 8, "fc_npd_avail_i": 12,
    "fc_cplh_avail_i": 8, "fc_cpld_avail_i": 12, "dl_up_i": 1,
    "reg_req_ready_i": 1, "reg_cpl_valid_i": 1, "reg_cpl_data_i": 32,
    "bus_num_i": 8, "dev_num_i": 5, "mem_space_en_i": 1, "bus_master_en_i": 1,
    "d_state_i": 2, "bar0_base_i": 32, "bar2_base_i": 32, "cpl_timeout_dis_i": 1,
    "msg_valid_i": 1, "msg_kind_i": 2, "msg_addr_i": 64, "msg_data_i": 32,
    "cq_ready_i": 1,
    "cc_valid_i": 1, "cc_sop_i": 1, "cc_eop_i": 1, "cc_data_i": 32, "cc_handle_i": 3,
    "rq_valid_i": 1, "rq_sop_i": 1, "rq_eop_i": 1, "rq_data_i": 32, "rq_write_i": 1,
    "rq_addr_i": 64, "rq_len_i": 6, "rq_first_be_i": 4, "rq_last_be_i": 4, "rq_tag_i": 3,
    "rc_ready_i": 1,
}

# Functional coverage: reset asserted and released, with the data link up
# and down, crossed.
COV = Covergroup("tl_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("dl_up", bins={"down": 0, "up": 1})
COV.cross("rst_x_dl_up", "rst", "dl_up")


def check_reset_values(dut, where):
    for name, want in RESET_VALUES.items():
        got = int(getattr(dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"


@cocotb.test()
async def smoke(dut):
    """L0: every output holds its reset value in reset and after it, with
    the clock running and the inputs toggling."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    for name in INPUTS:
        getattr(dut, name).value = 0
    for cycle in range(128):
        rst = int(cycle < 8 or 64 <= cycle < 72)
        dut.rst_i.value = rst
        for name, width in INPUTS.items():
            getattr(dut, name).value = random.getrandbits(width)
        COV.sample(rst=rst, dl_up=int(dut.dl_up_i.value))
        await FallingEdge(dut.clk_i)
        check_reset_values(dut, f"cycle {cycle}")
