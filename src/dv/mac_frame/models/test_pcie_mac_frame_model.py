"""Unit tests of the pcie_mac_frame reference models; no simulator needed.

Run: python3 -m pytest src/dv/mac_frame/models
"""

import random

import pcie_mac_frame_model as m


def words(syms):
    syms = list(syms) + [(0, 0)] * (len(syms) % 2)
    return [(syms[i], syms[i + 1]) for i in range(0, len(syms), 2)]


def run_rx(syms, rx_l0=True):
    ref = m.RxRef()
    pkts, errs, idles = [], 0, []
    for w in words(syms):
        p, e, idle, _ = ref.word(w, True, False, 0, rx_l0)
        pkts += p
        errs += e
        idles += idle
    return pkts, errs, idles


def tx_words(packets, cycles=200, **kw):
    """Drive TxRef with back-to-back packets; return the words it sends."""
    tx, out, beats = m.TxRef(), [], []
    for p in packets:
        n = len(p.data) // 2
        for i in range(n):
            beats.append((p.data[2 * i] | p.data[2 * i + 1] << 8, i == 0, i == n - 1,
                          p.dllp, p.nullify))
    for _ in range(cycles):
        b = beats[0] if beats else (0, 0, 0, 0, 0)
        o = tx.step(0, bool(beats), b[0], b[1], b[2], b[3], b[4], **kw)
        if beats and o.ready:
            beats.pop(0)
        if o.act:
            out.append(((o.datak & 1, o.data & 0xFF), (o.datak >> 1, o.data >> 8)))
    return out


def test_tx_framing_round_trips_through_the_arch_parser():
    rng = random.Random(1)
    pkts = [m.rand_packet(rng, 40) for _ in range(30)]
    sent = tx_words(pkts, cycles=2000, tx_l0=1, tx_stop=0, skp_req=0)
    ref = m.RxRef()
    got = []
    for w in sent:
        got += ref.word(w, True, False, 0, True)[0]
    assert got == pkts
    assert ref.framing_errors == 0


def test_tx_first_word_and_end_word():
    p = m.arch.Packet(False, bytes([1, 2, 3, 4]), nullify=True)
    sent = tx_words([p], cycles=4, tx_l0=1, tx_stop=0, skp_req=0)
    assert sent[:3] == [((1, m.STP), (0, 1)), ((0, 2), (0, 3)), ((0, 4), (1, m.EDB))]
    assert sent[3] == ((0, 0), (0, 0))


def test_tx_ready_rules():
    tx = m.TxRef()
    assert tx.step(0, 1, 0, 1, 0, 0, 0, tx_l0=1, tx_stop=0, skp_req=1).ready == 0
    assert tx.step(0, 1, 0, 1, 0, 0, 0, tx_l0=1, tx_stop=1, skp_req=0).ready == 0
    assert tx.step(0, 1, 0, 1, 0, 0, 0, tx_l0=0, tx_stop=0, skp_req=0).ready == 0
    assert tx.step(0, 1, 0, 1, 0, 0, 0, tx_l0=1, tx_stop=0, skp_req=0).ready == 1
    # Inside the packet ready stays 1 whatever the controls do; the line
    # leaving L0 aborts it with EDB.
    o = tx.step(0, 1, 0, 0, 0, 0, 0, tx_l0=0, tx_stop=1, skp_req=1)
    assert (o.ready, o.event, o.data, o.datak, o.act) == (1, "abort", m.EDB, 1, 1)
    assert tx.step(0, 1, 0, 0, 1, 0, 0, tx_l0=0, tx_stop=1, skp_req=1).event == "discard_eop"
    assert tx.step(1, 1, 0, 1, 0, 0, 0, tx_l0=1, tx_stop=0, skp_req=0).ready == 0


def test_skp_schedule_and_grant():
    s = m.SkpSched()
    for _ in range(589):
        assert s.req == 0
        s.step(0, True)
    s.step(0, True)
    assert s.req == 1
    assert s.step(1, True) == ((1, m.COM), (1, m.SKP))
    assert s.req == 1
    assert s.step(1, True) == ((1, m.SKP), (1, m.SKP))
    assert s.req == 0 and s.credit == 4


def test_rx_framing_errors_follow_the_symbols_page():
    # Nonzero data outside a packet: one error, then the resync discards
    # symbols without errors up to END.
    _, errs, _ = run_rx([(0, 5), (0, 6), (1, m.PAD), (1, m.END), (0, 0), (0, 0)])
    assert errs == 1
    # COM inside a packet: the packet ends bad and it is a framing error.
    pkts, errs, _ = run_rx([(1, m.STP), (0, 1), (0, 2), (0, 3), (1, m.COM), (1, m.SKP),
                            (1, m.SKP), (1, m.SKP)])
    assert len(pkts) == 1 and pkts[0].err and errs == 1
    # Leaving Rx_L0 ends a resync: idle data counts again.
    ref = m.RxRef()
    ref.word(((0, 5), (0, 6)), True, False, 0, True)
    assert ref.word(((0, 0), (0, 0)), True, False, 0, True)[2] == [0, 0]
    assert ref.word(((0, 0), (0, 0)), True, False, 0, False)[2] == [1, 1]
    # A packet with no data: no packet, one framing error.
    pkts, errs, _ = run_rx([(1, m.STP), (1, m.END)])
    assert pkts == [] and errs == 1


def test_rx_flags():
    pkts, errs, idle = run_rx([(1, m.STP), (0, 1), (0, 2), (1, m.EDB),
                               (1, m.SDP)] + [(0, 9)] * 6 + [(1, m.EDB), (0, 0), (0, 0)])
    assert errs == 0
    assert (pkts[0].nullify, pkts[0].err) == (True, False)
    assert (pkts[1].dllp, pkts[1].nullify, pkts[1].err) == (True, False, True)
    assert idle[-2:] == [1, 1]


def test_rx_random_stream_is_deterministic():
    a = m.gen_rx_items(random.Random(7), 50, 0.2)
    b = m.gen_rx_items(random.Random(7), 50, 0.2)
    assert a == b and len(a) > 50
