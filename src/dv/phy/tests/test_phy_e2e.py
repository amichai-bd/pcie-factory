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


async def rtl_plumbing(dut, chan_rtl: int) -> Env:
    """Durable checks of the RTL path, kept when the stack trains: the
    parameters reach ``cfg_*_o``, the environment builds ``RtlPort``s and
    drives each instance's ``port_role_i`` and ``rst_i`` through reset, and
    the clocks and channel coroutines run without error."""
    env = Env(dut, a_role="upstream", ppm=-300, seed=seed())
    want = {"mac": "rtl", "pcs": "rtl", "channel": "rtl" if chan_rtl else "model"}
    assert env.layers == want, f"cfg_*_o: expected {want}, observed {env.layers}"
    assert all(p.kind == "rtl" for p in env.ports), "expected RtlPort for both ports"
    assert (env.bench.channels is None) == bool(chan_rtl)
    env.start()
    await ClockCycles(dut.a_clk_i, 4, rising=False)
    for p in env.ports:
        assert int(p.s["rst_i"].value) == 1, f"{p.role}: rst_i not driven high in reset"
    await ClockCycles(dut.a_clk_i, 400, rising=False)
    for i, p in enumerate(env.ports):
        assert env.bench.cycles[i] > 300, f"port {'ab'[i]}: clock coroutine not running"
        assert int(p.s["rst_i"].value) == 0, f"port {'ab'[i]}: rst_i not released"
        assert int(p.s["port_role_i"].value) == ("downstream", "upstream").index(p.role), \
            f"port {'ab'[i]}: port_role_i does not match role {p.role}"
    return env


async def rtl_link(dut, env: Env) -> None:
    """Both RTL stacks from reset: every output at its reset value while
    rst_i is high with the upper-layer inputs toggling at random, training
    to L0 with link_up_o, packets both ways lossless, then both ports reset
    while packets flow, simultaneously and then with b released up to 64
    cycles later (reset values held, both ports retrain), each followed by
    a lossless phase."""
    await env.train()
    for i, p in enumerate(env.ports):
        assert p.reset_checks >= 7, (
            f"port {'ab'[i]}: expected the reset values checked on at least 7 edges "
            f"in the first reset, observed {p.reset_checks}")
    await env.lossless(16)
    for skew in (0, env.rng.randrange(1, 65)):
        await env.reset_ports(skew=skew)
        await env.lossless(8)
    env.final_checks()


@cocotb.test()
async def e2e_rtl_link(dut):
    """L0, mac and pcs as RTL, channel model at -300 ppm, usp first: the
    RTL path's plumbing, link-up, traffic and resets mid-traffic."""
    env = await rtl_plumbing(dut, 0)
    await rtl_link(dut, env)


@cocotb.test()
async def e2e_rtl_link_chan(dut):
    """L0, the same over the RTL channel (CHAN_RTL 1): the top joins the
    lines and the environment only steps the ports."""
    env = await rtl_plumbing(dut, 1)
    await rtl_link(dut, env)


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


@cocotb.test()
async def e2e_scrambler_block(dut):
    """L1: both ports' transmit scrambling done by the pcie_mac_scrambler
    block (its cycle model, at its MAS interface, one cycle of latency)
    instead of the mac model: train, lossless traffic, an idle dropout and
    lossless again. The step to its RTL is in the DV plan."""
    env = Env(dut, a_role="downstream", ppm=-300, seed=seed(), tx_scrambler="block_model")
    env.start()
    await env.train()
    await env.lossless(30)
    await env.impair("idle", 0)
    await env.lossless(12)
    env.final_checks()


@cocotb.test()
async def e2e_encoder_block(dut):
    """L1: both ports' 8b/10b transmit done by the pcs_enc8b10b block (its
    cycle model, at its MAS interface, two cycles of latency) instead of the
    pcs stand-in's encoder: train, lossless, a bit slip each way (relock on
    the block's code stream), lossless again. The step to its RTL is in the
    DV plan."""
    env = Env(dut, a_role="upstream", ppm=600, seed=seed(), tx_encoder="block_model")
    env.start()
    await env.train()
    await env.lossless(30)
    for direction in (0, 1):
        await env.impair("slip", direction)
        await env.lossless(12)
    env.final_checks()


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
