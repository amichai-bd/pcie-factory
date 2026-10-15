"""Arc sweep of the pcie_mac architecture model (wiki/src/arch/mac/MODEL.md).

Each episode runs two ``Mac`` models of the bound model back to back: ``a``
stands for the device under test, in the role the episode is given, and
``b`` is the link partner. The episode steers ``a`` across arcs only by
``b``'s upper-layer requests, ``b``'s injection hooks and the ``Link``
hooks (``a``'s own requests count too: they are DUT inputs), reacting to
``a``'s state as a DV test reads ``ltssm_state_o``. So each episode is a
recipe the RTL tests can replay against the partner. The one exception is
``a.inject.idle_to_rlock``, which presets the counter instead of looping
255 timeouts; the partner-only recipe is in MODEL.md.

The union of ``a``'s arcs over all episodes of a role is the sweep's
coverage of the HAS arc list for that role. Timeouts run with
TIMEOUT_DIV and ERR_TIMEOUT_DIV at 1024.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace

LIMIT = 400_000

# The model module (pcie_mac_arch), bound with ``use``: the model imports
# this file, so this file never imports the model.
m = SimpleNamespace()


def use(model) -> None:
    """Bind the model module (or a namespace of its globals)."""
    global m
    m = model


def _took(mac, a: str, b: str, after: int = 0):
    return lambda p: any(f == a and t == b and c >= after for c, f, t in mac.arcs)


def _state(mac, s: str):
    return lambda p: mac.state == s


def _l0(p) -> bool:
    return m.both_l0(p)


def _fresh(role: str, **kw):
    return m.make_pair(role, 1024, 1024, **kw)


def _trained(role: str, **kw):
    p = _fresh(role, **kw)
    p.run_until(_l0, LIMIT)
    return p


def _settle(p) -> None:
    for mac in p.macs:
        mac.inject.__init__()
        mac.retrain_req = mac.hot_reset_req = mac.disable_req = mac.loopback_req = False
        mac.pm_state_req = 0
    p.run_until(_l0, LIMIT)


def _script(b, ts: list[dict], then: dict) -> None:
    """Freeze the partner and play an exact TS sequence, then ``then``."""
    b.inject.freeze = True
    b.inject.ts_script = deque(ts)
    b.inject.ts = then


# ------------------------------------------------------------------ episodes

def ep_normal(role: str) -> list:
    """Training, traffic, L0s, retrain, L1, L2 and the control states, with
    the device directing and following."""
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
    # Retrain, then a directed exit while the receiver is in L0s.
    a.retrain_req = True
    p.run_until(lambda q: a.state == m.RL, LIMIT)
    p.run_until(_l0, LIMIT)
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
    # L1 and L2.
    for req, idle in ((2, m.L1I), (3, m.L2I)):
        a.pm_state_req = b.pm_state_req = req
        p.run_until(lambda q: a.state == idle and b.state == idle, LIMIT)
        p.run(100)
        a.pm_state_req = b.pm_state_req = 0
        p.run_until(_l0, LIMIT)
    # Hot Reset and Disabled directed by the downstream port; Loopback
    # mastered by each side.
    dsp = a if a.dsp else b
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
    return [p]


def ep_training_requests(role: str) -> list:
    """Loopback and Disabled requested before the link is up: both leave
    Configuration.Linkwidth.Start, with the device as master or slave and
    directing or following."""
    out = []
    for lb in ("a", "b"):
        p = _fresh(role)
        a, b = p.macs
        master = a if lb == "a" else b
        master.loopback_req = True
        p.run_until(_took(a, m.LWS, m.LBE), LIMIT)
        p.run_until(_state(a, m.LBA), LIMIT)
        master.loopback_req = False
        p.run_until(_l0, LIMIT)
        out.append(p)
    p = _fresh(role)
    a, b = p.macs
    dsp = a if a.dsp else b
    dsp.disable_req = True
    p.run_until(_took(a, m.LWS, m.DIS), LIMIT)
    p.run(500)
    dsp.disable_req = False
    p.run_until(_l0, LIMIT)
    out.append(p)
    return out


def ep_detect_and_polling(role: str) -> list:
    out = []
    # No receiver: the partner leaves reset late.
    p = _fresh(role, skew_cycles=4000)
    p.run_until(_took(p.macs[0], m.DA, m.DQ), LIMIT)
    p.run_until(_l0, LIMIT)
    out.append(p)
    # Partner silent: Polling.Compliance, left when it speaks.
    p = _fresh(role)
    a, b = p.macs
    b.inject.mute = True
    p.run_until(_state(a, m.PC), LIMIT)
    b.inject.mute = False
    p.run_until(_took(a, m.PC, m.PA), LIMIT)
    _settle(p)
    out.append(p)
    # Partner TS with a link number in Polling: Polling.Active times out.
    p = _fresh(role)
    a, b = p.macs
    b.inject.ts = {"link": 7}
    p.run_until(_took(a, m.PA, m.DQ), LIMIT)
    _settle(p)
    out.append(p)
    # Partner stuck sending TS1, then TS2: Polling.Configuration and
    # Configuration.Linkwidth.Start time out.
    for stuck, arc in ((m.PA, (m.PCFG, m.DQ)), (m.PCFG, (m.LWS, m.DQ))):
        p = _fresh(role)
        a, b = p.macs
        p.run_until(_state(b, stuck), LIMIT)
        b.inject.freeze = True
        p.run_until(_took(a, *arc), LIMIT)
        _settle(p)
        out.append(p)
    return out


# TS1 field sets the partner plays to walk the device through Configuration.
_LINK_PAD = {"kind": "TS1", "link": 0, "lane": None}
_LINK_LANE = {"kind": "TS1", "link": 0, "lane": 0}


def _walk(role: str, to: str) -> list[dict]:
    """The TS1 the partner sends, two per arc, to take a device in ``role``
    from Configuration.Linkwidth.Start to ``to``."""
    steps = [_LINK_PAD, _LINK_PAD if role == "downstream" else _LINK_LANE, _LINK_LANE]
    n = {m.LWA: 1, m.LNW: 2, m.LNA: 3}[to]
    return [f for f in steps[:n] for _ in range(2)]


def ep_configuration(role: str) -> list:
    out = []
    cases = ((m.LWA, {"kind": "TS1", "link": None, "lane": None}, m.DQ),
             (m.LNW, {"kind": "TS1", "link": None, "lane": None}, m.DQ),
             (m.LNA, {"kind": "TS1", "link": 0, "lane": 1}, m.LNW),
             (m.LNA, {"kind": "TS1", "link": None, "lane": None}, m.DQ))
    for st, then, to in cases:
        p = _fresh(role)
        a, b = p.macs
        p.run_until(_state(a, m.LWS), LIMIT)
        _script(b, _walk(role, st), then)
        p.run_until(_took(a, st, to), LIMIT)
        _settle(p)
        out.append(p)
    # Partner stuck in Lanenum.Accept (TS1) or Complete (TS2).
    for stuck, arc, preset in ((m.LNA, (m.CC, m.DQ), None), (m.CC, (m.CI, m.RL), None),
                               (m.CC, (m.CI, m.DQ), 255)):
        p = _fresh(role)
        a, b = p.macs
        a.inject.idle_to_rlock = preset
        p.run_until(_state(b, stuck), LIMIT)
        b.inject.freeze = True
        p.run_until(_took(a, *arc), LIMIT)
        _settle(p)
        out.append(p)
    return out


def ep_recovery(role: str) -> list:
    out = []
    # RcvrLock times out: TS with the wrong link number, or none at all.
    p = _trained(role)
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
    p = _trained(role)
    a, b = p.macs
    a.retrain_req = True
    p.run_until(_state(b, m.RL), LIMIT)
    b.inject.freeze = True                       # TS1 with the right numbers
    p.run_until(_state(a, m.RC), LIMIT)
    b.inject.ts = {"kind": "TS1", "link": 3}
    p.run_until(_took(a, m.RC, m.LWS), LIMIT)
    _settle(p)
    a.retrain_req = True
    p.run_until(_state(b, m.RL), LIMIT)
    b.inject.freeze = True
    p.run_until(_took(a, m.RC, m.DQ), LIMIT)
    _settle(p)
    out.append(p)
    # Recovery.Idle: lane PAD, and the idle timeout both ways.
    p = _trained(role)
    a, b = p.macs
    a.retrain_req = True
    p.run_until(_state(b, m.RC), LIMIT)
    b.inject.freeze = True                       # TS2 forever
    p.run_until(_state(a, m.RI), LIMIT)
    b.inject.ts = {"kind": "TS1", "lane": None}
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
    p = _trained(role)
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


EPISODES = (ep_normal, ep_training_requests, ep_detect_and_polling,
            ep_configuration, ep_recovery)
SMOKE = (ep_normal,)


def arcs_hit(pairs: list) -> set[tuple[str, str]]:
    hit: set[tuple[str, str]] = set()
    for p in pairs:
        hit |= {(f, t) for _, f, t in p.macs[0].arcs}
    return hit & m.ARC_SET


def run(role: str, episodes=EPISODES) -> list:
    pairs: list = []
    for ep in episodes:
        pairs.extend(ep(role))
    return pairs


def missed(pairs: list) -> list[str]:
    hit = arcs_hit(pairs)
    return sorted(name for name, arc in m.ARCS.items() if arc not in hit)


def arc_sweep(roles: list[str], full: bool) -> dict[str, float]:
    """Run the episodes (all, or the ``SMOKE`` set) for each role; report
    the arc coverage of the device side per role."""
    out: dict[str, float] = {"arcs_total": len(m.ARC_SET)}
    cycles = 0
    for role in roles:
        pairs = run(role, EPISODES if full else SMOKE)
        hit = arcs_hit(pairs)
        out[f"arcs_hit_{role}"] = len(hit)
        out[f"arcs_hit_percent_{role}"] = round(100.0 * len(hit) / len(m.ARC_SET), 3)
        cycles += sum(p.now for p in pairs)
    out["cycles"] = cycles
    return out
