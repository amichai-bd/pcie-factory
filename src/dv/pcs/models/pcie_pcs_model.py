"""Reference model of the pcie_pcs PIPE control, per clk_i cycle
(wiki/src/arch/pcs/PIPE_CONTROL.md).

It wraps the architecture model's ``PipeControl`` (imported, not copied:
src/arch/pcs is appended to sys.path) and adds what that model leaves out:
the two-cycle transmit latency of ``ser_tx_elec_idle_o`` and the forced
reset values of the receive outputs. Pure Python, so its unit tests run
without a simulator.

Cycle convention: ``step`` takes the inputs seen in cycle n (sampled at the
rising edge that ends it) and returns the outputs during cycle n.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ARCH = Path(__file__).resolve().parents[3] / "arch" / "pcs"
if str(_ARCH) not in sys.path:
    sys.path.append(str(_ARCH))

import pcie_pcs_arch as arch  # noqa: E402

TX_LATENCY = arch.TX_LATENCY
PHY_STATUS_RESET_CYCLES = arch.PHY_STATUS_RESET_CYCLES
RX_DETECT_CYCLES = arch.RX_DETECT_CYCLES
POWER_ACK_CYCLES = arch.POWER_ACK_CYCLES

P0, P0S, P1, P2 = 0, 1, 2, 3
POWER_NAMES = {P0: "P0", P0S: "P0s", P1: "P1", P2: "P2"}
# Transitions the MAC uses (PIPE_CONTROL.md, PowerDown).
LEGAL_ARCS = {(P0, P0S), (P0, P1), (P0, P2), (P0S, P0), (P1, P0), (P2, P0)}


class PipeRef:
    """PIPE control of the pcs top: PhyStatus, RxStatus from receiver
    detection, and the transmitter's electrical idle on the line."""

    def __init__(self) -> None:
        self.pc = arch.PipeControl()
        # Transmit idle of the last TX_LATENCY cycles (oldest first) and
        # whether each was a reset cycle; reset values before time zero.
        self.tx_hist = [1] * TX_LATENCY
        self.rst_hist = [1] * TX_LATENCY

    def step(self, rst: int, reset_n: int, power_down: int, detect_rx: int,
             tx_elec_idle: int, rxdet: int) -> dict[str, int]:
        in_rst = int(bool(rst or not reset_n))
        out = self.pc.step(rst, reset_n, power_down, detect_rx, tx_elec_idle, rxdet)
        # The word seen in cycle n reaches the line in cycle n + 2. A reset
        # cycle drops the words in flight, so the line is idle in it and in
        # the TX_LATENCY - 1 cycles after it.
        line_idle = int(in_rst or any(self.rst_hist[1:]) or self.tx_hist[0])
        self.tx_hist = self.tx_hist[1:] + [out["tx_elec_idle"]]
        self.rst_hist = self.rst_hist[1:] + [in_rst]
        return {
            "phy_status": out["phy_status"],
            "rx_status": out["rx_status"],
            "tx_elec_idle": line_idle,
        }
