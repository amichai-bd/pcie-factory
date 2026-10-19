"""pcie_rc_tl smoke test (wiki/src/dv/rc_tl/DV_PLAN.md).

L0: random inputs, commands included, with rst_i pulsed twice; the
environment checks every output at every falling clk_i edge against the
reference model: the reset values in reset, the front end's results after
it, the MSI capture and the releases at rest, and tlp_rx_ready_o 1 exactly
when rst_i is 0.
"""

import cocotb

from regress.funcov import Covergroup
from tb.rc_tl_env import Env

# Functional coverage: reset asserted and released, with the link up and
# down.
COV = Covergroup("rc_tl_reset")
COV.coverpoint("rst", bins={"low": 0, "high": 1})
COV.coverpoint("dl_up", bins={"low": 0, "high": 1})
COV.cross("rst_x_dl_up", "rst", "dl_up")


@cocotb.test()
async def smoke(dut):
    """L0: 128 cycles of random inputs and commands around two resets."""
    env = Env(dut, int(cocotb.RANDOM_SEED or 0))
    await env.start(rst_cycles=1)
    env.toggle_idle = True
    for cycle in range(128):
        rst = int(cycle < 8 or 64 <= cycle < 72)
        env.drv["rst_i"] = rst
        env.drv["dl_up_i"] = env.rng.getrandbits(1)
        env.drv["cmd_valid_i"] = env.rng.getrandbits(1)
        env.drv["cmd_i"] = env.rng.getrandbits(82)
        COV.sample(rst=rst, dl_up=env.drv["dl_up_i"])
        await env.tick()
    assert env.takes, "no command taken"
