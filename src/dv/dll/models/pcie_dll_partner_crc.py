"""CRCs of the independent DLL link partner (wiki/src/dv/dll/PARTNER.md).

Written for the partner only, from the polynomial definitions on the
wiki/src/arch/dll/DLLP.md#crc-16 and wiki/src/arch/dll/RETRY.md#lcrc pages
and the generic CRC parameter model (width, polynomial, initial value,
input and output reflection, final XOR). It shares no code with, and was
written without reading, the dll architecture model (M2 Q3).

Two independent statements of the same CRC:

- ``crc``: a generic bit-serial shift register, checked in the unit tests
  against published catalogue check values (the CRC of the ASCII string
  "123456789") for several well-known CRCs, so the engine is known good
  before the link CRCs use it.
- ``crc_long_division``: textbook polynomial division over GF(2) of the
  message read in line order, used to cross-check the register form.

Line mapping (this partner's reading, written down in PARTNER.md): the
bytes of a packet go on the line in order, each byte bit 0 first, so the
first bit on the line is the highest-order message coefficient. The CRC
remainder is complemented and sent highest-order coefficient first, in the
same bit-0-first byte order; that makes CRC byte 0 bit 0 the remainder's
x^(w-1) coefficient. With a reflected register that is simply the register
value, low byte first.
"""

from __future__ import annotations

# Polynomials without the x^w term.
CRC16_POLY = 0x100B        # x^16 + x^12 + x^3 + x + 1
LCRC_POLY = 0x04C11DB7     # x^32 + x^26 + x^23 + x^22 + x^16 + x^12 + x^11
                           # + x^10 + x^8 + x^7 + x^5 + x^4 + x^2 + x + 1


def reflect(value: int, width: int) -> int:
    """``value`` with its ``width`` low bits in reverse order."""
    out = 0
    for i in range(width):
        if value >> i & 1:
            out |= 1 << (width - 1 - i)
    return out


def crc(data: bytes, width: int, poly: int, init: int, refin: bool,
        refout: bool, xorout: int) -> int:
    """Generic bit-serial CRC in the usual parameter model.

    The register holds the remainder MSB-first (bit ``width - 1`` is the
    x^(w-1) coefficient). Each message bit, taken bit 0 first when
    ``refin`` and bit 7 first otherwise, is XORed into the top bit; a 1
    shifted out of the top subtracts (XORs) the polynomial."""
    mask = (1 << width) - 1
    top = 1 << (width - 1)
    reg = init & mask
    for byte in data:
        for i in range(8):
            bit = (byte >> i) & 1 if refin else (byte >> (7 - i)) & 1
            fb = bool(reg & top) ^ bit
            reg = (reg << 1) & mask
            if fb:
                reg ^= poly
    if refout:
        reg = reflect(reg, width)
    return reg ^ xorout


def line_bits(data: bytes) -> list[int]:
    """The bits of ``data`` in line order: bytes in order, bit 0 first."""
    return [(b >> i) & 1 for b in data for i in range(8)]


def crc_long_division(data: bytes, width: int, poly: int) -> int:
    """The link CRC by polynomial long division, independent of ``crc``.

    M(x) is the message in line order, its first bit the highest power.
    The all-ones seed is the same as complementing the first ``width``
    message bits. R(x) = M'(x) * x^w mod G(x); the CRC is R complemented.
    Returns the complemented remainder MSB-first (bit w-1 is x^(w-1))."""
    bits = line_bits(data)
    for i in range(min(width, len(bits))):
        bits[i] ^= 1
    if len(bits) < width:
        # Shorter than the register: the seed's tail lands in the x^w shift.
        raise ValueError("message shorter than the CRC width")
    num = 0
    for b in bits:
        num = (num << 1) | b
    num <<= width
    gen = (1 << width) | poly
    glen = width + 1
    while num.bit_length() >= glen:
        num ^= gen << (num.bit_length() - glen)
    return num ^ ((1 << width) - 1)


def to_line(remainder_msb_first: int, width: int) -> bytes:
    """Map a complemented remainder (bit w-1 = x^(w-1)) onto CRC bytes in
    line order: byte k bit 0 carries coefficient x^(w-1-8k), bit 7 carries
    x^(w-8-8k)."""
    out = []
    for k in range(width // 8):
        byte = 0
        for i in range(8):
            if remainder_msb_first >> (width - 1 - 8 * k - i) & 1:
                byte |= 1 << i
        out.append(byte)
    return bytes(out)


def crc16_bytes(body: bytes) -> bytes:
    """The two CRC bytes of a DLLP body (4 bytes), in line order."""
    value = crc(body, 16, CRC16_POLY, 0xFFFF, True, True, 0xFFFF)
    return value.to_bytes(2, "little")


def lcrc_bytes(data: bytes) -> bytes:
    """The four LCRC bytes over a TLP's sequence-number bytes and the TLP,
    in line order."""
    value = crc(data, 32, LCRC_POLY, 0xFFFFFFFF, True, True, 0xFFFFFFFF)
    return value.to_bytes(4, "little")
