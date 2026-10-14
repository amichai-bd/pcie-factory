"""Architecture model of pcie_top, the byte-stream scrambler
(wiki/src/top/arch/HAS.md).

For definition and performance analysis, not verification: it states the
HAS behavior and its latency as executable Python, and ``perf`` measures a
workload against the HAS targets for ``./build arch`` (wiki/bkm/arch.md).
DV may import it as a reference; it never imports DV code.

The key stream follows the lfsr sub-IP behavior that the HAS links to
(wiki/src/top/lfsr/rtl/MAS.md#behavior): G(X) = X^16 + X^5 + X^4 + X^3 + 1,
seed 0xFFFF, eight shifts per byte, key bit 0 first. Python stdlib only.
"""

from __future__ import annotations

import random
from collections import deque

SEED = 0xFFFF
FEEDBACK = 0x0039  # S[15] feeds back into bits 0, 3, 4 and 5
LATENCY_CYCLES = 1  # one register stage from valid_i to valid_o


def next_key(state: int) -> tuple[int, int]:
    """(key byte, state after eight shifts) for LFSR ``state``."""
    key = 0
    for i in range(8):
        msb = (state >> 15) & 1
        key |= msb << i
        state = ((state << 1) & 0xFFFF) ^ (FEEDBACK if msb else 0)
    return key, state


class Scrambler:
    """Port-level behavior: ``cycle`` applies one clock edge's inputs and
    returns ``(valid_o, data_o)`` as seen after that edge. ``data_o`` is 0,
    not "don't care", while ``valid_o`` is 0."""

    def __init__(self, latency: int = LATENCY_CYCLES) -> None:
        if latency < 1:
            raise ValueError("latency must be >= 1 (outputs are registered)")
        self.latency = latency
        self.state = SEED
        self.pipe: deque[tuple[int, int]] = deque([(0, 0)] * (latency - 1))

    def cycle(self, valid: int = 0, data: int = 0, reseed: int = 0,
              rst: int = 0) -> tuple[int, int]:
        key, advanced = next_key(self.state)
        if rst:
            self.pipe = deque([(0, 0)] * (self.latency - 1))
            self.pipe.append((0, 0))
        else:
            self.pipe.append((1, (data ^ key) & 0xFF) if valid else (0, 0))
        if rst or reseed:
            self.state = SEED
        elif valid:
            self.state = advanced
        return self.pipe.popleft()


def scramble(data: list[int]) -> list[int]:
    """Scramble a back-to-back byte stream that starts from the seed key."""
    model = Scrambler()
    return [model.cycle(valid=1, data=b)[1] for b in data]


_WORKLOAD = {"bytes": int, "valid_ratio": float, "reseed_every": int}


def _workload(w: dict) -> tuple[int, float, int]:
    unknown = set(w) - set(_WORKLOAD)
    if unknown:
        raise ValueError(f"unknown workload key(s) {sorted(unknown)}; "
                         f"known: {sorted(_WORKLOAD)}")
    n = w.get("bytes")
    ratio = w.get("valid_ratio", 1.0)
    every = w.get("reseed_every", 0)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("workload.bytes: expected an integer >= 1")
    if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 < ratio <= 1:
        raise ValueError("workload.valid_ratio: expected a number in (0, 1]")
    if not isinstance(every, int) or isinstance(every, bool) or every < 0:
        raise ValueError("workload.reseed_every: expected an integer >= 0 (0: never)")
    return n, float(ratio), every


def perf(workload: dict, seed: int) -> dict[str, float]:
    """Drive ``workload`` through the model and measure it.

    Workload: ``bytes`` to send; ``valid_ratio``, the chance that the source
    offers a byte in a cycle (1.0: back to back); ``reseed_every``, assert
    ``reseed_i`` with every N-th byte (0: never). Random choices come from
    ``random.Random(seed)``.

    Latency counts cycles from the cycle a byte is on ``data_i`` to the cycle
    it is on ``data_o``. Throughput is output bytes per cycle from the first
    to the last output byte; ``throughput_ratio`` divides it by the offered
    load (input bytes per cycle from the first to the last input byte).
    """
    n, ratio, every = _workload(workload)
    rng = random.Random(seed)
    model = Scrambler()
    in_cycle: list[int] = []
    out_cycle: list[int] = []
    reseeds = 0
    t = 0
    while len(out_cycle) < n:
        valid = len(in_cycle) < n and (ratio >= 1.0 or rng.random() < ratio)
        reseed = 0
        if valid:
            in_cycle.append(t)
            reseed = int(every > 0 and len(in_cycle) % every == 0)
            reseeds += reseed
        valid_o, _ = model.cycle(valid=int(valid), data=rng.randrange(256) if valid else 0,
                                 reseed=reseed)
        if valid_o:
            out_cycle.append(t + 1)  # visible in the cycle after this edge
        t += 1
        if t > 16 * n / ratio + 64:
            raise RuntimeError("workload did not drain; the model lost bytes")
    lat = [o - i for i, o in zip(in_cycle, out_cycle)]
    offered = n / (in_cycle[-1] - in_cycle[0] + 1)
    throughput = n / (out_cycle[-1] - out_cycle[0] + 1)
    return {
        "bytes": n,
        "cycles": out_cycle[-1] - in_cycle[0] + 1,
        "reseeds": reseeds,
        "offered_bytes_per_cycle": round(offered, 6),
        "throughput_bytes_per_cycle": round(throughput, 6),
        "throughput_ratio": round(throughput / offered, 6),
        "latency_cycles_min": min(lat),
        "latency_cycles_max": max(lat),
        "latency_cycles_mean": round(sum(lat) / n, 6),
    }
