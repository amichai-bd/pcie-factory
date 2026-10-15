"""Back-to-back e2e of two pcie_phy stacks (wiki/src/dv/phy/DV_PLAN.md).

Port a and port b on the top pcie_phy_e2e_tb; each layer is a model or the
RTL per the top's parameters (tb/phy_e2e_env.py). Every test trains both
ports from reset to L0 and checks packets both ways with the scoreboard;
the impairment tests then inject on the channel, recover through Recovery
and check a lossless phase after it. ``ppm`` is port b's clock offset from
port a's; "dsp first" means port a is the downstream port.
"""

import random

import cocotb
from cocotb.triggers import ClockCycles

from tb.phy_e2e_env import Env


def seed() -> int:
    return cocotb.RANDOM_SEED


async def trained(dut, a_role: str, ppm: float, **kw) -> Env:
    env = Env(dut, a_role=a_role, ppm=ppm, seed=seed(), **kw)
    env.start()
    await env.train()
    return env


async def ppm_run(dut, a_role: str, ppm: float) -> None:
    env = await trained(dut, a_role, ppm)
    await env.lossless(40)
    await env.lossless(20, mix="tlp", max_dw=256)      # TLPs up to 1046 bytes
    await env.lossless(40, mix="dllp")
    env.final_checks()


async def impairment_run(dut, kind: str, a_role: str, ppm: float) -> None:
    env = await trained(dut, a_role, ppm)
    await env.lossless(12)
    for direction in (0, 1):
        await env.impair(kind, direction)
        await env.lossless(12)
    env.final_checks()


# ---------------------------------------------------------------- level 0

@cocotb.test()
async def e2e_smoke(dut):
    """L0: dsp first, b +300 ppm: train to L0, 16 packets each way, one
    symbol-error burst a to b with a directed retrain, then 8 more each way."""
    env = await trained(dut, "downstream", 300)
    await env.lossless(16)
    await env.impair("flip", 0)
    await env.lossless(8)
    env.final_checks()


@cocotb.test()
async def e2e_rtl_shell(dut):
    """L0, mac and pcs as RTL (the pcie_phy shell): the environment drives
    both instances through reset in both roles and the channel model, and
    the shell keeps its reset contract: Detect.Quiet, link down, transmitter
    in electrical idle, no packet accepted or delivered. The stack RTL
    (#86) replaces this test with the e2e tests in RTL mode."""
    env = Env(dut, a_role="upstream", ppm=-300, seed=seed())
    assert all(p.kind == "rtl" for p in env.ports), "expected MAC_RTL=1 PCS_RTL=1"
    env.start()
    idle_words = [0, 0]

    def watch(bench, i):
        idle_words[i] += int(bench.ports[i].s["ser_tx_elec_idle_o"].value)

    env.bench.hooks.append(watch)
    env.send(0, 4)
    env.send(1, 4)
    await ClockCycles(dut.a_clk_i, 3000, rising=False)
    for i, p in enumerate(env.ports):
        name = "ab"[i]
        assert p.code == 0 and not p.link_up, f"port {name}: expected Detect.Quiet, link down"
        assert idle_words[i] == env.bench.cycles[i], f"port {name}: transmitter left electrical idle"
        assert not p.tx_idle(), f"port {name}: a beat was accepted by the shell"
        assert env.sb[1 - i].delivered == 0 and env.sb[1 - i].flagged == 0, \
            f"port {name}: the shell delivered a packet"
        assert int(p.s["port_role_i"].value) == ("downstream", "upstream").index(p.role)


# ---------------------------------------------------------------- level 1

@cocotb.test()
async def e2e_dsp_first_pos600(dut):
    """L1: dsp first, b +600 ppm: random, large-TLP and DLLP phases."""
    await ppm_run(dut, "downstream", 600)


@cocotb.test()
async def e2e_dsp_first_neg300(dut):
    """L1: dsp first, b -300 ppm."""
    await ppm_run(dut, "downstream", -300)


@cocotb.test()
async def e2e_usp_first_neg600(dut):
    """L1: usp first, b -600 ppm."""
    await ppm_run(dut, "upstream", -600)


@cocotb.test()
async def e2e_usp_first_pos300(dut):
    """L1: usp first, b +300 ppm."""
    await ppm_run(dut, "upstream", 300)


@cocotb.test()
async def e2e_symbol_errors(dut):
    """L1: single-bit errors each way, directed retrain, lossless after."""
    await impairment_run(dut, "flip", "upstream", 600)


@cocotb.test()
async def e2e_bit_slips(dut):
    """L1: a bit slip each way (relock), directed retrain, lossless after."""
    await impairment_run(dut, "slip", "downstream", -600)


@cocotb.test()
async def e2e_elec_idle(dut):
    """L1: a line dropout each way: the receiver goes to Recovery on its
    own (electrical idle without an EIOS), lossless after."""
    await impairment_run(dut, "idle", "upstream", 300)


# ---------------------------------------------------------------- level 2

async def soak(dut, a_role: str) -> None:
    """Offset drawn in [-600, 600] ppm from the seed; 6 random impairments,
    each on a random direction, between lossless phases of mixed traffic
    with TLPs up to the maximum size."""
    rng = random.Random(seed() ^ 0x5EED)
    ppm = rng.randint(-600, 600)
    env = await trained(dut, a_role, ppm, skew_max=2000)
    await env.lossless(60, max_dw=1024)
    for _ in range(6):
        await env.impair(rng.choice(("flip", "slip", "idle")), rng.randrange(2))
        await env.lossless(30, max_dw=1024)
    env.final_checks()


@cocotb.test()
async def e2e_soak_dsp_first(dut):
    """L2: soak with port a downstream."""
    await soak(dut, "downstream")


@cocotb.test()
async def e2e_soak_usp_first(dut):
    """L2: soak with port a upstream."""
    await soak(dut, "upstream")
