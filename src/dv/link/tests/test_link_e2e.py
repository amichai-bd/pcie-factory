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
import sys
from pathlib import Path

import cocotb

from tb.link_e2e_env import (BAD_DLLP, BAD_TLP, CORE_PS, COV_CREDIT, COV_LINK, COV_RECOVERY,
                             ROLLOVER, TIMEOUT, Env, now_ps, ptn)

sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "link"))
import pcie_link_arch as arch  # noqa: E402  (shadow figures only; DV may import arch)
import pcie_link_throughput_model as thr  # noqa: E402

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
    stalls. In the release configuration each end takes every beat but
    releases in 30% of cycles and, in those windows, not at all. Drained,
    every finite credit is back at the advertisement."""
    env = await up(dut)
    stallers = [e for e in env.ends if not e.any_inf]
    releasers = [e for e in env.ends if e.rel]
    for e in stallers:
        e.take_p = 0.3
    for e in releasers:
        e.rel_p = 0.3
    env.send(0, n)
    env.send(1, n)
    for length in stalls:
        await env.cycles(1500)
        for e in stallers:
            e.stall(length)
        for e in releasers:
            e.rel_stall(length)
        if stallers or releasers:
            COV_CREDIT.sample(stall=length)
        await env.cycles(length + 500)
        env.send(0, n // 3)
        env.send(1, n // 3)
    for e in stallers:
        e.take_p = 1.0
    for e in releasers:
        e.rel_p = 1.0
    await env.drain()
    if releasers:
        await held_posted_headers(env)
    await env.cycles(2500)          # the last UpdateFCs arrive
    stats = env.finish()
    # Starvation and stalls are not errors: nothing is lost or replayed.
    assert_quiet(env, f" ({env.credits} credits)")
    for e in env.ends:
        if stallers:
            env.log.info("end %s starved: %s, sop waits %d", e.name, sorted(e.starved), e.sop_wait)
    return env, stats


async def held_posted_headers(env, n=12):
    """Release configuration, drained: each end stops releasing and the far
    end sends ``n`` one-DW memory writes. The end holds every posted header
    credit it advertises while posted data credits are left (one each), so
    the far end's writes wait on headers alone, the gate the release port
    sets through link; once releases resume, the rest are delivered."""
    for e in env.ends:
        e.rel_stall_until = 1 << 62
    for i in (0, 1):
        env.send(i, n, kinds=("MWr",), dw=1)
    await env.until(lambda: all(e.held[0][0] == e.fc[0] for e in env.ends), 5000,
                    "every posted header credit held")
    await env.cycles(500)
    for i, e in enumerate(env.ends):
        far = env.ends[1 - i]
        assert e.held[0][0] == e.fc[0] and e.held[0][1] < e.fc[1], (
            f"end {e.name}: holds {e.held[0]} posted credits, advertised {e.fc[:2]}")
        assert not far.tx_idle() and far.avail[0] == 0 and far.avail[1] > 0, (
            f"end {far.name}: writes not waiting on posted headers alone (queued "
            f"{len(far.tx_q)}, P avail {far.avail[:2]})")
    for e in env.ends:
        e.rel_stall_until = e.cycle
    await env.drain()


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
    elif env.credits == "release":
        # Each end held all of a class's header credits unreleased, and the
        # far end waited on them: the release port, through link, set the pace.
        for e in env.ends:
            full = [c for c in (0, 1) if e.held_max[c][0] == e.fc[2 * c]]
            assert full and all(e.released), (f"end {e.name}: held at most {e.held_max}, "
                                              f"released {e.released}")
            assert e.starved, f"end {e.name}: never waited on the far end's held credits"
            env.log.info("end %s: held at most %s, released %s", e.name, e.held_max, e.released)


# ------------------------------------------------------------------- L2

@cocotb.test()
async def e2e_soak(dut):
    """L2: the mixed classes, line flips and the credit stalls together,
    1000 TLPs each way, ppm drawn from the seed. In the release
    configuration the stalls are release stalls: each end takes every beat
    but releases in half the cycles, and none in the stall windows."""
    rng = random.Random(seed() ^ 0x50A)
    env = await up(dut, ppm=rng.uniform(-600, 600))
    for inj in env.injectors:
        inj.rates.update(lcrc=0.005, corrupt_tlp=0.003, drop_tlp=0.003, corrupt_dllp=0.005,
                         drop_dllp=0.005)
    stallers = [e for e in env.ends if not e.any_inf]
    releasers = [e for e in env.ends if e.rel]
    for e in stallers:
        e.take_p = 0.5
    for e in releasers:
        e.rel_p = 0.5
    for _ in range(4):
        env.send(0, 250)
        env.send(1, 250)
        await env.cycles(rng.randint(2000, 6000))
        env.line(rng.choice(("ab", "ba")), "flip", 2)
        for e in stallers:
            e.stall(rng.choice((4000, 10000)))
        for e in releasers:
            e.rel_stall(rng.choice((4000, 10000)))
    for e in stallers:
        e.take_p = 1.0
    for e in releasers:
        e.rel_p = 1.0
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
    DL_Active, and new traffic is lossless. In the release configuration
    both upper layers stop releasing before the request, so each end goes
    down holding credits; the upper layer drops them (it is reset), and
    after the relink every credit is back at the advertisement."""
    env = await up(dut)
    env.send(0, 80)
    env.send(1, 80)
    await env.cycles(1500)
    releasers = [e for e in env.ends if e.rel]
    for e in releasers:
        e.rel_stall_until = 1 << 62
    if releasers:
        await env.cycles(500)
        for e in releasers:
            assert e.held[0][0] or e.held[1][0], f"end {e.name}: holds nothing at the {request}"
            env.log.info("%s: end %s holds %s at the request", request, e.name, e.held)
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
    for e in releasers:
        e.rel_stall_until = e.cycle
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


# -------------------------------------------------------------- throughput

# HAS targets (throughput): TLP payload as a fraction of the line rate, at
# least these, for 128-byte memory writes back to back, default credits,
# 0 ppm, error-free, upper layers never waiting, over at least 2000 TLPs
# per direction from the first accepted to the last delivered.
TARGET = {False: 0.85, True: 0.76}
THROUGHPUT_TLPS = 2000
MWR_DW = 32                          # 128-byte payload, 3-DW header: 140 bytes


async def throughput(dut, both: bool, n: int = THROUGHPUT_TLPS):
    """The HAS throughput measurement: ``n`` 128-byte MWr one way (a to b)
    or each way. Checks the delivered payload efficiency per direction
    against the HAS target, exactly, and attributes every symbol on each
    line in the measured window (``pcie_link_throughput_model``): payload,
    TLP header and framing, Ack, UpdateFC, SKP, idle and the rest; the
    attribution must close on the window. Lossless and error-free (every
    final check, no error pulse, no exit from L0)."""
    env = await up(dut)
    assert env.ppm == 0 and env.credits == "default", "the HAS measures at 0 ppm, default credits"
    for e in env.ends:
        e.valid_p = 1.0                  # the upper layer never waits (HAS checks)
    for inj in env.injectors:
        inj.line_runs = []
    # The seed moves the traffic against the SKP schedules (a start within
    # one SKP interval) and, both ways, b's first TLP against a's (within
    # one TLP time), so seeds line the items up differently.
    rng = random.Random(seed() ^ 0x7A9)
    await env.cycles(rng.randrange(arch.SKP_INTERVAL_MIN // 2))
    t_start = now_ps()
    dirs = ("ab", "ba") if both else ("ab",)
    tlps = [[ptn.make_tlp("MWr", MWR_DW, (k + 0x4000 * i) & 0xFFFF) for k in range(n)]
            for i in range(len(dirs))]
    env.ends[0].send(tlps[0])
    if both:
        stagger = rng.randrange(arch.tlp_line_symbols(4 * (3 + MWR_DW)) // 2)
        await env.cycles(stagger)
        env.ends[1].send(tlps[1])
        env.log.info("throughput: b starts %d cycles after a", stagger)
    await env.drain(400 * n)
    await env.cycles(500)
    env.finish()
    assert_quiet(env, " (throughput)")
    target = TARGET[both]
    has = arch.derived_fractions(4 * (3 + MWR_DW), 4 * MWR_DW, both)
    results, misses = {}, []
    for d in ("ab", "ba"):
        sb = env.sb[d]
        line = attribute_line(env, d, t_start)
        res = {"line": line}
        if d in dirs:
            assert sb.delivered == n and sb.payload == n * 4 * MWR_DW, (d, sb.delivered, sb.payload)
            cycles = (sb.last_deliver_t - sb.first_accept_t) / CORE_PS + 1
            eff = sb.payload / (arch.LINE_BYTES_PER_CYCLE * cycles)
            res.update(efficiency=round(eff, 5), cycles=round(cycles, 1),
                       payload_bytes=sb.payload, target=target,
                       has_derived=round(has["payload_line_fraction"], 5))
            if eff < target:
                misses.append(f"{d}: payload efficiency {eff:.5f} below the HAS target {target} "
                              f"({sb.payload} payload bytes in {cycles:.1f} cycles, HAS derived "
                              f"{has['payload_line_fraction']:.5f}); line attribution "
                              f"{line['per_tlp']} symbols per TLP, {line['fraction']}")
        results[d] = res
        env.log.info("throughput %s %s: %s", "both ways" if both else "one way", d, res)
    assert not misses, "; ".join(misses)
    return env, results


def attribute_line(env, d, since):
    """Every symbol of direction ``d``'s line from the first TLP sent after
    ``since`` to the last TLP's end (the reverse line of a one-way run: the
    forward window), attributed; the attribution must close."""
    i = 0 if d == "ab" else 1
    view = env.chk.views[("sent", d)]
    period = env.periods[i]
    fwd = [p for p in env.chk.views[("sent", "ab")] if not p.dllp and p.start >= since]
    tlps = [p for p in view if not p.dllp and p.start >= since] or fwd
    t0, t1 = tlps[0].start, tlps[-1].end
    att = thr.attribute(view, env.injectors[i].line_runs, t0, t1, period)
    c, w = att["counts"], att["window"]
    assert sum(c.values()) == w, (f"{d}: line attribution does not close: {sum(c.values())} "
                                  f"symbols attributed, {w} in the window; {c}")
    k = att["tlps"] or max(1, len(fwd))  # per TLP sent (the forward ones, one way)
    return {"window_symbols": w, "tlps": att["tlps"],
            "fraction": thr.summary(att),
            "per_tlp": {cat: round(v / k, 3) for cat, v in c.items() if v},
            "non_data": w - c["payload"]}


@cocotb.test()
async def e2e_throughput_oneway(dut):
    """HAS throughput, one way: 2000 128-byte MWr a to b, payload at least
    0.85 of the line rate; the Acks and UpdateFC-P ride the reverse line."""
    await throughput(dut, both=False)


@cocotb.test()
async def e2e_throughput_bidir(dut):
    """HAS throughput, both ways: 2000 128-byte MWr each way, payload at
    least 0.76 of the line rate in each direction."""
    await throughput(dut, both=True)


@cocotb.test()
async def e2e_throughput_short(dut):
    """L1 screen of the one-way measurement: 400 TLPs instead of 2000,
    against the same 0.85. Fewer TLPs put more of the start and drain in
    the window, so this is stricter than the HAS run, never looser."""
    await throughput(dut, both=False, n=400)
