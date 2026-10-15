"""Clock-pair generator for the common_cdc_* tests
(wiki/src/dv/common/cdc/common_cdc_sync2.md#strategy). Pure Python, so its
unit tests run without a simulator.

Times are integer picoseconds; periods are even so that both phases of
every clock are whole picoseconds.
"""

import math

MIN_PERIOD_PS = 2_000
MAX_PERIOD_PS = 64_000


def even(ps: float) -> int:
    return 2 * round(ps / 2)


def random_periods(rng, max_ratio: float) -> tuple[int, int]:
    """(src, dst) periods: a ratio log-uniform in [1, max_ratio], a fast
    period uniform in [MIN, MAX / ratio], and the fast clock on a random
    side. ``max_ratio`` may be at most MAX / MIN."""
    if not 1 <= max_ratio <= MAX_PERIOD_PS / MIN_PERIOD_PS:
        raise ValueError(f"max_ratio {max_ratio} outside [1, {MAX_PERIOD_PS / MIN_PERIOD_PS}]")
    ratio = math.exp(rng.uniform(0.0, math.log(max_ratio)))
    fast = even(rng.uniform(MIN_PERIOD_PS, MAX_PERIOD_PS / ratio))
    slow = min(even(fast * ratio), MAX_PERIOD_PS)
    return (fast, slow) if rng.random() < 0.5 else (slow, fast)
