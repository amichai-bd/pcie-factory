"""Unit tests of the comma-aligner model; no simulator needed.

Run: python3 -m pytest src/dv/pcs_align/models
"""

import random

import pcie_pcs_align_model as m


def run(line, relock_at=()):
    """Clock the model over a line; return per-cycle (lock, gen, offset, sym)."""
    al, out = m.Aligner(), []
    for n, (data, idle, _) in enumerate(line.words()):
        al.clock(data, idle, int(n in relock_at))
        out.append((al.lock, al.gen, al.offset, al.sym))
    return out


def test_comma_forms_match_the_8b10b_code():
    # K28.5 in both columns starts with a comma; no D code does.
    for rd in (-1, 1):
        assert m.comma_at(m.code8b10b.encode(m.K_COM, True, rd)[0], 0)
        for b in range(256):
            assert not m.comma_at(m.code8b10b.encode(b, False, rd)[0], 0)


def test_lock_from_every_bit_offset():
    codes = m.encode(m.ts_traffic(4))
    for shift in range(20):
        line = m.Line()
        line.noise(random.Random(shift), 40, idle=1)
        line.filler(shift)
        line.send(codes)
        line.pad()
        words = line.words()
        out = run(line)
        first = next(i for i, o in enumerate(out) if o[0])
        assert out[first][2] == shift % 10
        # Every locked symbol is a whole transmitted symbol.
        for i in range(first, len(out) - 1):
            win = words[i - 2][2] + words[i - 1][2]
            for j in range(2):
                p = out[i][2] + 10 * j
                if i == first and win[p] < 0:
                    continue  # the filler before the comma
                assert m.symbol_aligned(win, p) is not None, (shift, i, j)


def test_no_lock_in_encoded_data():
    rng = random.Random(1)
    codes = m.encode([(0, rng.randrange(256)) for _ in range(4000)])
    line = m.Line()
    line.send(codes)
    line.pad()
    assert not any(o[0] for o in run(line))


def test_relock_same_cycle_flips_generation():
    codes = m.encode(m.ts_traffic(20))
    line = m.Line()
    line.send(codes)
    line.pad()
    out = run(line)
    first = next(i for i, o in enumerate(out) if o[0])
    # A relock on an edge whose window holds a comma retakes lock at once.
    words = line.words()
    al = m.Aligner()
    gens = []
    for n, (data, idle, _) in enumerate(words):
        win = al.prev | (al.cur << 20)
        has = any(m.comma_at(win, p) for p in range(20))
        al.clock(data, idle, int(n > first + 4 and has and not gens))
        if n > first + 4 and has and not gens:
            gens.append((al.lock, al.gen))
    assert gens == [(1, 0)]  # still locked, generation flipped from 1 to 0


def test_error_counter_limits():
    ec = m.ErrorCounter()
    good = m.encode([(0, 0x4A)] * 2)
    bad = (0x3FF, 0x3FF)           # code errors
    assert not ec.word(tuple(good), 1, 1)
    assert not ec.word(bad, 1, 1)
    assert not ec.word(bad, 1, 1)
    assert ec.word(bad, 1, 1)       # the third errored word requests
    assert not ec.word(bad, 1, 1)   # no other until the generation changes
    assert not ec.word(bad, 1, 0)   # new run: count restarts
    assert not ec.word(bad, 1, 0)
    assert ec.word(bad, 1, 0)
