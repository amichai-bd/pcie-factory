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
    assert c.check_skp_status(sent, runs) >= 2


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
    assert c.check_skp_status(sent, runs) >= 1          # 3 SKP, status 0: fine
    got = list(sent[10:])
    del got[got.index(COM) + 1]                         # removed, but status 0
    runs = c.check_runs(sent, words_of(got))
    with pytest.raises(c.CheckError):
        c.check_skp_status(sent, runs)


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


def test_skp_sets_of_any_size():
    """Sets of one to five SKP: one fewer, the same or one more leaves; a
    set of one may leave as a bare COM; two more or a change on a COM sent
    with no SKP is rejected."""
    rng = random.Random(5)
    sent = []
    for n in (1, 2, 5, 0, 4):
        sent += [(0, rng.randrange(256)) for _ in range(60)]
        sent += [COM] + [SKP] * n
    sent += [(0, rng.randrange(256)) for _ in range(60)]
    got = list(sent)
    first = got.index(COM)
    del got[first + 1]                                  # bare COM
    words = words_of(got)
    status = {w_i: 2 for w_i, w in enumerate(words) if w.syms[0] == COM or w.syms[1] == COM}
    status = {k: v for k, v in status.items() if k == min(status)}
    runs = c.check_runs(sent, words_of(got, status))
    assert c.check_skp_status(sent, runs) == 5
    for bad in (2, 0):
        got = list(sent)
        i = [k for k, s in enumerate(got) if s == COM][3 if bad == 0 else 1]
        got[i + 1:i + 1] = [SKP] * (bad or 1)
        runs = c.check_runs(sent, words_of(got))
        with pytest.raises(c.CheckError):
            c.check_skp_status(sent, runs)


def test_noise_lock_needs_more_than_a_wrong_lead_symbol():
    rng = random.Random(6)
    sent = stream(rng)
    got = list(sent[10:300])
    got[0] = (1, 0xFE)                                  # lead symbol wrong
    runs = c.check_runs(sent, words_of(got, {0: 4}), lead_skip=lambda t: True)
    assert not runs[0].noise_lock
    got[3] = (1, 0xFE)                                  # and a later one
    runs = c.check_runs(sent, words_of(got, {0: 4, 1: 4}), lead_skip=lambda t: True)
    assert runs[0].noise_lock


def test_resync_after_a_slip_in_the_same_run():
    rng = random.Random(7)
    sent = stream(rng, 600)
    got = sent[10:200] + [(1, 0xFE)] * 6 + sent[230:500]
    words = words_of(got, {95: 4, 96: 4, 97: 4})
    with pytest.raises(c.CheckError):
        c.check_runs(sent, words)
    runs = c.check_runs(sent, words, slip_possible=lambda t0, t: True, resync=True)
    assert runs[0].resyncs and not runs[0].lost and runs[0].last >= 480
