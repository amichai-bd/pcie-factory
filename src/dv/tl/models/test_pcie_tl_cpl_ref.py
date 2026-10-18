"""Unit tests of the completion-side reference (pcie_tl_cpl_ref.py): the
COMPLETIONS.md rules on small cases, no simulator."""

import pytest

import pcie_tl_cpl_ref as C
import pcie_tl_rx_ref as R

MY = 0x0308                     # bus 3, device 1, function 0


def cpl(tag, bc, la, data=None, status=C.SC, rid=MY, ep=0, locked=False, digest=False):
    t = R.completion(status=status, byte_count=bc, rid=rid, tag=tag, lower_addr=la, data=data,
                     locked=locked)
    if ep:
        t = t[:2] + bytes([t[2] | 0x40]) + t[3:]
    return R.with_digest(t) if digest else t


def sent_read(tag=1, addr=0x1004, length=32, fbe=0xF, lbe=0xF):
    reads = C.Reads(1024)
    reads.take(tag, addr, length, fbe, lbe)
    reads.sent(tag, 10)
    return reads


def test_read_bytes():
    assert C.read_bytes(1, 0b1001, 0) == 4
    assert C.read_bytes(1, 0, 0) == 1
    assert C.read_bytes(1, 0b0110, 0) == 2
    assert C.read_bytes(32, 0xF, 0xF) == 128
    assert C.read_bytes(3, 0b1100, 0b0001) == 7


def test_legal_completions_split():
    # 128 bytes at offset 4, cut at both 64-byte boundaries inside it.
    parts = C.legal_completions(0x1004, 32, 0xF, 0xF, cuts=(0x1040, 0x1080))
    assert [(p["n"], p["bc"], p["la"]) for p in parts] == [(15, 128, 0x04), (16, 68, 0x40),
                                                            (1, 4, 0x00)]
    # A cut not on a boundary or outside the read is ignored.
    assert len(C.legal_completions(0x1004, 32, 0xF, 0xF, cuts=(0x1044, 0x2000))) == 1


def test_split_read_delivered_in_parts():
    reads = sent_read()
    parts = C.legal_completions(0x1004, 32, 0xF, 0xF, cuts=(0x1040, 0x1080))
    outs = []
    for p in parts:
        v = reads.judge(cpl(1, p["bc"], p["la"], list(range(p["n"]))), MY)
        outs.append((v.err, v.packet.done, len(v.packet.data)))
    assert outs == [(None, False, 15), (None, False, 16), (None, True, 1)]
    assert not reads.pending()


def test_first_byte_and_last_byte_enables():
    reads = sent_read(addr=0x2038, length=3, fbe=0b1100, lbe=0b0001)
    # 7 bytes from 0x203A; one completion of 3 DWs.
    v = reads.judge(cpl(1, 7, 0x3A, [1, 2, 3]), MY)
    assert v.err is None and v.packet.done and v.case == "last"


def test_zero_length_read():
    reads = sent_read(addr=0x100, length=1, fbe=0, lbe=0)
    v = reads.judge(cpl(1, 1, 0x00, [0xAB]), MY)
    assert v.packet.done and v.packet.data == [0xAB]


@pytest.mark.parametrize("bc, la, n, case", [
    (127, 0x04, 15, "mal_byte_count"),
    (128, 0x08, 15, "mal_lower_address"),
    (128, 0x04, 14, "mal_boundary"),
    (128, 0x04, 33, "mal_past_end"),
])
def test_malformed_keep_waiting(bc, la, n, case):
    reads = sent_read()
    v = reads.judge(cpl(1, bc, la, list(range(n))), MY)
    assert (v.err, v.packet, v.case) == (C.MALFORMED, None, case)
    v = reads.judge(cpl(1, 128, 0x04, list(range(32))), MY)
    assert v.packet.done


def test_no_data_and_crs_malformed():
    reads = sent_read()
    assert reads.judge(cpl(1, 128, 0x04), MY).case == "mal_no_data"
    assert reads.judge(cpl(1, 128, 0x04, status=C.CRS), MY).case == "crs"
    assert reads.pending()


@pytest.mark.parametrize("status, rc, err", [(C.UR, C.RC_UR, C.RX_UR), (C.CA, C.RC_CA, C.RX_CA),
                                             (0b111, C.RC_UR, C.RX_UR)])
def test_error_status_ends_read(status, rc, err):
    reads = sent_read()
    v = reads.judge(cpl(1, 128, 0x04, status=status), MY)
    assert v.err == err and v.packet == C.Packet(1, rc, True) and not reads.pending()


def test_poisoned_middle_then_failed_end():
    reads = sent_read()
    parts = C.legal_completions(0x1004, 32, 0xF, 0xF, cuts=(0x1040, 0x1080))
    p = parts[0]
    v = reads.judge(cpl(1, p["bc"], p["la"], [0] * p["n"], ep=1), MY)
    assert (v.err, v.packet) == (C.POISONED_CPL, None)
    p = parts[1]
    assert reads.judge(cpl(1, p["bc"], p["la"], [0] * p["n"]), MY).packet is None
    p = parts[2]
    v = reads.judge(cpl(1, p["bc"], p["la"], [0] * p["n"]), MY)
    assert v.packet == C.Packet(1, C.RC_FAILED, True) and v.err is None


def test_poisoned_last():
    reads = sent_read()
    v = reads.judge(cpl(1, 128, 0x04, [0] * 32, ep=1), MY)
    assert v.err == C.POISONED_CPL and v.packet.status == C.RC_FAILED


def test_unexpected():
    reads = C.Reads(1024)
    reads.take(2, 0x40, 1, 0xF, 0)
    assert reads.judge(cpl(2, 4, 0x40, [1]), MY).case == "unexpected_unsent"
    reads.sent(2, 5)
    assert reads.judge(cpl(2, 4, 0x40, [1], rid=MY + 8), MY).case == "unexpected_rid"
    assert reads.judge(cpl(10, 4, 0x40, [1]), MY).case == "unexpected_tag_high"
    assert reads.judge(cpl(3, 4, 0x40, [1]), MY).case == "unexpected_no_read"
    assert reads.judge(cpl(2, 4, 0x40, [1], locked=True), MY).case == "unexpected_locked"
    assert reads.judge(cpl(2, 4, 0x40, [1]), MY).packet.done


def test_digest_is_not_data():
    reads = sent_read(addr=0x40, length=2)
    v = reads.judge(cpl(1, 8, 0x40, [5, 6], digest=True), MY)
    assert v.packet.data == [5, 6] and v.packet.done


def test_timeout_window():
    reads = C.Reads(1024)
    assert reads.window() == (2048, 2176)
    assert C.Reads(1).window() == (2097152, 2228224)
    reads.take(0, 0, 1, 0xF, 0)
    reads.sent(0, 100)
    assert reads.due(100 + 2048, -10) == []
    assert reads.due(100 + 2049, -10) == [0]
    assert reads.overdue(100 + 2175, -10) == []
    assert reads.overdue(100 + 2176, -10) == [0]
    # The disable seen at cycle 3000 restarts the count from 3001.
    assert reads.due(3001 + 2049, 3000) == [0]
    assert reads.due(3001 + 2048, 3000) == []
    assert reads.timeout(0) == C.Packet(0, C.RC_FAILED, True)
    assert not reads.pending()
