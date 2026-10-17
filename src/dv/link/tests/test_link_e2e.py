"""Back-to-back e2e of two pcie_link stacks (wiki/src/dv/link/DV_PLAN.md).

End a (downstream) and end b (upstream) on the top pcie_link_e2e_tb, joined
by the line injector and the channel model (tb/link_e2e_env.py). Every
test brings both ends from reset to DL_Active, runs the HAS traffic mix
both ways under its injection, drains, and passes the environment's final
checks: the scoreboards, the credits, the error pulses, the link, the
boundary Ack latency and the partner's passive checker. The directed
tests also check each injected class's expected pulses (HAS, checks).
"""

import random

import cocotb

from tb.link_e2e_env import (BAD_DLLP, BAD_TLP, CORE_PS, COV_CREDIT, COV_LINK, COV_RECOVERY,
                             ROLLOVER, TIMEOUT, Env, now_ps)

REPLAY_TIMEOUT = 356                 # dll RETRY page, timers


def seed() -> int:
    return cocotb.RANDOM_SEED


async def up(dut, **kw) -> Env:
    env = Env(dut, seed(), **kw)
    env.start()
    await env.link_up()
    return env


def ends(env, d):
    """(sender, receiver) of direction ``d``."""
    return (env.ends[0], env.ends[1]) if d == "ab" else (env.ends[1], env.ends[0])


def rev(d):
    return d[::-1]


def naks(env, d, since):
    """Naks the receiver of ``d`` sent (reverse sent view) since ``since``."""
    return [p for p in env.chk.views[("sent", rev(d))]
            if p.dllp and p.start >= since and p.data[0] == 0x10]


async def settle(env, d, cycles=1500):
    """Let direction ``d`` deliver everything, then cover a replay timeout."""
    await env.until(lambda: env.sb[d].outstanding() == 0 and env.ends[0].tx_idle()
                    and env.ends[1].tx_idle(), 30_000, f"{d} delivered")
    await env.cycles(cycles)


def delta(end, bit, since):
    return end.pulses(bit, since)


def assert_quiet(env, why=""):
    """An error-free run: no Bad TLP, Bad DLLP, replay timeout or rollover
    on dl_err_o, no rcv_err_o, and neither end ever left L0 (DV BKM: a
    check the test could fail on)."""
    for e in env.ends:
        bits = {n: e.err[b] for n, b in (("bad_tlp", BAD_TLP), ("bad_dllp", BAD_DLLP),
                                          ("replay_timeout", TIMEOUT), ("rollover", ROLLOVER))}
        assert not any(bits.values()) and e.rcv_err == 0 and e.l0_leaves == 0, (
            f"end {e.name} in an error-free run{why}: dl_err_o {bits}, rcv_err_o "
            f"{e.rcv_err}, left L0 {e.l0_leaves} times")


def trace(env, d, since):
    """The injections and error pulses since ``since``, for a failure message."""
    cyc = lambda t: round(t / CORE_PS, 1)  # noqa: E731
    inj = [(cyc(i.time), i.direction, i.cls, i.what) for i in env.chk.log if i.time >= since]
    pulses = {e.name: [(cyc(t), bin(v)) for t, v in e.err_log if t >= since] for e in env.ends}
    return f"injections {inj}; dl_err_o pulses {pulses}"


async def directed(env, d, cls, traffic=30):
    """One injection of ``cls`` on ``d`` with traffic both ways; returns the
    injection time and the time window start."""
    s, r = ends(env, d)
    t0 = now_ps()
    s.send(env.tlps(env.ends.index(s), traffic))
    r.send(env.tlps(env.ends.index(r), traffic // 2))
    await env.cycles(40)
    t = await env.inject(d, cls)
    await settle(env, d)
    return t0, t


# ------------------------------------------------------------------- L0

@cocotb.test()
async def e2e_smoke(dut):
    """L0: reset values, link-up to DL_Active, 60 TLPs of the mix each way,
    lossless and in order, every final check."""
    env = await up(dut)
    env.send(0, 60)
    env.send(1, 60)
    await env.drain()
    await env.cycles(500)
    env.finish()
    assert_quiet(env)


# ------------------------------------------------------- error classes

@cocotb.test()
async def e2e_tlp_errors(dut):
    """LCRC error, corrupted TLP and dropped TLP, each way: Bad TLP (and
    rcv_err for an invalid symbol) at the receiver and replay; a dropped
    TLP with a TLP behind it gives Bad TLP and a Nak, the last TLP of a
    burst the sender's replay timeout."""
    env = await up(dut)
    for d in ("ab", "ba"):
        s, r = ends(env, d)
        for cls in ("lcrc", "corrupt_tlp"):
            rcv = r.rcv_err
            t0, _ = await directed(env, d, cls)
            assert delta(r, BAD_TLP, t0) >= 1, f"{cls} on {d}: no Bad TLP at the receiver"
            if cls == "corrupt_tlp":
                assert r.rcv_err > rcv, f"corrupt_tlp on {d}: no rcv_err at the receiver"
            else:
                assert r.rcv_err == rcv, f"lcrc on {d}: rcv_err {r.rcv_err - rcv}, expected none"
            assert len(naks(env, d, t0)) >= 1, f"{cls} on {d}: no Nak"
            COV_RECOVERY.sample(event="nak_sent")
        # A dropped TLP with more behind it: the next arrives ahead.
        t0, t = await directed(env, d, "drop_tlp")
        assert delta(r, BAD_TLP, t0) >= 1 and naks(env, d, t0), (
            f"drop_tlp on {d} inside a burst: expected Bad TLP and a Nak")
        # The last TLP of a burst dropped: nothing follows; the timeout replays it.
        await env.cycles(200)
        t0 = now_ps()
        req = env.injectors[0 if d == "ab" else 1]
        req.request("drop_tlp")
        s.send(env.tlps(env.ends.index(s), 1))
        await env.until(req.idle, 5000, "drop_tlp applied")
        await settle(env, d)
        assert delta(s, TIMEOUT, t0) >= 1, f"last TLP dropped on {d}: no replay timeout"
        assert delta(r, BAD_TLP, t0) == 0, f"last TLP dropped on {d}: unexpected Bad TLP"
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_dllp_errors(dut):
    """Corrupted DLLP (Bad DLLP) and dropped DLLP (no pulse), each way; the
    Nak class (a lost Nak and a corrupted TLP of the replay: one Nak only,
    the replay timeout twice); the duplicate class (Acks dropped until the
    sender replays: no pulse at the receiver)."""
    env = await up(dut)
    for d in ("ab", "ba"):
        s, r = ends(env, d)
        t0, _ = await directed(env, d, "corrupt_dllp")
        assert delta(r, BAD_DLLP, t0) >= 1, f"corrupt_dllp on {d}: no Bad DLLP"
        t0, _ = await directed(env, d, "drop_dllp")
        assert delta(r, BAD_DLLP, t0) == 0 and delta(r, BAD_TLP, t0) == 0, (
            f"drop_dllp on {d}: expected no pulse at the receiver")
        # Nak: the receiver's Nak (reverse direction) lost, then the first
        # replayed TLP corrupted.
        t0 = now_ps()
        env.injectors[1 if d == "ab" else 0].request("nak")
        s.send(env.tlps(env.ends.index(s), 30))
        r.send(env.tlps(env.ends.index(r), 15))
        await env.cycles(40)
        await env.inject(d, "lcrc")
        bad = int(env.chk.log[-1].what.split()[2])
        env.injectors[0 if d == "ab" else 1].request("replay_tlp", seq=bad)
        await env.until(lambda: all(i.idle() for i in env.injectors), 10_000, "nak class applied")
        await settle(env, d)
        why = trace(env, d, t0)
        assert len(naks(env, d, t0)) == 1, (
            f"nak class on {d}: the receiver sent {len(naks(env, d, t0))} Naks, expected 1 "
            f"(NAK_SCHEDULED stays set through the corrupted replay); {why}")
        assert delta(s, TIMEOUT, t0) >= 2, (
            f"nak class on {d}: {delta(s, TIMEOUT, t0)} replay timeouts, expected at least 2; {why}")
        assert delta(r, BAD_TLP, t0) >= 2
        # Duplicate: Acks dropped long enough for the sender to replay.
        t0 = now_ps()
        s.send(env.tlps(env.ends.index(s), 20))
        await env.cycles(30)
        env.injectors[1 if d == "ab" else 0].drop_acks = True
        await env.cycles(600)
        env.injectors[1 if d == "ab" else 0].drop_acks = False
        await settle(env, d)
        assert delta(s, TIMEOUT, t0) >= 1, f"duplicate on {d}: no replay timeout"
        assert delta(s, ROLLOVER, t0) == 0
        assert delta(r, BAD_TLP, t0) == 0 and delta(r, BAD_DLLP, t0) == 0, (
            f"duplicate on {d}: expected no pulse at the receiver")
        COV_RECOVERY.sample(event="duplicate_acked")
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_rollover(dut):
    """Every TLP one way dropped until the sender rolls REPLAY_NUM over:
    exactly 4 replay timeouts and 1 rollover at the sender, no Bad TLP at
    the receiver, a retrain (both ends leave L0 and return, link_up_o and
    DL_Active kept), then traffic resumes and drains. Each way."""
    env = await up(dut)
    for d in ("ab", "ba"):
        s, r = ends(env, d)
        inj = env.injectors[0 if d == "ab" else 1]
        await env.cycles(200)
        t0, leaves = now_ps(), [e.l0_leaves for e in env.ends]
        inj.drop_tlps = True
        s.send(env.tlps(env.ends.index(s), 40))
        r.send(env.tlps(env.ends.index(r), 10))
        await env.until(lambda: delta(s, ROLLOVER, t0) >= 1, 4000, f"rollover on {d}")
        inj.drop_tlps = False
        await env.until(lambda: all(e.l0() for e in env.ends)
                        and all(e.l0_leaves > leaves[i] for i, e in enumerate(env.ends)),
                        20_000, f"retrain after the rollover on {d}")
        await settle(env, d)
        assert delta(s, TIMEOUT, t0) == 4, f"rollover on {d}: {delta(s, TIMEOUT, t0)} timeouts, expected 4"
        assert delta(s, ROLLOVER, t0) == 1
        assert delta(r, BAD_TLP, t0) == 0, f"rollover on {d}: Bad TLP at the receiver"
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_line(dut):
    """The phy channel's impairments each way while traffic flows: bit
    flips and a slip (rcv_err at the receiver), and an electrical-idle
    dropout of up to 1000 cycles; any Recovery returns to L0 with
    link_up_o and DL_Active kept, and nothing is lost."""
    env = await up(dut)
    rng = random.Random(seed() ^ 0x11E)
    for d in ("ab", "ba"):
        s, r = ends(env, d)
        for kind in ("flip", "slip", "idle"):
            rcv = r.rcv_err
            s.send(env.tlps(env.ends.index(s), 30))
            r.send(env.tlps(env.ends.index(r), 15))
            await env.cycles(60)
            env.line(d, kind, 3 if kind == "flip" else rng.randint(200, 1000))
            await env.until(lambda: all(e.l0() for e in env.ends), 30_000, f"{kind}: back in L0")
            await settle(env, d, 2000)
            if kind != "idle":
                assert r.rcv_err > rcv, f"{kind} on {d}: no rcv_err at the receiver"
    await env.drain()
    env.finish()


@cocotb.test()
async def e2e_mixed(dut):
    """Every per-packet class at random both ways, the clocks ``ppm`` apart
    (drawn ±600 from the seed): lossless, and the error pulses at least the
    injected corruptions."""
    rng = random.Random(seed() ^ 0x313)
    env = await up(dut, ppm=rng.choice((600, -600)))
    for inj in env.injectors:
        inj.rates.update(lcrc=0.01, corrupt_tlp=0.005, drop_tlp=0.005, corrupt_dllp=0.01,
                         drop_dllp=0.01)
    env.send(0, 300)
    env.send(1, 300)
    await env.drain()
    for inj in env.injectors:
        inj.rates.update(dict.fromkeys(inj.rates, 0.0))
    await env.cycles(1500)
    stats = env.finish()
    for d in ("ab", "ba"):
        s, r = ends(env, d)
        c = env.injectors[0 if d == "ab" else 1].counts
        assert r.err[BAD_TLP] >= c["lcrc"] + c["corrupt_tlp"], (d, r.err, c)
        assert r.err[BAD_DLLP] >= c["corrupt_dllp"], (d, r.err, c)
    env.log.info("mixed: %s", stats["injected"])


# ------------------------------------------------------ credit configurations

async def credit_run(dut, n=120, stalls=(4000, 10000)):
    """The mix both ways; each end whose classes are all finite takes 30%
    of the beats and stalls for 4000 and then 10000 cycles (starvation,
    then recovery through UpdateFC); an end with an infinite class never
    stalls. Drained, every finite credit is back at the advertisement."""
    env = await up(dut)
    stallers = [e for e in env.ends if not e.any_inf]
    for e in stallers:
        e.take_p = 0.3
    env.send(0, n)
    env.send(1, n)
    for length in stalls:
        await env.cycles(1500)
        for e in stallers:
            e.stall_until = e.cycle + length
        if stallers:
            COV_CREDIT.sample(stall=length)
        await env.cycles(length + 500)
        env.send(0, n // 3)
        env.send(1, n // 3)
    for e in stallers:
        e.take_p = 1.0
    await env.drain()
    await env.cycles(2500)          # the last UpdateFCs arrive
    stats = env.finish()
    # Starvation and stalls are not errors: nothing is lost or replayed.
    assert_quiet(env, f" ({env.credits} credits)")
    for e in env.ends:
        if stallers:
            env.log.info("end %s starved: %s, sop waits %d", e.name, sorted(e.starved), e.sop_wait)
    return env, stats


@cocotb.test()
async def e2e_credits(dut):
    """Credit starvation and recovery in the top's credit configuration
    (CREDITS): no receiver overflow, credits available within the far
    end's advertisement, and back at it once drained."""
    env, _ = await credit_run(dut)
    if env.credits in ("minimum", "finite"):
        for e in env.ends:
            assert e.starved, f"end {e.name}: never waited on credits in {env.credits}"
    elif env.credits == "asymmetric":
        # b sends into a's minimum credits.
        assert env.ends[1].starved, "end b: never waited on a's minimum credits (asymmetric)"


# ------------------------------------------------------------------- L2

@cocotb.test()
async def e2e_soak(dut):
    """L2: the mixed classes, line flips and the credit stalls together,
    1000 TLPs each way, ppm drawn from the seed."""
    rng = random.Random(seed() ^ 0x50A)
    env = await up(dut, ppm=rng.uniform(-600, 600))
    for inj in env.injectors:
        inj.rates.update(lcrc=0.005, corrupt_tlp=0.003, drop_tlp=0.003, corrupt_dllp=0.005,
                         drop_dllp=0.005)
    stallers = [e for e in env.ends if not e.any_inf]
    for e in stallers:
        e.take_p = 0.5
    for _ in range(4):
        env.send(0, 250)
        env.send(1, 250)
        await env.cycles(rng.randint(2000, 6000))
        env.line(rng.choice(("ab", "ba")), "flip", 2)
        for e in stallers:
            e.stall_until = e.cycle + rng.choice((4000, 10000))
    for e in stallers:
        e.take_p = 1.0
    await env.drain(600_000)
    for inj in env.injectors:
        inj.rates.update(dict.fromkeys(inj.rates, 0.0))
    await env.cycles(2500)
    env.finish()


# ------------------------------------------------------------ stack wiring

async def link_control(dut, request: str):
    """A control request on the downstream end a, passed through to the
    phy, while TLPs flow both ways: both ends' link_up_o falls (the LTSSM
    leaves for Hot Reset, Disabled or Loopback), the dll outputs hold their
    reset values while it is 0, TLPs not yet delivered are dropped; after
    the request is released the link trains again, both ends return to
    DL_Active, and new traffic is lossless."""
    env = await up(dut)
    env.send(0, 80)
    env.send(1, 80)
    await env.cycles(1500)
    env.expect_down = True
    a = env.ends[0]
    a.s[f"{request}_req_i"].value = 1
    await env.until(lambda: not any(e.link_up for e in env.ends), 20_000,
                    f"{request}: link_up_o falls at both ends")
    await env.cycles(400)
    a.s[f"{request}_req_i"].value = 0
    await env.until(lambda: all(e.live for e in env.ends), 60_000, f"{request}: DL_Active again")
    env.expect_down = False
    assert env.link_downs == 2
    env.send(0, 40)
    env.send(1, 40)
    await env.drain()
    await env.cycles(500)
    stats = env.finish()
    # Not vacuous after the relink: the passive checker timed the Acks of
    # the receiver's second epoch (the out-of-L0 range closes at the
    # link-down, so no exemption covers the traffic after it).
    for d in ("ab", "ba"):
        n = env.chk.stats.get(f"ack_timing_{d}_epoch1", 0)
        assert n > 0, f"{request}: no Ack timed on {d} after the relink"
    COV_LINK.sample(request=request)
    env.log.info("%s: lost at the link-down %s", request, stats["lost_at_link_down"])


@cocotb.test()
async def e2e_hot_reset(dut):
    """Hot Reset passed through (HAS, stack wiring)."""
    await link_control(dut, "hot_reset")


@cocotb.test()
async def e2e_disable(dut):
    """Disabled passed through (HAS, stack wiring)."""
    await link_control(dut, "disable")


@cocotb.test()
async def e2e_loopback(dut):
    """Loopback passed through (HAS, stack wiring)."""
    await link_control(dut, "loopback")
