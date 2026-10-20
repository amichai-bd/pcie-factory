"""Testbench environment of the pcie_pcs top (wiki/src/dv/pcs/DV_PLAN.md):
the two clocks and the PIPE-side MAC driver and monitor.

Cycle convention (PIPE_CONTROL.md): the MAC drives this cycle's inputs at the
falling edge of clk_i, so the rising edge that ends the cycle samples them,
and reads this cycle's outputs once they settle (ReadOnly). That is exactly
``PipeRef.step``'s convention, so a scoreboard compares one model step per
cycle.
"""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge, Timer
from cocotb.utils import get_sim_time

CLK_PS = 8000                     # clk_i, 125 MHz

# MAC-driven PIPE inputs with their widths and the values the MAC holds
# while it keeps the PCS in reset (PIPE_CONTROL.md, Reset and PhyStatus).
PIPE_INPUTS = {
    "pipe_reset_ni": (1, 0),
    "pipe_tx_data_i": (16, 0),
    "pipe_tx_datak_i": (2, 0),
    "pipe_tx_detect_rx_i": (1, 0),
    "pipe_tx_elec_idle_i": (1, 1),
    "pipe_tx_compliance_i": (1, 0),
    "pipe_rx_polarity_i": (1, 0),
    "pipe_power_down_i": (2, 2),
}

PIPE_OUTPUTS = ("pipe_rx_data_o", "pipe_rx_datak_o", "pipe_rx_valid_o",
                "pipe_phy_status_o", "pipe_rx_elec_idle_o", "pipe_rx_status_o",
                "ser_tx_data_o", "ser_tx_elec_idle_o")


def now_ps() -> int:
    return int(get_sim_time("ps"))


async def _frac_clock(sig, period_ps: float) -> None:
    """A clock whose edges fall on the nearest picosecond of an exact
    fractional period, so a ppm offset accumulates exactly (with at most
    1 ps of edge jitter)."""
    half = period_ps / 2.0
    t0 = now_ps()
    n = 0
    level = 0
    while True:
        n += 1
        nxt = t0 + round(n * half)
        await Timer(nxt - now_ps(), "ps")
        level ^= 1
        sig.value = level


def start_clocks(dut, rx_ppm: float = 0.0, rx_phase_ps: int = 0, tasks: list | None = None) -> float:
    """Start clk_i at 125 MHz and ser_rx_clk_i ``rx_ppm`` faster (positive)
    or slower; returns the RX period in ps."""
    dut.clk_i.value = 0
    dut.ser_rx_clk_i.value = 0
    tx_task = cocotb.start_soon(Clock(dut.clk_i, CLK_PS, unit="ps").start())
    rx_period = CLK_PS / (1.0 + rx_ppm * 1e-6)

    async def rx():
        if rx_phase_ps:
            await Timer(rx_phase_ps, "ps")
        await _frac_clock(dut.ser_rx_clk_i, rx_period)

    rx_task = cocotb.start_soon(rx())
    if tasks is not None:
        tasks.extend((tx_task, rx_task))
    return rx_period


class PipeMac:
    """The MAC side of PIPE: holds the inputs it drives and steps one clk_i
    cycle at a time, returning that cycle's outputs."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.cycle = 0
        self.rst = 1
        self.ins = {name: reset for name, (_, reset) in PIPE_INPUTS.items()}
        self.ser_tx_rxdet = 0
        self.apply()

    def apply(self) -> None:
        self.dut.rst_i.value = self.rst
        self.dut.ser_tx_rxdet_i.value = self.ser_tx_rxdet
        for name, value in self.ins.items():
            getattr(self.dut, name).value = value

    def set(self, **kw) -> None:
        for name, value in kw.items():
            if name == "rst":
                self.rst = value
            elif name == "ser_tx_rxdet":
                self.ser_tx_rxdet = value
            elif name == "reset_n":
                self.ins["pipe_reset_ni"] = value
            else:
                key = f"pipe_{name}_i" if f"pipe_{name}_i" in PIPE_INPUTS else name
                if key not in PIPE_INPUTS:
                    raise KeyError(name)
                self.ins[key] = value

    def held_in_reset(self) -> None:
        """The values the MAC holds while the PCS is in reset."""
        for name, (_, reset) in PIPE_INPUTS.items():
            if name != "pipe_reset_ni":
                self.ins[name] = reset

    async def step(self) -> dict[str, int]:
        """Drive this cycle's inputs at the falling edge, then read its
        outputs."""
        await FallingEdge(self.dut.clk_i)
        self.apply()
        await ReadOnly()
        out = {name: int(getattr(self.dut, name).value) for name in PIPE_OUTPUTS}
        self.cycle += 1
        return out

    def model_inputs(self) -> dict[str, int]:
        """This cycle's inputs in PipeRef.step's argument order."""
        return {
            "rst": self.rst,
            "reset_n": self.ins["pipe_reset_ni"],
            "power_down": self.ins["pipe_power_down_i"],
            "detect_rx": self.ins["pipe_tx_detect_rx_i"],
            "tx_elec_idle": self.ins["pipe_tx_elec_idle_i"],
            "rxdet": self.ser_tx_rxdet,
        }


class EdgeLog:
    """Times of every rising edge of a clock from now on."""

    def __init__(self, clk) -> None:
        self.clk = clk
        self.times: list[int] = []
        self.task = cocotb.start_soon(self._run())

    async def _run(self) -> None:
        while True:
            await RisingEdge(self.clk)
            self.times.append(now_ps())

    def count(self, start: int, end: int) -> int:
        """Rising edges in [start, end]."""
        return sum(1 for t in self.times if start <= t <= end)
