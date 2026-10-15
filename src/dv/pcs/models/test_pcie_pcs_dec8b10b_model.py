"""Unit tests of the decoder cycle model; no simulator needed.

Run: python3 -m pytest src/dv/pcs/models
"""

import pcie_pcs_dec8b10b_model as m

# Published K28.5 codes in line order (a first), independent of the model.
K28_5_MINUS = "0011111010"
K28_5_PLUS = "1100000101"


def _code(line: str) -> int:
    return sum(int(c) << i for i, c in enumerate(line))


def test_k_edb_matches_rtl_package_value():
    assert m.K_EDB == 0xFE


def test_column_sizes():
    # 256 data and 12 K codes per column.
    for rd in (-1, 1):
        assert sum(m.valid_at(c, rd) for c in range(1024)) == 268


def test_comma_decodes_and_disparity_error():
    dec = m.Decoder()
    lock, gen = (1, 1), (0, 0)
    # Unknown RD after reset: either column, no disparity error.
    s0, s1 = dec.outputs((_code(K28_5_PLUS), _code(K28_5_MINUS)), lock, gen, 0)
    assert (s0.data, s0.k, s0.disp_err, s0.rd_in) == (0xBC, 1, 0, None)
    assert (s1.data, s1.k, s1.disp_err, s1.rd_in) == (0xBC, 1, 0, -1)
    dec.clock(0, 1, (_code(K28_5_PLUS), _code(K28_5_MINUS)), lock, gen, 0)
    # RD now positive: the RD- form of K28.5 again is a disparity error.
    s0, _ = dec.outputs((_code(K28_5_MINUS), m.PROBE), lock, gen, 0)
    assert (s0.data, s0.k, s0.code_err, s0.disp_err) == (0xBC, 1, 0, 1)


def test_code_error_is_edb():
    dec = m.Decoder()
    s0, _ = dec.outputs((0, 0x3FF), (1, 1), (0, 0), 0)
    assert (s0.data, s0.k, s0.code_err, s0.disp_err) == (m.K_EDB, 1, 1, 0)


def test_rd_unknown_rules():
    lock, gen = (1, 1), (0, 0)
    word = (m.PROBE, m.PROBE)
    dec = m.Decoder()
    dec.clock(0, 1, word, lock, gen, 0)
    assert dec.known
    # A cycle without a word keeps the RD; a polarity change drops it.
    dec.clock(0, 0, word, lock, gen, 0)
    assert dec.known
    dec.clock(0, 0, word, lock, gen, 1)
    assert not dec.known
    dec.clock(0, 1, word, lock, gen, 1)
    assert dec.known
    # A lock-generation change makes lane 0 start unknown.
    s0, s1 = dec.outputs(word, lock, (1, 1), 1)
    assert s0.rd_in is None and s1.rd_in is not None
    # An unlocked lane 0 makes lane 1 start unknown.
    s0, s1 = dec.outputs(word, (0, 1), gen, 1)
    assert s0.rd_in is None and s1.rd_in is None
    dec.clock(1, 0, word, lock, gen, 1)
    assert not dec.known


def test_setter_and_probe():
    for now in (-1, 1):
        for want in (-1, 1):
            c = m.setter(now, want)
            assert m.valid_at(c, now)
    assert m.valid_at(m.PROBE, -1) and not m.valid_at(m.PROBE, 1)
