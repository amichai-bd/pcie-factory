"""Unit tests of the independent DLL link partner (wiki/src/dv/dll/PARTNER.md).

Two kinds of test, no simulator:

- One partner driven by hand-built packets (``Bench``), to check each
  receive and transmit rule on its own.
- Two partners back to back (``Pair``), the loopback that shows no loss,
  duplication or reordering with every injection hook on.

Expected values come from the HAS rule text and from this package, never
from the dll architecture model (M2 Q3).

Run: python3 -m pytest src/dv/dll/models
"""

import random

import pytest

import pcie_dll_partner as m
from pcie_dll_partner_crc import crc16_bytes, lcrc_bytes

KINDS = ["MWr", "MRd", "CplD", "Cpl", "Msg", "MsgD", "CfgWr"]
FINITE = {"P": (8, 32), "NP": (8, 8), "Cpl": (8, 32)}


def traffic(rng, n, kinds=KINDS):
    return [m.make_tlp(rng.choice(kinds), rng.randint(1, 32), i) for i in range(n)]


class Bench:
    """One partner whose far end is this test: ``put`` queues a packet to
    the partner, ``run`` clocks it and collects the packets it sends."""

    def __init__(self, **kw):
        self.p = m.Partner("dut-side", **kw)
        self.p.link_up = True
        self.inq: list[m.Beat] = []
        self.out: list[tuple[int, bool, bytes]] = []   # (cycle of eop, dllp, bytes)
        self.cur: list[m.Beat] = []

    def put(self, pkt: bytes, dllp: bool, err=False, nullify=False):
        beats = m.to_beats(pkt, dllp, nullify)
        beats[-1] = beats[-1]._replace(err=err)
        self.inq += beats

    def run(self, cycles):
        for _ in range(cycles):
            b = self.p.clock(self.inq.pop(0) if self.inq else None)
            if b is not None:
                self.cur.append(b)
                if b.eop:
                    data = bytes(v for x in self.cur for v in (x.data & 0xFF, x.data >> 8))
                    self.out.append((self.p.cycle, b.dllp, data))
                    self.cur = []

    def dllps(self, typ=None):
        return [d for _, dl, d in self.out if dl and (typ is None or d[0] == typ)]

    def tlps(self):
        return [d for _, dl, d in self.out if not dl]

    def take(self):
        out, self.out = self.out, []
        return out

    def init(self, credits=FINITE):
        """Bring the partner to DL_Active as a far end with ``credits``."""
        for kinds in (m.INITFC1, m.INITFC2):
            for cls in m.CLASSES:
                self.put(m.make_dllp(kinds[cls], *credits[cls]), True)
            self.run(60)
        self.run(60)
        assert self.p.state == m.ACTIVE
        self.init_out = self.take()


def acknak_seq(d):
    return ((d[2] & 0x0F) << 8) | d[3]


# -- DLLP formats ------------------------------------------------------------


def test_dllp_fields_bit_by_bit():
    """Fields placed bit by bit from the DLLP.md field tables."""
    def place(fields):
        bits = [0] * 32                 # bits[8*byte + (7-bit)] in byte order
        for byte, bit, val in fields:
            bits[8 * byte + 7 - bit] = val
        return bytes(int("".join(map(str, bits[8 * i:8 * i + 8])), 2) for i in range(4))
    rng = random.Random(7)
    for _ in range(50):
        h, d, s = rng.randrange(256), rng.randrange(4096), rng.randrange(4096)
        fc = [(0, 7 - i, (0x90 >> (7 - i)) & 1) for i in range(8)]
        fc += [(1, 5 - i, (h >> (7 - i)) & 1) for i in range(6)]     # HdrFC[7:2]
        fc += [(2, 7 - i, (h >> (1 - i)) & 1) for i in range(2)]     # HdrFC[1:0]
        fc += [(2, 3 - i, (d >> (11 - i)) & 1) for i in range(4)]    # DataFC[11:8]
        fc += [(3, 7 - i, (d >> (7 - i)) & 1) for i in range(8)]     # DataFC[7:0]
        assert m.dllp_body(0x90, h, d) == place(fc)
        ack = [(2, 3 - i, (s >> (11 - i)) & 1) for i in range(4)]
        ack += [(3, 7 - i, (s >> (7 - i)) & 1) for i in range(8)]
        assert m.dllp_body(m.ACK, s) == place(ack)


def test_crc16_known_answer_independent():
    """DLLP CRC-16 known answers derived from the DLLP.md text alone, by a
    third statement of it written here: an MSB-first register (bit 15 is
    x^15) seeded with ones, the body fed in byte order bit 0 first, the
    remainder complemented, then byte 4 = remainder bits 15..8 and byte 5
    = bits 7..0, each byte bit-reversed. Recorded values: UpdateFC-NP
    HdrFC A5h DataFC 9C3h is 90 29 49 C3 + B3 94; Nak 5 is 10 00 00 05 +
    7D 70."""
    def literal(body):
        r = 0xFFFF
        for byte in body:
            for i in range(8):
                fb = (r >> 15 & 1) ^ (byte >> i & 1)
                r = (r << 1) & 0xFFFF
                if fb:
                    r ^= 0x100B
        r ^= 0xFFFF
        rev = lambda v: int(f"{v:08b}"[::-1], 2)        # noqa: E731
        return bytes([rev(r >> 8), rev(r & 0xFF)])
    kat = [(m.dllp_body(0x90, 0xA5, 0x9C3), bytes.fromhex("b394")),
           (m.dllp_body(m.NAK, 5), bytes.fromhex("7d70"))]
    assert kat[0][0] == bytes.fromhex("902949c3")       # DLLP.md field example
    for body, crc in kat:
        assert literal(body) == crc
        assert crc16_bytes(body) == crc
    rng = random.Random(11)
    for _ in range(200):
        body = bytes(rng.randrange(256) for _ in range(4))
        assert literal(body) == crc16_bytes(body)


def test_has_examples_agree():
    """Agreement check with the example values printed on the HAS pages
    (DLLP.md CRC-16 table, RETRY.md LCRC example). They are the HAS's
    numbers, not known answers: a mismatch is a possible disagreement to
    raise in the HAS Open items, never a reason to change the partner."""
    assert crc16_bytes(bytes(4)) == bytes.fromhex("b362")
    assert crc16_bytes(m.dllp_body(m.ACK, 4095)) == bytes.fromhex("25a8")
    assert crc16_bytes(m.dllp_body(0x40, 8, 32)) == bytes.fromhex("f534")
    tlp = bytes.fromhex("000000010001000f00000000")
    assert m.frame_tlp(0, tlp)[-4:] == bytes.fromhex("e1f1e7e2")


def test_tlp_classes_and_credits():
    assert m.tlp_class(m.make_tlp("MWr", 4)) == "P"
    assert m.tlp_class(m.make_tlp("Msg")) == "P"
    assert m.tlp_class(m.make_tlp("MsgD", 2)) == "P"
    assert m.tlp_class(m.make_tlp("MRd")) == "NP"
    assert m.tlp_class(m.make_tlp("CfgWr")) == "NP"
    assert m.tlp_class(m.make_tlp("Cpl")) == "Cpl"
    assert m.tlp_class(m.make_tlp("CplD", 1)) == "Cpl"
    assert m.tlp_class(bytes([0x80]) + bytes(11)) is None      # TLP prefix
    assert m.tlp_credits(m.make_tlp("MWr", 32)) == (1, 8)
    assert m.tlp_credits(m.make_tlp("MWr", 5)) == (1, 2)
    assert m.tlp_credits(m.make_tlp("MRd")) == (1, 0)          # Length 1, no data
    assert m.tlp_payload_dw(bytes([0x40, 0, 0, 0])) == 1024
    with pytest.raises(ValueError):
        m.Partner().send(m.make_tlp("MWr", 4)[:-4])


# -- one partner, hand-driven -----------------------------------------------


def test_initialisation_sequence():
    b = Bench(credits=FINITE)
    b.run(10)
    assert b.p.state == m.FC_INIT1
    first = b.dllps()[:3]
    assert [d[0] for d in first] == [0x40, 0x50, 0x60]
    assert all(crc16_bytes(d[:4]) == d[4:] for d in first)
    b.run(300)                          # resends while nothing is received
    assert len(b.dllps(0x40)) >= 3 and b.p.state == m.FC_INIT1
    b.init()
    assert b.p.avail("P") == (8, 32)
    # Entry UpdateFC for every class.
    assert {d[0] for _, dl, d in b.init_out if dl} >= {0x80, 0x90, 0xA0}


def test_entry_updatefc_for_infinite_class():
    b = Bench(credits={"P": (8, 32), "NP": (8, 8), "Cpl": (0, 0)})
    b.init()
    cpl = [d for _, dl, d in b.init_out if dl and d[0] == 0xA0]
    assert cpl and cpl[0][:4] == m.dllp_body(0xA0, 0, 0)


def test_receive_tlp_rules():
    b = Bench(credits=FINITE)
    b.init()
    t = [m.make_tlp("MWr", 4, i) for i in range(4)]
    b.put(m.frame_tlp(0, t[0]), False)
    eop = b.p.cycle + 17                                # 17 beats
    b.run(20)
    assert b.p.store == [] and b.p.received == [t[0]]
    b.run(40)
    acks = [(c, d) for c, dl, d in b.out if dl and d[0] == m.ACK]
    assert len(acks) == 1 and acknak_seq(acks[0][1]) == 0
    # Ack timer 32 cycles from the TLP's eop, then 3 beats of DLLP.
    assert acks[0][0] - eop == 32 + 2, acks[0][0] - eop
    b.take()
    # Ahead: Bad TLP, Nak 0; again: no second Nak (NAK_SCHEDULED).
    b.put(m.frame_tlp(2, t[2]), False)
    b.put(m.frame_tlp(2, t[2]), False)
    b.run(60)
    naks = b.dllps(m.NAK)
    assert len(naks) == 1 and acknak_seq(naks[0]) == 0
    assert b.p.stats["bad_tlp"] == 2 and b.dllps(m.ACK) == []
    b.take()
    # Duplicate: Ack at once, no error.
    b.put(m.frame_tlp(0, t[0]), False)
    b.run(25)
    assert [acknak_seq(d) for d in b.dllps(m.ACK)] == [0]
    assert b.p.stats["rx_duplicate"] == 1
    b.take()
    # The expected TLP clears NAK_SCHEDULED; a later gap naks again.
    b.put(m.frame_tlp(1, t[1]), False)
    b.put(m.frame_tlp(3, t[3]), False)
    b.run(60)
    assert [acknak_seq(d) for d in b.dllps(m.NAK)] == [1]
    assert b.p.received == t[:2]


def test_receive_bad_packets():
    b = Bench(credits=FINITE)
    b.init()
    t = m.make_tlp("MWr", 2, 9)
    framed = m.frame_tlp(0, t)
    b.put(m.frame_tlp(0, t, nullify=True), False, nullify=True)    # silent
    b.run(40)
    assert b.p.stats["rx_nullified"] == 1 and b.dllps(m.NAK) == []
    assert b.dllps(m.ACK) == []
    assert "bad_tlp" not in b.p.stats
    b.put(framed, False, nullify=True)          # EDB with a good LCRC: bad
    b.run(30)
    assert b.p.stats["bad_tlp"] == 1 and len(b.dllps(m.NAK)) == 1
    b2 = Bench(credits=FINITE)
    b2.init()
    b2.put(framed, False, err=True)             # flagged by phy: bad
    b2.put(framed[:-1] + bytes([framed[-1] ^ 1]), False)      # LCRC
    b2.put(framed[:6] + framed[10:], False)     # length still 2 mod 4, bad LCRC
    b2.put(framed[:-2], False)                  # length not 2 mod 4
    b2.run(60)
    assert b2.p.stats["bad_tlp"] == 4 and b2.p.received == []
    b2.put(framed, False)
    b2.run(60)
    assert b2.p.received == [t]


def test_receive_dllp_checks():
    b = Bench(credits=FINITE)
    b.init()
    bad = m.make_dllp(m.ACK, 4095)
    b.put(bad[:5] + bytes([bad[5] ^ 0x10]), True)
    b.run(10)
    assert b.p.stats["bad_dllp"] == 1 and b.p.violations == []
    b.put(m.make_dllp(m.ACK, 7), True)          # nothing sent: out of range
    b.run(10)
    assert any("DL protocol error" in v for v in b.p.violations)
    b.p.violations.clear()
    body = bytes([0x00, 0x01, 0x0F, 0xFF])      # reserved bit in byte 1
    b.put(body + crc16_bytes(body), True)
    b.run(10)
    assert any("reserved" in v for v in b.p.violations)
    b.p.violations.clear()
    body = bytes([0x30, 0, 0, 0])               # a type this design never sends
    b.put(body + crc16_bytes(body), True)
    body = bytes([0x81, 0, 0, 0])               # UpdateFC for VC 1
    b.put(body + crc16_bytes(body), True)
    b.run(20)
    assert len(b.p.violations) == 2


def test_updatefc_checks():
    b = Bench(credits=FINITE)
    b.init(credits={"P": (8, 32), "NP": (8, 8), "Cpl": (0, 32)})
    b.put(m.make_dllp(0x80, 12, 40), True)
    b.run(10)
    assert b.p.avail("P") == (12, 40)
    b.put(m.make_dllp(0x80, 10, 40), True)      # backwards
    b.put(m.make_dllp(0xA0, 3, 32), True)       # nonzero for an infinite field
    b.run(20)
    assert len(b.p.violations) == 2 and b.p.avail("P") == (12, 40)
    assert b.p.avail("Cpl") == (None, 32)


def test_ack_nak_and_replay():
    b = Bench(credits=FINITE)
    b.init()
    t = [m.make_tlp("MWr", 4, i) for i in range(3)]
    for x in t:
        b.p.send(x)
    b.run(60)
    assert b.tlps() == [m.frame_tlp(i, x) for i, x in enumerate(t)]
    b.take()
    b.put(m.make_dllp(m.NAK, 0), True)          # 0 received: replay 1, 2
    b.run(80)
    assert b.tlps() == [m.frame_tlp(1, t[1]), m.frame_tlp(2, t[2])]
    assert b.p.stats["replay"] == 1 and b.p.replay_num == 1
    b.take()
    b.put(m.make_dllp(m.ACK, 2), True)          # all acknowledged
    b.run(10)
    assert b.p.rbuf == [] and b.p.replay_num == 0 and b.p.replay_timer is None
    b.put(m.make_dllp(m.NAK, 2), True)          # nothing unacknowledged
    b.run(20)
    assert b.tlps() == [] and b.p.stats["replay"] == 1
    assert b.p.violations == []


def test_nak_during_a_tlp_replays_it_too():
    # A Nak that arrives while a new TLP is still going out: that TLP was
    # handed to the line, so the replay resends it after the older ones.
    b = Bench(credits=FINITE)
    b.init()
    t = [m.make_tlp("MWr", 32, i) for i in range(3)]
    for x in t:
        b.p.send(x)
    b.run(90)                                   # 0 sent, 1 going out
    assert b.p.cur is not None and b.p.cur.seq == 1 and b.p.cur.kind == "tlp"
    b.p._rx_acknak(True, 0)                     # Nak 0, as decoded now
    b.run(400)
    seqs = [(d[0] & 0x0F) << 8 | d[1] for d in b.tlps()]
    assert seqs == [0, 1, 1, 2], seqs
    assert b.p.violations == []


def test_replay_timer_and_rollover():
    b = Bench(credits=FINITE)
    b.init()
    t = m.make_tlp("MRd", 0, 1)
    b.p.send(t)
    b.run(10)
    start = b.p.cycle
    b.take()
    b.run(400)
    first = b.out[0][0] if b.out else None
    assert first is not None and 356 <= first - start <= 356 + 10
    b.run(2000)
    assert b.p.stats["rollover"] == 1 and b.p.stats["replay"] >= 4
    # The fourth replay waits for the link to leave L0 and return.
    held = len(b.tlps())
    b.p.in_l0 = False
    b.run(3000)                         # past the 1024-cycle fallback
    assert len(b.tlps()) == held and b.p.rollover_hold is not None
    b.p.in_l0 = True
    b.run(40)
    assert len(b.tlps()) == held + 1 and b.p.stats["rollover_retrained"] == 1


def test_rollover_fallback():
    b = Bench(credits=FINITE, timers=m.Timers(replay_timeout=50, rollover_fallback=300))
    b.init()
    b.p.send(m.make_tlp("MRd", 0, 1))
    b.run(250)
    assert b.p.rollover_hold is not None
    n = len(b.tlps())
    b.run(400)
    assert b.p.stats["rollover_fallback"] == 1 and len(b.tlps()) > n


def test_replay_timer_holds_outside_l0():
    b = Bench(credits=FINITE)
    b.init()
    b.p.send(m.make_tlp("MRd", 0, 1))
    b.run(10)
    b.p.in_l0 = False
    b.run(2000)
    assert "replay_timeout" not in b.p.stats
    b.p.in_l0 = True
    b.run(400)
    assert b.p.stats["replay_timeout"] == 1


def test_overflow_flagged_not_acknowledged():
    b = Bench(credits={"P": (1, 8), "NP": (8, 8), "Cpl": (0, 0)})
    b.init()
    b.p.upper_ready = False
    b.put(m.frame_tlp(0, m.make_tlp("MWr", 4, 0)), False)
    b.put(m.frame_tlp(1, m.make_tlp("MWr", 4, 1)), False)     # no header credit
    b.put(m.frame_tlp(2, m.make_tlp("MWr", 4, 2)), False)     # then "ahead"
    b.run(80)
    assert any("receiver overflow" in v for v in b.p.violations)
    assert b.p.next_rcv_seq == 1 and len(b.p.store) == 1
    assert [acknak_seq(d) for d in b.dllps(m.NAK)] == [0]


def test_credit_gate_and_updatefc():
    """The gate stops at the limit; an UpdateFC raises it."""
    b = Bench(credits=FINITE)
    b.init(credits={"P": (2, 8), "NP": (8, 8), "Cpl": (0, 0)})
    for i in range(4):
        b.p.send(m.make_tlp("MWr", 16, i))      # 4 data credits each
    b.run(200)
    assert len(b.tlps()) == 2 and b.p.avail("P") == (0, 0)
    b.put(m.make_dllp(0x80, 3, 12), True)       # one more TLP's worth
    b.run(200)
    assert len(b.tlps()) == 3


def test_link_down_resets():
    b = Bench(credits=FINITE)
    b.init()
    b.p.send(m.make_tlp("MWr", 2, 0))
    b.run(40)
    b.p.link_up = False
    b.run(80)                           # any started packet finishes first
    assert b.p.state == m.INACTIVE and b.p.rbuf == [] and b.p.next_tx_seq == 0
    assert b.p.stats["lost_on_reset"] == 1
    b.p.link_up = True
    b.init()
    assert b.p.ackd_seq == 4095


def test_link_down_finishes_started_packet():
    """Link-down inside a packet (dll HAS, Decisions, as for the dll): the
    partner sends the rest of a started TLP up to its eop, held by
    tx_ready, then nothing; it is already in DL_Inactive meanwhile. A
    packet not yet started is dropped, and the next link-up's first DLLP
    starts after the old eop, never inside it."""
    b = Bench(credits=FINITE)
    b.init()
    tlp = m.make_tlp("MWr", 32, 7)              # 140 bytes: 73 beats framed
    b.p.send(tlp)
    beats = []
    while len(beats) < 10:
        out = b.p.clock(None)
        if out is not None:
            beats.append(out)
    assert beats[0].sop and not beats[0].dllp
    b.p.link_up = False
    b.p.tx_ready = False                        # the tail waits for the stream
    assert b.p.clock(None) is None and b.p.state == m.INACTIVE and b.p.rbuf == []
    b.p.tx_ready = True
    for _ in range(200):
        out = b.p.clock(None)
        if out is not None:
            beats.append(out)
    assert len(beats) == 73 and beats[-1].eop and not any(x.sop for x in beats[1:])
    data = bytes(v for x in beats for v in (x.data & 0xFF, x.data >> 8))
    assert data == m.frame_tlp(0, tlp)
    assert b.p.stats["tail_finished"] == 1
    # A link-down between packets sends nothing more.
    b.p.link_up = True
    b.init()
    b.p.link_up = False
    assert all(b.p.clock(None) is None for _ in range(50))
    # Link-down and up again inside a tail: the tail ends before FC_INIT1's first DLLP.
    b.p.link_up = True
    b.init()
    b.p.send(m.make_tlp("MWr", 32, 8))
    seen = []
    while len(seen) < 5:
        out = b.p.clock(None)
        if out is not None and (seen or (out.sop and not out.dllp)):
            seen.append(out)
    b.p.link_up = False
    seen.append(b.p.clock(None))                # the tail goes on while down
    b.p.link_up = True
    for _ in range(300):
        out = b.p.clock(None)
        if out is not None:
            seen.append(out)
    eop = next(i for i, x in enumerate(seen) if x.eop)
    assert eop == 72 and not seen[eop].dllp
    assert seen[eop + 1].sop and seen[eop + 1].dllp     # the first InitFC1, after the tail


# -- two partners back to back ------------------------------------------------


def make_pair(seed, inject=None, credits=None, delay=22, skew=0):
    a = m.Partner("a", seed=2 * seed + 1, credits=credits,
                  inject=inject() if inject else None)
    b = m.Partner("b", seed=2 * seed + 2, credits=credits,
                  inject=inject() if inject else None)
    p = m.Pair(a, b, delay)
    a.link_up = True
    p.run(skew)
    b.link_up = True
    return p


def run_until_delivered(p, sa, sb, limit, rng=None, stall=0.0):
    for x in sa:
        p.a.send(x)
    for x in sb:
        p.b.send(x)
    for _ in range(limit):
        p.step()
        if stall and rng.random() < stall:
            p.b.upper_ready = not p.b.upper_ready
        if len(p.b.received) == len(sa) and len(p.a.received) == len(sb):
            break
    p.b.upper_ready = True
    p.run(2000)                         # drain: duplicates would show here


def check_exact(p, sa, sb):
    for got, want, who in ((p.b.received, sa, "b"), (p.a.received, sb, "a")):
        assert len(got) == len(want), f"{who}: {len(got)} of {len(want)} delivered"
        for i, (g, w) in enumerate(zip(got, want)):
            assert g == w, f"{who}: TLP {i}: expected tag {w[4:6].hex()}, got {g[4:6].hex()}"
    assert p.a.violations == [] and p.b.violations == []


@pytest.mark.parametrize("skew", [0, 37, 500])
def test_loopback_init_any_skew(skew):
    p = make_pair(0, credits=FINITE, skew=skew)
    for _ in range(400):
        p.step()
        if p.a.state == p.b.state == m.ACTIVE:
            break
    assert p.a.state == p.b.state == m.ACTIVE
    assert p.a.avail("NP") == (8, 8) and p.b.avail("Cpl") == (8, 32)


def test_loopback_clean():
    rng = random.Random(1)
    sa, sb = traffic(rng, 400), traffic(rng, 400)
    p = make_pair(1, credits=FINITE)
    run_until_delivered(p, sa, sb, 100000)
    check_exact(p, sa, sb)
    for x in (p.a, p.b):
        assert "replay" not in x.stats and "bad_tlp" not in x.stats
        assert x.ack_wait_max <= 22 + 32 + 76 + 3 + 22 + 4


def everything():
    return m.Inject(tx_tlp_drop=.01, tx_tlp_corrupt=.02, tx_tlp_nullify=.02,
                    tx_dllp_drop=.01, tx_dllp_corrupt=.02, rx_tlp_drop=.01,
                    rx_tlp_nak=.01, rx_dllp_drop=.01)


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("credits", [FINITE, None], ids=["finite", "default"])
def test_loopback_lossless_under_injection(seed, credits):
    rng = random.Random(100 + seed)
    sa, sb = traffic(rng, 600), traffic(rng, 600)
    p = make_pair(seed, inject=everything, credits=credits)
    run_until_delivered(p, sa, sb, 400000, rng, stall=0.002 if credits else 0.0)
    check_exact(p, sa, sb)
    for k in ("tx_tlp_dropped", "tx_tlp_corrupted", "tx_nullified",   # injection
              "tx_dllp_dropped", "tx_dllp_corrupted", "rx_tlp_dropped",  # really
              "rx_tlp_naked", "rx_dllp_dropped", "replay", "bad_tlp",    # happened
              "bad_dllp", "rx_nullified"):
        assert p.a.stats.get(k, 0) + p.b.stats.get(k, 0) > 0, k


def test_loopback_delayed_acks_and_forced_naks():
    rng = random.Random(5)
    sa, sb = traffic(rng, 300), traffic(rng, 300)
    p = make_pair(5, credits=FINITE)
    p.b.ack_delay = 400                 # past a's replay timeout
    for x in sa:
        p.a.send(x)
    for x in sb:
        p.b.send(x)
    for c in range(200000):
        p.step()
        if c % 997 == 0:
            p.a.force_nak()
        if len(p.b.received) == 300 and len(p.a.received) == 300:
            break
    p.run(2000)
    check_exact(p, sa, sb)
    assert p.a.stats["replay_timeout"] > 0 and p.b.stats["rx_duplicate"] > 0
    assert p.b.stats["rx_nak"] > 0


def test_loopback_withheld_credits():
    rng = random.Random(6)
    sa = traffic(rng, 200, ["MWr"])
    p = make_pair(6, credits=FINITE)
    p.b.withhold_credits = True
    for x in sa:
        p.a.send(x)
    p.run(5000)
    assert len(p.b.received) < 8        # stuck on b's initial P credits
    p.b.withhold_credits = False
    run_until_delivered(p, [], [], 100000)
    while len(p.b.received) < 200 and p.a.cycle < 200000:
        p.step()
    check_exact(p, sa, [])


def test_loopback_rollover_and_recovery():
    rng = random.Random(7)
    sa = traffic(rng, 50)
    p = make_pair(7, credits=FINITE)
    p.run(400)
    p.b.inject.rx_tlp_drop = 1.0        # every TLP lost one way
    for x in sa:
        p.a.send(x)
    p.run(2500)
    assert p.a.stats["rollover"] >= 1
    p.b.inject.rx_tlp_drop = 0.0
    run_until_delivered(p, [], [], 0)
    while len(p.b.received) < 50 and p.a.cycle < 100000:
        p.step()
    p.run(2000)
    check_exact(p, sa, [])


def test_loopback_detects_credit_cheat():
    """Negative: a sender that ignores the credit gate is caught."""
    p = make_pair(8, credits={"P": (2, 8), "NP": (8, 8), "Cpl": (8, 32)})
    p.run(400)
    p.b.upper_ready = False
    p.a.cr["P"].infinite = [True, True]         # the cheat
    for i in range(6):
        p.a.send(m.make_tlp("MWr", 4, i))
    p.run(1000)
    assert any("receiver overflow" in v for v in p.b.violations)


def test_deterministic():
    def once():
        rng = random.Random(9)
        sa, sb = traffic(rng, 100), traffic(rng, 100)
        p = make_pair(9, inject=everything, credits=FINITE)
        run_until_delivered(p, sa, sb, 100000)
        return p.a.stats, p.b.stats, p.a.cycle
    assert once() == once()


def test_lcrc_bytes_match_framing():
    t = m.make_tlp("CplD", 3, 0x123)
    f = m.frame_tlp(0x456, t)
    assert f[:2] == bytes([0x04, 0x56]) and f[2:-4] == t
    assert f[-4:] == lcrc_bytes(f[:-4])


def test_independence_no_shared_code():
    """M2 Q3: the partner modules (passive mode included) and their tests import only the standard
    library, pytest and the partner's own modules; none uses a dynamic
    import (importlib, runpy, __import__, exec, eval); and no partner
    module names an arch/ path or the architecture model's module names
    in code. Docstrings may name paths."""
    import ast
    import sys
    from pathlib import Path
    here = Path(m.__file__).resolve().parent
    own = {p.stem for p in here.glob("pcie_dll_partner*.py")}
    files = sorted(here.glob("pcie_dll_partner*.py")) + sorted(here.glob("test_pcie_dll_partner*.py"))
    assert len(files) == 6                  # the partner, its CRCs, its passive mode, their tests
    banned_mods = {"importlib", "runpy"}
    banned_calls = {"__import__", "exec", "eval"}
    # The architecture models and the link e2e's line injector (the passive
    # mode reads only the plain views it is given).
    banned_text = ("pcie_dll_crc", "pcie_dll_arch", "pcie_link_arch", "pcie_link_injector")
    for path in files:
        tree = ast.parse(path.read_text())
        docs = {id(n.value) for n in ast.walk(tree)
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                assert top not in banned_mods, f"{path.name}: imports {name}"
                assert top in own or top in sys.stdlib_module_names or top == "pytest", \
                    f"{path.name}: imports {name}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned_calls, f"{path.name}: calls {node.func.id}"
            if path.stem in own and isinstance(node, ast.Constant) \
                    and isinstance(node.value, str) and id(node) not in docs:
                v = node.value
                assert "arch" not in v.replace("\\", "/").split("/"), f"{path.name}: path {v!r}"
                assert not any(t in v for t in banned_text), f"{path.name}: names {v!r}"


def test_malformed_tlp_acknowledged_not_delivered():
    """A good, expected TLP whose byte count disagrees with its header is
    acknowledged, not delivered, its header's credits returned at once,
    and flagged (the far end must never send one)."""
    b = Bench(credits=FINITE)
    b.init()
    short = m.make_tlp("MWr", 4, 1)[:-4]        # header says 4 DW, 3 sent
    b.put(m.frame_tlp(0, short), False)
    b.run(60)
    assert b.p.received == [] and b.p.next_rcv_seq == 1
    assert [acknak_seq(d) for d in b.dllps(m.ACK)] == [0]
    assert b.p.cr["P"].allocated == [9, 33] and b.p.cr["P"].received == [1, 1]
    assert any("malformed" in v for v in b.p.violations)
    b.run(10)
    assert m.make_dllp(0x80, 9, 33) in b.dllps(0x80)   # credits back at once
    # The partner can send one too, for the far end's malformed rule.
    with pytest.raises(ValueError):
        b.p.send(short)
    b.p.send(short, check=False)


def test_receive_order_and_malformed_lengths():
    """RETRY.md receive order: under 6 bytes is bad; a good LCRC over a
    wrong length is malformed (acknowledged), not bad; a payload above
    128 bytes is malformed; an expected TLP only."""
    b = Bench(credits=FINITE)
    b.init()
    b.put(bytes([0, 0, 1, 2]), False)                   # 4 bytes: too short
    b.run(30)
    assert b.p.stats["bad_tlp"] == 1 and len(b.dllps(m.NAK)) == 1
    b2 = Bench(credits=FINITE)
    b2.init()
    odd = m.make_tlp("MRd", 0, 1)[:-2]                  # 10 bytes, good LCRC
    big = m.make_tlp("MWr", 32, 2)
    big = big[:2] + bytes([0, 33]) + big[4:] + bytes(4)  # 33 DW payload
    b2.put(m.frame_tlp(5, odd), False)                  # ahead: Bad TLP, Nak
    b2.put(m.frame_tlp(0, odd), False)                  # expected: malformed
    b2.put(m.frame_tlp(1, big), False)
    b2.run(200)
    assert b2.p.stats["rx_malformed"] == 2 and b2.p.stats["bad_tlp"] == 1
    assert b2.p.next_rcv_seq == 2 and b2.p.received == []
    assert acknak_seq(b2.dllps(m.ACK)[-1]) == 1
    # Credits its header claims come back at once: the odd one (an MRd
    # header) 1 NP header credit, the big one 1 P header and 9 P data.
    assert b2.p.cr["NP"].allocated == [9, 8]
    assert b2.p.cr["P"].allocated == [9, 41]
    with pytest.raises(ValueError):
        b2.p.send(big)
    with pytest.raises(ValueError):
        b2.p.send(bytes([0x80]) + bytes(11), check=False)


def test_np_data_credit_minimum():
    """FC_NPD is 0 (infinite) or at least 2 (#261): 1 is rejected."""
    with pytest.raises(ValueError):
        m.Partner(credits={"P": (8, 32), "NP": (8, 1), "Cpl": (0, 0)})
    for npd in (0, 2):
        p = m.Partner(credits={"P": (8, 32), "NP": (8, npd), "Cpl": (0, 0)})
        assert p.adv["NP"] == (8, npd)


def test_sequence_wrap():
    """Sequence numbers preloaded near 4095 cross to 0: expected TLPs,
    Ack, a duplicate, an ahead TLP with its Nak, and a replay, on both the
    receive and transmit sides."""
    b = Bench(credits=FINITE)
    b.init()
    p = b.p
    p.next_rcv_seq = 4094
    t = [m.make_tlp("MWr", 2, i) for i in range(5)]
    for k, seq in enumerate((4094, 4095, 0)):
        b.put(m.frame_tlp(seq, t[k]), False)
    b.run(120)
    assert p.received == t[:3] and p.next_rcv_seq == 1
    assert acknak_seq(b.dllps(m.ACK)[-1]) == 0
    b.take()
    b.put(m.frame_tlp(4095, t[1]), False)               # duplicate across the wrap
    b.run(30)
    assert [acknak_seq(d) for d in b.dllps(m.ACK)] == [0]
    b.take()
    b.put(m.frame_tlp(2, t[4]), False)                  # ahead: 1 is missing
    b.run(40)
    assert [acknak_seq(d) for d in b.dllps(m.NAK)] == [0]
    b.take()
    # Transmit side: 4094, 4095, 0, 1 go out; Nak 4095 replays 0 and 1.
    p.next_tx_seq, p.ackd_seq = 4094, 4093
    for x in t[:4]:
        p.send(x)
    b.run(150)
    assert [((d[0] & 0xF) << 8) | d[1] for d in b.tlps()] == [4094, 4095, 0, 1]
    b.take()
    b.put(m.make_dllp(m.NAK, 4095), True)
    b.run(80)
    assert [((d[0] & 0xF) << 8) | d[1] for d in b.tlps()] == [0, 1]
    assert p.ackd_seq == 4095 and [s for s, _, _ in p.rbuf] == [0, 1]
    b.put(m.make_dllp(m.ACK, 4094), True)               # behind ACKD_SEQ: error
    b.run(10)
    assert any("DL protocol error" in v for v in p.violations)
    p.violations.clear()
    b.put(m.make_dllp(m.ACK, 1), True)
    b.run(10)
    assert p.rbuf == [] and p.ackd_seq == 1 and p.violations == []


def test_credit_counters_wrap_loopback():
    """520 maximum-payload MWr one way: 4160 posted data credits and 520
    header credits, so both 12-bit and 8-bit counters wrap (several
    times for headers) on both sides, with the receiver stalling now and
    then; every TLP arrives once, in order, with no overflow."""
    rng = random.Random(12)
    sa = [m.make_tlp("MWr", 32, i) for i in range(520)]
    p = make_pair(12, credits=FINITE)
    run_until_delivered(p, sa, [], 200000, rng, stall=0.01)
    check_exact(p, sa, [])
    assert p.a.cr["P"].consumed == [520 % 256, 4160 % 4096]
    assert p.b.cr["P"].received == [520 % 256, 4160 % 4096]
    assert p.a.avail("P") == (8, 32)


def test_prefix_tlp_flagged():
    """A received TLP prefix (Fmt 100) is a far-end violation; it is
    accounted by the HAS Type rule (here NP)."""
    b = Bench(credits=FINITE)
    b.init()
    pre = bytes([0x80, 0, 0, 0]) + bytes(8)
    b.put(m.frame_tlp(0, pre), False)
    b.run(30)
    assert any("TLP prefix" in v for v in b.p.violations)
    assert b.p.cr["NP"].received == [1, 0]


def test_tx_flag_and_nullify_next():
    b = Bench(credits=FINITE)
    b.init()
    i = b.p.inject
    i.tx_tlp_nullify_next, i.tx_tlp_flag_next, i.tx_dllp_flag_next = 1, 1, 1
    raw = []
    t = m.make_tlp("MWr", 2, 3)
    b.p.send(t)
    b.p.force_nak()                                     # a DLLP to flag
    for _ in range(80):
        x = b.p.clock(None)
        if x is not None and x.eop:
            raw.append(x)
    tlp_eops = [x for x in raw if not x.dllp]
    assert tlp_eops[0].nullify and not tlp_eops[0].err      # nullified copy
    assert tlp_eops[1].err and not tlp_eops[1].nullify      # then flagged TLP
    assert any(x.dllp and x.err for x in raw)
    assert b.p.stats["tx_nullified"] == 1 and b.p.stats["tx_tlp_flagged"] == 1
