"""Self-tests of the pcie_tl architecture model; no simulator needed.

Run: python3 -m pytest src/arch/tl (./build ci unit runs them as arch:<ip>).

Each test states a rule of the tl HAS pages (HAS.md, TLP.md, ROUTING.md,
COMPLETIONS.md) as an example, then checks the model against it.
"""

import random

import pytest

import pcie_tl_arch as m

dll = m.dll
RID = 0x0100                       # the host's requester ID in these tests


class Wire:
    """The dll transmit stream: one TLP at a time, a beat per cycle, every
    credit available unless ``credits`` says otherwise."""

    def __init__(self, credits=None):
        self.sent, self.busy_until = [], 0
        self.credits = credits or {}

    def avail(self, cls, f):
        return self.credits.get((cls, f), 4095 if f else 255)

    def offer(self, tlp, now):
        if now < self.busy_until:
            return False
        self.sent.append(tlp)
        self.busy_until = now + len(tlp) // 4
        return True


def make_tl(**kw):
    cfg = m.Cfg(latency=2)
    cfg.bus, cfg.dev = 3, 1
    cfg.mem_en = cfg.bm_en = True
    cfg.bar0, cfg.bar2 = 0x8000_0000, 0x9000_0000
    tl = m.Tl(cfg, **kw)
    return tl, m.App(random.Random(1)), Wire()


def run(tl, app, wire, cycles, start=0):
    for now in range(start, start + cycles):
        tl.step(now, app, wire)
    return start + cycles


def mwr(addr, n=1, ep=0, fbe=0xF, lbe=None):
    lbe = (0xF if n > 1 else 0) if lbe is None else lbe
    return m.mem_tlp(True, addr, n, fbe, lbe, RID, 0, bytes(range(4 * n)), ep=ep)


def mrd(addr, n=1, tag=0, ep=0, fbe=0xF, lbe=None):
    lbe = (0xF if n > 1 else 0) if lbe is None else lbe
    return m.mem_tlp(False, addr, n, fbe, lbe, RID, tag, ep=ep)


# ------------------------------------------------------------ TLP formats

def test_memory_header_fields():
    """3-DW header below 4 GiB, 4-DW above; Length 0 means 1024 DWs."""
    t = m.mem_tlp(False, 0x1234_5678, 1024, 0xF, 0xF, 0xABCD, 7)
    h = m.parse(t)
    assert (h["fmt"], h["type"], h["length"], h["hdr_bytes"]) == (0, 0, 1024, 12)
    assert (h["rid"], h["tag"], h["fbe"], h["lbe"], h["addr"]) == (0xABCD, 7, 0xF, 0xF, 0x1234_5678)
    assert t[2] & 3 == 0 and t[3] == 0
    t = m.mem_tlp(True, 0x1_0000_0010, 2, 0xF, 0x3, 0, 0, bytes(8))
    h = m.parse(t)
    assert (h["fmt"], h["hdr_bytes"], h["addr"], len(t)) == (3, 16, 0x1_0000_0010, 24)


def test_completion_and_config_fields():
    t = m.cpl_tlp(0x0308, m.UR, 4096, RID, 5, 0x44)
    h = m.parse(t)
    assert (h["status"], h["bc"], h["rid"], h["tag"], h["la"], h["cid"]) == \
        (m.UR, 4096, RID, 5, 0x44, 0x0308)
    assert t[6] & 0xF == 0 and t[7] == 0                 # Byte Count 0 is 4096
    t = m.cfg_tlp(True, 2, 3, 0, 0x3FF, 0xF, RID, 1, bytes(4))
    h = m.parse(t)
    assert (h["bus"], h["dev"], h["fn"], h["reg"]) == (2, 3, 0, 0x3FF)


def test_credits_follow_the_dll():
    """tl releases exactly what the dll counted: class, 1 header, data
    credits from Length."""
    for tlp, want in ((mwr(0x8000_0000, 32), (m.P, 1, 8)), (mrd(0, 32), (m.NP, 1, 0)),
                      (m.msg_tlp(0x19, m.R_BROADCAST, 0), (m.P, 1, 0)),
                      (m.cfg_tlp(True, 0, 0, 0, 0, 0xF, 0, 0, bytes(4)), (m.NP, 1, 1)),
                      (m.cpl_tlp(0, 0, 4, 0, 0, 0, bytes(4)), (m.CPL, 1, 1))):
        assert m.credits(tlp) == want == dll.tlp_info(tlp)[:3]


# ------------------------------------------------------- byte enables

@pytest.mark.parametrize("length,fbe,lbe,want", [
    (1, 0x0, 0, 1), (1, 0xF, 0, 4), (1, 0x6, 0, 2), (1, 0x9, 0, 4), (1, 0x8, 0, 1),
    (2, 0xC, 0x3, 4), (32, 0xF, 0xF, 128), (1024, 0xF, 0xF, 4096), (3, 0xE, 0x1, 8)])
def test_byte_count(length, fbe, lbe, want):
    assert m.byte_count(length, fbe, lbe) == want


@pytest.mark.parametrize("length,fbe,lbe,addr,ok", [
    (1, 0x5, 0, 0, True), (1, 0xF, 1, 0, False), (2, 0x0, 0xF, 0, False),
    (2, 0x5, 0xA, 0, True),             # one aligned QW: holes allowed
    (2, 0x5, 0xA, 4, False), (3, 0xE, 0x7, 0, True), (3, 0x6, 0xF, 0, False),
    (3, 0xF, 0x2, 0, False)])
def test_byte_enable_rules(length, fbe, lbe, addr, ok):
    assert m.be_ok(length, fbe, lbe, addr) is ok


def test_split_on_the_read_completion_boundary():
    assert m.split_read(0x100, 32, 0xF, 0xF) == [(0, 32, 128, 0)]
    assert m.split_read(0x104, 32, 0xF, 0xF) == [(0, 32, 128, 4)]          # fits: one
    assert m.split_read(0x104, 64, 0xF, 0xF) == [(0, 31, 256, 4), (31, 32, 132, 0), (63, 1, 4, 0)]
    assert [p[1] for p in m.split_read(0x4, 128, 0xF, 0xF)] == [31, 32, 32, 32, 1]
    assert len(m.split_read(0, 1024, 0xF, 0xF)) == 32
    assert m.split_read(0x10, 1, 0x0, 0) == [(0, 1, 1, 0x10)]          # zero-length read
    # one completion when the data fits: a boundary is a split point, not a must
    assert m.split_read(0x7C, 2, 0x8, 0x1) == [(0, 2, 2, 0x7F)]


def test_split_properties():
    rng = random.Random(3)
    for _ in range(2000):
        n = rng.randint(1, 1024)
        addr = rng.randrange(0, 4096 - 4 * n + 1, 4)
        fbe = rng.choice([0xF, 0xE, 0xC, 0x8])
        lbe = 0 if n == 1 else rng.choice([0xF, 0x7, 0x3, 0x1])
        parts = m.split_read(addr, n, fbe, lbe)
        assert sum(p[1] for p in parts) == n
        assert parts[0][2] == m.byte_count(n, fbe, lbe)
        for k, (dw, cnt, bc, la) in enumerate(parts):
            end = addr + 4 * (dw + cnt)
            assert 4 * cnt <= m.MAX_PAYLOAD
            if k + 1 < len(parts):
                assert end % m.RCB == 0
                assert parts[k + 1][2] == bc - (end - (addr + 4 * dw + (m._lo(fbe) if k == 0 else 0)))


# ------------------------------------------------------- classification

def tc(tlp, value=1):
    """The TLP with its TC field set."""
    return bytes([tlp[0], (tlp[1] & 0x8F) | (value << 4)]) + tlp[2:]


@pytest.fixture
def cfg():
    c = m.Cfg()
    c.mem_en, c.bar0, c.bar2 = True, 0x8000_0000, 0x9000_0000
    return c


@pytest.mark.parametrize("tlp,want", [
    (mwr(0x8000_0100), ("cq", None)),
    (mrd(0x8000_0FFC), ("cq", None)),
    (mwr(0x9000_0010), ("bar2", None)),
    (mrd(0x9000_0800), ("bar2", None)),
    (mwr(0x8000_1000), ("drop", m.E_UR_P)),
    (mrd(0xA000_0000), ("ur", m.E_UR_NP)),
    (m.mem_tlp(False, 0x1_8000_0000, 1, 0xF, 0, RID, 0), ("ur", m.E_UR_NP)),
    (m.mem_tlp(False, 0x8000_0000, 1, 0xF, 0, RID, 0, force_4dw=True), ("cq", None)),
    (mwr(0x8000_0000, ep=1), ("drop", m.E_POISONED_REQ)),
    (mrd(0x8000_0000, ep=1), ("ur", m.E_POISONED_REQ)),
    (mrd(0x8000_0F00, 65), ("malformed", m.E_MALFORMED)),           # crosses 4 KiB
    (mwr(0x8000_0000, 1, lbe=0xF), ("malformed", m.E_MALFORMED)),
    (m.cfg_tlp(False, 0, 0, 0, 0, 0xF, RID, 0), ("cfg", None)),
    (m.cfg_tlp(False, 0, 0, 1, 0, 0xF, RID, 0), ("ur", m.E_UR_NP)),
    (m.cfg_tlp(False, 0, 0, 0, 0, 0xF, RID, 0, type1=True), ("ur", m.E_UR_NP)),
    (m.cfg_tlp(False, 0, 0, 0, 0, 0xF, RID, 0, length=2), ("malformed", m.E_MALFORMED)),
    (m.cfg_tlp(False, 0, 0, 0, 0, 0xF, RID, 0, tc=1), ("malformed", m.E_MALFORMED)),
    (m.cfg_tlp(True, 0, 0, 0, 0, 0xF, RID, 0, bytes(4), ep=1), ("ur", m.E_POISONED_REQ)),
    (mwr(0x8000_1000, ep=1), ("drop", m.E_UR_P)),                   # UR outranks poisoned
    (mrd(0xA000_0000, ep=1), ("ur", m.E_UR_NP)),
    (m.cfg_tlp(True, 0, 0, 1, 0, 0xF, RID, 0, bytes(4), ep=1), ("ur", m.E_UR_NP)),
    (bytes([0x02, 0, 0, 1, 1, 0, 0, 0xF, 0, 0, 0, 0]), ("ur", m.E_UR_NP)),       # I/O read
    (bytes([0x01, 0, 0, 1, 1, 0, 0, 0xF, 0x80, 0, 0, 0]), ("ur", m.E_UR_NP)),    # MRdLk
    (bytes([0x4C, 0, 0, 1, 1, 0, 0, 0xF, 0x80, 0, 0, 0]) + bytes(4), ("ur", m.E_UR_NP)),
    (bytes([0x9C, 0, 0, 0]) + bytes(8), ("malformed", m.E_MALFORMED)),          # undefined
    (m.msg_tlp(m.MSG_UNLOCK, m.R_BROADCAST, 0), ("drop", None)),
    (m.msg_tlp(m.MSG_VDM1, m.R_LOCAL, 0), ("drop", None)),
    (m.msg_tlp(m.MSG_SET_SLOT_POWER, m.R_LOCAL, 0, bytes(4)), ("drop", None)),
    (m.msg_tlp(0x40, m.R_LOCAL, 0), ("drop", None)),
    (m.msg_tlp(m.MSG_PME_TURN_OFF, m.R_BROADCAST, 0), ("pme_ack", None)),
    (m.msg_tlp(0x7E, m.R_LOCAL, 0), ("drop", m.E_UR_P)),                        # VDM Type 0
    (m.msg_tlp(0x30, m.R_TO_RC, 0), ("drop", m.E_UR_P)),
    (m.msg_tlp(0x20, m.R_LOCAL, 0), ("drop", m.E_UR_P)),
    (m.msg_tlp(m.MSG_PME_TURN_OFF, m.R_BROADCAST, 0, ep=1), ("drop", m.E_POISONED_REQ)),
    (tc(m.msg_tlp(m.MSG_PME_TURN_OFF, m.R_BROADCAST, 0)), ("malformed", m.E_MALFORMED)),
    (tc(m.msg_tlp(m.MSG_UNLOCK, m.R_BROADCAST, 0)), ("malformed", m.E_MALFORMED)),
    (tc(m.msg_tlp(m.MSG_SET_SLOT_POWER, m.R_LOCAL, 0, bytes(4))), ("malformed", m.E_MALFORMED)),
    (tc(m.msg_tlp(0x30, m.R_TO_RC, 0)), ("malformed", m.E_MALFORMED)),
    (tc(m.msg_tlp(0x24, m.R_LOCAL, 0)), ("malformed", m.E_MALFORMED)),
    (tc(m.msg_tlp(m.MSG_VDM1, m.R_LOCAL, 0)), ("drop", None)),         # any TC
])
def test_classify(cfg, tlp, want):
    assert m.classify(m.parse(tlp), cfg, 12) == want


def test_bar_hit_needs_memory_space_and_d0(cfg):
    h = m.parse(mrd(0x8000_0000))
    assert m.classify(h, cfg, 12) == ("cq", None)
    cfg.d_state = m.D3HOT
    assert m.classify(h, cfg, 12) == ("ur", m.E_UR_NP)
    cfg.d_state, cfg.mem_en = m.D0, False
    assert m.classify(h, cfg, 12) == ("ur", m.E_UR_NP)


def test_bar0_size_parameter(cfg):
    cfg.bar0 = 0x8000_0000
    h = m.parse(mrd(0x8001_0000))
    assert m.classify(h, cfg, 12)[0] == "ur"
    assert m.classify(h, cfg, 20)[0] == "cq"


# ---------------------------------------------------------- completer side

def test_write_then_read_on_cq_and_credit_release():
    tl, app, wire = make_tl()
    tl.receive(mwr(0x8000_0010, 4), 0)
    tl.receive(mrd(0x8000_0010, 4, tag=9), 0)
    now = run(tl, app, wire, 60)
    assert [p["write"] for p in app.cq_got] == [True, False]
    assert tl.released[0][1:] == (m.P, 1) and tl.released[1][1:] == (m.NP, 0)
    cpl = [m.parse(t) for t in wire.sent]
    assert len(cpl) == 1
    c = cpl[0]
    assert (c["cid"], c["rid"], c["tag"], c["bc"], c["la"], c["status"]) == \
        ((3 << 8) | (1 << 3), RID, 9, 16, 0x10, m.SC)
    assert c["data"] == b"".join(m.App.pattern(0x10 + 4 * i).to_bytes(4, "little")
                                 for i in range(4))
    assert now and tl.occ == [0, 0, 0, 0]


def test_read_waits_for_an_earlier_write_but_a_write_passes_a_waiting_read():
    tl, app, wire = make_tl()
    app.stall = ((0, 30),)
    tl.receive(mwr(0x8000_0000), 0)
    tl.receive(mrd(0x8000_0000), 0)
    run(tl, app, wire, 40)
    assert [p["write"] for p in app.cq_got] == [True, False]
    # nine reads, the application answers none: eight handles, then a write passes
    tl, app, wire = make_tl(fc={"nph": 16})
    app.latency = 10**9
    for i in range(9):
        tl.receive(mrd(0x8000_0000 + 4 * i, tag=i), 0)
    tl.receive(mwr(0x8000_0100), 0)
    run(tl, app, wire, 50)
    assert [p["write"] for p in app.cq_got] == [False] * 8 + [True]
    assert sorted(p["handle"] for p in app.cq_got[:8]) == list(range(8))
    assert sum(1 for r in tl.released if r[1] == m.NP) == 8     # the 9th still holds its credit


def test_config_and_bar2_pass_reads_waiting_for_a_handle():
    tl, app, wire = make_tl(fc={"nph": 16})
    app.latency = 10**9
    for i in range(9):
        tl.receive(mrd(0x8000_0000, tag=i), 0)
    tl.receive(m.cfg_tlp(True, 5, 2, 0, 1, 0x1, RID, 20, (0x0406).to_bytes(4, "little")), 0)
    tl.receive(m.cfg_tlp(False, 5, 2, 0, 1, 0xF, RID, 21), 0)
    tl.receive(mrd(0x9000_0000, 2, tag=22), 0)
    run(tl, app, wire, 60)
    got = [m.parse(t) for t in wire.sent]
    assert [g["tag"] for g in got] == [20, 21, 22]
    assert got[0]["status"] == m.SC and not got[0]["data"] and got[0]["bc"] == 4
    assert got[1]["data"] == (0x0006).to_bytes(4, "little")      # only enabled bytes written
    assert got[1]["cid"] == (5 << 8) | (2 << 3)                    # captured on the write
    assert got[2]["bc"] == 8 and len(got[2]["data"]) == 8


def test_unsupported_request_completions():
    tl, app, wire = make_tl()
    tl.receive(mrd(0xA000_0104, 2, tag=3, fbe=0xC, lbe=0x1), 0)
    tl.receive(m.cfg_tlp(False, 0, 0, 0, 0, 0xF, RID, 4, type1=True), 0)
    tl.receive(mwr(0xA000_0000), 0)
    run(tl, app, wire, 30)
    a, b = (m.parse(t) for t in wire.sent)
    assert (a["status"], a["bc"], a["la"], a["data"]) == (m.UR, 3, 0x06, b"")
    assert (b["status"], b["bc"], b["la"]) == (m.UR, 4, 0)
    assert tl.errors[m.E_UR_NP] == 2 and tl.errors[m.E_UR_P] == 1
    assert len(tl.released) == 3


def test_poisoned_requests():
    tl, app, wire = make_tl()
    tl.receive(mwr(0x8000_0000, ep=1), 0)
    tl.receive(m.cfg_tlp(True, 0, 0, 0, 1, 0xF, RID, 7, bytes(4), ep=1), 0)
    run(tl, app, wire, 30)
    assert not app.cq_got and not tl.cfg.accesses
    (c,) = (m.parse(t) for t in wire.sent)
    assert (c["status"], c["tag"]) == (m.UR, 7)
    assert tl.errors[m.E_POISONED_REQ] == 2 and tl.errors[m.E_UR_NP] == 0


def test_disabled_bytes_sent_as_zero():
    tl, app, wire = make_tl()
    tl.receive(mrd(0x8000_0010, 2, fbe=0xC, lbe=0x1), 0)
    run(tl, app, wire, 40)
    (c,) = (m.parse(t) for t in wire.sent)
    want = bytearray(b"".join(m.App.pattern(0x10 + 4 * i).to_bytes(4, "little") for i in range(2)))
    want[0:2] = bytes(2)
    want[5:8] = bytes(3)
    assert c["data"] == bytes(want) and (c["bc"], c["la"]) == (3, 0x12)


def test_long_read_splits_into_completions():
    tl, app, wire = make_tl()
    tl.receive(mrd(0x8000_0004, 64), 0)
    run(tl, app, wire, 300)
    got = [m.parse(t) for t in wire.sent]
    assert [len(g["data"]) // 4 for g in got] == [31, 32, 1]     # 256 bytes at offset 4
    assert [g["bc"] for g in got] == [256, 132, 4]
    assert [g["la"] for g in got] == [0x04, 0x00, 0x00]


def test_pme_turn_off_answered_in_any_d_state():
    tl, app, wire = make_tl()
    tl.cfg.d_state = m.D3HOT
    tl.receive(m.msg_tlp(m.MSG_PME_TURN_OFF, m.R_BROADCAST, 0), 0)
    run(tl, app, wire, 10)
    (a,) = (m.parse(t) for t in wire.sent)
    assert (a["type"], a["code"], a["rid"]) == (m.T_MSG | m.R_GATHER, m.MSG_PME_TO_ACK, tl.cfg.id)


# ---------------------------------------------------------- requester side

def rq_read(app, tag, addr=0x4000, n=32):
    app.rq.append({"write": False, "addr": addr, "len": n, "fbe": 0xF,
                   "lbe": 0xF if n > 1 else 0, "tag": tag})


def test_requests_gated_by_bus_master_and_d_state():
    tl, app, wire = make_tl()
    tl.cfg.bm_en = False
    rq_read(app, 0)
    run(tl, app, wire, 10)
    assert not wire.sent
    tl.cfg.bm_en, tl.cfg.d_state = True, m.D3HOT
    run(tl, app, wire, 10, 10)
    assert not wire.sent
    tl.cfg.d_state = m.D0
    run(tl, app, wire, 10, 20)
    (h,) = (m.parse(t) for t in wire.sent)
    assert (h["rid"], h["tag"], h["length"], h["hdr_bytes"]) == (tl.cfg.id, 0, 32, 12)


def test_completion_tracking():
    tl, app, wire = make_tl()
    rq_read(app, 2, 0x4020)
    now = run(tl, app, wire, 5)
    me = tl.cfg.id
    # two completions split at the host's 64-byte boundary
    tl.receive(m.cpl_tlp(0, m.SC, 128, me, 2, 0x20, bytes(32)), now)
    tl.receive(m.cpl_tlp(0, m.SC, 96, me, 7, 0x40, bytes(96)), now)      # unexpected tag
    tl.receive(m.cpl_tlp(0, m.SC, 64, me, 2, 0x40, bytes(96)), now)      # bad byte count
    tl.receive(m.cpl_tlp(0, m.SC, 96, me, 2, 0x40, bytes(96)), now)
    run(tl, app, wire, 200, now)
    assert [(p["status"], len(p["data"]), p["done"]) for p in app.rc_got] == \
        [(m.RC_OK, 32, False), (m.RC_OK, 96, True)]
    assert tl.errors[m.E_UNEXPECTED] == 1 and tl.errors[m.E_MALFORMED] == 1
    assert not tl.track.reads


@pytest.mark.parametrize("status,rc,bit", [(m.UR, m.RC_UR, m.E_RX_UR), (m.CA, m.RC_CA, m.E_RX_CA),
                                           (7, m.RC_UR, m.E_RX_UR)])
def test_error_status_ends_the_read(status, rc, bit):
    tl, app, wire = make_tl()
    rq_read(app, 1)
    now = run(tl, app, wire, 5)
    tl.receive(m.cpl_tlp(0, status, 128, tl.cfg.id, 1, 0), now)
    run(tl, app, wire, 10, now)
    assert [(p["status"], p["done"]) for p in app.rc_got] == [(rc, True)]
    assert tl.errors[bit] == 1 and not tl.track.reads


def test_np_pending_until_the_final_completion():
    tl, app, wire = make_tl()
    app.rc_ready = lambda now: False           # the application holds rc
    assert not tl.np_pending
    rq_read(app, 1, n=1)
    now = run(tl, app, wire, 5)
    assert tl.np_pending
    tl.receive(m.cpl_tlp(0, m.SC, 4, tl.cfg.id, 1, 0, bytes(4)), now)
    assert not tl.np_pending and not app.rc_got


def test_poisoned_and_crs_completions():
    tl, app, wire = make_tl()
    rq_read(app, 1)
    now = run(tl, app, wire, 5)
    me = tl.cfg.id
    tl.receive(m.cpl_tlp(0, m.CRS, 128, me, 1, 0), now)
    tl.receive(m.cpl_tlp(0, m.SC, 128, me, 1, 0, bytes(64), ep=1), now)
    tl.receive(m.cpl_tlp(0, m.SC, 64, me, 1, 0x40, bytes(64)), now)
    run(tl, app, wire, 50, now)
    assert [(p["status"], p["done"]) for p in app.rc_got] == [(m.RC_FAILED, True)]
    assert tl.errors[m.E_MALFORMED] == 1 and tl.errors[m.E_POISONED_CPL] == 1


@pytest.mark.parametrize("div", [1, 64, 1024])
def test_timeout_range(div):
    tr = m.Tracker(div)
    lo, hi = tr.timeout_bounds()
    assert lo == m.CPL_TIMEOUT // div and hi == lo + lo // 16
    if div == 1:
        # 16.8 to 17.8 ms at 125 MHz: inside the 50 us to 50 ms default
        # range and not below the recommended 10 ms
        assert 10e-3 <= lo * 8e-9 and hi * 8e-9 <= 50e-3


def test_timeout_reported_once_and_late_completion_unexpected():
    tl, app, wire = make_tl(cpl_timeout_div=1024)
    rq_read(app, 4)
    now = run(tl, app, wire, 2300)
    assert [(p["tag"], p["status"], p["done"]) for p in app.rc_got] == [(4, m.RC_FAILED, True)]
    lo, hi = tl.track.timeout_bounds()
    assert lo <= tl.counts["timeout_cycles"][0] <= hi
    tl.receive(m.cpl_tlp(0, m.SC, 128, tl.cfg.id, 4, 0, bytes(128)), now)
    assert tl.errors[m.E_TIMEOUT] == 1 and tl.errors[m.E_UNEXPECTED] == 1


def test_disabled_timeout_never_fires():
    tl, app, wire = make_tl(cpl_timeout_div=1024)
    tl.cfg.cpl_timeout_dis = True
    rq_read(app, 4)
    now = run(tl, app, wire, 20000)
    assert not app.rc_got and tl.errors[m.E_TIMEOUT] == 0 and tl.np_pending
    tl.cfg.cpl_timeout_dis = False              # a full timeout from the clear
    lo, hi = tl.track.timeout_bounds()
    run(tl, app, wire, lo, now)                 # cycles now .. now + lo - 1
    assert tl.errors[m.E_TIMEOUT] == 0 and tl.np_pending
    run(tl, app, wire, hi - lo, now + lo)       # cycles now + lo .. now + hi - 1
    assert tl.errors[m.E_TIMEOUT] == 1 and not tl.np_pending
    run(tl, app, wire, 5, now + hi)
    assert [p["status"] for p in app.rc_got] == [m.RC_FAILED]


def test_rc_completion_waits_for_earlier_posted_writes():
    tl, app, wire = make_tl()
    rq_read(app, 0, n=1)
    now = run(tl, app, wire, 5)
    app.stall = ((now, now + 50),)
    tl.receive(mwr(0x8000_0000), now)
    tl.receive(m.cpl_tlp(0, m.SC, 4, tl.cfg.id, 0, 0, bytes(4)), now)
    run(tl, app, wire, 40, now)
    assert not app.rc_got
    run(tl, app, wire, 30, now + 40)
    assert len(app.rc_got) == 1 and app.cq_got[0]["write"]


def test_cc_completion_waits_for_earlier_rq_write():
    tl, app, wire = make_tl()
    wire.credits = {(m.P, 0): 0}                 # the host grants no posted credit yet
    app.rq.append({"write": True, "addr": 0x4000, "len": 1, "fbe": 0xF, "lbe": 0, "data": [1]})
    run(tl, app, wire, 5)
    tl.receive(mrd(0x8000_0000), 5)
    run(tl, app, wire, 60, 5)
    assert not wire.sent                         # the completion stays behind the write
    wire.credits = {}
    run(tl, app, wire, 20, 65)
    assert [m.parse(t)["type"] for t in wire.sent] == [m.T_MEM, m.T_CPL]


def test_completion_passes_a_credit_blocked_read():
    tl, app, wire = make_tl()
    wire.credits = {(m.NP, 0): 0}
    rq_read(app, 0)
    run(tl, app, wire, 5)
    tl.receive(mrd(0x8000_0000), 5)
    run(tl, app, wire, 60, 5)
    assert [m.parse(t)["type"] for t in wire.sent] == [m.T_CPL]


def test_posted_write_passes_a_credit_blocked_read():
    """A posted request may pass a non-posted one waiting for credits;
    reads keep their order among themselves."""
    tl, app, wire = make_tl()
    wire.credits = {(m.NP, 0): 0}
    rq_read(app, 0)
    app.rq.append({"write": True, "addr": 0x5000, "len": 1, "fbe": 0xF, "lbe": 0, "data": [7]})
    rq_read(app, 1)
    run(tl, app, wire, 40)
    assert [(m.parse(t)["fmt"] & 2, m.parse(t)["addr"]) for t in wire.sent] == [(2, 0x5000)]
    wire.credits = {}
    run(tl, app, wire, 40, 40)
    assert [m.parse(t)["tag"] for t in wire.sent[1:]] == [0, 1]


def test_read_never_passes_an_earlier_write():
    tl, app, wire = make_tl()
    wire.credits = {(m.P, 0): 0}
    app.rq.append({"write": True, "addr": 0x5000, "len": 1, "fbe": 0xF, "lbe": 0, "data": [7]})
    rq_read(app, 0)
    run(tl, app, wire, 30)
    assert not wire.sent
    wire.credits = {}
    run(tl, app, wire, 30, 30)
    assert [m.parse(t)["fmt"] & 2 for t in wire.sent] == [2, 0]


def test_interrupt_write_follows_earlier_rq_writes():
    tl, app, wire = make_tl()
    app.rq.append({"write": True, "addr": 0x5000, "len": 1, "fbe": 0xF, "lbe": 0, "data": [7]})
    run(tl, app, wire, 3)
    assert tl.message(0, 0xFEE0_0000, 1)
    run(tl, app, wire, 30, 3)
    assert [m.parse(t)["addr"] for t in wire.sent] == [0x5000, 0xFEE0_0000]


def test_interrupt_and_error_messages():
    tl, app, wire = make_tl()
    assert tl.message(0, 0xFEE0_0000, 0x41)
    assert tl.message(0, 0x1_FEE0_0000, 0x42)
    assert tl.message(2)
    run(tl, app, wire, 30)
    a, b, c = (m.parse(t) for t in wire.sent)
    assert (a["hdr_bytes"], a["addr"], a["fbe"], a["lbe"], a["data"]) == \
        (12, 0xFEE0_0000, 0xF, 0, (0x41).to_bytes(4, "little"))
    assert b["hdr_bytes"] == 16
    assert (c["type"], c["code"], c["data"]) == (m.T_MSG | m.R_TO_RC, 0x31, b"")


def test_host_beyond_the_advertised_credits_is_caught():
    tl, app, wire = make_tl()
    for i in range(8):
        tl.receive(mrd(0x8000_0000, tag=i), 0)
    with pytest.raises(AssertionError):
        tl.receive(mrd(0x8000_0000, tag=8), 0)


def test_reset_drops_everything():
    tl, app, wire = make_tl()
    app.latency = 10**9
    rq_read(app, 0)
    tl.receive(mrd(0x8000_0000), 0)
    run(tl, app, wire, 10)
    tl.reset()
    assert not tl.track.reads and not tl.handles and not tl.reqp and not tl.reqnp and tl.occ == [0, 0, 0, 0]


def test_queue_never_exceeds_advertised_credits():
    met = m.perf({"kind": "completer", "tlps": 200, "cq_duty": 0.3,
                  "cq_stall": [[2000, 6000]]}, 2)
    assert met["posted_header_credits_held_max"] <= 8
    assert met["posted_data_credits_held_max"] <= 32
    assert met["requests_done"] == 200


# ------------------------------------------------------------------ perf

def test_completer_write_throughput():
    met = m.perf({"kind": "completer", "tlps": 400}, 1)
    assert met["fraction_of_link"] >= 0.98 and met["tl_errors"] == 0


def test_completer_read_throughput():
    met = m.perf({"kind": "completer", "tlps": 300, "op": "read"}, 1)
    assert met["fraction_of_link"] >= 0.98 and met["completion_errors"] == 0


def test_bad_workload():
    with pytest.raises(ValueError):
        m.perf({"kind": "nope"}, 1)
    with pytest.raises(ValueError):
        m.perf({"kind": "completer", "tlps": 0}, 1)
