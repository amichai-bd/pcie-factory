"""Unit tests of the pcie_top model; no simulator needed.

Run: python3 -m pytest src/top/dv/models
"""

import random
import sys
from pathlib import Path

import pcie_top_model as m

# DV may import the IP's architecture model as a second reference; arch never
# imports DV (wiki/bkm/arch.md#dv-and-arch).
_ARCH = Path(__file__).resolve().parents[2] / "arch"
if str(_ARCH) not in sys.path:
    sys.path.append(str(_ARCH))

import pcie_top_arch  # noqa: E402


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


def test_agrees_with_the_arch_model():
    rng = random.Random(7)
    dv, arch = m.Scrambler(), pcie_top_arch.Scrambler()
    for _ in range(3000):
        rst = int(rng.random() < 0.01)
        valid = int(rng.random() < 0.7)
        reseed = int(rng.random() < 0.05)
        data = rng.randrange(256)
        dv.clock(rst=rst, valid=valid, data=data, reseed=reseed)
        valid_o, data_o = arch.cycle(valid=valid, data=data, reseed=reseed, rst=rst)
        assert dv.valid_o == valid_o
        if valid_o:
            assert dv.data_o == data_o
