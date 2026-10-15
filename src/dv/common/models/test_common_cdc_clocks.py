"""The clock-pair generator spans the ratios the DV plans claim, in both
directions, without piling up on the period limits."""

import math
import random

import pytest

from common_cdc_clocks import MAX_PERIOD_PS, MIN_PERIOD_PS, random_periods


@pytest.mark.parametrize("max_ratio", [8.0, 16.0])
def test_ratio_range_both_directions(max_ratio):
    rng = random.Random(1)
    draws = [random_periods(rng, max_ratio) for _ in range(20_000)]
    for src, dst in draws:
        assert MIN_PERIOD_PS <= src <= MAX_PERIOD_PS and MIN_PERIOD_PS <= dst <= MAX_PERIOD_PS
        assert src % 2 == 0 and dst % 2 == 0
        assert max(src, dst) / min(src, dst) <= max_ratio * 1.001
    src_fast = [d / s for s, d in draws if s < d]
    dst_fast = [s / d for s, d in draws if d < s]
    for side in (src_fast, dst_fast):
        assert 0.45 * len(draws) < len(side) < 0.55 * len(draws)
        assert max(side) > 0.95 * max_ratio      # reaches the claimed ratio
        assert min(side) < 1.05                   # and near 1:1
        # Log-uniform: each half of the log range holds about half the draws.
        low = sum(r < math.sqrt(max_ratio) for r in side)
        assert 0.4 < low / len(side) < 0.6
    # No pile-up on the fast-period floor (the old generator put ~30% there).
    floor = sum(min(s, d) == MIN_PERIOD_PS for s, d in draws)
    assert floor < 0.01 * len(draws)


def test_rejects_ratio_beyond_the_period_limits():
    with pytest.raises(ValueError):
        random_periods(random.Random(1), MAX_PERIOD_PS / MIN_PERIOD_PS * 2)
