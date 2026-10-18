"""pcie_tl smoke test (wiki/src/dv/tl/DV_PLAN.md): the reset values with
the inputs toggling in reset, then one BAR0 write and one configuration
read through the receive path, the read's completion, and one rq write
and one interrupt write on the transmit path.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly

from tb import tl_env
from tb.tl_env import ref

# Inputs toggled at random in reset, with their widths.
INPUTS = {
    "tlp_tx_ready_i": 1,
    "tlp_rx_valid_i": 1, "tlp_rx_data_i": 32, "tlp_rx_sop_i": 1, "tlp_rx_eop_i": 1,
    "fc_ph_avail_i": 8, "fc_pd_avail_i": 12, "fc_nph_avail_i": 8, "fc_npd_avail_i": 12,
    "fc_cplh_avail_i": 8, "fc_cpld_avail_i": 12,
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


@cocotb.test()
async def smoke(dut):
    """L0: every handshake output holds its reset value while rst_i is 1 or
    dl_up_i is 0, with every input toggling; then a BAR0 write reaches cq,
    a configuration read reaches cfg and is completed, and an rq write and
    an interrupt write are sent."""
    rng = random.Random(cocotb.RANDOM_SEED)
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    for cycle in range(96):
        rst = int(cycle < 8 or 48 <= cycle < 56)
        await FallingEdge(dut.clk_i)
        dut.rst_i.value = rst
        dut.dl_up_i.value = 0 if not rst else rng.getrandbits(1)
        for name, width in INPUTS.items():
            getattr(dut, name).value = rng.getrandbits(width)
        await ReadOnly()
        for name in tl_env.RESET_ZERO:
            got = int(getattr(dut, name).value)
            assert got == 0, f"cycle {cycle}: {name} expected 0 got {got}"
    env = tl_env.TlEnv(dut, cocotb.RANDOM_SEED)
    await env.start(clock=False)
    env.send(ref.memory(True, env.regs.bar0_base + 0x40, 4))
    env.send(ref.config(False, 0x01, rid=0x0200, tag=5))
    env.request(True, 0x1000, 2)
    env.offer_msg(0, 0x0FEE_0000, 0x41)
    await env.drain(limit=500)
    assert env.outcomes == ["cq", "cfg"], env.outcomes
    assert sorted(env.tx_kinds) == ["cpl_data", "irq32", "mwr32"], env.tx_kinds
