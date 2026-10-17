"""Unit tests of the partner's passive mode (wiki/src/dv/dll/PARTNER.md,
passive mode). No simulator.

Two partners back to back through ``TapLine``s, which record the sent view
at the sender's output, apply injected drops and corruptions, and record
the delivered view at the receiver's input. A correct pair under any
injection gives no violation; hand-built views show that each check flags
what it should and nothing else.

Run: python3 -m pytest src/dv/dll/models
"""

import random

import pytest

import pcie_dll_partner as m
from pcie_dll_partner_passive import PassiveChecker, ViewPacket

KINDS = ["MWr", "MRd", "CplD", "Cpl", "CfgWr"]
FINITE = {"P": (8, 32), "NP": (8, 8), "Cpl": (8, 32)}
MINIMUM = {"P": (1, 8), "NP": (1, 2), "Cpl": (1, 8)}


class TapLine:
    """One direction: packets from the sender are recorded as sent (times
    in cycles at the sender's output), then dropped or corrupted with the
    given rates, and recorded as delivered at the receiver's input,
    ``delay`` cycles later."""

    def __init__(self, chk: PassiveChecker, direction: str, rng: random.Random,
                 delay: int = 22, drop: float = 0.0, corrupt: float = 0.0) -> None:
        self.chk, self.d, self.rng = chk, direction, rng
        self.delay, self.drop, self.corrupt = delay, drop, corrupt
        self.pipe: list = [None] * delay
        self.cur: list = []
        self.start = 0
        self.mute = False               # the packet in progress is dropped
        self.flip: int | None = None    # byte index to corrupt in this packet
        self.cycle = 0
        self.drop_tlps = False          # drop every TLP (rollover)

    def push(self, beat):
        self.cycle += 1
        out = beat
        if beat is not None:
            if beat.sop:
                self.cur, self.start = [], self.cycle
                tlp = not beat.dllp
                self.mute = (tlp and self.drop_tlps) or self.rng.random() < self.drop
                self.flip = None
                if not self.mute and self.rng.random() < self.corrupt:
                    self.flip = self.rng.randrange(6)
            self.cur.append(beat)
            i = 2 * (len(self.cur) - 1)
            if self.flip is not None and self.flip in (i, i + 1):
                out = beat._replace(data=beat.data ^ (0x10 << (8 * (self.flip - i))))
            if self.mute:
                out = None
            if beat.eop:
                data = bytes(v for x in self.cur for v in (x.data & 0xFF, x.data >> 8))
                self.chk.sent(self.d, ViewPacket(self.start, self.cycle, beat.dllp, data))
                if self.mute:
                    self.chk.inject(self.cycle, self.d, "drop")
                else:
                    if self.flip is not None:
                        data = bytearray(data)
                        data[self.flip] ^= 0x10
                        self.chk.inject(self.cycle, self.d, "corrupt")
                    self.chk.delivered(self.d, ViewPacket(self.start + self.delay,
                                                          self.cycle + self.delay,
                                                          beat.dllp, bytes(data)))
        self.pipe.append(out)
        return self.pipe.pop(0)


def tap_pair(seed, credits=None, drop=0.0, corrupt=0.0, delay=22):
    chk = PassiveChecker()
    rng = random.Random(seed)
    a = m.Partner("a", seed=2 * seed + 1, credits=credits)
    b = m.Partner("b", seed=2 * seed + 2, credits=credits)
    p = m.Pair(a, b, 0)
    p.ab = TapLine(chk, "ab", rng, delay, drop, corrupt)
    p.ba = TapLine(chk, "ba", rng, delay, drop, corrupt)
    a.link_up = b.link_up = True
    return p, chk


def run_traffic(p, n, seed, limit=200_000, stall=0.0):
    rng = random.Random(seed)
    sa = [m.make_tlp(rng.choice(KINDS), rng.randint(1, 32), i) for i in range(n)]
    sb = [m.make_tlp(rng.choice(KINDS), rng.randint(1, 32), 1000 + i) for i in range(n)]
    for x in sa:
        p.a.send(x)
    for x in sb:
        p.b.send(x)
    for _ in range(limit):
        p.step()
        if stall and rng.random() < stall:
            p.b.upper_ready = not p.b.upper_ready
        if len(p.b.received) == n and len(p.a.received) == n:
            break
    p.b.upper_ready = True
    p.run(3000)
    assert p.b.received == sa and p.a.received == sb
    assert p.a.violations == [] and p.b.violations == []


def test_passive_clean_loopback():
    p, chk = tap_pair(1, credits=FINITE)
    run_traffic(p, 200, 1)
    assert chk.check() == []
    for d in ("ab", "ba"):
        assert chk.stats[f"new_{d}"] == 200
        assert chk.stats[f"credit_checked_{d}"] == 200
        assert chk.stats[f"ack_timing_{d}"] == 200
        assert chk.stats.get(f"replays_{d}", 0) == 0
    # The partner's Ack timer is 32 cycles; with a TLP ahead it stays well inside 140.
    assert 32 <= chk.ack_gap_max <= 140


@pytest.mark.parametrize("seed,credits", [(2, FINITE), (3, MINIMUM), (4, None)])
def test_passive_with_injection(seed, credits):
    """Drops and corruptions both ways, of TLPs and DLLPs: replays, Naks
    and lost credit updates, all lossless, and still no violation: injected
    events show only in the delivered view."""
    p, chk = tap_pair(seed, credits=credits, drop=0.02, corrupt=0.03)
    run_traffic(p, 300, seed, stall=0.01 if credits else 0.0)
    assert chk.check() == [], chk.violations[:5]
    assert sum(chk.stats.get(f"replays_{d}", 0) for d in ("ab", "ba")) > 0
    assert len(chk.injections(cls="drop")) > 0 and len(chk.injections(cls="corrupt")) > 0


def test_passive_rollover():
    """Every TLP a to b dropped for 2500 cycles: replays, a rollover (the
    partner's fallback ends the hold), then recovery; no violation."""
    p, chk = tap_pair(5, credits=FINITE)
    p.run(400)
    p.ab.drop_tlps = True
    rng = random.Random(5)
    sa = [m.make_tlp(rng.choice(KINDS), rng.randint(1, 32), i) for i in range(40)]
    for x in sa:
        p.a.send(x)
    p.run(2500)
    assert p.a.stats["rollover"] >= 1
    p.ab.drop_tlps = False
    for _ in range(50_000):
        p.step()
        if len(p.b.received) == 40:
            break
    p.run(2000)
    assert p.b.received == sa
    assert chk.check() == [], chk.violations[:5]
    assert chk.stats["replays_ab"] >= 4


def test_passive_flags_credit_cheat():
    """A sender that ignores the credit gate: a's first transmissions
    exceed the limits b advertised."""
    p, chk = tap_pair(6, credits={"P": (2, 8), "NP": (8, 8), "Cpl": (8, 32)})
    p.run(400)
    p.b.upper_ready = False
    p.a.cr["P"].infinite = [True, True]         # the cheat
    for i in range(6):
        p.a.send(m.make_tlp("MWr", 4, i))
    p.run(1000)
    v = chk.check()
    assert any(x.startswith("credits (ab, sender a)") for x in v), v
    assert not any(x.startswith("credits (ba") for x in v)


# -- hand-built views -----------------------------------------------------------

def tlp(seq, tag=0, start=0, end=None, err=False, bad=False):
    framed = m.frame_tlp(seq, m.make_tlp("MWr", 1, tag))
    if bad:
        framed = framed[:-1] + bytes([framed[-1] ^ 1])
    return ViewPacket(start, start + 10 if end is None else end, False, framed, err)


def dllp(typ, a, b=0, start=0):
    return ViewPacket(start, start + 3, True, m.make_dllp(typ, a, b))


def init_views(chk, t=0):
    """Both ends' InitFC1 for every class, finite, reaching both lines."""
    for d in ("ab", "ba"):
        for cls in m.CLASSES:
            chk.delivered(d, dllp(m.INITFC1[cls], *FINITE[cls], start=t))


def test_flags_replay_not_from_oldest():
    chk = PassiveChecker()
    init_views(chk)
    for i, s in enumerate((0, 1, 2)):
        chk.sent("ab", tlp(s, start=100 + 20 * i))
    chk.sent("ab", tlp(1, start=200))           # no Ack: the oldest is 0
    v = chk.check()
    assert len(v) == 1 and v[0].startswith("sequence (ab") and "replay starts at 1" in v[0]


def test_flags_replay_skipping_an_unacknowledged_tlp():
    chk = PassiveChecker()
    init_views(chk)
    for i, s in enumerate((0, 1, 2)):
        chk.sent("ab", tlp(s, start=100 + 20 * i))
    chk.delivered("ba", dllp(m.NAK, 0, start=150))
    chk.sent("ab", tlp(1, start=300))           # replay from 1: right
    chk.sent("ab", tlp(2, start=320))
    chk.sent("ab", tlp(3, start=340))           # new after a complete replay: right
    assert chk.check() == []
    chk.sent("ab", tlp(4, start=360))
    chk.delivered("ba", dllp(m.NAK, 2, start=380))
    chk.sent("ab", tlp(4, start=500))           # skips 3, not acknowledged
    v = chk.check()
    assert len(v) == 1 and "replay starts at 4" in v[0]


def test_flags_new_tlp_before_replay_complete():
    chk = PassiveChecker()
    init_views(chk)
    for i, s in enumerate((0, 1, 2)):
        chk.sent("ab", tlp(s, start=100 + 20 * i))
    chk.delivered("ba", dllp(m.NAK, 4095, start=170))
    chk.sent("ab", tlp(0, start=300))
    chk.sent("ab", tlp(3, start=320))           # 1 and 2 not replayed
    v = chk.check()
    assert len(v) == 1 and "new TLP 3 before the replay" in v[0]


def test_flags_wrong_new_sequence():
    chk = PassiveChecker()
    init_views(chk)
    chk.sent("ab", tlp(0, start=100))
    chk.sent("ab", tlp(2, start=120))
    v = chk.check()
    assert len(v) == 1 and "TLP 2 is neither new" in v[0]


def test_replay_window_tolerates_a_late_ack():
    """An Ack that arrived within ack_window cycles of the replay may not
    have been seen yet: a replay from the older TLP is accepted; one from
    before the Ack-window bound is not."""
    chk = PassiveChecker(ack_window=40)
    init_views(chk)
    for i, s in enumerate((0, 1, 2)):
        chk.sent("ab", tlp(s, start=100 + 20 * i))
    chk.delivered("ba", dllp(m.ACK, 0, start=280))   # ends 283
    chk.sent("ab", tlp(0, start=300))           # 17 cycles after: still allowed
    chk.sent("ab", tlp(1, start=320))
    chk.sent("ab", tlp(2, start=340))
    assert chk.check() == []
    chk2 = PassiveChecker(ack_window=40)
    init_views(chk2)
    for i, s in enumerate((0, 1, 2)):
        chk2.sent("ab", tlp(s, start=100 + 20 * i))
    chk2.delivered("ba", dllp(m.ACK, 0, start=200))  # 97 cycles before the replay
    chk2.sent("ab", tlp(0, start=300))
    v = chk2.check()
    assert len(v) == 1 and "TLP 0 is neither new (3) nor sent and unacknowledged (1 to 2)" in v[0]


def test_flags_credit_overrun_and_respects_updatefc():
    chk = PassiveChecker()
    for cls in m.CLASSES:
        chk.delivered("ba", dllp(m.INITFC1[cls], *MINIMUM[cls]))
    chk.sent("ab", tlp(0, start=100))           # 1 P header: fits
    chk.sent("ab", tlp(1, start=200))           # a second: over
    v = chk.check()
    assert len(v) == 1 and v[0].startswith("credits (ab") and "needs 1 header" in v[0]
    chk2 = PassiveChecker()
    for cls in m.CLASSES:
        chk2.delivered("ba", dllp(m.INITFC1[cls], *MINIMUM[cls]))
    chk2.sent("ab", tlp(0, start=100))
    chk2.delivered("ba", dllp(m.UPDATEFC["P"], 2, 9, start=150))   # one more of each
    chk2.sent("ab", tlp(1, start=200))
    chk2.sent("ab", tlp(1, start=400))          # a replay consumes nothing
    chk2.delivered("ba", dllp(m.NAK, 0, start=300))
    assert chk2.check() == []


def test_updatefc_counts_only_once_on_the_line():
    """An UpdateFC that reaches the line after the TLP started does not
    cover it."""
    chk = PassiveChecker()
    for cls in m.CLASSES:
        chk.delivered("ba", dllp(m.INITFC1[cls], *MINIMUM[cls]))
    chk.sent("ab", tlp(0, start=100))
    chk.sent("ab", tlp(1, start=200))
    chk.delivered("ba", dllp(m.UPDATEFC["P"], 2, 9, start=199))   # ends 202
    assert len(chk.check()) == 1


def test_flags_ack_ahead_and_ignores_injected_damage():
    chk = PassiveChecker()
    chk.delivered("ab", tlp(0, start=100, end=110))
    chk.delivered("ab", tlp(1, start=120, end=130, bad=True))   # injected: LCRC bad
    chk.delivered("ab", tlp(2, start=140, end=150))             # ahead: not good in order
    chk.sent("ba", dllp(m.ACK, 0, start=160))
    chk.sent("ba", dllp(m.NAK, 0, start=170))
    assert chk.check() == []
    chk.sent("ba", dllp(m.ACK, 2, start=180))   # 1 never arrived good
    v = chk.check()
    assert len(v) == 1 and v[0].startswith("acknak (ba, sender b)") and "Ack 2" in v[0]


def test_ack_timing_bound_and_exemption():
    chk = PassiveChecker(cycle=8, ack_bound=140)  # times in ns, 8 ns cycles
    chk.delivered("ab", tlp(0, start=0, end=800))
    chk.sent("ba", dllp(m.ACK, 0, start=800 + 8 * 140))           # exactly the bound
    chk.delivered("ab", tlp(1, start=2000, end=2800))
    chk.sent("ba", dllp(m.ACK, 1, start=2800 + 8 * 141))          # one cycle late
    v = chk.check()
    assert len(v) == 1 and v[0].startswith("ack_timing (ba") and "TLP 1" in v[0]
    assert chk.ack_gap_max == pytest.approx(141)
    chk.exempt(3000, 3100, "retrain")
    assert chk.check() == []
    assert chk.stats["ack_timing_exempt_ab"] == 1


def test_ack_timing_flags_a_tlp_never_acknowledged():
    chk = PassiveChecker()
    chk.delivered("ab", tlp(0, start=0, end=10))
    chk.sent("ba", dllp(m.UPDATEFC["P"], 1, 8, start=500))   # the record goes on
    v = chk.check()
    assert len(v) == 1 and "never acknowledged" in v[0]


@pytest.mark.parametrize("trigger", ["rollover", "link_down", "recovery", None])
def test_rollover_withdrawal_allowed_only_at_a_rollover(trigger):
    """A replay cut short by its newest TLP, then the next replay: allowed
    only when the sender's rollover pulse (or link-down) falls in between.
    A plain Recovery (out of L0, no rollover) is not enough: the offered TLP
    just waits. With no trigger, even a timeout-spaced replay is flagged;
    cut short by two, flagged in any case."""
    def views(cut):
        chk = PassiveChecker()
        init_views(chk)
        for i, s in enumerate((0, 1, 2)):
            chk.sent("ab", tlp(s, start=100 + 20 * i))
        chk.delivered("ba", dllp(m.NAK, 4095, start=170))
        t = 300
        for s in range(0, 3 - cut):
            chk.sent("ab", tlp(s, start=t))
            t += 20
        if trigger == "rollover":
            chk.rollover("a", t + 10)
            chk.retrain("a", t + 50, t + 300)
        elif trigger == "recovery":
            chk.retrain("a", t + 50, t + 300)
        chk.sent("ab", tlp(0, start=t + 356))       # a replay-timeout later
        return chk

    def run(cut):
        chk = views(cut)
        if trigger == "link_down":                  # the sender's state restarts: epoch split
            return chk
        return chk.check()
    if trigger in ("rollover",):
        assert run(1) == []
    elif trigger is None or trigger == "recovery":
        v = run(1)
        assert len(v) == 1 and "before the one from 1 to 2 completed" in v[0]
    if trigger == "rollover":
        v = run(2)
        assert len(v) == 1 and "before the one from 0 to 2 completed" in v[0]
    if trigger == "link_down":
        chk = PassiveChecker()
        chk._sel = {}
        assert chk._withdrawn("ab", [1, 2], 4095, 400.0, 300.0) is False
        chk.link_down("a", 350.0)
        assert chk._withdrawn("ab", [1, 2], 4095, 400.0, 300.0) is True

def test_line_unsure_ack_widens_the_replay_start():
    """An Ack delivered inside a line-impairment range may have been lost
    after the tap: a replay from before it is accepted, while a replay
    that skips a TLP the Ack does not cover is still flagged."""
    def views(line):
        chk = PassiveChecker()
        init_views(chk)
        for i, s in enumerate((0, 1, 2)):
            chk.sent("ab", tlp(s, start=100 + 20 * i))
        chk.delivered("ba", dllp(m.ACK, 1, start=200))
        if line:
            chk.exempt(150, 400, "line flip", line=True)
        chk.sent("ab", tlp(0, start=900))           # the sender never saw Ack 1
        chk.sent("ab", tlp(1, start=920))
        chk.sent("ab", tlp(2, start=940))
        return chk
    assert len(views(False).check()) == 2           # TLPs 0 and 1 were acknowledged
    assert views(True).check() == []
    chk = views(True)
    chk.sent("ab", tlp(3, start=960))
    chk.delivered("ba", dllp(m.NAK, 2, start=1000))  # outside the range: surely seen
    chk.sent("ab", tlp(3, start=1100))              # the replay from 3: right
    assert chk.check() == []
    chk.sent("ab", tlp(0, start=1200))              # 0 to 2 surely acknowledged
    v = chk.check()
    assert len(v) == 1 and "TLP 0 is neither new" in v[0]


@pytest.mark.parametrize("mark", [True, False])
def test_link_down_starts_a_new_epoch(mark):
    """Both ends' LinkUp falls mid-traffic (each finishes the packet it
    started) and returns: sequence numbers and credits restart at 0. With
    the link-downs recorded the checker passes; without, the restart reads
    as a sequence error."""
    p, chk = tap_pair(9, credits=FINITE)
    rng = random.Random(9)
    for i in range(200):
        p.a.send(m.make_tlp("MWr", 32, i))
        p.b.send(m.make_tlp(rng.choice(KINDS), rng.randint(1, 32), 1000 + i))
    p.run(1501)                         # inside a's stream of 73-beat TLPs
    p.a.link_up = p.b.link_up = False
    if mark:
        chk.link_down("a", p.ab.cycle)
        chk.link_down("b", p.ba.cycle)
    p.run(150)
    p.a.link_up = p.b.link_up = True
    p.run(20_000)
    assert p.a.stats.get("tail_finished", 0) == 1
    assert len(p.b.received) >= 150 and p.a.violations == [] and p.b.violations == []
    v = chk.check()
    if mark:
        assert v == [], v[:3]
    else:
        assert any(x.startswith("sequence") for x in v)


@pytest.mark.parametrize("retrain", [True, False])
def test_cut_tlp_counts_as_sent_only_at_a_retrain(retrain):
    """A TLP the phy cut short on the line (err in the sent view) used its
    sequence number: the next new TLP follows it, and its replay is a
    replay. One cut before its first DW is not judged on credits. A cut
    is allowed only when the sender left L0 (or its link went down) at
    that time; in L0 it is flagged."""
    chk = PassiveChecker(cut_slack=20)
    init_views(chk)
    chk.sent("ab", tlp(0, start=100))
    cut = tlp(1, start=120)
    chk.sent("ab", cut._replace(data=cut.data[:9], end=125, err=True))
    chk.sent("ab", ViewPacket(130, 131, False, tlp(2).data[:5], True))   # too short to read
    chk.sent("ab", tlp(3, start=140))
    chk.delivered("ba", dllp(m.NAK, 0, start=300))
    chk.sent("ab", tlp(1, start=400))
    chk.sent("ab", tlp(2, start=420))
    chk.sent("ab", tlp(3, start=440))
    if retrain:
        chk.retrain("a", 128, 135)
        assert chk.check() == []
    else:
        v = chk.check()
        assert len(v) == 2 and all("cut short on the line while the sender was in L0" in x for x in v)
    assert chk.stats["cut_ab"] == 2 and chk.stats["credit_unread_ab"] == 1


def test_duplicate_acknowledged_in_time():
    """A good duplicate is Acked at once: an Ack covering it within the
    bound passes; a late one is flagged."""
    def views(gap):
        chk = PassiveChecker(cycle=1, ack_bound=140)
        chk.delivered("ab", tlp(0, start=0, end=10))
        chk.sent("ba", dllp(m.ACK, 0, start=50))
        chk.delivered("ab", tlp(0, start=500, end=510))   # the replay: a duplicate
        chk.sent("ba", dllp(m.ACK, 0, start=510 + gap))
        return chk
    chk = views(60)
    assert chk.check() == [] and chk.stats["dup_ack_timing_ab"] == 1
    v = views(200).check()
    assert len(v) == 1 and "duplicate TLP 0" in v[0]
