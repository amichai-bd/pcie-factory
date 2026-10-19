"""Line rewriter of the rc e2e (wiki/src/dv/rc/DV_PLAN.md, line models).

One direction of the serial line between ``rc`` and ``core``, stepped once
per ``clk_i`` cycle with the transmitter's 20-bit word ([9:0] the first
symbol) and its electrical-idle flag; it returns the word the receiver
gets. It lets the e2e replace a packet on the line by other packets of any
length (one TLP by several split completions, a completion by a poisoned,
malformed or Completer Abort one, a request by a malformed one) while the
receiver sees a clean line:

1. it decodes both symbols with the transmitter's running disparity and
   descrambles data symbols with its own LFSR (COM resets it, SKP holds
   it, every other symbol advances it);
2. it holds each packet, from its start symbol (STP, SDP) to its END or
   EDB, until it is whole, then asks ``on_packet(dllp, body, nullified)``
   for its fate: ``None`` keeps it, a list of bodies replaces it (each
   body is what lies between the start symbol and END: for a TLP the
   sequence number, the TLP and the LCRC, for a DLLP its 6 bytes);
3. it re-encodes what it sends with its own LFSR and running disparity,
   so a symbol that keeps its place and value keeps its code, and a
   replaced or moved one is scrambled and encoded for its new place.

While a packet is held, the receiver gets logical idle (scrambled zero
data symbols) in its place; once the packet is sent, the delay is taken
back by dropping logical-idle words (both symbols logical idle after an
END, an EDB or a SKP ordered set). Words go out whole, so packets and
ordered sets keep the symbol slot the transmitter gave them; packets start
in slot 0 (the transmitter's framing, checked). Nothing is rewritten or
dropped before the first COM has synchronised the LFSR, and while the
rewriter does not change a packet the line is bit for bit the
transmitter's, one word later (the testbench register's delay).

``SeqShift`` and ``TlpSide`` add the data-link view: replacing one TLP by
several shifts the sequence numbers of every later TLP of that direction,
so the TLP side renumbers them (new LCRC) and translates the receiver's
Ack and Nak DLLPs, in the other direction, back to the transmitter's
numbers (new CRC); a replay of a replaced TLP is replaced the same way.

Uses the 8b/10b tables of the pcs architecture model, the scrambler of
the mac architecture model and the CRCs of the dll architecture model (DV
may import architecture models; arch BKM).
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

_ARCH = Path(__file__).resolve().parents[3] / "arch"
for _d in ("pcs", "mac", "dll"):
    if str(_ARCH / _d) not in sys.path:
        sys.path.append(str(_ARCH / _d))

import pcie_dll_crc as crc  # noqa: E402
import pcie_mac_arch as mac_arch  # noqa: E402
import pcie_pcs_8b10b as code8b10b  # noqa: E402

K_STP, K_SDP, K_END, K_EDB = code8b10b.K_STP, code8b10b.K_SDP, code8b10b.K_END, code8b10b.K_EDB
K_COM, K_SKP = code8b10b.K_COM, code8b10b.K_SKP
ACK, NAK = 0x00, 0x10
SEQ_MOD = 4096


class LineError(AssertionError):
    """The transmitter did something the rewriter relies on it not doing."""


# An input symbol: (code, k, byte as on the line, key or None, rd_in,
# code error). A generated symbol: (None, k, plain byte, None, None, False).
def _gen(k: bool, byte: int) -> tuple:
    return (None, k, byte, None, None, False)


_EI = ("ei",)


class LineRewriter:
    """One direction of the line. ``on_packet(dllp, body, nullified)``
    returns None (keep) or a list of replacement bodies."""

    def __init__(self, on_packet=None) -> None:
        self.on_packet = on_packet
        self.scr_in = mac_arch.Scrambler()
        self.scr_out = mac_arch.Scrambler()
        self.rd_in: int | None = None
        self.rd_out: int | None = None
        self.synced = False             # a COM has been seen: the LFSR is known
        self.q: deque = deque()         # symbols ready to send, in pairs
        self.pkt: list | None = None    # the packet being received
        self.after_k: int | None = None  # the last K symbol received outside a packet
        self.stats = {"packets": 0, "replaced": 0, "fill_words": 0, "dropped_words": 0}

    # -- one cycle ---------------------------------------------------------

    def step(self, word: int, ei: bool) -> tuple[int, bool]:
        if ei:
            self._ei()
        else:
            syms = [self._decode((word >> (10 * i)) & 0x3FF) for i in (0, 1)]
            if self.synced and len(self.q) >= 2 and self.pkt is None and all(
                    self._idle(s) for s in syms):
                self.stats["dropped_words"] += 1      # taking back the delay
            else:
                for i, s in enumerate(syms):
                    self._take(s, i)
        return self._emit()

    @property
    def delay(self) -> int:
        """Symbols the line is behind the transmitter."""
        return len(self.q) + (len(self.pkt) if self.pkt is not None else 0)

    # -- input ---------------------------------------------------------------

    def _decode(self, code: int) -> tuple:
        byte, k, rd_after, code_err, _ = code8b10b.decode(code, self.rd_in)
        rd_before = self.rd_in
        if self.rd_in is None and not code_err:
            after = code8b10b.rd_after(code, -1), code8b10b.rd_after(code, 1)
            rd_after = after[0] if after[0] == after[1] else code8b10b.rd_after(
                code, code8b10b.DECODE[code][0][2])
        self.rd_in = rd_after
        if k and byte == K_COM:
            self.scr_in.feed(1, K_COM, False)
            self.synced = True
            key = None
        elif k and byte == K_SKP:
            key = None
        else:
            key = self.scr_in.feed(0, 0, True)
        return (code, k, byte, key, rd_before, code_err)

    @staticmethod
    def _plain(s: tuple) -> int:
        return s[2] if s[1] or s[3] is None else s[2] ^ s[3]

    def _idle(self, s: tuple) -> bool:
        """Logical idle: a data symbol scrambling 0, outside a packet, after
        an END, EDB or SKP (not inside a training ordered set)."""
        return (not s[1] and not s[5] and s[3] is not None and self._plain(s) == 0
                and self.after_k in (K_END, K_EDB, K_SKP))

    def _take(self, s: tuple, slot: int) -> None:
        code, k, byte, _, _, err = s
        if not self.synced or self.on_packet is None:   # nothing held: the line as it came
            self.q.append(s)
            return
        if self.pkt is None:
            if k and byte in (K_STP, K_SDP) and not err:
                if slot:
                    raise LineError(f"a packet starts in symbol slot {slot}; the rewriter "
                                    "relies on packets starting in slot 0")
                self.pkt = [s]
                return
            if k and not err:
                self.after_k = byte
            self.q.append(s)
            return
        self.pkt.append(s)
        if err or (k and byte not in (K_END, K_EDB)):
            self._close(cut=True)                       # a broken packet goes as it came
        elif k:
            self._close(cut=False)

    def _close(self, cut: bool) -> None:
        pkt, self.pkt = self.pkt, None
        self.stats["packets"] += 1
        self.after_k = K_END
        last = pkt[-1]
        if not cut and last[2] == K_EDB:
            self.after_k = K_EDB
        new = None
        if not cut and self.synced and self.on_packet is not None:
            body = bytes(self._plain(s) for s in pkt[1:-1])
            new = self.on_packet(pkt[0][2] == K_SDP, body, last[2] == K_EDB)
        if new is None:
            self.q.extend(pkt)
            if len(pkt) % 2:                            # keep words whole
                self.q.append(_gen(False, 0))
            return
        self.stats["replaced"] += 1
        start, end = pkt[0][2], (K_EDB if last[2] == K_EDB else K_END)
        for b in new:
            self.q.append(_gen(True, start))
            self.q.extend(_gen(False, x) for x in b)
            self.q.append(_gen(True, end))
            if len(b) % 2:
                self.q.append(_gen(False, 0))

    def _ei(self) -> None:
        """Electrical idle: anything held goes first; the line state is
        lost (training follows)."""
        if self.pkt is not None:
            self.q.extend(self.pkt)
            self.pkt = None
        if len(self.q) % 2:
            self.q.append(_gen(False, 0))
        self.q.extend((_EI, _EI))
        self.rd_in = None
        self.synced = False
        self.after_k = None

    # -- output --------------------------------------------------------------

    def _emit(self) -> tuple[int, bool]:
        if len(self.q) < 2:
            self.stats["fill_words"] += 1
            if not self.synced:
                raise LineError("a word to fill before the LFSR is known")
            pair = (_gen(False, 0), _gen(False, 0))
        else:
            pair = (self.q.popleft(), self.q.popleft())
        if pair[0] is _EI:
            self.rd_out = None
            return 0, True
        word = 0
        for i, s in enumerate(pair):
            word |= self._encode(s) << (10 * i)
        return word, False

    def _encode(self, s: tuple) -> int:
        code, k, byte, key, rd_in, err = s
        if k and byte == K_COM:
            self.scr_out.feed(1, K_COM, False)
            key_out = None
        elif k and byte == K_SKP:
            key_out = None
        else:
            key_out = self.scr_out.feed(0, 0, True)
        rd = self.rd_out
        if code is not None and (err or k or key is None or key == key_out) and \
                (rd is None or rd == rd_in):
            out = code                                  # unchanged in place
        elif code is not None and err:
            out = code                                  # an invalid code stays invalid
        else:
            if code is None:
                b = byte if k else byte ^ (key_out or 0)
            else:
                b = byte if k or key is None else byte ^ key ^ (key_out or 0)
            if rd is None:
                rd = rd_in if rd_in is not None else -1
            out, _ = code8b10b.encode(b, k, rd)
        base = rd if rd is not None else (rd_in if rd_in is not None else -1)
        self.rd_out = code8b10b.rd_after(out, base)
        return out


# --------------------------------------------------------------- data link

def tlp_body(seq: int, tlp: bytes, lcrc_good: bool = True, nullified: bool = False) -> bytes:
    """The bytes between STP and END: sequence number, TLP, LCRC (inverted
    for a nullified TLP, one byte wrong for a bad one)."""
    head = bytes([(seq >> 8) & 0x0F, seq & 0xFF]) + tlp
    check = crc.lcrc(head)
    if nullified:
        check = crc.invert(check)
    elif not lcrc_good:
        check = bytes([check[0] ^ 0x01]) + check[1:]
    return head + check


def split_body(body: bytes) -> tuple[int, bytes, bool]:
    """(sequence number, TLP, LCRC good) of a TLP body."""
    seq = ((body[0] & 0x0F) << 8) | body[1]
    return seq, body[2:-4], crc.lcrc(body[:-4]) == body[-4:]


def dllp_body(kind: int, seq: int) -> bytes:
    body = bytes([kind, 0, (seq >> 8) & 0x0F, seq & 0xFF])
    return body + crc.crc16(body)


class SeqShift:
    """The sequence numbers one direction's TLPs have on the line after
    some were replaced by several: ``extra[s]`` more TLPs for the
    transmitter's TLP ``s``."""

    def __init__(self) -> None:
        self.extra: dict[int, int] = {}

    def out(self, s: int) -> int:
        """The line number of the transmitter's TLP ``s`` (its first, when
        replaced by several)."""
        return (s + sum(e for t, e in self.extra.items()
                        if 0 < (s - t) % SEQ_MOD < SEQ_MOD // 2)) % SEQ_MOD

    def back(self, a: int) -> int:
        """The transmitter's number of the newest TLP whose whole
        replacement is acknowledged by a receiver Ack (or Nak) of line
        number ``a``."""
        total = sum(self.extra.values())
        for t, e in self.extra.items():
            first = self.out(t)
            if 0 <= (a - first) % SEQ_MOD < e:          # only part of t's replacement
                return (t - 1) % SEQ_MOD
        for d in range(total + 1):
            s = (a - d) % SEQ_MOD
            if (self.out(s) + self.extra.get(s, 0)) % SEQ_MOD == a:
                return s
        return a


class TlpSide:
    """The TLP view of one direction. ``select(seq, tlp)`` returns None
    or the TLPs (header and data, no framing) to send instead; the choice
    for a sequence number is kept, so its replays are replaced alike.
    ``shift`` is this direction's renumbering; ``ack_shift`` the other
    direction's, whose Acks and Naks this direction carries back."""

    def __init__(self, select=None, ack_shift: SeqShift | None = None) -> None:
        self.select = select
        self.shift = SeqShift()
        self.ack_shift = ack_shift
        self.chosen: dict[int, list[bytes] | None] = {}
        self.log: list[tuple[int, int, int]] = []   # (seq, line seq, TLPs sent)
        self.acks_translated = 0

    def on_packet(self, dllp: bool, body: bytes, nullified: bool):
        if dllp:
            return self._dllp(body)
        if len(body) < 18:
            return None                             # too short to be a TLP: as it came
        seq, tlp, good = split_body(body)
        if seq not in self.chosen and good and not nullified and self.select is not None:
            new = self.select(seq, tlp)
            self.chosen[seq] = new
            if new is not None and len(new) > 1:
                self.shift.extra[seq] = len(new) - 1
        new = self.chosen.get(seq)
        line = self.shift.out(seq)
        if new is None:
            if line == seq:
                return None
            return [tlp_body(line, tlp, good, nullified)]
        self.log.append((seq, line, len(new)))
        return [tlp_body((line + i) % SEQ_MOD, t, good, nullified) for i, t in enumerate(new)]

    def _dllp(self, body: bytes):
        if self.ack_shift is None or not self.ack_shift.extra or body[0] not in (ACK, NAK):
            return None
        if crc.crc16(body[:4]) != body[4:6]:
            return None
        a = ((body[2] & 0x0F) << 8) | body[3]
        b = self.ack_shift.back(a)
        if b == a:
            return None
        self.acks_translated += 1
        return [dllp_body(body[0], b)]
