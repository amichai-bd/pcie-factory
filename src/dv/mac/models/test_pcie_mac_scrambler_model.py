"""Unit tests of the scrambler cycle model and, through it, of the mac arch
model's Scrambler against an independent bit-serial statement of
wiki/src/arch/mac/SYMBOLS.md#scrambling. No simulator needed.

Run: python3 -m pytest src/dv/mac/models
"""

import random

import pcie_mac_scrambler_model as m

# The known answer of SYMBOLS.md: key bytes after a reset (scrambling 00h).
KAT = [0xFF, 0x17, 0xC0, 0x14, 0xB2, 0xE7, 0x02, 0x82,
       0x72, 0x6E, 0x28, 0xA6, 0xBE, 0x6D, 0xBF, 0x8D]
COM, SKP, STP, END = 0xBC, 0x1C, 0xFB, 0xFD


def shift(state):
    """One shift as SYMBOLS.md words it: left by one, XOR 0039h when the bit
    shifted out of bit 15 is 1; returns (key bit, new state)."""
    out = (state >> 15) & 1
    state = (state << 1) & 0xFFFF
    return out, state ^ (0x0039 if out else 0)


def ref_key_stream(n, state=0xFFFF):
    keys = []
    for _ in range(n):
        key = 0
        for i in range(8):
            bit, state = shift(state)
            key |= bit << i
        keys.append(key)
    return keys


def run(words):
    """Clock (data, datak, scr) words through a fresh model, valid every cycle."""
    mdl, out = m.WordScrambler(), []
    for data, datak, scr in words:
        mdl.clock(0, 1, data, datak, scr)
        out.append(mdl.data)
    return out


def test_known_answer():
    out = run([(0x0000, 0, 3)] * 8)
    assert [b for w in out for b in (w & 0xFF, w >> 8)] == KAT


def test_bit_serial_reference_full_period():
    n = 2**16
    keys = ref_key_stream(n)
    assert keys[:16] == KAT
    assert m.arch.keystream(n) == keys
    assert keys[2**16 - 1] == 0xFF  # maximal length: back at the seed


def test_com_skp_k_and_disable():
    # COM in [15:8] after two data symbols: the next word starts at the seed.
    out = run([(0x0000, 0, 3), (COM << 8, 0b10, 3), (0x0000, 0, 3)])
    assert out[0] == (KAT[1] << 8) | KAT[0]
    assert out[1] == (COM << 8) | KAT[2]
    assert out[2] == (KAT[1] << 8) | KAT[0]
    # SKP does not advance; other K symbols advance but are not changed.
    out = run([(SKP << 8 | SKP, 3, 3), (STP << 8 | 0x00, 0b10, 3), (0x00 << 8 | END, 0b01, 3)])
    assert out == [SKP << 8 | SKP, STP << 8 | KAT[0], KAT[3] << 8 | END]
    # scr 0: sent as is, but the LFSR still advances.
    out = run([(0x1234, 0, 0), (0x0000, 0, 3)])
    assert out == [0x1234, KAT[3] << 8 | KAT[2]]
    # A data byte equal to COM or SKP is data: scrambled and advancing.
    out = run([(SKP << 8 | COM, 0, 3)])
    assert out == [((SKP ^ KAT[1]) << 8) | (COM ^ KAT[0])]


def test_valid_holds_and_reset():
    mdl = m.WordScrambler()
    mdl.clock(0, 1, 0, 0, 3)
    mdl.clock(0, 0, 0xFFFF, 0, 3)  # not valid: LFSR and outputs hold
    assert mdl.valid == 0 and mdl.data == (KAT[1] << 8 | KAT[0])
    mdl.clock(0, 1, 0, 0, 3)
    assert mdl.data == (KAT[3] << 8 | KAT[2])
    mdl.clock(1, 1, 0, 0, 3)
    assert mdl.valid == 0 and mdl.state == 0xFFFF


def test_round_trip():
    rng = random.Random(7)
    words = []
    for _ in range(2000):
        datak = rng.getrandbits(2)
        data = 0
        for i in range(2):
            b = rng.choice([COM, SKP, STP, END]) if (datak >> i) & 1 else rng.getrandbits(8)
            data |= b << (8 * i)
        words.append((data, datak, rng.getrandbits(2)))
    scrambled = run(words)
    back = run([(s, k, c) for s, (_, k, c) in zip(scrambled, words)])
    assert back == [d for d, _, _ in words]
    assert scrambled != back
