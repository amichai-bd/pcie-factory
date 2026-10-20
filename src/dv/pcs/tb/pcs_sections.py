"""Pure helpers for receive sections sharing one clock/reset fixture."""

from dataclasses import replace
import hashlib
import random

from models.pcie_pcs_rx_check import K_SKP


def legacy_rng(fullname: str, seed: int) -> tuple[random.Random, int]:
    """cocotb 2.1's original testcase seed, independent of the new owner."""
    effective = seed + int(hashlib.sha1(fullname.encode()).hexdigest(), 16)
    return random.Random(effective), effective


def section_runs(runs, lo: int, hi: int):
    """Whole delivered words mapped inside sent-symbol interval [lo, hi).

    The global checker still checks every boundary word. Local SKP
    statistics omit only boundary-straddling words, and keep the original
    sent indices so check_skp_status uses the same oracle.
    """
    out = []
    for run in runs:
        j = 0
        selected = []
        first = None
        last_position = None
        for word in run.words:
            n = sum(sym != (1, K_SKP) for sym in word.syms)
            positions = [run.sent_of[k] for k in range(j, j + n) if k in run.sent_of]
            # SKP-only words still belong to the preceding mapped COM.
            # Omitting them fabricates a removal in the local oracle.
            inside = (all(lo <= p < hi for p in positions) if positions else
                      last_position is not None and lo <= last_position < hi)
            if inside:
                first = j if first is None else first
                selected.append(word)
            if positions:
                last_position = positions[-1]
            j += n
        if selected:
            n = sum(sym != (1, K_SKP) for word in selected for sym in word.syms)
            out.append(replace(run, words=selected,
                               sent_of={k - first: p for k, p in run.sent_of.items()
                                        if first <= k < first + n}))
    return out
