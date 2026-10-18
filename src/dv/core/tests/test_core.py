"""pcie_core smoke test (wiki/src/dv/core/DV_PLAN.md).

The level 0 check that the stack elaborates and resets: pcie_core alone,
both clocks running, every input toggling at random (the line too). While
rst_i is 1 every output with a reset value holds it; after reset the link
cannot train on a random line, so dl_up_o stays 0 and the application and
function-status outputs hold their reset values (HAS, reset). The
functional tests are the back-to-back e2e (test_core_e2e.py). Inputs are
driven and outputs sampled at the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the qualified and status outputs
# (wiki/src/arch/core/HAS.md#reset); data outputs are don't care.
RESET_FN = {
    "cq_valid_o": 0, "cc_ready_o": 0, "rq_ready_o": 0, "rc_valid_o": 0,
    "irq_ready_o": 0, "irq_done_o": 0,
    "bus_master_en_o": 0, "mem_space_en_o": 0, "d_state_o": 0,
    "max_payload_o": 0, "max_read_req_o": 2,
    "dl_up_o": 0, "dl_state_o": 0,
}
# The link outputs at their reset values (link HAS), while rst_i is 1 only.
RESET_LINK = {"dl_err_o": 0, "link_up_o": 0, "ltssm_state_o": 0, "rcv_err_o": 0,
              "ser_tx_elec_idle_o": 1}

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
COV = Covergroup("core_smoke")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("app_offer", bins={"idle": 0, "rq": 1, "irq": 2, "both": 3})
COV.cross("rst_x_app_offer", "rst", "app_offer")


def check_reset_values(dut, table, where):
    for name, want in table.items():
        got = int(getattr(dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"


@cocotb.test()
async def smoke(dut):
    """L0: the reset values in reset, and the application and function
    status at theirs after it while the link is down, with both clocks
    running and the inputs toggling."""
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
        check_reset_values(dut, RESET_FN, f"cycle {cycle}")
        if rst and cycle not in (0, 64):         # rst_i sampled at the edge just passed
            check_reset_values(dut, RESET_LINK, f"cycle {cycle} in reset")
