"""Unit tests of the elastic-buffer stream checker and traffic; no
simulator needed.

Run: python3 -m pytest src/dv/pcs_elastic/models
"""

import random
import re
from pathlib import Path

import pytest

import pcie_pcs_elastic_model as m

RTL = Path(__file__).resolve().parents[3] / "rtl" / "pcs_elastic"
COM_M, COM_P = sorted(m.COM_CODES)
SKP_M, SKP_P = sorted(m.SKP_CODES)


def _localparams(path: Path) -> dict[str, int]:
    out = {}
    for name, width, digits in re.findall(
            r"localparam\s+\w+(?:\s+unsigned)?\s+(\w+)\s*=\s*(?:(\d+)'h([0-9A-Fa-f]+)|)",
            path.read_text()):
        if width:
            out[name] = int(digits, 16)
    for name, value in re.findall(r"localparam\s+int\s+unsigned\s+(\w+)\s*=\s*(\d+)\s*;",
                                  path.read_text()):
        out[name] = int(value)
    return out


def test_rtl_codes_match_arch_code():
    """The package's raw COM and SKP codes are the arch model's K28.5 and
    K28.0 in both columns (RECEIVE.md: matching is polarity-blind)."""
    p = _localparams(RTL / "pcie_pcs_elastic_pkg.sv")
    assert {p["COM_RD_MINUS"], p["COM_RD_PLUS"]} == m.COM_CODES
    assert {p["SKP_RD_MINUS"], p["SKP_RD_PLUS"]} == m.SKP_CODES
    for c in m.COM_CODES | m.SKP_CODES:
        assert c ^ 0x3FF in m.COM_CODES | m.SKP_CODES   # inverted lane


def test_rtl_levels_match_arch():
    p = _localparams(RTL / "pcie_pcs_elastic.sv")
    assert 2 ** p["EB_ADDR_W"] == m.EB_WORDS
    assert (p["EB_NOMINAL"], p["EB_HIGH"], p["EB_LOW"], p["EB_HOLDOFF"]) == \
        (m.EB_NOMINAL, m.EB_HIGH, m.EB_LOW, m.EB_HOLDOFF)


# A small hand-built stream: data, a SKP set of three, data.
D = [0x2AA, 0x155, 0x0F1, 0x30E]


def _traffic(codes, lock=1, gen=1):
    t = m.Traffic()
    for c in codes:
        t.syms.append(m.Sym(c, lock, gen))
    return t


def _words(codes, add=(), rem=(), ovf=(), lock=1, gen=1, fill=8):
    """Pack ``codes`` into output words; flag sets hold word indices."""
    assert len(codes) % 2 == 0
    return [m.Word(i, codes[2 * i], codes[2 * i + 1], (lock, lock), (gen, gen),
                   int(i in add), int(i in rem), int(i in ovf), 0, fill)
            for i in range(len(codes) // 2)]


STREAM = D + [COM_M, SKP_M, SKP_M, SKP_M] + D


def test_identity_passes():
    r = m.check(_traffic(STREAM), _words(STREAM))
    assert (r.delivered, r.added, r.removed) == (len(STREAM), 0, 0)


def test_add_after_com_reported_on_com_word():
    out = D + [COM_M, SKP_M, SKP_M, SKP_M, SKP_M] + D + [D[0]]
    r = m.check(_traffic(STREAM + [D[0]]), _words(out, add={2}))
    assert r.added == 1 and r.add_slot == [1, 0]


def test_add_without_flag_fails():
    out = D + [COM_M, SKP_M, SKP_M, SKP_M, SKP_M] + D + [D[0]]
    with pytest.raises(m.CheckError, match="add flag"):
        m.check(_traffic(STREAM + [D[0]]), _words(out))


def test_two_adds_in_one_set_fail():
    out = D + [COM_M] + [SKP_M] * 5 + D
    with pytest.raises(m.CheckError, match="no addition is allowed"):
        m.check(_traffic(STREAM), _words(out, add={2}))


def test_add_in_a_set_without_skp_fails():
    stream = D + [COM_M, D[1]] + D
    out = D + [COM_M, SKP_M, D[1]] + D + [D[0]]
    with pytest.raises(m.CheckError):
        m.check(_traffic(stream), _words(out, add={2}))


def test_remove_reported_on_com_word_or_next():
    out = D + [COM_M, SKP_M, SKP_M] + D + [D[0]]
    r = m.check(_traffic(STREAM + [D[0]]), _words(out, rem={2}))
    assert (r.removed, r.rem_on_com) == (1, 1)
    out = D + [D[0], COM_M, SKP_M, SKP_M] + D + [D[1], D[2]]
    stream = D + [D[0], COM_M, SKP_M, SKP_M, SKP_M] + D + [D[1], D[2]]
    r = m.check(_traffic(stream), _words(out, rem={3}, fill=2))
    assert (r.removed, r.rem_fallback) == (1, 1)


def test_remove_on_next_word_needs_an_unseen_symbol():
    """At a fill of 4 or more the symbol after the COM was visible, so the
    status must be on the COM's word."""
    out = D + [D[0], COM_M, SKP_M, SKP_M] + D + [D[1], D[2]]
    stream = D + [D[0], COM_M, SKP_M, SKP_M, SKP_M] + D + [D[1], D[2]]
    with pytest.raises(m.CheckError, match="not on the COM's word"):
        m.check(_traffic(stream), _words(out, rem={3}, fill=4))


def test_sets_are_recorded():
    out = D + [COM_M, SKP_M, SKP_M] + D + [D[0]]
    r = m.check(_traffic(STREAM + [D[0]]), _words(out, rem={2}))
    assert [(s["com_in"], s["com_word"], s["n"], s["change"]) for s in r.sets] == \
        [(4, 2, 3, "rem")]


def test_unreported_removal_fails():
    out = D + [COM_M, SKP_M, SKP_M] + D + [D[0]]
    with pytest.raises(m.CheckError, match="not reported"):
        m.check(_traffic(STREAM + [D[0]]), _words(out))


def test_two_removals_in_one_set_fail():
    out = D + [COM_M, SKP_M] + D
    with pytest.raises(m.CheckError, match="missing"):
        m.check(_traffic(STREAM), _words(out, rem={2}))


def test_bare_com_after_removing_the_only_skp():
    stream = D + [COM_P, SKP_P] + D
    out = D + [COM_P] + D + [D[0]]
    r = m.check(_traffic(stream + [D[0]]), _words(out, rem={2}))
    assert (r.removed, r.bare_coms) == (1, 1)


def test_removal_in_unlocked_set_fails():
    t = _traffic(STREAM + [D[0]])
    for s in t.syms[4:8]:
        s.lock = 0
    out = D + [COM_M, SKP_M, SKP_M] + D + [D[0]]
    words = _words(out, rem={2})
    with pytest.raises(m.CheckError):
        m.check(t, words)


def test_lost_or_swapped_data_fails():
    out = list(STREAM)
    out[1], out[2] = out[2], out[1]
    with pytest.raises(m.CheckError, match="mismatch"):
        m.check(_traffic(STREAM), _words(out))
    with pytest.raises(m.CheckError, match="mismatch"):
        m.check(_traffic(STREAM), _words(STREAM[2:] + [D[0], D[1]]))


def test_generation_is_checked():
    t = _traffic(STREAM)
    words = _words(STREAM, gen=0)
    with pytest.raises(m.CheckError, match="mismatch"):
        m.check(t, words)


def test_unlocked_word_drop_only_on_word_boundary():
    t = _traffic(D + D + D)
    for s in t.syms[4:8]:
        s.lock = 0
    words = _words(D) + [m.Word(9, D[2], D[3], (0, 0), (1, 1), 0, 0, 0, 0)] + _words(D)
    r = m.check(t, words)
    assert r.dropped_words == 1


def test_overflow_loss_needs_the_flag():
    rng = random.Random(1)
    stream = [rng.randrange(1024) for _ in range(40)]
    out = stream[:10] + stream[12:]
    r = m.check(_traffic(stream), _words(out, ovf={5}), loss=True)
    assert (r.overflows, r.lost_symbols) == (1, 2)
    with pytest.raises(m.CheckError):
        m.check(_traffic(stream), _words(out), loss=True)
    with pytest.raises(m.CheckError):
        m.check(_traffic(stream), _words(out, ovf={5}), loss=False)


def test_underflow_word_is_empty_and_timed():
    t = _traffic(STREAM)
    words = _words(STREAM)
    words.insert(2, m.Word(50, 0, 0, (0, 0), (0, 0), 0, 0, 0, 1))
    assert m.check(t, words).underflows == 1
    with pytest.raises(m.CheckError, match="underflow while data"):
        m.check(t, words, udf_allowed_from=100)


@pytest.mark.parametrize("kind", ["tlp", "random", "dllp", "stress", "short_burst", "dense"])
def test_traffic_kinds(kind):
    t = m.link_traffic(random.Random(3), kind, 4, 1180)
    assert t.words and len(t.syms) == 2 * t.words
    assert all(s.lock == 1 and s.gen == 1 for s in t.syms)
    codes = [s.code for s in t.syms]
    assert any(c in m.COM_CODES for c in codes) and any(c in m.SKP_CODES for c in codes)


def test_short_burst_sends_one_set_too_few():
    """After the short packet only one set goes out although the gap is
    almost two intervals long."""
    rng = random.Random(5)
    syms = m.packet_stream(rng, [2 * 1538 - 7], 3, 1538, lead_ts=0)
    runs, i = [], 0
    while i < len(syms):
        if syms[i] == (1, m.code8b10b.K_COM):
            n = 0
            while i < len(syms) and syms[i] == (1, m.code8b10b.K_COM):
                n += 1
                i += 4
            runs.append(n)
        i += 1
    assert 1 in runs


def test_idle_traffic_unlocks_and_relocks_with_new_generation():
    t = m.link_traffic(random.Random(7), "random", 30, 1180, idle_gaps=1, idle_symbols=400)
    gens = {s.gen for s in t.syms if s.lock}
    assert any(not s.lock for s in t.syms) and gens == {0, 1}


def test_relocks_flip_generation_while_locked():
    t = m.link_traffic(random.Random(2), "random", 30, 1180, relocks=3)
    assert t.relocks == 3 and all(s.lock for s in t.syms)
    flips = sum(t.syms[i].gen != t.syms[i - 1].gen for i in range(1, len(t.syms)))
    assert flips == 3


def test_dump_round_trip(tmp_path):
    t = _traffic(STREAM)
    words = _words(STREAM)
    t2, w2 = m.load(str(m.dump(t, words, str(tmp_path / "d.json"))))
    assert m.check(t2, w2).delivered == len(STREAM)
