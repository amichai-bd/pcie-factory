"""Reference models for pcie_mac_frame (wiki/src/dv/mac_frame/DV_PLAN.md).

- ``TxRef``: the transmit rules of the MAS (ready, STP/SDP, END/EDB, abort,
  idle, SKP grant), cycle by cycle at the sub-IP's ports. Written from
  wiki/src/rtl/mac_frame/MAS.md and the mac HAS, not from the RTL.
- ``RxRef``: the receive reference. It is the ``pcie_mac`` architecture
  model's own receive parser (``Mac._rx_word``), imported, not copied
  (arch BKM, DV and arch): a ``Mac`` frozen in L0 is fed the same words.
  The adapter only scrambles the plain symbols the RTL sees into line
  symbols for the model's descrambler, applies the MAS rule that leaving
  ``Rx_L0`` cuts an open packet, and maps a packet with no data byte to
  "no beats, one framing error" (MAS, Receive).
- ``SkpSched``: a stand-in for the ordered-set transmitter's SKP schedule
  (mac SYMBOLS page, SKP schedule): a credit of symbols sent, a request
  while 1180 or more are due, two SKP words per granted ordered set.
- ``gen_rx_items``: random receive stimulus (packets, ordered sets,
  framing errors) as symbol lists.

Python standard library and the arch model only; no simulator.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "mac"))
import pcie_mac_arch as arch  # noqa: E402

COM, STP, SDP, END, EDB = 0xBC, 0xFB, 0x5C, 0xFD, 0xFE
SKP, FTS, IDL, PAD = 0x1C, 0x3C, 0x7C, 0xF7
SKP_INTERVAL = arch.SKP_INTERVAL_MIN    # 1180 symbols
OTHER_K = (0x1C, 0x3C, 0x7C, 0xF7, 0xFD, 0xFE)   # K symbols that are no start


# ----------------------------------------------------------------- transmit

class TxOut:
    """One cycle of the transmit outputs."""

    __slots__ = ("ready", "gnt", "act", "data", "datak", "pkt", "event")

    def __init__(self):
        self.ready = self.gnt = self.act = self.pkt = 0
        self.data, self.datak = 0, 0
        self.event = None      # "sop", "end", "edb_end", "abort", "discard_eop"


class TxRef:
    """The transmit half of pcie_mac_frame at its ports, one ``step`` per
    clk_i cycle, from the MAS (Transmit)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.up_open = False      # sop accepted, eop not yet
        self.line_open = False    # STP/SDP sent, its end not yet
        self.carry = 0
        self.dllp = False
        self.tail_edb = False

    def step(self, rst, valid, data, sop, eop, dllp, nullify, tx_l0, tx_stop, skp_req):
        o = TxOut()
        if rst:
            self.reset()
            return o
        start_ok = tx_l0 and not tx_stop and not skp_req and not self.line_open
        o.ready = int(self.up_open or start_ok)
        accept = valid and o.ready
        o.gnt = int(tx_l0 and skp_req and not self.line_open)
        o.act = int((tx_l0 or self.line_open) and not o.gnt)
        lo, hi = data & 0xFF, data >> 8
        word, k = 0x0000, 0
        abort = self.line_open and (not tx_l0 or (self.up_open and not valid))
        if abort:
            word, k, o.pkt, o.event = EDB, 0b01, 1, "abort"
            self.line_open = False
            if accept and eop:
                self.up_open = False
        elif self.line_open and self.up_open:
            word, o.pkt = lo << 8 | self.carry, 1
            self.carry = hi
            if eop:
                self.up_open = False
                self.tail_edb = bool(nullify) and not self.dllp
        elif self.line_open:
            word, k, o.pkt = (EDB if self.tail_edb else END) << 8 | self.carry, 0b10, 1
            o.event = "edb_end" if self.tail_edb else "end"
            self.line_open = False
        elif accept and not self.up_open:
            word, k, o.pkt, o.event = lo << 8 | (SDP if dllp else STP), 0b01, 1, "sop"
            self.carry, self.line_open, self.up_open = hi, True, not eop
            self.dllp = bool(dllp)
            self.tail_edb = bool(eop and nullify and not dllp)
        elif accept and eop:
            self.up_open = False
            o.event = "discard_eop"
        o.data, o.datak = word, k
        return o


class SkpSched:
    """Stand-in for the ordered-set transmitter's SKP schedule: ``req`` is
    the request for the cycle; ``step`` returns the SKP word sent when
    granted (else None) and counts the symbols the line carried."""

    def __init__(self, interval: int = SKP_INTERVAL):
        self.interval = interval
        self.credit = 0
        self.words_left = 0       # words of the SKP ordered set being sent

    @property
    def req(self) -> int:
        return int(self.credit >= self.interval or self.words_left > 0)

    def step(self, gnt: int, line_active: bool):
        word = None
        if gnt:
            if self.words_left == 0:
                self.credit -= self.interval
                self.words_left = 2
                word = ((1, COM), (1, SKP))
            else:
                word = ((1, SKP), (1, SKP))
            self.words_left -= 1
        else:
            self.words_left = 0   # the line left L0: the set is abandoned
        if line_active:
            self.credit += 2
        return word


# ------------------------------------------------------------------ receive

class RxRef(arch.Mac):
    """The arch model's receive parser, frozen in L0, driven with the plain
    (descrambled) symbols the RTL sees."""

    def __init__(self):
        super().__init__()
        self.rst = False
        self.inject.freeze = True          # no LTSSM arc: L0 stays L0
        self.state = arch.L0
        self._set_rx(arch.RL0)
        self._resync = False
        self.line_scr = arch.Scrambler()   # the far transmitter's scrambler
        self._sym_idle: list[bool] = []
        self._lane = 0
        self.start_lane: int | None = None
        self.events: list[str] = []        # what this word exercised
        self.ends: list[tuple] = []        # (packet, start lane, cause) per packet end

    def _parsed(self, k: int, b: int) -> bool:
        """Whether the model parses this symbol as packet or idle (not as an
        ordered-set symbol); such data symbols are scrambled on the line."""
        if k and b == COM:
            return False
        p = self._p
        if p in (1, 2):
            return False
        if p == 3 and k and b == SKP:
            return False
        if p == 4 and k and b in (IDL, FTS):
            return False
        return True

    def _rx_sym(self, k: int, b: int) -> None:
        parsed = self._parsed(k, b)
        in_l0 = self.rx_sub == arch.RL0
        opened = self._pkt is not None
        if k and b == COM and opened:
            self.events.append("com_in_pkt")
        elif parsed and opened and k and b in (STP, SDP):
            self.events.append("start_in_pkt")
        elif parsed and opened and k and b not in (END, EDB):
            self.events.append("k_in_pkt")
        elif parsed and not opened and in_l0:
            if self._resync and not (k and b in (STP, SDP, END, EDB)):
                self.events.append("resync_discard")
            elif not self._resync and k and b in (END, EDB):
                self.events.append("end_outside")
            elif not self._resync and k and b not in (STP, SDP):
                self.events.append("k_outside")
            elif not self._resync and not k and b != 0:
                self.events.append("data_outside")
        line_b = self.line_scr.feed(k, b, parsed)
        before = self.idle_run
        super()._rx_sym(k, line_b)
        self._sym_idle.append(self.idle_run > before)
        if not opened and self._pkt is not None:
            self.start_lane = self._lane
        self._lane += 1

    def _pkt_end(self, err: bool, edb: bool = False) -> None:
        n, dllp, bad_status = len(self._pkt), self._pkt_dllp, self._pkt_bad
        super()._pkt_end(err, edb)
        p = self.rx_packets[-1]
        if n == 0:
            cause = "empty"
        elif err:
            cause = "cut"
        elif bad_status:
            cause = "status"
        elif n % 2:
            cause = "odd"
        elif dllp and edb:
            cause = "dllp_edb"
        elif dllp and n != 6:
            cause = "dllp_len"
        elif edb:
            cause = "nullify"
        else:
            cause = "good"
        self.ends.append((p, self.start_lane, cause))

    def word(self, syms, valid: bool, elec_idle: bool, status: int, rx_l0: bool):
        """One word; returns (packets with data, framing error, idle flags)."""
        n0, f0, e0 = len(self.rx_packets), self.framing_errors, len(self.ends)
        self._sym_idle, self._lane = [], 0
        if not rx_l0 and self._pkt is not None:
            self.events.append("cut_l0")
            self._pkt_end(err=True)                # leaving Rx_L0 cuts it (MAS)
        self.rx_sub = arch.RL0 if rx_l0 else arch.RE
        if not rx_l0:
            self._resync = False               # as the model's L0 exit and entry do (MAS)
        if self._pkt is not None:
            if status >= 4:
                self.events.append("status")
            if not valid:
                self.events.append("cut_valid")
            elif elec_idle:
                self.events.append("cut_ei")
        self._rx_word(arch.PipeRx(syms=tuple(syms), valid=valid, elec_idle=elec_idle,
                                  status=status, phy_status=False))
        new = self.rx_packets[n0:]
        empty = any(not p.data for p in new)
        if empty:
            self.events.append("empty")
        ferr = self.framing_errors > f0 or (empty and rx_l0)
        idle = [int(x) for x in self._sym_idle] + [0] * (2 - len(self._sym_idle))
        ends = [e for e in self.ends[e0:] if e[0].data]
        return [p for p in new if p.data], ferr, idle, ends


# ----------------------------------------------------------------- stimulus

def rand_bytes(rng: random.Random, n: int) -> bytes:
    return bytes(rng.randrange(256) for _ in range(n))


def rand_packet(rng: random.Random, max_tlp: int) -> arch.Packet:
    """A legal upper-layer packet: a 6-byte DLLP or an even-length TLP,
    10% of TLPs nullified."""
    if rng.random() < 0.3:
        return arch.Packet(True, rand_bytes(rng, 6))
    n = 2 * rng.randint(1, max_tlp // 2)
    return arch.Packet(False, rand_bytes(rng, n), nullify=rng.random() < 0.1)


def _pkt_syms(dllp: bool, data: bytes, end: int) -> list:
    return [(1, SDP if dllp else STP)] + [(0, x) for x in data] + [(1, end)]


def gen_rx_items(rng: random.Random, count: int, p_err: float, max_tlp: int = 64):
    """``count`` random items of a receive symbol stream; returns the list
    of symbols. Good packets, idle and ordered sets; with probability
    ``p_err`` an item is malformed or a framing error."""
    syms: list = []
    for _ in range(count):
        r = rng.random()
        if r < p_err:
            kind = rng.choice(("stp_in_pkt", "com_in_pkt", "k_in_pkt", "empty", "odd",
                               "dllp_len", "dllp_edb", "data_out", "k_out", "end_out"))
            data = rand_bytes(rng, 2 * rng.randint(1, 8))
            if kind == "stp_in_pkt":
                syms += [(1, STP)] + [(0, x) for x in data]
            elif kind == "com_in_pkt":
                syms += [(1, STP)] + [(0, x) for x in data] + [(1, COM)] + [(1, SKP)] * 3
            elif kind == "k_in_pkt":
                syms += [(1, STP)] + [(0, x) for x in data] + [(1, rng.choice(OTHER_K[:4]))]
                syms += [(0, rng.randrange(256))] * rng.randint(0, 3) + [(1, END)]
            elif kind == "empty":
                syms += [(1, rng.choice((STP, SDP))), (1, rng.choice((END, EDB)))]
            elif kind == "odd":
                syms += _pkt_syms(False, data[:-1], END)
            elif kind == "dllp_len":
                syms += _pkt_syms(True, rand_bytes(rng, rng.choice((2, 4, 8))), END)
            elif kind == "dllp_edb":
                syms += _pkt_syms(True, rand_bytes(rng, 6), EDB)
            elif kind == "data_out":
                syms += [(0, rng.randrange(1, 256))] + [(0, rng.randrange(256))] * rng.randint(0, 3)
                syms += [(1, rng.choice((STP, SDP, END, EDB)))]
                if syms[-1][1] in (STP, SDP):
                    syms += [(0, x) for x in data] + [(1, END)]
            elif kind == "k_out":
                syms += [(1, rng.choice((PAD, SKP, FTS, IDL)))] + [(0, 0)] * rng.randint(0, 3)
                syms += [(1, END)]
            else:
                syms += [(1, rng.choice((END, EDB)))]
            continue
        r = rng.random()
        if r < 0.45:
            p = rand_packet(rng, max_tlp)
            syms += _pkt_syms(p.dllp, p.data, EDB if p.nullify else END)
        elif r < 0.7:
            syms += [(0, 0)] * rng.randint(1, 6)
        elif r < 0.8:
            syms += [(1, COM)] + [(1, SKP)] * rng.randint(1, 5)
        elif r < 0.85:
            syms += [(1, COM)] + [(1, rng.choice((IDL, FTS)))] * 3
        else:
            kind = rng.choice((arch.TS1_ID, arch.TS2_ID))
            syms += [(1, COM), (0, rng.randrange(256)), (1, PAD), (0, 32), (0, 2), (0, 0)]
            syms += [(0, kind)] * 10
    return syms
