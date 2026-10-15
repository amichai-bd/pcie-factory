"""Cycle model of pcie_mac_scrambler (wiki/src/rtl/mac_scrambler/MAS.md).

The symbol rules are not restated here: each symbol goes through the mac
architecture model's ``Scrambler`` (wiki/src/arch/mac/SYMBOLS.md#scrambling),
imported from src/arch/mac, located from this file (DV may import arch). This
module adds only what the sub-IP adds: two symbols per word in line order,
``valid_i`` holding the LFSR, and the one-cycle output register.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "mac"))
import pcie_mac_arch as arch  # noqa: E402

SYMS = 2


class WordScrambler:
    """One instance of the sub-IP. ``clock`` applies one rising edge with the
    given inputs; ``valid``, ``data`` and ``datak`` are then the registered
    outputs (``data``/``datak`` hold when ``valid_i`` is 0)."""

    def __init__(self) -> None:
        self.scr = arch.Scrambler()
        self.valid = 0
        self.data = 0
        self.datak = 0

    @property
    def state(self) -> int:
        return self.scr.state

    def word(self, data: int, datak: int, scr: int) -> int:
        """Process one word, symbol 0 ([7:0]) first; return the output data."""
        out = 0
        for i in range(SYMS):
            k = (datak >> i) & 1
            b = (data >> (8 * i)) & 0xFF
            out |= self.scr.feed(k, b, bool((scr >> i) & 1)) << (8 * i)
        return out

    def clock(self, rst: int, valid: int, data: int, datak: int, scr: int) -> None:
        if rst:
            self.scr.state = arch.SEED
            self.valid = 0
            return
        self.valid = valid
        if valid:
            self.data = self.word(data, datak, scr)
            self.datak = datak
