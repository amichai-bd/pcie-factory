"""Self-tests of the pcie_gpio architecture model; no simulator needed.

Run: python3 -m pytest src/arch/gpio (./build ci unit runs them as arch:<ip>).
"""

import pytest

import pcie_gpio_arch as m


def test_slot():
    """Four transfers of CLK_DIV cycles make a slot (HAS, slots)."""
    assert m.slot_cycles(2) == 8
    assert m.slot_cycles(64) == 256


@pytest.mark.parametrize("ratio", [0.5, 1.0, 2.0])
@pytest.mark.parametrize("clk_div", [2, 4, 64])
def test_w_within_bound(ratio, clk_div):
    """The MAS keeps W at most 4 slots of the slower end at every ratio of
    the clock contract (LINK_UP.md, terms)."""
    assert m.w_slots(clk_div, ratio) <= m.W_BOUND_SLOTS


def test_line_use_back_to_back():
    """Under the ready rule the next sop refills the buffer before the next
    slot start (S >= 8 > 3 cycles), so back-to-back packets leave no idle
    slot between them."""
    r = m.line_use([75, 75, 3, 9, 75])
    assert r["busy_slots"] == 75 + 75 + 3 + 9 + 75
    assert r["slots"] == r["busy_slots"] + 1      # the first slot starts before the first beat


@pytest.mark.parametrize("ratio", [0.5, 1.0, 2.0])
def test_link_up_converges(ratio):
    """Both ends reach up within the LINK_UP.md bound at each ratio."""
    for skew in (0, 37, 500):
        assert m.link_up(ratio, skew) <= 8 + 16 + 8 + 3 * m.W_BOUND_SLOTS


def test_replay_round_trip_values():
    """The link HAS figures: 1924 cycles at equal clocks, 3214 with the far
    end twice as slow, at CLK_DIV 2 (D = 75, L = 3 S)."""
    assert m.replay_round_trip(2, 1.0) == 1924
    assert m.replay_round_trip(2, 2.0) == 3214
    assert m.replay_timeout(2) == 3214


def test_replay_timeout_clamps():
    """Above CLK_DIV 41 the round trip passes the dll's 65535 maximum."""
    assert m.replay_timeout(41) < 65535
    assert m.replay_timeout(42) == 65535


def test_perf_rejects_bad_workloads():
    with pytest.raises(ValueError):
        m.perf({"kind": "latency", "clk_div": 3}, 1)
    with pytest.raises(ValueError):
        m.perf({"kind": "nope"}, 1)
