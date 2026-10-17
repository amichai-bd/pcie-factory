"""Architecture model of pcie_dll, the data link layer
(wiki/src/arch/dll/HAS.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). It executes the HAS rules at packet level, one step
per clk_i cycle:

- DLLP formats and CRC-16, TLP sequence numbers and LCRC
  (``pcie_dll_crc``);
- the data link control states and flow-control initialisation;
- the transmit side: sequence numbers, the replay buffer, the replay timer,
  REPLAY_NUM and its rollover to a retrain request, Ack and Nak handling;
- the receive side: LCRC and sequence checks, duplicates and lost TLPs,
  Ack and Nak scheduling, the receive buffer and its overflow check;
- credit gating, credit accounting and UpdateFC scheduling.

``Pair`` joins two ``Dll`` models through a ``Channel`` that stands in for
two ``phy`` stacks and the line: a fixed latency, one framing cycle per
packet, injected errors and a retrain hold. ``perf`` measures the HAS
targets. Python standard library only.
"""

from __future__ import annotations

import random
from collections import deque

from pcie_dll_crc import crc16, invert, lcrc

# ---------------------------------------------------------------- constants

# Spec facts are cited where they are stated; the rest are this design's
# choices from the HAS.
SEQ_MOD = 4096              # 12-bit sequence numbers [cite: PCIe Base Spec, rev 3.0, §3.5.2.1]
SEQ_HALF = 2048             # at most 2047 TLPs unacknowledged [cite: PCIe Base Spec, rev 3.0, §3.5.2.1]
HDR_MOD = 256               # 8-bit header credit fields [cite: PCIe Base Spec, rev 3.0, §2.6.1]
DATA_MOD = 4096             # 12-bit data credit fields [cite: PCIe Base Spec, rev 3.0, §2.6.1]
MODS = (HDR_MOD, DATA_MOD)
CREDIT_BYTES = 16           # one data credit [cite: PCIe Base Spec, rev 3.0, §2.6.1]
MAX_PAYLOAD = 128           # HAS Parameters (decision Q6)
MAX_TLP = 16 + MAX_PAYLOAD  # 4-DW header, no ECRC
SEQ_BYTES = 2
LCRC_BYTES = 4
DLLP_BYTES = 6
REPLAY_BUF_BYTES = 1024     # HAS: replay buffer
REPLAY_TIMEOUT = 356        # cycles: 711 symbol times, rounded up [cite: PCIe Base Spec, rev 3.0, §3.5.2.1]
ACK_LATENCY = 32            # cycles: HAS Ack scheduling
ACK_LIMIT = 118             # cycles: 237 symbol times [cite: PCIe Base Spec, rev 3.0, §3.5.3.1]
FC_INIT_PERIOD = 128        # cycles between InitFC groups; at most 34 us [cite: PCIe Base Spec, rev 3.0, §3.3.1]
UPDATEFC_PERIOD = 2048      # cycles; at most 30 us [cite: PCIe Base Spec, rev 3.0, §2.6.1.2]
INF_RESERVE = (8, 24)       # receive space of an infinite class (HAS, receive buffer)
TX_STORE = 1                # cycles: TLP eop accepted to eligible for transmission
RX_CHECK = 2                # cycles: TLP eop from phy to the upper sop
RECOVERY_LTSSM = range(0x10, 0x15)  # Recovery codes of ltssm_state (mac HAS)

# Classes and the advertised-credit parameter names per class.
P, NP, CPL = 0, 1, 2
CLASSES = ("p", "np", "cpl")
DEFAULT_FC = {"ph": 8, "pd": 32, "nph": 8, "npd": 8, "cplh": 8, "cpld": 32}

# DLLP type byte [cite: PCIe Base Spec, rev 3.0, §3.4.1].
ACK, NAK = 0x00, 0x10
INITFC1 = (0x40, 0x50, 0x60)   # P, NP, Cpl; bits [2:0] carry the VC
INITFC2 = (0xC0, 0xD0, 0xE0)
UPDATEFC = (0x80, 0x90, 0xA0)

# Data link control states (HAS, dl_state_o).
INACTIVE, INIT1, INIT2, ACTIVE = 0, 1, 2, 3

# Error pulse bits (HAS, err_o).
E_BAD_TLP, E_BAD_DLLP, E_TIMEOUT, E_ROLLOVER, E_PROTOCOL, E_OVERFLOW = range(6)
ERR_NAMES = ("bad_tlp", "bad_dllp", "replay_timeout", "replay_rollover",
             "dl_protocol", "rx_overflow")


# --------------------------------------------------------------- formats

def ack_dllp(kind: int, seq: int) -> bytes:
    """An Ack or Nak DLLP for ``seq``, with its CRC."""
    body = bytes([kind, 0, (seq >> 8) & 0x0F, seq & 0xFF])
    return body + crc16(body)


def fc_dllp(kinds: tuple, cls: int, hdr: int, data: int, vc: int = 0) -> bytes:
    """An InitFC1, InitFC2 or UpdateFC DLLP (``kinds`` names which)."""
    body = bytes([kinds[cls] | vc, (hdr >> 2) & 0x3F,
                  ((hdr & 0x3) << 6) | ((data >> 8) & 0x0F), data & 0xFF])
    return body + crc16(body)


def decode_dllp(d: bytes) -> tuple:
    """(kind, cls, vc, a, b): kind 'ack', 'nak', 'init1', 'init2',
    'update' or 'other'; for Ack/Nak ``a`` is the sequence number, for the
    FC kinds ``a`` and ``b`` are HdrFC and DataFC."""
    t = d[0]
    if t in (ACK, NAK):
        return ("ack" if t == ACK else "nak", None, None, ((d[2] & 0x0F) << 8) | d[3], None)
    hi, vc = t & 0xF8, t & 0x07
    hdr = ((d[1] & 0x3F) << 2) | (d[2] >> 6)
    data = ((d[2] & 0x0F) << 8) | d[3]
    for kind, kinds in (("init1", INITFC1), ("init2", INITFC2), ("update", UPDATEFC)):
        if hi in kinds:
            return (kind, kinds.index(hi), vc, hdr, data)
    return ("other", None, None, None, None)


def tlp_info(tlp: bytes) -> tuple:
    """(class, header credits, data credits, byte length) from header DW0
    [cite: PCIe Base Spec, rev 3.0, §2.2.1]."""
    fmt, typ = tlp[0] >> 5, tlp[0] & 0x1F
    has_data, hdr4 = bool(fmt & 0x2), bool(fmt & 0x1)
    length = ((tlp[2] & 0x3) << 8) | tlp[3] or 1024
    if (typ == 0 and has_data) or (typ & 0x18) == 0x10:
        cls = P                        # memory write, message
    elif typ in (0x0A, 0x0B):
        cls = CPL                      # completion, locked completion
    else:
        cls = NP
    data = -(-length // 4) if has_data else 0
    return cls, 1, data, (16 if hdr4 else 12) + (4 * length if has_data else 0)


def make_tlp(kind: str, payload: int, ident: int) -> bytes:
    """A TLP for the model's traffic: ``kind`` 'mwr' (posted), 'mrd'
    (non-posted) or 'cpld' (completion), ``payload`` bytes (a multiple of
    4), and ``ident`` in DW2 so each TLP is unique."""
    if payload % 4 or not 0 <= payload <= MAX_PAYLOAD:
        raise ValueError(f"payload {payload}")
    dw = payload // 4
    if kind == "mrd":
        b0, length, body = 0x00, max(dw, 1), b""
    elif kind == "mwr":
        b0, length = 0x40, max(dw, 1)
        body = bytes((ident + i) & 0xFF for i in range(4 * length))
    elif kind == "cpld":
        b0, length = 0x4A, max(dw, 1)
        body = bytes((ident ^ i) & 0xFF for i in range(4 * length))
    else:
        raise ValueError(kind)
    hdr = bytes([b0, 0, (length >> 8) & 0x3, length & 0xFF, 0, 1, 0, 0x0F])
    return hdr + ident.to_bytes(4, "big") + body


def tlp_frame(seq: int, tlp: bytes) -> bytes:
    """Sequence number, TLP and LCRC: the packet handed to phy."""
    head = bytes([(seq >> 8) & 0x0F, seq & 0xFF]) + tlp
    return head + lcrc(head)


class Packet:
    """One packet on the phy packet stream."""

    __slots__ = ("dllp", "data", "err", "nullify")

    def __init__(self, dllp: bool, data: bytes, err: bool = False, nullify: bool = False):
        self.dllp, self.data, self.err, self.nullify = dllp, data, err, nullify

    def beats(self) -> int:
        return (len(self.data) + 1) // 2


# ------------------------------------------------------------------ the DLL

class Dll:
    """One pcie_dll at packet level. ``fc`` maps the six credit parameters
    (0 is infinite); ``ack_latency`` lets a scenario model a slower partner."""

    def __init__(self, name: str, fc: dict | None = None, ack_latency: int = ACK_LATENCY):
        fc = dict(DEFAULT_FC, **(fc or {}))
        if set(fc) != set(DEFAULT_FC):
            raise ValueError(f"credit parameters {sorted(fc)}")
        self.name = name
        self.adv = [(fc[c + "h"], fc[c + "d"]) for c in CLASSES]
        for c, (h, d) in enumerate(self.adv):
            if not (h == 0 or 1 <= h <= 127) or not (d == 0 or 1 <= d <= 2047):
                raise ValueError(f"{CLASSES[c]} credits {h}, {d}")
        if self.adv[P][1] and self.adv[P][1] < MAX_PAYLOAD // CREDIT_BYTES:
            raise ValueError("pd below one maximum payload")
        if self.adv[CPL][1] and self.adv[CPL][1] < MAX_PAYLOAD // CREDIT_BYTES:
            raise ValueError("cpld below one maximum payload")
        self.ack_latency = ack_latency
        self.errors = [0] * len(ERR_NAMES)
        self.counts = {"replays": 0, "retrains": 0, "acks": 0, "naks": 0, "updatefc": 0,
                       "duplicates": 0, "tx_tlps": 0, "credit_blocked": 0}
        self.delivered: list[bytes] = []
        self.ack_lat: list[int] = []
        self.dl_up_at: int | None = None
        self.link_up = False
        self.reset()

    # -- DL_Inactive: every piece of state back to its reset value
    def reset(self) -> None:
        self.state = INACTIVE
        # transmit: sequence numbers, replay buffer, replay timer
        self.seq_alloc = 0                 # next sequence number to assign
        self.nts = 0                       # NEXT_TRANSMIT_SEQ: after the newest TLP sent
        self.ackd = SEQ_MOD - 1            # ACKD_SEQ
        self.rbuf: deque = deque()         # [seq, packet bytes, eligible time]
        self.send_idx = 0                  # next replay-buffer entry to send
        self.replay_end = 0                # entries a replay resends
        self.replay_active = False
        self.replay_pending = False
        self.replay_num = 0
        self.rt_running, self.rt_count = False, 0
        self.retrain_req = False
        # receive: sequence numbers, Ack and Nak scheduling
        self.nrs = 0                       # NEXT_RCV_SEQ
        self.nak_scheduled = False
        self.nak_due = False
        self.ack_due = False
        self.ack_since: int | None = None
        self.unacked: list[int] = []       # accept times not yet covered by an Ack
        # flow control, transmit side
        self.cl = [[0, 0] for _ in CLASSES]          # CREDIT_LIMIT
        self.inf = [[False, False] for _ in CLASSES]
        self.cc = [[0, 0] for _ in CLASSES]          # CREDITS_CONSUMED
        self.fi1 = [False] * 3
        self.fi2 = False
        # flow control, receive side
        self.ca = [list(a) for a in self.adv]        # CREDITS_ALLOCATED
        self.cr = [[0, 0] for _ in CLASSES]          # CREDITS_RECEIVED
        self.occ = [[0, 0] for _ in CLASSES]         # infinite classes: space in use
        self.last_adv = [list(a) for a in self.adv]
        self.upd_time = [0, 0, 0]
        self.upd_rr = 0
        self.rxq: deque = deque()          # [eligible time, tlp, (cls, h, d), beats left]
        self.init_q: deque = deque()
        self.init_next = 0
        # datapaths
        self.tx_busy = 0
        self.tx_cur: Packet | None = None
        self.tx_cur_tlp = False
        self.src_busy = 0

    # ------------------------------------------------------------ control
    def set_link_up(self, up: bool, now: int) -> None:
        if not up and self.state != INACTIVE:
            self.reset()
        elif up and self.state == INACTIVE:
            self.state, self.init_next = INIT1, now
        self.link_up = up

    @property
    def dl_up(self) -> bool:
        return self.state in (INIT2, ACTIVE)

    def err(self, bit: int) -> None:
        self.errors[bit] += 1

    # ------------------------------------------------- transmit credits
    def credits_ok(self, cls: int, need: tuple) -> bool:
        for f in (0, 1):
            if self.inf[cls][f]:
                continue
            mod = MODS[f]
            if (self.cl[cls][f] - (self.cc[cls][f] + need[f])) % mod > mod // 2:
                return False
        return True

    def avail(self, cls: int, f: int) -> int:
        """The credit-available output: all ones for infinite, 0 unless
        DL_Active."""
        if self.state != ACTIVE:
            return 0
        if self.inf[cls][f]:
            return MODS[f] - 1
        return (self.cl[cls][f] - self.cc[cls][f]) % MODS[f]

    def rbuf_bytes(self) -> int:
        return sum(len(e[1]) for e in self.rbuf)

    def offer(self, tlp: bytes, now: int) -> bool:
        """The upper layer offers a TLP's sop beat; True when accepted."""
        if self.state != ACTIVE or self.src_busy:
            return False
        cls, h, d, n = tlp_info(tlp)
        if n != len(tlp):
            raise ValueError("TLP length does not match its header")
        if not self.credits_ok(cls, (h, d)):
            self.counts["credit_blocked"] += 1
            return False
        if self.rbuf_bytes() + n + SEQ_BYTES + LCRC_BYTES > REPLAY_BUF_BYTES:
            return False
        if (self.seq_alloc - self.ackd - 1) % SEQ_MOD >= SEQ_HALF - 1:
            return False
        for f, v in ((0, h), (1, d)):
            self.cc[cls][f] = (self.cc[cls][f] + v) % MODS[f]
        beats = -(-n // 4)
        self.rbuf.append([self.seq_alloc, tlp_frame(self.seq_alloc, tlp), now + beats + TX_STORE])
        self.seq_alloc = (self.seq_alloc + 1) % SEQ_MOD
        self.src_busy = beats
        return True

    # ------------------------------------------------------------ receive
    def receive(self, pkt: Packet, now: int) -> None:
        if self.state == INACTIVE:
            return
        if pkt.dllp:
            self._rx_dllp(pkt)
        else:
            self._rx_tlp(pkt, now)

    def _schedule_nak(self) -> None:
        if not self.nak_scheduled:
            self.nak_scheduled = self.nak_due = True

    def _rx_tlp(self, pkt: Packet, now: int) -> None:
        if self.state not in (INIT2, ACTIVE):
            return
        d = pkt.data
        if pkt.err:
            self.err(E_BAD_TLP)
            self._schedule_nak()
            return
        ok_len = len(d) >= SEQ_BYTES + 12 + LCRC_BYTES and len(d) % 4 == 2
        if pkt.nullify:
            if ok_len and d[-4:] == invert(lcrc(d[:-4])):
                return                                  # nullified: silent discard
            self.err(E_BAD_TLP)
            self._schedule_nak()
            return
        if not ok_len or d[-4:] != lcrc(d[:-4]):
            self.err(E_BAD_TLP)
            self._schedule_nak()
            return
        if self.state == INIT2:
            self.fi2 = True
        seq = ((d[0] & 0x0F) << 8) | d[1]
        ahead = (seq - self.nrs) % SEQ_MOD
        if ahead == 0:
            tlp = d[SEQ_BYTES:-LCRC_BYTES]
            cls, h, dc, n = tlp_info(tlp)
            if not self._rx_space(cls, (h, dc)):
                self.err(E_OVERFLOW)
                return                                  # no Ack: the partner replays
            for f, v in ((0, h), (1, dc)):
                if self.adv[cls][f]:
                    self.cr[cls][f] = (self.cr[cls][f] + v) % MODS[f]
                else:
                    self.occ[cls][f] += v
            self.rxq.append([now + RX_CHECK, tlp, (cls, h, dc), -(-len(tlp) // 4)])
            self.nrs = (self.nrs + 1) % SEQ_MOD
            self.nak_scheduled = False
            self.unacked.append(now)
            if self.ack_since is None:
                self.ack_since = now
        elif ahead < SEQ_HALF:
            self.err(E_BAD_TLP)                         # a TLP was lost
            self._schedule_nak()
        else:
            self.counts["duplicates"] += 1
            self.ack_due = True

    def _rx_space(self, cls: int, need: tuple) -> bool:
        for f in (0, 1):
            if self.adv[cls][f]:
                if (self.ca[cls][f] - self.cr[cls][f]) % MODS[f] < need[f]:
                    return False
            elif self.occ[cls][f] + need[f] > INF_RESERVE[f]:
                return False
        return True

    def _rx_dllp(self, pkt: Packet) -> None:
        d = pkt.data
        if pkt.err or len(d) != DLLP_BYTES or d[4:] != crc16(d[:4]):
            self.err(E_BAD_DLLP)
            return
        kind, cls, vc, a, b = decode_dllp(d)
        if kind in ("ack", "nak"):
            if self.state == ACTIVE:
                self._rx_acknak(kind == "nak", a)
        elif kind in ("init1", "init2") and vc == 0:
            if self.state == INIT1 and not self.fi1[cls]:
                self.fi1[cls] = True
                for f, v in ((0, a), (1, b)):
                    self.cl[cls][f], self.inf[cls][f] = v, v == 0
            elif self.state == INIT2 and kind == "init2":
                self.fi2 = True
        elif kind == "update" and vc == 0:
            if self.state in (INIT2, ACTIVE):
                if self.state == INIT2:
                    self.fi2 = True
                for f, v in ((0, a), (1, b)):
                    if not self.inf[cls][f]:
                        self.cl[cls][f] = v

    def _rx_acknak(self, nak: bool, seq: int) -> None:
        last = (self.nts - 1) % SEQ_MOD
        if (last - seq) % SEQ_MOD > (last - self.ackd) % SEQ_MOD:
            self.err(E_PROTOCOL)
            return
        n = (seq - self.ackd) % SEQ_MOD
        if n:
            for _ in range(n):
                self.rbuf.popleft()
            self.send_idx = max(0, self.send_idx - n)
            self.replay_end = max(0, self.replay_end - n)
            self.ackd = seq
            self.replay_num = 0
            if self.send_idx:
                self.rt_count = 0
            else:
                self.rt_running, self.rt_count = False, 0
            if self.replay_active and self.send_idx >= self.replay_end:
                self._replay_done()
        if nak:
            self._start_replay()

    # ------------------------------------------------------------- replay
    def _start_replay(self) -> None:
        if self.replay_active:
            self.replay_pending = True
            return
        if self.send_idx == 0:
            return                      # nothing sent is unacknowledged
        self.counts["replays"] += 1
        if self.replay_num == 3:
            self.replay_num = 0
            self.err(E_ROLLOVER)
            self.retrain_req = True
            self.counts["retrains"] += 1
        else:
            self.replay_num += 1
        self.replay_active = True
        self.replay_end = self.send_idx
        self.send_idx = 0
        self.rt_running, self.rt_count = False, 0

    def _replay_done(self) -> None:
        self.replay_active = False
        if self.replay_pending:
            self.replay_pending = False
            self._start_replay()

    # ------------------------------------------------------- one clock
    def tick(self, now: int, ltssm_recovery: bool, can_send: bool, sink_ready: bool) -> Packet | None:
        """Timers, the upper receive stream and the transmit arbiter for one
        cycle. Returns the packet that starts on the phy stream, if any."""
        self.retrain_req = False
        # replay timer, held while the LTSSM is in Recovery
        if self.rt_running and not ltssm_recovery:
            self.rt_count += 1
            if self.rt_count >= REPLAY_TIMEOUT:
                self.err(E_TIMEOUT)
                self.rt_running, self.rt_count = False, 0
                self._start_replay()
        # upper receive stream, one 32-bit beat per cycle
        if self.rxq and self.rxq[0][0] <= now and sink_ready:
            head = self.rxq[0]
            head[3] -= 1
            if head[3] == 0:
                self.rxq.popleft()
                self.delivered.append(head[1])
                cls, h, d = head[2]
                for f, v in ((0, h), (1, d)):
                    if self.adv[cls][f]:
                        self.ca[cls][f] = (self.ca[cls][f] + v) % MODS[f]
                    else:
                        self.occ[cls][f] -= v
        if self.src_busy:
            self.src_busy -= 1
        # transmit
        if self.tx_busy:
            self.tx_busy -= 1
            if self.tx_busy == 0 and self.tx_cur_tlp and not self.rt_running:
                self.rt_running, self.rt_count = True, 0
        if self.tx_busy or not can_send or self.state == INACTIVE:
            return None
        pkt = self._pick(now)
        if pkt is not None:
            self.tx_cur, self.tx_busy = pkt, pkt.beats() + 1  # + framing cycle
        return pkt

    def _pick(self, now: int) -> Packet | None:
        self.tx_cur_tlp = False
        if self.state in (INIT1, INIT2):
            if not self.init_q:
                if self.state == INIT1 and all(self.fi1):
                    self.state, self.init_next = INIT2, now
                elif self.state == INIT2 and self.fi2:
                    self._enter_active(now)
            if self.state != ACTIVE:
                if not self.init_q and now >= self.init_next:
                    kinds = INITFC1 if self.state == INIT1 else INITFC2
                    for c in range(3):
                        h, d = self.adv[c]
                        self.init_q.append(fc_dllp(kinds, c, h, d))
                    self.init_next = now + FC_INIT_PERIOD
                return Packet(True, self.init_q.popleft()) if self.init_q else None
        last_rcv = (self.nrs - 1) % SEQ_MOD
        if self.nak_due or self.ack_due or (
                self.ack_since is not None and now - self.ack_since >= self.ack_latency):
            kind = NAK if self.nak_due else ACK
            self.counts["naks" if kind == NAK else "acks"] += 1
            self.nak_due = self.ack_due = False
            self.ack_since = None
            self.ack_lat += [now - t for t in self.unacked]
            self.unacked = []
            return Packet(True, ack_dllp(kind, last_rcv))
        for k in range(3):
            c = (self.upd_rr + k) % 3
            if self.adv[c] == (0, 0):
                continue
            if self.ca[c] != self.last_adv[c] or now - self.upd_time[c] >= UPDATEFC_PERIOD:
                self.upd_rr = (c + 1) % 3
                self.last_adv[c] = list(self.ca[c])
                self.upd_time[c] = now
                self.counts["updatefc"] += 1
                h = self.ca[c][0] if self.adv[c][0] else 0
                d = self.ca[c][1] if self.adv[c][1] else 0
                return Packet(True, fc_dllp(UPDATEFC, c, h, d))
        if self.send_idx < len(self.rbuf) and self.rbuf[self.send_idx][2] <= now:
            seq, data, _ = self.rbuf[self.send_idx]
            self.send_idx += 1
            if (seq - self.nts) % SEQ_MOD < SEQ_HALF:
                self.nts = (seq + 1) % SEQ_MOD          # first transmission
            self.counts["tx_tlps"] += 1
            if self.replay_active and self.send_idx >= self.replay_end:
                self._replay_done()
            self.tx_cur_tlp = True
            return Packet(False, data)
        return None

    def _enter_active(self, now: int) -> None:
        self.state = ACTIVE
        self.dl_up_at = now if self.dl_up_at is None else self.dl_up_at
        # one UpdateFC per class at once: a partner still in FC_INIT2
        # needs one to finish its initialisation
        self.upd_time = [now - UPDATEFC_PERIOD] * 3


# --------------------------------------------------------- channel, pair

class Channel:
    """Two phy stacks and the line, one direction each way: a fixed
    latency, one framing cycle per packet, injected errors, and a retrain
    hold during which nothing is sent and packets in flight are lost."""

    def __init__(self, rng: random.Random, latency: int = 22, retrain_cycles: int = 500,
                 errors: dict | None = None):
        self.rng, self.latency, self.retrain_cycles = rng, latency, retrain_cycles
        self.errors = errors or {}
        self.q = (deque(), deque())
        self.recovery_until = -1
        self.drop_tlp_window = tuple(self.errors.get("drop_tlp_window", (0, 0)))

    def recovery(self, now: int) -> bool:
        return now < self.recovery_until

    def retrain(self, now: int) -> None:
        if not self.recovery(now):
            self.recovery_until = now + self.retrain_cycles
            for q in self.q:
                q.clear()

    def send(self, way: int, pkt: Packet, now: int) -> None:
        e, r = self.errors, self.rng
        kind = "dllp" if pkt.dllp else "tlp"
        if r.random() < e.get(kind + "_drop", 0.0):
            return
        if not pkt.dllp and way == 0 and self.drop_tlp_window[0] <= now < self.drop_tlp_window[1]:
            return
        data, err = pkt.data, False
        if r.random() < e.get(kind + "_corrupt", 0.0):
            i = r.randrange(len(data))
            data = data[:i] + bytes([data[i] ^ (1 << r.randrange(8))]) + data[i + 1:]
        if r.random() < e.get(kind + "_phy_err", 0.0):
            err = True
        self.q[way].append((now + pkt.beats() + 1 + self.latency, Packet(pkt.dllp, data, err)))

    def arrivals(self, way: int, now: int) -> list[Packet]:
        out, q = [], self.q[way]
        while q and q[0][0] <= now:
            out.append(q.popleft()[1])
        return out


class Pair:
    """Two DLLs back to back: ``a`` (way 0 sends a to b) and ``b``."""

    def __init__(self, a: Dll, b: Dll, ch: Channel):
        self.a, self.b, self.ch = a, b, ch
        self.now = 0

    def step(self, sinks=(True, True), link=(True, True)) -> None:
        now, ch = self.now, self.ch
        for i, d in enumerate((self.a, self.b)):
            if link[i] != d.link_up:
                d.set_link_up(link[i], now)
        for way, d in ((0, self.b), (1, self.a)):
            for pkt in ch.arrivals(way, now):
                d.receive(pkt, now)
        rec = ch.recovery(now)
        for way, d in ((0, self.a), (1, self.b)):
            pkt = d.tick(now, rec, not rec, sinks[way])
            if pkt is not None:
                ch.send(way, pkt, now)
            if d.retrain_req:
                ch.retrain(now)
        self.now += 1


# ---------------------------------------------------------------- perf

def make_traffic(rng: random.Random, count: int, mix: str, payload: int) -> list[bytes]:
    kinds = {"mwr": ["mwr"], "cpld": ["cpld"], "mixed": ["mwr", "mrd", "cpld"]}[mix]
    out = []
    for i in range(count):
        k = rng.choice(kinds)
        p = payload if payload >= 0 else 4 * rng.randint(1, MAX_PAYLOAD // 4)
        out.append(make_tlp(k, 0 if k == "mrd" else p, i))
    return out


def _int(w: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = w.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"workload {key}={v!r} not an integer in {lo}..{hi}")
    return v


def run_traffic(w: dict, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    count = _int(w, "tlps", 500, 1, 100000)
    payload = _int(w, "payload", MAX_PAYLOAD, -1, MAX_PAYLOAD)
    mix = w.get("mix", "mwr")
    both = bool(w.get("bidirectional", False))
    fc = w.get("fc", {})
    a = Dll("a", fc)
    b = Dll("b", fc, ack_latency=_int(w, "partner_ack_latency", ACK_LATENCY, 1, 1000))
    ch = Channel(rng, latency=_int(w, "latency", 22, 0, 1000),
                 retrain_cycles=_int(w, "retrain_cycles", 500, 1, 100000),
                 errors=w.get("errors", {}))
    pair = Pair(a, b, ch)
    starve = w.get("sink_starve", [])            # [[start, end], ...] for b's sink
    duty = float(w.get("sink_duty", 1.0))
    sent = {0: make_traffic(rng, count, mix, payload), 1: []}
    if both:
        sent[1] = make_traffic(rng, count, mix, payload)
    idx, first, last = [0, 0], None, 0
    busy_a = 0
    limit = _int(w, "max_cycles", 2_000_000, 1000, 50_000_000)
    while pair.now < limit:
        now = pair.now
        for i, d in enumerate((a, b)):
            if idx[i] < len(sent[i]) and d.offer(sent[i][idx[i]], now):
                first = now if first is None else first
                idx[i] += 1
        sink_b = (not any(s <= now < e for s, e in starve)) and (duty >= 1.0 or rng.random() < duty)
        pair.step(sinks=(True, sink_b))
        if a.tx_busy and a.tx_cur is not None and not a.tx_cur.dllp:
            busy_a += 1
        if len(b.delivered) >= len(sent[0]) and len(a.delivered) >= len(sent[1]):
            last = pair.now
            break
    else:
        last = pair.now
    m = _score(sent[0], b.delivered)
    m2 = _score(sent[1], a.delivered)
    span = max(1, last - (first or 0))
    payload_bytes = sum(tlp_info(t)[3] - (16 if t[0] & 0x20 else 12) for t in b.delivered)
    met = {
        "tlps_lost": m[0] + m2[0], "tlps_duplicated": m[1] + m2[1],
        "tlps_out_of_order": m[2] + m2[2],
        "payload_bytes_per_cycle": payload_bytes / span,
        "tlp_line_fraction": busy_a / span,
        "cycles": float(last),
        "ack_latency_cycles_max": float(max(b.ack_lat + a.ack_lat, default=0)),
        "replays": float(a.counts["replays"] + b.counts["replays"]),
        "retrains": float(a.counts["retrains"] + b.counts["retrains"]),
        "credit_blocked_cycles": float(a.counts["credit_blocked"] + b.counts["credit_blocked"]),
    }
    for i, n in enumerate(ERR_NAMES):
        met["err_" + n] = float(a.errors[i] + b.errors[i])
    return met


def _score(sent: list[bytes], got: list[bytes]) -> tuple[int, int, int]:
    """(lost, duplicated, out of order) of ``got`` against ``sent``."""
    pos = {t: i for i, t in enumerate(sent)}
    seen, dup, ooo, prev = set(), 0, 0, -1
    for t in got:
        i = pos.get(t, -2)
        if i in seen:
            dup += 1
            continue
        seen.add(i)
        if i < prev:
            ooo += 1
        prev = max(prev, i)
    return len(sent) - len(seen - {-2}), dup, ooo


def run_init(w: dict, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    trials = _int(w, "trials", 20, 1, 10000)
    skew = _int(w, "skew", 0, 0, 100000)
    worst = 0
    for _ in range(trials):
        a, b = Dll("a", w.get("fc", {})), Dll("b", w.get("fc", {}))
        ch = Channel(rng, latency=_int(w, "latency", 22, 0, 1000), errors=w.get("errors", {}))
        pair = Pair(a, b, ch)
        up_b = rng.randint(0, skew)
        while pair.now < 100000 and (a.dl_up_at is None or b.dl_up_at is None):
            pair.step(link=(True, pair.now >= up_b))
        done = [d.dl_up_at for d in (a, b)]
        worst = max(worst, (max(done) - up_b) if None not in done else 10**9)
    # From the later link-up to both ends in DL_Active.
    return {"dl_active_cycles_max": float(worst)}


def perf(workload: dict, seed: int) -> dict[str, float]:
    kind = workload.get("kind")
    if kind == "traffic":
        return run_traffic(workload, seed)
    if kind == "init":
        return run_init(workload, seed)
    raise ValueError(f"workload kind {kind!r}")
