"""Line building for the lock tests of the pcs top (wiki/src/dv/pcs/DV_PLAN.md,
Aligner): partner traffic encoded with running disparity and serialized
bit by bit with models.pcie_pcs_align_model.Line, so filler, electrical
idle, noise, bit slips and bit flips sit at exact bit positions and every
received bit keeps its transmitted index (tb.pcs_rxbench.align_link).

Training traffic numbers its TS1 sets (the N_FTS byte counts), so every
24-symbol window of it is unique and the receive scoreboard can place a
run that starts, or resynchronizes, inside training.
"""

from __future__ import annotations

import random

from models import pcie_pcs_align_model as am
from models.pcie_pcs_model import arch
from tb.pcs_channel import Link
from tb.pcs_rxbench import align_link

K_COM, K_SKP, K_FTS, K_PAD = am.K_COM, am.K_SKP, am.K_FTS, am.K_PAD
SKP_SET = am.SKP_SET
FTS_SET = am.FTS_SET


def ts1(i: int) -> list[tuple[int, int]]:
    """TS1 number ``i``: the N_FTS byte carries i mod 256."""
    return ([(1, K_COM), (1, K_PAD), (1, K_PAD), (0, i % 256), (0, 0x02), (0, 0x00)]
            + [(0, 0x4A)] * 10)


def ts_traffic(n: int, start: int = 0, skp_every: int = 70) -> list[tuple[int, int]]:
    """Comma-dense training: ``n`` numbered TS1 sets, a SKP set every
    ``skp_every`` of them."""
    out: list[tuple[int, int]] = []
    for i in range(n):
        if i % skp_every == 0:
            out += SKP_SET
        out += ts1(start + i)
    return out


def fts_traffic(n: int, start: int = 0) -> list[tuple[int, int]]:
    """The densest comma traffic: FTS sets (a COM every four symbols), a
    numbered TS1 after every eight and a SKP set after every 64."""
    out: list[tuple[int, int]] = []
    for i in range(n):
        if i % 64 == 0:
            out += SKP_SET
        out += FTS_SET
        if i % 8 == 7:
            out += ts1(start + i)
    return out


class Builder:
    """A partner stream and its received line."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.line = am.Line()
        self.syms: list[tuple[int, int]] = []
        self.rd = -1

    def idle(self, words: int) -> None:
        """Electrical idle: the line pads to a word boundary, then ``words``
        words of random bits flagged idle."""
        self.line.pad()
        self.line.noise(self.rng, 20 * words, idle=1)

    def noise(self, bits: int) -> None:
        """Random bits not flagged idle."""
        self.line.noise(self.rng, bits, idle=0)

    def filler(self, bits: int) -> None:
        self.line.filler(bits)

    def send(self, syms: list[tuple[int, int]], events: dict[int, list] | None = None,
             rd: int | None = None) -> int:
        """Encode ``syms`` from the partner's RD (or ``rd``) and serialize
        them; ``events`` maps a symbol index of ``syms`` to slips and flips
        (models.pcie_pcs_align_model.Line.send). Returns the sent index of
        the first symbol."""
        if rd is not None:
            self.rd = rd
        codes, self.rd = arch.tx_encode(syms, None, self.rd)
        base = len(self.syms)
        self.line.send(codes, events, base=base)
        self.syms += syms
        return base

    def link(self) -> Link:
        return align_link(self.syms, self.line)


def slip_events(rng: random.Random, n_syms: int, n: int, kinds=("drop", "repeat"),
                lo: int = 200, spacing: int = 300) -> dict[int, list]:
    """``n`` slips at least ``spacing`` symbols apart, from symbol ``lo``."""
    places = list(range(lo, n_syms - 100, spacing))
    rng.shuffle(places)
    return {p: [(rng.choice(kinds), rng.randrange(10))] for p in sorted(places[:n])}


def skp_slip_events(syms, rng: random.Random, n: int) -> tuple[dict[int, list], list[str]]:
    """Slips on the COM or on one of the SKPs of SKP ordered sets."""
    sets = [i for i in range(200, len(syms) - 8)
            if syms[i] == (1, K_COM) and syms[i + 1] == (1, K_SKP)]
    ev, bins = {}, []
    last = -10**9
    for i in sets:
        if i - last < 300:
            continue
        off = rng.randrange(4)
        ev[i + off] = [(rng.choice(("drop", "repeat")), rng.randrange(10))]
        bins.append("skp_com" if off == 0 else "skp_skp")
        last = i
        if len(bins) >= n:
            break
    return ev, bins
