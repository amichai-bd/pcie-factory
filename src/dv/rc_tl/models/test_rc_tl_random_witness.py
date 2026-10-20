"""Long-only witness guards and observer transparency, without simulation."""

from collections import Counter
import importlib.util
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from pcie_rc_tl_model import Cmd, MEM_RD, request_tlp
from pcie_rc_tl_random_witness import (EXPECTED_STATUSES, PHASE_WITNESSES, PLAN_CATEGORIES,
                                      check_witness)


def complete_counts():
    return Counter({**{f"plan_{name}": 1 for name in PLAN_CATEGORIES},
                    **{name: 1 for name in PHASE_WITNESSES}, "event_mask": 0x7F})


def test_complete_long_campaign_witness():
    check_witness(complete_counts(), EXPECTED_STATUSES)


@pytest.mark.parametrize("missing", [*(f"plan_{n}" for n in PLAN_CATEGORIES), *PHASE_WITNESSES])
def test_missing_executed_path_fails(missing):
    counts = complete_counts()
    counts[missing] = 0
    with pytest.raises(AssertionError, match="did not execute"):
        check_witness(counts, EXPECTED_STATUSES)


@pytest.mark.parametrize("missing", sorted(EXPECTED_STATUSES))
def test_missing_result_status_fails(missing):
    with pytest.raises(AssertionError, match="missing statuses"):
        check_witness(complete_counts(), EXPECTED_STATUSES - {missing})


def test_missing_requester_error_event_fails():
    counts = complete_counts()
    counts["event_mask"] &= ~(1 << 3)  # TIMEOUT was never actually observed
    with pytest.raises(AssertionError, match="error events"):
        check_witness(counts, EXPECTED_STATUSES)


def test_plan_observer_preserves_frames_and_rng(monkeypatch):
    dv = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(dv))
    monkeypatch.syspath_prepend(str(dv.parents[2] / "tools"))
    spec = importlib.util.spec_from_file_location("rc_tl_random_recipe", dv / "tests/test_rc_tl_req.py")
    recipe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipe)
    request = recipe.Req.decode(request_tlp(Cmd(MEM_RD, 0x10000020, 32, 15, 15), 0x2A, 7))
    seen = Counter()
    for seed in range(512):
        control = SimpleNamespace(rng=random.Random(seed))
        observed = SimpleNamespace(rng=random.Random(seed))
        counts = Counter()
        left = recipe.random_plan(control, control.rng)
        right = recipe.random_plan(observed, observed.rng, counts)
        assert not counts, "a scheduled but unexecuted plan counted as exercised"
        assert left(request) == right(request)
        assert control.rng.getstate() == observed.rng.getstate()
        assert sum(counts.values()) == 1
        seen.update(counts)
    assert all(seen[f"plan_{name}"] for name in PLAN_CATEGORIES), seen
