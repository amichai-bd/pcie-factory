"""Unit tests of the pcie_top model; no simulator needed.

Run: python3 -m pytest src/top/dv/models
"""

import pcie_top_model as m


def test_zero_stream_is_keystream():
    assert m.scramble([0x00] * 4) == [0xFF, 0x17, 0xC0, 0x14]


def test_descramble_is_scramble():
    data = [0x00, 0x5A, 0xA5, 0xFF, 0x12, 0x34]
    assert m.scramble(m.scramble(data)) == data


def test_bubble_does_not_advance_and_reseed_after_byte():
    s = m.Scrambler()
    s.clock(rst=0, valid=1, data=0x00, reseed=0)
    assert (s.valid_o, s.data_o) == (1, 0xFF)
    s.clock(rst=0, valid=0, data=0x00, reseed=0)
    assert s.valid_o == 0
    s.clock(rst=0, valid=1, data=0x00, reseed=1)  # scrambled before re-seed
    assert s.data_o == 0x17
    s.clock(rst=0, valid=1, data=0x00, reseed=0)
    assert s.data_o == 0xFF
