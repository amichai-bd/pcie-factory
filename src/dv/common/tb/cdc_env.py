"""Clock and edge-timing helpers for the common_cdc_* tests
(wiki/src/dv/common/).

Times are integer picoseconds. Clock periods come from
models/common_cdc_clocks.py.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

from models.common_cdc_clocks import random_periods  # noqa: F401  (re-exported)


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

    def check_lag(self, start: int, produced: int, what: str) -> None:
        """A synchronizer update launched at ``start`` reached its output at
        the rising edge ``produced``. Zero-delay simulation has no late
        resolution, so that is exactly the second edge after ``start``; an
        edge at ``start`` itself may capture the update or not, so then the
        first edge after it is also correct (the MAS's "two or three edges
        at or after the launch")."""
        after = sum(start < e <= produced for e in self.times)
        ok = after == 2 or (after == 1 and start in self.times)
        assert ok, (f"{what}: launched at {start} ps, output after {after} edge(s) "
                    f"strictly later (edge at launch: {start in self.times}); expected 2, "
                    "or 1 when an edge coincides with the launch")


async def falling_edges(clk, n: int) -> None:
    for _ in range(n):
        await FallingEdge(clk)
