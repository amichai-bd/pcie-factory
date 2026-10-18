"""Independent receive reference of pcie_tl (wiki/src/dv/tl/DV_PLAN.md,
Strategy): what the transaction layer must do with each TLP the link
delivers, written from the tl HAS pages (TLP.md for the fields,
ROUTING.md for the checks and outcomes, ORDERING.md and HAS.md for the
classes and credits). It shares no code with the architecture model
(src/arch/tl), which DV uses only as a second, shadow reference.

A TLP is its bytes, byte 0 first. On the dll upper stream byte 4k of a
TLP is bits [7:0] of beat k (``to_beats``). Python standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Fmt values (TLP.md): bit 0 a 4-DW header, bit 1 with data, 0b100 a prefix.
FMT_3DW, FMT_4DW, FMT_3DW_DATA, FMT_4DW_DATA = 0b000, 0b001, 0b010, 0b011

TYPE_MEM, TYPE_MEM_LOCKED, TYPE_IO = 0b00000, 0b00001, 0b00010
TYPE_CFG0, TYPE_CFG1 = 0b00100, 0b00101
TYPE_CPL, TYPE_CPL_LOCKED = 0b01010, 0b01011
TYPE_FETCH_ADD, TYPE_SWAP, TYPE_CAS = 0b01100, 0b01101, 0b01110
TYPE_MSG = 0b10000                    # 10rrr, rrr the routing subfield

# (kind name, the Fmt values that make it defined) per Type (TLP.md, kinds).
KINDS = {
    TYPE_MEM: ("mem", {0, 1, 2, 3}),
    TYPE_MEM_LOCKED: ("mem_locked", {0, 1}),
    TYPE_IO: ("io", {0, 2}),
    TYPE_CFG0: ("cfg0", {0, 2}),
    TYPE_CFG1: ("cfg1", {0, 2}),
    TYPE_CPL: ("cpl", {0, 2}),
    TYPE_CPL_LOCKED: ("cpl_locked", {0, 2}),
    TYPE_FETCH_ADD: ("atomic", {2, 3}),
    TYPE_SWAP: ("atomic", {2, 3}),
    TYPE_CAS: ("atomic", {2, 3}),
}

# Message codes (ROUTING.md, messages).
UNLOCK, PM_ACTIVE_STATE_NAK, PME_TURN_OFF, PME_TO_ACK = 0x00, 0x14, 0x19, 0x1B
SET_SLOT_POWER_LIMIT, VENDOR_TYPE0, VENDOR_TYPE1 = 0x50, 0x7E, 0x7F
IGNORED_MSGS = (0x40, 0x41, 0x43, 0x44, 0x45, 0x47, 0x48)
DROPPED_MSGS = (UNLOCK, PM_ACTIVE_STATE_NAK, SET_SLOT_POWER_LIMIT, VENDOR_TYPE1) + IGNORED_MSGS
# Codes whose TC must be 0 (ROUTING.md, step 6): INTx, power management,
# error signalling, Unlock, Set_Slot_Power_Limit.
TC0_CODES = tuple(range(0x20, 0x28)) + (0x14, 0x18, 0x19, 0x1B, 0x30, 0x31, 0x33, 0x00, 0x50)

# tl_err_o bits (TL_CFG error events).
UR_POSTED, UR_NONPOSTED, MALFORMED, POISONED_REQUEST = 0, 1, 2, 3


# ----------------------------------------------------------- building TLPs

def _dw0(fmt: int, typ: int, length: int, tc: int, attr: int, ep: int, at: int, td: int) -> bytes:
    length_field = length & 0x3FF               # 1024 is sent as 0
    return bytes([
        (fmt << 5) | typ,
        (tc & 7) << 4,
        ((td & 1) << 7) | ((ep & 1) << 6) | ((attr & 3) << 4) | ((at & 3) << 2)
        | (length_field >> 8),
        length_field & 0xFF,
    ])


def _payload(data) -> bytes:
    return b"".join(int(d).to_bytes(4, "little") for d in data)


def memory(write: bool, addr: int, length: int, fbe: int = 0xF, lbe: int = 0xF,
           rid: int = 0x0100, tag: int = 0, data=None, tc: int = 0, attr: int = 0,
           ep: int = 0, at: int = 0, four_dw: bool | None = None, locked: bool = False,
           fmt: int | None = None) -> bytes:
    """A memory read or write. A 4-DW header when the address needs it
    (or ``four_dw``). ``data`` is a list of DWs, byte 0 in bits [7:0];
    a write without ``data`` gets a counting pattern."""
    four = (addr >> 32 != 0) if four_dw is None else four_dw
    if write and data is None:
        data = [(0x11111111 * (i + 1)) & 0xFFFFFFFF for i in range(length)]
    f = (FMT_4DW if four else FMT_3DW) | (2 if write else 0) if fmt is None else fmt
    typ = TYPE_MEM_LOCKED if locked else TYPE_MEM
    a = addr.to_bytes(8, "big") if four else (addr & 0xFFFFFFFF).to_bytes(4, "big")
    return (_dw0(f, typ, length, tc, attr, ep, at, 0)
            + bytes([rid >> 8, rid & 0xFF, tag, ((lbe & 0xF) << 4) | (fbe & 0xF)]) + a
            + (_payload(data) if f & 2 else b""))


def config(write: bool, reg: int, fbe: int = 0xF, bus: int = 0, dev: int = 0, fn: int = 0,
           rid: int = 0x0000, tag: int = 0, data: int = 0, type1: bool = False,
           length: int = 1, lbe: int = 0, tc: int = 0, attr: int = 0, ep: int = 0) -> bytes:
    """A configuration request: ``reg`` is the DW index (Extended Register
    Number in bits 9:6, Register Number in 5:0)."""
    f = FMT_3DW_DATA if write else FMT_3DW
    body = bytes([rid >> 8, rid & 0xFF, tag, ((lbe & 0xF) << 4) | (fbe & 0xF),
                  bus, (dev << 3) | fn, (reg >> 6) & 0xF, (reg & 0x3F) << 2])
    payload = _payload([data] * length) if write else b""
    return _dw0(f, TYPE_CFG1 if type1 else TYPE_CFG0, length, tc, attr, ep, 0, 0) + body + payload


def io(write: bool, addr: int, fbe: int = 0xF, rid: int = 0x0100, tag: int = 0,
       data: int = 0, tc: int = 0) -> bytes:
    f = FMT_3DW_DATA if write else FMT_3DW
    return (_dw0(f, TYPE_IO, 1, tc, 0, 0, 0, 0) + bytes([rid >> 8, rid & 0xFF, tag, fbe & 0xF])
            + (addr & 0xFFFFFFFC).to_bytes(4, "big") + (_payload([data]) if write else b""))


def atomic(typ: int, addr: int, length: int = 1, rid: int = 0x0100, tag: int = 0) -> bytes:
    four = addr >> 32 != 0
    f = FMT_4DW_DATA if four else FMT_3DW_DATA
    a = addr.to_bytes(8, "big") if four else addr.to_bytes(4, "big")
    return (_dw0(f, typ, length, 0, 0, 0, 0, 0) + bytes([rid >> 8, rid & 0xFF, tag, 0xFF])
            + a + _payload([0x5A5A0000 + i for i in range(length)]))


def message(code: int, routing: int = 0b100, rid: int = 0x0000, data=None, tc: int = 0,
            ep: int = 0, fmt: int | None = None) -> bytes:
    """A message, 4-DW header, bytes 8 to 15 zero (TLP.md, messages)."""
    f = (FMT_4DW_DATA if data else FMT_4DW) if fmt is None else fmt
    length = len(data) if data else 0
    return (_dw0(f, TYPE_MSG | routing, length, tc, 0, ep, 0, 0)
            + bytes([rid >> 8, rid & 0xFF, 0, code]) + bytes(8)
            + (_payload(data) if data else b""))


def completion(status: int = 0, byte_count: int = 4, rid: int = 0x0000, tag: int = 0,
               lower_addr: int = 0, data=None, cid: int = 0x0100, locked: bool = False,
               fmt: int | None = None) -> bytes:
    f = (FMT_3DW_DATA if data else FMT_3DW) if fmt is None else fmt
    length = len(data) if data else 0
    bc = byte_count & 0xFFF
    return (_dw0(f, TYPE_CPL_LOCKED if locked else TYPE_CPL, length, 0, 0, 0, 0, 0)
            + bytes([cid >> 8, cid & 0xFF, (status << 5) | (bc >> 8), bc & 0xFF,
                     rid >> 8, rid & 0xFF, tag, lower_addr & 0x7F])
            + (_payload(data) if data else b""))


def raw(fmt: int, typ: int, length: int = 1, header_dws: int | None = None, data_dws: int = 0,
        tc: int = 0, ep: int = 0, b4_7: bytes = bytes(4)) -> bytes:
    """Any Fmt and Type, for the kind check: a header of the size the Fmt
    states (as the dll checks it) and ``data_dws`` data DWs."""
    hdr = header_dws if header_dws is not None else (4 if fmt & 1 else 3)
    return (_dw0(fmt, typ, length, tc, 0, ep, 0, 0) + b4_7 + bytes(4 * (hdr - 2))
            + _payload([0xC0DE0000 + i for i in range(data_dws)]))


def with_digest(tlp: bytes, digest: int = 0xD16E57ED) -> bytes:
    """The TLP with TD set and a digest DW appended, as the dll delivers a
    received TLP with a digest (TLP.md, TD); tl ignores the digest."""
    return tlp[:2] + bytes([tlp[2] | 0x80]) + tlp[3:] + digest.to_bytes(4, "little")


def to_beats(tlp: bytes) -> list[int]:
    """The TLP as dll upper-stream beats: byte 4k in bits [7:0] of beat k."""
    return [int.from_bytes(tlp[i:i + 4], "little") for i in range(0, len(tlp), 4)]


def from_beats(beats) -> bytes:
    return b"".join(int(b).to_bytes(4, "little") for b in beats)


# ------------------------------------------------------------- the rules

@dataclass
class Regs:
    """The tl-cfg register state the receive path reads (TL_CFG)."""
    mem_space_en: bool = True
    d_state: int = 0
    bar0_base: int = 0x8000_0000
    bar2_base: int = 0x9000_0000
    bar0_addr_w: int = 12


@dataclass
class Verdict:
    outcome: str            # cq, bar2, cfg, ur, drop, pme, malformed, cpl
    err: int | None         # the tl_err_o bit raised, or None
    cls: str                # P, NP, CPL (the dll's credit class)
    data_credits: int       # what the dll counted for it
    kind: str               # mem, cfg0, msg, ... or "undefined"
    fields: dict = field(default_factory=dict)

    @property
    def posted(self) -> bool:
        return self.cls == "P"


def credit_class(tlp: bytes) -> tuple[str, int]:
    """(class, data credits) as the dll counts them (dll FLOW_CONTROL,
    classes and credits; tl HAS, credit release)."""
    fmt, typ = tlp[0] >> 5, tlp[0] & 0x1F
    with_data = bool(fmt & 2)
    if (typ == 0 and with_data) or typ >> 3 == 0b10:
        cls = "P"
    elif typ >> 1 == 0b0101:
        cls = "CPL"
    else:
        cls = "NP"
    length = ((tlp[2] & 3) << 8 | tlp[3]) or 1024
    return cls, ((length + 3) // 4 if with_data else 0)


def header(tlp: bytes) -> dict:
    """Every field ROUTING.md reads, by name (TLP.md)."""
    b = tlp
    h = {
        "fmt": b[0] >> 5, "type": b[0] & 0x1F, "tc": (b[1] >> 4) & 7,
        "ep": (b[2] >> 6) & 1, "attr": (b[2] >> 4) & 3, "at": (b[2] >> 2) & 3,
        "length": ((b[2] & 3) << 8 | b[3]) or 1024,
        "rid": b[4] << 8 | b[5], "tag": b[6], "lbe": b[7] >> 4, "fbe": b[7] & 0xF, "code": b[7],
    }
    if h["fmt"] & 1:
        h["addr"] = (int.from_bytes(b[8:12], "big") << 32) | (int.from_bytes(b[12:16], "big") & ~3)
    else:
        h["addr"] = int.from_bytes(b[8:12], "big") & ~3
    h["bus"], h["dev"], h["fn"] = b[8], b[9] >> 3, b[9] & 7
    h["reg"] = ((b[10] & 0xF) << 6) | (b[11] >> 2)
    hdr_len = 16 if h["fmt"] & 1 else 12
    h["td"] = b[2] >> 7
    # Length DWs of data; a digest DW after them (TD 1) is not data.
    end = hdr_len + (4 * h["length"] if h["fmt"] & 2 else 0)
    h["data"] = [int.from_bytes(b[i:i + 4], "little") for i in range(hdr_len, min(end, len(b)), 4)]
    return h


def kind_of(h: dict) -> str:
    """Step 1: the kind, or "undefined" (a prefix or an undefined pair)."""
    fmt, typ = h["fmt"], h["type"]
    if fmt & 0b100:
        return "undefined"
    if typ >> 3 == 0b10:
        return "msg" if fmt in (FMT_4DW, FMT_4DW_DATA) else "undefined"
    name, fmts = KINDS.get(typ, ("undefined", set()))
    return name if fmt in fmts else "undefined"


def byte_enables_ok(length: int, fbe: int, lbe: int, addr: int) -> bool:
    """Step 3 (ROUTING.md)."""
    if length == 1:
        return lbe == 0
    if not fbe or not lbe:
        return False
    if length == 2 and not addr & 4:
        return True
    return fbe in (0b1111, 0b1110, 0b1100, 0b1000) and lbe in (0b0001, 0b0011, 0b0111, 0b1111)


def bar_hit(addr: int, regs: Regs) -> int | None:
    """0 or 2 for a BAR hit (ROUTING.md, BAR decode), BAR0 first."""
    if not regs.mem_space_en or regs.d_state != 0 or addr >= 1 << 32:
        return None
    if addr >> regs.bar0_addr_w == regs.bar0_base >> regs.bar0_addr_w:
        return 0
    if addr >> 12 == regs.bar2_base >> 12:
        return 2
    return None


def judge(tlp: bytes, regs: Regs) -> Verdict:
    """The outcome and error event of one received TLP (ROUTING.md)."""
    h = header(tlp)
    cls, dcred = credit_class(tlp)
    kind = kind_of(h)

    def verdict(outcome: str, err: int | None) -> Verdict:
        return Verdict(outcome, err, cls, dcred, kind, h)

    if kind == "undefined":
        return verdict("malformed", MALFORMED)
    if kind in ("cpl", "cpl_locked"):
        return verdict("cpl", None)
    if kind in ("mem", "mem_locked"):
        if not byte_enables_ok(h["length"], h["fbe"], h["lbe"], h["addr"]):
            return verdict("malformed", MALFORMED)
        if (h["addr"] & 0xFFF) + 4 * h["length"] > 4096:
            return verdict("malformed", MALFORMED)
    if kind in ("io", "cfg0", "cfg1"):
        if h["length"] != 1 or h["lbe"] or h["tc"] or h["attr"]:
            return verdict("malformed", MALFORMED)
    if kind == "msg" and h["code"] in TC0_CODES and h["tc"]:
        return verdict("malformed", MALFORMED)
    # Step 7.
    outcome = "ur"
    if kind == "mem" and h["at"] == 0:
        hit = bar_hit(h["addr"], regs)
        if hit == 0:
            outcome = "cq"
        elif hit == 2:
            outcome = "bar2"
    elif kind == "cfg0" and h["fn"] == 0:
        outcome = "cfg"
    elif kind == "msg":
        if h["code"] in DROPPED_MSGS:
            outcome = "drop"
        elif h["code"] == PME_TURN_OFF:
            outcome = "pme"
    if outcome == "ur":
        return verdict("ur", UR_POSTED if cls == "P" else UR_NONPOSTED)
    # Step 8.
    if h["ep"]:
        return verdict("drop" if cls == "P" else "ur", POISONED_REQUEST)
    return verdict(outcome, None)


# ------------------------------------------------- what each outcome makes

def first_enabled(fbe: int) -> int:
    return (fbe & -fbe).bit_length() - 1 if fbe else 0


def total_bytes(length: int, fbe: int, lbe: int) -> int:
    """A read's byte count T (COMPLETIONS.md, byte count and lower address)."""
    if length == 1:
        if not fbe:
            return 1
        return fbe.bit_length() - first_enabled(fbe)
    return 4 * length - first_enabled(fbe) - (4 - lbe.bit_length())


def bar2_accesses(h: dict, write: bool) -> list[tuple]:
    """A BAR2 request as DW accesses (TL_CFG register access):
    (bar2, write, DW index, byte enables, data)."""
    n = h["length"]
    out = []
    for i in range(n):
        be = h["fbe"] if i == 0 else (h["lbe"] if i == n - 1 else 0xF)
        data = h["data"][i] if write else None
        out.append((True, write, ((h["addr"] & 0xFFF) >> 2) + i, be, data))
    return out


def cfg_access(h: dict, write: bool) -> tuple:
    """A configuration request as its one access: (bar2, write, register,
    byte enables, data, bus, device)."""
    return (False, write, h["reg"], h["fbe"], h["data"][0] if write else None,
            h["bus"] if write else 0, h["dev"] if write else 0)
