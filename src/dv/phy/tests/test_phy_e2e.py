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
    """Link-up with up to 2000 cycles of reset skew, checked against the
    HAS targets at TIMEOUT_DIV 1024, then random, large-TLP and DLLP
    lossless phases."""
    env = await trained(dut, a_role, ppm, skew_max=2000)
    env.check_link_up(max_us=100.0)
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


# ------------------------------------------- RTL: resets, power, control

async def rtl_env(dut, a_role: str, ppm: float, **kw) -> Env:
    env = Env(dut, a_role=a_role, ppm=ppm, seed=seed(), **kw)
    assert env.layers["mac"] == "rtl", "this test needs MAC_RTL 1 and PCS_RTL 1"
    env.start()
    await env.train()
    return env


def downstream(env: Env) -> int:
    return 0 if env.a_role == "downstream" else 1


async def poll_until(env: Env, cond, limit: int, what: str) -> int:
    """``env.until`` checked every a cycle: for the cycle-bounded targets."""
    return await env.until(cond, limit, what, poll=1)


@cocotb.test()
async def e2e_reset_single(dut):
    """L1, RTL, ERR_TIMEOUT_DIV 1024: port a reset alone while packets
    flow, then port b alone. The port not reset loses its partner, waits
    out Recovery's divided timeout and goes to Detect; both retrain to L0.
    Lossless after each."""
    env = await rtl_env(dut, "downstream", 300)
    assert env.err_timeout_div == 1024, "e2e_reset_single needs ERR_TIMEOUT_DIV 1024"
    await env.lossless(12)
    for i in (0, 1):
        await env.reset_ports(which=(i,), limit_cycles=120_000)
        await env.lossless(12)
    env.final_checks()


L0S_IDLE_CYCLES = 256          # pcie_mac default (the phy keeps it)
N_FTS = 32                     # pcie_mac default
L1_IDLE, L2_IDLE = 0x31, 0x34  # ltssm_state_o codes (mac HAS, LTSSM page)
HOT_RESET, DISABLED, LOOPBACK_ACTIVE = 0x19, 0x18, 0x1D


def tx_l0s(code: int) -> int:
    """The transmit L0s sub-state in an L0 code (0x20 | tx << 2 | rx)."""
    return (code >> 2) & 3 if code & 0xF0 == 0x20 else -1


def rx_l0s(code: int) -> int:
    return code & 3 if code & 0xF0 == 0x20 else -1


async def stays_up(env: Env, cycles: int, what: str) -> None:
    """link_up_o stays high on both ports for ``cycles``."""
    downs = [0, 0]

    def watch(bench, j):
        downs[j] += not bench.ports[j].link_up

    env.bench.hooks.append(watch)
    await ClockCycles(env.dut.a_clk_i, cycles, rising=False)
    env.bench.hooks.remove(watch)
    assert downs == [0, 0], f"{what}: link_up_o fell ({downs} cycles down on a, b)"


@cocotb.test()
async def e2e_power(dut):
    """L1, RTL, ERR_TIMEOUT_DIV 1024, end to end through pm_state_req_i
    (HAS Power states; mac HAS targets):
    - L0s (request 1 on both): with no traffic, both transmitters reach
      Tx_L0s.Idle within L0S_IDLE_CYCLES + 16 cycles and the receivers
      follow; a packet each way leaves L0s through FTS, back in Tx_L0
      within 2 x N_FTS + 16 cycles of being offered; traffic lossless.
    - L1 (request 2): both in L1.Idle within 64 cycles of the requests,
      link_up_o high throughout; request 0: back in L0 through Recovery
      within 4 us; lossless after.
    - L2 (request 3): both in L2.Idle within 64 cycles, link_up_o high;
      request 0: link_up_o falls (Detect) and both are back in L0 within
      100 us; lossless after."""
    env = await rtl_env(dut, "upstream", -600)
    assert env.err_timeout_div == 1024, "e2e_power needs ERR_TIMEOUT_DIV 1024"
    a, b = env.ports
    await env.lossless(8)
    # ---- L0s
    await env.settle(100)
    mark = [len(c) for c in env.codes]
    for p in env.ports:
        p.request(pm_state=1)
    await ClockCycles(dut.a_clk_i, 400, rising=False)
    env.log.info("DEBUG codes a %s b %s", [hex(c) for c in env.codes[0][mark[0]:]], [hex(c) for c in env.codes[1][mark[1]:]])
    n = await poll_until(env, lambda: all(tx_l0s(p.code) == 2 and rx_l0s(p.code) == 2 for p in env.ports),
                         L0S_IDLE_CYCLES + 16, "both in transmit and receive L0s")
    env.log.info("L0s: both Tx_L0s.Idle and Rx_L0s.Idle %d cycles after the request", n)
    for i, p in enumerate(env.ports):
        before = env.bench.cycles[0]
        env.send(i, 1, mix="tlp")
        n = await poll_until(env, lambda: tx_l0s(p.code) == 0, 2 * N_FTS + 16,
                             f"port {'ab'[i]}: Tx_L0s exit on a packet")
        env.log.info("L0s: port %s back in Tx_L0 %d cycles after a packet", "ab"[i], n)
        await env.until(lambda: env.sb[i].drained(), 2000, "the L0s exit packet delivered")
        assert env.bench.cycles[0] > before
    await env.lossless(16, l0s=True)
    for i, s in enumerate(env.sb):
        assert s.lost == 0 and s.flagged == 0 and s.check_failed == 0, f"{s.name}: loss across L0s"
    for p in env.ports:
        p.request(pm_state=0)
    await env.settle(200)
    # ---- L1 and L2
    for req, idle in ((2, L1_IDLE), (3, L2_IDLE)):
        for p in env.ports:
            p.request(pm_state=req)
        n = await poll_until(env, lambda: all(p.code == idle for p in env.ports), 64,
                             f"both in {idle:#x}")
        env.log.info("pm_state %d: both in %#x %d cycles after the requests", req, idle, n)
        await stays_up(env, 500, f"in {idle:#x}")
        assert all(p.code == idle for p in env.ports), f"left {idle:#x} without a request"
        downs = [False, False]

        def watch(bench, j):
            downs[j] |= not bench.ports[j].link_up

        env.bench.hooks.append(watch)
        for p in env.ports:
            p.request(pm_state=0)
        limit = 500 if req == 2 else 12_500           # 4 us, 100 us
        n = await poll_until(env, env.bench.both_l0, limit, f"{idle:#x} back to L0")
        env.bench.hooks.remove(watch)
        if req == 2:
            assert downs == [False, False], "L1 exit: link_up_o fell"
            assert all(any(c in stack_recovery() for c in env.codes[i][-8:]) for i in (0, 1)), \
                "L1 exit: not through Recovery"
        else:
            assert downs == [True, True], "L2 exit: link_up_o did not fall"
        env.log.info("pm_state 0 from %#x: both in L0 after %d cycles", idle, n)
        await env.lossless(12)
    env.final_checks()


def stack_recovery():
    from tb.phy_e2e_env import RECOVERY_CODES
    return RECOVERY_CODES


@cocotb.test()
async def dbg_l0s(dut):
    env = await rtl_env(dut, "upstream", 0)
    a, b = env.ports
    await env.settle(100)
    a.request(pm_state=1)
    u = dut.u_b
    from cocotb.triggers import FallingEdge
    trace = []
    for _ in range(400):
        await FallingEdge(dut.b_clk_i)
        trace.append((int(u.pipe_rx_elec_idle.value), int(u.pipe_rx_valid.value),
                      int(u.pipe_rx_data.value), int(u.pipe_rx_datak.value), int(u.ltssm_state_o.value),
                      int(dut.u_a.pipe_tx_elec_idle.value), int(dut.u_a.pipe_tx_data.value), int(dut.u_a.pipe_tx_datak.value)))
    prev = None
    for i, t in enumerate(trace):
        if t != prev:
            dut._log.info("DBG %3d b: rx_ei %d valid %d data %04x k %d code %#x | a: tx_ei %d data %04x k %d", i, *t)
        prev = t


@cocotb.test()
async def dbg_states(dut):
    import os
    env = await rtl_env(dut, "downstream", 0)
    a, b = env.ports
    for what in os.environ.get("DBG_WHAT", "l1,l2,hot,dis,loop").split(","):
        await env.settle(100)
        mark = [len(c) for c in env.codes]
        req = {"l1": dict(pm_state=2), "l2": dict(pm_state=3), "hot": dict(hot_reset=1),
               "dis": dict(disable=1), "loop": dict(loopback=1)}[what]
        if what in ("l1", "l2"):
            a.request(**req); b.request(**req)
        else:
            a.request(**req)
        await ClockCycles(dut.a_clk_i, 3000, rising=False)
        mid = [list(c[m:]) for c, m in zip(env.codes, mark)]
        for k in req:
            a.request(**{k: 0}); b.request(**{k: 0})
        try:
            await env.until(env.bench.both_l0, 60_000, f"{what}: back to L0")
            ok = True
        except AssertionError as e:
            ok = str(e)[:120]
        dut._log.info("DBGS %s: held %s -> after %s ; back %s", what, [[hex(x) for x in m] for m in mid],
                      [[hex(x) for x in c[m:]][-6:] for c, m in zip(env.codes, mark)], ok)
        if ok is not True:
            break
