"""TLP codec of the independent host (root complex) model.

Builds and parses the transaction layer packets the host exchanges with the
endpoint at the ``link`` upper TLP stream: a TLP is its header and data as
bytes, byte 0 (Fmt and Type) first, with no sequence number and no LCRC.
On the 32-bit stream, byte 0 is bits [7:0] of the ``sop`` beat
(``to_beats`` and ``from_beats``).

Written from the PCIe Base Spec rev 3.0 (the author's reading, cited by
section, never reproduced) and the HAS pages under ``wiki/src/arch/``;
independent of the architecture models (see ``wiki/src/dv/core/HOST.md``).

Header layouts [cite: PCIe Base Spec, rev 3.0, §2.2.1]:
common DW 0, memory requests [cite: PCIe Base Spec, rev 3.0, §2.2.7],
configuration requests [cite: PCIe Base Spec, rev 3.0, §2.2.7],
messages [cite: PCIe Base Spec, rev 3.0, §2.2.8],
completions [cite: PCIe Base Spec, rev 3.0, §2.2.9].
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Fmt values (byte 0 bits [7:5]).
FMT_3DW = 0b000
FMT_4DW = 0b001
FMT_3DW_DATA = 0b010
FMT_4DW_DATA = 0b011
FMT_PREFIX = 0b100

# Type values (byte 0 bits [4:0]).
TYPE_MEM = 0b00000
TYPE_MEM_LOCKED = 0b00001
TYPE_IO = 0b00010
TYPE_CFG0 = 0b00100
TYPE_CFG1 = 0b00101
TYPE_CPL = 0b01010
TYPE_CPL_LOCKED = 0b01011
TYPE_FETCHADD = 0b01100
TYPE_SWAP = 0b01101
TYPE_CAS = 0b01110
TYPE_MSG_BASE = 0b10000  # 10rrr, rrr the routing subfield

# Completion Status (byte 6 bits [7:5]) [cite: PCIe Base Spec, rev 3.0, §2.2.9].
CPL_SC = 0b000
CPL_UR = 0b001
CPL_CRS = 0b010
CPL_CA = 0b100
STATUS_NAMES = {CPL_SC: "SC", CPL_UR: "UR", CPL_CRS: "CRS", CPL_CA: "CA"}

# Message routing subfield values [cite: PCIe Base Spec, rev 3.0, §2.2.8].
ROUTE_TO_RC = 0b000
ROUTE_BY_ADDR = 0b001
ROUTE_BY_ID = 0b010
ROUTE_BROADCAST = 0b011
ROUTE_LOCAL = 0b100
ROUTE_GATHER = 0b101

# Message codes the host sends or expects
# [cite: PCIe Base Spec, rev 3.0, §2.2.8.2] [cite: PCIe Base Spec, rev 3.0, §2.2.8.3]
# [cite: PCIe Base Spec, rev 3.0, §2.2.8.5].
MSG_UNLOCK = 0x00
MSG_PM_ACTIVE_STATE_NAK = 0x14
MSG_PM_PME = 0x18
MSG_PME_TURN_OFF = 0x19
MSG_PME_TO_ACK = 0x1B
MSG_ERR_COR = 0x30
MSG_ERR_NONFATAL = 0x31
MSG_ERR_FATAL = 0x33
MSG_SET_SLOT_POWER_LIMIT = 0x50

# Credit classes [cite: PCIe Base Spec, rev 3.0, §2.6.1].
POSTED = "P"
NON_POSTED = "NP"
COMPLETION = "CPL"


class TlpError(ValueError):
    """A byte string that is not a well-formed TLP of a known kind."""


def bdf_id(bus: int, dev: int, fn: int = 0) -> int:
    """16-bit Requester or Completer ID from bus, device and function."""
    if not (0 <= bus < 256 and 0 <= dev < 32 and 0 <= fn < 8):
        raise ValueError(f"bad BDF {bus}:{dev}.{fn}")
    return (bus << 8) | (dev << 3) | fn


def id_bdf(ident: int) -> tuple[int, int, int]:
    """Bus, device and function of a 16-bit ID."""
    return (ident >> 8) & 0xFF, (ident >> 3) & 0x1F, ident & 0x7


@dataclass
class Tlp:
    """One TLP, fields as named in the Base Spec.

    ``address`` is the full byte address of the first DW (bits [1:0] 0) for
    memory and I/O requests. ``data`` holds the payload bytes in address
    order; ``digest`` the 4 ECRC bytes when TD is 1 (kept, never checked).
    """

    fmt: int
    type: int
    tc: int = 0
    attr: int = 0  # bit 2 ID-based ordering, bits 1:0 Relaxed Ordering, No Snoop
    th: int = 0
    td: int = 0
    ep: int = 0
    at: int = 0
    length: int = 0  # DWs as encoded; 0 means 1024 when the TLP carries data
    requester_id: int = 0
    tag: int = 0
    first_be: int = 0
    last_be: int = 0
    address: int = 0
    # Configuration requests.
    cfg_bus: int = 0
    cfg_dev: int = 0
    cfg_fn: int = 0
    cfg_reg: int = 0  # byte offset of the DW, 0 to 0xFFC
    # Completions.
    completer_id: int = 0
    status: int = CPL_SC
    bcm: int = 0
    byte_count: int = 0  # as encoded: 0 means 4096
    lower_addr: int = 0
    # Messages.
    msg_code: int = 0
    msg_bytes: bytes = bytes(8)  # header bytes 8 to 15
    data: bytes = b""
    digest: bytes = b""
    extra: dict = field(default_factory=dict)

    # -- classification --------------------------------------------------
    @property
    def has_data(self) -> bool:
        return bool(self.fmt & 0b010)

    @property
    def four_dw(self) -> bool:
        return bool(self.fmt & 0b001)

    @property
    def length_dw(self) -> int:
        """Length in DWs, 0 decoded as 1024."""
        return self.length if self.length else 1024

    @property
    def kind(self) -> str:
        """Short kind name: MRd, MWr, CfgRd0, CplD, Msg, MsgD, and so on."""
        t, d = self.type, self.has_data
        if t == TYPE_MEM:
            return "MWr" if d else "MRd"
        if t == TYPE_MEM_LOCKED and not d:
            return "MRdLk"
        if t == TYPE_IO:
            return "IOWr" if d else "IORd"
        if t == TYPE_CFG0:
            return "CfgWr0" if d else "CfgRd0"
        if t == TYPE_CFG1:
            return "CfgWr1" if d else "CfgRd1"
        if t == TYPE_CPL:
            return "CplD" if d else "Cpl"
        if t == TYPE_CPL_LOCKED:
            return "CplDLk" if d else "CplLk"
        if t in (TYPE_FETCHADD, TYPE_SWAP, TYPE_CAS) and d:
            return {TYPE_FETCHADD: "FetchAdd", TYPE_SWAP: "Swap", TYPE_CAS: "CAS"}[t]
        if t & 0b11000 == TYPE_MSG_BASE:
            return "MsgD" if d else "Msg"
        return f"Unknown(fmt={self.fmt:03b},type={self.type:05b})"

    @property
    def is_completion(self) -> bool:
        return self.type in (TYPE_CPL, TYPE_CPL_LOCKED)

    @property
    def credit_class(self) -> str:
        """Flow-control class [cite: PCIe Base Spec, rev 3.0, §2.6.1]."""
        k = self.kind
        if k in ("MWr", "Msg", "MsgD"):
            return POSTED
        if self.is_completion:
            return COMPLETION
        return NON_POSTED

    def credit_cost(self) -> tuple[str, int, int]:
        """(class, header credits, data credits): one header credit, and one
        data credit per 16 bytes (4 DWs) of payload, rounded up
        [cite: PCIe Base Spec, rev 3.0, §2.6.1]."""
        data = (self.length_dw + 3) // 4 if self.has_data else 0
        return self.credit_class, 1, data

    @property
    def routing(self) -> int:
        """Message routing subfield (Type bits [2:0])."""
        return self.type & 0b111

    # -- encode ----------------------------------------------------------
    def encode(self) -> bytes:
        """Header, data and digest as the byte string on the upper stream."""
        if self.has_data and len(self.data) != 4 * self.length_dw:
            raise TlpError(
                f"{self.kind}: {len(self.data)} data bytes, Length says {4 * self.length_dw}"
            )
        if not self.has_data and self.data:
            raise TlpError(f"{self.kind}: data on a TLP without data")
        h = bytearray(16 if self.four_dw else 12)
        h[0] = ((self.fmt & 0x7) << 5) | (self.type & 0x1F)
        h[1] = ((self.tc & 0x7) << 4) | (((self.attr >> 2) & 1) << 2) | (self.th & 1)
        h[2] = (
            ((self.td & 1) << 7)
            | ((self.ep & 1) << 6)
            | ((self.attr & 0x3) << 4)
            | ((self.at & 0x3) << 2)
            | ((self.length >> 8) & 0x3)
        )
        h[3] = self.length & 0xFF
        if self.is_completion:
            h[4:6] = self.completer_id.to_bytes(2, "big")
            bc = self.byte_count & 0xFFF
            h[6] = ((self.status & 0x7) << 5) | ((self.bcm & 1) << 4) | (bc >> 8)
            h[7] = bc & 0xFF
            h[8:10] = self.requester_id.to_bytes(2, "big")
            h[10] = self.tag & 0xFF
            h[11] = self.lower_addr & 0x7F
        elif self.type & 0b11000 == TYPE_MSG_BASE:
            h[4:6] = self.requester_id.to_bytes(2, "big")
            h[6] = self.tag & 0xFF
            h[7] = self.msg_code & 0xFF
            h[8:16] = bytes(self.msg_bytes)[:8].ljust(8, b"\0")
        else:
            h[4:6] = self.requester_id.to_bytes(2, "big")
            h[6] = self.tag & 0xFF
            h[7] = ((self.last_be & 0xF) << 4) | (self.first_be & 0xF)
            if self.type in (TYPE_CFG0, TYPE_CFG1):
                h[8] = self.cfg_bus & 0xFF
                h[9] = ((self.cfg_dev & 0x1F) << 3) | (self.cfg_fn & 0x7)
                h[10] = (self.cfg_reg >> 8) & 0xF
                h[11] = self.cfg_reg & 0xFC
            elif self.four_dw:
                h[8:12] = ((self.address >> 32) & 0xFFFFFFFF).to_bytes(4, "big")
                h[12:16] = (self.address & 0xFFFFFFFC).to_bytes(4, "big")
            else:
                h[8:12] = (self.address & 0xFFFFFFFC).to_bytes(4, "big")
        out = bytes(h) + bytes(self.data)
        if self.td:
            out += bytes(self.digest).ljust(4, b"\0")[:4]
        return out

    # -- decode ----------------------------------------------------------
    @classmethod
    def decode(cls, raw: bytes) -> "Tlp":
        """Parse a TLP. Raises TlpError when the bytes disagree with Fmt,
        Length and TD, or Fmt and Type name no known kind."""
        raw = bytes(raw)
        if len(raw) < 12 or len(raw) % 4:
            raise TlpError(f"TLP of {len(raw)} bytes: not whole DWs of at least 3")
        fmt, typ = raw[0] >> 5, raw[0] & 0x1F
        if fmt == FMT_PREFIX or fmt > FMT_4DW_DATA:
            raise TlpError(f"Fmt {fmt:03b}: prefixes and reserved Fmt are not supported")
        t = cls(fmt=fmt, type=typ)
        t.tc = (raw[1] >> 4) & 0x7
        t.attr = (((raw[1] >> 2) & 1) << 2) | ((raw[2] >> 4) & 0x3)
        t.th = raw[1] & 1
        t.td = raw[2] >> 7
        t.ep = (raw[2] >> 6) & 1
        t.at = (raw[2] >> 2) & 0x3
        t.length = ((raw[2] & 0x3) << 8) | raw[3]
        if t.kind.startswith("Unknown"):
            raise TlpError(f"unknown TLP kind: {t.kind}")
        hlen = 16 if t.four_dw else 12
        if len(raw) < hlen:
            raise TlpError(f"{t.kind}: {len(raw)} bytes, shorter than its header")
        dlen = 4 * t.length_dw if t.has_data else 0
        want = hlen + dlen + (4 if t.td else 0)
        if len(raw) != want:
            raise TlpError(f"{t.kind}: {len(raw)} bytes, Fmt, Length and TD give {want}")
        t.data = raw[hlen:hlen + dlen]
        t.digest = raw[hlen + dlen:]
        if t.is_completion:
            t.completer_id = int.from_bytes(raw[4:6], "big")
            t.status = raw[6] >> 5
            t.bcm = (raw[6] >> 4) & 1
            t.byte_count = ((raw[6] & 0xF) << 8) | raw[7]
            t.requester_id = int.from_bytes(raw[8:10], "big")
            t.tag = raw[10]
            t.lower_addr = raw[11] & 0x7F
        elif typ & 0b11000 == TYPE_MSG_BASE:
            t.requester_id = int.from_bytes(raw[4:6], "big")
            t.tag = raw[6]
            t.msg_code = raw[7]
            t.msg_bytes = raw[8:16] if t.four_dw else raw[8:12]
        else:
            t.requester_id = int.from_bytes(raw[4:6], "big")
            t.tag = raw[6]
            t.last_be = raw[7] >> 4
            t.first_be = raw[7] & 0xF
            if typ in (TYPE_CFG0, TYPE_CFG1):
                t.cfg_bus = raw[8]
                t.cfg_dev = raw[9] >> 3
                t.cfg_fn = raw[9] & 0x7
                t.cfg_reg = ((raw[10] & 0xF) << 8) | (raw[11] & 0xFC)
            elif t.four_dw:
                t.address = (int.from_bytes(raw[8:12], "big") << 32) | (
                    int.from_bytes(raw[12:16], "big") & 0xFFFFFFFC
                )
            else:
                t.address = int.from_bytes(raw[8:12], "big") & 0xFFFFFFFC
        return t

    @property
    def byte_count_value(self) -> int:
        """Completion Byte Count with 0 read as 4096."""
        return self.byte_count if self.byte_count else 4096


# -- builders -------------------------------------------------------------
def byte_enables(addr: int, nbytes: int) -> tuple[int, int, int, int]:
    """(DW address, Length in DWs, First DW BE, Last DW BE) for the
    contiguous bytes [addr, addr + nbytes). nbytes 0 gives the zero-length
    form: Length 1, both enables 0 [cite: PCIe Base Spec, rev 3.0, §2.2.5]."""
    dw = addr & ~0x3
    if nbytes == 0:
        return dw, 1, 0, 0
    end = addr + nbytes  # exclusive
    ndw = ((end + 3) & ~0x3) - dw >> 2
    first = (0xF << (addr & 3)) & 0xF
    last_mask = 0xF >> ((4 - (end & 3)) & 3)
    if ndw == 1:
        return dw, 1, first & last_mask, 0
    return dw, ndw, first, last_mask


def _mem_fmt(address: int, data: bool) -> int:
    four = address >> 32 != 0
    return (FMT_4DW_DATA if four else FMT_3DW_DATA) if data else (FMT_4DW if four else FMT_3DW)


def mem_read(requester_id: int, tag: int, address: int, length: int, first_be: int,
             last_be: int, tc: int = 0, attr: int = 0) -> Tlp:
    """Memory read request: 3-DW header below 4 GiB, 4-DW header above
    [cite: PCIe Base Spec, rev 3.0, §2.2.4.1]."""
    return Tlp(fmt=_mem_fmt(address, False), type=TYPE_MEM, tc=tc, attr=attr,
               length=length & 0x3FF, requester_id=requester_id, tag=tag,
               first_be=first_be, last_be=last_be, address=address)


def mem_write(requester_id: int, address: int, data: bytes, first_be: int, last_be: int,
              tc: int = 0, attr: int = 0, ep: int = 0) -> Tlp:
    """Memory write request carrying ``data`` (whole DWs from the DW
    address)."""
    if len(data) % 4 or not data:
        raise TlpError("memory write data must be whole DWs")
    return Tlp(fmt=_mem_fmt(address, True), type=TYPE_MEM, tc=tc, attr=attr, ep=ep,
               length=(len(data) // 4) & 0x3FF, requester_id=requester_id,
               first_be=first_be, last_be=last_be, address=address, data=bytes(data))


def cfg_read(requester_id: int, tag: int, bus: int, dev: int, fn: int, reg: int,
             first_be: int = 0xF, type1: bool = False) -> Tlp:
    """Configuration read, Type 0 (or 1): Length 1, Last DW BE 0, TC 0,
    Attr 0 [cite: PCIe Base Spec, rev 3.0, §2.2.7]."""
    return Tlp(fmt=FMT_3DW, type=TYPE_CFG1 if type1 else TYPE_CFG0, length=1,
               requester_id=requester_id, tag=tag, first_be=first_be, last_be=0,
               cfg_bus=bus, cfg_dev=dev, cfg_fn=fn, cfg_reg=reg)


def cfg_write(requester_id: int, tag: int, bus: int, dev: int, fn: int, reg: int,
              value: int, first_be: int = 0xF, type1: bool = False) -> Tlp:
    """Configuration write: one DW, register bits [7:0] in the first data
    byte."""
    return Tlp(fmt=FMT_3DW_DATA, type=TYPE_CFG1 if type1 else TYPE_CFG0, length=1,
               requester_id=requester_id, tag=tag, first_be=first_be, last_be=0,
               cfg_bus=bus, cfg_dev=dev, cfg_fn=fn, cfg_reg=reg,
               data=(value & 0xFFFFFFFF).to_bytes(4, "little"))


def completion(completer_id: int, requester_id: int, tag: int, status: int = CPL_SC,
               byte_count: int = 4, lower_addr: int = 0, data: bytes = b"",
               tc: int = 0, attr: int = 0, locked: bool = False, ep: int = 0) -> Tlp:
    """Completion, with data when ``data`` is given. ``byte_count`` 4096 is
    encoded as 0 [cite: PCIe Base Spec, rev 3.0, §2.2.9]."""
    if len(data) % 4:
        raise TlpError("completion data must be whole DWs")
    return Tlp(fmt=FMT_3DW_DATA if data else FMT_3DW,
               type=TYPE_CPL_LOCKED if locked else TYPE_CPL, tc=tc, attr=attr & 0x3, ep=ep,
               length=(len(data) // 4) & 0x3FF, completer_id=completer_id, status=status,
               byte_count=byte_count & 0xFFF, requester_id=requester_id, tag=tag,
               lower_addr=lower_addr & 0x7F, data=bytes(data))


def message(requester_id: int, code: int, routing: int, data: bytes = b"",
            msg_bytes: bytes = bytes(8)) -> Tlp:
    """Message with a 4-DW header, TC 0
    [cite: PCIe Base Spec, rev 3.0, §2.2.8]."""
    if len(data) % 4:
        raise TlpError("message data must be whole DWs")
    return Tlp(fmt=FMT_4DW_DATA if data else FMT_4DW, type=TYPE_MSG_BASE | (routing & 0x7),
               length=(len(data) // 4) & 0x3FF, requester_id=requester_id, msg_code=code,
               msg_bytes=msg_bytes, data=bytes(data))


# -- upper-stream beats ---------------------------------------------------
def to_beats(raw: bytes) -> list[int]:
    """32-bit beats of a TLP: beat k holds bytes 4k to 4k+3, byte 4k in bits
    [7:0] (the ``dll`` upper TLP stream)."""
    raw = bytes(raw)
    if len(raw) % 4:
        raise TlpError("a TLP is whole DWs")
    return [int.from_bytes(raw[i:i + 4], "little") for i in range(0, len(raw), 4)]


def from_beats(beats: list[int]) -> bytes:
    """The bytes of a TLP from its beats, ``sop`` beat first."""
    return b"".join((b & 0xFFFFFFFF).to_bytes(4, "little") for b in beats)
