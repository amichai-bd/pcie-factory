"""Architecture model of pcie_tl, the transaction layer
(wiki/src/arch/tl/HAS.md and its pages TLP.md, ROUTING.md,
COMPLETIONS.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). It runs the HAS rules at TLP level, one step per
clk_i cycle, one beat per stream per cycle:

- header formats: parsing every received TLP and forming every TLP sent
  (``parse``, ``mem_tlp``, ``cpl_tlp``, ``msg_tlp``);
- receive classification: malformed checks, poisoned requests, BAR0 and
  BAR2 decode, configuration, messages and Unsupported Request
  (``classify``);
- the receive queues sized by the advertised credits, their ordering and
  the credit release when a request leaves ``tl`` (Q9);
- completions from ``cc``, from ``cfg`` and for Unsupported Request, split
  on the 64-byte read completion boundary (``split_read``);
- the requester side: ``rq`` requests, the completion tracker by tag and
  the completion timeout (``Tracker``);
- the transmit arbiter and the posted-before-completion order.

The TLP stream is the dll model's: a TLP is its bytes, and its class and
credits come from ``dll.tlp_info``, loaded unchanged from src/arch/dll (as
the link model does) so the credit rules have one model. ``perf`` runs
``tl`` over the link model's ``LinkPair`` (two dll models over the line)
against a host stand-in. Until the dll and link gain the credit-release
input (#303), the dll model returns credits at delivery; the host
stand-in therefore also keeps the release rule's credit count itself and
sends only what that count allows, which is the gate the release input
gives a real host. Python standard library only (plus the sibling dll and
link models).
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

# The dll and link models by bare name from src/arch: arch may use another
# IP's arch model; it never imports DV, RTL or tools.
for _ip in ("dll", "link"):
    _d = str(Path(__file__).resolve().parent.parent / _ip)
    if _d not in sys.path:
        sys.path.append(_d)

import pcie_dll_arch as dll  # noqa: E402
import pcie_link_arch as link  # noqa: E402

# ---------------------------------------------------------------- constants

P, NP, CPL = dll.P, dll.NP, dll.CPL
MAX_PAYLOAD = dll.MAX_PAYLOAD      # 128 bytes, fixed (F2)
MAX_READ_REQ = 128                 # requester reads, fixed (F2)
RCB = 64                           # read completion boundary, fixed (F2)
TAGS = 8                           # requester tags 0..7 (F2)
HANDLES = 8                        # completer read handles (core HAS)
CPL_TIMEOUT = 1 << 21              # cycles at CPL_TIMEOUT_DIV 1 (COMPLETIONS.md)
CPL_TIMEOUT_STEPS = 16             # prescaler pulses per timeout
RX_CPL_BYTES = TAGS * MAX_READ_REQ # rc data buffer (APP: 1 KiB)
TX_CPL_BYTES = 2 * (MAX_PAYLOAD + 12)   # model's cc completion buffer (MAS sizes the RTL)
TX_REQ_BYTES = 2 * (MAX_PAYLOAD + 16)   # model's rq/message buffer
RELEASE_LOOP = 32                  # cycles: a release to the host's credit gate (UpdateFC, line)

# Fmt (byte 0 bits 7:5) and Type (bits 4:0) [cite: PCIe Base Spec, rev 3.0, §2.2.1]
F_3DW, F_4DW, F_3DW_D, F_4DW_D, F_PREFIX = 0, 1, 2, 3, 4
T_MEM, T_MEMLK, T_IO, T_CFG0, T_CFG1 = 0x00, 0x01, 0x02, 0x04, 0x05
T_CPL, T_CPLLK = 0x0A, 0x0B
T_FETCHADD, T_SWAP, T_CAS = 0x0C, 0x0D, 0x0E
T_MSG = 0x10                       # 10rrr: rrr is the routing subfield

# Completion status [cite: PCIe Base Spec, rev 3.0, §2.2.9]
SC, UR, CRS, CA = 0, 1, 2, 4

# Message codes [cite: PCIe Base Spec, rev 3.0, §2.2.8]
MSG_UNLOCK = 0x00
MSG_PM_AS_NAK = 0x14
MSG_PME_TURN_OFF = 0x19
MSG_PME_TO_ACK = 0x1B
MSG_ERR = {1: 0x30, 2: 0x31, 3: 0x33}          # msg_kind 1..3: ERR_COR, ERR_NONFATAL, ERR_FATAL
MSG_SET_SLOT_POWER = 0x50
MSG_VDM1 = 0x7F
MSG_IGNORED = {0x40, 0x41, 0x43, 0x44, 0x45, 0x47, 0x48}
SILENT_MSGS = {MSG_UNLOCK, MSG_PM_AS_NAK, MSG_SET_SLOT_POWER, MSG_VDM1} | MSG_IGNORED
R_TO_RC, R_BROADCAST, R_LOCAL, R_GATHER = 0b000, 0b011, 0b100, 0b101

# tl_err bits (TL_CFG error events)
E_UR_P, E_UR_NP, E_MALFORMED, E_POISONED_REQ, E_POISONED_CPL, E_UNEXPECTED, \
    E_TIMEOUT, E_RX_UR, E_RX_CA = range(9)
ERR_NAMES = ("ur_posted", "ur_nonposted", "malformed", "poisoned_request",
             "poisoned_completion", "unexpected_completion", "completion_timeout",
             "received_ur", "received_ca")

# rc_status_o (APP)
RC_OK, RC_UR, RC_CA, RC_FAILED = 0, 1, 2, 3

D0, D3HOT = 0, 3


# ------------------------------------------------------------ TLP formats

def parse(tlp: bytes) -> dict:
    """Every header field this design reads (TLP.md), by name."""
    b = tlp
    fmt, typ = b[0] >> 5, b[0] & 0x1F
    h = {"fmt": fmt, "type": typ, "tc": (b[1] >> 4) & 7, "td": b[2] >> 7,
         "ep": (b[2] >> 6) & 1, "attr": (b[2] >> 4) & 3, "at": (b[2] >> 2) & 3,
         "length": (((b[2] & 3) << 8) | b[3]) or 1024}
    hdr = 16 if fmt & 1 else 12
    h["hdr_bytes"] = hdr
    h["data"] = tlp[hdr:] if fmt & 2 else b""
    if typ in (T_CPL, T_CPLLK):
        h.update(cid=(b[4] << 8) | b[5], status=b[6] >> 5, bcm=(b[6] >> 4) & 1,
                 bc=(((b[6] & 0xF) << 8) | b[7]) or 4096, rid=(b[8] << 8) | b[9],
                 tag=b[10], la=b[11] & 0x7F)
        return h
    h.update(rid=(b[4] << 8) | b[5], tag=b[6], lbe=b[7] >> 4, fbe=b[7] & 0xF, code=b[7])
    if typ in (T_CFG0, T_CFG1):
        h.update(bus=b[8], dev=b[9] >> 3, fn=b[9] & 7, reg=((b[10] & 0xF) << 6) | (b[11] >> 2))
    elif hdr == 16:
        h["addr"] = int.from_bytes(b[8:16], "big") & ~3
    else:
        h["addr"] = int.from_bytes(b[8:12], "big") & ~3
    return h


def _dw0(fmt: int, typ: int, length: int, tc: int = 0, attr: int = 0, ep: int = 0) -> bytes:
    return bytes([(fmt << 5) | typ, tc << 4, (ep << 6) | (attr << 4) | ((length >> 8) & 3),
                  length & 0xFF])


def mem_tlp(write: bool, addr: int, length: int, fbe: int, lbe: int, rid: int, tag: int,
            data: bytes = b"", ep: int = 0, tc: int = 0, attr: int = 0,
            force_4dw: bool = False) -> bytes:
    """A memory read or write: a 3-DW header below 4 GiB, a 4-DW header
    otherwise (TLP.md)."""
    four = force_4dw or addr >> 32 != 0
    fmt = (F_4DW if four else F_3DW) | (2 if write else 0)
    a = addr.to_bytes(8, "big") if four else (addr & 0xFFFFFFFF).to_bytes(4, "big")
    return (_dw0(fmt, T_MEM, length % 1024, tc, attr, ep)
            + bytes([rid >> 8, rid & 0xFF, tag, (lbe << 4) | fbe]) + a + data)


def cfg_tlp(write: bool, bus: int, dev: int, fn: int, reg: int, fbe: int, rid: int,
            tag: int, data: bytes = b"", type1: bool = False, length: int = 1,
            tc: int = 0, ep: int = 0) -> bytes:
    """A configuration request (host stand-in and tests)."""
    fmt = F_3DW_D if write else F_3DW
    return (_dw0(fmt, T_CFG1 if type1 else T_CFG0, length, tc, 0, ep)
            + bytes([rid >> 8, rid & 0xFF, tag, fbe, bus, (dev << 3) | fn,
                     (reg >> 6) & 0xF, (reg & 0x3F) << 2]) + data)


def cpl_tlp(cid: int, status: int, bc: int, rid: int, tag: int, la: int,
            data: bytes = b"", locked: bool = False, tc: int = 0, attr: int = 0,
            ep: int = 0) -> bytes:
    """A completion, with data when ``data`` is given (TLP.md)."""
    fmt = F_3DW_D if data else F_3DW
    return (_dw0(fmt, T_CPLLK if locked else T_CPL, len(data) // 4, tc, attr, ep)
            + bytes([cid >> 8, cid & 0xFF, (status << 5) | ((bc >> 8) & 0xF), bc & 0xFF,
                     rid >> 8, rid & 0xFF, tag, la & 0x7F]) + data)


def msg_tlp(code: int, routing: int, rid: int, data: bytes = b"", ep: int = 0) -> bytes:
    """A message, 4-DW header, bytes 8 to 15 zero (TLP.md)."""
    fmt = F_4DW_D if data else F_4DW
    return (_dw0(fmt, T_MSG | routing, len(data) // 4, 0, 0, ep)
            + bytes([rid >> 8, rid & 0xFF, 0, code]) + bytes(8) + data)


def credits(tlp: bytes) -> tuple[int, int, int]:
    """(class, header credits, data credits) exactly as the dll counts them."""
    cls, h, d, _ = dll.tlp_info(tlp)
    return cls, h, d


# --------------------------------------------------------- byte enables

def _lo(be: int) -> int:
    return (be & -be).bit_length() - 1 if be else 0


def be_ok(length: int, fbe: int, lbe: int, addr: int) -> bool:
    """The byte-enable rules a memory request must meet (ROUTING.md)
    [cite: PCIe Base Spec, rev 3.0, §2.2.5]."""
    if length == 1:
        return lbe == 0
    if fbe == 0 or lbe == 0:
        return False
    if length == 2 and addr & 4 == 0:
        return True                    # one aligned QW: any non-zero pattern
    contiguous_f = fbe in (0b1111, 0b1110, 0b1100, 0b1000)
    contiguous_l = lbe in (0b0001, 0b0011, 0b0111, 0b1111)
    return contiguous_f and contiguous_l


def byte_count(length: int, fbe: int, lbe: int) -> int:
    """Bytes from the first enabled byte to the last enabled byte of a read
    (COMPLETIONS.md) [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]."""
    if length == 1:
        if fbe == 0:
            return 1                   # zero-length read
        return fbe.bit_length() - _lo(fbe)
    return 4 * length - _lo(fbe) - (4 - lbe.bit_length())


def enabled_data(data: bytes, length: int, fbe: int, lbe: int) -> bytes:
    """A read's DWs with the bytes the request did not enable set to 0
    (COMPLETIONS.md)."""
    out = bytearray(data)
    last = lbe if length > 1 else fbe
    for i in range(4):
        if not fbe >> i & 1:
            out[i] = 0
        if length > 1 and not last >> i & 1:
            out[4 * (length - 1) + i] = 0
    return bytes(out)


def split_read(addr: int, length: int, fbe: int, lbe: int) -> list[tuple[int, int, int, int]]:
    """The completions of a successful read: (first DW index, DWs, Byte
    Count, Lower Address) each. Every completion but the last ends on a
    64-byte boundary and carries at most 128 bytes; each is as long as
    those two rules allow (COMPLETIONS.md)."""
    total = byte_count(length, fbe, lbe)
    first = addr + _lo(fbe)                    # first enabled byte
    end_byte = first + total                   # one past the last enabled byte
    out, s, dw = [], addr, 0
    end_all = addr + 4 * length
    while s < end_all:
        e = min(end_all, (s + MAX_PAYLOAD) // RCB * RCB)
        n = (e - s) // 4
        start = first if dw == 0 else s
        out.append((dw, n, end_byte - start, start & 0x7F))
        s, dw = e, dw + n
    return out


# ------------------------------------------------------- register state

class Cfg:
    """Stand-in for pcie_cfg on the tl-cfg interface: the register state
    tl reads, and the register-access channel (one access at a time,
    finished ``latency`` cycles after it is taken). Config writes capture
    the bus and device number; unimplemented registers read 0."""

    def __init__(self, latency: int = 2):
        self.latency = latency
        self.bus = self.dev = 0
        self.mem_en = self.bm_en = False
        self.d_state = D0
        self.bar0 = self.bar2 = 0
        self.regs: dict[tuple[int, int], int] = {}
        self.accesses: list[tuple] = []
        self.msgs: deque = deque()                 # (kind, addr, data) offered on msg_*

    def access(self, bar2: bool, write: bool, addr: int, be: int, data: int,
               bus: int, dev: int) -> int:
        self.accesses.append((bar2, write, addr, be, data))
        key = (int(bar2), addr)
        if write:
            if not bar2:
                self.bus, self.dev = bus, dev
            old = self.regs.get(key, 0)
            mask = sum(0xFF << (8 * i) for i in range(4) if be >> i & 1)
            self.regs[key] = (old & ~mask) | (data & mask)
            return 0
        return self.regs.get(key, 0)

    @property
    def id(self) -> int:
        """Completer and requester ID: bus, device, function 0."""
        return (self.bus << 8) | (self.dev << 3)


# ----------------------------------------------------------- classification

def bar_hit(cfg: Cfg, addr: int, bar0_addr_w: int) -> int | None:
    """0 or 2 for a BAR hit, None otherwise (TL_CFG register state)."""
    if not cfg.mem_en or cfg.d_state != D0 or addr >> 32:
        return None
    if addr >> bar0_addr_w == cfg.bar0 >> bar0_addr_w:
        return 0
    if addr >> 12 == cfg.bar2 >> 12:
        return 2
    return None


def classify(h: dict, cfg: Cfg, bar0_addr_w: int) -> tuple[str, int | None]:
    """(outcome, error bit) for a received TLP, in the order of the checks
    in ROUTING.md. Outcomes: 'cq', 'bar2', 'cfg', 'cpl', 'ur', 'drop',
    'pme_ack', 'malformed'. The error bit is None when nothing is raised."""
    fmt, typ, length = h["fmt"], h["type"], h["length"]
    posted = (typ == T_MEM and fmt & 2) or typ & 0x18 == 0x10
    # 1. format: defined Fmt and Type, prefix
    known = (typ in (T_MEM, T_MEMLK) and fmt in (0, 1, 2, 3) and not (typ == T_MEMLK and fmt & 2)
             or typ in (T_IO, T_CFG0, T_CFG1) and fmt in (F_3DW, F_3DW_D)
             or typ in (T_CPL, T_CPLLK) and fmt in (F_3DW, F_3DW_D)
             or typ in (T_FETCHADD, T_SWAP, T_CAS) and fmt in (F_3DW_D, F_4DW_D)
             or typ & 0x18 == 0x10 and fmt in (F_4DW, F_4DW_D))
    if not known:
        return "malformed", E_MALFORMED
    if typ in (T_CPL, T_CPLLK):
        return "cpl", None                         # the tracker checks completions
    # 2. request field rules
    if typ in (T_MEM, T_MEMLK):
        if not be_ok(length, h["fbe"], h["lbe"], h["addr"]):
            return "malformed", E_MALFORMED
        if (h["addr"] & 0xFFF) + 4 * length > 4096:
            return "malformed", E_MALFORMED        # crosses a 4 KiB boundary
    if typ in (T_IO, T_CFG0, T_CFG1):
        if length != 1 or h["lbe"] != 0 or h["tc"] != 0 or h["attr"] != 0:
            return "malformed", E_MALFORMED
    # 3. poisoned requests
    if h["ep"]:
        return ("drop" if posted else "ur"), E_POISONED_REQ
    # 4. routing
    if typ == T_MEM and h["at"] == 0:
        hit = bar_hit(cfg, h["addr"], bar0_addr_w)
        if hit == 0:
            return "cq", None
        if hit == 2:
            return "bar2", None
    elif typ == T_CFG0 and h["fn"] == 0:
        return "cfg", None
    elif typ & 0x18 == 0x10:
        code = h["code"]
        if code in SILENT_MSGS:
            return "drop", None
        if code == MSG_PME_TURN_OFF:
            return "pme_ack", None
        return "drop", E_UR_P                      # unsupported message
    return ("drop", E_UR_P) if posted else ("ur", E_UR_NP)


# --------------------------------------------------------------- tracker

class Read:
    __slots__ = ("addr", "length", "fbe", "lbe", "left", "nxt", "sent", "pulses",
                 "failed", "dws")

    def __init__(self, addr, length, fbe, lbe):
        self.addr, self.length, self.fbe, self.lbe = addr, length, fbe, lbe
        self.left = byte_count(length, fbe, lbe)
        self.nxt = addr + _lo(fbe)             # next expected byte address
        self.sent = None                       # cycle the dll took its sop
        self.pulses = 0
        self.failed = False
        self.dws = 0


class Tracker:
    """Outstanding requester reads by tag, completion matching and the
    timeout (COMPLETIONS.md)."""

    def __init__(self, timeout_div: int = 1):
        if not 1 <= timeout_div <= 1024:
            raise ValueError(f"CPL_TIMEOUT_DIV {timeout_div}")
        self.period = max(1, CPL_TIMEOUT // timeout_div // CPL_TIMEOUT_STEPS)
        self.reads: dict[int, Read] = {}

    def timeout_bounds(self) -> tuple[int, int]:
        """A read times out between these many cycles after it was sent."""
        return CPL_TIMEOUT_STEPS * self.period, (CPL_TIMEOUT_STEPS + 1) * self.period

    def tick(self, now: int) -> list[int]:
        """Tags that time out this cycle."""
        if now % self.period:
            return []
        out = []
        for tag, r in self.reads.items():
            if r.sent is not None and r.sent < now:
                r.pulses += 1
                if r.pulses > CPL_TIMEOUT_STEPS:
                    out.append(tag)
        return out

    def match(self, h: dict, my_id: int) -> tuple[str, int | None]:
        """('deliver' | 'done' | 'fail' | 'drop', error bit) for a received
        completion; updates the read."""
        r = self.reads.get(h["tag"])
        if h["type"] == T_CPLLK or h["rid"] != my_id or r is None or r.sent is None:
            return "drop", E_UNEXPECTED
        if h["status"] == CRS:
            return "drop", E_MALFORMED               # CRS answers configuration requests only
        if h["status"] != SC:
            del self.reads[h["tag"]]                 # the read ends; reserved status reads as UR
            if h["status"] == CA:
                return ("status", RC_CA), E_RX_CA
            return ("status", RC_UR), E_RX_UR
        n = len(h["data"]) // 4
        span = (r.nxt & ~3) + 4 * n - r.nxt     # bytes from the expected byte to the data's end
        if (not n or h["bc"] != r.left or h["la"] != r.nxt & 0x7F
                or span < 1 or (span < r.left and (r.nxt + span) % RCB)
                or span >= r.left + 4):
            return "drop", E_MALFORMED
        if h["ep"]:
            r.failed = True
            err = E_POISONED_CPL
        else:
            err = None
        last = span >= r.left
        r.left, r.nxt, r.dws = r.left - min(span, r.left), r.nxt + span, r.dws + n
        if last:
            del self.reads[h["tag"]]
            return ("fail" if r.failed else "done"), err
        return ("drop" if r.failed else "deliver"), err


# -------------------------------------------------------------------- tl

class Tl:
    """pcie_tl at TLP level. ``fc``: the advertised posted and non-posted
    credits that size the receive queues. ``app`` and ``tx`` are passed to
    ``step``; ``on_release(now, cls, data_credits)`` sees each release."""

    def __init__(self, cfg: Cfg | None = None, fc: dict | None = None, bar0_addr_w: int = 12,
                 cpl_timeout_div: int = 1, on_release=None):
        fc = {"ph": 8, "pd": 32, "nph": 8, "npd": 8, **(fc or {})}
        if not (1 <= fc["ph"] <= 127 and 8 <= fc["pd"] <= 2047 and 1 <= fc["nph"] <= 127
                and 2 <= fc["npd"] <= 2047):
            raise ValueError(f"credits {fc}")
        if not 12 <= bar0_addr_w <= 28:
            raise ValueError(f"BAR0_ADDR_W {bar0_addr_w}")
        self.fc = fc
        self.cfg = cfg or Cfg()
        self.bar0_addr_w = bar0_addr_w
        self.on_release = on_release
        self.track = Tracker(cpl_timeout_div)
        self.errors = [0] * len(ERR_NAMES)
        self.released: list[tuple[int, int, int]] = []
        self.counts = {"cq_bytes": 0, "rc_bytes": 0, "completions_sent": 0,
                       "cc_completions": 0, "occ_max": [0, 0, 0, 0],
                       "handles_max": 0, "timeout_cycles": []}
        self.reset()

    def reset(self) -> None:
        """rst_i, or dl_up_i 0: every queue, handle, tag and timer dropped."""
        self.p_rx = self.p_left = 0                 # posted received / left tl
        self.pq: deque = deque()                    # [seq, kind, h, credits]
        self.npq_cq: deque = deque()                # [stamp, h, credits]
        self.npq_cfg: deque = deque()               # [stamp, kind, h, credits]
        self.occ = [0, 0, 0, 0]                     # ph, pd, nph, npd credits held
        self.handles: dict[int, dict] = {}
        self.cq_cur = None                          # [packet, beats left, release]
        self.cfg_cur = None                         # access sequence to cfg
        self.cc_cur = None
        self.rq_cur = None
        self.rc_cur = None
        self.reqp: deque = deque()                  # posted line: [tlp, posted seq]
        self.reqnp: deque = deque()                 # non-posted line: [tlp, stamp]
        self.cplq: deque = deque()                  # [tlp, stamp]
        self.p_taken = self.p_sent = 0              # posted taken on rq/msg, sent to dll
        self.rcq: deque = deque()                   # [stamp, packet]
        self.rc_bytes_held = 0
        self.track.reads.clear()
        self.rr = 0
        self.tx_cur_tag = None

    # ---------------------------------------------------------- errors
    def err(self, bit: int | None) -> None:
        if bit is not None:
            self.errors[bit] += 1

    def _release(self, now: int, cr: tuple[int, int, int]) -> None:
        cls, h, d = cr
        i = 0 if cls == P else 2
        self.occ[i] -= h
        self.occ[i + 1] -= d
        self.released.append((now, cls, d))
        if self.on_release:
            self.on_release(now, cls, d)

    # ---------------------------------------------------------- receive
    def receive(self, tlp: bytes, now: int) -> None:
        """A TLP's eop beat taken from the dll; tl never stalls the stream."""
        h = parse(tlp)
        cr = credits(tlp)
        if cr[0] != CPL:
            i = 0 if cr[0] == P else 2
            self.occ[i] += cr[1]
            self.occ[i + 1] += cr[2]
            for k in range(4):
                self.counts["occ_max"][k] = max(self.counts["occ_max"][k], self.occ[k])
            lim = (self.fc["ph"], self.fc["pd"], self.fc["nph"], self.fc["npd"])
            if any(self.occ[k] > lim[k] for k in range(4)):
                raise AssertionError(f"receive queue above the advertised credits: {self.occ}")
        kind, bit = classify(h, self.cfg, self.bar0_addr_w)
        self.err(bit)
        if kind == "cpl":
            self._rx_cpl(h, now)
        elif cr[0] == P:                            # every posted request keeps its order
            self.pq.append([self.p_rx, kind, h, cr])
            self.p_rx += 1
        elif kind == "malformed":
            self._release(now, cr)
        elif kind == "cq":
            self.npq_cq.append([self.p_rx, h, cr])
        else:                                       # cfg, BAR2 read, Unsupported Request
            self.npq_cfg.append([self.p_rx, kind, h, cr])

    def _posted_busy(self) -> bool:
        """A posted request is on its way out (to cq or cfg) but not gone."""
        return bool(self.cq_cur and self.cq_cur[3]) or bool(self.cfg_cur and self.cfg_cur["posted"])

    def _pq_drops(self, now: int) -> None:
        """Posted requests tl does not pass on leave at the queue head."""
        while self.pq and self.pq[0][1] in ("drop", "malformed", "pme_ack") \
                and not self._posted_busy():
            _, kind, h, cr = self.pq.popleft()
            self.p_left += 1
            if kind == "pme_ack":
                self.reqp.append([msg_tlp(MSG_PME_TO_ACK, R_GATHER, self.cfg.id), self.p_taken])
                self.p_taken += 1
            self._release(now, cr)

    def _rx_cpl(self, h: dict, now: int) -> None:
        res, bit = self.track.match(h, self.cfg.id)
        self.err(bit)
        stamp = self.p_rx
        if res == "deliver" or res == "done":
            pkt = {"tag": h["tag"], "status": RC_OK, "data": h["data"], "done": res == "done"}
            self.rcq.append([stamp, pkt])
            self.rc_bytes_held += len(h["data"])
            if self.rc_bytes_held > RX_CPL_BYTES:
                raise AssertionError("rc buffer above 1 KiB")
        elif res == "fail":
            self.rcq.append([stamp, {"tag": h["tag"], "status": RC_FAILED, "data": b"\0" * 4,
                                     "done": True}])
        elif isinstance(res, tuple):
            self.rcq.append([stamp, {"tag": h["tag"], "status": res[1], "data": b"\0" * 4,
                                     "done": True}])

    # ------------------------------------------------------------ cycle
    def step(self, now: int, app, tx) -> None:
        """One clk_i cycle: timers, then each stream moves at most one beat,
        then at most one TLP is offered to the dll."""
        for tag in self.track.tick(now):
            self.counts["timeout_cycles"].append(now - self.track.reads.pop(tag).sent)
            self.err(E_TIMEOUT)
            self.rcq.append([self.p_rx, {"tag": tag, "status": RC_FAILED, "data": b"\0" * 4,
                                         "done": True}])
        self._pq_drops(now)
        self._cq(now, app)
        self._cfg(now)
        self._cc(now, app)
        self._rq(now, app)
        self._rc(now, app)
        self._tx(now, tx)

    # cq: BAR0 writes in order; a BAR0 read once every earlier write left
    # and a handle is free; a write may pass a read waiting for a handle.
    def _cq(self, now: int, app) -> None:
        if self.cq_cur is None:
            w = (self.pq[0] if self.pq and self.pq[0][1] == "cq" and not self._posted_busy()
                 else None)
            r = self.npq_cq[0] if self.npq_cq else None
            r_ok = (r is not None and self.p_left >= r[0]
                    and len(self.handles) < HANDLES)
            # An eligible read is older than every waiting write.
            if not r_ok and w is not None:
                self.pq.popleft()
                h = w[2]
                pkt = {"write": True, "addr": h["addr"] & ((1 << self.bar0_addr_w) - 1),
                       "len": h["length"], "fbe": h["fbe"], "lbe": h["lbe"], "data": h["data"]}
                self.cq_cur = [pkt, h["length"], w[3], True]
            elif r_ok:
                self.npq_cq.popleft()
                h = r[1]
                hd = min(set(range(HANDLES)) - set(self.handles))
                self.handles[hd] = h
                self.counts["handles_max"] = max(self.counts["handles_max"], len(self.handles))
                pkt = {"write": False, "addr": h["addr"] & ((1 << self.bar0_addr_w) - 1),
                       "len": h["length"], "fbe": h["fbe"], "lbe": h["lbe"], "handle": hd}
                self.cq_cur = [pkt, 1, r[2], False]
        if self.cq_cur is not None and app.cq_ready(now):
            self.cq_cur[1] -= 1
            if self.cq_cur[1] == 0:
                pkt, _, cr, posted = self.cq_cur
                self.cq_cur = None
                if posted:
                    self.p_left += 1
                    self.counts["cq_bytes"] += 4 * pkt["len"]
                self._release(now, cr)
                app.cq_take(pkt, now)

    # cfg: one DW access at a time; BAR2 writes in posted order, config and
    # BAR2 reads and Unsupported Request completions in arrival order once
    # every earlier posted request left.
    def _cfg(self, now: int) -> None:
        c = self.cfg_cur
        if c is not None:
            if now < c["done_at"]:
                return
            h = c["h"]
            if c["kind"] == "cfg":
                if c["write"]:
                    self._cpl_out(cpl_tlp(self.cfg.id, SC, 4, h["rid"], h["tag"], 0,
                                          tc=h["tc"], attr=h["attr"]))
                else:
                    data = enabled_data(c["data"][0].to_bytes(4, "little"), 1, h["fbe"], 0)
                    self._cpl_out(cpl_tlp(self.cfg.id, SC, 4, h["rid"], h["tag"], 0, data,
                                          tc=h["tc"], attr=h["attr"]))
                self.cfg_cur = None
            elif c["i"] + 1 < c["n"]:
                c["i"] += 1
                self._cfg_access(now, c)
            else:
                if not c["write"]:
                    data = enabled_data(b"".join(d.to_bytes(4, "little") for d in c["data"]),
                                        h["length"], h["fbe"], h["lbe"])
                    for dw, n, bc, la in split_read(h["addr"], h["length"], h["fbe"], h["lbe"]):
                        self._cpl_out(cpl_tlp(self.cfg.id, SC, bc, h["rid"], h["tag"], la,
                                              data[4 * dw:4 * (dw + n)], tc=h["tc"],
                                              attr=h["attr"]))
                self.cfg_cur = None
            return
        w = (self.pq[0] if self.pq and self.pq[0][1] == "bar2" and not self._posted_busy()
             else None)
        q = self.npq_cfg[0] if self.npq_cfg and self.p_left >= self.npq_cfg[0][0] else None
        if q is not None:                            # eligible: older than every waiting write
            self.npq_cfg.popleft()
            stamp, kind, h, cr = q
            if kind == "ur":
                if self._cpl_room(12):
                    self._ur_cpl(h)
                    self._release(now, cr)
                else:
                    self.npq_cfg.appendleft(q)
                return
            c = {"kind": kind, "h": h, "write": bool(h["fmt"] & 2), "i": 0, "data": [],
                 "n": 1 if kind == "cfg" else h["length"], "cr": cr, "posted": False}
        elif w is not None:
            self.pq.popleft()
            c = {"kind": "bar2", "h": w[2], "write": True, "i": 0, "data": [],
                 "n": w[2]["length"], "cr": w[3], "posted": True}
        else:
            return
        self.cfg_cur = c
        self._cfg_access(now, c)

    def _cfg_access(self, now: int, c: dict) -> None:
        h, i = c["h"], c["i"]
        if c["kind"] == "cfg":
            addr, be = h["reg"], h["fbe"]
            data = int.from_bytes(h["data"][:4], "little") if c["write"] else 0
            bus, dev = h["bus"], h["dev"]
        else:
            addr = ((h["addr"] & 0xFFF) >> 2) + i
            be = h["fbe"] if i == 0 else (h["lbe"] if i == c["n"] - 1 else 0xF)
            data = int.from_bytes(h["data"][4 * i:4 * i + 4], "little") if c["write"] else 0
            bus = dev = 0
        c["data"].append(self.cfg.access(c["kind"] == "bar2", c["write"], addr, be, data, bus, dev))
        c["done_at"] = now + self.cfg.latency
        if i == c["n"] - 1:                          # the last access is taken: it left tl
            if c["posted"]:
                self.p_left += 1
            self._release(now, c["cr"])

    def _ur_cpl(self, h: dict) -> None:
        """An Unsupported Request completion (COMPLETIONS.md)."""
        if h["type"] == T_MEM or h["type"] == T_MEMLK:
            bc = byte_count(h["length"], h["fbe"], h["lbe"])
            la = (h["addr"] + _lo(h["fbe"])) & 0x7F
        else:
            bc, la = 4, 0
        self._cpl_out(cpl_tlp(self.cfg.id, UR, bc, h["rid"], h["tag"], la,
                              locked=h["type"] == T_MEMLK, tc=h["tc"], attr=h["attr"]))

    def _cpl_room(self, n: int) -> bool:
        return sum(len(t) for t, _ in self.cplq) + n <= TX_CPL_BYTES

    def _cpl_out(self, tlp: bytes, stamp: int | None = None) -> None:
        self.cplq.append([tlp, self.p_taken if stamp is None else stamp])

    # cc: the application's data for one handle, split into completions as
    # its DWs arrive.
    def _cc(self, now: int, app) -> None:
        if self.cc_cur is None:
            pkt = app.cc_peek(now)
            if pkt is None:
                return
            hd = pkt["handle"]
            h = self.handles[hd]
            pkt["bytes"] = enabled_data(b"".join(x.to_bytes(4, "little") for x in pkt["data"]),
                                        h["length"], h["fbe"], h["lbe"])
            self.cc_cur = {"pkt": pkt, "h": h, "i": 0, "stamp": self.p_taken,
                           "parts": deque(split_read(h["addr"], h["length"], h["fbe"],
                                                     h["lbe"]))}
        c = self.cc_cur
        dw, n, bc, la = c["parts"][0]
        if c["i"] == dw and not self._cpl_room(12 + 4 * n):
            return                                  # cc_ready_o 0: no room for this completion
        c["i"] += 1                                 # one beat taken
        if c["i"] == dw + n:
            h = c["h"]
            data = c["pkt"]["bytes"][4 * dw:4 * (dw + n)]
            self._cpl_out(cpl_tlp(self.cfg.id, SC, bc, h["rid"], h["tag"], la, data,
                                  tc=h["tc"], attr=h["attr"]), c["stamp"])
            self.counts["cc_completions"] += 1
            c["parts"].popleft()
            if not c["parts"]:
                del self.handles[c["pkt"]["handle"]]
                app.cc_done(c["pkt"], now)
                self.cc_cur = None

    # rq: requests in the order taken, only while allowed.
    def _rq(self, now: int, app) -> None:
        cfg = self.cfg
        if self.rq_cur is None:
            if not (cfg.bm_en and cfg.d_state == D0):
                return
            req = app.rq_peek(now)
            if req is None:
                return
            n = 12 + (4 if req["addr"] >> 32 else 0) + (4 * req["len"] if req["write"] else 0)
            line = self.reqp if req["write"] else self.reqnp
            if sum(len(t) for t, _ in line) + n > TX_REQ_BYTES:
                return
            if req["len"] * 4 > MAX_PAYLOAD or (req["addr"] & 0xFFF) + 4 * req["len"] > 4096:
                raise AssertionError("rq request breaks an application obligation")
            if not req["write"]:
                if req["tag"] in self.track.reads:
                    raise AssertionError(f"rq tag {req['tag']} in use")
                self.track.reads[req["tag"]] = Read(req["addr"], req["len"], req["fbe"],
                                                    req["lbe"])
            self.rq_cur = [req, req["len"] if req["write"] else 1]
        self.rq_cur[1] -= 1                          # one beat taken (rq_valid_i held)
        if self.rq_cur[1] == 0:
            req = self.rq_cur[0]
            self.rq_cur = None
            data = b"".join(x.to_bytes(4, "little") for x in req.get("data", []))
            tlp = mem_tlp(req["write"], req["addr"], req["len"], req["fbe"], req["lbe"],
                          cfg.id, 0 if req["write"] else req["tag"], data)
            if req["write"]:
                self.reqp.append([tlp, self.p_taken])
                self.p_taken += 1
            else:                                    # waits for the posted taken before it
                self.reqnp.append([tlp, self.p_taken])
            app.rq_done(req, now)

    @property
    def np_pending(self) -> bool:
        """tl_np_pending: an rq read awaits its final completion or timeout."""
        return bool(self.track.reads)

    def message(self, kind: int, addr: int = 0, data: int = 0) -> bool:
        """msg_valid from cfg in this cycle: True when taken (msg_ready).
        The fields are sampled now; an offer not taken leaves no state, so
        cfg may withdraw it."""
        if sum(len(t) for t, _ in self.reqp) + 16 + 4 > TX_REQ_BYTES:
            return False
        if kind == 0:
            tlp = mem_tlp(True, addr, 1, 0xF, 0, self.cfg.id, 0, data.to_bytes(4, "little"))
        else:
            tlp = msg_tlp(MSG_ERR[kind], R_TO_RC, self.cfg.id)
        self.reqp.append([tlp, self.p_taken])
        self.p_taken += 1
        return True

    # rc: completions in arrival order, each once every posted request
    # received before it has left tl.
    def _rc(self, now: int, app) -> None:
        if self.rc_cur is None:
            if not self.rcq or self.p_left < self.rcq[0][0]:
                return
            _, pkt = self.rcq.popleft()
            self.rc_cur = [pkt, len(pkt["data"]) // 4]
        if app.rc_ready(now):
            self.rc_cur[1] -= 1
            if self.rc_cur[1] == 0:
                pkt = self.rc_cur[0]
                self.rc_cur = None
                if pkt["status"] == RC_OK:
                    self.rc_bytes_held -= len(pkt["data"])
                    self.counts["rc_bytes"] += len(pkt["data"])
                app.rc_take(pkt, now)

    # transmit: three lines take turns. Posted requests (rq writes,
    # messages) in order; non-posted rq requests in order, each after every
    # posted request taken before it; completions, each after every posted
    # request taken before it. A head without link credits is skipped, so a
    # posted request passes a non-posted one waiting for credits.
    def _tx(self, now: int, tx) -> None:
        heads = []
        if self.reqp:
            heads.append(("p", self.reqp[0][0]))
        if self.reqnp and self.p_sent >= self.reqnp[0][1]:
            heads.append(("np", self.reqnp[0][0]))
        if self.cplq and self.p_sent >= self.cplq[0][1]:
            heads.append(("cpl", self.cplq[0][0]))
        order = ("p", "np", "cpl")
        heads.sort(key=lambda x: (order.index(x[0]) - self.rr) % 3)
        for which, tlp in heads:
            cls, h, d = credits(tlp)
            if not all(tx.avail(cls, f) >= v for f, v in ((0, h), (1, d))):
                continue
            if not tx.offer(tlp, now):
                return                               # the dll is busy: try again next cycle
            if which == "p":
                self.reqp.popleft()
                self.p_sent += 1
            elif which == "np":
                self.reqnp.popleft()
                self.track.reads[parse(tlp)["tag"]].sent = now
            else:
                self.cplq.popleft()
                self.counts["completions_sent"] += 1
            self.rr = (order.index(which) + 1) % 3
            return


# ---------------------------------------------------- stand-ins for perf

class App:
    """The application stand-in: takes cq at ``cq_duty``, answers each read
    ``latency`` cycles after it is taken, issues ``rq`` requests from a
    list, and takes rc always. BAR0 holds a fixed pattern."""

    def __init__(self, rng: random.Random, latency: int = 16, cq_duty: float = 1.0,
                 stall: tuple = ()):
        self.rng, self.latency, self.cq_duty, self.stall = rng, latency, cq_duty, stall
        self.cq_got: list[dict] = []
        self.reads: deque = deque()                  # [ready time, packet]
        self.rq: deque = deque()
        self.rc_got: list[dict] = []
        self.done_reads: list[tuple[int, int]] = []  # (tag, cycle) with rc_done
        self.cc_sent = 0

    @staticmethod
    def pattern(off: int) -> int:
        return sum(((off + i) * 7 + 3 & 0xFF) << (8 * i) for i in range(4))

    def cq_ready(self, now: int) -> bool:
        if any(s <= now < e for s, e in self.stall):
            return False
        return self.cq_duty >= 1.0 or self.rng.random() < self.cq_duty

    def cq_take(self, pkt: dict, now: int) -> None:
        self.cq_got.append(pkt)
        if not pkt["write"]:
            data = [self.pattern(pkt["addr"] + 4 * i) for i in range(pkt["len"])]
            self.reads.append([now + self.latency, {"handle": pkt["handle"], "data": data}])

    def cc_peek(self, now: int):
        if self.reads and self.reads[0][0] <= now:
            return self.reads[0][1]
        return None

    def cc_done(self, pkt: dict, now: int) -> None:
        self.reads.popleft()
        self.cc_sent += 1

    def rq_peek(self, now: int):
        return self.rq[0] if self.rq else None

    def rq_done(self, req: dict, now: int) -> None:
        self.rq.popleft()

    def rc_ready(self, now: int) -> bool:
        return True

    def rc_take(self, pkt: dict, now: int) -> None:
        self.rc_got.append(pkt)
        if pkt["done"]:
            self.done_reads.append((pkt["tag"], now))


class Host:
    """The host stand-in over its dll: sends the requests ``gen`` yields,
    keeps the release rule's credit count for P and NP (the gate a host
    gets from the credit-release input, #303), answers tl's reads with
    completions split on a 64-byte boundary after ``latency`` cycles
    (dropping those ``drop`` selects), and checks every completion tl
    sends against the read it answers."""

    def __init__(self, fc: dict, latency: int = 100, drop=None):
        self.cr = {P: [fc["ph"], fc["pd"]], NP: [fc["nph"], fc["npd"]]}
        self.returns: deque = deque()
        self.latency, self.drop = latency, drop or (lambda tag, n: False)
        self.out: deque = deque()                    # [ready time, tlp]
        self.reads: dict[int, dict] = {}             # host tag -> expected read
        self.mwr_bytes = 0
        self.cpl_bytes = 0
        self.cpl_count = 0
        self.cpl_errors = 0
        self.msgs: list[int] = []
        self.n_reads = 0

    def release(self, now: int, cls: int, d: int) -> None:
        self.returns.append((now + RELEASE_LOOP, cls, d))

    def gate(self, tlp: bytes, now: int) -> bool:
        while self.returns and self.returns[0][0] <= now:
            _, cls, d = self.returns.popleft()
            self.cr[cls][0] += 1
            self.cr[cls][1] += d
        cls, h, d = credits(tlp)
        if cls == CPL:
            return True
        return self.cr[cls][0] >= h and self.cr[cls][1] >= d

    def consume(self, tlp: bytes) -> None:
        cls, h, d = credits(tlp)
        if cls != CPL:
            self.cr[cls][0] -= h
            self.cr[cls][1] -= d

    def expect_read(self, tag: int, addr: int, length: int) -> None:
        self.reads[tag] = {"parts": split_read(addr, length, 0xF, 0xF if length > 1 else 0),
                           "addr": addr, "i": 0}

    def receive(self, tlp: bytes, now: int) -> None:
        h = parse(tlp)
        if h["type"] in (T_CPL, T_CPLLK):
            self.cpl_count += 1
            r = self.reads.get(h["tag"])
            if r is None or h["status"] != SC:
                self.cpl_errors += 1
                return
            dw, n, bc, la = r["parts"][r["i"]]
            data = b"".join(App.pattern(r["addr"] + 4 * (dw + i) & 0xFFF).to_bytes(4, "little")
                            for i in range(n))
            if (h["bc"], h["la"], h["data"]) != (bc, la, data):
                self.cpl_errors += 1
            self.cpl_bytes += len(h["data"])
            r["i"] += 1
            if r["i"] == len(r["parts"]):
                del self.reads[h["tag"]]
                self.n_reads += 1
        elif h["type"] == T_MEM and h["fmt"] & 2:
            self.mwr_bytes += len(h["data"])
        elif h["type"] == T_MEM:
            parts = split_read(h["addr"], h["length"], h["fbe"], h["lbe"])
            for k, (dw, n, bc, la) in enumerate(parts):
                if self.drop(h["tag"], k):
                    continue
                data = bytes(4 * n)
                self.out.append([now + self.latency, cpl_tlp(0, SC, bc, h["rid"], h["tag"],
                                                             la, data)])
        else:
            self.msgs.append(h["code"])


class Bench:
    """tl over one link of the link model, a host stand-in over the other."""

    def __init__(self, rng: random.Random, fc: dict | None = None, app: App | None = None,
                 host_latency: int = 100, drop=None, cpl_timeout_div: int = 1):
        fcd = {"ph": 8, "pd": 32, "nph": 8, "npd": 8, **(fc or {})}
        dfc = dict(dll.DEFAULT_FC, **fcd)
        self.a, self.b = dll.Dll("host", dfc), dll.Dll("tl", dfc)
        self.pair = link.LinkPair(self.a, self.b, link.Line(rng))
        self.host = Host(fcd, host_latency, drop)
        self.cfg = Cfg()
        self.cfg.bus, self.cfg.dev = 1, 0
        self.cfg.mem_en = self.cfg.bm_en = True
        self.cfg.bar0, self.cfg.bar2 = 0x8000_0000, 0x9000_0000
        self.tl = Tl(self.cfg, fcd, cpl_timeout_div=cpl_timeout_div,
                     on_release=self.host.release)
        self.app = app or App(rng)
        self.gen = iter(())
        self.pending: bytes | None = None
        self.seen = [0, 0]

    def step(self) -> None:
        now = self.pair.now
        host = self.host
        if self.pending is None:
            if host.out and host.out[0][0] <= now:
                self.pending = host.out.popleft()[1]
            else:
                self.pending = next(self.gen, None)
        if (self.pending is not None and host.gate(self.pending, now)
                and self.a.offer(self.pending, now)):
            host.consume(self.pending)
            self.pending = None
        for d, sink, i in ((self.a, host.receive, 0), (self.b, self.tl.receive, 1)):
            while self.seen[i] < len(d.delivered):
                sink(d.delivered[self.seen[i]], now)
                self.seen[i] += 1
        self.tl.step(now, self.app, self.b)
        self.pair.step()

    @property
    def now(self) -> int:
        return self.pair.now


# ---------------------------------------------------------------- perf

def _int(w: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = w.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"workload {key}={v!r} not an integer in {lo}..{hi}")
    return v


def _link_payload(read: bool) -> float:
    """The link payload rate the HAS targets compare with: one way for
    writes, and for reads the completion direction, which also carries the
    Ack and UpdateFC of every read (the both-ways closed form)."""
    return link.derived_fractions(12 + MAX_PAYLOAD, MAX_PAYLOAD, read)["payload_line_fraction"]


def _run(bench: Bench, done, limit: int) -> int:
    while bench.now < limit and not done():
        bench.step()
    return bench.now


def run_completer(w: dict, seed: int) -> dict[str, float]:
    """Host writes or reads to BAR0, back to back."""
    rng = random.Random(seed)
    n = _int(w, "tlps", 500, 1, 100000)
    size = _int(w, "bytes", MAX_PAYLOAD, 4, 4096)
    offset = _int(w, "offset", 0, 0, 4092)
    read = w.get("op", "write") == "read"
    app = App(rng, latency=_int(w, "app_latency", 16, 1, 10000),
              cq_duty=float(w.get("cq_duty", 1.0)),
              stall=tuple(tuple(s) for s in w.get("cq_stall", [])))
    bench = Bench(rng, w.get("fc"), app)
    host = bench.host
    outstanding = _int(w, "host_reads", 16, 1, 256)
    base = bench.cfg.bar0
    length = size // 4
    if read and (offset + size > 4096 or size % 4):
        raise ValueError("read must stay inside one 4 KiB page")

    def gen():
        for i in range(n):
            addr = base + offset if read else base + (i * size) % 4096
            if read:
                while len(host.reads) >= outstanding:
                    yield None
                tag = i % 256
                while tag in host.reads:
                    yield None
                host.expect_read(tag, addr, length)
                yield mem_tlp(False, addr, length, 0xF, 0xF if length > 1 else 0, 0, tag)
            else:
                data = bytes((i + k) & 0xFF for k in range(size))
                yield mem_tlp(True, addr, length, 0xF, 0xF if length > 1 else 0, 0, 0, data)

    bench.gen = gen()
    first = None

    def done():
        nonlocal first
        if first is None and bench.a.counts["tx_tlps"]:
            first = bench.now
        return host.n_reads >= n if read else len(app.cq_got) >= n

    last = _run(bench, done, _int(w, "max_cycles", 3_000_000, 1000, 50_000_000))
    span = max(1, last - (first or 0))
    pay = host.cpl_bytes if read else bench.tl.counts["cq_bytes"]
    frac = pay / (link.LINE_BYTES_PER_CYCLE * span)
    occ = bench.tl.counts["occ_max"]
    return {
        "payload_line_fraction": frac,
        "fraction_of_link": frac / _link_payload(read),
        "completions_per_read": host.cpl_count / max(1, host.n_reads) if read else 0.0,
        "completion_errors": float(host.cpl_errors),
        "requests_done": float(host.n_reads if read else len(app.cq_got)),
        "posted_header_credits_held_max": float(occ[0]),
        "posted_data_credits_held_max": float(occ[1]),
        "nonposted_header_credits_held_max": float(occ[2]),
        "handles_in_use_max": float(bench.tl.counts["handles_max"]),
        "cycles": float(span),
        "tl_errors": float(sum(bench.tl.errors)),
    }


def run_requester(w: dict, seed: int) -> dict[str, float]:
    """The application writes or reads host memory through rq."""
    rng = random.Random(seed)
    n = _int(w, "tlps", 500, 1, 100000)
    size = _int(w, "bytes", MAX_PAYLOAD, 4, MAX_PAYLOAD)
    read = w.get("op", "write") == "read"
    drop_every = _int(w, "drop_every", 0, 0, 100000)
    tags = _int(w, "tags", TAGS, 1, TAGS)
    app = App(rng)
    length = size // 4
    issued, sent_at = [0], {}
    dropped = set()
    last_part = len(split_read(0x4000, length, 0xF, 0xF if length > 1 else 0)) - 1

    def drop(tag, k):
        # every drop_every-th read loses its last completion
        return sent_at.get(tag) in dropped and k == last_part

    bench = Bench(rng, w.get("fc"), app, host_latency=_int(w, "host_latency", 100, 0, 100000),
                  drop=drop, cpl_timeout_div=_int(w, "cpl_timeout_div", 1, 1, 1024))
    busy: set[int] = set()
    in_use_max = 0
    stall = 0
    first, last = None, 0
    limit = _int(w, "max_cycles", 3_000_000, 1000, 50_000_000)
    while bench.now < limit:
        now = bench.now
        for tag, _ in app.done_reads:
            busy.discard(tag)
        app.done_reads.clear()
        if not app.rq and issued[0] < n:
            addr = 0x1_0000_0000 * int(w.get("addr_64", False)) + 0x4000 + 128 * (issued[0] % 32)
            if read:
                free = [t for t in range(tags) if t not in busy]
                if free:
                    t = free[0]
                    busy.add(t)
                    sent_at[t] = issued[0]
                    if drop_every and (issued[0] + 1) % drop_every == 0:
                        dropped.add(issued[0])
                    app.rq.append({"write": False, "addr": addr, "len": length, "fbe": 0xF,
                                   "lbe": 0xF if length > 1 else 0, "tag": t})
                    issued[0] += 1
                else:
                    stall += 1
            else:
                app.rq.append({"write": True, "addr": addr, "len": length, "fbe": 0xF,
                               "lbe": 0xF if length > 1 else 0,
                               "data": [issued[0]] * length})
                issued[0] += 1
        in_use_max = max(in_use_max, len(bench.tl.track.reads))
        if first is None and bench.b.counts["tx_tlps"]:
            first = now
        bench.step()
        if read and issued[0] >= n and not busy and not app.rq:
            break
        if not read and bench.host.mwr_bytes >= n * size:
            break
    last = bench.now
    span = max(1, last - (first or 0))
    tl = bench.tl
    pay = tl.counts["rc_bytes"] if read else bench.host.mwr_bytes
    frac = pay / (link.LINE_BYTES_PER_CYCLE * span)
    lat = tl.counts["timeout_cycles"]
    fails = [p for p in app.rc_got if p["status"] == RC_FAILED]
    return {
        "payload_line_fraction": frac,
        "fraction_of_link": frac / _link_payload(read),
        "payload_bytes_per_cycle": pay / span,
        "tags_in_use_max": float(in_use_max),
        "rq_tag_stall_cycles": float(stall),
        "reads_finished": float(sum(1 for p in app.rc_got if p["done"])),
        "reads_failed": float(len(fails)),
        "reads_dropped": float(len(dropped)),
        "timeouts": float(tl.errors[E_TIMEOUT]),
        # From the cycle the dll took the read's sop to the timeout.
        "timeout_cycles_min": float(min(lat, default=0)),
        "timeout_cycles_max": float(max(lat, default=0)),
        "unexpected_completions": float(tl.errors[E_UNEXPECTED]),
        "malformed": float(tl.errors[E_MALFORMED]),
        "cycles": float(span),
    }


def perf(workload: dict, seed: int) -> dict[str, float]:
    kind = workload.get("kind")
    if kind == "completer":
        return run_completer(workload, seed)
    if kind == "requester":
        return run_requester(workload, seed)
    raise ValueError(f"workload kind {kind!r}")
