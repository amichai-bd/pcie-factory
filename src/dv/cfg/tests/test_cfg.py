"""pcie_cfg tests (wiki/src/dv/cfg/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every
qualified and status output while the clock runs and the inputs toggle.
The register-file tests replace it with the wave-2 RTL. Inputs are driven
and outputs sampled at the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the qualified and state outputs
# (wiki/src/arch/cfg/HAS.md#reset); data outputs are don't care.
RESET_VALUES = {
    "reg_req_ready_o": 0, "reg_cpl_valid_o": 0,
    "bus_num_o": 0, "dev_num_o": 0, "bar0_base_o": 0, "bar2_base_o": 0,
    "mem_space_en_o": 0, "bus_master_en_o": 0, "d_state_o": 0,
    "msg_valid_o": 0, "irq_ready_o": 0, "irq_done_o": 0,
    "max_payload_o": 0, "max_read_req_o": 2,
}

# Inputs toggled at random, with their widths.
INPUTS = {
    "reg_req_valid_i": 1, "reg_req_bar2_i": 1, "reg_req_write_i": 1, "reg_req_addr_i": 10,
    "reg_req_be_i": 4, "reg_req_data_i": 32, "reg_req_bus_i": 8, "reg_req_dev_i": 5,
    "tl_err_i": 9, "dl_err_i": 7, "rcv_err_i": 1, "msg_ready_i": 1,
    "irq_valid_i": 1, "irq_vector_i": 6,
}

# Functional coverage: reset asserted and released, with and without a
# register access and an interrupt request offered.
COV = Covergroup("cfg_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("offer", bins={"idle": 0, "reg": 1, "irq": 2, "both": 3})
COV.cross("rst_x_offer", "rst", "offer")


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
        COV.sample(rst=rst,
                   offer=int(dut.reg_req_valid_i.value) | int(dut.irq_valid_i.value) << 1)
        await FallingEdge(dut.clk_i)
        check_reset_values(dut, f"cycle {cycle}")
