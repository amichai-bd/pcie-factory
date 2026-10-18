"""Independent transmit reference of pcie_tl (wiki/src/dv/tl/DV_PLAN.md,
Strategy): the TLPs the transaction layer must send, written from the tl
HAS pages (TLP.md for the fields, COMPLETIONS.md for completions, byte
count, lower address and splitting, ORDERING.md and HAS.md for the
credits). Like the receive reference it shares no code with the
architecture model (src/arch/tl) or the RTL.

A TLP is its bytes, byte 0 first (``to_beats`` in the receive reference
puts byte 4k in bits [7:0] of beat k). Bytes 4 and 5 of every TLP tl sends
carry this function's ID (Requester ID of a request or message, Completer
ID of a completion); the builders take it as ``my_id``. Python standard
library only.
"""

from __future__ import annotations

# Completion Status (TLP.md, completions)
SC, UR = 0b000, 0b001
# Message codes and routing (TLP.md, messages)
ERR_CODES = {1: 0x30, 2: 0x31, 3: 0x33}       # msg_kind 1..3
PME_TO_ACK = 0x1B
ROUTE_TO_RC, ROUTE_GATHER = 0b000, 0b101

MAX_PAYLOAD = 128                             # bytes (F2)
RCB = 64                                      # read completion boundary (F2)


def _dw0(fmt: int, typ: int, length: int, tc: int = 0, attr: int = 0) -> bytes:
    """Byte 0 to 3: TD, EP, AT and Attr bit 2 are 0 on every TLP tl sends."""
    return bytes([(fmt << 5) | typ, (tc & 7) << 4, ((attr & 3) << 4) | ((length >> 8) & 3),
                  length & 0xFF])


def _id(my_id: int) -> bytes:
    return bytes([my_id >> 8, my_id & 0xFF])


def _dws(data) -> bytes:
    return b"".join(int(d).to_bytes(4, "little") for d in data)


def my_id_of(bus: int, dev: int) -> int:
    """This function's ID: Bus, Device, Function 0 (TLP.md)."""
    return (bus & 0xFF) << 8 | (dev & 0x1F) << 3


# ------------------------------------------------------------- requests

def mem_write(my_id: int, addr: int, data, fbe: int = 0xF, lbe: int = 0) -> bytes:
    """A memory write: 3-DW header when address bits 63:32 are 0, TC 0,
    Attr 0, Tag 0 (TLP.md, memory requests)."""
    four = addr >> 32 != 0
    a = addr.to_bytes(8, "big") if four else addr.to_bytes(4, "big")
    return (_dw0(0b011 if four else 0b010, 0, len(data)) + _id(my_id) + bytes([0, lbe << 4 | fbe])
            + a + _dws(data))


def mem_read(my_id: int, addr: int, length: int, fbe: int, lbe: int, tag: int) -> bytes:
    four = addr >> 32 != 0
    a = addr.to_bytes(8, "big") if four else addr.to_bytes(4, "big")
    return (_dw0(0b001 if four else 0b000, 0, length) + _id(my_id)
            + bytes([tag & 7, lbe << 4 | fbe]) + a)


def interrupt_write(my_id: int, addr: int, data: int) -> bytes:
    """A 1-DW write of msg_data to msg_addr, all four bytes enabled."""
    return mem_write(my_id, addr, [data], 0xF, 0)


def message(my_id: int, code: int, routing: int) -> bytes:
    """A message without data: 4-DW header, TC 0, bytes 8 to 15 zero."""
    return _dw0(0b001, 0b10000 | routing, 0) + _id(my_id) + bytes([0, code]) + bytes(8)


def error_message(my_id: int, kind: int) -> bytes:
    return message(my_id, ERR_CODES[kind], ROUTE_TO_RC)


def pme_to_ack(my_id: int) -> bytes:
    return message(my_id, PME_TO_ACK, ROUTE_GATHER)


# ----------------------------------------------------------- completions

def lowest(be: int) -> int:
    for i in range(4):
        if be >> i & 1:
            return i
    return 0


def total_bytes(length: int, fbe: int, lbe: int) -> int:
    """T (COMPLETIONS.md, byte count and lower address)."""
    if length == 1:
        if fbe == 0:
            return 1
        highest = max(i for i in range(4) if fbe >> i & 1)
        return highest - lowest(fbe) + 1
    highest_l = max(i for i in range(4) if lbe >> i & 1)
    return 4 * length - lowest(fbe) - (3 - highest_l)


def parts(addr: int, length: int, fbe: int, lbe: int) -> list[tuple[int, int, int, int]]:
    """A read's completions as (first DW index, DWs, Byte Count, Lower
    Address), the fewest the rules allow (COMPLETIONS.md, splitting): each
    starts where the previous ended; the rest goes in one when it is at
    most 128 bytes, else it ends at the highest 64-byte-aligned address
    within 128 bytes. Byte Count 4096 is given as 4096 (sent as 0)."""
    a = addr & ~3
    t = total_bytes(length, fbe, lbe)
    first_byte = a + lowest(fbe)
    end = a + 4 * length
    out = []
    s = a
    while s < end:
        if end - s <= MAX_PAYLOAD:
            e = end
        else:
            e = s + MAX_PAYLOAD
            e -= e % RCB
        start_byte = first_byte if s == a else s
        out.append(((s - a) // 4, (e - s) // 4, first_byte + t - start_byte, start_byte & 0x7F))
        s = e
    return out


def masked(data, length: int, fbe: int, lbe: int) -> list[int]:
    """Disabled bytes as 0: the first DW by F, the last of a longer read by L."""
    out = list(data)

    def mask(be):
        return sum(0xFF << (8 * i) for i in range(4) if be >> i & 1)

    out[0] &= mask(fbe)
    if length > 1:
        out[-1] &= mask(lbe)
    return out


def completion(my_id: int, rid: int, tag: int, tc: int, attr: int, status: int, bc: int,
               la: int, data=None, locked: bool = False) -> bytes:
    """A completion (TLP.md, completions): BCM 0, Byte Count 4096 as 0."""
    n = len(data) if data else 0
    bc &= 0xFFF
    return (_dw0(0b010 if n else 0b000, 0b01011 if locked else 0b01010, n, tc, attr)
            + _id(my_id) + bytes([(status << 5) | (bc >> 8), bc & 0xFF, rid >> 8, rid & 0xFF,
                                  tag & 0xFF, la & 0x7F]) + (_dws(data) if n else b""))


def read_completions(my_id: int, h: dict, data) -> list[bytes]:
    """Every completion of a successful memory read whose header fields are
    ``h`` (receive reference names) and whose DWs, all bytes, are ``data``."""
    d = masked(data, h["length"], h["fbe"], h["lbe"])
    return [completion(my_id, h["rid"], h["tag"], h["tc"], h["attr"], SC, bc, la, d[i:i + n])
            for i, n, bc, la in parts(h["addr"], h["length"], h["fbe"], h["lbe"])]


def ur_completion(my_id: int, h: dict, memory_read: bool, locked: bool) -> bytes:
    """Unsupported Request: no data; a memory read's Byte Count T and Lower
    Address of its first enabled byte, else 4 and 0 (COMPLETIONS.md)."""
    if memory_read:
        bc = total_bytes(h["length"], h["fbe"], h["lbe"])
        la = ((h["addr"] & ~3) + lowest(h["fbe"])) & 0x7F
    else:
        bc, la = 4, 0
    return completion(my_id, h["rid"], h["tag"], h["tc"], h["attr"], UR, bc, la, locked=locked)


def cfg_completion(my_id: int, h: dict, data: int | None) -> bytes:
    """A configuration read (the enabled bytes of the DW) or write (no
    data): Successful, Byte Count 4, Lower Address 0."""
    d = masked([data], 1, h["fbe"], 0) if data is not None else None
    return completion(my_id, h["rid"], h["tag"], h["tc"], h["attr"], SC, 4, 0, d)


# ------------------------------------------------------------- credits

def credits(tlp: bytes) -> tuple[str, int]:
    """(class, data credits) of a TLP tl sends: 1 header credit each, data
    credits Length / 4 rounded up when it has data (HAS, credit release)."""
    fmt, typ = tlp[0] >> 5, tlp[0] & 0x1F
    with_data = bool(fmt & 2)
    length = ((tlp[2] & 3) << 8 | tlp[3]) or 1024
    if (typ == 0 and with_data) or typ >> 3 == 0b10:
        cls = "P"
    elif typ >> 1 == 0b0101:
        cls = "CPL"
    else:
        cls = "NP"
    return cls, ((length + 3) // 4 if with_data else 0)


def with_id(tlp: bytes, my_id: int) -> bytes:
    """The TLP with bytes 4 and 5 set to ``my_id``."""
    return tlp[:4] + _id(my_id) + tlp[6:]
