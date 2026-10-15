"""Arc sweep of the pcie_mac architecture model (wiki/src/arch/mac/LTSSM.md).

Each episode runs two ``Mac`` models of the bound model back to back: ``a`` stands
for the device under test, ``b`` is the link partner, and the episode steers
``a`` across arcs with ``b``'s upper-layer requests and the injection hooks
of both. The union of ``a``'s arcs over all episodes is the sweep's
coverage of the HAS arc list. Each episode is also a recipe for the RTL
tests that must reach the same arc against the partner.

Timeouts run with TIMEOUT_DIV and ERR_TIMEOUT_DIV at 1024.
"""

from __future__ import annotations

from types import SimpleNamespace

LIMIT = 400_000

# The model module (pcie_mac_arch), bound with ``use``: the model imports
# this file, so this file never imports the model.
m = SimpleNamespace()


def use(model) -> None:
    """Bind the model module (or a namespace of its globals)."""
    global m
    m = model


def _took(mac: m.Mac, a: str, b: str, after: int = 0):
    return lambda p: any(f == a and t == b and c >= after for c, f, t in mac.arcs)


def _state(mac: m.Mac, s: str):
    return lambda p: mac.state == s


def _l0(p: m.Pair) -> bool:
    return m.both_l0(p)


def _fresh(role: str = "downstream", **kw) -> m.Pair:
    return m.make_pair(role, 1024, 1024, **kw)


def _trained(role: str = "downstream", **kw) -> m.Pair:
    p = _fresh(role, **kw)
    p.run_until(_l0, LIMIT)
    return p


def _settle(p: m.Pair) -> None:
    for mac in p.macs:
        mac.inject.__init__()
        mac.retrain_req = mac.hot_reset_req = mac.disable_req = mac.loopback_req = False
        mac.pm_state_req = 0
    p.run_until(_l0, LIMIT)


# ------------------------------------------------------------------ episodes

def ep_normal(role: str) -> m.Pair:
    """Training, traffic, L0s, retrain, L1, L2, and the partner-initiated
    control states with ``b`` directing (``a`` follows)."""
    p = _trained(role)
    a, b = p.macs
    a.tx_packets.append(m.Packet(False, bytes(range(40))))
    b.tx_packets.append(m.Packet(True, bytes(6)))
    p.run(200)
    # Transmit and receive L0s, exit by traffic each way.
    a.pm_state_req = b.pm_state_req = 1
    p.run_until(lambda q: a.tx_sub == m.TI and a.rx_sub == m.RXI, LIMIT)
    b.tx_packets.append(m.Packet(False, bytes(20)))
    p.run_until(lambda q: a.rx_packets and a.rx_sub == m.RL0, LIMIT)
    a.tx_packets.append(m.Packet(False, bytes(20)))
    p.run_until(_took(a, m.TF, m.TL0), LIMIT)
    _settle(p)
    # Retrain.
    a.retrain_req = True
    p.run_until(lambda q: a.state == m.RL, LIMIT)
    p.run_until(_l0, LIMIT)
    # Directed exit while the receiver is in L0s.
    b.pm_state_req = 1
    p.run_until(lambda q: a.rx_sub == m.RXI, LIMIT)
    a.retrain_req = True
    p.run_until(lambda q: a.state == m.RL, LIMIT)
    _settle(p)
    # Receive L0s with too many FTS: the FTS timeout.
    b.pm_state_req = 1
    b.inject.fts_extra = 200
    p.run_until(lambda q: a.rx_sub == m.RXI, LIMIT)
    b.tx_packets.append(m.Packet(False, bytes(20)))
    p.run_until(_took(a, m.RF, m.ROFF), LIMIT)
    _settle(p)
    # L1 and L2: the downstream port is directed first.
    dsp, usp = (a, b) if a.dsp else (b, a)
    for req, idle in ((2, m.L1I), (3, m.L2I)):
        dsp.pm_state_req = req
        usp.pm_state_req = req
        p.run_until(lambda q: a.state == idle and b.state == idle, LIMIT)
        p.run(100)
        a.pm_state_req = b.pm_state_req = 0
        p.run_until(_l0, LIMIT)
    # Control states directed by the downstream port; loopback by a.
    for attr, st in (("hot_reset_req", m.HR), ("disable_req", m.DIS)):
        setattr(dsp, attr, True)
        p.run_until(_state(a, st), LIMIT)
        p.run(2000)
        setattr(dsp, attr, False)
        p.run_until(_l0, LIMIT)
    for master in (a, b):
        master.loopback_req = True
        p.run_until(_state(a, m.LBA), LIMIT)
        p.run(500)
        master.loopback_req = False
        p.run_until(_l0, LIMIT)
    return p


def ep_training_requests(role: str) -> list[m.Pair]:
    """Loopback and Disabled requested before the link is up: both leave
    Configuration.Linkwidth.Start."""
    p = _fresh(role)
    a, b = p.macs
    a.loopback_req = True
    p.run_until(_took(a, m.LWS, m.LBE), LIMIT)
    p.run_until(_state(a, m.LBA), LIMIT)
    a.loopback_req = False
    p.run_until(_l0, LIMIT)
    p2 = _fresh(role)
    a, b = p2.macs
    dsp = a if a.dsp else b
    dsp.disable_req = True
    p2.run_until(_took(a, m.LWS, m.DIS), LIMIT)
    p2.run(500)
    dsp.disable_req = False
    p2.run_until(_l0, LIMIT)
    return [p, p2]


def ep_detect_and_polling() -> list[m.Pair]:
    out = []
    # No receiver: the partner leaves reset late.
    p = _fresh(skew_cycles=4000)
    p.run_until(_took(p.macs[0], m.DA, m.DQ), LIMIT)
    p.run_until(_l0, LIMIT)
    out.append(p)
    # Partner silent: Polling.Compliance, left when it speaks.
    p = _fresh()
    a, b = p.macs
    b.inject.mute = True
    p.run_until(_state(a, m.PC), LIMIT)
    b.inject.mute = False
    p.run_until(_took(a, m.PC, m.PA), LIMIT)
    _settle(p)
    out.append(p)
    # Partner TS with a link number in Polling: Polling.Active times out.
    p = _fresh()
    a, b = p.macs
    b.inject.ts = {"link": 7}
    p.run_until(_took(a, m.PA, m.DQ), LIMIT)
    _settle(p)
    out.append(p)
    # Partner stuck sending TS1, then TS2: Polling.Configuration and
    # Configuration.Linkwidth.Start time out.
    for stuck, arc in ((m.PA, (m.PCFG, m.DQ)), (m.PCFG, (m.LWS, m.DQ))):
        p = _fresh()
        a, b = p.macs
        p.run_until(_state(b, stuck), LIMIT)
        b.inject.freeze = True
        p.run_until(_took(a, *arc), LIMIT)
        _settle(p)
        out.append(p)
    return out


def ep_configuration() -> list[m.Pair]:
    out = []
    # Partner TS fields changed while a is held in a Configuration state.
    cases = ((m.LWA, {"kind": "TS1", "link": None, "lane": None}, "pad", m.DQ),
             (m.LNW, {"kind": "TS1", "link": None}, "pad", m.DQ),
             (m.LNA, {"kind": "TS1", "lane": 1}, "renum", m.LNW),
             (m.LNA, {"kind": "TS1", "link": None}, "pad", m.DQ))
    for st, ts, run, to in cases:
        p = _fresh()
        a, b = p.macs
        p.run_until(_state(a, st), LIMIT)
        a.inject.freeze = b.inject.freeze = True
        b.inject.ts = ts
        p.run_until(lambda q: a.runs[run] >= 2, LIMIT)
        a.inject.freeze = b.inject.freeze = False
        p.run_until(_took(a, st, to), LIMIT)
        _settle(p)
        out.append(p)
    # Partner stuck in Lanenum.Accept (TS1) or Complete (TS2).
    for stuck, arc, preset in ((m.LNA, (m.CC, m.DQ), None), (m.CC, (m.CI, m.RL), None),
                               (m.CC, (m.CI, m.DQ), 255)):
        p = _fresh()
        a, b = p.macs
        a.inject.idle_to_rlock = preset
        p.run_until(_state(b, stuck), LIMIT)
        b.inject.freeze = True
        p.run_until(_took(a, *arc), LIMIT)
        _settle(p)
        out.append(p)
    return out


def ep_recovery() -> list[m.Pair]:
    out = []
    # RcvrLock times out: TS with the wrong link number, or none at all.
    p = _trained()
    a, b = p.macs
    b.inject.ts = {"link": 3}
    a.retrain_req = True
    p.run_until(_took(a, m.RL, m.LWS), LIMIT)
    _settle(p)
    b.inject.freeze = True
    a.retrain_req = True
    p.run_until(_took(a, m.RL, m.DQ), LIMIT)
    _settle(p)
    out.append(p)
    # RcvrCfg: TS1 with other numbers, or TS1 that never turn into TS2.
    p = _trained()
    a, b = p.macs
    a.retrain_req = True
    p.run_until(_state(a, m.RC), LIMIT)
    a.inject.freeze = b.inject.freeze = True
    b.inject.ts = {"kind": "TS1", "link": 3}
    p.run_until(lambda q: a.runs["mism"] >= 8, LIMIT)
    a.inject.freeze = b.inject.freeze = False
    p.run_until(_took(a, m.RC, m.LWS), LIMIT)
    _settle(p)
    a.retrain_req = True
    p.run_until(_state(b, m.RL), LIMIT)
    b.inject.freeze = True
    p.run_until(_took(a, m.RC, m.DQ), LIMIT)
    _settle(p)
    out.append(p)
    # Recovery.Idle: lane PAD, and the idle timeout both ways.
    p = _trained()
    a, b = p.macs
    a.retrain_req = True
    p.run_until(_state(b, m.RC), LIMIT)
    b.inject.freeze = True
    p.run_until(_state(a, m.RI), LIMIT)
    a.inject.freeze = True
    b.inject.ts = {"kind": "TS1", "lane": None}
    p.run_until(lambda q: a.runs["lanepad"] >= 2, LIMIT)
    a.inject.freeze = b.inject.freeze = False
    p.run_until(_took(a, m.RI, m.LWS), LIMIT)
    _settle(p)
    for preset, to in ((None, m.RL), (255, m.DQ)):
        t0 = p.now
        a.retrain_req = True
        p.run_until(_state(b, m.RC), LIMIT)
        b.inject.freeze = True
        p.run_until(_state(a, m.RI), LIMIT)
        a.inject.idle_to_rlock = preset
        p.run_until(_took(a, m.RI, to, t0), LIMIT)
        _settle(p)
    out.append(p)
    # Loopback master gets no echo: Loopback.Entry times out.
    p = _trained()
    a, b = p.macs
    a.retrain_req = True
    p.run_until(_state(b, m.RI), LIMIT)
    b.inject.freeze = True
    a.loopback_req = True
    p.run_until(_took(a, m.LBE, m.LBX), LIMIT)
    a.loopback_req = False
    _settle(p)
    out.append(p)
    return out


EPISODES = (
    lambda: [ep_normal("downstream")],
    lambda: [ep_normal("upstream")],
    lambda: ep_training_requests("downstream"),
    lambda: ep_training_requests("upstream"),
    ep_detect_and_polling,
    ep_configuration,
    ep_recovery,
)


def arcs_hit(pairs: list[m.Pair]) -> set[tuple[str, str]]:
    hit: set[tuple[str, str]] = set()
    for p in pairs:
        hit |= {(f, t) for _, f, t in p.macs[0].arcs}
    return hit & m.ARC_SET


def arc_sweep(seed: int) -> dict[str, float]:
    """Run every episode; report the arc coverage of the device side.
    ``seed`` is unused: the episodes are directed."""
    del seed
    pairs: list[m.Pair] = []
    for ep in EPISODES:
        pairs.extend(ep())
    hit = arcs_hit(pairs)
    total = len(m.ARC_SET)
    return {"arcs_total": total, "arcs_hit": len(hit),
            "arcs_missed": total - len(hit),
            "arcs_hit_percent": round(100.0 * len(hit) / total, 3),
            "cycles": sum(p.now for p in pairs)}


def missed(pairs: list[m.Pair]) -> list[str]:
    hit = arcs_hit(pairs)
    return sorted(name for name, arc in m.ARCS.items() if arc not in hit)
