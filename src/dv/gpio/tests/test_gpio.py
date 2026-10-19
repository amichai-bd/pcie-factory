"""pcie_gpio tests (wiki/src/dv/gpio/DV_PLAN.md).

Wave-1 shell: the smoke test checks the HAS reset values of every
qualified, status and pin output while both clocks run and the inputs
toggle. The adapter tests replace it with the RTL (#378). Inputs are driven
and outputs sampled at the falling edge of clk_i.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

# Reset values of the qualified, status and pin outputs
# (wiki/src/arch/gpio/HAS.md#reset-values); data outputs are don't care.
RESET_VALUES = {
    "pkt_tx_ready_o": 0, "pkt_rx_valid_o": 0, "pkt_rx_sop_o": 0, "pkt_rx_eop_o": 0,
    "pkt_rx_err_o": 0, "pkt_rx_nullify_o": 0,
    "link_up_o": 0, "ltssm_state_o": 0, "rcv_err_o": 0,
    "gpio_tx_clk_o": 0, "gpio_tx_ctrl_o": 0, "gpio_tx_data_o": 0,
}

# clk_i-domain inputs toggled at random, with their widths.
INPUTS = {
    "port_role_i": 1, "pkt_tx_valid_i": 1, "pkt_tx_data_i": 16, "pkt_tx_sop_i": 1,
    "pkt_tx_eop_i": 1, "pkt_tx_dllp_i": 1, "hot_reset_req_i": 1,
}

# gpio_rx_clk_i-domain inputs.
RX_INPUTS = {"gpio_rx_ctrl_i": 1, "gpio_rx_data_i": 4}

# Functional coverage: reset asserted and released, with and without a
# packet beat offered and a Hot Reset requested.
COV = Covergroup("gpio_shell")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("offer", bins={"idle": 0, "pkt": 1, "hot_reset": 2, "both": 3})
COV.cross("rst_x_offer", "rst", "offer")


def check_reset_values(dut, where):
    for name, want in RESET_VALUES.items():
        got = int(getattr(dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"


async def drive_rx_pins(dut):
    """Toggle the receive pins on the partner's forwarded clock."""
    while True:
        await FallingEdge(dut.gpio_rx_clk_i)
        for name, width in RX_INPUTS.items():
            getattr(dut, name).value = random.getrandbits(width)


@cocotb.test()
async def smoke(dut):
    """L0: every output holds its reset value in reset and after it, with
    both clocks running and the inputs toggling."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.gpio_rx_clk_i, 16, unit="ns").start())
    for name in (*INPUTS, *RX_INPUTS):
        getattr(dut, name).value = 0
    cocotb.start_soon(drive_rx_pins(dut))
    for cycle in range(128):
        rst = int(cycle < 8 or 64 <= cycle < 72)
        dut.rst_i.value = rst
        for name, width in INPUTS.items():
            getattr(dut, name).value = random.getrandbits(width)
        COV.sample(rst=rst,
                   offer=int(dut.pkt_tx_valid_i.value) | int(dut.hot_reset_req_i.value) << 1)
        await FallingEdge(dut.clk_i)
        check_reset_values(dut, f"cycle {cycle}")
