"""Partner traffic kinds of the pcs DV (wiki/src/dv/pcs/DV_PLAN.md): the
symbol streams that load the elastic buffer's corners, written from the
pcs receive-path rules (wiki/src/arch/pcs/RECEIVE.md, Elastic buffer), not
from the RTL. Moved from the elastic buffer's former unit DV (#84) when the
pcs IP took over its tests (Q24); its stream checker, which read the
buffer's internal words, has no place at the pcs ports and was dropped.

The pcs architecture model is imported for the 8b/10b code, its partner
stream and the buffer constants (the arch BKM lets DV import arch;
src/arch/pcs is appended to sys.path, located from this file). Pure Python.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "pcs"))
import pcie_pcs_arch as arch  # noqa: E402
import pcie_pcs_8b10b as code8b10b  # noqa: E402

# Buffer constants (RECEIVE.md, Elastic buffer), taken from the arch model.
EB_WORDS = arch.EB_WORDS
EB_NOMINAL = arch.EB_NOMINAL
EB_HIGH = arch.EB_HIGH
EB_LOW = arch.EB_LOW
EB_HOLDOFF = arch.EB_HOLDOFF_WORDS

COM_CODES = frozenset(code8b10b.ENCODE[(code8b10b.K_COM, True, rd)][0] for rd in (-1, 1))
SKP_CODES = frozenset(code8b10b.ENCODE[(code8b10b.K_SKP, True, rd)][0] for rd in (-1, 1))


# ------------------------------------------------------------------ traffic

def packet_stream(rng: random.Random, sizes, packets: int, skp_interval: int,
                  skp_counts=(3,), lead_ts: int = 8) -> list[tuple[int, int]]:
    """(k, byte) symbols a partner sends: ``lead_ts`` TS1 ordered sets, then
    ``packets`` back-to-back packets (STP, data, END) whose data length is
    drawn from ``sizes`` (a list, or a callable of ``rng``), with a SKP
    ordered set due every ``skp_interval`` symbols, deferred to the end of
    an ordered set or packet and sent back to back when several are due
    (the rule of arch.make_stream; RECEIVE.md, Depth versus clock offset).
    Each set is a COM and a SKP count drawn from ``skp_counts``."""
    out: list[tuple[int, int]] = []
    credit = 0

    def skp_set() -> None:
        nonlocal credit
        n = rng.choice(skp_counts)
        out.extend([(1, code8b10b.K_COM)] + [(1, code8b10b.K_SKP)] * n)
        credit += 1 + n

    def boundary() -> None:
        nonlocal credit
        while credit >= skp_interval:
            credit -= skp_interval
            skp_set()

    ts1 = [(1, code8b10b.K_COM), (1, code8b10b.K_PAD), (1, code8b10b.K_PAD),
           (0, 0x80), (0, 0x02), (0, 0x00)] + [(0, 0x4A)] * 10
    for _ in range(lead_ts):
        boundary()
        out.extend(ts1)
        credit += len(ts1)
    for _ in range(packets):
        boundary()
        n = sizes(rng) if callable(sizes) else rng.choice(sizes)
        out.append((1, code8b10b.K_STP))
        out.extend((0, rng.randrange(256)) for _ in range(n))
        out.append((1, code8b10b.K_END))
        credit += n + 2
    boundary()
    skp_set()
    return out


KINDS = ("tlp", "dllp", "random", "stress", "dense", "short_burst")


def link_symbols(rng: random.Random, kind: str, packets: int,
                 skp_interval: int) -> list[tuple[int, int]]:
    """A partner stream of one kind: ``tlp`` (maximum TLPs), ``dllp``,
    ``random`` (random TLP and DLLP sizes, as arch.make_stream, with SKP
    ordered sets of 1 to 5 SKPs), ``stress`` (sizes chosen to strand
    corrections: 4122, 3070, 1534 and 2 bytes), ``dense`` (short packets with
    SKP ordered sets of 1 to 5 SKPs every ``skp_interval`` symbols, for the
    corner cases) or ``short_burst`` (a packet just under two SKP intervals,
    so one set too few goes out after it, then a maximum TLP; the case the
    #73 sign-off carried forward)."""
    if kind == "tlp":
        sizes = [arch.TLP_BYTES_MAX]
    elif kind == "dllp":
        sizes = [6]
    elif kind == "random":
        sizes = lambda r: 2 + r.choice((12, 16)) + 4 * r.randrange(0, 65) + 4  # noqa: E731
    elif kind == "stress":
        sizes = [4122, 3070, 1534, 2]
    elif kind == "dense":
        sizes = lambda r: r.randrange(0, 48)  # noqa: E731
    elif kind == "short_burst":
        # 2 * interval - 2 (STP, END) - 4 (the set) - 1: one set is sent
        # after a gap that needed two.
        sizes = [2 * skp_interval - 7, arch.TLP_BYTES_MAX]
    else:
        raise ValueError(f"unknown traffic kind {kind!r}")
    counts = (1, 2, 3, 4, 5) if kind in ("random", "dense") else (3,)
    return packet_stream(rng, sizes, packets, skp_interval, counts)


def plain_symbols(rng: random.Random, n: int) -> list[tuple[int, int]]:
    """Eight TS1 ordered sets for lock, then data only, no ordered set:
    nothing to add or remove (overflow and underflow tests)."""
    return packet_stream(rng, [0], 0, 10**9, lead_ts=8)[:-4] + \
        [(0, rng.randrange(256)) for _ in range(n)]
