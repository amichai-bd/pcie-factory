"""Self-tests of the pcie_dll architecture model; no simulator needed.

Run: python3 -m pytest src/arch/dll (./build ci unit runs them as arch:<ip>).

The CRC known answers are not taken from any document: they come from a
second, independent computation in this file (polynomial long division
over GF(2) on Python integers), from the standard library's CRC-32 for the
LCRC, and from the published CRC-32 check value of the ASCII digits 1 to 9.
"""

import random
import zlib

import pytest

import pcie_dll_arch as m
import pcie_dll_crc as crc


# ------------------------------------------------------------------ CRCs

def longdiv(data: bytes, poly: int, width: int) -> bytes:
    """CRC by long division: M(x) x^w + S(x) x^n modulo the polynomial,
    where M is the n message bits in line order (bit 0 of each byte first,
    the first bit the highest power), w the width and S the all-ones seed;
    then the line mapping."""
    bits = [(b >> i) & 1 for b in data for i in range(8)]
    msg = 0
    for x in bits:
        msg = (msg << 1) | x
    msg = (msg << width) ^ (((1 << width) - 1) << len(bits))
    g = (1 << width) | poly
    for sh in range(msg.bit_length() - 1 - width, -1, -1):
        if (msg >> (sh + width)) & 1:
            msg ^= g << sh
    inv = ~msg & ((1 << width) - 1)
    return bytes(int(f"{(inv >> (width - 8 - 8 * k)) & 0xFF:08b}"[::-1], 2)
                 for k in range(width // 8))


# Recorded from longdiv above (an independent computation), not from a spec.
CRC16_KAT = [
    (bytes(4), "b362"),
    (bytes([0x00, 0x00, 0x0F, 0xFF]), "25a8"),    # Ack, sequence 4095
    (bytes([0x10, 0x00, 0x0A, 0x5C]), "7546"),    # Nak, sequence 0xA5C
    (bytes([0x40, 0x02, 0x00, 0x20]), "f534"),    # InitFC1-P, 8 headers, 32 data
    (bytes([0x80, 0x02, 0x00, 0x40]), "3428"),    # UpdateFC-P
    (bytes([0xC0, 0x00, 0x00, 0x00]), "7422"),    # InitFC2-P, infinite
]


@pytest.mark.parametrize("body,want", CRC16_KAT)
def test_crc16_known_answers(body, want):
    assert crc.crc16(body).hex() == want
    assert longdiv(body, crc.CRC16_POLY, 16).hex() == want


def test_crc16_matches_long_division_on_random_bodies():
    rng = random.Random(7)
    for _ in range(300):
        body = bytes(rng.randrange(256) for _ in range(4))
        assert crc.crc16(body) == longdiv(body, crc.CRC16_POLY, 16)


def test_lcrc_known_answers():
    # The CRC-32 check value of b"123456789" is 0xCBF43926, sent low byte first.
    assert crc.lcrc(b"123456789").hex() == "2639f4cb"
    # Sequence 0 and a 3-DW memory read, from longdiv.
    frame = bytes([0, 0]) + bytes([0, 0, 0, 1, 0, 1, 0, 0x0F, 0, 0, 0, 0])
    assert crc.lcrc(frame).hex() == "e1f1e7e2"
    assert longdiv(frame, crc.LCRC_POLY, 32).hex() == "e1f1e7e2"


def test_lcrc_matches_zlib_and_long_division():
    rng = random.Random(9)
    for n in (2, 6, 18, 150, 1000):
        data = bytes(rng.randrange(256) for _ in range(n))
        want = zlib.crc32(data).to_bytes(4, "little")
        assert crc.lcrc(data) == want == longdiv(data, crc.LCRC_POLY, 32)


def test_single_bit_errors_are_detected():
    frame = m.tlp_frame(5, m.make_tlp("mwr", 128, 3))
    dllp = m.ack_dllp(m.ACK, 77)
    for i in range(len(frame) * 8):
        bad = bytearray(frame)
        bad[i // 8] ^= 1 << (i % 8)
        assert crc.lcrc(bytes(bad[:-4])) != bytes(bad[-4:])
    for i in range(48):
        bad = bytearray(dllp)
        bad[i // 8] ^= 1 << (i % 8)
        assert crc.crc16(bytes(bad[:4])) != bytes(bad[4:])


# ------------------------------------------------------------- formats

def test_dllp_fields():
    d = m.fc_dllp(m.UPDATEFC, m.NP, 0xA5, 0x9C3)
    assert d[:4] == bytes([0x90, 0x29, 0x49, 0xC3])
    assert m.decode_dllp(d) == ("update", m.NP, 0, 0xA5, 0x9C3)
    assert m.decode_dllp(m.fc_dllp(m.INITFC1, m.CPL, 1, 2)) == ("init1", m.CPL, 0, 1, 2)
    assert m.decode_dllp(m.fc_dllp(m.INITFC2, m.P, 0, 0)) == ("init2", m.P, 0, 0, 0)
    assert m.decode_dllp(m.ack_dllp(m.NAK, 0xABC))[0::3] == ("nak", 0xABC)
    assert m.ack_dllp(m.ACK, 0xABC)[:4] == bytes([0, 0, 0x0A, 0xBC])
    assert m.decode_dllp(bytes([0x20, 0, 0, 0, 0, 0]))[0] == "other"     # PM, ignored
    assert m.decode_dllp(m.fc_dllp(m.UPDATEFC, m.P, 1, 1, vc=1))[2] == 1


def test_tlp_classes_and_credits():
    assert m.tlp_info(m.make_tlp("mwr", 128, 0)) == (m.P, 1, 8, 140)
    assert m.tlp_info(m.make_tlp("mwr", 4, 0)) == (m.P, 1, 1, 16)
    assert m.tlp_info(m.make_tlp("mrd", 0, 0)) == (m.NP, 1, 0, 12)
    assert m.tlp_info(m.make_tlp("cpld", 36, 0)) == (m.CPL, 1, 3, 48)
    assert m.tlp_info(bytes([0x30, 0, 0, 0]))[0] == m.P            # message, 4-DW header
    assert m.tlp_info(bytes([0x44, 0, 0, 1]))[:3] == (m.NP, 1, 1)  # configuration write
    assert m.tlp_info(bytes([0x0A, 0, 0, 0]))[0] == m.CPL          # completion, no data
    assert m.tlp_info(bytes([0x60, 0, 0, 0]))[2:] == (256, 16 + 4096)  # length 0 is 1024 DW


def test_tlp_frame_layout():
    t = m.make_tlp("mrd", 0, 1)
    f = m.tlp_frame(0x123, t)
    assert f[:2] == bytes([0x01, 0x23]) and f[2:-4] == t and len(f) == len(t) + 6


# ------------------------------------------------------------- helpers

def up_pair(fc=None, errors=None, seed=1, ack_latency=m.ACK_LATENCY):
    a = m.Dll("a", fc)
    b = m.Dll("b", fc, ack_latency=ack_latency)
    p = m.Pair(a, b, m.Channel(random.Random(seed), errors=errors))
    for _ in range(400):
        p.step()
    assert a.state == b.state == m.ACTIVE
    return p, a, b


def run(p, cycles, **kw):
    for _ in range(cycles):
        p.step(**kw)


def tlp_pkt(seq, tlp, nullify=False, bad=False):
    f = m.tlp_frame(seq, tlp)
    if nullify:
        f = f[:-4] + crc.invert(f[-4:])
    if bad:
        f = f[:-1] + bytes([f[-1] ^ 1])
    return m.Packet(False, f, nullify=nullify)


# ------------------------------------------------- data link control

def test_fc_init_records_partner_credits_and_reaches_active():
    p, a, b = up_pair(fc={"ph": 5, "pd": 40, "nph": 0, "npd": 0})
    assert a.cl[m.P] == [5, 40] and a.inf[m.NP] == [True, True]
    assert a.avail(m.NP, 0) == 255 and a.avail(m.NP, 1) == 4095
    assert a.avail(m.P, 1) == 40 and a.dl_up and b.dl_up


def test_init_waits_for_link_and_link_down_resets():
    a, b = m.Dll("a"), m.Dll("b")
    p = m.Pair(a, b, m.Channel(random.Random(1)))
    run(p, 300, link=(True, False))
    assert a.state == m.INIT1 and b.state == m.INACTIVE and a.avail(m.P, 0) == 0
    run(p, 300)
    assert a.state == b.state == m.ACTIVE
    assert a.offer(m.make_tlp("mwr", 64, 1), p.now)
    run(p, 10, link=(False, True))
    assert a.state == m.INACTIVE and not a.rbuf and a.nts == 0 and a.ackd == 4095
    assert a.cc == [[0, 0]] * 3 and not a.dl_up


def test_initfc2_counts_in_fc_init1():
    d = m.Dll("d")
    d.set_link_up(True, 0)
    for c in range(3):
        d.receive(m.Packet(True, m.fc_dllp(m.INITFC2, c, 4, 16)), 0)
    assert all(d.fi1) and d.cl[m.CPL] == [4, 16]


def test_update_fc_before_init_and_on_other_vcs_is_ignored():
    d = m.Dll("d")
    d.set_link_up(True, 0)
    d.receive(m.Packet(True, m.fc_dllp(m.UPDATEFC, m.P, 9, 9)), 0)
    d.receive(m.Packet(True, m.fc_dllp(m.INITFC1, m.P, 9, 9, vc=1)), 0)
    assert d.cl[m.P] == [0, 0] and not d.fi1[m.P] and not d.fi2


# ------------------------------------------------------------ receiver

def test_receiver_sequence_rules():
    p, a, b = up_pair()
    t = m.make_tlp("mwr", 16, 1)
    b.receive(tlp_pkt(0, t), p.now)                       # in sequence
    assert b.nrs == 1 and len(b.rxq) == 1
    b.receive(tlp_pkt(0, t), p.now)                       # duplicate: Ack
    assert b.ack_due and b.counts["duplicates"] == 1 and b.nrs == 1
    b.receive(tlp_pkt(3, t), p.now)                       # ahead: Nak, once
    b.receive(tlp_pkt(4, t), p.now)
    assert b.nak_due and b.errors[m.E_BAD_TLP] == 2
    run(p, 6)
    assert b.counts["naks"] == 1 and b.nak_scheduled
    b.receive(tlp_pkt(5, t), p.now)
    run(p, 6)
    assert b.counts["naks"] == 1                          # still scheduled: no second Nak
    b.receive(tlp_pkt(1, t), p.now)                       # the expected one clears it
    assert not b.nak_scheduled and b.nrs == 2


@pytest.mark.parametrize("nrs", [0, 100, 4095])
def test_receiver_ahead_and_duplicate_boundary(nrs):
    p, a, b = up_pair()
    b.nrs = nrs
    t = m.make_tlp("mrd", 0, 1)
    b.receive(tlp_pkt((nrs + 2047) % 4096, t), p.now)    # furthest ahead: lost TLPs
    assert b.nak_due and b.errors[m.E_BAD_TLP] == 1 and not b.ack_due
    b.receive(tlp_pkt((nrs + 2048) % 4096, t), p.now)    # nearest duplicate
    assert b.ack_due and b.counts["duplicates"] == 1 and b.errors[m.E_BAD_TLP] == 1
    b.receive(tlp_pkt((nrs + 4095) % 4096, t), p.now)    # the last one received
    assert b.counts["duplicates"] == 2 and b.nrs == nrs


def test_receiver_lcrc_and_nullify_rules():
    p, a, b = up_pair()
    t = m.make_tlp("mrd", 0, 2)
    b.receive(tlp_pkt(0, t, nullify=True), p.now)        # inverted LCRC: silent
    assert b.errors[m.E_BAD_TLP] == 0 and not b.nak_due and b.nrs == 0
    b.receive(m.Packet(False, m.tlp_frame(0, t), nullify=True), p.now)   # EDB, good LCRC
    assert b.errors[m.E_BAD_TLP] == 1 and b.nak_due
    b.nak_scheduled = b.nak_due = False
    b.receive(tlp_pkt(0, t, bad=True), p.now)
    assert b.errors[m.E_BAD_TLP] == 2 and b.nak_due
    b.nak_scheduled = b.nak_due = False
    b.receive(m.Packet(False, m.tlp_frame(0, t), err=True), p.now)
    assert b.errors[m.E_BAD_TLP] == 3 and b.nak_due and b.nrs == 0


def test_bad_dllps_are_dropped():
    p, a, b = up_pair()
    good = m.ack_dllp(m.ACK, 4095)
    a.receive(m.Packet(True, good[:-1] + bytes([good[-1] ^ 0x80])), p.now)
    a.receive(m.Packet(True, good, err=True), p.now)
    assert a.errors[m.E_BAD_DLLP] == 2


def test_ack_sent_within_latency():
    p, a, b = up_pair()
    assert a.offer(m.make_tlp("mwr", 128, 1), p.now)
    run(p, 400)
    assert len(b.delivered) == 1 and b.counts["acks"] >= 1
    assert max(b.ack_lat) <= m.ACK_LATENCY + 1
    assert not a.rbuf and not a.rt_running


# ---------------------------------------------------------- transmitter

def test_ack_out_of_range_is_a_protocol_error():
    p, a, b = up_pair()
    a.receive(m.Packet(True, m.ack_dllp(m.ACK, 5)), p.now)
    assert a.errors[m.E_PROTOCOL] == 1


def test_nak_purges_then_replays_and_counts():
    p, a, b = up_pair(errors={"tlp_drop": 0.0})
    for i in range(3):
        while not a.offer(m.make_tlp("mwr", 32, i), p.now):
            p.step()
    run(p, 20)
    while a.send_idx < 3:
        p.step()
    a.receive(m.Packet(True, m.ack_dllp(m.NAK, 0)), p.now)    # seq 0 delivered, 1 and 2 not
    assert len(a.rbuf) == 2 and a.replay_active and a.replay_num == 1
    assert a.send_idx == 0 and a.replay_end == 2 and a.ackd == 0


def test_replay_timeout_rollover_requests_retrain_and_progress_resets():
    p, a, b = up_pair(errors={"drop_tlp_window": (0, 10**9)})
    assert a.offer(m.make_tlp("mwr", 64, 1), p.now)
    rollover_at = None
    for _ in range(4 * (m.REPLAY_TIMEOUT + 200)):
        p.step()
        if a.errors[m.E_ROLLOVER] and rollover_at is None:
            rollover_at = p.now
    assert a.errors[m.E_TIMEOUT] == 4 and a.counts["retrains"] == 1
    assert a.replay_num == 0 and rollover_at is not None
    p.ch.drop_tlp_window = (0, 0)
    run(p, 3000)
    assert len(b.delivered) == 1 and not a.rbuf and a.replay_num == 0


def test_replay_timer_holds_in_recovery():
    p, a, b = up_pair(errors={"drop_tlp_window": (0, 10**9)})
    assert a.offer(m.make_tlp("mwr", 4, 1), p.now)
    run(p, 40)
    assert a.rt_running
    count = a.rt_count
    p.ch.retrain(p.now)
    run(p, 400)
    assert a.rt_count == count and a.errors[m.E_TIMEOUT] == 0


def test_credit_gating_and_update_fc():
    # b advertises 2 posted headers; its sink stays idle, so no UpdateFC.
    p, a, b = up_pair(fc={"ph": 2})
    sent = 0
    for i in range(5):
        if a.offer(m.make_tlp("mwr", 8, i), p.now):
            sent += 1
        run(p, 10, sinks=(True, False))
    assert sent == 2 and a.avail(m.P, 0) == 0
    run(p, 200, sinks=(True, True))
    assert a.avail(m.P, 0) == 2 and b.counts["updatefc"] >= 1


def test_receiver_overflow_is_detected_and_not_acknowledged():
    p, a, b = up_pair(fc={"ph": 1})
    t = m.make_tlp("mwr", 8, 1)
    b.receive(tlp_pkt(0, t), p.now)
    b.receive(tlp_pkt(1, t), p.now)               # partner ignored the credit limit
    assert b.errors[m.E_OVERFLOW] == 1 and b.nrs == 1
    assert not b.nak_due and not b.ack_due and not b.nak_scheduled   # neither Ack nor Nak
    run(p, 100, sinks=(True, False))
    assert b.counts["naks"] == 0 and b.nrs == 1   # the Ack sent covers sequence 0 only
    assert m.decode_dllp(m.ack_dllp(m.ACK, b.nrs - 1))[3] == 0


def test_malformed_tlp_is_acknowledged_not_delivered_and_credits_return():
    p, a, b = up_pair()
    t = m.make_tlp("mwr", 32, 1)
    short = t[:-4]                                # header says 8 DW of data, 7 sent
    b.receive(tlp_pkt(0, short), p.now)
    assert b.errors[m.E_MALFORMED] == 1 and b.nrs == 1 and not b.rxq and not b.nak_due
    assert b.ca[m.P] == [9, 34] and b.cr[m.P] == [1, 2]
    big = bytes([0x40, 0, 0, 40]) + bytes(8 + 160)    # 160-byte payload, header agrees
    b.receive(tlp_pkt(1, big), p.now)
    assert b.errors[m.E_MALFORMED] == 2 and b.nrs == 2 and not b.rxq
    over = bytes([0x40, 0, 0, 33]) + bytes(8 + 132)   # 132-byte payload: above 128, 144 bytes
    assert len(over) == 144
    b.receive(tlp_pkt(2, over), p.now)
    assert b.errors[m.E_MALFORMED] == 3 and b.nrs == 3 and not b.rxq
    odd = m.tlp_frame(3, m.make_tlp("mrd", 0, 5) + b"\x00")  # good LCRC, 19 bytes
    b.receive(m.Packet(False, odd), p.now)
    assert b.errors[m.E_MALFORMED] == 4 and b.nrs == 4 and not b.nak_due
    assert b.errors[m.E_BAD_TLP] == 0
    b.receive(m.Packet(False, bytes([0, 4, 1])), p.now)    # too short to hold an LCRC
    assert b.errors[m.E_BAD_TLP] == 1 and b.nak_due


def test_default_completion_credits_are_infinite():
    d = m.Dll("d")
    assert d.adv[m.CPL] == (0, 0) and d.adv[m.P] == (8, 32)


def test_sink_must_not_stall_when_a_class_is_infinite():
    with pytest.raises(ValueError):
        m.perf({"kind": "traffic", "tlps": 10, "sink_duty": 0.5}, 1)


def test_infinite_credits_with_skew_send_entry_update_fc():
    inf = {"ph": 0, "pd": 0, "nph": 0, "npd": 0, "cplh": 0, "cpld": 0}
    a, b = m.Dll("a", inf), m.Dll("b", inf)
    p = m.Pair(a, b, m.Channel(random.Random(1)))
    for _ in range(1000):
        p.step(link=(True, p.now >= 300))
    assert a.state == b.state == m.ACTIVE
    assert a.counts["updatefc"] >= 3 and b.counts["updatefc"] >= 3


def test_nak_or_timeout_during_replay_gives_exactly_one_more_replay():
    p, a, b = up_pair(errors={"drop_tlp_window": (0, 10**9)})
    for i in range(3):
        while not a.offer(m.make_tlp("mwr", 128, i), p.now):
            p.step()
    while a.send_idx < 3:
        p.step()
    a.receive(m.Packet(True, m.ack_dllp(m.NAK, 4095)), p.now)
    assert a.replay_active and a.counts["replays"] == 1 and a.replay_num == 1
    a.receive(m.Packet(True, m.ack_dllp(m.NAK, 4095)), p.now)    # during the replay
    a._start_replay()                                            # as a timeout would
    assert a.replay_pending and a.counts["replays"] == 1
    while a.counts["replays"] == 1:
        p.step()
    assert a.replay_active and a.counts["replays"] == 2 and not a.replay_pending
    while a.replay_active:
        p.step()
    assert a.counts["replays"] == 2 and a.replay_num == 2


def test_rollover_holds_tlps_until_the_ltssm_returns_to_l0():
    p, a, b = up_pair(errors={"drop_tlp_window": (0, 10**9)})
    p.ch.retrain_cycles = 300
    assert a.offer(m.make_tlp("mwr", 64, 1), p.now)
    while not a.errors[m.E_ROLLOVER]:
        p.step()
    sent = a.counts["tx_tlps"]
    assert a.replay_hold
    while p.ch.recovery(p.now) or p.now == p.ch.recovery_until - 300:
        p.step()
        assert a.counts["tx_tlps"] == sent
    p.step()
    assert not a.replay_hold                      # released on the first cycle back in L0
    run(p, 20)                                    # after the DLLPs already due
    assert a.counts["tx_tlps"] == sent + 1


def test_rollover_hold_falls_back_when_no_retrain_happens():
    p, a, b = up_pair(errors={"drop_tlp_window": (0, 10**9)})
    p.ch.retrain = lambda now: None               # the retrain request is not acted on
    assert a.offer(m.make_tlp("mwr", 64, 1), p.now)
    while not a.errors[m.E_ROLLOVER]:
        p.step()
    sent = a.counts["tx_tlps"]
    run(p, m.ROLLOVER_HOLD_MAX - 2)
    assert a.replay_hold and a.counts["tx_tlps"] == sent
    run(p, 3)
    assert not a.replay_hold and a.counts["tx_tlps"] == sent + 1


def test_sequence_numbers_wrap():
    p, a, b = up_pair()
    a.seq_alloc = a.nts = b.nrs = 4090     # both ends idle: restart near the wrap
    a.ackd = 4089
    tlps = [m.make_tlp("mrd", 0, i) for i in range(20)]
    i = 0
    for _ in range(3000):
        if i < len(tlps) and a.offer(tlps[i], p.now):
            i += 1
        p.step()
    assert b.delivered == tlps and a.nts == (4090 + 20) % 4096 and not a.rbuf


@pytest.mark.parametrize("bad", [{"ph": 128}, {"pd": 7}, {"cpld": 4}, {"qq": 1}])
def test_bad_parameters_rejected(bad):
    with pytest.raises(ValueError):
        m.Dll("x", bad)


# ---------------------------------------------------------------- perf

def test_perf_traffic_is_lossless_and_deterministic():
    w = {"kind": "traffic", "tlps": 300, "mix": "mixed", "payload": -1, "bidirectional": True,
         "errors": {"tlp_corrupt": 0.05, "dllp_drop": 0.05}}
    r1, r2 = m.perf(w, 4), m.perf(w, 4)
    assert r1 == r2
    assert r1["tlps_lost"] == r1["tlps_duplicated"] == r1["tlps_out_of_order"] == 0
    assert r1["replays"] > 0 and r1["err_rx_overflow"] == 0


def test_perf_init_and_bad_workloads():
    assert m.perf({"kind": "init", "trials": 5, "skew": 100}, 1)["dl_active_cycles_max"] <= 200
    with pytest.raises(ValueError):
        m.perf({"kind": "nope"}, 1)
    with pytest.raises(ValueError):
        m.perf({"kind": "traffic", "tlps": 0}, 1)


def test_ack_progress_restarts_a_stopped_timer_while_sent_tlps_remain():
    p, a, b = up_pair(errors={"drop_tlp_window": (0, 10**9)})
    for i in range(3):
        while not a.offer(m.make_tlp("mwr", 16, i), p.now):
            p.step()
    while a.send_idx < 3:
        p.step()
    a.receive(m.Packet(True, m.ack_dllp(m.NAK, 4095)), p.now)   # replay starts: timer stopped
    assert not a.rt_running and a.send_idx == 0
    a.receive(m.Packet(True, m.ack_dllp(m.ACK, 0)), p.now)      # progress, two sent remain
    assert a.rt_running and a.rt_count == 0
    a.receive(m.Packet(True, m.ack_dllp(m.ACK, 2)), p.now)      # nothing sent remains
    assert not a.rt_running


def test_malformed_credit_return_is_header_plus_length_data():
    p, a, b = up_pair()
    ca, cr = [list(x) for x in b.ca], [list(x) for x in b.cr]
    big = bytes([0x40, 0, 0, 40]) + bytes(8 + 160)    # posted, Length 40 DW: 10 data credits
    b.receive(tlp_pkt(0, big), p.now)
    assert b.errors[m.E_MALFORMED] == 1
    assert b.ca[m.P] == [ca[m.P][0] + 1, ca[m.P][1] + 10]
    assert b.cr[m.P] == [cr[m.P][0] + 1, cr[m.P][1] + 10]
    # CREDITS_ALLOCATED - CREDITS_RECEIVED is unchanged: no credit leaks
    assert [x - y for x, y in zip(b.ca[m.P], b.cr[m.P])] == [ca[m.P][0] - cr[m.P][0],
                                                            ca[m.P][1] - cr[m.P][1]]
    before = [list(x) for x in b.ca], [list(x) for x in b.cr]
    nohdr = m.tlp_frame(1, bytes([0x40, 0, 0]))       # 3 TLP bytes: no header
    b.receive(m.Packet(False, nohdr), p.now)
    assert b.errors[m.E_MALFORMED] == 2 and b.nrs == 2
    assert ([list(x) for x in b.ca], [list(x) for x in b.cr]) == before


@pytest.mark.parametrize("npd,ok", [(1, False), (2, True), (0, True)])
def test_npd_minimum_is_two(npd, ok):
    if ok:
        m.Dll("x", {"npd": npd})
    else:
        with pytest.raises(ValueError):
            m.Dll("x", {"npd": npd})


def test_malformed_four_byte_non_posted_tlp_returns_its_credits():
    p, a, b = up_pair()
    b.receive(tlp_pkt(0, m.make_tlp("mrd", 0, 1)), p.now)    # two good TLPs first
    b.receive(tlp_pkt(1, m.make_tlp("mrd", 0, 2)), p.now)
    ca, cr = [list(x) for x in b.ca], [list(x) for x in b.cr]
    b.receive(m.Packet(False, m.tlp_frame(2, bytes([0x44, 0, 0, 1]))), p.now)  # CfgWr DW0 only
    assert b.errors[m.E_MALFORMED] == 1 and b.nrs == 3
    assert b.ca[m.NP] == [ca[m.NP][0] + 1, ca[m.NP][1] + 1]
    assert b.cr[m.NP] == [cr[m.NP][0] + 1, cr[m.NP][1] + 1]
    assert b.ca[m.P] == ca[m.P] and b.cr[m.P] == cr[m.P]
