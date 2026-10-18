"""Unit tests of the transmit reference (no simulator): the examples of
the COMPLETIONS.md and TLP.md pages."""

import pcie_tl_tx_ref as tx


def sizes(addr, length, fbe=0xF, lbe=0xF):
    return [4 * n for _, n, _, _ in tx.parts(addr, length, fbe, lbe)]


def test_split_table():
    # COMPLETIONS.md, splitting: the table's reads.
    assert sizes(0x1000, 32) == [128]
    assert sizes(0x1004, 32) == [128]
    assert sizes(0x1004, 128) == [124, 128, 128, 128, 4]
    assert sizes(0x0, 1024) == [128] * 32
    assert sizes(0x1004, 64) == [124, 128, 4]
    assert sizes(0x7C, 2) == [8]


def test_split_ends_on_boundary():
    for off in range(0, 64, 4):
        for length in (1, 31, 32, 33, 64, 100, 1024 - off // 4):
            ps = tx.parts(0x2000 + off, length, 0xF, 0xF)
            assert sum(n for _, n, _, _ in ps) == length
            for i, n, _, _ in ps[:-1]:
                assert (0x2000 + off + 4 * (i + n)) % 64 == 0 and 4 * n <= 128
            assert 4 * ps[-1][1] <= 128


def test_byte_count_and_lower_address():
    assert tx.total_bytes(1, 0b1001, 0) == 4
    assert tx.total_bytes(1, 0, 0) == 1
    assert tx.total_bytes(1, 0b0100, 0) == 1
    assert tx.total_bytes(2, 0b1100, 0b0011) == 4
    assert tx.total_bytes(1024, 0xF, 0xF) == 4096
    # 512 bytes at offset 4 with F 1110, L 0111: T 510, B offset 5.
    ps = tx.parts(0x1004, 128, 0b1110, 0b0111)
    assert ps[0][2:] == (510, 0x05)
    assert ps[1][2:] == (510 - (128 - 4 - 1), 0x00)
    assert ps[-1][2:] == (3, 0x00)
    # Byte Count 4096 is sent as 0.
    c = tx.read_completions(0x0100, {"addr": 0, "length": 1024, "fbe": 0xF, "lbe": 0xF,
                                     "rid": 1, "tag": 2, "tc": 0, "attr": 0}, [0] * 1024)
    assert c[0][6] & 0xF == 0 and c[0][7] == 0 and len(c) == 32


def test_masked_data():
    h = {"addr": 0x10, "length": 2, "fbe": 0b1100, "lbe": 0b0011, "rid": 0x1234, "tag": 0x56,
         "tc": 3, "attr": 1}
    (c,) = tx.read_completions(0x0108, h, [0xAABBCCDD, 0x11223344])
    assert c[12:16] == bytes([0, 0, 0xBB, 0xAA]) and c[16:20] == bytes([0x44, 0x33, 0, 0])
    assert c[1] >> 4 == 3 and (c[2] >> 4) & 3 == 1 and c[4:6] == bytes([1, 8])


def test_requests_and_messages():
    w = tx.mem_write(0x0100, 0x1_0000_0010, [1, 2])
    assert w[0] == 0x60 and w[3] == 2 and len(w) == 16 + 8
    r = tx.mem_read(0x0100, 0x40, 4, 0xF, 0xF, 5)
    assert r[0] == 0x00 and r[6] == 5 and len(r) == 12
    assert tx.pme_to_ack(0)[0] == 0x35 and tx.pme_to_ack(0)[7] == 0x1B
    assert tx.error_message(0, 3)[7] == 0x33 and tx.error_message(0, 1)[0] == 0x30
    assert tx.credits(w) == ("P", 1) and tx.credits(r) == ("NP", 0)
    assert tx.credits(tx.pme_to_ack(0)) == ("P", 0)


def test_ur_completion():
    h = {"addr": 0x87, "length": 2, "fbe": 0b1110, "lbe": 0b0001, "rid": 0x0203, "tag": 9,
         "tc": 0, "attr": 0}
    c = tx.ur_completion(0, h, True, True)
    assert c[0] == 0x0B and c[6] >> 5 == tx.UR and c[7] == 4 and c[11] == 0x05
    c = tx.ur_completion(0, h, False, False)
    assert c[0] == 0x0A and c[7] == 4 and c[11] == 0
