"""Independent completion-side reference of pcie_tl (wiki/src/dv/tl/DV_PLAN.md,
Strategy): what the transaction layer must do with the application's
reads and the completions the host returns, written from the tl HAS pages
(COMPLETIONS.md: requester tags, matching, checks, delivery on rc,
non-posted pending, completion timeout; TLP.md for the completion fields)
and the core APP page (the rc stream). Like the receive and transmit
references it shares no code with the architecture model (src/arch/tl) or
the RTL.

It keeps each outstanding read by tag with absolute byte addresses: the
next byte due and the bytes still due. A completion is judged as a whole
(``judge``); the timeout window is the HAS's (``Reads.window``). The host
side helper ``legal_completions`` builds the completions a correct
completer may return for a read, split at any chosen 64-byte boundaries.
Python standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Completion Status (TLP.md, completions)
SC, UR, CRS, CA = 0b000, 0b001, 0b010, 0b100
TYPE_CPL, TYPE_CPL_LOCKED = 0b01010, 0b01011

# tl_err_o bits (TL_CFG error events)
MALFORMED, POISONED_CPL, UNEXPECTED, TIMEOUT, RX_UR, RX_CA = 2, 4, 5, 6, 7, 8

# rc_status_o (APP, requester completions)
RC_OK, RC_UR, RC_CA, RC_FAILED = 0, 1, 2, 3

RCB = 64                        # read completion boundary (F2)
TAGS = 8                        # tags 0 to 7 (F2)
TIMEOUT_STEPS = 16              # the timeout is the 17th prescaler pulse after sending


def _lowest(be: int) -> int:
    """Index of the lowest enabled byte of a byte-enable nibble, 0 if none."""
    for i in range(4):
        if be >> i & 1:
            return i
    return 0


def _highest(be: int) -> int:
    for i in (3, 2, 1, 0):
        if be >> i & 1:
            return i
    return 0


def read_bytes(length: int, fbe: int, lbe: int) -> int:
    """The bytes a read asks for (COMPLETIONS.md, byte count and lower
    address): the span of F for one DW (1 for a zero-length read), else 4n
    less the disabled bytes below F's first and above L's last enabled byte."""
    if length == 1:
        return _highest(fbe) - _lowest(fbe) + 1 if fbe else 1
    return 4 * length - _lowest(fbe) - (3 - _highest(lbe))


@dataclass
class Read:
    tag: int
    addr: int                   # DW address of the read
    length: int                 # DWs
    fbe: int
    lbe: int
    nxt: int = 0                # address of the next byte due
    left: int = 0               # bytes still due
    sent: int | None = None     # cycle the link took its sop beat
    failed: bool = False        # a poisoned completion arrived

    def __post_init__(self) -> None:
        self.nxt = self.addr + _lowest(self.fbe)
        self.left = read_bytes(self.length, self.fbe, self.lbe)

    @property
    def last_dw_end(self) -> int:
        """The address just past the DW holding the read's last byte."""
        return ((self.nxt + self.left - 1) | 3) + 1


@dataclass
class Packet:
    """One rc packet (APP): a completion's data, or one status beat."""
    tag: int
    status: int
    done: bool
    data: list = field(default_factory=list)

    @property
    def beats(self) -> int:
        return len(self.data) if self.status == RC_OK else 1


@dataclass
class Verdict:
    err: int | None             # the tl_err_o bit, or None
    packet: Packet | None       # what goes to rc, or None
    case: str                   # coverage name of the case
    ends: bool = False          # the read is finished


def fields(tlp: bytes) -> dict:
    """The completion fields the tracker reads (TLP.md, completions)."""
    b = tlp
    fmt, typ = b[0] >> 5, b[0] & 0x1F
    length = ((b[2] & 3) << 8 | b[3]) or 1024
    n = length if fmt & 2 else 0
    return {"fmt": fmt, "type": typ, "ep": (b[2] >> 6) & 1, "td": b[2] >> 7,
            "status": b[6] >> 5, "bc": ((b[6] & 0xF) << 8 | b[7]) or 4096,
            "rid": b[8] << 8 | b[9], "tag": b[10], "la": b[11] & 0x7F,
            "data": [int.from_bytes(b[12 + 4 * i:16 + 4 * i], "little") for i in range(n)]}


class Reads:
    """The application's outstanding reads (COMPLETIONS.md, requester
    tags) and the completion timeout window, ``div`` being
    ``CPL_TIMEOUT_DIV``."""

    def __init__(self, div: int = 1):
        self.period = (1 << 21) // (TIMEOUT_STEPS * div)
        self.reads: dict[int, Read] = {}

    # ------------------------------------------------------------ reads
    def take(self, tag: int, addr: int, length: int, fbe: int, lbe: int) -> None:
        if tag in self.reads:
            raise ValueError(f"tag {tag} in use")
        self.reads[tag] = Read(tag, addr & ~3, length, fbe, lbe)

    def sent(self, tag: int, cycle: int) -> None:
        self.reads[tag].sent = cycle

    def pending(self) -> bool:
        """tl_np_pending_o (COMPLETIONS.md, non-posted pending)."""
        return bool(self.reads)

    # ---------------------------------------------------------- timeout
    def window(self) -> tuple[int, int]:
        """A read times out more than the first and at most the second
        number of cycles after it was sent (16 and 17 prescaler periods)."""
        return TIMEOUT_STEPS * self.period, (TIMEOUT_STEPS + 1) * self.period

    def start(self, r: Read, dis_last: int) -> int | None:
        """The cycle its timer counts from: when it was sent, or the cycle
        after the disable was last seen (the disable is registered, so it
        holds the timers one cycle after the input)."""
        if r.sent is None:
            return None
        return max(r.sent, dis_last + 1)

    def due(self, now: int, dis_last: int) -> list[int]:
        """Tags whose timeout may fire at ``now``: beyond 16 periods."""
        lo, _ = self.window()
        return sorted(t for t, r in self.reads.items()
                      if (s := self.start(r, dis_last)) is not None and now - s > lo)

    def overdue(self, now: int, dis_last: int) -> list[int]:
        """Tags that should have timed out by ``now`` at the latest."""
        _, hi = self.window()
        return sorted(t for t, r in self.reads.items()
                      if (s := self.start(r, dis_last)) is not None and now - s >= hi)

    def timeout(self, tag: int) -> Packet:
        """The read ends: one failed beat (COMPLETIONS.md, completion timeout)."""
        del self.reads[tag]
        return Packet(tag, RC_FAILED, True)

    # ------------------------------------------------------- completions
    def judge(self, tlp: bytes, my_id: int) -> Verdict:
        """A received completion, judged when its header has arrived
        (COMPLETIONS.md, matching and checks); the read changes."""
        f = fields(tlp)
        r = self.reads.get(f["tag"])
        if f["type"] != TYPE_CPL:
            return Verdict(UNEXPECTED, None, "unexpected_locked")
        if f["rid"] != my_id:
            return Verdict(UNEXPECTED, None, "unexpected_rid")
        if f["tag"] >= TAGS:
            return Verdict(UNEXPECTED, None, "unexpected_tag_high")
        if r is None:
            return Verdict(UNEXPECTED, None, "unexpected_no_read")
        if r.sent is None:
            return Verdict(UNEXPECTED, None, "unexpected_unsent")
        st = f["status"]
        if st == CRS:
            return Verdict(MALFORMED, None, "crs")
        if st != SC:
            del self.reads[r.tag]
            if st == CA:
                return Verdict(RX_CA, Packet(r.tag, RC_CA, True), "ca", True)
            return Verdict(RX_UR, Packet(r.tag, RC_UR, True), "ur" if st == UR else "reserved",
                           True)
        data = f["data"]
        if not data:
            return Verdict(MALFORMED, None, "mal_no_data")
        if f["bc"] != r.left:
            return Verdict(MALFORMED, None, "mal_byte_count")
        if f["la"] != r.nxt & 0x7F:
            return Verdict(MALFORMED, None, "mal_lower_address")
        end = (r.nxt & ~3) + 4 * len(data)          # just past the data
        if end > r.last_dw_end:
            return Verdict(MALFORMED, None, "mal_past_end")
        if end < r.nxt + r.left and end % RCB:
            return Verdict(MALFORMED, None, "mal_boundary")
        err = None
        if f["ep"]:
            r.failed = True
            err = POISONED_CPL
        covered = min(end, r.nxt + r.left) - r.nxt
        r.left -= covered
        r.nxt = end
        if r.left == 0:
            del self.reads[r.tag]
            if r.failed:
                return Verdict(err, Packet(r.tag, RC_FAILED, True),
                               "poisoned_last" if f["ep"] else "failed_last", True)
            return Verdict(err, Packet(r.tag, RC_OK, True, data), "last", True)
        if r.failed:
            return Verdict(err, None, "poisoned" if f["ep"] else "failed_more")
        return Verdict(err, Packet(r.tag, RC_OK, False, data), "partial")


# ------------------------------------------------------------- host side

def legal_completions(addr: int, length: int, fbe: int, lbe: int, cuts=()) -> list[dict]:
    """The completions a correct completer returns for a read (COMPLETIONS.md,
    byte count and lower address, splitting): one per segment of the read's
    DWs, cut at the 64-byte boundaries in ``cuts`` that fall inside it. Each
    is {"bc", "la", "first" (DW index), "n" (DWs)}."""
    start, end = addr & ~3, (addr & ~3) + 4 * length
    first_byte = start + _lowest(fbe)
    total = read_bytes(length, fbe, lbe)
    marks = [start] + sorted({c for c in cuts if start < c < end and c % RCB == 0}) + [end]
    out = []
    for k in range(len(marks) - 1):
        s = marks[k]
        b0 = first_byte if k == 0 else s
        out.append({"bc": (first_byte + total - b0) & 0xFFF, "la": b0 & 0x7F,
                    "first": (s - start) // 4, "n": (marks[k + 1] - s) // 4})
    return out
