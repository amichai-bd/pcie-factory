"""Unit tests of the scrambler LFSR model; no simulator needed.

Run: python3 -m pytest src/top/lfsr/dv/models
"""

import pcie_top_lfsr_model as m

# Key bytes after a re-seed (scrambling 00h data), from the published
# Gen1/Gen2 scrambler sequence; independent of this model's code.
KAT = [0xFF, 0x17, 0xC0, 0x14, 0xB2, 0xE7, 0x02, 0x82,
       0x72, 0x6E, 0x28, 0xA6, 0xBE, 0x6D, 0xBF, 0x8D]


def test_known_answer_from_seed():
    assert m.keystream(len(KAT)) == KAT


def test_maximal_length():
    state, n = m.shift(m.SEED), 1
    while state != m.SEED:
        state, n = m.shift(state), n + 1
    assert n == 2**16 - 1


def test_cycle_model_controls():
    lfsr = m.Lfsr()
    assert lfsr.key == 0xFE  # THROWAWAY: deliberate break to prove ci-gate goes red
    lfsr.clock(rst=0, reseed=0, advance=0)
    assert lfsr.key == 0xFF  # hold
    lfsr.clock(rst=0, reseed=0, advance=1)
    assert lfsr.key == 0x17
    lfsr.clock(rst=0, reseed=1, advance=1)  # re-seed wins over advance
    assert lfsr.key == 0xFF
    lfsr.clock(rst=0, reseed=0, advance=1)
    lfsr.clock(rst=1, reseed=0, advance=1)  # reset wins
    assert lfsr.state == m.SEED
