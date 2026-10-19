"""Unit tests of the rc e2e line rewriter (pcie_rc_line_model.py).

A synthetic transmitter, written here from the 8b/10b and scrambler rules
of the pcs and mac architecture models, sends SKP ordered sets, logical
idle, DLLPs and TLPs; a receiver decodes what the rewriter sends. Each test
checks what the receiver sees, and that the line returns to the
transmitter's own words once the delay is taken back.
"""

import random
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent))
import pcie_rc_line_model as lm  # noqa: E402

code8b10b, mac_arch, crc = lm.code8b10b, lm.mac_arch, lm.crc


def _invalid(rd: int) -> int:
    """A code in neither column that leaves running disparity ``rd``."""
    for code in range(1024):
        _, _, out, err, _ = code8b10b.decode(code, rd)
        if err and out == rd and 3 <= bin(code).count("1") <= 7:
            return code
    raise RuntimeError("no invalid code")


INVALID_CODE = {-1: _invalid(-1), 1: _invalid(1)}


class Tx:
    """A transmitter: symbols in, 20-bit words out, slot 0 first."""

    def __init__(self) -> None:
        self.scr = mac_arch.Scrambler()
        self.rd = -1
        self.syms: list[tuple[bool, int]] = []

    def skp(self) -> None:
        self.syms += [(True, lm.K_COM)] + [(True, lm.K_SKP)] * 3

    def idle(self, words: int) -> None:
        self.syms += [(False, 0)] * (2 * words)

    def packet(self, dllp: bool, body: bytes, end: int = lm.K_END, bad_at: int | None = None) -> None:
        """``end`` END or EDB; ``bad_at`` replaces that body byte with an
        invalid code."""
        self.syms += [(True, lm.K_SDP if dllp else lm.K_STP)]
        self.syms += [("bad", 0) if i == bad_at else (False, b) for i, b in enumerate(body)]
        self.syms.append((True, end))
        if len(self.syms) % 2:
            self.syms.append((False, 0))

    def words(self) -> list[int]:
        assert len(self.syms) % 2 == 0
        out = []
        for i in range(0, len(self.syms), 2):
            w = 0
            for j in (0, 1):
                k, b = self.syms[i + j]
                if k == "bad":
                    self.scr.feed(0, 0, True)
                    w |= INVALID_CODE[self.rd] << (10 * j)
                    continue
                if k:
                    self.scr.feed(1, b, False)
                    byte = b
                else:
                    byte = self.scr.feed(0, b, True)
                code, self.rd = code8b10b.encode(byte, k, self.rd)
                w |= code << (10 * j)
            out.append(w)
        self.syms = []
        return out


class Rx:
    """A receiver: decodes words, descrambles, collects packets."""

    def __init__(self) -> None:
        self.scr = mac_arch.Scrambler()
        self.rd = None
        self.cur = None
        self.packets: list[tuple[bool, bytes]] = []
        self.edb: list[bytes] = []
        self.errors = 0

    def word(self, w: int) -> None:
        for j in (0, 1):
            byte, k, self.rd, err, derr = code8b10b.decode((w >> (10 * j)) & 0x3FF, self.rd)
            if err or derr:
                self.errors += 1
                self.scr.feed(0, 0, True)
                if self.cur is not None:
                    self.cur[1].append(0)
                continue
            if k:
                self.scr.feed(1, byte, False)
                plain = byte
            else:
                plain = self.scr.feed(0, byte, True)
            if k and plain in (lm.K_STP, lm.K_SDP):
                self.cur = [plain == lm.K_SDP, bytearray()]
            elif k and plain in (lm.K_END, lm.K_EDB) and self.cur is not None:
                self.packets.append((self.cur[0], bytes(self.cur[1])))
                if plain == lm.K_EDB:
                    self.edb.append(bytes(self.cur[1]))
                self.cur = None
            elif not k and self.cur is not None:
                self.cur[1].append(plain)


def tlp_mrd(tag: int) -> bytes:
    return bytes([0x00, 0, 0, 1, 0, 0, tag, 0x0F, 0x10, 0, 0, 0])


def cpld(tag: int, data: bytes, byte_count: int | None = None) -> bytes:
    n = len(data) // 4
    bc = len(data) if byte_count is None else byte_count
    return bytes([0x4A, 0, 0, n, 0, 0, (bc >> 8) & 0xF, bc & 0xFF, 0, 0, tag, 0]) + data


def run(rw: lm.LineRewriter, words: list[int], rx: Rx) -> list[int]:
    out = []
    for w in words:
        o, ei = rw.step(w, False)
        assert not ei
        rx.word(o)
        out.append(o)
    return out


def traffic(tx: Tx, bodies: list[tuple[bool, bytes]], gap: int = 6) -> list[int]:
    tx.skp()
    tx.idle(4)
    for dllp, b in bodies:
        tx.packet(dllp, b)
        tx.idle(gap)
    tx.idle(400)
    tx.skp()
    tx.idle(8)
    return tx.words()


def test_pass_through_is_the_same_line():
    tx, rx = Tx(), Rx()
    bodies = [(True, lm.dllp_body(lm.ACK, 5)), (False, lm.tlp_body(7, tlp_mrd(3)))]
    words = traffic(tx, bodies)
    rw = lm.LineRewriter()
    out = run(rw, words, rx)
    assert out == words
    assert rx.packets == bodies and not rx.errors
    # Held and kept: the same packets, the delay taken back by the end.
    tx, rx = Tx(), Rx()
    words = traffic(tx, bodies)
    rw = lm.LineRewriter(on_packet=lambda d, b, n: None)
    out = run(rw, words, rx)
    assert rx.packets == bodies and not rx.errors
    assert rw.delay == 0 and len(out) == len(words)


def test_one_tlp_replaced_by_three_then_the_line_catches_up():
    tx, rx = Tx(), Rx()
    data = bytes(range(128))
    orig = lm.tlp_body(9, cpld(1, data))
    words = traffic(tx, [(False, orig), (True, lm.dllp_body(lm.ACK, 2))])
    splits = [cpld(1, data[:32]), cpld(1, data[32:96], 96), cpld(1, data[96:], 32)]

    def on_packet(dllp, body, nullified):
        return None if dllp else [lm.tlp_body(9 + i, t) for i, t in enumerate(splits)]

    rw = lm.LineRewriter(on_packet)
    out = run(rw, words, rx)
    assert not rx.errors
    assert [p for p in rx.packets if not p[0]] == [(False, lm.tlp_body(9 + i, t))
                                                   for i, t in enumerate(splits)]
    assert (True, lm.dllp_body(lm.ACK, 2)) in rx.packets
    assert rw.delay == 0 and rw.stats["fill_words"] and rw.stats["dropped_words"]
    assert len(out) == len(words)


def test_shorter_replacement_and_tlp_side_renumbering():
    tx, rx = Tx(), Rx()
    bodies = [(False, lm.tlp_body(s, cpld(s, bytes(8)))) for s in (20, 21, 22)]
    words = traffic(tx, bodies)
    seen = []

    def select(seq, tlp):
        seen.append(seq)
        if seq == 20:
            return [cpld(20, b""), tlp]                # one more TLP
        if seq == 21:
            return [tlp[:2] + bytes([tlp[2] | 0x40]) + tlp[3:]]  # poisoned, same length
        return None

    side = lm.TlpSide(select)
    rw = lm.LineRewriter(side.on_packet)
    run(rw, words, rx)
    got = [lm.split_body(b) for d, b in rx.packets if not d]
    assert [(s, ok) for s, _, ok in got] == [(20, True), (21, True), (22, True), (23, True)]
    assert got[2][1][2] & 0x40 and got[3][1] == cpld(22, bytes(8))
    assert side.shift.out(21) == 22 and side.shift.out(22) == 23
    assert seen == [20, 21, 22] and not rx.errors


def test_seq_shift_back():
    sh = lm.SeqShift()
    sh.extra[100] = 2                     # 100 became line 100, 101, 102
    assert [sh.out(s) for s in (99, 100, 101, 102)] == [99, 100, 103, 104]
    assert sh.back(99) == 99
    assert sh.back(100) == 99 and sh.back(101) == 99      # 100 only partly acknowledged
    assert sh.back(102) == 100 and sh.back(103) == 101 and sh.back(104) == 102
    sh2 = lm.SeqShift()
    sh2.extra[4095] = 1                   # wraps
    assert sh2.out(0) == 1 and sh2.back(4095) == 4094 and sh2.back(0) == 4095
    assert sh2.back(1) == 0 and sh2.back(2) == 1


def test_acks_carried_back_and_replays_replaced_alike():
    up = lm.TlpSide(lambda seq, t: [t, t] if seq == 3 else None)
    dn = lm.TlpSide(ack_shift=up.shift)
    first = up.on_packet(False, lm.tlp_body(3, tlp_mrd(0)), False)
    again = up.on_packet(False, lm.tlp_body(3, tlp_mrd(0)), False)    # a replay
    assert first == again and len(first) == 2
    assert dn.on_packet(True, lm.dllp_body(lm.ACK, 4), False) == [lm.dllp_body(lm.ACK, 3)]
    assert dn.on_packet(True, lm.dllp_body(lm.NAK, 3), False) == [lm.dllp_body(lm.NAK, 2)]
    assert dn.on_packet(True, lm.dllp_body(lm.ACK, 2), False) is None
    bad = up.on_packet(False, lm.tlp_body(4, tlp_mrd(1), lcrc_good=False), False)
    assert bad and not lm.split_body(bad[0])[2] and lm.split_body(bad[0])[0] == 5


def test_packet_in_slot_one_is_refused():
    tx = Tx()
    tx.skp()
    tx.syms += [(False, 0)]
    tx.syms += [(True, lm.K_SDP)] + [(False, b) for b in lm.dllp_body(lm.ACK, 1)] + [(True, lm.K_END)]
    tx.syms += [(False, 0)]
    rw = lm.LineRewriter(lambda d, b, n: None)
    with pytest.raises(lm.LineError):
        for w in tx.words():
            rw.step(w, False)


def test_random_replacements_keep_the_line_clean():
    rng = random.Random(377)
    for _ in range(20):
        tx, rx = Tx(), Rx()
        bodies = []
        for s in range(rng.randint(3, 8)):
            if rng.random() < 0.3:
                bodies.append((True, lm.dllp_body(lm.ACK, rng.randrange(4096))))
            else:
                bodies.append((False, lm.tlp_body(s, cpld(s, bytes(4 * rng.randint(1, 32))))))
        words = traffic(tx, bodies, gap=rng.randint(0, 12))
        reps = {}

        def on_packet(dllp, body, nullified):
            if dllp or rng.random() < 0.5:
                return None
            n = rng.randint(0, 3)
            reps[body] = [bytes(rng.getrandbits(8) for _ in range(4 * rng.randint(4, 30)))
                          for _ in range(n)]
            return reps[body]

        rw = lm.LineRewriter(on_packet)
        run(rw, words, rx)
        want = []
        for d, b in bodies:
            want += [(False, r) for r in reps[b]] if b in reps else [(d, b)]
        assert rx.packets == want and not rx.errors
        assert rw.delay == 0


def test_nullified_and_broken_tlps_keep_their_fault_while_renumbered():
    """After a replacement shifts the numbers, a nullified TLP (EDB) is
    renumbered with its LCRC still inverted, and a TLP with an invalid
    code goes as it came: still an error at the receiver, never fixed."""
    tx, rx = Tx(), Rx()
    tx.skp()
    tx.idle(4)
    tx.packet(False, lm.tlp_body(3, tlp_mrd(0)))
    tx.idle(6)
    tx.packet(False, lm.tlp_body(4, tlp_mrd(1), nullified=True), end=lm.K_EDB)
    tx.idle(6)
    tx.packet(False, lm.tlp_body(5, tlp_mrd(2)), bad_at=9)
    tx.idle(6)
    tx.packet(False, lm.tlp_body(6, tlp_mrd(3)))
    tx.idle(400)
    tx.skp()
    tx.idle(8)
    side = lm.TlpSide(lambda seq, t: [t, t] if seq == 3 else None)
    run(lm.LineRewriter(side.on_packet), tx.words(), rx)
    seqs = [lm.split_body(b)[0] for d, b in rx.packets]
    assert seqs[:2] == [3, 4] and seqs[2] == 5 and seqs[-1] == 7, seqs
    edb = [lm.split_body(b) for b in rx.edb]
    assert len(edb) == 1 and edb[0][0] == 5 and not edb[0][2]
    assert rx.packets[2][1][-4:] == lm.crc.invert(lm.crc.lcrc(rx.packets[2][1][:-4]))
    assert rx.errors == 1, "the invalid code reaches the receiver"
