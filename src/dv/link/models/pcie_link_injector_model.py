"""Line error injector of the link e2e (wiki/src/dv/link/DV_PLAN.md, injector;
wiki/src/arch/link/HAS.md, error injection and passive checker).

One direction of the serial line, placed right at the transmitting
``link``'s ``ser_tx_data_o`` and stepped once per edge of the transmitter's
clock, with the 20-bit word ([9:0] the first symbol) and its electrical
idle flag. It:

1. decodes both symbols with the transmitter's running disparity and
   descrambles data symbols with its own LFSR (COM resets it, SKP holds
   it, every other symbol advances it), which tracks the transmitter's
   scrambler across SKP ordered sets;
2. finds packets between their start symbol (STP, SDP) and their END and
   records each as **sent** (``on_sent``);
3. holds ``look_words`` words, so it can decide a packet's fate once it
   has seen the TLP's sequence number or the DLLP's type, then applies one
   class to it (``request``, ``rates`` or the level switches) and logs it
   (``on_inject``);
4. re-encodes with its own running disparity, so the receiver sees no
   other error: an unchanged symbol keeps its code while the two running
   disparities agree; any change re-encodes from there on;
5. parses its own output and records each packet as **delivered**
   (``on_delivered``), with ``err`` when the receiver sees it as bad on the
   line (an invalid symbol, or cut short).

Times are the ``t`` passed to ``step``: sent packets carry the times their
first and last symbols entered (the transmitter's serial port); delivered
ones the times they left plus ``delivered_delay``, the channel delay to
the receiver's serial port, so both views sit at a ``link`` port (the
passive checker's Ack bound needs that).

Classes (HAS error injection), on the next matching packet:

========== ==========================================================
``lcrc``        one TLP byte (header) changed, re-encoded cleanly
``corrupt_tlp`` one TLP symbol replaced by an invalid code
``drop_tlp``    the whole TLP, framing included, replaced by logical idle
``corrupt_dllp`` one DLLP byte changed
``drop_dllp``   the whole DLLP replaced by logical idle
``nak``         the next Nak dropped or corrupted (drawn)
``replay_tlp``  the next replayed TLP (a sequence number not after the
                newest seen), or with ``seq`` the next replay of that
                TLP, gets an LCRC error
========== ==========================================================

Level switches: ``drop_acks`` drops every Ack (the duplicate class),
``drop_tlps`` every TLP (the REPLAY_NUM rollover class). ``rates`` gives a
per-packet probability for the first five classes.

Uses the 8b/10b tables of the pcs architecture model and the scrambler of
the mac architecture model (DV may import architecture models; arch BKM).
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

_ARCH = Path(__file__).resolve().parents[3] / "arch"
for _d in ("pcs", "mac"):
    if str(_ARCH / _d) not in sys.path:
        sys.path.append(str(_ARCH / _d))

import pcie_mac_arch as mac_arch  # noqa: E402
import pcie_pcs_8b10b as code8b10b  # noqa: E402

K_STP, K_SDP, K_END, K_EDB = code8b10b.K_STP, code8b10b.K_SDP, code8b10b.K_END, code8b10b.K_EDB
K_COM, K_SKP = code8b10b.K_COM, code8b10b.K_SKP
ACK, NAK = 0x00, 0x10
SEQ_MOD = 4096
TLP_CLASSES = ("lcrc", "corrupt_tlp", "drop_tlp")
DLLP_CLASSES = ("corrupt_dllp", "drop_dllp")
CLASSES = TLP_CLASSES + DLLP_CLASSES + ("nak", "replay_tlp")


def _max_run(code: int) -> int:
    run = best = 1
    for i in range(1, 10):
        run = run + 1 if (code >> i & 1) == (code >> (i - 1) & 1) else 1
        best = max(best, run)
    return best


def _invalid_code(rd: int) -> int:
    """An invalid code that leaves running disparity ``rd`` as it is, with
    no run of five equal bits (so no comma can appear around it)."""
    for code in range(1024):
        _, _, rd_out, code_err, _ = code8b10b.decode(code, rd)
        if code_err and rd_out == rd and _max_run(code) <= 4 and abs(bin(code).count("1") - 5) <= 1:
            return code
    raise RuntimeError("no invalid code found")


INVALID = {-1: _invalid_code(-1), 1: _invalid_code(1)}


class _Sym:
    """One symbol in the look-ahead buffer."""
    __slots__ = ("code", "k", "byte", "key", "rd_in", "rd_after", "mod", "t_in")

    def __init__(self, code, k, byte, key, rd_in, rd_after, t_in):
        self.code, self.k, self.byte, self.key = code, k, byte, key
        self.rd_in, self.rd_after, self.t_in = rd_in, rd_after, t_in
        self.mod = None                  # None, "idle", ("xor", v) or "invalid"

    def plain(self) -> int:
        return self.byte if self.k or self.key is None else self.byte ^ self.key


class _Parser:
    """Packets in a symbol stream: (start, end, dllp, data, err)."""

    def __init__(self, emit) -> None:
        self.emit = emit
        self.cur = None                  # [start, dllp, bytearray, err]

    def symbol(self, t, k: bool, byte: int, invalid: bool = False) -> None:
        if invalid:
            if self.cur is not None:
                self.cur[3] = True
                self.cur[2].append(0)
            return
        if k and byte in (K_STP, K_SDP):
            if self.cur is not None:     # a start inside a packet: the old one is cut
                self._end(t, True)
            self.cur = [t, byte == K_SDP, bytearray(), False]
        elif self.cur is None:
            return
        elif k and byte in (K_END, K_EDB):
            self._end(t, self.cur[3] or byte == K_EDB)
        elif k:
            self._end(t, True)           # any other K inside a packet cuts it
        else:
            self.cur[2].append(byte)

    def idle(self, t) -> None:
        if self.cur is not None:
            self._end(t, True)

    def _end(self, t, err: bool) -> None:
        start, dllp, data, _ = self.cur
        self.cur = None
        self.emit((start, t, dllp, bytes(data), err))


class Injector:
    def __init__(self, rng: random.Random, look_words: int = 4, delivered_delay: float = 0.0,
                 on_sent=None, on_delivered=None, on_inject=None) -> None:
        if look_words < 3:
            raise ValueError("look_words: at least 3 (a TLP's sequence number)")
        self.rng = rng
        self.look = look_words
        self.delivered_delay = delivered_delay
        self.on_sent = on_sent or (lambda p: None)
        self.on_delivered = on_delivered or (lambda p: None)
        self.on_inject = on_inject or (lambda t, cls, what: None)
        self.rates = dict.fromkeys(TLP_CLASSES + DLLP_CLASSES, 0.0)
        self.pending = dict.fromkeys(CLASSES, 0)
        self.drop_acks = False
        self.drop_tlps = False
        self.replay_seq: int | None = None
        self.counts = dict.fromkeys(CLASSES + ("drop_ack", "drop_tlp_window"), 0)
        self.buf: deque = deque()        # (ei, [sym, sym] or None, t_in)
        self.scr = mac_arch.Scrambler()
        self.rd_in = None                # transmitter's running disparity (None: unknown)
        self.rd_out = None
        self.last_new = SEQ_MOD - 1      # newest TLP sequence number seen
        self.pkt = None                  # the packet entering: dict
        self.sent_parser = _Parser(self.on_sent)
        self.out_parser = _Parser(self._delivered)
        self.steps = 0

    # -- control ----------------------------------------------------------

    def request(self, cls: str, n: int = 1, seq: int | None = None) -> None:
        if cls not in CLASSES:
            raise ValueError(f"injector class: expected one of {CLASSES}, got {cls}")
        if seq is not None and cls != "replay_tlp":
            raise ValueError("seq: replay_tlp only")
        self.pending[cls] += n
        if cls == "replay_tlp":
            self.replay_seq = seq

    def idle(self) -> bool:
        """No one-shot request still waiting for its packet."""
        return not any(self.pending.values())

    # -- one edge -----------------------------------------------------------

    def step(self, word: int, ei: bool, t) -> tuple[int, bool]:
        self.steps += 1
        if ei:
            self.rd_in = None
            self.sent_parser.idle(t)
            self.pkt = None
            self.buf.append((True, None, t))
        else:
            syms = [self._decode((word >> (10 * i)) & 0x3FF, t) for i in (0, 1)]
            self.buf.append((False, syms, t))
        if len(self.buf) <= self.look:
            return 0, True
        return self._emit(t)

    def _decode(self, code: int, t) -> _Sym:
        byte, k, rd_after, _, _ = code8b10b.decode(code, self.rd_in)
        rd_before = self.rd_in
        if self.rd_in is None:
            # Unknown until a code fixes it: a neutral code leaves either.
            after = code8b10b.rd_after(code, -1), code8b10b.rd_after(code, 1)
            rd_after = after[0] if after[0] == after[1] else None
            if rd_after is None and code8b10b.DECODE.get(code):
                rd_after = code8b10b.rd_after(code, code8b10b.DECODE[code][0][2])
        self.rd_in = rd_after
        if k and byte == K_COM:
            self.scr.feed(1, K_COM, True)
            key = None
        elif k and byte == K_SKP:
            key = None
        else:
            key = self.scr.feed(0, 0, True)
        s = _Sym(code, k, byte, key, rd_before, rd_after, t)
        self.sent_parser.symbol(t, k, s.plain())
        self._track(s)
        return s

    # -- packet decisions -----------------------------------------------------

    def _track(self, s: _Sym) -> None:
        if s.k and s.byte in (K_STP, K_SDP):
            self.pkt = {"dllp": s.byte == K_SDP, "syms": [s], "bytes": [], "action": None,
                        "decided": False, "t": s.t_in}
            return
        p = self.pkt
        if p is None:
            return
        p["syms"].append(s)
        idx = len(p["syms"]) - 1
        if s.k:
            self._apply(p, s, idx)
            self.pkt = None
            return
        p["bytes"].append(s.plain())
        n = len(p["bytes"])
        if not p["decided"] and n == (1 if p["dllp"] else 2):
            self._decide(p)
            for i, x in enumerate(p["syms"]):
                self._apply(p, x, i)
        elif p["decided"]:
            self._apply(p, s, idx)

    def _take(self, cls: str) -> bool:
        if self.pending[cls] > 0:
            self.pending[cls] -= 1
            return True
        return cls in self.rates and self.rates[cls] > 0 and self.rng.random() < self.rates[cls]

    def _decide(self, p: dict) -> None:
        p["decided"] = True
        b = p["bytes"]
        action, cls = None, None
        if p["dllp"]:
            typ = b[0]
            what = f"DLLP type {typ:#04x}"
            if self.drop_acks and typ == ACK:
                action, cls = ("drop",), "drop_ack"
            elif typ == NAK and self.pending["nak"]:
                self.pending["nak"] -= 1
                cls = "nak"
                action = ("drop",) if self.rng.random() < 0.5 else self._corrupt_byte(1, 6)
            elif self._take("corrupt_dllp"):
                action, cls = self._corrupt_byte(1, 6), "corrupt_dllp"
            elif self._take("drop_dllp"):
                action, cls = ("drop",), "drop_dllp"
        else:
            seq = ((b[0] & 0x0F) << 8) | b[1]
            replay = not 0 < (seq - self.last_new) % SEQ_MOD < SEQ_MOD // 2
            if not replay:
                self.last_new = seq
            what = f"TLP seq {seq}{' (replay)' if replay else ''}"
            if self.drop_tlps:
                action, cls = ("drop",), "drop_tlp_window"
            elif replay and self.pending["replay_tlp"] and self.replay_seq in (None, seq):
                self.pending["replay_tlp"] -= 1
                action, cls = self._corrupt_byte(2, 14), "replay_tlp"
            elif self._take("lcrc"):
                action, cls = self._corrupt_byte(2, 14), "lcrc"
            elif self._take("corrupt_tlp"):
                action, cls = ("invalid", self.rng.randrange(2, 14)), "corrupt_tlp"
            elif self._take("drop_tlp"):
                action, cls = ("drop",), "drop_tlp"
        p["action"] = action
        if cls is not None:
            self.counts[cls] += 1
            self.on_inject(p["t"], cls, what)

    def _corrupt_byte(self, lo: int, hi: int) -> tuple:
        """A byte position in [lo, hi) of the packet body, XORed with a
        nonzero value (any CRC detects a single changed byte)."""
        return ("xor", self.rng.randrange(lo, hi), self.rng.randrange(1, 256))

    def _apply(self, p: dict, s: _Sym, idx: int) -> None:
        a = p["action"]
        if a is None:
            return
        if a[0] == "drop":
            s.mod = "idle"
            return
        if idx - 1 == a[1] and not s.k:      # idx 0 is the start symbol
            s.mod = ("xor", a[2]) if a[0] == "xor" else "invalid"

    # -- output ---------------------------------------------------------------

    def _emit(self, t) -> tuple[int, bool]:
        ei, syms, _ = self.buf.popleft()
        if ei:
            self.rd_out = None
            self.out_parser.idle(t)
            return 0, True
        word = 0
        for i, s in enumerate(syms):
            word |= self._encode(s, t) << (10 * i)
        return word, False

    def _encode(self, s: _Sym, t) -> int:
        """The output code of one symbol. Unchanged while the two running
        disparities agree; otherwise (and for every changed symbol)
        encoded at the output's running disparity."""
        rd = self.rd_out
        if s.mod is None and rd == s.rd_in:
            self.rd_out = s.rd_after
            self.out_parser.symbol(t, s.k, s.plain())
            return s.code
        if rd is None:
            rd = s.rd_in if s.rd_in is not None else -1
        if s.mod == "invalid":
            code = INVALID[rd]
            self.out_parser.symbol(t, False, 0, invalid=True)
        else:
            if s.mod == "idle":
                k, byte = False, s.key if s.key is not None else 0
            elif s.mod is not None:
                k, byte = False, s.byte ^ s.mod[1]
            else:
                k, byte = s.k, s.byte
            code, _ = code8b10b.encode(byte, k, rd)
            self.out_parser.symbol(t, k, byte if k or s.key is None else byte ^ s.key)
        self.rd_out = code8b10b.rd_after(code, rd)
        return code

    def _delivered(self, p) -> None:
        start, end, dllp, data, err = p
        self.on_delivered((start + self.delivered_delay, end + self.delivered_delay,
                           dllp, data, err))
