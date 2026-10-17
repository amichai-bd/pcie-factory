"""CRC-16 for DLLPs and the 32-bit LCRC for TLPs, from their polynomial
definitions (wiki/src/arch/dll/DLLP.md#crc-16, wiki/src/arch/dll/RETRY.md#lcrc).

Both are computed bit-serially: a shift register seeded with all ones, the
bytes fed in order and each byte bit 0 first, the remainder complemented,
and the complemented remainder's highest bit sent first. Each byte of the
result therefore holds eight remainder bits in reverse order
[cite: PCIe Base Spec, rev 3.0, §3.4.1] [cite: PCIe Base Spec, rev 3.0, §3.5.2.1].
Python standard library only.
"""

from __future__ import annotations

CRC16_POLY = 0x100B        # x^16 + x^12 + x^3 + x + 1 [cite: PCIe Base Spec, rev 3.0, §3.4.1]
LCRC_POLY = 0x04C11DB7     # the 32-bit polynomial [cite: PCIe Base Spec, rev 3.0, §3.5.2.1]


def remainder(data: bytes, poly: int, width: int) -> int:
    """The register after feeding ``data`` bit-serially, bit 0 of each byte
    first, into a ``width``-bit register seeded with all ones; bit
    ``width - 1`` holds the highest-order coefficient."""
    top = 1 << (width - 1)
    mask = (1 << width) - 1
    reg = mask
    for byte in data:
        for i in range(8):
            feedback = bool(reg & top) ^ bool((byte >> i) & 1)
            reg = (reg << 1) & mask
            if feedback:
                reg ^= poly
    return reg


def _reverse8(b: int) -> int:
    return int(f"{b:08b}"[::-1], 2)


def to_bytes(reg: int, width: int) -> bytes:
    """The check bytes on the line: the complemented register, highest bit
    first, so byte k holds register bits ``width-1-8k`` down to
    ``width-8-8k`` with the highest of them in bit 0."""
    inv = ~reg & ((1 << width) - 1)
    out = []
    for k in range(width // 8):
        out.append(_reverse8((inv >> (width - 8 - 8 * k)) & 0xFF))
    return bytes(out)


def crc16(body: bytes) -> bytes:
    """The two CRC bytes of a DLLP whose first four bytes are ``body``."""
    if len(body) != 4:
        raise ValueError("a DLLP body is 4 bytes")
    return to_bytes(remainder(body, CRC16_POLY, 16), 16)


def lcrc(data: bytes) -> bytes:
    """The four LCRC bytes over ``data`` (the 2 sequence-number bytes and
    the TLP)."""
    return to_bytes(remainder(data, LCRC_POLY, 32), 32)


def invert(check: bytes) -> bytes:
    """Every bit inverted: the LCRC of a nullified TLP."""
    return bytes(b ^ 0xFF for b in check)
