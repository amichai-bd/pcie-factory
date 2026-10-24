"""Continuous elastic sections retain testcase RNG and local SKP oracles."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import hashlib
import random

import pytest
from models.pcie_pcs_rx_check import Word, check_runs, check_skp_status, CheckError, K_COM, K_SKP
from tb.pcs_sections import legacy_rng, section_runs

COM, SKP = (1, K_COM), (1, K_SKP)

def words(syms, statuses):
    return [Word(i * 4000, 1, tuple(syms[i:i + 2]), statuses.get(i // 2, 0))
            for i in range(0, len(syms), 2)]

def test_preserved_cocotb_seed_and_distinct_testcase():
    name='tests.test_elastic.eb_stress_sizes'
    rng, effective=legacy_rng(name,2)
    assert effective == 2 + int(hashlib.sha1(name.encode()).hexdigest(),16)
    assert rng.getrandbits(64)==random.Random(effective).getrandbits(64)
    assert legacy_rng(name,1)[1]!=effective
    assert legacy_rng('tests.test_elastic.eb_tlp_m600',2)[1]!=effective

def test_section_skp_mapping_is_not_rebased_to_zero_sent_position():
    rng=random.Random(2)
    before=[(0,rng.randrange(256)) for _ in range(50)]
    after=[(0,rng.randrange(256)) for _ in range(60)]
    sent=before+[COM,SKP,SKP,SKP]+after
    got=sent[:52]+sent[53:]+[(0,0)] # remove a SKP
    ws=words(got,{25:2})
    runs=check_runs(sent,ws[:-2])
    local=section_runs(runs,50,len(sent))
    stats={}
    assert check_skp_status(sent,local,stats=stats)==1
    assert stats['removed']==1
    assert min(local[0].sent_of.values())==50
    # Mutant: unreported removal in the local slice must still fail.
    ws[25]=Word(ws[25].t,1,ws[25].syms,0)
    with pytest.raises(CheckError):
        check_skp_status(sent,section_runs(check_runs(sent,ws[:-2]),50,len(sent)))

def test_boundary_word_is_still_checked_by_global_oracle():
    rng=random.Random(3)
    sent=[(0,rng.randrange(256)) for _ in range(100)]
    ws=words(sent,{})
    run=check_runs(sent,ws)[0]
    local=section_runs([run],51,90)
    assert all(51<=p<90 for r in local for p in r.sent_of.values())
    # One boundary word was omitted locally, never omitted globally.
    assert len(local[0].words)==19
    corrupted=list(sent);corrupted[50]=(0,corrupted[50][1]^1)
    with pytest.raises(CheckError):
        check_runs(sent,words(corrupted,{}))


def test_skp_only_word_is_not_dropped_from_section():
    rng = random.Random(9)
    sent = [(0, rng.randrange(256)) for _ in range(50)] + [COM] + [SKP]*5
    sent += [(0, rng.randrange(256)) for _ in range(60)]
    runs = check_runs(sent, words(sent, {}))
    local = section_runs(runs, 50, len(sent))
    assert any(w.syms == (SKP, SKP) for w in local[0].words)
    stats = {}
    assert check_skp_status(sent, local, stats=stats) == 1
    assert stats == {}
