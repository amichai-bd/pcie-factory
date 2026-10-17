"""Passive mode of the independent DLL link partner (wiki/src/dv/dll/PARTNER.md,
passive mode; wiki/src/arch/link/HAS.md, passive checker).

In the ``link`` e2e the partner is not a far end: it watches the line
between two ``dll`` instances and judges both transmitters against the
rules of the dll HAS pages. It is fed packet *views* that the e2e's line
injector records, one per direction (``"ab"``: sent by end a towards b,
``"ba"`` the reverse):

- **sent**: what the transmitting end put on the line, before injection;
- **delivered**: what the receiving end got, after injection.

Each view is a list of ``ViewPacket`` records (start and end time, the
DLLP/TLP tag, the bytes between the framing symbols, and ``err`` when the
receiver sees the packet as bad on the line: an invalid symbol, EDB, or
cut short). Times are in any one unit for both directions; ``cycle`` is
one ``clk_i`` cycle in that unit. The recorder must place each view where
the check reads it: sent at the transmitter's serial output, delivered at
the receiver's serial input (with every channel delay between the
recorder and the receiver added), so the Ack bound applies to the line
alone.

Checks (one view each, so injected drops, corruptions and duplicates
need no exemption; they show only in the delivered view):

========== ======================================= ==========================
Check      Reads                                   Flags
========== ======================================= ==========================
credits    the sender's TLPs, sent view of its     a first transmission whose
           direction; InitFC/UpdateFC in the       credits exceed the limit
           delivered reverse view that reached
           the sender's line before the TLP
sequence   sent view of one direction; Acks and    a new TLP not one past the
           Naks in the delivered reverse view      last new one; a replay not
                                                   from the oldest TLP not
                                                   acknowledged, or not in
                                                   order and complete
acknak     Acks and Naks, sent view of one         a sequence number naming a
           direction; TLPs, delivered view of the  TLP that did not arrive
           other                                   good and in order
ack_timing the covered TLP's end, delivered view;  a gap above ``ack_bound``
           the covering DLLP's start, reverse      cycles
           sent view
========== ======================================= ==========================

Using an Ack's arrival on the line makes the sender-side checks looser by
the receiving end's processing time, never stricter. The sequence check
accepts a replay starting at any TLP between the oldest not acknowledged
by Acks that arrived ``ack_window`` cycles before it (the sender may not
have seen later ones yet) and the oldest not acknowledged by every Ack
that arrived before it.

``rollover(name, t)`` records end ``name``'s REPLAY_NUM rollover pulse and
``retrain(name, start, end)`` a time range in which it was out of L0. A
replay may stop short by its newest TLP only when the sender's rollover
pulse falls while the replay's last TLP goes out (the rollover withdraws
the TLP offered but not started; at a plain Recovery the offered TLP just
waits), or when the sender's link-down follows it (the link-down ends the
sender's epoch, so the short replay is its last). A TLP may be cut short on the line (``err``
in the sent view) only when it ends within ``cut_slack`` cycles of the
sender's out-of-L0 range or link-down (the phy cuts a packet when the
LTSSM leaves L0).

``link_down(name, t)`` starts a new epoch for that end (its state restarts
in DL_Inactive). ``exempt(start, end, why)`` marks a time range in which the Ack timing
bound does not apply (the link left L0, or a line impairment that the
views cannot see hit it); ``inject(time, direction, cls, what)`` records
the injection log. Standard library and the partner's own modules only
(M2 Q3).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.append(str(_HERE))

from pcie_dll_partner import (  # noqa: E402
    ACK, CLASSES, DATA_W, HDR_W, NAK, SEQ_MOD, _FC_KIND, tlp_class, tlp_credits,
)
from pcie_dll_partner_crc import crc16_bytes, lcrc_bytes  # noqa: E402

DIRECTIONS = ("ab", "ba")


class ViewPacket(NamedTuple):
    """One packet in a view: ``data`` is the bytes between the start and end
    framing symbols (TLP: sequence number, TLP, LCRC; DLLP: 6 bytes)."""
    start: float
    end: float
    dllp: bool
    data: bytes
    err: bool = False


class Injection(NamedTuple):
    time: float
    direction: str
    cls: str
    what: str


def _rev(d: str) -> str:
    return d[::-1]


def _ahead(a: int, b: int) -> bool:
    """Sequence ``a`` is after ``b`` (modulo 4096, half range)."""
    return 0 < (a - b) % SEQ_MOD < SEQ_MOD // 2


def _seq(data: bytes) -> int:
    return ((data[0] & 0x0F) << 8) | data[1]


def good_dllp(p: ViewPacket) -> bool:
    return p.dllp and not p.err and len(p.data) == 6 and crc16_bytes(p.data[:4]) == p.data[4:]


def good_tlp(p: ViewPacket) -> bool:
    return (not p.dllp and not p.err and len(p.data) >= 6
            and lcrc_bytes(p.data[:-4]) == p.data[-4:])


class PassiveChecker:
    """Collects the views of both directions, then ``check()`` judges them."""

    def __init__(self, cycle: float = 1.0, ack_bound: int = 140, ack_window: int = 40,
                 cut_slack: int = 20, names: tuple[str, str] = ("a", "b")) -> None:
        self.cycle = cycle
        self.ack_bound = ack_bound
        self.ack_window = ack_window
        self.cut_slack = cut_slack
        self.retrains: dict[str, list[tuple[float, float]]] = {n: [] for n in names}
        self.rollovers: dict[str, list[float]] = {n: [] for n in names}
        self.names = names
        self.views = {(v, d): [] for v in ("sent", "delivered") for d in DIRECTIONS}
        self.log: list[Injection] = []
        self.exempts: list[tuple[float, float, str]] = []
        self.unsure: list[tuple[float, float, str]] = []
        self.downs: dict[str, list[float]] = {n: [] for n in names}
        self._sel: dict = {}            # the views of the epoch being checked
        self.violations: list[str] = []
        self.stats: dict[str, int] = {}
        self.ack_gap_max = 0.0          # cycles, over the TLPs the timing check judged

    # -- recording ------------------------------------------------------------

    def sent(self, direction: str, pkt: ViewPacket) -> None:
        self.views[("sent", direction)].append(pkt)

    def delivered(self, direction: str, pkt: ViewPacket) -> None:
        self.views[("delivered", direction)].append(pkt)

    def inject(self, time: float, direction: str, cls: str, what: str = "") -> None:
        self.log.append(Injection(time, direction, cls, what))

    def exempt(self, start: float, end: float, why: str, line: bool = False) -> None:
        """No Ack timing bound for TLPs arriving in [start, end]. With
        ``line``, a line impairment the views cannot see: a packet delivered
        then may not have reached the receiver, so an Ack or Nak delivered
        in the range counts as maybe seen by its sender (it widens the
        accepted replay start, never narrows it)."""
        self.exempts.append((start, end, why))
        if line:
            self.unsure.append((start, end, why))

    def retrain(self, name: str, start: float, end: float) -> None:
        """End ``name`` was out of L0 from ``start`` to ``end``."""
        self.retrains[name].append((start, end))

    def rollover(self, name: str, t: float) -> None:
        """End ``name`` pulsed a REPLAY_NUM rollover at ``t``."""
        self.rollovers[name].append(t)

    def link_down(self, name: str, t: float) -> None:
        """End ``name``'s LinkUp fell at ``t``: it went to DL_Inactive, so its
        sequence numbers, credits and receive state start again. Its packets
        are judged per epoch: those it sent (by start) and those that
        reached it (by end) from one link-down to the next."""
        self.downs[name].append(t)

    def injections(self, direction: str | None = None, cls: str | None = None) -> list[Injection]:
        return [i for i in self.log if (direction is None or i.direction == direction)
                and (cls is None or i.cls == cls)]

    # -- helpers --------------------------------------------------------------

    def _count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    def _violate(self, check: str, direction: str, t: float, msg: str) -> None:
        sender = self.names[DIRECTIONS.index(direction)]
        self.violations.append(f"{check} ({direction}, sender {sender}) at "
                               f"{t / self.cycle:.1f} cycles: {msg}")

    def _acks(self, view: str, direction: str) -> list[tuple[float, float, bool, int]]:
        """Good Acks and Naks of a view: (start, end, nak, seq)."""
        out = []
        for p in self._view(view, direction):
            if good_dllp(p) and p.data[0] in (ACK, NAK):
                out.append((p.start, p.end, p.data[0] == NAK, _seq(p.data[2:4])))
        return out

    # -- checks ---------------------------------------------------------------

    def check(self) -> list[str]:
        """Run every check over both directions, per link-down epoch of the
        end whose state the check follows; returns the violations."""
        self.violations, self.stats = [], {}
        self._horizon = max([p.end for v in self.views.values() for p in v], default=0.0)
        inf = float("inf")
        for d in DIRECTIONS:
            sender, receiver = self.names[DIRECTIONS.index(d)], self.names[1 - DIRECTIONS.index(d)]
            for name, check, sent_d, dlv_d in ((sender, self._check_tx, d, _rev(d)),
                                                (receiver, self._check_acknak, _rev(d), d)):
                cuts = [-inf] + sorted(self.downs[name]) + [inf]
                for self._epoch, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
                    self._sel = {
                        ("sent", sent_d): [p for p in self.views[("sent", sent_d)]
                                           if lo <= p.start < hi],
                        ("delivered", dlv_d): [p for p in self.views[("delivered", dlv_d)]
                                               if lo <= p.end < hi]}
                    self._hi = hi
                    check(d)
        self._sel = {}
        return self.violations

    def _view(self, view: str, direction: str) -> list[ViewPacket]:
        return self._sel.get((view, direction), self.views[(view, direction)])

    def _check_tx(self, d: str) -> None:
        """Credits and sequence for the sender of direction ``d``: its sent
        TLPs against the DLLPs that reached its line (delivered, reverse)."""
        events = []                     # (time, order, kind, item)
        for p in self._view("delivered", _rev(d)):
            if good_dllp(p):
                events.append((p.end, 0, "dllp", p))
        for p in self._view("sent", d):
            if not p.dllp:
                events.append((p.start, 1, "tlp", p))
        events.sort(key=lambda e: (e[0], e[1]))
        limit = {c: [None, None] for c in CLASSES}       # None: not recorded
        infinite = {c: [False, False] for c in CLASSES}
        consumed = {c: [0, 0] for c in CLASSES}
        acks: list[tuple[float, int]] = []               # (arrival, seq) surely seen
        ackd = SEQ_MOD - 1
        next_new = 0
        replay = None                   # [last seq on the line, newest sent at start]
        last_start = None               # start of the previous TLP on the line
        last_end = None                 # and its end
        for t, _, kind, p in events:
            if kind == "dllp":
                typ = p.data[0]
                if typ in (ACK, NAK):
                    s = _seq(p.data[2:4])
                    newest = (next_new - 1) % SEQ_MOD
                    if not _ahead(s, newest) and _ahead(s, ackd) or s == ackd:
                        ackd = s
                        if not any(a <= p.start and p.end <= b for a, b, _ in self.unsure):
                            acks.append((t, s))
                    continue
                kind_cls = _FC_KIND.get(typ & 0xF8)
                if kind_cls is None:
                    continue
                fc, cls = kind_cls
                hdr = ((p.data[1] & 0x3F) << 2) | (p.data[2] >> 6)
                dat = ((p.data[2] & 0x0F) << 8) | p.data[3]
                if fc.startswith("InitFC"):
                    if limit[cls][0] is None:
                        limit[cls] = [hdr, dat]
                        infinite[cls] = [hdr == 0, dat == 0]
                elif limit[cls][0] is not None:
                    for f, v in ((0, hdr), (1, dat)):
                        if not infinite[cls][f]:
                            limit[cls][f] = v
                continue
            # A TLP going out on the sender's line. One the phy cut short
            # (the LTSSM left L0 inside it) still went out: its sequence
            # number and credits count, as far as its bytes tell.
            if len(p.data) < 2:
                self._count(f"cut_unread_{d}")
                continue
            s, tlp = _seq(p.data), (p.data[2:] if p.err else p.data[2:-4])
            self._count(f"tlp_{d}")
            if p.err:
                self._count(f"cut_{d}")
                if not self._cut_ok(d, p):
                    self._violate("sequence", d, t, f"TLP {s} cut short on the line while "
                                  "the sender was in L0 (no retrain or link-down near it)")
            prev_start, last_start = last_start, p.start
            prev_end, last_end = last_end, p.end
            if s == next_new:
                if replay is not None and not self._replay_done(replay, ackd):
                    self._violate("sequence", d, t, f"new TLP {s} before the replay from "
                                  f"{replay[0]} to {replay[1]} completed")
                replay = None
                next_new = (s + 1) % SEQ_MOD
                self._count(f"new_{d}")
                self._credit(d, t, s, tlp, limit, infinite, consumed)
                continue
            newest = (next_new - 1) % SEQ_MOD
            # Acknowledged as far as the sender can have seen by now.
            early = SEQ_MOD - 1
            for at, a in acks:
                if at <= t - self.ack_window * self.cycle:
                    early = a
            if not (_ahead(s, early) and not _ahead(s, newest)):
                self._violate("sequence", d, t, f"TLP {s} is neither new ({next_new}) nor "
                              f"sent and unacknowledged ({(early + 1) % SEQ_MOD} to {newest})")
                continue
            self._count(f"replayed_{d}")
            if replay is not None and _ahead(s, replay[0]) and not _ahead(s, replay[1]) \
                    and all(not _ahead(q, ackd) for q in self._between(replay[0], s)):
                replay[0] = s           # in order; any skipped TLP was acknowledged
                continue
            # A replay starts here.
            if replay is not None and not self._replay_done(replay, ackd):
                if self._withdrawn(d, replay, ackd, prev_start, prev_end):
                    self._count(f"withdrawn_{d}")
                else:
                    self._violate("sequence", d, t, f"replay restarts at {s} before the one "
                                  f"from {replay[0]} to {replay[1]} completed")
            oldest_late, oldest_early = (ackd + 1) % SEQ_MOD, (early + 1) % SEQ_MOD
            if _ahead(s, oldest_late) or _ahead(oldest_early, s):
                self._violate("sequence", d, t, f"replay starts at {s}, expected the oldest "
                              f"unacknowledged TLP, {oldest_early} to {oldest_late}")
            self._count(f"replays_{d}")
            replay = [s, newest]

    @staticmethod
    def _between(a: int, b: int) -> list[int]:
        """Sequence numbers strictly between ``a`` and ``b``."""
        return [(a + i) % SEQ_MOD for i in range(1, (b - a) % SEQ_MOD)]

    def _replay_done(self, replay: list[int], ackd: int) -> bool:
        last, end = replay
        return all(not _ahead(q, ackd) for q in self._between(last, (end + 1) % SEQ_MOD))

    def _sender(self, d: str) -> str:
        return self.names[DIRECTIONS.index(d)]

    def _withdrawn(self, d: str, replay: list[int], ackd: int, prev_start, prev_end) -> bool:
        """The REPLAY_NUM rollover withdrawal (dll RETRY page, REPLAY_NUM):
        the TLP offered but not started when the rollover happens is not
        sent, and the rollover replay, after the retrain, resends it. So a
        replay may stop short by its newest TLP only, and only when the
        sender's rollover pulse falls while the short replay's last TLP goes
        out: from ``cut_slack`` cycles (the phy transmit latency) before its
        start to ``cut_slack`` cycles after its end. A later rollover
        cannot have withdrawn it: by then the newest TLP was due on the
        line (dll RETRY page, REPLAY_NUM; dll MAS decisions). A link-down
        needs no window here: it ends the sender's epoch (``check``), so
        the short replay is the last the epoch judges."""
        last, end = replay
        missing = [q for q in self._between(last, (end + 1) % SEQ_MOD) if _ahead(q, ackd)]
        if missing != [end] or prev_start is None:
            return False
        slack = self.cut_slack * self.cycle
        lo, hi = prev_start - slack, prev_end + slack
        return any(lo <= x <= hi for x in self.rollovers[self._sender(d)])

    def _cut_ok(self, d: str, p: ViewPacket) -> bool:
        """A sent TLP cut short: allowed only at the sender's exit from L0
        (a retrain) or its link-down, within ``cut_slack`` cycles."""
        name, slack = self._sender(d), self.cut_slack * self.cycle
        near = [(a, b) for a, b in self.retrains[name]] + [(t, t) for t in self.downs[name]]
        return any(a - slack <= p.end <= b + slack for a, b in near)

    def _credit(self, d, t, s, tlp, limit, infinite, consumed) -> None:
        if len(tlp) < 4:
            self._count(f"credit_unread_{d}")   # cut before its Length: nothing to judge
            return
        cls = tlp_class(tlp)
        if cls is None:
            self._violate("credits", d, t, f"TLP {s} has a prefix")
            return
        if limit[cls][0] is None:
            self._violate("credits", d, t, f"TLP {s} ({cls}) before the partner's InitFC")
            return
        need = tlp_credits(tlp)
        for f, w in ((0, HDR_W), (1, DATA_W)):
            if infinite[cls][f]:
                continue
            after = (consumed[cls][f] + need[f]) % 2 ** w
            if (limit[cls][f] - after) % 2 ** w > 2 ** (w - 1):
                self._violate("credits", d, t, f"TLP {s} ({cls}) needs {need[f]} "
                              f"{('header', 'data')[f]} credits, {(limit[cls][f] - consumed[cls][f]) % 2 ** w} "
                              "available")
            consumed[cls][f] = after
        self._count(f"credit_checked_{d}")

    def _check_acknak(self, d: str) -> None:
        """Ack and Nak, and Ack timing, for TLPs travelling in direction
        ``d``: the receiver's NEXT_RCV_SEQ from the delivered view, its Acks
        and Naks from the reverse sent view."""
        rcv = []                        # (end, seq) of TLPs that arrived good and in order
        dup = []                        # (end, seq) of good duplicates: Acked at once
        nxt = 0
        for p in self._view("delivered", d):
            if not good_tlp(p):
                continue
            q = _seq(p.data)
            if q == nxt:
                rcv.append((p.end, nxt))
                nxt = (nxt + 1) % SEQ_MOD
            elif not _ahead(q, (nxt - 1) % SEQ_MOD):
                dup.append((p.end, q))
        acks = self._acks("sent", _rev(d))
        # Ack and Nak: never ahead of what had arrived good and in order.
        i, last = 0, SEQ_MOD - 1
        for start, _, nak, s in acks:
            while i < len(rcv) and rcv[i][0] <= start:
                last = rcv[i][1]
                i += 1
            self._count(f"acknak_{_rev(d)}")
            if _ahead(s, last):
                self._violate("acknak", _rev(d), start, f"{'Nak' if nak else 'Ack'} {s} names a "
                              f"TLP not yet received good and in order (last {last})")
        # Ack timing: the first covering Ack or Nak after each such TLP.
        horizon = min(self._horizon, self._hi)     # the receiver's link-down ends its epoch
        timed = sorted([(end, s, "") for end, s in rcv] + [(end, s, "dup_") for end, s in dup])
        j = 0
        for end, s, kind in timed:
            if any(a <= end + self.ack_bound * self.cycle and b >= end
                   for a, b, _ in self.exempts):
                self._count(f"{kind}ack_timing_exempt_{d}")
                continue
            while j < len(acks) and acks[j][0] < end:
                j += 1
            cover = next((a for a in acks[j:] if not _ahead(s, a[3])), None)
            if cover is None:
                if horizon - end > self.ack_bound * self.cycle:
                    self._violate("ack_timing", _rev(d), end, f"TLP {s} never acknowledged")
                continue
            gap = (cover[0] - end) / self.cycle
            self.ack_gap_max = max(self.ack_gap_max, gap)
            self._count(f"{kind}ack_timing_{d}")
            self._count(f"{kind}ack_timing_{d}_epoch{self._epoch}")
            if gap > self.ack_bound:
                what = "duplicate TLP" if kind else "TLP"
                self._violate("ack_timing", _rev(d), end, f"{what} {s} covered by an Ack or Nak "
                              f"{gap:.1f} cycles after it arrived, bound {self.ack_bound}")
