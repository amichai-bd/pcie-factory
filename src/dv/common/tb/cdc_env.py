"""Clock and edge-timing helpers for the common_cdc_* tests
(wiki/src/dv/common/).

Times are integer picoseconds. Clock periods come from
models/common_cdc_clocks.py.
"""

from bisect import bisect_left, bisect_right

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

from models.common_cdc_clocks import random_periods  # noqa: F401  (re-exported)


# Functional-coverage bins of a clock pair, on dst period / src period:
# above 1 the source clock is faster.
CLOCK_BINS = {
    "src_fast_4x": lambda r: r >= 4.0,
    "src_fast": lambda r: 1.25 <= r < 4.0,
    "near_1to1": lambda r: 0.8 < r < 1.25,
    "dst_fast": lambda r: 0.25 < r <= 0.8,
    "dst_fast_4x": lambda r: r <= 0.25,
}


def clock_ratio(src_period: int, dst_period: int) -> float:
    """The CLOCK_BINS value of a clock pair."""
    return dst_period / src_period


def now() -> int:
    return int(get_sim_time("ps"))


async def _start(sig, period: int, phase: int) -> None:
    if phase:
        await Timer(phase, "ps")
    Clock(sig, period, "ps").start()


def start_clock(sig, period: int, phase: int = 0) -> None:
    """Drive ``sig`` low, then toggle it with ``period`` after ``phase``."""
    sig.value = 0
    cocotb.start_soon(_start(sig, period, phase))


class EdgeLog:
    """Times of every rising edge of a clock, recorded from now on, in
    increasing order (lookups bisect, so long runs stay linear)."""

    def __init__(self, clk):
        self.clk = clk
        self.times: list[int] = []
        cocotb.start_soon(self._run())

    async def _run(self) -> None:
        while True:
            await RisingEdge(self.clk)
            self.times.append(now())

    def last_before(self, t: int) -> int:
        """The last rising edge strictly before ``t``."""
        i = bisect_left(self.times, t)
        if not i:
            raise ValueError(f"no rising edge recorded before {t} ps")
        return self.times[i - 1]

    def check_lag(self, start: int, produced: int, what: str) -> None:
        """A synchronizer update launched at ``start`` reached its output at
        the rising edge ``produced``. Zero-delay simulation has no late
        resolution, so that is exactly the second edge after ``start``; an
        edge at ``start`` itself may capture the update or not, so then the
        first edge after it is also correct (the MAS's "two or three edges
        at or after the launch")."""
        after = bisect_right(self.times, produced) - bisect_right(self.times, start)
        i = bisect_left(self.times, start)
        at_start = i < len(self.times) and self.times[i] == start
        ok = after == 2 or (after == 1 and at_start)
        assert ok, (f"{what}: launched at {start} ps, output after {after} edge(s) "
                    f"strictly later (edge at launch: {at_start}); expected 2, "
                    "or 1 when an edge coincides with the launch")


async def falling_edges(clk, n: int) -> None:
    for _ in range(n):
        await FallingEdge(clk)
