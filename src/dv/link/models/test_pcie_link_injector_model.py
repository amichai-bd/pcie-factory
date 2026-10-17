"""Unit tests of the link e2e line injector (wiki/src/dv/link/DV_PLAN.md).
No simulator.

A reference transmitter in this file builds an L0 symbol stream (logical
idle, SKP ordered sets, TLPs and DLLPs), scrambles and 8b/10b-encodes it;
the injector's output is decoded strictly (every code and disparity
checked, the scrambler re-run) and compared with what each class should
do.

Run: python3 -m pytest src/dv/link/models
"""

import random
import sys
import zlib
from pathlib import Path

import pytest

import pcie_link_injector_model as inj

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "mac"))
import pcie_mac_arch as mac_arch  # noqa: E402

c = inj.code8b10b


def tlp_body(seq, n=12, tag=0):
    """A framed TLP body: sequence number, ``n`` TLP bytes, a 4-byte check."""
    head = bytes([seq >> 8 & 0x0F, seq & 0xFF]) + bytes((tag + i) & 0xFF for i in range(n))
    return head + zlib.crc32(head).to_bytes(4, "little")


def dllp_body(typ, seq=0):
    body = bytes([typ, 0, seq >> 8 & 0x0F, seq & 0xFF])
    return body + bytes([0x5A, 0xA5])


class Tx:
    """Builds the symbol stream, then the words: [9:0] is the first symbol."""

    def __init__(self):
        self.syms = []

    def idle(self, n=2):
        self.syms += [(0, 0x00)] * n
        return self

    def skp(self):
        self.syms += [(1, c.K_COM), (1, c.K_SKP), (1, c.K_SKP), (1, c.K_SKP)]
        return self

    def tlp(self, body):
        self.syms += [(1, c.K_STP)] + [(0, b) for b in body] + [(1, c.K_END)]
        return self

    def dllp(self, body):
        self.syms += [(1, c.K_SDP)] + [(0, b) for b in body] + [(1, c.K_END)]
        return self

    def words(self):
        assert len(self.syms) % 2 == 0
        scr, rd, codes = mac_arch.Scrambler(), -1, []
        scr.feed(1, c.K_COM, True)
        for k, b in [(1, c.K_COM), (0, 0)] + self.syms:   # a COM first sets the LFSR
            code, rd = c.encode(scr.feed(k, b, True), k, rd)
            codes.append(code)
        return [codes[i] | codes[i + 1] << 10 for i in range(0, len(codes), 2)]


def decode_strict(words):
    """(symbols as (k, plain byte, invalid), code errors, disparity errors)."""
    scr, rd, out, cerr, derr = mac_arch.Scrambler(), None, [], 0, 0
    for w in words:
        for code in (w & 0x3FF, w >> 10):
            byte, k, rd_out, ce, de = c.decode(code, rd)
            cerr += ce
            derr += de
            rd = rd_out
            if ce:
                out.append((0, 0, True))
                scr.feed(0, 0, True)
                continue
            out.append((k, scr.feed(k, byte, True), False))
    return out, cerr, derr


def run(tx, setup=None, look=4, seed=1):
    sent, delivered, log = [], [], []
    x = inj.Injector(random.Random(seed), look, delivered_delay=100,
                     on_sent=sent.append, on_delivered=delivered.append,
                     on_inject=lambda t, cls, what: log.append((t, cls, what)))
    if setup:
        setup(x)
    words = tx.words()
    out = [x.step(w, False, t) for t, w in enumerate(words + [words[-1]] * 0)]
    for t in range(len(words), len(words) + look):                  # flush with idle data
        out.append(x.step(Tx().idle().words()[1], False, t))
    got = [w for w, ei in out[look:]]
    assert not any(ei for _, ei in out[look:])
    return x, words, got, sent, delivered, log


def stream():
    t = Tx().idle(4)
    for i in range(3):
        t.tlp(tlp_body(i, 12 + 4 * i, tag=i)).idle(2).dllp(dllp_body(inj.ACK, i)).idle(2)
    t.skp().idle(2)
    t.tlp(tlp_body(3, 40, tag=3)).dllp(dllp_body(inj.NAK, 2)).idle(2)
    t.tlp(tlp_body(1, 16, tag=1)).idle(2)          # a replay of seq 1
    t.dllp(dllp_body(0x80)).idle(4)
    return t


def bodies(view):
    return [(dllp, data) for _, _, dllp, data, _ in view]


def test_passthrough_is_bit_exact_and_views_agree():
    x, words, got, sent, delivered, log = run(stream())
    assert got[:len(words)] == words
    assert bodies(sent) == bodies(delivered) and len(sent) == 10 and log == []
    assert sent[0][2] is False and sent[0][3] == tlp_body(0, 12, 0)
    # Delivered times: the look-ahead (4 words) plus the channel delay (100).
    assert all(d[0] == s[0] + 4 + 100 and d[1] == s[1] + 4 + 100 for s, d in zip(sent, delivered))


def test_scrambler_tracked_across_skp():
    x, words, got, sent, *_ = run(stream())
    assert sent[6][3] == tlp_body(3, 40, tag=3)  # after the SKP ordered set


@pytest.mark.parametrize("seed", range(6))
def test_lcrc_changes_one_byte_and_reencodes_cleanly(seed):
    x, words, got, sent, delivered, log = run(stream(), lambda x: x.request("lcrc"), seed=seed)
    syms, cerr, derr = decode_strict(got)
    assert (cerr, derr) == (0, 0)
    assert [cls for _, cls, _ in log] == ["lcrc"] and "TLP seq 0" in log[0][2]
    diff = [i for i, (a, b) in enumerate(zip(sent[0][3], delivered[0][3])) if a != b]
    assert len(diff) == 1 and 2 <= diff[0] < 14 and not delivered[0][4]
    assert bodies(sent)[1:] == bodies(delivered)[1:]


@pytest.mark.parametrize("seed", range(4))
def test_corrupt_tlp_one_invalid_code(seed):
    x, words, got, sent, delivered, log = run(stream(), lambda x: x.request("corrupt_tlp"), seed=seed)
    syms, cerr, derr = decode_strict(got)
    assert (cerr, derr) == (1, 0)
    assert delivered[0][4] and not any(d[4] for d in delivered[1:])
    assert [cls for _, cls, _ in log] == ["corrupt_tlp"]


def test_drop_tlp_becomes_logical_idle():
    x, words, got, sent, delivered, log = run(stream(), lambda x: x.request("drop_tlp"))
    syms, cerr, derr = decode_strict(got)
    assert (cerr, derr) == (0, 0)
    assert bodies(delivered) == bodies(sent)[1:]
    # Where the TLP was: data 00 only (logical idle), scrambler still in step.
    n = len(tlp_body(0, 12)) + 2
    assert all(k == 0 and b == 0 for k, b, _ in syms[2 + 4:2 + 4 + n])


def test_dllp_classes():
    x, *_, sent, delivered, log = run(stream(), lambda x: x.request("corrupt_dllp"))
    assert [cls for _, cls, _ in log] == ["corrupt_dllp"]
    d = [i for i, (a, b) in enumerate(zip(sent[1][3], delivered[1][3])) if a != b]
    assert len(d) == 1 and d[0] >= 1
    x, words, got, sent, delivered, log = run(stream(), lambda x: x.request("drop_dllp"))
    assert bodies(delivered) == bodies(sent)[:1] + bodies(sent)[2:]
    assert decode_strict(got)[1:] == (0, 0)


def test_nak_class_hits_only_a_nak():
    for seed in range(4):
        x, words, got, sent, delivered, log = run(stream(), lambda x: x.request("nak"), seed=seed)
        assert [cls for _, cls, _ in log] == ["nak"] and "0x10" in log[0][2]
        nak = [i for i, (dl, d) in enumerate(bodies(sent)) if dl and d[0] == inj.NAK][0]
        other = [b for i, b in enumerate(bodies(sent)) if i != nak]
        assert [b for b in bodies(delivered) if b in other] == other
        assert decode_strict(got)[1:] == (0, 0)


def test_replay_tlp_class_hits_the_replay():
    x, *_, sent, delivered, log = run(stream(), lambda x: x.request("replay_tlp"))
    assert len(log) == 1 and log[0][1] == "replay_tlp" and "seq 1 (replay)" in log[0][2]
    assert sent[8][3] != delivered[8][3] and bodies(sent)[:8] == bodies(delivered)[:8]


def test_replay_tlp_by_sequence_number():
    t = Tx().idle(2)
    for seq in (0, 1, 2, 0, 1, 2):
        t.tlp(tlp_body(seq, 12, tag=seq)).idle(2)
    x, *_, sent, delivered, log = run(t, lambda x: x.request("replay_tlp", seq=1))
    assert [(cls, what) for _, cls, what in log] == [("replay_tlp", "TLP seq 1 (replay)")]
    assert [i for i, (a, b) in enumerate(zip(sent, delivered)) if a[3] != b[3]] == [4]
    with pytest.raises(ValueError):
        x.request("lcrc", seq=1)


def test_level_switches_and_rates():
    def acks(x):
        x.drop_acks = True
    x, *_, sent, delivered, log = run(stream(), acks)
    assert x.counts["drop_ack"] == 3
    assert not any(dl and d[0] == inj.ACK for dl, d in bodies(delivered))

    def tlps(x):
        x.drop_tlps = True
    x, *_, sent, delivered, log = run(stream(), tlps)
    assert x.counts["drop_tlp_window"] == 5 and all(dl for dl, _ in bodies(delivered))

    def rates(x):
        x.rates["lcrc"] = 1.0
    x, words, got, sent, delivered, log = run(stream(), rates)
    assert x.counts["lcrc"] == 5 and decode_strict(got)[1:] == (0, 0)


def test_electrical_idle_cuts_a_packet_and_resets():
    sent, delivered = [], []
    x = inj.Injector(random.Random(0), 3, on_sent=sent.append, on_delivered=delivered.append)
    words = Tx().idle(2).tlp(tlp_body(0, 12)).idle(2).words()
    for t, w in enumerate(words[:5]):
        x.step(w, False, t)
    for t in range(5, 12):
        assert x.step(0, True, t)[1] in (True, False)
    assert sent and sent[0][4] and delivered and delivered[0][4]   # cut short: err


def test_bad_arguments():
    with pytest.raises(ValueError):
        inj.Injector(random.Random(0), 2)
    with pytest.raises(ValueError):
        inj.Injector(random.Random(0)).request("bogus")
