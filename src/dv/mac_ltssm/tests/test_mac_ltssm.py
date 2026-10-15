"""pcie_mac_ltssm tests (wiki/src/dv/mac_ltssm/DV_PLAN.md).

Every test runs the DUT against the architecture model's link partner
through tb.harness, which compares the DUT with the shadow model every
cycle and samples the ``mac_ltssm_arcs`` covergroup on every observed state
change. The recipes steer the DUT only through the partner's requests and
injection hooks, the Link's PCS hooks and the DUT's own link-control inputs:
the partner-only recipes of wiki/src/arch/mac/MODEL.md#partner-only-recipes
(the DUT has no device-side hooks).
"""

import random

import cocotb

from tb.harness import CORE_ARCS, Harness, arch

LIMIT = 400_000          # cycles any one wait may take at dividers 1024
WALK_PAD = {"kind": "TS1", "link": 0, "lane": None}
WALK_LANE = {"kind": "TS1", "link": 0, "lane": 0}
TS1_PAD = {"kind": "TS1", "link": None, "lane": None}


# ------------------------------------------------------------------ helpers

async def train(h, role, **kw):
    await h.fresh(role, **kw)
    await h.run_until(h.both_l0, LIMIT, "training to L0")
    assert h.a.link_up, f"{role}: link_up_o low in L0 after training"
    want = h.partner.n_fts
    assert h.partner_n_fts == want, (
        f"{role}: partner_n_fts_o expected {want} got {h.partner_n_fts}")


async def settle(h):
    """Clear the partner's hooks and every request; wait for both in L0."""
    p = h.partner
    p.inject.__init__()
    p.retrain_req = p.hot_reset_req = p.disable_req = p.loopback_req = False
    p.pm_state_req = 0
    h.request(retrain=0, hot_reset=0, disable=0, loopback=0, pm_state=0)
    await h.run_until(h.both_l0, LIMIT, "settling to L0")


def script(p, ts, then):
    """Freeze the partner and play an exact TS sequence, then ``then``."""
    from collections import deque
    p.inject.freeze = True
    p.inject.ts_script = deque(ts)
    p.inject.ts = then


def walk(role, to):
    """The TS1 the frozen partner sends, two per arc, to take the DUT from
    Configuration.Linkwidth.Start to ``to``."""
    steps = [WALK_PAD, WALK_PAD if role == "downstream" else WALK_LANE, WALK_LANE]
    n = {arch.LWA: 1, arch.LNW: 2, arch.LNA: 3}[to]
    return [f for f in steps[:n] for _ in range(2)]


def took(h, a, b, after=None):
    return h.took(a, b, after)


# ------------------------------------------------------------------ recipes

async def ep_normal(h, role):
    """Training; retrain by the DUT, by the partner (a TS in L0) and by
    electrical idle without an EIOS (partner muted in L0)."""
    await train(h, role)
    h.request(retrain=1)
    await h.run_until(h.in_state(arch.RL), LIMIT, "Recovery after retrain_req_i")
    await h.run_until(h.both_l0, LIMIT)
    t = h.now
    h.partner.retrain_req = True
    await h.run_until(took(h, arch.L0, arch.RL, t), LIMIT, "Recovery on a received TS")
    await h.run_until(h.both_l0, LIMIT)
    t = h.now
    h.partner.inject.mute = True
    await h.run_until(took(h, arch.L0, arch.RL, t), LIMIT, "Recovery on electrical idle")
    h.partner.inject.mute = False
    await settle(h)


async def ep_no_receiver(h, role):
    """The partner leaves reset late (no termination): Detect.Active finds
    no receiver."""
    await h.fresh(role, skew=4000)
    await h.run_until(took(h, arch.DA, arch.DQ), LIMIT, "Detect.Active to Detect.Quiet")
    await h.run_until(h.both_l0, LIMIT)


async def ep_compliance(h, role):
    """Partner silent: Polling.Active times out to Polling.Compliance, left
    when the partner speaks."""
    await h.fresh(role)
    h.partner.inject.mute = True
    await h.run_until(h.in_state(arch.PC), LIMIT, "Polling.Compliance")
    h.partner.inject.mute = False
    await h.run_until(took(h, arch.PC, arch.PA), LIMIT)
    await settle(h)


async def ep_polling_timeouts(h, role):
    """Partner TS with a link number in Polling (Polling.Active times out),
    then the partner frozen sending TS1 or TS2 (Polling.Configuration and
    Configuration.Linkwidth.Start time out)."""
    await h.fresh(role)
    h.partner.inject.ts = {"link": 7}
    await h.run_until(took(h, arch.PA, arch.DQ), LIMIT, "Polling.Active timeout")
    await settle(h)
    for stuck, arc in ((arch.PA, (arch.PCFG, arch.DQ)), (arch.PCFG, (arch.LWS, arch.DQ))):
        await h.fresh(role)
        await h.run_until(lambda q: q.partner.state == stuck, LIMIT)
        h.partner.inject.freeze = True
        await h.run_until(took(h, *arc), LIMIT, f"{arc[0]} timeout")
        await settle(h)


async def ep_cfg_walks(h, role):
    """The frozen partner walks the DUT through Configuration, then sends
    TS1 with PAD fields (Linkwidth.Accept, Lanenum.Wait and Lanenum.Accept
    to Detect.Quiet) or lane 1 (Lanenum.Accept back to Lanenum.Wait)."""
    cases = ((arch.LWA, TS1_PAD, arch.DQ), (arch.LNW, TS1_PAD, arch.DQ),
             (arch.LNA, {"kind": "TS1", "link": 0, "lane": 1}, arch.LNW),
             (arch.LNA, TS1_PAD, arch.DQ))
    for st, then, to in cases:
        await h.fresh(role)
        await h.run_until(h.in_state(arch.LWS), LIMIT)
        script(h.partner, walk(role, st), then)
        await h.run_until(took(h, st, to), LIMIT, f"{st} to {to}")
        await settle(h)


async def ep_cfg_timeouts(h, role):
    """Partner frozen in Lanenum.Accept (TS1): Complete times out; frozen in
    Complete (TS2): Configuration.Idle times out to RcvrLock."""
    for stuck, arc in ((arch.LNA, (arch.CC, arch.DQ)), (arch.CC, (arch.CI, arch.RL))):
        await h.fresh(role)
        await h.run_until(lambda q: q.partner.state == stuck, LIMIT)
        h.partner.inject.freeze = True
        await h.run_until(took(h, *arc), LIMIT, f"{arc[0]} timeout")
        await settle(h)


async def ep_rcvrlock_timeouts(h, role):
    """RcvrLock times out: TS with another link number (to Configuration),
    or none at all (to Detect.Quiet)."""
    if not h.both_l0():
        await train(h, role)
    p = h.partner
    p.inject.ts = {"link": 3}
    t = h.now
    h.request(retrain=1)
    await h.run_until(took(h, arch.RL, arch.LWS, t), LIMIT, "RcvrLock timeout with TS")
    await settle(h)
    p.inject.freeze = True
    t = h.now
    h.request(retrain=1)
    await h.run_until(took(h, arch.RL, arch.DQ, t), LIMIT, "RcvrLock timeout without TS")
    await settle(h)


async def _partner_frozen_in(h, role, state):
    """Retrain from the DUT in L0 and freeze the partner once in ``state``."""
    if not h.both_l0():
        await train(h, role)
    h.request(retrain=1)
    await h.run_until(lambda q: q.partner.state == state, LIMIT)
    h.partner.inject.freeze = True


async def ep_rcvrcfg_mism(h, role):
    """Partner frozen in RcvrLock (TS1); once the DUT is in RcvrCfg, TS1 with
    link 3: 8 mismatching TS1 and 16 TS2 sent take it to Configuration."""
    t = h.now
    await _partner_frozen_in(h, role, arch.RL)
    await h.run_until(h.in_state(arch.RC), LIMIT)
    h.partner.inject.ts = {"kind": "TS1", "link": 3}
    await h.run_until(took(h, arch.RC, arch.LWS, t), LIMIT, "RcvrCfg to Configuration")
    await settle(h)


async def ep_rcvrcfg_timeout(h, role):
    """Partner frozen in RcvrLock: its TS1 never turn into TS2."""
    t = h.now
    await _partner_frozen_in(h, role, arch.RL)
    await h.run_until(took(h, arch.RC, arch.DQ, t), LIMIT, "RcvrCfg timeout")
    await settle(h)


async def ep_ridle_lanepad(h, role):
    """Partner frozen in RcvrCfg (TS2); once the DUT is in Recovery.Idle, TS1
    with lane PAD take it to Configuration."""
    t = h.now
    await _partner_frozen_in(h, role, arch.RC)
    await h.run_until(h.in_state(arch.RI), LIMIT)
    h.partner.inject.ts = {"kind": "TS1", "lane": None}
    await h.run_until(took(h, arch.RI, arch.LWS, t), LIMIT, "Recovery.Idle to Configuration")
    await settle(h)


async def ep_ridle_timeout(h, role):
    """Partner frozen in RcvrCfg: no idle symbols, Recovery.Idle times out."""
    t = h.now
    await _partner_frozen_in(h, role, arch.RC)
    await h.run_until(took(h, arch.RI, arch.RL, t), LIMIT, "Recovery.Idle timeout")
    await settle(h)


async def ep_idle_to_rlock(h, role):
    """The partner-only recipe for the two idle_to_rlock arcs: Recovery.Idle
    times out 256 times without reaching L0 (RcvrLock and RcvrCfg pass on the
    frozen partner's TS2), the last time to Detect.Quiet; the counter, not
    cleared there, then sends Configuration.Idle's timeout to Detect.Quiet."""
    await train(h, role)
    p = h.partner
    h.request(retrain=1)
    await h.run_until(lambda q: q.partner.state == arch.RC, LIMIT)
    p.inject.freeze = True
    t = h.now
    limit = (arch.IDLE_TO_RLOCK_MAX + 2) * 20_000
    await h.run_until(took(h, arch.RI, arch.DQ, t), limit, "Recovery.Idle to Detect.Quiet")
    n = sum(1 for c, a, b in h.a.arcs if c >= t and (a, b) == (arch.RI, arch.RL))
    assert n == arch.IDLE_TO_RLOCK_MAX, (
        f"{role}: Recovery.Idle timeouts to RcvrLock expected "
        f"{arch.IDLE_TO_RLOCK_MAX} got {n}")
    # Retrain from Detect; stop the partner in Configuration.Complete.
    p.inject.__init__()
    await h.run_until(lambda q: q.partner.state == arch.CC, LIMIT)
    p.inject.freeze = True
    t = h.now
    await h.run_until(took(h, arch.CI, arch.DQ, t), LIMIT, "Configuration.Idle to Detect.Quiet")
    await settle(h)


# Recipes without an error-path timeout (level 1), and the rest.
QUICK = (ep_normal, ep_no_receiver, ep_cfg_walks, ep_rcvrcfg_mism, ep_ridle_lanepad)
TIMEOUTS = (ep_compliance, ep_polling_timeouts, ep_cfg_timeouts, ep_rcvrlock_timeouts,
            ep_rcvrcfg_timeout, ep_ridle_timeout)


async def run_arcs(dut, roles, episodes, require_all):
    """Run ``episodes`` in each of ``roles``; with ``require_all``, fail
    unless every core arc was taken in each role."""
    h = Harness(dut)
    await h.start()
    for role in roles:
        first = len(h.a.arcs)
        for ep in episodes:
            await ep(h, role)
            dut._log.info("%s: %s done at cycle %d", role, ep.__name__, h.now)
        hit = {(a, b) for _, a, b in h.a.arcs[first:]}
        left = sorted(n for n, arc in CORE_ARCS.items() if arc not in hit)
        dut._log.info("%s: %d/%d core arcs, %d cycles checked against the shadow; "
                      "missed: %s", role, len(CORE_ARCS) - len(left), len(CORE_ARCS),
                      h.checks, left)
        if require_all:
            assert not left, f"{role}: core arcs not reached: {left}"


# -------------------------------------------------------------------- tests

@cocotb.test()
async def smoke(dut):
    """L0: training to L0 and a retrain each way, in both port roles."""
    h = Harness(dut)
    await h.start()
    for role in arch.ROLES:
        await train(h, role)
        h.request(retrain=1)
        await h.run_until(h.in_state(arch.RL), LIMIT, "Recovery after retrain_req_i")
        await h.run_until(h.both_l0, LIMIT)
        t = h.now
        h.partner.retrain_req = True
        await h.run_until(took(h, arch.L0, arch.RL, t), LIMIT, "Recovery on a received TS")
        await h.run_until(h.both_l0, LIMIT)
        dut._log.info("%s: %d cycles checked", role, h.checks)


@cocotb.test()
async def arcs_quick(dut):
    """L1: the recipes without an error-path timeout, both port roles."""
    await run_arcs(dut, arch.ROLES, QUICK, False)


@cocotb.test()
async def arcs_full_downstream(dut):
    """L2: every core arc, downstream role, the idle_to_rlock loop included."""
    await run_arcs(dut, ("downstream",), QUICK + TIMEOUTS + (ep_idle_to_rlock,), True)


@cocotb.test()
async def arcs_full_upstream(dut):
    """L2: every core arc, upstream role, the idle_to_rlock loop included."""
    await run_arcs(dut, ("upstream",), QUICK + TIMEOUTS + (ep_idle_to_rlock,), True)


@cocotb.test()
async def polarity(dut):
    """L1: an inverted receive wire sets pipe_rx_polarity_o in Polling, once,
    and training completes; Detect.Quiet clears it."""
    h = Harness(dut)
    await h.start()
    for role in arch.ROLES:
        await h.fresh(role)
        h.link.pcs[0].invert = True
        await h.run_until(h.both_l0, LIMIT, "training on an inverted wire")
        assert h._read()["pipe_rx_polarity_o"] == 1, (
            f"{role}: pipe_rx_polarity_o expected 1 in L0 on an inverted wire")
        sets = [c for c, a, b in h.a.arcs if b == arch.PCFG]
        assert sets, f"{role}: no Polling.Configuration"
        # A reset of the partner takes the link down to Detect.Quiet.
        h.release = (h.release[0], h.now - h.t0 + 64)
        await h.run_until(h.in_state(arch.DQ), LIMIT)
        assert h._read()["pipe_rx_polarity_o"] == 0, (
            f"{role}: pipe_rx_polarity_o expected 0 in Detect.Quiet")
        await h.run_until(h.both_l0, LIMIT)


@cocotb.test()
async def dividers(dut):
    """L1: TIMEOUT_DIV 256 and ERR_TIMEOUT_DIV 128 (the entry's params):
    Detect.Quiet 5859 cycles, Polling.Active 23437, Polling.Configuration
    46875 and the 2 ms timeouts at the 16384 floor, each checked exactly by
    the shadow."""
    h = Harness(dut, timeout_div=256, err_timeout_div=128)
    await h.start()
    for role in arch.ROLES:
        await h.fresh(role)
        h.partner.inject.mute = True
        await h.run_until(h.in_state(arch.PC), LIMIT)
        entry = next(c for c, a, b in h.a.arcs if b == arch.PA)
        pc = next(c for c, a, b in h.a.arcs if b == arch.PC)
        assert pc - entry == 23437, f"{role}: Polling.Active timeout {pc - entry} cycles"
        h.partner.inject.mute = False
        await settle(h)
        await h.fresh(role)
        await h.run_until(lambda q: q.partner.state == arch.PCFG, LIMIT)
        h.partner.inject.freeze = True
        await h.run_until(took(h, arch.LWS, arch.DQ), LIMIT)
        await settle(h)
        quiet = next(c for c, a, b in h.a.arcs if (a, b) == (arch.DQ, arch.DA))
        dut._log.info("%s: Detect.Quiet left at cycle %d", role, quiet)


@cocotb.test()
async def random_soak(dut):
    """L1, seeded: random partner resets, mutes, freezes, retrains, wrong
    TS fields, inverted wires and symbol errors, in both roles, every cycle
    checked against the shadow; the link must recover to L0 at the end."""
    rng = random.Random(cocotb.RANDOM_SEED)
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    h = Harness(dut)
    await h.start()
    actions = ("retrain_dut", "retrain_partner", "mute", "freeze", "ts_fields",
               "partner_reset", "corrupt", "wait")
    for role in arch.ROLES:
        await h.fresh(role, skew=rng.randrange(0, 2000), delay=rng.randrange(1, 40),
                      partner_link=0 if role == "downstream" else rng.randrange(256))
        h.link.pcs[0].invert = rng.random() < 0.3
        await h.run_until(h.both_l0, LIMIT, "training")
        for _ in range(12):
            act = rng.choice(actions)
            p = h.partner
            if act == "retrain_dut":
                h.request(retrain=1)
            elif act == "retrain_partner":
                p.retrain_req = True
            elif act == "mute":
                p.inject.mute = True
                await h.run(rng.randrange(1, 3000))
                p.inject.mute = False
            elif act == "freeze":
                p.inject.freeze = True
                await h.run(rng.randrange(1, 20_000))
                p.inject.freeze = False
            elif act == "ts_fields":
                p.inject.ts = rng.choice(({"link": rng.randrange(256)}, {"lane": None},
                                          {"lane": 1}, {"kind": "TS1"}, {"kind": "TS2"}))
                h.request(retrain=1)
                await h.run(rng.randrange(1, 20_000))
                p.inject.ts = {}
            elif act == "partner_reset":
                h.release = (h.release[0], h.now - h.t0 + rng.randrange(1, 5000))
            elif act == "corrupt":
                h.link.pcs[0].corrupt = rng.randrange(1, 40)
                h.request(retrain=int(rng.random() < 0.5))
            await h.run(rng.randrange(1, 5000))
        await settle(h)
        dut._log.info("%s: %d arcs, %d cycles checked", role, len(h.a.arcs), h.checks)
