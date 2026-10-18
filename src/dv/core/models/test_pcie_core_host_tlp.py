"""Unit tests of the host model's TLP codec (no simulator).

The byte vectors are worked out by hand from the field positions of the
Base Spec headers [cite: PCIe Base Spec, rev 3.0, §2.2.1] and the TLP page
of the tl HAS, not produced by the codec.
"""

import pytest

from pcie_core_host_tlp import (
    COMPLETION,
    CPL_UR,
    NON_POSTED,
    POSTED,
    Tlp,
    TlpError,
    bdf_id,
    byte_enables,
    cfg_read,
    cfg_write,
    completion,
    from_beats,
    id_bdf,
    mem_read,
    mem_write,
    message,
    to_beats,
)


def test_bdf_id_round_trip():
    assert bdf_id(1, 2, 3) == 0x0113
    assert id_bdf(0x0113) == (1, 2, 3)
    with pytest.raises(ValueError):
        bdf_id(0, 32, 0)


def test_mem_read_3dw_bytes():
    t = mem_read(0x0000, 5, 0x8000_1004, 2, 0xF, 0xF)
    assert t.encode() == bytes([0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x05, 0xFF,
                                0x80, 0x00, 0x10, 0x04])
    assert t.kind == "MRd" and t.credit_cost() == (NON_POSTED, 1, 0)


def test_mem_read_4dw_above_4g():
    t = mem_read(0x0100, 7, 0x1_2345_6788, 1, 0x3, 0)
    raw = t.encode()
    assert raw[0] == 0x20 and len(raw) == 16
    assert raw[8:16] == bytes([0x00, 0x00, 0x00, 0x01, 0x23, 0x45, 0x67, 0x88])
    back = Tlp.decode(raw)
    assert back.address == 0x1_2345_6788 and back.requester_id == 0x0100 and back.tag == 7


def test_mem_write_bytes_and_credits():
    data = bytes(range(16))
    t = mem_write(0x0000, 0x8000_0000, data, 0xF, 0xF)
    raw = t.encode()
    assert raw[:4] == bytes([0x40, 0x00, 0x00, 0x04])
    assert raw[12:] == data
    assert t.credit_cost() == (POSTED, 1, 1)
    big = mem_write(0, 0, bytes(128), 0xF, 0xF)
    assert big.credit_cost() == (POSTED, 1, 8)


def test_cfg_write_fields():
    t = cfg_write(0x0000, 3, 1, 2, 0, 0x104, 0x11223344, 0b0011)
    raw = t.encode()
    assert raw[0] == 0x44  # Fmt 010, Type 00100
    assert raw[3] == 1 and raw[7] == 0x03  # Length 1, Last BE 0, First BE 0011
    assert raw[8] == 1 and raw[9] == (2 << 3) and raw[10] == 0x1 and raw[11] == 0x04
    assert raw[12:16] == bytes([0x44, 0x33, 0x22, 0x11])  # register bits [7:0] first
    back = Tlp.decode(raw)
    assert (back.cfg_bus, back.cfg_dev, back.cfg_fn, back.cfg_reg) == (1, 2, 0, 0x104)
    assert back.kind == "CfgWr0" and back.credit_cost() == (NON_POSTED, 1, 1)


def test_cfg_read_type1_kind():
    t = cfg_read(0, 0, 2, 0, 0, 0x0, type1=True)
    assert t.encode()[0] == 0x05 and Tlp.decode(t.encode()).kind == "CfgRd1"


def test_completion_fields():
    t = completion(0x0100, 0x0000, 9, byte_count=4096, lower_addr=0x44, data=bytes(8))
    raw = t.encode()
    assert raw[0] == 0x4A  # CplD
    assert raw[4:6] == bytes([0x01, 0x00])
    assert raw[6] == 0x00 and raw[7] == 0x00  # status SC, Byte Count 4096 as 0
    assert raw[10] == 9 and raw[11] == 0x44
    back = Tlp.decode(raw)
    assert back.byte_count_value == 4096 and back.credit_cost() == (COMPLETION, 1, 1)
    ur = completion(0x0100, 0, 1, CPL_UR, byte_count=0x123)
    assert ur.encode()[6] == 0x21 and ur.encode()[7] == 0x23 and ur.kind == "Cpl"


def test_message_fields():
    t = message(0x0000, 0x50, 0b100, data=bytes([0x0A, 0x01, 0, 0]))
    raw = t.encode()
    assert raw[0] == 0x74  # Fmt 011, Type 10100
    assert raw[7] == 0x50 and len(raw) == 20
    back = Tlp.decode(raw)
    assert back.kind == "MsgD" and back.routing == 0b100 and back.credit_class == POSTED


def test_digest_kept_and_length_checked():
    t = mem_write(0, 0x1000, bytes(4), 0xF, 0)
    t.td, t.digest = 1, b"\x01\x02\x03\x04"
    back = Tlp.decode(t.encode())
    assert back.data == bytes(4) and back.digest == b"\x01\x02\x03\x04"


@pytest.mark.parametrize("raw, why", [
    (bytes(8), "short"),
    (bytes(13), "not whole DWs"),
    (bytes([0x40, 0, 0, 2]) + bytes(8) + bytes(4), "data shorter than Length"),
    (bytes([0x80, 0, 0, 0]) + bytes(8), "prefix"),
    (bytes([0x1F, 0, 0, 0]) + bytes(8), "unknown type"),
])
def test_decode_rejects(raw, why):
    with pytest.raises(TlpError):
        Tlp.decode(raw)


@pytest.mark.parametrize("addr, n, want", [
    (0x1000, 4, (0x1000, 1, 0xF, 0x0)),
    (0x1001, 2, (0x1000, 1, 0x6, 0x0)),
    (0x1003, 2, (0x1000, 2, 0x8, 0x1)),
    (0x1004, 512, (0x1004, 128, 0xF, 0xF)),
    (0x1002, 7, (0x1000, 3, 0xC, 0x1)),
    (0x1000, 0, (0x1000, 1, 0x0, 0x0)),
])
def test_byte_enables(addr, n, want):
    assert byte_enables(addr, n) == want


def test_beats_put_byte0_in_low_bits():
    raw = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77])
    assert to_beats(raw) == [0x33221100, 0x77665544]
    assert from_beats(to_beats(raw)) == raw
