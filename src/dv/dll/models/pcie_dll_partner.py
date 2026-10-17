"""Independent DLL link partner (wiki/src/dv/dll/PARTNER.md).

The far-end data link layer that the ``dll`` and ``link`` testbenches put
opposite the design: it builds and checks DLLPs, frames TLPs with sequence
numbers and LCRC, acknowledges and naks, replays, runs flow-control
initialisation and UpdateFC, and keeps credits on both sides, all on the
``phy`` upper packet stream (16-bit beats). It is the second opinion of M2
Q3: written from the dll HAS pages (wiki/src/arch/dll/) and the author's own
reading of the PCIe Base Spec rev 3.0 rules they cite, by a different author
from the dll architecture model, sharing no code with it and written
without reading it. Nothing under src/arch/dll/ is imported or read.

Use, once per clock edge:

    beat_out = partner.clock(beat_in)

``beat_in`` is the beat the far end's packet stream delivers this cycle
(``Beat`` or None) and ``beat_out`` the beat the partner drives (or None,
idle). Inputs are plain attributes set before ``clock``: ``link_up``
(the physical layer's LinkUp), ``tx_ready`` (the stream takes beats this
cycle), ``in_l0`` (0 holds the replay timer, and a replay held after a
REPLAY_NUM rollover waits for it to fall and rise again) and ``upper_ready`` (the
partner's upper layer takes received TLPs). ``send(tlp)`` queues a TLP for
the far end; ``received`` lists the TLPs delivered upward, in order.

Error injection lives in ``Inject`` (rates drawn from the partner's own
seeded generator, plus one-shot requests) and in ``withhold_credits``,
``force_nak`` and ``ack_delay``. ``violations`` collects every rule the far
end broke that injection cannot explain (a test asserts it stays empty);
``stats`` counts events.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.append(str(_HERE))

from pcie_dll_partner_crc import crc16_bytes, lcrc_bytes  # noqa: E402

SEQ_MOD = 4096
CLASSES = ("P", "NP", "Cpl")
HDR_W, DATA_W = 8, 12           # credit counter widths

# Data link control states.
INACTIVE, FC_INIT1, FC_INIT2, ACTIVE = "DL_Inactive", "FC_INIT1", "FC_INIT2", "DL_Active"

# DLLP type byte values (DLLP.md, types used); FC types carry the VC in [2:0].
ACK, NAK = 0x00, 0x10
INITFC1 = {"P": 0x40, "NP": 0x50, "Cpl": 0x60}
INITFC2 = {"P": 0xC0, "NP": 0xD0, "Cpl": 0xE0}
UPDATEFC = {"P": 0x80, "NP": 0x90, "Cpl": 0xA0}
_FC_KIND = {}
for _cls in CLASSES:
    _FC_KIND[INITFC1[_cls]] = ("InitFC1", _cls)
    _FC_KIND[INITFC2[_cls]] = ("InitFC2", _cls)
    _FC_KIND[UPDATEFC[_cls]] = ("UpdateFC", _cls)


class Beat(NamedTuple):
    """One beat of the packet stream: ``data`` [7:0] is the earlier byte."""
    data: int
    sop: bool
    eop: bool
    dllp: bool
    err: bool = False
    nullify: bool = False


@dataclass
class Timers:
    """Partner timers in clk_i cycles (125 MHz; RETRY.md#timers values)."""
    replay_timeout: int = 356
    ack_latency: int = 32
    initfc_resend: int = 128
    updatefc_refresh: int = 2048
    rollover_fallback: int = 1024   # hold ends if L0 is not left by then


@dataclass
class Inject:
    """Error injection. Rates are probabilities per packet, drawn from the
    partner's generator; ``*_next`` counters apply to that many upcoming
    packets first. TX hooks act on packets the partner sends (the packet
    still takes its line time; a dropped one is driven as idle beats), RX
    hooks on packets it receives (as if the line had lost or damaged
    them)."""
    tx_tlp_drop: float = 0.0
    tx_tlp_corrupt: float = 0.0
    tx_tlp_nullify: float = 0.0     # send a nullified copy first, then the TLP
    tx_dllp_drop: float = 0.0
    tx_dllp_corrupt: float = 0.0
    rx_tlp_drop: float = 0.0
    rx_tlp_nak: float = 0.0         # treat a good expected TLP as bad: discard, Nak
    rx_dllp_drop: float = 0.0
    tx_tlp_drop_next: int = 0
    tx_tlp_corrupt_next: int = 0
    tx_dllp_drop_next: int = 0
    tx_dllp_corrupt_next: int = 0
    rx_tlp_drop_next: int = 0
    rx_tlp_nak_next: int = 0
    rx_dllp_drop_next: int = 0


# ---------------------------------------------------------------------------
# TLP header helpers (FLOW_CONTROL.md, classes and credits)


def tlp_class(tlp: bytes) -> str | None:
    """P, NP or Cpl from header byte 0; None for a TLP prefix (Fmt 100)."""
    fmt, typ = tlp[0] >> 5, tlp[0] & 0x1F
    if fmt == 0b100:
        return None
    if typ == 0b00000 and fmt & 0b010:
        return "P"                      # memory write
    if typ >> 3 == 0b10:
        return "P"                      # message, with or without data
    if typ in (0b01010, 0b01011):
        return "Cpl"
    return "NP"


def tlp_payload_dw(tlp: bytes) -> int:
    """Payload DWs from the Length field (0 is 1024), 0 without data."""
    if not tlp[0] & 0x40:
        return 0
    length = ((tlp[2] & 0x03) << 8) | tlp[3]
    return length or 1024


def tlp_credits(tlp: bytes) -> tuple[int, int]:
    """(header credits, data credits): one header, 16 bytes per data credit."""
    return 1, (tlp_payload_dw(tlp) + 3) // 4


def tlp_size(tlp: bytes) -> int:
    """Bytes the header says the TLP has: 3 or 4 header DWs plus payload."""
    return 4 * ((4 if tlp[0] & 0x20 else 3) + tlp_payload_dw(tlp))


def tlp_malformed(tlp: bytes) -> bool:
    """Malformed at this layer: not whole DWs, shorter than a 3-DW header,
    a byte count other than the header's, or a payload above 128 bytes."""
    if len(tlp) % 4 or len(tlp) < 12:
        return True
    return len(tlp) != tlp_size(tlp) or tlp_payload_dw(tlp) > 32


def make_tlp(kind: str, payload_dw: int = 0, tag: int = 0) -> bytes:
    """A well-formed TLP for tests: ``kind`` one of MWr, MRd, CfgWr, Msg,
    MsgD, Cpl, CplD. ``tag`` (16 bits) goes in header bytes 4-5 and the
    first payload bytes so each TLP is recognisable."""
    fmt_type = {"MWr": 0x40, "MRd": 0x00, "CfgWr": 0x44, "Msg": 0x30,
                "MsgD": 0x70, "Cpl": 0x0A, "CplD": 0x4A}[kind]
    data = bool(fmt_type & 0x40)
    if kind == "CfgWr":
        payload_dw = 1
    if not data:
        payload_dw = 0
    elif not 1 <= payload_dw <= 32:
        raise ValueError("payload 1 to 32 DW (128 bytes)")
    length = payload_dw if data else (1 if kind == "MRd" else 0)
    hdr_dw = 4 if fmt_type & 0x20 else 3
    hdr = bytearray(4 * hdr_dw)
    hdr[0] = fmt_type
    hdr[2] = (length >> 8) & 0x03
    hdr[3] = length & 0xFF
    hdr[4], hdr[5] = (tag >> 8) & 0xFF, tag & 0xFF
    payload = bytes(((tag + i) * 37 + (tag >> 8)) & 0xFF for i in range(4 * payload_dw))
    return bytes(hdr) + payload


# ---------------------------------------------------------------------------
# DLLP and TLP framing (DLLP.md#fields, RETRY.md#tlp-framing)


def dllp_body(typ: int, a: int, b: int = 0) -> bytes:
    """The 4 body bytes. Ack/Nak: ``a`` is the 12-bit sequence number.
    FC DLLPs: ``a`` HdrFC (8 bits), ``b`` DataFC (12 bits)."""
    if typ in (ACK, NAK):
        return bytes([typ, 0, (a >> 8) & 0x0F, a & 0xFF])
    return bytes([typ, (a >> 2) & 0x3F, ((a & 0x03) << 6) | ((b >> 8) & 0x0F), b & 0xFF])


def make_dllp(typ: int, a: int, b: int = 0) -> bytes:
    body = dllp_body(typ, a, b)
    return body + crc16_bytes(body)


def frame_tlp(seq: int, tlp: bytes, nullify: bool = False) -> bytes:
    head = bytes([(seq >> 8) & 0x0F, seq & 0xFF]) + tlp
    crc = lcrc_bytes(head)
    if nullify:
        crc = bytes(x ^ 0xFF for x in crc)
    return head + crc


def to_beats(pkt: bytes, dllp: bool, nullify: bool = False) -> list[Beat]:
    n = len(pkt) // 2
    return [Beat(pkt[2 * i] | (pkt[2 * i + 1] << 8), i == 0, i == n - 1, dllp,
                 False, nullify and i == n - 1) for i in range(n)]


def _not_older(a: int, b: int, ref: int) -> bool:
    """Sequence ``a`` is ``b`` or newer, both at or before ``ref``."""
    return (ref - a) % SEQ_MOD <= (ref - b) % SEQ_MOD


# ---------------------------------------------------------------------------


@dataclass
class _Tx:
    """A packet on the partner's transmit stream."""
    beats: list[Beat]
    kind: str                       # "tlp", "replay", "dllp", "nullified"
    seq: int | None = None
    drop: bool = False
    pos: int = 0


@dataclass
class _Credit:
    """One class's credits: limit and consumed (transmit side), allocated
    and received (receive side), per field (0 header, 1 data)."""
    limit: list[int] = field(default_factory=lambda: [0, 0])
    infinite: list[bool] = field(default_factory=lambda: [False, False])
    recorded: bool = False
    init_value: tuple[int, int] | None = None
    consumed: list[int] = field(default_factory=lambda: [0, 0])
    allocated: list[int] = field(default_factory=lambda: [0, 0])
    received: list[int] = field(default_factory=lambda: [0, 0])
    sent_value: tuple[int, int] | None = None
    refresh: int = 0


# The HAS parameter defaults; 0 is infinite.
DEFAULT_CREDITS = {"P": (8, 32), "NP": (8, 8), "Cpl": (0, 0)}


class Partner:
    """The far-end data link layer; see the module docstring."""

    def __init__(self, name: str = "partner", *,
                 credits: dict[str, tuple[int, int]] | None = None,
                 timers: Timers | None = None, inject: Inject | None = None,
                 replay_bytes: int | None = 1024, seed: int = 0) -> None:
        self.name = name
        self.adv = dict(DEFAULT_CREDITS if credits is None else credits)
        for cls in CLASSES:
            h, d = self.adv[cls]
            if not (0 <= h < 2 ** (HDR_W - 1) and 0 <= d < 2 ** (DATA_W - 1)):
                raise ValueError(f"{cls} credits {self.adv[cls]} out of range")
        if self.adv["NP"][1] == 1:
            # 0 (infinite) or at least 2: a compare-and-swap AtomicOp with
            # 16-byte operands needs 2 NP data credits (#261).
            raise ValueError("NP data credits must be 0 or at least 2")
        self.t = timers or Timers()
        self.inject = inject or Inject()
        self.replay_bytes = replay_bytes
        self.rng = random.Random(seed)
        # Inputs.
        self.link_up = False
        self.tx_ready = True
        self.in_l0 = True               # the LTSSM is in L0
        self.upper_ready = True
        # Test hooks besides Inject.
        self.withhold_credits = False   # send no UpdateFC (credits pile up)
        self.ack_delay = 0              # extra cycles before a due Ack
        # Upper layer.
        self.tx_queue: list[bytes] = []
        self.received: list[bytes] = []
        # Outputs and records.
        self.retrain_req = False
        self.violations: list[str] = []
        self.stats: dict[str, int] = {}
        self.ack_wait_max = 0
        self.cycle = 0
        self._reset()

    # -- upper layer --------------------------------------------------------

    def send(self, tlp: bytes, check: bool = True) -> None:
        """Queue a TLP. ``check=False`` lets a malformed one through (its
        byte count disagrees with its header, it is not whole DWs or shorter
        than a 3-DW header, or its payload is above 128 bytes), to test the
        far end's malformed-TLP rule."""
        if len(tlp) < 4 or tlp_class(tlp) is None or (check and tlp_malformed(tlp)):
            raise ValueError("send takes well-formed TLPs only")
        self.tx_queue.append(bytes(tlp))

    def force_nak(self) -> None:
        """Make a Nak due now, whatever NAK_SCHEDULED says (in DL_Active)."""
        self.nak_due = True

    def avail(self, cls: str) -> tuple[int | None, int | None]:
        """Credits available to send (None: infinite)."""
        c = self.cr[cls]
        return tuple(None if c.infinite[f] else
                     (c.limit[f] - c.consumed[f]) % (2 ** (HDR_W, DATA_W)[f])
                     for f in (0, 1))

    # -- state --------------------------------------------------------------

    def _reset(self) -> None:
        lost = len(getattr(self, "rbuf", []))
        if lost:
            self._count("lost_on_reset", lost)
        self.state = INACTIVE
        self.next_tx_seq = 0
        self.ackd_seq = SEQ_MOD - 1
        self.replay_num = 0
        self.replay_timer: int | None = None
        self.rbuf: list[tuple[int, bytes, int]] = []    # (seq, framed, eop cycle)
        self.replay_queue: list[int] = []
        self.replay_pending = False
        self.rollover_hold: list[int] | None = None    # [left L0, cycles held]
        self.next_rcv_seq = 0
        self.nak_scheduled = False
        self.ack_timer: int | None = None
        self.ack_due = False
        self.nak_due = False
        self.cr = {cls: _Credit() for cls in CLASSES}
        for cls in CLASSES:
            self.cr[cls].allocated = list(self.adv[cls])
        self.store: list[tuple[bytes, str]] = []
        self.group: list[int] = []      # FC DLLP types still to send in this group
        self.group_timer = 0
        self.init_done = False
        self.uf_rr = 0
        self.cur: _Tx | None = None
        self.rx_pkt: list[Beat] = []

    def _count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    def _violate(self, msg: str) -> None:
        self.violations.append(f"{self.name} cycle {self.cycle}: {msg}")

    # -- one clock edge -----------------------------------------------------

    def clock(self, beat_in: Beat | None) -> Beat | None:
        self.cycle += 1
        self.retrain_req = False
        if not self.link_up:
            if self.state != INACTIVE or self.cur:
                self._reset()
            return None
        if self.state == INACTIVE:
            self._enter(FC_INIT1)
        if beat_in is not None:
            self._rx_beat(beat_in)
        self._upper_take()
        self._timers()
        return self._tx_beat()

    def _enter(self, state: str) -> None:
        self.state = state
        if state in (FC_INIT1, FC_INIT2):
            types = INITFC1 if state == FC_INIT1 else INITFC2
            self.group = [types[c] for c in CLASSES]
            self.group_timer = 0
            self.init_done = False
        elif state == ACTIVE:
            self.group = []
            for c in self.cr.values():
                c.sent_value = None     # UpdateFC due at once for finite classes
                c.refresh = 0

    # -- receive ------------------------------------------------------------

    def _rx_beat(self, b: Beat) -> None:
        if b.sop:
            if self.rx_pkt:
                self._violate("sop inside a packet")
            self.rx_pkt = []
        elif not self.rx_pkt:
            self._violate("beat outside a packet")
            return
        self.rx_pkt.append(b)
        if b.eop:
            beats, self.rx_pkt = self.rx_pkt, []
            if any(x.dllp != beats[0].dllp for x in beats):
                self._violate("dllp tag changes inside a packet")
            data = bytes(v for x in beats for v in (x.data & 0xFF, x.data >> 8))
            if beats[0].dllp:
                self._rx_dllp(data, b.err)
            else:
                self._rx_tlp(data, b.err, b.nullify)

    def _take_rx_inject(self, rate: float, nxt: str) -> bool:
        if getattr(self.inject, nxt) > 0:
            setattr(self.inject, nxt, getattr(self.inject, nxt) - 1)
            return True
        return rate > 0 and self.rng.random() < rate

    def _rx_dllp(self, data: bytes, err: bool) -> None:
        if self._take_rx_inject(self.inject.rx_dllp_drop, "rx_dllp_drop_next"):
            self._count("rx_dllp_dropped")
            return
        if err or len(data) != 6 or crc16_bytes(data[:4]) != data[4:]:
            self._count("bad_dllp")
            return
        typ = data[0]
        if typ in (ACK, NAK):
            if data[1] or data[2] & 0xF0:
                self._violate(f"reserved bits set in {data[:4].hex()}")
            if self.state == ACTIVE:
                self._rx_acknak(typ == NAK, ((data[2] & 0x0F) << 8) | data[3])
            return
        kind = _FC_KIND.get(typ & 0xF8)
        if kind is None:
            self._violate(f"DLLP type {typ:#04x} this design never sends")
            return
        if typ & 0x07:
            self._violate(f"FC DLLP for VC {typ & 7}")
            return
        if data[1] & 0xC0 or data[2] & 0x30:
            self._violate(f"reserved bits set in {data[:4].hex()}")
        hdr = ((data[1] & 0x3F) << 2) | (data[2] >> 6)
        dat = ((data[2] & 0x0F) << 8) | data[3]
        self._rx_fc(kind[0], kind[1], hdr, dat)

    def _rx_fc(self, kind: str, cls: str, hdr: int, dat: int) -> None:
        c = self.cr[cls]
        self._count(f"rx_{kind}")
        if kind.startswith("InitFC"):
            if c.init_value is not None and c.init_value != (hdr, dat) \
                    and self.state in (FC_INIT1, FC_INIT2):
                self._violate(f"{kind}-{cls} {(hdr, dat)} differs from {c.init_value}")
            if self.state == FC_INIT1 and not c.recorded:
                c.recorded = True
                c.init_value = (hdr, dat)
                c.limit = [hdr, dat]
                c.infinite = [hdr == 0, dat == 0]
            if self.state == FC_INIT2 and kind == "InitFC2":
                self.init_done = True
            return
        if self.state not in (FC_INIT2, ACTIVE):
            return
        if self.state == FC_INIT2:
            self.init_done = True
        for f, (val, w) in enumerate(((hdr, HDR_W), (dat, DATA_W))):
            if c.infinite[f]:
                if val:
                    self._violate(f"UpdateFC-{cls} field {f} is {val} for an infinite field")
                continue
            if (val - c.limit[f]) % 2 ** w > 2 ** (w - 1):
                self._violate(f"UpdateFC-{cls} field {f} moves the limit back "
                              f"from {c.limit[f]} to {val}")
                continue
            c.limit[f] = val

    def _rx_acknak(self, nak: bool, s: int) -> None:
        self._count("rx_nak" if nak else "rx_ack")
        newest = (self.next_tx_seq - 1) % SEQ_MOD
        if not _not_older(s, self.ackd_seq, newest):
            self._violate(f"{'Nak' if nak else 'Ack'} {s} outside "
                          f"[{self.ackd_seq}, {newest}] (DL protocol error)")
            return
        if s != self.ackd_seq:
            keep = []
            for seq, framed, eop in self.rbuf:
                if _not_older(s, seq, newest):
                    if eop is not None:
                        self.ack_wait_max = max(self.ack_wait_max, self.cycle - eop)
                else:
                    keep.append((seq, framed, eop))
            self.rbuf = keep
            self.ackd_seq = s
            self.replay_num = 0
            self.replay_timer = 0 if self.rbuf and self._rbuf_sent() else None
        if nak:
            self.replay_pending = True

    def _rbuf_sent(self) -> bool:
        return any(eop is not None for _, _, eop in self.rbuf)

    def _rx_tlp(self, data: bytes, err: bool, nullify: bool) -> None:
        if self.state not in (FC_INIT2, ACTIVE):
            self._count("tlp_before_init")
            return
        if self._take_rx_inject(self.inject.rx_tlp_drop, "rx_tlp_drop_next"):
            self._count("rx_tlp_dropped")
            return
        # Receive order of RETRY.md#receiving-tlps: flagged, too short, EDB,
        # LCRC; a bad length with a good LCRC is malformed, not bad.
        if err:
            return self._bad_tlp("flagged")
        if len(data) < 6:
            return self._bad_tlp(f"length {len(data)}")
        crc = lcrc_bytes(data[:-4])
        if nullify:
            if data[-4:] == bytes(x ^ 0xFF for x in crc):
                self._count("rx_nullified")
                return
            return self._bad_tlp("EDB without inverted LCRC")
        if data[-4:] != crc:
            return self._bad_tlp("LCRC")
        if self.state == FC_INIT2:
            self.init_done = True
        if data[0] & 0xF0:
            self._violate(f"reserved sequence bits {data[0] >> 4:#x}")
        seq = ((data[0] & 0x0F) << 8) | data[1]
        tlp = data[2:-4]
        d = (seq - self.next_rcv_seq) % SEQ_MOD
        if d == 0:
            if tlp_malformed(tlp):
                # Accepted at this layer (acknowledged), not delivered; the
                # credits its header claims are returned at once. With no
                # whole first DW there is no header to read: nothing returns.
                self._violate(f"malformed TLP {tlp[:4].hex()} length {len(tlp)}")
                self._count("rx_malformed")
                cls = tlp_class(tlp) if len(tlp) >= 4 else None
                if cls is not None:
                    need, c = tlp_credits(tlp), self.cr[cls]
                    for f, w in ((0, HDR_W), (1, DATA_W)):
                        if self.adv[cls][f]:
                            c.received[f] = (c.received[f] + need[f]) % 2 ** w
                            c.allocated[f] = (c.allocated[f] + need[f]) % 2 ** w
                self._accept()
                return
            cls = tlp_class(tlp) or "NP"
            if self._take_rx_inject(self.inject.rx_tlp_nak, "rx_tlp_nak_next"):
                self._count("rx_tlp_naked")
                return self._bad_tlp("injected Nak")
            if not self._covered(cls, tlp):
                self._violate(f"receiver overflow: {cls} TLP seq {seq} exceeds advertised credits")
                self._count("overflow")
                return
            need = tlp_credits(tlp)
            c = self.cr[cls]
            for f, w in ((0, HDR_W), (1, DATA_W)):
                c.received[f] = (c.received[f] + need[f]) % 2 ** w
            self.store.append((tlp, cls))
            self._accept()
        elif d < 2048:
            self._bad_tlp(f"sequence {seq} ahead of {self.next_rcv_seq}")
        else:
            self._count("rx_duplicate")
            self.ack_due = True

    def _accept(self) -> None:
        self.next_rcv_seq = (self.next_rcv_seq + 1) % SEQ_MOD
        self.nak_scheduled = False
        if self.ack_timer is None:
            self.ack_timer = -1         # counts from the next cycle

    def _covered(self, cls: str, tlp: bytes) -> bool:
        need = tlp_credits(tlp)
        adv = self.adv[cls]
        c = self.cr[cls]
        for f, w in ((0, HDR_W), (1, DATA_W)):
            if adv[f] and need[f] > (c.allocated[f] - c.received[f]) % 2 ** w:
                return False
        return True

    def _bad_tlp(self, why: str) -> None:
        self._count("bad_tlp")
        if not self.nak_scheduled:
            self.nak_scheduled = True
            self.nak_due = True

    def _upper_take(self) -> None:
        if not self.upper_ready or not self.store:
            return
        tlp, cls = self.store.pop(0)
        self.received.append(tlp)
        need = tlp_credits(tlp)
        c = self.cr[cls]
        for f, w in ((0, HDR_W), (1, DATA_W)):
            if self.adv[cls][f]:
                c.allocated[f] = (c.allocated[f] + need[f]) % 2 ** w

    # -- timers -------------------------------------------------------------

    def _timers(self) -> None:
        if self.state in (FC_INIT1, FC_INIT2):
            self.group_timer += 1
            if self.group_timer >= self.t.initfc_resend and not self.group:
                types = INITFC1 if self.state == FC_INIT1 else INITFC2
                self.group = [types[c] for c in CLASSES]
                self.group_timer = 0
        if self.state == ACTIVE:
            for c in self.cr.values():
                c.refresh += 1
        if self.rollover_hold is not None:
            h = self.rollover_hold
            h[1] += 1
            if not self.in_l0:
                h[0] = 1
            elif h[0] or h[1] >= self.t.rollover_fallback:     # no bound once left
                self._count("rollover_fallback" if not h[0] else "rollover_retrained")
                self.rollover_hold = None
        if self.replay_timer is not None and self.in_l0:
            self.replay_timer += 1
            if self.replay_timer >= self.t.replay_timeout:
                self._count("replay_timeout")
                self.replay_timer = None
                self.replay_pending = True
        if self.ack_timer is not None:
            self.ack_timer += 1
            if self.ack_timer >= self.t.ack_latency + self.ack_delay:
                self.ack_due = True
        if self.replay_pending and not self._replaying():
            self._start_replay()

    def _replaying(self) -> bool:
        return bool(self.replay_queue) or (self.cur is not None and self.cur.kind == "replay")

    def _start_replay(self) -> None:
        self.replay_pending = False
        sent = [seq for seq, _, eop in self.rbuf if eop is not None]
        if not sent:
            return
        self.replay_num += 1
        self._count("replay")
        if self.replay_num == 4:
            self.replay_num = 0
            self.retrain_req = True
            self.rollover_hold = [0, 0]
            self._count("rollover")
        self.replay_timer = None
        self.replay_queue = sent

    # -- transmit -----------------------------------------------------------

    def _tx_beat(self) -> Beat | None:
        if not self.tx_ready:
            return None
        if self.cur is None:
            self.cur = self._pick()
            if self.cur is None:
                return None
        cur = self.cur
        beat = cur.beats[cur.pos]
        cur.pos += 1
        if cur.pos == len(cur.beats):
            self.cur = None
            self._tx_done(cur)
        return None if cur.drop else beat

    def _tx_done(self, cur: _Tx) -> None:
        if cur.kind in ("tlp", "replay"):
            for i, (seq, framed, eop) in enumerate(self.rbuf):
                if seq == cur.seq:
                    self.rbuf[i] = (seq, framed, self.cycle)
            if self.replay_timer is None and self.rbuf:
                self.replay_timer = 0
        if self.state in (FC_INIT1, FC_INIT2) and not self.group:
            self._init_exit()

    def _init_exit(self) -> None:
        if self.state == FC_INIT1 and all(c.recorded for c in self.cr.values()):
            self._enter(FC_INIT2)
        elif self.state == FC_INIT2 and self.init_done:
            self._enter(ACTIVE)

    def _pick(self) -> _Tx | None:
        if self.state in (FC_INIT1, FC_INIT2) and not self.group:
            self._init_exit()
        if self.state == ACTIVE and self.nak_due:
            return self._acknak(NAK)
        if self.state == ACTIVE and self.ack_due:
            return self._acknak(ACK)
        if self.group:
            typ = self.group.pop(0)
            cls = _FC_KIND[typ][1]
            return self._dllp(make_dllp(typ, *self.adv[cls]))
        if self.state != ACTIVE:
            return None
        uf = self._updatefc()
        if uf:
            return uf
        if self.rollover_hold is not None:
            return None                 # no TLP until the retrain is done
        while self.replay_queue:
            seq = self.replay_queue.pop(0)
            for s, framed, _ in self.rbuf:
                if s == seq:
                    self._count("tlp_replayed")
                    return self._tlp_tx(framed, seq, "replay")
        return self._new_tlp()

    def _acknak(self, typ: int) -> _Tx:
        self.ack_due = self.nak_due = False
        self.ack_timer = None
        self._count("tx_nak" if typ == NAK else "tx_ack")
        return self._dllp(make_dllp(typ, (self.next_rcv_seq - 1) % SEQ_MOD))

    def _dllp(self, pkt: bytes) -> _Tx:
        i = self.inject
        drop = self._take_tx(i.tx_dllp_drop, "tx_dllp_drop_next")
        if not drop and self._take_tx(i.tx_dllp_corrupt, "tx_dllp_corrupt_next"):
            pkt = _flip(pkt, self.rng)
            self._count("tx_dllp_corrupted")
        if drop:
            self._count("tx_dllp_dropped")
        return _Tx(to_beats(pkt, True), "dllp", drop=drop)

    def _take_tx(self, rate: float, nxt: str) -> bool:
        if getattr(self.inject, nxt) > 0:
            setattr(self.inject, nxt, getattr(self.inject, nxt) - 1)
            return True
        return rate > 0 and self.rng.random() < rate

    def _updatefc(self) -> _Tx | None:
        if self.withhold_credits:
            return None
        for k in range(3):
            cls = CLASSES[(self.uf_rr + k) % 3]
            c = self.cr[cls]
            adv = self.adv[cls]
            value = (c.allocated[0] % 256 if adv[0] else 0,
                     c.allocated[1] % 4096 if adv[1] else 0)
            if value != c.sent_value or c.refresh >= self.t.updatefc_refresh:
                self.uf_rr = (self.uf_rr + k + 1) % 3
                c.sent_value = value
                c.refresh = 0
                self._count("tx_updatefc")
                return self._dllp(make_dllp(UPDATEFC[cls], *value))
        return None

    def _new_tlp(self) -> _Tx | None:
        if not self.tx_queue:
            return None
        tlp = self.tx_queue[0]
        cls = tlp_class(tlp)
        need = tlp_credits(tlp)
        c = self.cr[cls]
        for f, w in ((0, HDR_W), (1, DATA_W)):
            if not c.infinite[f] and \
                    (c.limit[f] - (c.consumed[f] + need[f])) % 2 ** w > 2 ** (w - 1):
                return None
        if (self.next_tx_seq - self.ackd_seq - 1) % SEQ_MOD >= 2047:
            return None
        framed = frame_tlp(self.next_tx_seq, tlp)
        if self.replay_bytes is not None and \
                sum(len(f) for _, f, _ in self.rbuf) + len(framed) > self.replay_bytes:
            return None
        if self.inject.tx_tlp_nullify and self.rng.random() < self.inject.tx_tlp_nullify:
            # A nullified copy goes first; the TLP itself stays queued and
            # takes the same sequence number next time.
            self._count("tx_nullified")
            return _Tx(to_beats(frame_tlp(self.next_tx_seq, tlp, nullify=True), False,
                                nullify=True), "nullified")
        self.tx_queue.pop(0)
        for f, w in ((0, HDR_W), (1, DATA_W)):
            c.consumed[f] = (c.consumed[f] + need[f]) % 2 ** w
        seq = self.next_tx_seq
        self.next_tx_seq = (seq + 1) % SEQ_MOD
        self.rbuf.append((seq, framed, None))
        self._count("tlp_sent")
        return self._tlp_tx(framed, seq, "tlp")

    def _tlp_tx(self, framed: bytes, seq: int, kind: str) -> _Tx:
        i = self.inject
        drop = self._take_tx(i.tx_tlp_drop, "tx_tlp_drop_next")
        if drop:
            self._count("tx_tlp_dropped")
        elif self._take_tx(i.tx_tlp_corrupt, "tx_tlp_corrupt_next"):
            framed = _flip(framed, self.rng)
            self._count("tx_tlp_corrupted")
        return _Tx(to_beats(framed, False), kind, seq=seq, drop=drop)


def _flip(pkt: bytes, rng: random.Random) -> bytes:
    """``pkt`` with one random bit inverted (any CRC detects it)."""
    i = rng.randrange(8 * len(pkt))
    out = bytearray(pkt)
    out[i // 8] ^= 1 << (i % 8)
    return bytes(out)


# ---------------------------------------------------------------------------
# A line between two packet-stream ends, for partner-against-partner tests.


class Line:
    """A fixed delay of ``delay`` cycles in one direction, carrying beats
    unchanged (store nothing, reorder nothing)."""

    def __init__(self, delay: int = 22) -> None:
        self.pipe: list[Beat | None] = [None] * delay

    def push(self, beat: Beat | None) -> Beat | None:
        if not self.pipe:
            return beat
        self.pipe.append(beat)
        return self.pipe.pop(0)


class Pair:
    """Two partners, a and b, joined by a ``Line`` each way."""

    def __init__(self, a: Partner, b: Partner, delay: int = 22) -> None:
        self.a, self.b = a, b
        self.ab, self.ba = Line(delay), Line(delay)
        self.to_a: Beat | None = None
        self.to_b: Beat | None = None

    def step(self) -> None:
        out_a = self.a.clock(self.to_a)
        out_b = self.b.clock(self.to_b)
        self.to_b = self.ab.push(out_a)
        self.to_a = self.ba.push(out_b)

    def run(self, cycles: int) -> None:
        for _ in range(cycles):
            self.step()
