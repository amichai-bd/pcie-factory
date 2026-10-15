"""pcie_mac tests (wiki/src/dv/mac/DV_PLAN.md).

Every test drives pcie_mac only through its ports, against the architecture
model's link partner, through tb.harness: the shadow model checks the
state, the PIPE outputs, the transmitted symbols, receiver errors and every
delivered packet each cycle, and an independent decoder checks the
ordered-set receive events and TS fields. The recipes steer the DUT only
through the partner's requests and injection hooks, the Link's PCS hooks,
a test's line hook and the DUT's own inputs
(wiki/src/arch/mac/MODEL.md#partner-only-recipes).
"""

import random
from collections import deque

import cocotb
from cocotb.triggers import FallingEdge, Timer

from tb.harness import (CONTROL_ARCS, COV_ARCS, COV_CTL, COV_OS_RX, COV_PKT,  # noqa: F401
                        CORE_ARCS, FIELDS_IN, Harness, arch)

LIMIT = 400_000          # cycles any one wait may take at dividers 1024
WALK_PAD = {"kind": "TS1", "link": 0, "lane": None}
WALK_LANE = {"kind": "TS1", "link": 0, "lane": 0}
TS1_PAD = {"kind": "TS1", "link": None, "lane": None}

# The first sixteen key bytes after a COM (wiki/src/arch/mac/SYMBOLS.md,
# Scrambling, Known answer): idle data symbols scramble to them.
KNOWN_ANSWER = (0xFF, 0x17, 0xC0, 0x14, 0xB2, 0xE7, 0x02, 0x82,
                0x72, 0x6E, 0x28, 0xA6, 0xBE, 0x6D, 0xBF, 0x8D)

# Reset values of the outputs (wiki/src/arch/mac/HAS.md#reset-values).
RESET_VALUES = {
    "pkt_tx_ready_o": 0, "pkt_rx_valid_o": 0, "pkt_rx_sop_o": 0, "pkt_rx_eop_o": 0,
    "pkt_rx_err_o": 0, "pkt_rx_nullify_o": 0, "link_up_o": 0,
    "ltssm_state_o": arch.CODES["Detect.Quiet"], "rcv_err_o": 0, "pipe_reset_no": 0,
    "pipe_tx_detect_rx_o": 0, "pipe_tx_elec_idle_o": 1, "pipe_tx_compliance_o": 0,
    "pipe_rx_polarity_o": 0, "pipe_power_down_o": arch.P1,
}


# ------------------------------------------------------------------ helpers

def rng_for(dut):
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    return random.Random(cocotb.RANDOM_SEED)


async def train(h, role, **kw):
    await h.fresh(role, **kw)
    await h.run_until(h.both_l0, LIMIT, "training to L0")
    assert h.a.link_up, f"{role}: link_up_o low in L0 after training"


async def settle(h):
    """Clear the partner's hooks and every request; wait for both in L0."""
    p = h.partner
    p.inject.__init__()
    p.retrain_req = p.hot_reset_req = p.disable_req = p.loopback_req = False
    p.pm_state_req = 0
    h.request(retrain=0, hot_reset=0, disable=0, loopback=0, pm_state=0)
    h.line_hook = None
    await h.run_until(h.both_l0, LIMIT, "settling to L0")


def script(p, ts, then):
    """Freeze the partner and play an exact TS sequence, then ``then``."""
    p.inject.freeze = True
    p.inject.ts_script = deque(ts)
    p.inject.ts = then


def walk(role, to):
    steps = [WALK_PAD, WALK_PAD if role == "downstream" else WALK_LANE, WALK_LANE]
    n = {arch.LWA: 1, arch.LNW: 2, arch.LNA: 3}[to]
    return [f for f in steps[:n] for _ in range(2)]


def took(h, a, b, after=None):
    return h.took(a, b, after)


def rand_packet(rng, max_tlp=256, p_big=0.0, big_max=2800):
    """A TLP (even length, 4 to max_tlp bytes; with ``p_big`` past one SKP
    interval, 1400 to big_max bytes) or a 6-byte DLLP, some nullified
    (ignored for a DLLP)."""
    if rng.random() < 0.3:
        return arch.Packet(True, bytes(rng.randrange(256) for _ in range(6)),
                           nullify=rng.random() < 0.1)
    n = 2 * rng.randint(700, big_max // 2) if rng.random() < p_big else 2 * rng.randint(1, max_tlp // 2)
    return arch.Packet(False, bytes(rng.randrange(256) for _ in range(n)),
                       nullify=rng.random() < 0.15)


def known_answer_seen(h) -> int:
    """Count SKP ordered sets on the DUT's line followed by 16 idle data
    symbols equal to the known key sequence."""
    syms, hits = list(h.txmon.syms), 0
    for i in range(len(syms) - 20):
        if syms[i] == (1, 0xBC) and syms[i + 1] == (1, 0x1C):
            j = i + 1
            while j < len(syms) and syms[j] == (1, 0x1C):
                j += 1
            run = syms[j:j + 16]
            if len(run) == 16 and all(k == 0 for k, _ in run):
                if tuple(b for _, b in run) == KNOWN_ANSWER:
                    hits += 1
    return hits


async def reset_values(h, role, rng, cycles=48):
    """HAS reset values with every input toggling, then a clean release."""
    dut = h.dut
    dut.port_role_i.value = int(role == "upstream")
    dut.rst_i.value = 1
    await FallingEdge(dut.clk_i)
    for c in range(cycles):
        h.inputs.update({n: rng.getrandbits(w) for n, w in FIELDS_IN})
        h._write()
        await FallingEdge(dut.clk_i)
        o = h._read()
        for name, want in RESET_VALUES.items():
            assert o[name] == want, f"{role} reset cycle {c}: {name} expected {want} got {o[name]}"
    h.inputs.update({n: 0 for n, _ in FIELDS_IN})
    h._write()


async def reset_in_packet(h, role):
    """HAS reset values: reset wins over the sop-to-eop ready rule. With a
    packet half accepted, rst_i rises: pkt_tx_ready_o falls in that same
    cycle although the packet is open; after reset and training a clean
    packet goes through intact."""
    h.send(arch.Packet(False, bytes(range(200))))
    await h.run_until(lambda q: q.upper.cur is not None and q.upper.cur[1] >= 4, 2000,
                      "a packet half accepted")
    assert h._read()["pkt_tx_ready_o"] == 1, f"{role}: ready low inside a packet"
    h.dut.rst_i.value = 1                    # valid and the next beat stay driven
    await Timer(1, unit="ns")
    got = h._read()["pkt_tx_ready_o"]
    assert got == 0, f"{role}: pkt_tx_ready_o {got} with rst_i high inside a packet"
    await train(h, role)
    q = arch.Packet(False, bytes(range(100, 140)))
    h.send(q)
    await h.drain()
    h.check_partner()
    assert h.partner.rx_packets[-1] == q, f"{role}: the packet after reset arrived bad"


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


async def ep_upstream_link(h, role):
    """Training against a downstream partner with link number 255: the
    upstream DUT learns it and sends it back, which the shadow checks on
    every TS (#158)."""
    if role != "upstream":
        return
    await train(h, role, partner_link=255)
    await settle(h)


async def ep_no_receiver(h, role):
    await h.fresh(role, skew=4000)
    await h.run_until(took(h, arch.DA, arch.DQ), LIMIT, "Detect.Active to Detect.Quiet")
    await h.run_until(h.both_l0, LIMIT)


async def ep_compliance(h, role):
    await h.fresh(role)
    h.partner.inject.mute = True
    await h.run_until(h.in_state(arch.PC), LIMIT, "Polling.Compliance")
    await h.run(64)
    h.partner.inject.mute = False
    await h.run_until(took(h, arch.PC, arch.PA), LIMIT)
    await settle(h)


async def ep_polling_timeouts(h, role):
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
    for stuck, arc in ((arch.LNA, (arch.CC, arch.DQ)), (arch.CC, (arch.CI, arch.RL))):
        await h.fresh(role)
        await h.run_until(lambda q: q.partner.state == stuck, LIMIT)
        h.partner.inject.freeze = True
        await h.run_until(took(h, *arc), LIMIT, f"{arc[0]} timeout")
        await settle(h)


async def ep_rcvrlock_timeouts(h, role):
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
    if getattr(h, "partner", None) is None or h.role != role or not h.both_l0():
        await train(h, role)
    h.request(retrain=1)
    await h.run_until(lambda q: q.partner.state == state, LIMIT)
    h.partner.inject.freeze = True


async def ep_rcvrcfg_mism(h, role):
    t = h.now
    await _partner_frozen_in(h, role, arch.RL)
    await h.run_until(h.in_state(arch.RC), LIMIT)
    h.partner.inject.ts = {"kind": "TS1", "link": 3}
    await h.run_until(took(h, arch.RC, arch.LWS, t), LIMIT, "RcvrCfg to Configuration")
    await settle(h)


async def ep_rcvrcfg_timeout(h, role):
    t = h.now
    await _partner_frozen_in(h, role, arch.RL)
    await h.run_until(took(h, arch.RC, arch.DQ, t), LIMIT, "RcvrCfg timeout")
    await settle(h)


async def ep_ridle_lanepad(h, role):
    t = h.now
    await _partner_frozen_in(h, role, arch.RC)
    await h.run_until(h.in_state(arch.RI), LIMIT)
    h.partner.inject.ts = {"kind": "TS1", "lane": None}
    await h.run_until(took(h, arch.RI, arch.LWS, t), LIMIT, "Recovery.Idle to Configuration")
    await settle(h)


async def ep_ridle_timeout(h, role):
    t = h.now
    await _partner_frozen_in(h, role, arch.RC)
    await h.run_until(took(h, arch.RI, arch.RL, t), LIMIT, "Recovery.Idle timeout")
    await settle(h)


async def ep_idle_to_rlock(h, role):
    """Recovery.Idle times out 256 times without reaching L0, the last time
    to Detect.Quiet; the counter, not cleared there, then sends
    Configuration.Idle's timeout to Detect.Quiet."""
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
    p.inject.__init__()
    await h.run_until(lambda q: q.partner.state == arch.CC, LIMIT)
    p.inject.freeze = True
    t = h.now
    await h.run_until(took(h, arch.CI, arch.DQ, t), LIMIT, "Configuration.Idle to Detect.Quiet")
    await settle(h)


async def ep_power(h, role):
    """The power states against the partner (wiki/src/arch/mac/ARCS.md#l0,
    #l0s-sub-machines, #l1-and-l2): transmit and receive L0s, left by a
    packet each way; a retrain while the receiver is in Rx_L0s.Idle, and
    one in Rx_L0s.Entry (the exit waits for it); L1 requested after an
    EIOS already started receive L0s; the
    Rx_L0s.FTS timeout (the partner sends 200 FTS too many); L1 and L2,
    entered by both requests and left by request 0; and the upstream wait
    for L1 withdrawn (back through Recovery)."""
    if not h.both_l0():
        await train(h, role)
    p = h.partner
    t = h.now
    h.request(pm_state=1)
    p.pm_state_req = 1
    await h.run_until(lambda q: q.a.tx_sub == arch.TI and q.a.rx_sub == arch.RXI, LIMIT,
                      "transmit and receive L0s")
    p.tx_packets.append(arch.Packet(False, bytes(range(20))))
    await h.run_until(took(h, arch.RF, arch.RL0, t), LIMIT, "receive L0s left by FTS")
    h.send(arch.Packet(False, bytes(range(30, 54))))
    await h.run_until(took(h, arch.TF, arch.TL0, t), LIMIT, "transmit L0s left by FTS")
    await h.drain()
    h.check_partner()
    await settle(h)
    # A directed exit while the receiver is in L0s.
    t = h.now
    p.pm_state_req = 1
    await h.run_until(lambda q: q.a.rx_sub == arch.RXI, LIMIT, "receive L0s")
    h.request(retrain=1)
    await h.run_until(took(h, arch.RXI, arch.ROFF, t), LIMIT, "Rx_L0s.Idle to Rx_Off")
    await settle(h)
    # A retrain while the receiver is in Rx_L0s.Entry: the Recovery exit is
    # latched and waits for Rx_L0s.Idle, the frame sending idle meanwhile.
    t = h.now
    p.pm_state_req = 1
    await h.run_until(lambda q: q.a.rx_sub == arch.RE, LIMIT, "Rx_L0s.Entry")
    h.request(retrain=1)
    await h.run_until(took(h, arch.RXI, arch.ROFF, t), LIMIT, "the latched exit")
    await settle(h)
    # The Rx_L0s.FTS timeout, with the DUT's own transmitter in
    # Tx_L0s.Idle: the timeout's Recovery exit waits for Tx_L0s.FTS, so it
    # is latched (MAS, the latched L0 exit). The partner's extra FTS outlast
    # the DUT's own N_FTS FTS, so its SKP cannot end Rx_L0s.FTS first.
    t = h.now
    h.request(pm_state=1)
    p.pm_state_req = 1
    p.inject.fts_extra = 200 + 2 * h.n_fts
    await h.run_until(lambda q: q.a.rx_sub == arch.RXI and q.a.tx_sub == arch.TI, LIMIT,
                      "receive and transmit L0s")
    p.tx_packets.append(arch.Packet(False, bytes(8)))
    await h.run_until(took(h, arch.RF, arch.ROFF, t), LIMIT, "Rx_L0s.FTS timeout")
    await settle(h)
    # L1 requested after an EIOS already put the receiver in receive L0s (the
    # HAS edge case): the downstream port enters at once, the upstream port
    # only after its own EIOS (its wait); the pair recovers through Recovery.
    t = h.now
    p.pm_state_req = 1
    await h.run_until(lambda q: q.a.rx_sub == arch.RXI, LIMIT, "receive L0s")
    h.request(pm_state=2)
    p.pm_state_req = 2
    await h.run_until(took(h, arch.L0, arch.L1E, t), LIMIT, "L1 on an earlier EIOS")
    await h.run(200)
    await settle(h)
    # L1 and L2, entered and left by both ports' requests.
    for req, idle, out in ((2, arch.L1I, arch.RL), (3, arch.L2I, arch.DQ)):
        t = h.now
        h.request(pm_state=req)
        p.pm_state_req = req
        await h.run_until(lambda q: q.a.state == idle and q.partner.state == idle, LIMIT,
                          f"both in {idle}")
        await h.run(100)
        h.request(pm_state=0)
        p.pm_state_req = 0
        await h.run_until(took(h, idle, out, t), LIMIT, f"{idle} to {out}")
        await h.run_until(h.both_l0, LIMIT)
    # The upstream port's wait for L1, withdrawn.
    t = h.now
    if role == "upstream":
        h.request(pm_state=2)
        await h.run(64)
        o = h._read()
        assert o["pipe_tx_elec_idle_o"] == 1 and o["ltssm_state_o"] == 0x20, (
            f"{role}: the upstream wait for L1 expected L0 in electrical idle")
        h.request(pm_state=0)
    else:
        p.pm_state_req = 2
        await h.run_until(lambda q: q.partner.pm_wait, LIMIT, "the partner's upstream wait")
        await h.run(8)
        p.pm_state_req = 0
    await h.run_until(took(h, arch.L0, arch.RL, t), LIMIT, "Recovery after the wait")
    await settle(h)


# Level 1 (the compliance recipe's one timeout is at its floor), then the
# error-path timeouts.
QUICK = (ep_normal, ep_upstream_link, ep_no_receiver, ep_cfg_walks, ep_rcvrcfg_mism,
         ep_ridle_lanepad, ep_compliance, ep_power)
TIMEOUTS = (ep_polling_timeouts, ep_cfg_timeouts, ep_rcvrlock_timeouts,
            ep_rcvrcfg_timeout, ep_ridle_timeout)


async def run_arcs(dut, roles, episodes, require_all, arcs=None):
    arcs = CORE_ARCS if arcs is None else arcs
    h = Harness(dut)
    await h.start()
    for role in roles:
        first = len(h.a.arcs)
        for ep in episodes:
            await ep(h, role)
            dut._log.info("%s: %s done at cycle %d", role, ep.__name__, h.now)
        hit = {(a, b) for _, a, b in h.a.arcs[first:]}
        left = sorted(n for n, arc in arcs.items() if arc not in hit)
        dut._log.info("%s: %d/%d arcs, %d cycles checked; missed: %s", role,
                      len(arcs) - len(left), len(arcs), h.checks, left)
        if require_all:
            assert not left, f"{role}: arcs not reached: {left}"


# ------------------------------------------------------- control states

async def ensure_l0(h, role):
    """Train when the pair is not both in L0 (or not made yet, or in the
    other role)."""
    if getattr(h, "partner", None) is None or h.role != role or not h.both_l0():
        await train(h, role)


def downstream_of(h):
    """Set a Hot Reset or Disable request on the downstream port of the
    pair, DUT or partner: ``set(attr, value)``."""
    def set_(attr, v):
        if h.a.dsp:
            h.request(**{attr: v})
        else:
            setattr(h.partner, {"hot_reset": "hot_reset_req",
                                "disable": "disable_req"}[attr], bool(v))
    return set_


async def ep_directed(h, role):
    """Hot Reset and Disabled from L0, directed by the downstream port (the
    DUT downstream, the partner upstream: the DUT follows), held, then
    released; the pair trains again. The partner's Disabled ends in an EIOS,
    so an upstream DUT leaves on its EIOS and EI exit."""
    await ensure_l0(h, role)
    dsp = downstream_of(h)
    for attr, st in (("hot_reset", arch.HR), ("disable", arch.DIS)):
        t = h.now
        dsp(attr, 1)
        await h.run_until(took(h, arch.RI, st, t), LIMIT, f"Recovery.Idle to {st}")
        await h.run(2000)
        assert not h.a.link_up, f"{role}: link_up_o high in {st}"
        dsp(attr, 0)
        await h.run_until(took(h, st, arch.DQ, t), LIMIT, f"{st} to Detect.Quiet")
        await settle(h)


async def ep_upstream_ignores(h, role):
    """An upstream port ignores hot_reset_req_i and disable_req_i: it stays
    in L0 with both held high, and a retrain with them held goes through
    Recovery.Idle back to L0."""
    if role != "upstream":
        return
    await ensure_l0(h, role)
    t = h.now
    h.request(hot_reset=1, disable=1)
    await h.run(3000)
    assert not any(c >= t for c, *_ in h.a.arcs), f"{role}: left L0 on a downstream request"
    h.request(retrain=1)
    await h.run_until(took(h, arch.RI, arch.L0, t), LIMIT, "Recovery.Idle to L0")
    h.request(hot_reset=0, disable=0)


async def ep_request_priority(h, role):
    """Several requests at once take the first row of the arcs page:
    Recovery.Idle gives Hot Reset over Disabled over Loopback (downstream;
    upstream, Loopback over the ignored two), Linkwidth.Start gives
    Loopback over Disabled."""
    sets = ((dict(hot_reset=1, disable=1, loopback=1), arch.HR),
            (dict(disable=1, loopback=1), arch.DIS)) if role == "downstream" else (
            (dict(hot_reset=1, disable=1, loopback=1), arch.LBE),)
    for req, st in sets:
        await ensure_l0(h, role)
        t = h.now
        h.request(**req)
        await h.run_until(lambda q: any(c >= t and f == arch.RI for c, f, _ in q.a.arcs),
                          LIMIT, "leaving Recovery.Idle")
        got = next(to for c, f, to in h.a.arcs if c >= t and f == arch.RI)
        assert got == st, f"{role}: {req} took Recovery.Idle to {got}, expected {st}"
        await settle(h)
    await h.fresh(role)
    t = h.now
    h.request(**({"disable": 1, "loopback": 1}))
    await h.run_until(lambda q: any(c >= t and f == arch.LWS for c, f, _ in q.a.arcs), LIMIT)
    got = next(to for c, f, to in h.a.arcs if c >= t and f == arch.LWS)
    assert got == arch.LBE, f"{role}: Linkwidth.Start took {got} with both requests"
    await settle(h)


async def loopback_round(h, role, master):
    """Loopback with the DUT (``master`` 'dut') or the partner as master,
    until both are in Loopback.Active, a while of pattern, then released."""
    t = h.now
    if master == "dut":
        h.request(loopback=1)
    else:
        h.partner.loopback_req = True
    await h.run_until(lambda q: q.a.state == arch.LBA and q.partner.state == arch.LBA, LIMIT,
                      "both in Loopback.Active")
    errs = h.shadow.lb_errors
    await h.run(1500)                      # past a SKP interval of the pattern
    assert h.shadow.lb_errors == errs == 0, (
        f"{role}: {h.shadow.lb_errors} loopback pattern errors")
    if master == "dut":
        h.request(loopback=0)
    else:
        h.partner.loopback_req = False
    await h.run_until(took(h, arch.LBX, arch.DQ, t), LIMIT, "Loopback.Exit to Detect.Quiet")
    await settle(h)


async def ep_loopback(h, role):
    """Loopback from L0 through Recovery.Idle, the DUT master then slave."""
    await ensure_l0(h, role)
    for master in ("dut", "partner"):
        await loopback_round(h, role, master)


async def ep_training_requests(h, role):
    """Loopback (DUT master, then slave) and Disabled requested before the
    link is up: Configuration.Linkwidth.Start leaves for them."""
    for master in ("dut", "partner"):
        await h.fresh(role)
        t = h.now
        if master == "dut":
            h.request(loopback=1)
        else:
            h.partner.loopback_req = True
        await h.run_until(took(h, arch.LWS, arch.LBE, t), LIMIT, "Linkwidth.Start to Loopback")
        await h.run_until(h.in_state(arch.LBA), LIMIT)
        await h.run(300)
        await settle(h)
    await h.fresh(role)
    t = h.now
    downstream_of(h)("disable", 1)
    await h.run_until(took(h, arch.LWS, arch.DIS, t), LIMIT, "Linkwidth.Start to Disabled")
    await h.run(500)
    await settle(h)


async def ep_following(h, role):
    """The partner's TS1 carry Hot Reset, then Disable Link, while the DUT
    is in Recovery.Idle (the partner frozen in RcvrCfg, then TS1 with the
    bit): the DUT follows in either role. Hot Reset restarts its dwell on
    each such TS1 and leaves once they stop; the partner never sends the
    EIOS, so Disabled leaves on its timeout."""
    for bit, st in ((arch.CTL_HOT_RESET, arch.HR), (arch.CTL_DISABLE, arch.DIS)):
        t = h.now
        await _partner_frozen_in(h, role, arch.RC)
        await h.run_until(h.in_state(arch.RI), LIMIT)
        h.partner.inject.ts = {"kind": "TS1", "ctl": bit}
        await h.run_until(took(h, arch.RI, st, t), LIMIT, f"Recovery.Idle to {st}, following")
        await h.run(3 * h.shadow.dwell_cycles)
        if st == arch.HR:
            assert h.a.state == arch.HR, f"{role}: Hot Reset left while its TS1 still arrive"
            h.partner.inject.ts = {"kind": "TS1"}
        await h.run_until(took(h, st, arch.DQ, t), LIMIT, f"{st} to Detect.Quiet, following")
        await settle(h)


async def ep_loopback_no_echo(h, role):
    """The DUT as Loopback master with the partner frozen in Recovery.Idle
    (idle symbols, no echo): Loopback.Entry times out to Loopback.Exit."""
    t = h.now
    await _partner_frozen_in(h, role, arch.RI)
    h.request(loopback=1)
    await h.run_until(took(h, arch.LBE, arch.LBX, t), LIMIT, "Loopback.Entry timeout")
    h.request(loopback=0)
    await settle(h)


CONTROL_QUICK = (ep_directed, ep_upstream_ignores, ep_request_priority, ep_loopback,
                 ep_training_requests)
CONTROL_FULL = CONTROL_QUICK + (ep_following, ep_loopback_no_echo)


# -------------------------------------------------------------------- tests

@cocotb.test()
async def smoke(dut):
    """L0, both roles: HAS reset values; training to L0; the scrambler's
    known answer on the line; a TLP, a DLLP and a nullified TLP each way;
    a retrain each way; reset inside a packet, then a clean packet."""
    await _smoke(dut)


async def _smoke(dut):
    rng = rng_for(dut)
    h = Harness(dut)
    await h.start()
    for role in arch.ROLES:
        await reset_values(h, role, rng)
        await train(h, role)
        sent = [arch.Packet(False, bytes(range(20))), arch.Packet(True, bytes(range(6))),
                arch.Packet(False, bytes(range(40, 52)), nullify=True)]
        for q in sent:
            h.send(q)
            h.partner.tx_packets.append(arch.Packet(q.dllp, q.data, q.nullify))
        await h.drain()
        h.check_partner()
        assert len(h.upper.got) == 3, f"{role}: the DUT delivered {len(h.upper.got)} of 3"
        # Idle until a SKP ordered set, then the known answer.
        await h.run_until(lambda q: known_answer_seen(q) > 0, 4000,
                          "the known answer after a SKP ordered set")
        h.request(retrain=1)
        await h.run_until(h.in_state(arch.RL), LIMIT, "Recovery after retrain_req_i")
        await h.run_until(h.both_l0, LIMIT)
        t = h.now
        h.partner.retrain_req = True
        await h.run_until(took(h, arch.L0, arch.RL, t), LIMIT, "Recovery on a received TS")
        await h.run_until(h.both_l0, LIMIT)
        await reset_in_packet(h, role)
        dut._log.info("%s: %d cycles checked", role, h.checks)
    return h


@cocotb.test()
async def smoke_arcs(dut):
    """L2, for the N_FTS variants: smoke, then the level-1 arc recipes in
    both roles (QUICK with the power episode, and CONTROL_QUICK), so N_FTS
    sets the FTS the partner sends on its L0s exit and this receiver's
    Rx_L0s.FTS timeout (the smaller of 2 x N_FTS + 32 and 4 x N_FTS + 12
    cycles) at its extremes, and every state runs in these builds."""
    h = await _smoke(dut)
    for role in arch.ROLES:
        for ep in QUICK + CONTROL_QUICK:
            await ep(h, role)
        dut._log.info("%s: arc recipes done, %d cycles checked", role, h.checks)


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
        h.send(arch.Packet(False, bytes(range(30))))
        h.partner.tx_packets.append(arch.Packet(True, bytes(range(6))))
        await h.drain()
        h.check_partner()
        h.release = (h.release[0], h.now - h.t0 + 64)
        await h.run_until(h.in_state(arch.DQ), LIMIT)
        assert h._read()["pipe_rx_polarity_o"] == 0, (
            f"{role}: pipe_rx_polarity_o expected 0 in Detect.Quiet")
        await h.run_until(h.both_l0, LIMIT)


@cocotb.test()
async def dividers(dut):
    """L2: TIMEOUT_DIV 256 and ERR_TIMEOUT_DIV 128 (the entry's params):
    Polling.Active times out after 23437 cycles, every other timeout
    checked exactly by the shadow; the control states' recipes run too, so
    the Hot Reset dwell (976 cycles) and the 2 ms control timeouts (at
    their floor) are checked at these dividers."""
    h = Harness(dut, timeout_div=256, err_timeout_div=128)
    await h.start()
    for role in arch.ROLES:
        for ep in CONTROL_FULL:
            await ep(h, role)
        hr = [c for c, a, b in h.a.arcs if b == arch.HR or a == arch.HR]
        assert h.shadow.dwell_cycles == 976 and hr, f"{role}: no Hot Reset at these dividers"
        await h.fresh(role)
        h.partner.inject.mute = True
        await h.run_until(h.in_state(arch.PC), LIMIT)
        entry = next(c for c, a, b in h.a.arcs if b == arch.PA and c >= h.t0)
        pc = next(c for c, a, b in h.a.arcs if b == arch.PC and c >= h.t0)
        assert pc - entry == 23437, f"{role}: Polling.Active timeout {pc - entry} cycles"
        h.partner.inject.mute = False
        await settle(h)
        await h.fresh(role)
        await h.run_until(lambda q: q.partner.state == arch.PCFG, LIMIT)
        h.partner.inject.freeze = True
        await h.run_until(took(h, arch.LWS, arch.DQ), LIMIT)
        await settle(h)


@cocotb.test()
async def train_spec(dut):
    """L2: both dividers at 1 (the entry's params): reset release to L0
    within 12.0 to 12.2 ms and Polling.Active to L0 within 65.536 to 80 us
    (HAS targets), in both roles."""
    h = Harness(dut, timeout_div=1, err_timeout_div=1)
    await h.start()
    for role in arch.ROLES:
        await h.fresh(role)
        await h.run_until(h.both_l0, 2_000_000, "training at dividers 1")
        t = h.t0 + h.release[0]
        l0 = next(c for c, a, b in h.a.arcs if b == arch.L0 and c >= h.t0) - t
        pa = next(c for c, a, b in h.a.arcs if b == arch.PA and c >= h.t0) - t
        up_us, trn_us = arch.cycles_to_us(l0), arch.cycles_to_us(l0 - pa)
        dut._log.info("%s: link-up %.3f us, training %.3f us", role, up_us, trn_us)
        assert 12_000 <= up_us <= 12_200, f"{role}: link-up {up_us} us"
        assert 65.536 <= trn_us <= 80, f"{role}: training {trn_us} us"


async def traffic_run(dut, packets, p_big, p_retrain, big_max=2800):
    """Random packets both ways in each role, with gaps, long TLPs past the
    SKP interval, lane shifts from the partner's SKP ordered sets and DUT
    retrains inside packets (aborts); every symbol and delivered packet is
    checked against the shadow, and the partner must get what was sent."""
    rng = rng_for(dut)
    h = Harness(dut)
    await h.start()
    for role in arch.ROLES:
        await train(h, role, partner_link=0 if role == "downstream" else rng.randrange(256))
        retrains = 0
        for i in range(packets):
            h.send(rand_packet(rng, 512, p_big, big_max), gap=rng.choice((0, 0, 0, 1, 5, 40)))
            if rng.random() < 0.6:
                h.partner.tx_packets.append(rand_packet(rng, 512, p_big, big_max))
            if rng.random() < 0.05:
                h.partner.inject.skp_adjust = rng.choice((1, -1))
            if rng.random() < 0.3:
                await h.run(rng.randrange(1, 120))
            if rng.random() < p_retrain and h.upper.cur is not None:
                h.request(retrain=1)
                retrains += 1
                await h.run_until(h.both_l0, LIMIT)
        await h.drain(LIMIT)
        h.check_partner(aborted=retrains)
        dut._log.info("%s: %d sent, %d received, %d retrains, %d cycles checked", role,
                      len(h.upper.sent), len(h.upper.got), retrains, h.checks)


@cocotb.test()
async def traffic(dut):
    """L1, seeded: random traffic both ways (traffic_run)."""
    await traffic_run(dut, 120, 0.05, 0.03)


@cocotb.test()
async def traffic_long(dut):
    """L2, seeded: the same, longer and with more long TLPs, up to the
    largest (4122 bytes), whose SKP deferral takes the SKP credit past
    4096."""
    await traffic_run(dut, 600, 0.1, 0.02, big_max=4122)


@cocotb.test()
async def tx_bubble(dut):
    """L1: a bubble inside a packet aborts it with EDB; its remaining beats are
    taken and discarded, it arrives bad, and later packets are intact. The
    shadow cannot leave a bubble, so symbol comparison stops at it."""
    h = Harness(dut)
    await h.start()
    for role in arch.ROLES:
        await train(h, role)
        h.send(arch.Packet(False, bytes(range(16))))
        await h.drain()
        h.compare_tx = False
        h.abort_kind = "abort_bubble"
        h.send(arch.Packet(False, bytes(range(64))), bubble=5)
        h.send(arch.Packet(True, bytes(range(6))), gap=8)
        h.send(arch.Packet(False, bytes(range(8))))
        await h.drain()
        h.check_partner(aborted=1)
        got = h.partner.rx_packets
        assert got[1].err and not got[1].nullify, f"{role}: the bubbled packet arrived {got[1]}"
        h.abort_kind = "abort_l0_exit"


class Crafter:
    """A line hook that replaces the DUT's received words with a crafted
    symbol stream (plain symbols, scrambled here after a COM so the DUT's
    descrambler keys them), with per-word RxValid, electrical idle and
    RxStatus overrides. With ``hold_idle`` the line stays in electrical idle
    once the crafted words are used up."""

    def __init__(self, hold_idle: bool = False) -> None:
        self.words: deque = deque()
        self.scr = arch.Scrambler()
        self.in_os = 0
        self.hold_idle = hold_idle

    def symbols(self, syms, valid=1, status=0) -> None:
        """Queue plain symbols ((k, byte) pairs, an even count)."""
        out = []
        for k, b in syms:
            out.append((k, b))
        for i in range(0, len(out), 2):
            self.words.append((out[i], out[i + 1], valid, status))

    def idle(self, n: int) -> None:
        """Queue ``n`` words of electrical idle (RxValid 0, RxElecIdle 1)."""
        for _ in range(n):
            self.words.append(None)

    def __call__(self, h, rx):
        if not self.words:
            if self.hold_idle:
                return arch.PipeRx(elec_idle=True, phy_status=rx.phy_status)
            return rx
        w = self.words.popleft()
        if w is None:
            return arch.PipeRx(elec_idle=True, phy_status=rx.phy_status)
        s0, s1, valid, status = w
        syms = []
        for k, b in (s0, s1):
            if not valid:
                syms.append((k, b))
                continue
            if k and b == 0xBC:
                self.in_os = 1
                syms.append((k, self.scr.feed(k, b, False)))
                continue
            if self.in_os and k and b in (0x1C,):
                syms.append((k, self.scr.feed(k, b, False)))
                continue
            self.in_os = 0
            syms.append((k, self.scr.feed(k, b, not k)))
        return arch.PipeRx(syms=tuple(syms), valid=bool(valid), elec_idle=False,
                           status=status, phy_status=rx.phy_status)


def frame(dllp, data, end=arch.END):
    return [arch.SDP if dllp else arch.STP] + [(0, x) for x in data] + [end]


IDLE = (0, 0)


@cocotb.test()
async def rx_errors(dut):
    """L1, seeded: crafted receive streams in L0, after a SKP ordered set
    that resets the descrambler: good packets in either lane, nullified
    TLPs, and every malformed case of the symbols page (STP, COM or another
    K inside a packet, empty, odd and wrong-length packets, a DLLP ended by
    EDB, data, K, END or EDB outside a packet), RxValid drops and RxStatus
    errors; then electrical idle and a DUT retrain inside a packet. The
    shadow is the reference for packets, receiver errors and state."""
    rng = rng_for(dut)
    h = Harness(dut)
    await h.start()
    for role in arch.ROLES:
        await train(h, role)
        for burst in range(12):
            c = Crafter()
            items = [[arch.COM, arch.SKP, arch.SKP, arch.SKP]]
            for _ in range(rng.randrange(6, 14)):
                n = 2 * rng.randrange(1, 12)
                data = [rng.randrange(256) for _ in range(n)]
                kind = rng.choice(("tlp", "dllp", "nullify", "stp_in", "com_in", "k_in",
                                   "empty", "odd", "dllp_len", "dllp_edb", "data_out",
                                   "end_out", "k_out", "idle"))
                if kind == "tlp":
                    items.append(frame(False, data))
                elif kind == "dllp":
                    items.append(frame(True, data[:6] + [0] * (6 - len(data[:6]))))
                elif kind == "nullify":
                    items.append(frame(False, data, arch.EDB))
                elif kind == "stp_in":
                    items.append([arch.STP] + [(0, x) for x in data[:3]] + frame(False, data))
                    COV_PKT.sample(rx_error="framing")
                elif kind == "com_in":
                    items.append([arch.STP] + [(0, x) for x in data[:2]]
                                 + [arch.COM, arch.SKP, arch.SKP, arch.SKP])
                    COV_PKT.sample(rx_error="framing")
                elif kind == "k_in":
                    items.append([arch.STP, (0, 1), arch.PAD, (0, 2)])
                    COV_PKT.sample(rx_error="framing")
                elif kind == "empty":
                    items.append([arch.STP, arch.END])
                    COV_PKT.sample(rx_error="empty")
                elif kind == "odd":
                    items.append(frame(False, data[:-1]) + [IDLE])
                elif kind == "dllp_len":
                    items.append(frame(True, data[:4]))
                elif kind == "dllp_edb":
                    items.append(frame(True, (data + [0] * 6)[:6], arch.EDB))
                elif kind == "data_out":
                    items.append([(0, rng.randrange(1, 256)), IDLE])
                    COV_PKT.sample(rx_error="framing")
                elif kind == "end_out":
                    items.append([rng.choice((arch.END, arch.EDB)), IDLE])
                elif kind == "k_out":
                    items.append([arch.PAD, IDLE])
                else:
                    items.append([IDLE] * 2 * rng.randrange(1, 4))
                if rng.random() < 0.3:
                    items.append([IDLE])     # the next item starts in the other lane
            syms = [s for it in items for s in it]
            if len(syms) % 2:
                syms.append(IDLE)
            c.symbols(syms)
            # Word-level impairments inside the burst: RxValid low, RxStatus.
            words = list(c.words)
            for i in range(2, len(words)):
                r = rng.random()
                if r < 0.04:
                    s0, s1, _, st = words[i]
                    words[i] = (s0, s1, 0, st)
                    COV_PKT.sample(rx_error="cut_valid")
                elif r < 0.08:
                    s0, s1, v, _ = words[i]
                    words[i] = (s0, s1, v, rng.randrange(4, 8))
                    COV_PKT.sample(rx_error="status")
            c.words = deque(words)
            h.line_hook = c
            await h.run_until(lambda q: not c.words, 10_000)
            h.line_hook = None
            await h.run(rng.randrange(20, 200))
            assert h.a.state == arch.L0, f"{role}: the DUT left L0 on a crafted burst"
        # Electrical idle inside a packet, a DUT retrain inside one, and a
        # retrain during a resync (leaving Rx_L0 ends it, so Recovery.Idle
        # counts the partner's idle symbols).
        for cut in ("cut_elec_idle", "cut_rx_l0", "resync"):
            c = Crafter()
            if cut == "resync":
                c.symbols([arch.COM, arch.SKP, arch.SKP, arch.SKP, arch.PAD, IDLE]
                          + [(0, 0x55)] * 400)
            else:
                c.symbols([arch.COM, arch.SKP, arch.SKP, arch.SKP]
                          + frame(False, list(range(200))))
            h.line_hook = c
            await h.run(20)
            if cut == "cut_elec_idle":
                h.line_hook = lambda q, rx: arch.PipeRx(elec_idle=True,
                                                         phy_status=rx.phy_status)
                await h.run(4)
            else:
                h.request(retrain=1)
                await h.run(4)
            if cut != "resync":
                COV_PKT.sample(rx_error=cut)
            h.line_hook = None
            await settle(h)
        assert h.rcv_pulses > 0, f"{role}: no receiver error pulse in an error test"
        dut._log.info("%s: %d packets delivered, %d rcv_err_o pulses, %d cycles checked",
                      role, len(h.upper.got), h.rcv_pulses, h.checks)


async def _tail_burst(h, c, what):
    """Play a crafted burst that ends in electrical idle (or idle data);
    return the receiver-error pulses and the packets delivered meanwhile.
    The line stays idle afterwards, so nothing else is received."""
    pulses, got = h.rcv_pulses, len(h.upper.got)
    h.line_hook = c
    await h.run_until(lambda q: not c.words, 1_000, what)
    await h.run(8)                       # the last word through the pipeline
    return h.rcv_pulses - pulses, h.upper.got[got:]


@cocotb.test()
async def rx_idle_tail(dut):
    """L1, directed (#197): the PCS idle tail, a word whose [7:0] is the
    last symbol before electrical idle and whose [15:8] is an EDB the line
    never carried (wiki/src/arch/mac/HAS.md#receive). In each role: a
    nullified TLP followed by idle data is still nullified with no receiver
    error (the control); the same symbols followed by electrical idle (the
    line idles mid-TLP with no EIOS) end bad with one receiver error, as
    does the cut one symbol earlier; a received EDB in [15:8] outside a
    packet is one framing error, then resync (the control for the next
    cases), and without the resync when the receiver leaves Rx_L0 as the
    next word arrives; a 3-byte DLLP from [15:8] ends in the receive
    queue's second slot, one bad DLLP; an EIOS whose last IDL is an idle
    tail starts receive L0s with no receiver error. Upstream only: an EIOS with an idle tail received in
    Rx_L0 while the port has L1 requested but has not started its wait (a
    packet of its own on the line) raises no receiver error. The expected
    outcomes come from the HAS, not the shadow; the shadow checks every
    cycle as usual."""
    h = Harness(dut)
    await h.start()
    skp = [arch.COM, arch.SKP, arch.SKP, arch.SKP]
    eios_odd = [IDLE, arch.COM, arch.EIDL, arch.EIDL, arch.EIDL, arch.EDB]
    data = list(range(1, 21))
    for role in arch.ROLES:
        await train(h, role)
        # The control: a nullified TLP, then idle data, then the line idles.
        c = Crafter(hold_idle=True)
        c.symbols(skp + frame(False, data, arch.EDB) + [IDLE] * 4)
        pulses, got = await _tail_burst(h, c, "nullified TLP")
        assert [g[2:4] for g in got] == [(False, True)], (
            f"{role}: a nullified TLP followed by idle data delivered {got}, "
            "expected one nullified packet")
        assert pulses == 0, f"{role}: {pulses} receiver errors for a nullified TLP"
        await settle(h)
        # Case 1: the same symbols, then electrical idle with no EIOS.
        c = Crafter(hold_idle=True)
        c.symbols(skp + frame(False, data, arch.EDB))
        assert h.a.state == arch.L0 and h.a.rx_sub == arch.RL0
        pulses, got = await _tail_burst(h, c, "idle mid-TLP")
        assert [g[2:4] for g in got] == [(True, False)], (
            f"{role}: a TLP cut by electrical idle at an idle tail delivered {got}, "
            "expected one packet with err and no nullify")
        assert pulses == 1, (f"{role}: {pulses} receiver errors for a TLP cut by "
                             "electrical idle, expected 1")
        COV_PKT.sample(rx_error="idle_tail")
        await settle(h)
        # The same cut one symbol earlier (no idle tail): err, one error.
        c = Crafter(hold_idle=True)
        c.symbols(skp + [arch.STP] + [(0, x) for x in data[:-1]])
        pulses, got = await _tail_burst(h, c, "idle mid-TLP, even")
        assert [g[2:4] for g in got] == [(True, False)] and pulses == 1, (
            f"{role}: a TLP cut by electrical idle after [15:8] delivered {got} with "
            f"{pulses} receiver errors, expected one bad packet and 1")
        await settle(h)
        # A received EDB in [15:8] outside a packet is one framing error,
        # followed by idle data or by a data symbol, one word later (so its
        # own error would be a second pulse), that the resync discards.
        for after in ([IDLE, IDLE], [IDLE, IDLE, (0, 0x55), IDLE]):
            c = Crafter(hold_idle=True)
            c.symbols(skp + [IDLE, arch.EDB] + after + [IDLE] * 4)
            pulses, got = await _tail_burst(h, c, "EDB outside a packet")
            assert pulses == 1 and not got, (
                f"{role}: a received EDB outside a packet, then {after}, gave {pulses} "
                f"receiver errors, {got}; expected 1 (then resync)")
            await settle(h)
        # The same EDB, then the receiver leaves Rx_L0 (a retrain 0 to 2
        # cycles after the word, so the next word arrives as it leaves): the
        # deferred framing error is taken without a resync (#90). The
        # shadow checks rcv_err_o and the state every cycle.
        for delay in range(3):
            c = Crafter()
            c.symbols(skp + [IDLE, arch.EDB] + [IDLE] * 40)
            h.line_hook = c
            await h.run(2 + delay)
            h.request(retrain=1)
            await h.run(40)
            h.line_hook = None
            await settle(h)
        # A wrong-length DLLP of 3 bytes from [15:8] (#90): its last byte is
        # the second beat pushed in its word, so the receive queue's second
        # slot carries a DLLP's end. One bad DLLP, one receiver error.
        c = Crafter()
        c.symbols(skp + [IDLE, arch.SDP, (0, 1), (0, 2), (0, 3), arch.END] + [IDLE] * 4)
        pulses, got = await _tail_burst(h, c, "3-byte DLLP")
        assert [(g[0], g[2], g[3]) for g in got] == [(True, True, False)], (
            f"{role}: a 3-byte DLLP delivered {got}, expected one bad DLLP")
        assert pulses == 1, f"{role}: {pulses} receiver errors for a 3-byte DLLP, expected 1"
        h.line_hook = None
        await settle(h)
        # An EIOS ending in an idle tail: receive L0s, no receiver error.
        c = Crafter(hold_idle=True)
        c.symbols(skp + eios_odd)
        pulses, got = await _tail_burst(h, c, "EIOS with an idle tail")
        assert h.a.rx_sub in (arch.RE, arch.RXI), (
            f"{role}: receiver in {h.a.rx_sub} after an EIOS, expected receive L0s")
        assert pulses == 0 and not got, (
            f"{role}: an EIOS with an idle tail gave {pulses} receiver errors, {got}")
        await settle(h)
        if role != "upstream":
            continue
        # Case 2: L1 requested while the DUT sends a long TLP, so its wait
        # (pm_wait) cannot start; the partner's EIOS with an idle tail
        # arrives in Rx_L0.
        h.send(arch.Packet(False, bytes(range(256)) * 2))
        await h.run_until(lambda q: q.upper.cur is not None and q.upper.cur[1] > 4,
                          1_000, "the DUT's packet started")
        t = h.now
        h.request(pm_state=2)
        await h.run(2)
        c = Crafter(hold_idle=True)
        c.symbols(skp + eios_odd)
        pulses, got = await _tail_burst(h, c, "EIOS before the wait")
        assert h.upper.cur is not None, (
            f"{role}: the DUT's packet ended before the EIOS was parsed; the case "
            "needs the wait not yet started")
        assert h.a.state == arch.L0 and h.a.rx_sub == arch.RL0, (
            f"{role}: DUT in {h.a.state}/{h.a.rx_sub}, expected L0 with Rx_L0 "
            "(the EIOS noted for L1 entry)")
        assert pulses == 0 and not got, (
            f"{role}: an EIOS before the L1 wait gave {pulses} receiver errors, {got}")
        COV_PKT.sample(rx_error="idle_tail_out")
        await h.run_until(took(h, arch.L0, arch.L1E, t), LIMIT, "L1 after the wait")
        h.line_hook = None
        await settle(h)


class Mangler:
    """A line hook that rewrites the partner's ordered sets on the DUT's
    receive line: from the next COM, the 16 symbols are replaced by the
    queued sequence, symbol by symbol, from that COM's position."""

    def __init__(self, seqs) -> None:
        self.seqs = deque(seqs)
        self.cur: list = []

    def __call__(self, h, rx):
        if not rx.valid or rx.elec_idle:
            return rx
        out = []
        for k, b in rx.syms:
            if not self.cur and self.seqs and k and b == 0xBC:
                self.cur = list(self.seqs.popleft())
            if self.cur:
                k, b = self.cur.pop(0)
            out.append((k, b))
        return arch.PipeRx(syms=tuple(out), valid=True, elec_idle=False, status=rx.status,
                           phy_status=rx.phy_status)


def ts_syms(link=None, lane=None, n_fts=32, ctl=0, ident=arch.TS1_ID):
    f = (lambda v: arch.PAD if v is None else (v if isinstance(v, tuple) else (0, v)))
    i = ident if isinstance(ident, tuple) else (0, ident)
    return [arch.COM, f(link), f(lane), (0, n_fts), (0, 2), (0, ctl)] + [i] * 10


@cocotb.test()
async def ts_fields(dut):
    """L1, seeded: received TS fields and events through the mac's parser,
    checked every cycle against the independent decoder: with the DUT in
    Polling.Active, the frozen partner sends TS1 with every link number,
    lane number and N_FTS byte, PAD, each control bit and TS2, from both
    lanes; the line then carries EIOS and FTS ordered sets, a K symbol in
    the link and lane fields, a K identifier, an unknown and a mismatched
    identifier and a TS cut by COM. In Configuration.Linkwidth.Start it
    carries inverted TS1 and TS2 identifiers. Training then completes."""
    rng = rng_for(dut)
    h = Harness(dut)
    await h.start()
    role = rng.choice(arch.ROLES)
    await h.fresh(role)
    await h.run_until(h.in_state(arch.PA), LIMIT)
    await h.run_until(lambda q: q.partner.state == arch.PA, LIMIT)
    fields = []
    order = list(range(256))
    rng.shuffle(order)
    for v in order:
        fields.append({"kind": "TS1", "link": v, "lane": order[(v + 7) % 256],
                       "n_fts": v, "ctl": 1 << (v % 5)})
    fields += [{"kind": "TS2", "link": None, "lane": None}] * 4
    script(h.partner, fields, {})
    h.partner.inject.skp_adjust = 1          # later sets start in [15:8]
    await h.run_until(lambda q: not q.partner.inject.ts_script, 20_000, "the TS script")
    h.partner.inject.skp_adjust = -1
    m = Mangler([[arch.COM, arch.EIDL, arch.EIDL, arch.EIDL, arch.COM, arch.FTS, arch.FTS,
                  arch.FTS] + [(0, 0)] * 8,
                 [arch.COM, arch.SKP, arch.SKP, (0, 0), arch.COM, arch.EIDL, arch.EIDL,
                  arch.EIDL, (0, 0), arch.COM, arch.FTS, arch.FTS, arch.FTS] + [(0, 0)] * 3,
                 [arch.COM, arch.SKP, arch.SKP, arch.SKP, (0, 0), arch.COM, arch.EIDL,
                  arch.EIDL, arch.EIDL] + [(0, 0)] * 7,
                 ts_syms(link=arch.STP, lane=arch.SKP),
                 ts_syms(ident=(1, 0x4A)),
                 ts_syms(ident=0x77),
                 ts_syms()[:12] + [(0, 0x45)] * 4,
                 ts_syms()[:9] + [arch.COM, arch.SKP, arch.SKP, arch.SKP] + [(0, 0)] * 3,
                 [arch.COM, arch.FTS, arch.FTS, arch.FTS, arch.COM, arch.EIDL, arch.EIDL,
                  arch.EIDL] + [(0, 0)] * 8])
    h.line_hook = m
    await h.run_until(lambda q: not m.seqs and not m.cur, 20_000, "the mangled sets")
    h.line_hook = None
    await settle(h)
    # Inverted identifiers outside Polling.
    await h.fresh(role)
    await h.run_until(h.in_state(arch.LWS), LIMIT)
    # Both lanes: a leading idle symbol moves the set's COM by one.
    m = Mangler([ts_syms(link=0, ident=0x4A ^ 0xFF), ts_syms(link=0, ident=0x45 ^ 0xFF),
                 [(0, 0)] + ts_syms(link=0, ident=0x4A ^ 0xFF),
                 [(0, 0)] + ts_syms(link=0, ident=0x45 ^ 0xFF)])
    h.line_hook = m
    await h.run_until(lambda q: not m.seqs and not m.cur, 20_000, "the inverted sets")
    h.line_hook = None
    await settle(h)
    dut._log.info("%s: %d cycles checked", role, h.checks)


@cocotb.test()
async def random_soak(dut):
    """L1, seeded: random partner resets, mutes, freezes, retrains, wrong TS
    fields, inverted wires, symbol errors and power-state requests on either
    side, with packets both ways, in both roles, every cycle checked against
    the shadow; the link must recover to L0 at the end."""
    rng = rng_for(dut)
    h = Harness(dut)
    await h.start()
    actions = ("retrain_dut", "retrain_partner", "mute", "freeze", "ts_fields",
               "partner_reset", "corrupt", "traffic", "power", "wait")
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
                                          {"lane": 1}, {"kind": "TS1"}, {"kind": "TS2"},
                                          {"n_fts": rng.randrange(256)}))
                h.request(retrain=1)
                await h.run(rng.randrange(1, 20_000))
                p.inject.ts = {}
            elif act == "partner_reset":
                h.release = (h.release[0], h.now - h.t0 + rng.randrange(1, 5000))
            elif act == "corrupt":
                h.link.pcs[0].corrupt = rng.randrange(1, 40)
                h.request(retrain=int(rng.random() < 0.5))
            elif act == "power":
                # Random power requests on either side (L0s, L1, L2), held for
                # a while with traffic, then withdrawn.
                h.request(pm_state=rng.randrange(4))
                p.pm_state_req = rng.randrange(4)
                for _ in range(rng.randrange(0, 4)):
                    h.send(rand_packet(rng, 64), gap=rng.randrange(0, 600))
                    p.tx_packets.append(rand_packet(rng, 64))
                await h.run(rng.randrange(1, 6000))
                h.request(pm_state=0)
                p.pm_state_req = 0
            elif act == "traffic":
                for _ in range(rng.randrange(1, 12)):
                    h.send(rand_packet(rng, 128), gap=rng.randrange(0, 4))
                    p.tx_packets.append(rand_packet(rng, 128))
            await h.run(rng.randrange(1, 5000))
        await settle(h)
        await h.drain(LIMIT)
        dut._log.info("%s: %d arcs, %d cycles checked", role, len(h.a.arcs), h.checks)


@cocotb.test()
async def control_quick(dut):
    """L1: Hot Reset and Disabled directed by the downstream port, Loopback
    with the DUT master and slave, from L0 and from
    Configuration.Linkwidth.Start, both roles; upstream ignores the
    downstream-only requests."""
    await run_arcs(dut, arch.ROLES, CONTROL_QUICK, False, CONTROL_ARCS)


@cocotb.test()
async def control_full_downstream(dut):
    """L2: every control-state arc, downstream role, partner-initiated
    entries and the timeouts included."""
    await run_arcs(dut, ("downstream",), CONTROL_FULL, True, CONTROL_ARCS)


@cocotb.test()
async def control_full_upstream(dut):
    """L2: every control-state arc, upstream role."""
    await run_arcs(dut, ("upstream",), CONTROL_FULL, True, CONTROL_ARCS)


class RawLine:
    """A line hook that replaces every received word: queued symbols (an
    even count, sent as they are), then idle data 00h, unscrambled."""

    def __init__(self, syms) -> None:
        self.words = deque((syms[i], syms[i + 1]) for i in range(0, len(syms), 2))

    def __call__(self, h, rx):
        s0, s1 = self.words.popleft() if self.words else (IDLE, IDLE)
        return arch.PipeRx(syms=(s0, s1), valid=True, elec_idle=False,
                           phy_status=rx.phy_status)


async def scr_off_first_word(h, role):
    """Disable Scrambling applies on receive from the first word the step
    after Configuration.Idle entry takes. A crafted line, the partner
    frozen: from Lanenum.Accept, TS1 to Complete, one TS2 with the bit
    (the 16 TS2 sent start counting), TS1 that break the run, ``o`` idle
    words, then 8 TS2 with the bit and unscrambled idle. Over the eight
    offsets, one lines the DUT's last TS2 up with the run's end, so its
    first word in Configuration.Idle is an idle symbol it counts at once
    (the shadow shows it). Only the DUT has scrambling off, so each offset
    ends in a fresh reset."""
    hit = False
    nts = lambda ident, ctl=0: ts_syms(link=0, lane=0, ctl=ctl, ident=ident)  # noqa: E731
    for o in range(8):
        await h.fresh(role)
        await h.run_until(h.in_state(arch.LNA), LIMIT)
        h.partner.inject.freeze = True
        syms = (nts(arch.TS1_ID) * 2 + nts(arch.TS2_ID, arch.CTL_NO_SCRAMBLE)
                + nts(arch.TS1_ID) * 20 + [IDLE] * (2 * o)
                + nts(arch.TS2_ID, arch.CTL_NO_SCRAMBLE) * 8)
        h.line_hook = RawLine(syms)
        while h.a.state != arch.L0:
            if h.now - h.t0 > LIMIT:
                raise AssertionError(f"{role}: no L0 on the crafted line, offset {o}")
            await h.step()
            sh = h.shadow
            if sh.state == arch.CI and sh.now == sh.entered + 1 and sh.idle_sent_after == 2:
                hit = True
        assert h.shadow.scr_off, f"{role}: scrambling on after the crafted TS2"
        h.line_hook = None
    await train(h, role)
    assert hit, f"{role}: no offset counted an idle symbol in the first Configuration.Idle step"


@cocotb.test()
async def no_scramble(dut):
    """L1: the partner sends Disable Scrambling in its TS2 in
    Configuration.Complete: from Configuration.Idle both ports send idle
    and packets unscrambled (the shadow checks every symbol), through a
    retrain; Detect.Quiet enables scrambling again. Both roles, each with
    a 12-cycle and a 1-cycle line: on the short line the upstream port's
    first word in Configuration.Idle is already the partner's unscrambled
    idle, which the step entering it must not descramble."""
    h = Harness(dut)
    await h.start()
    for role, delay in ((r, d) for r in arch.ROLES for d in (12, 1)):
        await h.fresh(role, delay=delay)
        h.partner.inject.ts = {"ctl": arch.CTL_NO_SCRAMBLE}
        await h.run_until(h.both_l0, LIMIT, "training with Disable Scrambling")
        assert h.shadow.scr_off and h.partner.scr_off, f"{role}: scrambling still on"
        COV_CTL.sample(scrambling_off=role)
        n = h.txmon.n
        await h.run(200)
        assert list(h.txmon.syms)[-(h.txmon.n - n):].count((0, 0)) > 100, (
            f"{role}: idle symbols not sent as 00h")
        h.send(arch.Packet(False, bytes(range(1, 61))))
        h.partner.tx_packets.append(arch.Packet(True, bytes(range(1, 7))))
        await h.drain()
        h.check_partner()
        h.request(retrain=1)
        await h.run_until(h.in_state(arch.RL), LIMIT)
        await h.run_until(h.both_l0, LIMIT)
        assert h.shadow.scr_off, f"{role}: Recovery enabled scrambling"
        h.partner.inject.__init__()
        h.release = (h.release[0], h.now - h.t0 + 64)   # partner reset: Detect.Quiet
        await h.run_until(h.in_state(arch.DQ), LIMIT)
        assert not h.shadow.scr_off, f"{role}: Detect.Quiet kept scrambling off"
        await h.run_until(h.both_l0, LIMIT)
        assert not h.shadow.scr_off
    for role in arch.ROLES:
        await scr_off_first_word(h, role)
