"""Channel model of the phy e2e (wiki/src/dv/phy/DV_PLAN.md): one direction
of the serial line between two pcie_phy lower interfaces.

It is stepped once per rising edge of the transmitter's clk_i, which is
also the receiver's ser_rx_clk_i (the recovered clock follows the far
transmitter), so the clock offset in ppm lives in the two clocks, not here.
Each step takes the transmitter's 20-bit word and electrical-idle flag and
returns what the receiver sees ``delay_words`` steps later, with the
impairments of the phy HAS lower interface applied:

- ``slip(+1 | -1)``: one bit repeated or dropped in the next active word;
  the bit stream shifts, so the receiver must find the comma again.
- ``flip(n)``: one random bit inverted in each of the next ``n`` active words.
- ``idle(n)``: the next ``n`` words are forced to electrical idle (a line
  dropout without an EIOS).
- ``invert``: every bit inverted (lane polarity).

While a word is in electrical idle its bits are noise from ``rng``. Idle
starts and ends on whole words here, a case of the phy HAS contract for
``ser_rx_elec_idle_i`` (1 for any word that holds an idle bit; the bits
before the idle in that word are line data).
Python standard library only; deterministic from ``rng``.
"""

from __future__ import annotations

import random
from collections import deque

WORD_BITS = 20
MASK = (1 << WORD_BITS) - 1


class Channel:
    def __init__(self, rng: random.Random, delay_words: int = 4) -> None:
        if delay_words < 1:
            raise ValueError("delay_words: expected at least 1")
        self.rng = rng
        self.bits = 0
        self.nbits = delay_words * WORD_BITS
        self.ei: deque = deque([True] * delay_words)
        self.invert = False
        self._slips: deque = deque()
        self._flips = 0
        self._idle = 0
        self.log: list[tuple[str, int]] = []     # (event, step) of applied impairments
        self.steps = 0

    # ------------------------------------------------------------ hooks

    def slip(self, direction: int) -> None:
        if direction not in (1, -1):
            raise ValueError("slip: expected +1 (repeat a bit) or -1 (drop a bit)")
        self._slips.append(direction)

    def flip(self, n: int = 1) -> None:
        self._flips += n

    def idle(self, n: int) -> None:
        self._idle += n

    def pending(self) -> bool:
        return bool(self._slips or self._flips or self._idle)

    # ------------------------------------------------------------- step

    def step(self, word: int, ei: bool) -> tuple[int, bool]:
        self.steps += 1
        if self._idle:
            self._idle -= 1
            if not ei:
                self.log.append(("idle", self.steps))
            ei = True
        n = WORD_BITS
        if ei:
            word = self.rng.getrandbits(WORD_BITS)
        else:
            if self._flips:
                self._flips -= 1
                word ^= 1 << self.rng.randrange(WORD_BITS)
                self.log.append(("flip", self.steps))
            if self._slips:
                d = self._slips.popleft()
                pos = self.rng.randrange(1, WORD_BITS - 1)
                low, high = word & ((1 << pos) - 1), word >> pos
                if d > 0:      # repeat bit pos-1
                    word = low | (((low >> (pos - 1)) & 1) << pos) | (high << (pos + 1))
                    n += 1
                else:          # drop bit pos
                    word = low | ((high >> 1) << pos)
                    n -= 1
                self.log.append(("slip", self.steps))
        if self.invert:
            word ^= (1 << n) - 1
        self.bits |= (word & ((1 << n) - 1)) << self.nbits
        self.nbits += n
        self.ei.append(ei)
        if self.nbits < WORD_BITS:
            raise RuntimeError("channel bit buffer underflow: too many net dropped bits")
        out = self.bits & MASK
        self.bits >>= WORD_BITS
        self.nbits -= WORD_BITS
        return out, self.ei.popleft()
