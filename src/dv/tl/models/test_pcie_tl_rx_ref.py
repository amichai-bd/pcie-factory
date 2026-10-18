"""Unit tests of the independent receive reference (pcie_tl_rx_ref.py):
the examples and rules of the tl ROUTING and TLP pages, no simulator."""

import pcie_tl_rx_ref as ref

R = ref.Regs()                      # BAR0 0x8000_0000 (4 KiB), BAR2 0x9000_0000
BAR0, BAR2 = R.bar0_base, R.bar2_base


def j(tlp, regs=R):
    v = ref.judge(tlp, regs)
    return v.outcome, v.err


def test_beats_byte_order():
    tlp = ref.memory(False, BAR0, 1, 0xF, 0)
    beats = ref.to_beats(tlp)
    assert beats[0] & 0xFF == 0x00 and (beats[0] >> 24) == 1       # Fmt/Type, Length
    assert beats[2] & 0xFF == 0x80                                   # address bits 31:24
    assert ref.from_beats(beats) == tlp


def test_served():
    assert j(ref.memory(True, BAR0 + 0x10, 4)) == ("cq", None)
    assert j(ref.memory(False, BAR0 + 0xFFC, 1, 0xF, 0)) == ("cq", None)
    assert j(ref.memory(False, BAR0, 4, four_dw=True)) == ("cq", None)
    assert j(ref.memory(True, BAR2 + 8, 2)) == ("bar2", None)
    assert j(ref.config(False, 0x10)) == ("cfg", None)
    assert j(ref.config(True, 0x3FF, data=1)) == ("cfg", None)


def test_unsupported():
    assert j(ref.memory(True, 0x1000, 1, 0xF, 0)) == ("ur", ref.UR_POSTED)
    assert j(ref.memory(False, 0x1000, 1, 0xF, 0)) == ("ur", ref.UR_NONPOSTED)
    assert j(ref.memory(False, (1 << 32) | BAR0, 1, 0xF, 0)) == ("ur", ref.UR_NONPOSTED)
    assert j(ref.memory(False, BAR0, 1, 0xF, 0, at=1)) == ("ur", ref.UR_NONPOSTED)
    assert j(ref.memory(False, BAR0, 1, 0xF, 0, locked=True)) == ("ur", ref.UR_NONPOSTED)
    assert j(ref.config(False, 0, fn=1)) == ("ur", ref.UR_NONPOSTED)
    assert j(ref.config(False, 0, type1=True)) == ("ur", ref.UR_NONPOSTED)
    assert j(ref.io(True, 0x100)) == ("ur", ref.UR_NONPOSTED)
    assert j(ref.atomic(ref.TYPE_CAS, BAR0, 2)) == ("ur", ref.UR_NONPOSTED)
    off = ref.Regs(mem_space_en=False)
    assert j(ref.memory(True, BAR0, 1, 0xF, 0), off) == ("ur", ref.UR_POSTED)
    d3 = ref.Regs(d_state=3)
    assert j(ref.memory(False, BAR2, 1, 0xF, 0), d3) == ("ur", ref.UR_NONPOSTED)
    assert j(ref.config(False, 0), d3) == ("cfg", None)


def test_messages():
    for code in ref.DROPPED_MSGS:
        assert j(ref.message(code)) == ("drop", None), hex(code)
    assert j(ref.message(ref.PME_TURN_OFF, 0b011)) == ("pme", None)
    for code in (ref.VENDOR_TYPE0, 0x20, 0x18, ref.PME_TO_ACK, 0x30, 0x10, 0x12, 0x99):
        assert j(ref.message(code)) == ("ur", ref.UR_POSTED), hex(code)
    assert j(ref.message(ref.VENDOR_TYPE1, data=[1, 2])) == ("drop", None)


def test_malformed_kinds():
    assert j(ref.raw(0b100, 0))[0] == "malformed"                       # prefix
    assert j(ref.raw(0b011, ref.TYPE_MEM_LOCKED))[0] == "malformed"
    assert j(ref.raw(0b001, ref.TYPE_CFG0))[0] == "malformed"
    assert j(ref.raw(0b000, ref.TYPE_MSG))[0] == "malformed"
    assert j(ref.raw(0b000, ref.TYPE_SWAP))[0] == "malformed"
    assert j(ref.raw(0b001, ref.TYPE_CPL))[0] == "malformed"
    assert j(ref.raw(0b000, 0b00011))[0] == "malformed"                 # undefined Type
    assert ref.judge(ref.raw(0b001, ref.TYPE_CPL), R).cls == "CPL"


def test_byte_enables():
    ok = ref.byte_enables_ok
    assert ok(1, 0, 0, 0) and not ok(1, 0xF, 1, 0)
    assert ok(2, 0b0101, 0b1010, 0x0)            # one aligned QW: any pattern
    assert not ok(2, 0b0101, 0b1010, 0x4)
    assert not ok(3, 0b0111, 0xF, 0) and ok(3, 0b1100, 0b0011, 0)
    assert not ok(2, 0, 0xF, 0)
    assert j(ref.memory(False, BAR0, 1, 0, 0)) == ("cq", None)          # zero-length read
    assert j(ref.memory(True, BAR0, 4, 0b1011, 0xF))[0] == "malformed"


def test_4k_and_config_rules():
    assert j(ref.memory(False, BAR0 + 0xFFC, 2))[0] == "malformed"
    assert j(ref.memory(False, BAR0, 1024))[0] == "cq"                  # 4 KiB read
    assert j(ref.config(False, 0, length=2, lbe=0xF))[0] == "malformed"
    assert j(ref.config(False, 0, lbe=1))[0] == "malformed"
    assert j(ref.config(False, 0, tc=1))[0] == "malformed"
    assert j(ref.config(False, 0, attr=2))[0] == "malformed"
    assert j(ref.io(False, 0, tc=2))[0] == "malformed"
    assert j(ref.message(0x20, tc=1))[0] == "malformed"
    assert j(ref.message(ref.VENDOR_TYPE0, tc=1)) == ("ur", ref.UR_POSTED)


def test_poisoned():
    assert j(ref.memory(True, BAR0, 1, 0xF, 0, ep=1)) == ("drop", ref.POISONED_REQUEST)
    assert j(ref.config(True, 4, ep=1)) == ("ur", ref.POISONED_REQUEST)
    assert j(ref.memory(True, 0x1000, 1, 0xF, 0, ep=1)) == ("ur", ref.UR_POSTED)   # UR wins
    assert j(ref.message(ref.PME_TURN_OFF, ep=1)) == ("drop", ref.POISONED_REQUEST)
    assert j(ref.message(ref.UNLOCK, ep=1)) == ("drop", ref.POISONED_REQUEST)


def test_credits():
    assert ref.credit_class(ref.memory(True, BAR0, 5)) == ("P", 2)
    assert ref.credit_class(ref.memory(False, BAR0, 32)) == ("NP", 0)
    assert ref.credit_class(ref.config(True, 0)) == ("NP", 1)
    assert ref.credit_class(ref.message(0x7F, data=[0] * 9)) == ("P", 3)
    assert ref.credit_class(ref.completion(data=[1])) == ("CPL", 1)
    assert ref.credit_class(ref.raw(0b001, ref.TYPE_MSG))[0] == "P"


def test_byte_count():
    assert ref.total_bytes(1, 0b1001, 0) == 4
    assert ref.total_bytes(1, 0, 0) == 1
    assert ref.total_bytes(2, 0b1110, 0b0011) == 5
    assert ref.total_bytes(32, 0xF, 0xF) == 128


def test_accesses():
    h = ref.header(ref.memory(True, BAR2 + 0x7F8, 3, 0b1100, 0b0001, data=[1, 2, 3]))
    acc = ref.bar2_accesses(h, True)
    assert acc == [(True, True, 0x1FE, 0b1100, 1), (True, True, 0x1FF, 0xF, 2),
                   (True, True, 0x200, 0b0001, 3)]
    h = ref.header(ref.config(True, 0x123, 0b0011, bus=7, dev=3, data=0xAB))
    assert ref.cfg_access(h, True) == (False, True, 0x123, 0b0011, 0xAB, 7, 3)


def test_digest():
    t = ref.with_digest(ref.memory(True, BAR0, 2, data=[5, 6]))
    h = ref.header(t)
    assert h["td"] == 1 and h["data"] == [5, 6] and len(ref.to_beats(t)) == 6
    assert j(t) == ("cq", None) and ref.credit_class(t) == ("P", 1)
    assert j(ref.with_digest(ref.config(False, 3))) == ("cfg", None)
