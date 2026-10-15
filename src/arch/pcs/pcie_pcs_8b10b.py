"""8b/10b code of the pcs, computed from the rules in
wiki/src/arch/pcs/8B10B.md; no code table is copied.

A helper of the pcs architecture model (pcie_pcs_arch.py). Bit order: a
10-bit code is an int whose bit 0 is the first bit on the line (bit ``a``),
then b c d e i f g h j. A byte HGFEDCBA splits into x = EDCBA (bits 4..0)
and y = HGF (bits 7..5); the symbol is named Dx.y or Kx.y. Running
disparity (RD) is -1 or +1.
"""

from __future__ import annotations

# ------------------------------------------------------------ sub-blocks

def _bits(value: int, n: int) -> list[int]:
    return [(value >> i) & 1 for i in range(n)]


def _pack(bits: list[int]) -> int:
    return sum(b << i for i, b in enumerate(bits))


def _ones(code: int, n: int) -> int:
    return bin(code & ((1 << n) - 1)).count("1")


# The 5b/6b block, written as the (a, b, c, d, e, i) bits for the RD- column
# (the form sent when the running disparity is negative). The rule: a value
# with two or three ones among EDCBA sends them unchanged as abcde and sets i
# so that the six bits hold three ones; every other value, and D.24 (whose
# rule form would collide with D.07's alternate), uses the exception form
# below, which holds four ones in the RD- column. The RD+ form of an
# unbalanced block is its complement; a balanced block is sent the same in
# both columns, except D.07 (111000 / 000111). K.28 has its own block.
_SIX_EXCEPTIONS = {          # x: abcdei for RD-
    0: "100111", 1: "011101", 2: "101101", 4: "110101", 8: "111001",
    15: "010111", 16: "011011", 23: "111010", 24: "110011", 27: "110110",
    29: "101110", 30: "011110", 31: "101011",
}
_K28_SIX = "001111"


def _six_minus(x: int, k28: bool = False) -> int:
    """The RD- column 6b code of x (bit 0 = a)."""
    if k28:
        return _pack([int(c) for c in _K28_SIX])
    if x in _SIX_EXCEPTIONS:
        return _pack([int(c) for c in _SIX_EXCEPTIONS[x]])
    abcde = _bits(x, 5)
    i = 1 if sum(abcde) == 2 else 0
    return _pack(abcde + [i])


# The 3b/4b block (f, g, h, j) for the RD- column. A value with one or two
# ones among HGF sends them unchanged as fgh and sets j to balance the four
# bits; y = 3 (110 + j=0) is balanced but alternates like D.07. y = 0 sends
# 1011 and y = 4 sends 1101 in RD- (unbalanced, complemented in RD+). y = 7
# has a primary form P7 (1110) and an alternate A7 (0111), both complemented
# in RD+.
def _four_minus(y: int, alt7: bool = False) -> int:
    if y == 0:
        return _pack([1, 0, 1, 1])
    if y == 4:
        return _pack([1, 1, 0, 1])
    if y == 7:
        return _pack([0, 1, 1, 1] if alt7 else [1, 1, 1, 0])
    fgh = _bits(y, 3)
    j = 1 if sum(fgh) == 1 else 0
    return _pack(fgh + [j])


def _block_rd(code: int, n: int, rd: int) -> int:
    """Running disparity after a sub-block of n bits (6 or 4): positive when
    it holds more ones than zeros or is 000111 / 0011 (in line order),
    negative when it holds fewer or is 111000 / 1100, else unchanged."""
    ones = _ones(code, n)
    if 2 * ones > n:
        return 1
    if 2 * ones < n:
        return -1
    pos = 0b111000 if n == 6 else 0b1100   # line order 000111 / 0011, bit 0 first
    neg = 0b000111 if n == 6 else 0b0011   # line order 111000 / 1100
    if code == pos:
        return 1
    if code == neg:
        return -1
    return rd


def _sub_rd_ok(code: int, n: int, rd: int) -> bool:
    """Whether a sub-block may be sent when the running disparity is rd."""
    ones = _ones(code, n)
    if 2 * ones > n:
        return rd < 0
    if 2 * ones < n:
        return rd > 0
    pos = 0b111000 if n == 6 else 0b1100
    neg = 0b000111 if n == 6 else 0b0011
    if code == pos:          # 000111 / 0011 only after a positive RD
        return rd > 0
    if code == neg:          # 111000 / 1100 only after a negative RD
        return rd < 0
    return True


def _column(minus: int, n: int, rd: int) -> int:
    """The form sent at running disparity rd: the RD- code, or for RD+ its
    complement when the block is unbalanced or alternating."""
    if rd < 0:
        return minus
    ones = _ones(minus, n)
    alternating = minus in ((0b000111, 0b111000) if n == 6 else (0b0011, 0b1100))
    if 2 * ones != n or alternating:
        return minus ^ ((1 << n) - 1)
    return minus


# --------------------------------------------------------------- symbols

# The valid K codes: K28.0 to K28.7 and K23.7, K27.7, K29.7, K30.7.
VALID_K = tuple([(y << 5) | 28 for y in range(8)] + [(7 << 5) | x for x in (23, 27, 29, 30)])

# This design's special symbols (bytes with K set).
K_SKP = 0x1C   # K28.0
K_FTS = 0x3C   # K28.1
K_SDP = 0x5C   # K28.2
K_IDL = 0x7C   # K28.3
K_COM = 0xBC   # K28.5
K_PAD = 0xF7   # K23.7
K_STP = 0xFB   # K27.7
K_END = 0xFD   # K29.7
K_EDB = 0xFE   # K30.7


def _encode(byte: int, k: bool, rd: int) -> tuple[int, int]:
    x, y = byte & 0x1F, byte >> 5
    if k and byte not in VALID_K:
        raise ValueError(f"K{x}.{y} is not a valid K code")
    if k and x == 28:
        # K28.y: in RD- it is 001111 then the 4b block of y as sent at a
        # positive RD (A7 for y = 7); in RD+ it is the complement of that.
        minus = _six_minus(x, True) | (_column(_four_minus(y, y == 7), 4, 1) << 6)
        code = minus if rd < 0 else minus ^ 0x3FF
        return code, rd_after(code, rd)
    six = _column(_six_minus(x), 6, rd)
    rd6 = _block_rd(six, 6, rd)
    alt = False
    if y == 7:
        # Kx.7 always uses A7. Dx.7 uses A7 only where P7 would make a run
        # of five equal bits across e, i, f, g, h.
        p7 = _column(_four_minus(7, False), 4, rd6)
        e_i = (six >> 4) & 0b11
        f_g_h = p7 & 0b111
        alt = k or (e_i == 0b11 and f_g_h == 0b111) or (e_i == 0b00 and f_g_h == 0b000)
    four = _column(_four_minus(y, alt), 4, rd6)
    return six | (four << 6), _block_rd(four, 4, rd6)


def encode(byte: int, k: bool, rd: int) -> tuple[int, int]:
    """10-bit code and the running disparity after it."""
    return ENCODE[(byte, bool(k), rd)]


def rd_after(code: int, rd: int) -> int:
    """Running disparity after receiving ``code`` (valid or not), sub-block
    by sub-block."""
    return _block_rd(code >> 6, 4, _block_rd(code & 0x3F, 6, rd))


def disparity_ok(code: int, rd: int) -> bool:
    """Whether both sub-blocks of ``code`` are allowed at running disparity
    ``rd`` (the 4b block judged at the RD after the 6b block)."""
    rd6 = _block_rd(code & 0x3F, 6, rd)
    return _sub_rd_ok(code & 0x3F, 6, rd) and _sub_rd_ok(code >> 6, 4, rd6)


def decode(code: int, rd: int | None) -> tuple[int, bool, int, bool, bool]:
    """Decode one received code at running disparity ``rd`` (None: unknown,
    either column accepted, as for the first symbol after lock).

    Returns (byte, k, rd_out, code_error, disparity_error). A code error
    (a code in neither column) returns EDB as a K code; a disparity error
    (a code valid only in the other column) returns the decoded byte.
    rd_out follows the received sub-blocks either way.
    """
    start = -1 if rd is None else rd
    entries = DECODE.get(code)
    if not entries:
        return K_EDB, True, rd_after(code, start), True, False
    if rd is None:
        b, k, rd_in = entries[0]
        return b, k, rd_after(code, rd_in), False, False
    for b, k, rd_in in entries:
        if rd_in == rd:
            return b, k, rd_after(code, rd), False, False
    b, k, rd_in = entries[0]
    return b, k, rd_after(code, rd), False, True


COMMA_BITS = 7
COMMA_MINUS = 0b1111100   # line order 0011111 (bits a..g), bit 0 first
COMMA_PLUS = 0b0000011    # line order 1100000


def has_comma(code: int) -> bool:
    """A comma at bit 0: the first seven bits are 0011111 or 1100000."""
    head = code & 0x7F
    return head in (COMMA_MINUS, COMMA_PLUS)


def raw_is(code: int, byte: int) -> bool:
    """Whether a raw 10-bit code is K ``byte`` in either column, or either
    column inverted (polarity-invariant match for COM and SKP)."""
    m = ENCODE[(byte, True, -1)][0]
    p = ENCODE[(byte, True, 1)][0]
    return code in (m, p, m ^ 0x3FF, p ^ 0x3FF)


# Encoder table: (byte, k, rd) -> (code, rd_out); decoder table:
# code -> list of (byte, k, rd_in) it is valid for.
ENCODE: dict[tuple[int, bool, int], tuple[int, int]] = {}
DECODE: dict[int, list[tuple[int, bool, int]]] = {}
for _k in (False, True):
    for _b in (VALID_K if _k else range(256)):
        for _rd in (-1, 1):
            _code, _out = _encode(_b, _k, _rd)
            ENCODE[(_b, _k, _rd)] = (_code, _out)
            DECODE.setdefault(_code, []).append((_b, _k, _rd))
