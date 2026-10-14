"""Clock and edge-timing helpers for the common_cdc_* tests
(wiki/src/dv/common/).

Times are integer picoseconds. Clock periods are even so that both phases
of every clock are whole picoseconds.
"""

import math

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

MIN_PERIOD_PS = 2_000
MAX_PERIOD_PS = 40_000


def even(ps: float) -> int:
    return max(MIN_PERIOD_PS, 2 * round(ps / 2))


def random_periods(rng, max_ratio: float) -> tuple[int, int]:
    """(src, dst) periods whose ratio is log-uniform in
    [1/max_ratio, max_ratio]."""
    src = even(rng.uniform(MIN_PERIOD_PS, 10_000))
    dst = even(src * math.exp(rng.uniform(-1, 1) * math.log(max_ratio)))
    return src, min(dst, MAX_PERIOD_PS)


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
    """Times of every rising edge of a clock, recorded from now on."""

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
        return max(e for e in self.times if e < t)

    def count(self, start: int, end: int) -> int:
        """Rising edges ``e`` with ``start <= e <= end``."""
        return sum(start <= e <= end for e in self.times)


async def falling_edges(clk, n: int) -> None:
    for _ in range(n):
        await FallingEdge(clk)
