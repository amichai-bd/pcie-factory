"""pcie_core tests (wiki/src/dv/core/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every output
while both clocks run and the inputs toggle. The back-to-back e2e against
the independent host model replaces it once pcie_tl and pcie_cfg are
stacked over pcie_link. Inputs are driven and outputs sampled at the
falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the qualified and status outputs
# (wiki/src/arch/core/HAS.md#reset); data outputs are don't care.
RESET_VALUES = {
    "cq_valid_o": 0, "cc_ready_o": 0, "rq_ready_o": 0, "rc_valid_o": 0,
    "irq_ready_o": 0, "irq_done_o": 0,
    "bus_master_en_o": 0, "mem_space_en_o": 0, "d_state_o": 0,
    "max_payload_o": 0, "max_read_req_o": 2,
    "dl_up_o": 0, "dl_state_o": 0, "dl_err_o": 0,
    "link_up_o": 0, "ltssm_state_o": 0, "rcv_err_o": 0, "ser_tx_elec_idle_o": 1,
}

# Inputs toggled at random, with their widths.
INPUTS = {
    "cq_ready_i": 1,
    "cc_valid_i": 1, "cc_sop_i": 1, "cc_eop_i": 1, "cc_data_i": 32, "cc_handle_i": 3,
    "rq_valid_i": 1, "rq_sop_i": 1, "rq_eop_i": 1, "rq_data_i": 32, "rq_write_i": 1,
    "rq_addr_i": 64, "rq_len_i": 6, "rq_first_be_i": 4, "rq_last_be_i": 4, "rq_tag_i": 3,
    "rc_ready_i": 1, "irq_valid_i": 1, "irq_vector_i": 6,
    "ser_tx_rxdet_i": 1, "ser_rx_data_i": 20, "ser_rx_elec_idle_i": 1,
}

# Functional coverage: reset asserted and released, with and without the
# application offering a request and an interrupt.
COV = Covergroup("core_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("app_offer", bins={"idle": 0, "rq": 1, "irq": 2, "both": 3})
COV.cross("rst_x_app_offer", "rst", "app_offer")


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
        COV.sample(rst=rst,
                   app_offer=int(dut.rq_valid_i.value) | int(dut.irq_valid_i.value) << 1)
        await FallingEdge(dut.clk_i)
        check_reset_values(dut, f"cycle {cycle}")
