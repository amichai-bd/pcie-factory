"""Unit tests of the link partner's CRCs (wiki/src/dv/dll/PARTNER.md#crcs).

Every expected value here is either a published generic catalogue check
value (the CRC of ASCII "123456789"), the standard library's CRC-32, or a
second implementation in this package (long division). No value is taken
from the PCIe Base Spec or from the dll architecture model. The HAS page
examples are compared separately, as an agreement check, in
test_pcie_dll_partner.py.

Run: python3 -m pytest src/dv/dll/models
"""

import random
import zlib

import pytest

import pcie_dll_partner_crc as c

CHECK = b"123456789"

# Generic catalogue entries: (name, width, poly, init, refin, refout, xorout,
# check value of "123456789"). Several conventions, so a bug in reflection,
# seeding or the final XOR fails at least one of them.
CATALOGUE = [
    ("CRC-32/ISO-HDLC", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xCBF43926),
    ("CRC-32/BZIP2", 32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0xFFFFFFFF, 0xFC891918),
    ("CRC-32/MPEG-2", 32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0x00000000, 0x0376E6E7),
    ("CRC-16/IBM-3740", 16, 0x1021, 0xFFFF, False, False, 0x0000, 0x29B1),
    ("CRC-16/KERMIT", 16, 0x1021, 0x0000, True, True, 0x0000, 0x2189),
    ("CRC-16/ARC", 16, 0x8005, 0x0000, True, True, 0x0000, 0xBB3D),
    ("CRC-16/IBM-SDLC", 16, 0x1021, 0xFFFF, True, True, 0xFFFF, 0x906E),
]


@pytest.mark.parametrize("name,width,poly,init,refin,refout,xorout,check", CATALOGUE)
def test_engine_catalogue(name, width, poly, init, refin, refout, xorout, check):
    got = c.crc(CHECK, width, poly, init, refin, refout, xorout)
    assert got == check, f"{name}: expected {check:#x}, got {got:#x}"


def test_polynomials_from_terms():
    """The polynomial constants equal the exponent lists of the HAS pages."""
    def poly(*exps):
        return sum(1 << e for e in exps)
    assert c.CRC16_POLY == poly(12, 3, 1, 0)
    assert c.LCRC_POLY == poly(26, 23, 22, 16, 12, 11, 10, 8, 7, 5, 4, 2, 1, 0)


def test_lcrc_is_stdlib_crc32():
    """The LCRC under this reading is the CRC-32 of the ISO-HDLC entry, so
    the standard library's independent implementation must agree."""
    rng = random.Random(1)
    for n in range(0, 160, 7):
        data = bytes(rng.randrange(256) for _ in range(n))
        assert c.lcrc_bytes(data) == zlib.crc32(data).to_bytes(4, "little"), n


@pytest.mark.parametrize("width,poly,func", [(16, c.CRC16_POLY, c.crc16_bytes),
                                             (32, c.LCRC_POLY, c.lcrc_bytes)])
def test_register_matches_long_division(width, poly, func):
    rng = random.Random(width)
    for n in [4, 5, 6, 14, 18, 64, 146]:
        for _ in range(20):
            data = bytes(rng.randrange(256) for _ in range(n))
            want = c.to_line(c.crc_long_division(data, width, poly), width)
            assert func(data) == want, (n, data.hex())


def test_line_mapping_top_coefficient_first():
    """The x^(w-1) coefficient is the first CRC bit on the line: byte 0
    bit 0; x^0 is the last: last byte bit 7."""
    assert c.to_line(1 << 15, 16) == bytes([0x01, 0x00])
    assert c.to_line(1, 16) == bytes([0x00, 0x80])
    assert c.to_line(1 << 31, 32) == bytes([0x01, 0, 0, 0])
    assert c.to_line(1 << 24, 32) == bytes([0x80, 0, 0, 0])


@pytest.mark.parametrize("func,n", [(c.crc16_bytes, 4), (c.lcrc_bytes, 18)])
def test_constant_residue(func, n):
    """Data followed by its own CRC always leaves the same remainder (a
    property of every CRC with these parameters), whatever the data."""
    rng = random.Random(n)
    width = 8 * len(func(bytes(n)))
    poly = c.CRC16_POLY if width == 16 else c.LCRC_POLY
    residues = set()
    for _ in range(30):
        data = bytes(rng.randrange(256) for _ in range(n))
        residues.add(c.crc(data + func(data), width, poly, (1 << width) - 1,
                           True, True, 0))
    assert len(residues) == 1


def test_every_single_bit_error_detected():
    rng = random.Random(3)
    for _ in range(10):
        body = bytes(rng.randrange(256) for _ in range(4))
        pkt = body + c.crc16_bytes(body)
        for i in range(48):
            bad = bytearray(pkt)
            bad[i // 8] ^= 1 << (i % 8)
            assert c.crc16_bytes(bytes(bad[:4])) != bytes(bad[4:]), i


def test_long_division_rejects_short_message():
    with pytest.raises(ValueError):
        c.crc_long_division(b"\x00", 16, c.CRC16_POLY)
