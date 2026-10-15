"""Cycle model of pcie_pcs_dec8b10b (wiki/src/rtl/pcs/MAS.md, Decoder).

The code itself (which byte a code is at which running disparity, the error
classes and the RD after a code) is the pcs architecture model's
``decode()`` (src/arch/pcs/pcie_pcs_8b10b.py), imported, not copied, as the
DV BKM allows. This file adds only what the MAS adds around it: the
polarity inversion, two symbols per word, and when the RD is known
(reset, unlocked symbols, a lock-generation change, a polarity change).

Symbols are ints with bit 0 first on the line; RD is -1 or +1, None when
unknown.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "pcs"))
import pcie_pcs_8b10b as code  # noqa: E402

K_EDB = code.K_EDB


@dataclass(frozen=True)
class Sym:
    """One decoded symbol and the RD it was decoded at (None: unknown)."""
    data: int
    k: int
    code_err: int
    disp_err: int
    rd_in: int | None
    rd_out: int


class Decoder:
    """The decoder's state: the RD after the last symbol of the last word,
    whether it is known, that symbol's lock generation, and the previous
    cycle's polarity."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.known = False
        self.rd = -1
        self.gen = 0
        self.pol = 0

    def outputs(self, syms: tuple[int, int], lock: tuple[int, int],
                gen: tuple[int, int], pol: int) -> tuple[Sym, Sym]:
        """The combinational outputs for this cycle's inputs."""
        inv = 0x3FF if pol else 0
        known0 = (self.known and pol == self.pol and bool(lock[0])
                  and gen[0] == self.gen)
        known1 = bool(lock[0]) and bool(lock[1]) and gen[1] == gen[0]
        s0 = _decode(syms[0] ^ inv, self.rd if known0 else None)
        s1 = _decode(syms[1] ^ inv, s0.rd_out if known1 else None)
        return s0, s1

    def clock(self, rst: int, valid: int, syms: tuple[int, int],
              lock: tuple[int, int], gen: tuple[int, int], pol: int) -> None:
        """Advance one rising edge."""
        if rst:
            self.reset()
            return
        if valid:
            _, s1 = self.outputs(syms, lock, gen, pol)
            self.known = bool(lock[1])
            self.rd = s1.rd_out
            self.gen = gen[1]
        elif pol != self.pol:
            self.known = False
        self.pol = pol


def _decode(c: int, rd: int | None) -> Sym:
    b, k, rd_out, ce, de = code.decode(c, rd)
    return Sym(b, int(k), int(ce), int(de), rd, rd_out)


def encode(byte: int, k: bool, rd: int) -> tuple[int, int]:
    """The code of (byte, k) at RD ``rd`` and the RD after it."""
    return code.encode(byte, k, rd)


def valid_at(c: int, rd: int) -> bool:
    """Whether the encoder sends code ``c`` at RD ``rd`` (any byte)."""
    return any(r == rd for _, _, r in code.DECODE.get(c, []))


def setter(rd_now: int, rd_want: int) -> int:
    """A data code valid at ``rd_now`` that leaves the RD at ``rd_want``."""
    for byte in range(256):
        c, out = code.encode(byte, False, rd_now)
        if out == rd_want:
            return c
    raise ValueError("no setter")  # pragma: no cover


# D0.0 at RD-: valid only at RD-, a disparity error at RD+, so a probe
# symbol after a code shows the RD that code left.
PROBE = code.encode(0x00, False, -1)[0]
