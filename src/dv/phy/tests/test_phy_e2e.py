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

from tb.phy_e2e_env import COV_CONTROL, COV_POWER, RECOVERY_CODES, Env, LoopbackScoreboard


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
    """L1: both ports' 8b/10b transmit done by the pcs encoder block (its
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
    env.check_link_up(max_us=100.0)
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
        COV_CONTROL.sample(event="reset_single")
        await env.lossless(12)
    env.final_checks()


L0S_IDLE_CYCLES = 256          # pcie_mac default (the phy keeps it)
N_FTS = 32                     # pcie_mac default
L1_IDLE, L2_IDLE = 0x31, 0x34  # ltssm_state_o codes (mac HAS, LTSSM page)
LOOPBACK_ECHO_MIN = 1.9        # pattern symbols echoed per held cycle (2 per cycle, see e2e_loopback)
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
    for p in env.ports:
        p.request(pm_state=1)
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
    COV_POWER.sample(state="l0s")
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
            assert all(any(c in RECOVERY_CODES for c in env.codes[i][-8:]) for i in (0, 1)), \
                "L1 exit: not through Recovery"
        else:
            assert downs == [True, True], "L2 exit: link_up_o did not fall"
        env.log.info("pm_state 0 from %#x: both in L0 after %d cycles", idle, n)
        COV_POWER.sample(state="l1" if req == 2 else "l2")
        await env.lossless(12)
    env.final_checks()


async def directed_round(env: Env, port: int, req: str, state: int, hold: int = 2000,
                         traffic: bool = True) -> None:
    """``req`` held high on ``port`` (while packets flow, with ``traffic``): both ports reach
    ``state`` with link_up_o low, stay there while it is held, then train
    back to L0 when it drops (through Detect); lossless after. For
    loopback, ``port`` is the master: the master's data pattern in
    Loopback.Active is its traffic, and ``LoopbackScoreboard`` checks that
    every symbol it receives there is its own, echoed in order, with at
    least ``LOOPBACK_ECHO_MIN`` of ``hold`` echoed pattern symbols."""
    downs = [False, False]
    echo = LoopbackScoreboard(env.ports[port], f"loopback master {'ab'[port]}") \
        if req == "loopback" else None

    def watch(bench, j):
        downs[j] |= not bench.ports[j].link_up

    env.bench.hooks.append(watch)
    if traffic:
        env.send(0, 8)
        env.send(1, 8)
        await ClockCycles(env.dut.a_clk_i, 40, rising=False)
    if echo:
        echo.start()
    env.ports[port].request(**{req: 1})
    n = await env.until(lambda: all(p.code == state for p in env.ports), 5000, f"{req}: both in {state:#x}")
    env.log.info("%s on port %s: both in %#x after %d cycles", req, "ab"[port], state, n)
    for _ in range(hold // 100):
        await ClockCycles(env.dut.a_clk_i, 100, rising=False)
        assert all(p.code == state and not p.link_up for p in env.ports), (
            f"{req} held: expected both in {state:#x} with link_up_o low, observed codes "
            f"{[hex(p.code) for p in env.ports]}, link_up {[p.link_up for p in env.ports]}")
    env.ports[port].request(**{req: 0})
    n = await env.until(env.bench.both_l0, 50_000, f"{req} released: back to L0")
    if echo:
        echo.stop()
        got = echo.check(min_pattern=int(LOOPBACK_ECHO_MIN * hold))
        env.log.info("loopback master %s: %d symbols received in Loopback.Active (edges %d to %d), "
                     "all its own in order; %d pattern symbols", "ab"[port], got["symbols"],
                     got["first_edge"], got["last_edge"], got["pattern"])
    env.bench.hooks.remove(watch)
    assert downs == [True, True], f"{req}: link_up_o did not fall on both ports"
    assert all(0x00 in c[-12:] or 0x01 in c[-12:] for c in env.codes), f"{req}: not through Detect"
    env.log.info("%s released: both in L0 after %d cycles", req, n)
    if req == "loopback":
        COV_POWER.sample(state="loopback")
    else:
        COV_CONTROL.sample(event=req)
    await env.settle()
    for s in env.sb:
        s.next = len(s.sent)
    await env.lossless(12)


@cocotb.test()
async def e2e_control(dut):
    """L1, RTL, ERR_TIMEOUT_DIV 1024, end to end through the link-control
    inputs (HAS Link-up and requests):
    - retrain_req_i on each port: both through Recovery back to L0 within
      4 us, link_up_o high throughout, lossless after;
    - hot_reset_req_i and disable_req_i on the upstream port: ignored for
      4000 cycles (both stay in L0, traffic lossless);
    - hot_reset_req_i on the downstream port: both in Hot Reset with
      link_up_o low while held, then Detect and back to L0;
    - disable_req_i on the downstream port: the same through Disabled."""
    env = await rtl_env(dut, "upstream", 300)
    assert env.err_timeout_div == 1024, "e2e_control needs ERR_TIMEOUT_DIV 1024"
    dsp, usp = downstream(env), 1 - downstream(env)
    await env.lossless(8)
    for i in (0, 1):
        mark = [len(c) for c in env.codes]
        downs = [0, 0]

        def watch(bench, j):
            downs[j] += not bench.ports[j].link_up

        env.bench.hooks.append(watch)
        env.ports[i].retrain()
        n = await poll_until(env, lambda: all(any(c in RECOVERY_CODES for c in env.codes[j][mark[j]:])
                                              for j in (0, 1)) and env.bench.both_l0(), 500,
                             f"retrain on {'ab'[i]}: both through Recovery to L0")
        env.bench.hooks.remove(watch)
        assert downs == [0, 0], (
            f"retrain on {'ab'[i]}: link_up_o fell in Recovery ({downs} cycles low on a, b)")
        env.log.info("retrain on %s: both back in L0 after %d cycles", "ab"[i], n)
        COV_CONTROL.sample(event="retrain")
        await env.lossless(8)
    env.ports[usp].request(hot_reset=1, disable=1)
    env.send(0, 16)
    env.send(1, 16)
    await stays_up(env, 4000, "upstream hot_reset_req_i and disable_req_i")
    assert env.bench.both_l0(), "upstream requests: left L0"
    COV_CONTROL.sample(event="usp_requests_ignored")
    env.ports[usp].request(hot_reset=0, disable=0)
    await env.drain()
    await env.lossless(8)
    await directed_round(env, dsp, "hot_reset", HOT_RESET)
    await directed_round(env, dsp, "disable", DISABLED)
    env.final_checks()


@cocotb.test()
async def e2e_loopback(dut):
    """L1, RTL, ERR_TIMEOUT_DIV 1024: loopback_req_i on port a (the
    master), then on port b: both reach Loopback.Active with link_up_o low and stay while it
    is held; when it drops the master leaves through Loopback.Exit, the
    slave follows on its EIOS, and both pass through Detect back to L0;
    lossless after each. In Loopback.Active the master sends its data
    pattern (two symbols a cycle) and the slave echoes it: every symbol
    the master receives there is one it sent, in order and unchanged (SKP
    symbols aside), and at least 1.9 pattern symbols per held cycle come
    back (the rest of the 2 per cycle covers SKP ordered sets and the
    training sets still returning at entry). Packets flow into each entry,
    as in e2e_control: a packet of the master's own that came back to it
    would fail its scoreboard, as not sent by the far port or as
    corrupted."""
    env = await rtl_env(dut, "downstream", -300)
    assert env.err_timeout_div == 1024, "e2e_loopback needs ERR_TIMEOUT_DIV 1024"
    await env.lossless(8)
    for i in (0, 1):
        await directed_round(env, i, "loopback", LOOPBACK_ACTIVE)
    env.final_checks()


# ---------------------------------------------- RTL: HAS targets

TX_LATENCY_MAX = 6             # HAS Targets: accepted beat to its first symbol
RX_LATENCY_MAX = 16            # HAS Targets: first symbol in to its sop beat
# The stack as built: the MAC's transmit word is combinational (mac MAS,
# zero latency inside its budget of 3) and the PCS takes 2 (pcs HAS).
TX_LATENCY_STACK = 0 + 2


def throughput_floor(n: int) -> float:
    """HAS Targets: back-to-back packets of n bytes, framing plus a SKP
    ordered set in every 1180 symbols."""
    return 2 * n / (n + 2) * 1176 / 1180


def sized_packet(seq: int, nbytes: int, rng: random.Random) -> tuple:
    """(dllp, data, nullify): a 6-byte DLLP or a TLP of ``nbytes`` (2 +
    header + data + 4), with the stub's sequence number and check code."""
    import binascii
    head = (seq & 0xFFFF).to_bytes(2, "little")
    if nbytes == 6:
        body = head + rng.randbytes(2)
        return True, body + binascii.crc_hqx(body, 0xFFFF).to_bytes(2, "little"), False
    body = head + rng.randbytes(nbytes - 6)
    return False, body + binascii.crc32(body).to_bytes(4, "little"), False


async def burst(env: Env, nbytes: int, count: int) -> list:
    """``count`` back-to-back packets of ``nbytes`` each way, traced and
    lossless. Per port (a, b) returns a dict: ``beats`` accepted and
    ``cycles`` from the first sop to the last eop taken; ``skps``, the SKP
    ordered sets sent in that window (shifted by the TX latency);
    ``tx_lat``, each packet's TX latency; ``rx_ps``, each frame start to
    the far port's sop, in ps."""
    await env.settle(200)
    for s in env.sb:
        s.mark()
    for p in env.ports:
        p.trace = True
        for log in (p.tx_log, p.tx_starts, p.tx_skps, p.rx_sops):
            log.clear()
    for i, p in enumerate(env.ports):
        for _ in range(count):
            pkt = sized_packet(env.seq[i], nbytes, env.rng)
            env.seq[i] += 1
            env.sb[i].add(pkt)
            p.send(*pkt)
    await env.drain()
    for s in env.sb:
        s.check_lossless()
    out = []
    for i, p in enumerate(env.ports):
        p.trace = False
        beats = p.tx_log
        sops = [b for b in beats if b[1]]
        eops = [b for b in beats if b[2]]
        assert len(sops) == count and len(eops) == count, (
            f"port {'ab'[i]}: expected {count} packets taken, observed {len(sops)} sop {len(eops)} eop")
        first, last = sops[0][0], eops[-1][0]
        starts = p.tx_starts[:count]
        assert len(starts) == count, f"port {'ab'[i]}: {len(p.tx_starts)} frame starts on the line"
        skps = [e for e in p.tx_skps if first + TX_LATENCY_STACK < e <= last + TX_LATENCY_STACK]
        far = env.ports[1 - i]
        out.append({"beats": len(beats), "cycles": last - first + 1, "skps": len(skps),
                    "tx_lat": [st[0] - sp[0] for sp, st in zip(sops, starts)],
                    "rx_ps": [rs[1] - st[1] for st, rs in zip(starts, far.rx_sops[:count])]})
    return out


@cocotb.test()
async def e2e_targets(dut):
    """L1, RTL over the RTL channel, b +600 ppm: the HAS targets on the
    RTL stack.
    - Throughput, each way, 160 back-to-back 274-byte TLPs and 600 DLLPs:
      the HAS floor 2 n/(n+2) 1176/1180 (1.979 and 1.495, >= 1.97 and
      1.49) checked free of the window's SKP phase. From the first sop to
      the last eop accepted, the cycles without a beat are exactly one
      framing cycle per packet boundary plus two per SKP ordered set sent,
      and the SKP ordered sets are no more than the 1180-symbol schedule
      allows in that window.
    - TX latency: every packet's STP or SDP on ser_tx_data_o (with its
      first byte in [19:10]) exactly TX_LATENCY_STACK cycles after its sop
      beat is accepted, and at most 6 (HAS).
    - RX latency: from a frame start entering the far port's ser_rx_data_i
      (one channel register after it leaves) to its sop beat, at most 16
      cycles (HAS), the elastic buffer at its nominal fill."""
    env = Env(dut, a_role="downstream", ppm=600, seed=seed())
    assert env.layers == {"mac": "rtl", "pcs": "rtl", "channel": "rtl"}, "e2e_targets needs all layers RTL"
    env.start()
    await env.train()
    await env.lossless(8)
    for nbytes, count, target in ((274, 160, 1.97), (6, 600, 1.49)):
        floor = throughput_floor(nbytes)
        for i, r in enumerate(await burst(env, nbytes, count)):
            # The far clock's period: b is 600 ppm slower.
            far_ps = 8000 * (1 + 600e-6) if i == 0 else 8000
            rx_cyc = [(t - 8000) / far_ps for t in r["rx_ps"]]
            moved, cycles, k = 2 * r["beats"], r["cycles"], r["skps"]
            gaps = cycles - r["beats"]
            env.log.info("%d-byte packets from %s: %d bytes in %d cycles (%.4f bytes/cycle, HAS floor "
                         "%.4f), %d SKP ordered sets, %d cycles without a beat; TX latency %s; RX "
                         "latency at %s %.2f to %.2f cycles", nbytes, "ab"[i], moved, cycles,
                         moved / cycles, floor, k, gaps, sorted(set(r["tx_lat"])), "ba"[i],
                         min(rx_cyc), max(rx_cyc))
            where = f"{nbytes}-byte packets from {'ab'[i]}"
            # The HAS throughput, phase-free: (1) back to back, the only
            # cycles without a beat are one framing cycle between packets
            # and two per SKP ordered set; (2) SKP ordered sets fall due
            # every 1180 symbols (590 cycles) and one due inside a packet
            # goes out after it, so the window holds at most the due times
            # in it, widened by one packet. Together they give at least the
            # HAS floor over whole SKP intervals, whatever the window's
            # phase, with no tolerance on either.
            assert gaps == (count - 1) + 2 * k, (
                f"{where}: expected {count - 1} framing cycles + 2 x {k} SKP cycles without a beat, "
                f"observed {gaps}")
            skp_max = (cycles + nbytes // 2 + 1) // 590 + 1
            assert k <= skp_max, f"{where}: {k} SKP ordered sets in {cycles} cycles, expected at most {skp_max}"
            assert floor >= target, f"{where}: HAS floor {floor:.4f} below {target}"
            assert set(r["tx_lat"]) == {TX_LATENCY_STACK} and TX_LATENCY_STACK <= TX_LATENCY_MAX, (
                f"TX latency from {'ab'[i]}: expected {TX_LATENCY_STACK} cycles for every packet, "
                f"observed {sorted(set(r['tx_lat']))}")
            assert len(rx_cyc) == count and max(rx_cyc) <= RX_LATENCY_MAX, (
                f"RX latency at {'ba'[i]}: expected at most {RX_LATENCY_MAX} cycles, observed "
                f"up to {max(rx_cyc):.2f}")
    env.final_checks()


@cocotb.test()
async def e2e_link_up_spec(dut):
    """L2, RTL over the RTL channel, TIMEOUT_DIV 1 (silicon timeouts),
    simultaneous reset: each port's rst_i release to link_up_o within
    12.0 to 12.2 ms, and Polling.Active entry to L0 within 65.536 to 80 us
    (HAS Targets). No Python runs per cycle: the clocks are simulator
    clocks and the test waits on link_up_o and ltssm_state_o edges."""
    from cocotb.clock import Clock
    from cocotb.triggers import Edge, First, Timer
    from cocotb.utils import get_sim_time
    assert int(dut.cfg_timeout_div_o.value) == 1 and int(dut.cfg_chan_rtl_o.value) == 1, \
        "e2e_link_up_spec needs TIMEOUT_DIV 1 and CHAN_RTL 1"
    assert int(dut.cfg_mac_rtl_o.value) == 1 and int(dut.cfg_pcs_rtl_o.value) == 1
    for p in "ab":
        for name in ("pkt_tx_valid_i", "pkt_tx_data_i", "pkt_tx_sop_i", "pkt_tx_eop_i",
                     "pkt_tx_dllp_i", "pkt_tx_nullify_i", "retrain_req_i", "hot_reset_req_i",
                     "disable_req_i", "loopback_req_i", "pm_state_req_i", "ser_tx_rxdet_i",
                     "ser_rx_data_i", "ser_rx_elec_idle_i"):
            getattr(dut, f"{p}_{name}").value = 0
        getattr(dut, f"{p}_rst_i").value = 1
    dut.a_port_role_i.value = 0
    dut.b_port_role_i.value = 1
    cocotb.start_soon(Clock(dut.a_clk_i, 8000, unit="ps").start())
    await Timer(2960, "ps")
    cocotb.start_soon(Clock(dut.b_clk_i, 8000, unit="ps").start())
    await ClockCycles(dut.a_clk_i, 8, rising=False)
    dut.a_rst_i.value = 0
    dut.b_rst_i.value = 0
    await ClockCycles(dut.a_clk_i, 1)          # the first rising edge out of reset
    t0 = get_sim_time("ps")
    poll = {}
    up = {}
    end = t0 + 12_400_000_000
    while len(up) < 2:
        left = end - get_sim_time("ps")
        assert left > 0, f"link-up: not both up by 12.4 ms (up {sorted(up)})"
        deadline = Timer(left, "ps")
        ev = await First(Edge(dut.a_ltssm_state_o), Edge(dut.b_ltssm_state_o),
                         Edge(dut.a_link_up_o), Edge(dut.b_link_up_o), deadline)
        assert ev is not deadline, f"link-up: not both up by 12.4 ms (up {sorted(up)})"
        now = get_sim_time("ps")
        for p in "ab":
            if p not in poll and int(getattr(dut, f"{p}_ltssm_state_o").value) == 0x04:
                poll[p] = now
            if p not in up and int(getattr(dut, f"{p}_link_up_o").value):
                up[p] = now
    for p in "ab":
        lu_us = (up[p] - t0) / 1e6
        tr_us = (up[p] - poll[p]) / 1e6
        dut._log.info("port %s: rst_i release to link_up_o %.3f us, Polling.Active to L0 %.3f us",
                      p, lu_us, tr_us)
        assert 12_000 <= lu_us <= 12_200, f"port {p}: link-up {lu_us:.3f} us, expected 12000 to 12200 us"
        assert 65.536 <= tr_us <= 80, f"port {p}: training {tr_us:.3f} us, expected 65.536 to 80 us"


@cocotb.test()
async def e2e_polarity(dut):
    """L1, RTL, b -300 ppm: the a-to-b lane inverted from reset (HAS
    PIPE Polarity): port b's receiver finds the inversion in training and
    sets RxPolarity; both reach L0 with the link-up targets, and traffic
    is lossless both ways. Then a reset of both ports retrains with the
    inversion still there."""
    env = Env(dut, a_role="downstream", ppm=-300, seed=seed())
    assert env.layers["mac"] == "rtl" and env.bench.channels is not None
    env.bench.channels[0].invert = True
    env.start()
    await env.train()
    env.check_link_up(max_us=100.0)
    pol = int(dut.u_b.pipe_rx_polarity.value), int(dut.u_a.pipe_rx_polarity.value)
    assert pol == (1, 0), f"RxPolarity (b, a): expected (1, 0), observed {pol}"
    COV_CONTROL.sample(event="polarity")
    await env.lossless(30)
    await env.reset_ports()
    await env.lossless(12)
    env.final_checks()
