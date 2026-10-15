"""Unit tests of the encoder cycle model; no simulator needed.

Run: python3 -m pytest src/dv/pcs/models
"""

import pcie_pcs_enc8b10b_model as m

# Published anchors in line order (bit a first), from wiki/src/arch/pcs/8B10B.md
# and the widely published 8b/10b tables; independent of the model's code.
ANCHORS = {  # (byte, k, rd): line-order code
    (0xBC, 1, -1): "0011111010", (0xBC, 1, 1): "1100000101",   # K28.5
    (0x1C, 1, -1): "0011110100", (0x1C, 1, 1): "1100001011",   # K28.0
    (0xB5, 0, -1): "1010101010", (0xB5, 0, 1): "1010101010",   # D21.5
    (0x4A, 0, -1): "0101010101", (0x4A, 0, 1): "0101010101",   # D10.2
    (0x00, 0, -1): "1001110100", (0x00, 0, 1): "0110001011",   # D0.0
    # A7 rule: D17.7 uses A7 at RD- only, D11.7 at RD+ only; the other RD
    # of each is the primary P7 form.
    (0xF1, 0, -1): "1000110111", (0xF1, 0, 1): "1000110001",   # D17.7
    (0xEB, 0, -1): "1101001110", (0xEB, 0, 1): "1101001000",   # D11.7
    # Kx.7 always uses A7: K23.7, and K28.7 (both comma forms).
    (0xF7, 1, -1): "1110101000", (0xF7, 1, 1): "0001010111",   # K23.7
    (0xFC, 1, -1): "0011111000", (0xFC, 1, 1): "1100000111",   # K28.7
}


def line(code: int) -> str:
    return "".join(str((code >> i) & 1) for i in range(10))


def test_anchors_in_both_lanes():
    for (b, k, rd), want in ANCHORS.items():
        out, rd_mid, _ = m.encode_word(rd, b, k, 0)
        assert line(out & 0x3FF) == want
        # lane 1 at the same RD: lane 0 D21.5 is balanced and keeps RD
        out, _, _ = m.encode_word(rd, 0xB5 | (b << 8), k << 1, 0)
        assert line(out >> 10) == want


def test_compliance_forces_negative_rd():
    out, _, _ = m.encode_word(1, 0xBC, 1, 1)
    assert line(out & 0x3FF) == "0011111010"


def test_unsupported_k_is_edb():
    edb_minus = m.encode_word(-1, m.K_EDB, 1, 0)[0] & 0x3FF
    for b in range(256):
        if b not in m.VALID_K:
            assert m.encode_word(-1, b, 1, 0)[0] & 0x3FF == edb_minus


def test_pipeline_latency_hold_and_reset():
    e = m.Encoder()
    e.clock(0, 1, 0x00BC, 0b01, 0)          # K28.5 flips RD to +
    assert e.valid_o == 0
    e.clock(0, 0, 0, 0, 0)
    assert e.valid_o == 1 and e.rd == 1
    e.clock(0, 0, 0, 0, 0)                  # idle: RD holds
    assert e.valid_o == 0 and e.rd == 1
    e.clock(1, 1, 0, 0, 0)                  # reset: RD-, nothing in flight
    e.clock(0, 0, 0, 0, 0)
    assert e.valid_o == 0 and e.rd == -1
