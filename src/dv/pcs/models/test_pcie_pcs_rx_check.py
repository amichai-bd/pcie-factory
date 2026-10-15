"""Unit tests of the pcs receive scoreboard: it accepts a faithful
delivery with SKP changes and reported errors, and rejects loss,
duplication, a wrong unreported symbol and a wrong SKP status.

Run: python3 -m pytest src/dv/pcs/models
"""

import random

import pytest

import pcie_pcs_rx_check as c

COM, SKP = (1, c.K_COM), (1, c.K_SKP)


def stream(rng, n=400):
    out = []
    for i in range(n):
        if i % 100 == 50:
            out += [COM, SKP, SKP, SKP]
        else:
            out.append((0, rng.randrange(256)))
    return out


def words_of(syms, status=None, t0=0):
    status = status or {}
    syms = syms[:len(syms) // 2 * 2]
    return [c.Word(t0 + 8000 * i, 1, (syms[2 * i], syms[2 * i + 1]), status.get(i, 0))
            for i in range(len(syms) // 2)]


def test_faithful_delivery_with_skp_changes():
    rng = random.Random(1)
    sent = stream(rng)
    got = list(sent[10:])
    # Remove one SKP of the first set, add one to the second.
    i = got.index(COM)
    del got[i + 1]
    j = got.index(COM, i + 1)
    got.insert(j + 1, SKP)
    coms = [w_i for w_i, w in enumerate(words_of(got)) if COM in w.syms]
    words = words_of(got, {coms[0]: 2, coms[1]: 1})
    runs = c.check_runs(sent, words)
    assert len(runs) == 1 and runs[0].first == 10 and runs[0].mismatches == 0
    assert c.check_skp_status(runs) >= 2


def test_loss_and_duplication_rejected():
    rng = random.Random(2)
    sent = stream(rng)
    for got in (sent[10:200] + sent[201:300], sent[10:200] + sent[199:300]):
        with pytest.raises(c.CheckError):
            c.check_runs(sent, words_of(got))


def test_reported_error_resyncs_unreported_does_not():
    rng = random.Random(3)
    sent = stream(rng)
    got = list(sent[10:300])
    got[100] = (0, got[100][1] ^ 0x40)                 # wrong byte
    with pytest.raises(c.CheckError):
        c.check_runs(sent, words_of(got))
    words = words_of(got, {51: 7})                     # reported one word later
    runs = c.check_runs(sent, words)
    assert runs[0].mismatches == 1


def test_wrong_skp_status_rejected():
    rng = random.Random(4)
    sent = stream(rng)
    runs = c.check_runs(sent, words_of(sent[10:]))
    assert c.check_skp_status(runs) >= 1                # 3 SKP, status 0: fine
    got = list(sent[10:])
    del got[got.index(COM) + 1]                         # removed, but status 0
    runs = c.check_runs(sent, words_of(got))
    with pytest.raises(c.CheckError):
        c.check_skp_status(runs)


def mk(valid_status):
    return [c.Word(8000 * i, v, ((0, 0), (0, 0)), st) for i, (v, st) in enumerate(valid_status)]


def test_relock_rule_limit_and_decay():
    # Three errored words in a row: one request at the third.
    ws = mk([(1, 0)] * 4 + [(1, 4), (1, 7), (1, 4)] + [(1, 0)] * 3 + [(0, 0)] * 2)
    assert c.relock_requests(ws) == [6]
    assert c.check_relocks(ws, 6) == 1
    # Two, then eight clean words forgive one: the third error is count 2.
    ws = mk([(1, 4), (1, 4)] + [(1, 0)] * 8 + [(1, 4)] + [(1, 0)] * 4)
    assert c.relock_requests(ws) == []
    # Only seven clean words: the third error relocks.
    ws = mk([(1, 4), (1, 4)] + [(1, 0)] * 7 + [(1, 4)] + [(1, 0)] * 4)
    assert c.relock_requests(ws) == [9]
    with pytest.raises(c.CheckError):
        c.check_relocks(ws, 3)                     # RxValid never falls


def test_unexplained_drop_rejected():
    ws = mk([(1, 0)] * 5 + [(0, 0)] * 3 + [(1, 0)] * 3)
    with pytest.raises(c.CheckError):
        c.check_relocks(ws, 8)
    assert c.check_relocks(ws, 8, explained=lambda i: i == 5) == 0
