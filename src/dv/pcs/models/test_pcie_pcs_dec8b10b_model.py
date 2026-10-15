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


# Classic 8b/10b tables, typed from the widely published 5b/6b and 3b/4b
# tables (abcdei and fghj, line order), independent of the architecture
# model's rule-based code: (RD- form, RD+ form) per sub-block value.
SIX = [
    ("100111", "011000"), ("011101", "100010"), ("101101", "010010"), ("110001", "110001"),
    ("110101", "001010"), ("101001", "101001"), ("011001", "011001"), ("111000", "000111"),
    ("111001", "000110"), ("100101", "100101"), ("010101", "010101"), ("110100", "110100"),
    ("001101", "001101"), ("101100", "101100"), ("011100", "011100"), ("010111", "101000"),
    ("011011", "100100"), ("100011", "100011"), ("010011", "010011"), ("110010", "110010"),
    ("001011", "001011"), ("101010", "101010"), ("011010", "011010"), ("111010", "000101"),
    ("110011", "001100"), ("100110", "100110"), ("010110", "010110"), ("110110", "001001"),
    ("001110", "001110"), ("101110", "010001"), ("011110", "100001"), ("101011", "010100"),
]
SIX_K28 = ("001111", "110000")
FOUR_D = [("1011", "0100"), ("1001", "1001"), ("0101", "0101"), ("1100", "0011"),
          ("1101", "0010"), ("1010", "1010"), ("0110", "0110"), ("1110", "0001")]
FOUR_A7 = ("0111", "1000")
# K28.y 4b blocks, RD- form (the RD+ form is its complement).
FOUR_K28 = ["0100", "1001", "0101", "0011", "0010", "1010", "0110", "1000"]
A7_AT_MINUS = (17, 18, 20)   # Dx.7 uses A7 at RD- (after the 6b block) for these x
A7_AT_PLUS = (11, 13, 14)    # and at RD+ for these


def _rd_after(bits: str, rd: int) -> int:
    ones = bits.count("1")
    return rd if 2 * ones == len(bits) else (1 if 2 * ones > len(bits) else -1)


def _classic(byte: int, k: bool, rd: int) -> str:
    """The classic-table code of (byte, k) at RD ``rd``, line order."""
    x, y = byte & 0x1F, byte >> 5
    col = 0 if rd < 0 else 1
    if k and x == 28:
        four = FOUR_K28[y] if rd < 0 else "".join("1" if c == "0" else "0" for c in FOUR_K28[y])
        return SIX_K28[col] + four
    six = SIX[x][col]
    rd6 = _rd_after(six, rd)
    if y == 7 and (k or (rd6 < 0 and x in A7_AT_MINUS) or (rd6 > 0 and x in A7_AT_PLUS)):
        return six + FOUR_A7[0 if rd6 < 0 else 1]
    return six + FOUR_D[y][0 if rd6 < 0 else 1]


def _classic_columns() -> dict[int, dict[int, tuple[int, int, int]]]:
    """Per RD: code -> (byte, k, RD after), for 256 data and the 12 K codes."""
    k_bytes = [(y << 5) | 28 for y in range(8)] + [(7 << 5) | x for x in (23, 27, 29, 30)]
    cols: dict[int, dict[int, tuple[int, int, int]]] = {-1: {}, 1: {}}
    for rd in (-1, 1):
        for byte, k in [(b, 0) for b in range(256)] + [(b, 1) for b in k_bytes]:
            bits = _classic(byte, bool(k), rd)
            cols[rd][_code(bits)] = (byte, k, _rd_after(bits[6:], _rd_after(bits[:6], rd)))
    return cols


def test_decode_matches_classic_table():
    # Every code at both known RDs against the classic table: a valid code
    # gives its byte, K flag and RD after, no error; a code in the other
    # column only is a disparity error with its byte; any other code is a
    # code error (EDB as K).
    cols = _classic_columns()
    assert len(cols[-1]) == len(cols[1]) == 268
    for rd in (-1, 1):
        for c in range(1024):
            b, k, rd_out, ce, de = m.code.decode(c, rd)
            if c in cols[rd]:
                assert (b, int(k), rd_out, ce, de) == (*cols[rd][c], False, False), (rd, c)
            elif c in cols[-rd]:
                assert (b, int(k), ce, de) == (*cols[-rd][c][:2], False, True), (rd, c)
            else:
                assert (b, int(k), ce, de) == (m.K_EDB, 1, True, False), (rd, c)


def test_decode_unknown_rd_matches_classic_table():
    # At an unknown RD (8B10B.md, Decoder and error classes): never a
    # disparity error; the start is RD- unless the code is valid only at
    # RD+, which sets the RD after it.
    cols = _classic_columns()
    for c in range(1024):
        b, k, rd_out, ce, de = m.code.decode(c, None)
        assert not de, c
        start = 1 if (c in cols[1] and c not in cols[-1]) else -1
        if c in cols[start]:
            assert (b, int(k), rd_out, ce) == (*cols[start][c], False), c
        else:
            assert (b, int(k), ce) == (m.K_EDB, 1, True), c
