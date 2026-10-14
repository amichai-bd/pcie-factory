"""Reference model of the pcie_top_lfsr data scrambler LFSR.

Written from wiki/src/rtl/top_lfsr/MAS.md, bit-serially, without mirroring the
RTL: G(X) = X^16 + X^5 + X^4 + X^3 + 1, seed 0xFFFF, one key bit per shift
taken from state bit 15 before the shift, eight shifts per byte, key bit 0
first. Pure Python, no simulator.
"""

SEED = 0xFFFF
MASK = 0xFFFF


def shift(state: int) -> int:
    """One LFSR shift: left shift, bit 15 fed back into bits 0, 3, 4 and 5."""
    fb = (state >> 15) & 1
    nxt = (state << 1) & MASK
    if fb:
        nxt ^= 0b0000_0000_0011_1001  # taps x^0, x^3, x^4, x^5
    return nxt


def key_byte(state: int) -> int:
    """Scrambling byte produced by the next eight shifts from ``state``."""
    key = 0
    for i in range(8):
        key |= ((state >> 15) & 1) << i
        state = shift(state)
    return key


def advance_byte(state: int) -> int:
    """State after eight shifts."""
    for _ in range(8):
        state = shift(state)
    return state


class Lfsr:
    """Cycle model of pcie_top_lfsr: ``key`` is ``key_o`` for the current
    state; ``clock`` applies one rising edge."""

    def __init__(self) -> None:
        self.state = SEED

    @property
    def key(self) -> int:
        return key_byte(self.state)

    def clock(self, rst: int, reseed: int, advance: int) -> None:
        if rst or reseed:
            self.state = SEED
        elif advance:
            self.state = advance_byte(self.state)


def keystream(n: int, state: int = SEED) -> list[int]:
    """The first ``n`` key bytes from ``state`` with an advance every byte."""
    out = []
    for _ in range(n):
        out.append(key_byte(state))
        state = advance_byte(state)
    return out
