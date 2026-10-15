"""Reference model of pcie_pcs_align, the comma aligner, and of the
traffic and channel its tests need.

Written from wiki/src/rtl/pcs/MAS.md (Aligner) and the pcs receive path
(wiki/src/arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock), not from
the RTL. The 8b/10b code, the training and packet traffic and the relock
constants come from the pcs architecture model, imported (not copied) from
src/arch/pcs, located relative to this file. Pure Python, no simulator.

Bit order everywhere: bit 0 of a word or code is the earliest on the line.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "pcs"))
import pcie_pcs_8b10b as code8b10b  # noqa: E402
import pcie_pcs_arch as arch  # noqa: E402

WORD_BITS = 20
SYM_BITS = 10
COMMA_FORMS = (code8b10b.COMMA_MINUS, code8b10b.COMMA_PLUS)
ERR_LIMIT = arch.ERR_LIMIT              # errored words that request a relock
ERR_DECAY_WORDS = arch.ERR_DECAY_WORDS  # clean words that forgive one

K_COM, K_SKP, K_FTS, K_PAD = code8b10b.K_COM, code8b10b.K_SKP, code8b10b.K_FTS, code8b10b.K_PAD
K_STP, K_END = code8b10b.K_STP, code8b10b.K_END


def comma_at(window: int, p: int) -> bool:
    """A comma (bits a to g) starts at bit ``p`` of ``window``."""
    return (window >> p) & 0x7F in COMMA_FORMS


class Aligner:
    """Cycle model of pcie_pcs_align. ``clock`` applies one rising edge of
    the RX clock with the inputs sampled at it; afterwards ``sym``,
    ``lock`` and ``gen`` are the outputs. ``acquired`` says whether that
    edge took lock, ``offset`` is the symbol boundary."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.cur = 0          # last registered input word
        self.prev = 0         # the word before it
        self.idle = 1         # registered electrical idle of cur
        self.idle_prev = 1
        self.lock = 0
        self.gen = 0
        self.offset = 0
        self.sym = 0
        self.acquired = False

    def clock(self, data: int, idle: int, relock: int, rst: int = 0) -> None:
        if rst:
            cur = self.cur
            self.reset()
            self.prev, self.cur = cur, data  # data registers are not reset
            return
        window = self.prev | (self.cur << WORD_BITS)
        # Loss of lock: idle seen on the current word, or a relock request.
        lock = self.lock and not relock and not self.idle
        self.acquired = False
        if not lock and not self.idle and not self.idle_prev:
            for p in range(WORD_BITS):
                if comma_at(window, p):
                    lock, self.offset, self.acquired = 1, p % SYM_BITS, True
                    self.gen ^= 1
                    break
        self.lock = int(lock)
        self.sym = (window >> self.offset) & ((1 << WORD_BITS) - 1)
        self.prev, self.cur = self.cur, data
        self.idle_prev, self.idle = self.idle, int(idle)


class ErrorCounter:
    """The clk_i-side relock rule of the pcs (RECEIVE.md, Relock request),
    which the pcs top owns: fed every word that reaches PIPE (two codes,
    their lock tag and generation), it returns True when it sends a relock
    request. Decoding restarts with an unknown running disparity at every
    unlocked word and at every new lock generation."""

    def __init__(self) -> None:
        self.count = 0
        self.clean = 0
        self.wait = False
        self.wait_gen = 0
        self.last_gen: int | None = None
        self.rd: int | None = None
        self.requests = 0

    def word(self, codes: tuple[int, int], lock: int, gen: int) -> bool:
        if not lock:
            self.rd = None
            return False
        if gen != self.last_gen:
            self.last_gen, self.count, self.clean, self.rd = gen, 0, 0, None
        if self.wait and gen != self.wait_gen:
            self.wait = False                      # the aligner has relocked
        errored = False
        for c in codes:
            _, _, self.rd, ce, de = code8b10b.decode(c, self.rd)
            errored |= ce or de
        if self.wait:
            return False
        if errored:
            self.count += 1
            self.clean = 0
            if self.count >= ERR_LIMIT:
                self.count, self.wait, self.wait_gen = 0, True, gen
                self.requests += 1
                return True
        else:
            self.clean += 1
            if self.clean >= ERR_DECAY_WORDS:
                self.count, self.clean = max(0, self.count - 1), 0
        return False


# ------------------------------------------------------------------ traffic

TS1 = ([(1, K_COM), (1, K_PAD), (1, K_PAD), (0, 0x80), (0, 0x02), (0, 0x00)]
       + [(0, 0x4A)] * 10)
SKP_SET = [(1, K_COM), (1, K_SKP), (1, K_SKP), (1, K_SKP)]
FTS_SET = [(1, K_COM), (1, K_FTS), (1, K_FTS), (1, K_FTS)]


def ts_traffic(n: int, skp_every: int = 70) -> list[tuple[int, int]]:
    """Comma-dense training traffic: ``n`` TS1 sets, a SKP set every
    ``skp_every`` of them."""
    out: list[tuple[int, int]] = []
    for i in range(n):
        if i % skp_every == 0:
            out += SKP_SET
        out += TS1
    return out


def fts_traffic(n: int, skp_every: int = 64) -> list[tuple[int, int]]:
    """The densest comma traffic: ``n`` FTS sets (a COM every four
    symbols), a SKP set every ``skp_every`` of them."""
    out: list[tuple[int, int]] = []
    for i in range(n):
        if i % skp_every == 0:
            out += SKP_SET
        out += FTS_SET
    return out


def packet_traffic(rng: random.Random, n: int, sizes=(6, 20, 200)) -> list[tuple[int, int]]:
    """Packet traffic: STP, random bytes, END, a SKP set every few packets."""
    out: list[tuple[int, int]] = []
    for p in range(n):
        if p % 6 == 0:
            out += SKP_SET
        out.append((1, K_STP))
        out += [(0, rng.randrange(256)) for _ in range(rng.choice(sizes))]
        out.append((1, K_END))
    return out


def encode(symbols: list[tuple[int, int]], rd: int = -1) -> list[int]:
    """Encode with running disparity (the arch model's transmit encoder)."""
    return arch.tx_encode(symbols, rd=rd)[0]


# ------------------------------------------------------------------ channel

@dataclass
class Line:
    """Received bits with, per bit, the transmitted bit index (-1 for
    noise or a repeated bit) and the electrical-idle flag; ``slips`` and
    ``flips`` hold the received bit index of each impairment."""
    bits: list[int] = field(default_factory=list)
    txpos: list[int] = field(default_factory=list)
    idle: list[int] = field(default_factory=list)
    slips: list[int] = field(default_factory=list)
    flips: list[int] = field(default_factory=list)

    def add(self, bit: int, txpos: int, idle: int = 0) -> None:
        self.bits.append(bit)
        self.txpos.append(txpos)
        self.idle.append(idle)

    def noise(self, rng: random.Random, n: int, idle: int) -> None:
        for _ in range(n):
            self.add(rng.randrange(2), -1, idle)

    def filler(self, n: int) -> None:
        """``n`` alternating bits (no comma, not idle): a bit-offset shim."""
        for i in range(n):
            self.add(i & 1, -1)

    def send(self, codes: list[int], events: dict[int, list] | None = None,
             base: int = 0) -> None:
        """Serialize ``codes``; ``events`` maps a symbol index to a list of
        ("drop" | "repeat", bit) slips and ("flip", bit) errors. ``base``
        offsets the transmitted symbol index recorded in txpos."""
        events = events or {}
        for s, c in enumerate(codes):
            ev = events.get(s, [])
            for i in range(SYM_BITS):
                bit = (c >> i) & 1
                if ("flip", i) in ev:
                    self.flips.append(len(self.bits))
                    bit ^= 1
                if ("drop", i) in ev:
                    self.slips.append(len(self.bits))
                    continue
                if ("repeat", i) in ev:
                    self.slips.append(len(self.bits))
                    self.add(bit, -1)
                self.add(bit, 10 * (base + s) + i)

    def pad(self) -> None:
        """Pad with idle noise to a whole number of words."""
        while len(self.bits) % WORD_BITS:
            self.add(0, -1, 1)

    def words(self) -> list[tuple[int, int, list[int]]]:
        """Per 20-bit word: (data, elec_idle, txpos of its bits). The idle
        flag of a word is that of its last bit."""
        out = []
        for w in range(len(self.bits) // WORD_BITS):
            b = w * WORD_BITS
            data = sum(self.bits[b + i] << i for i in range(WORD_BITS))
            out.append((data, self.idle[b + WORD_BITS - 1], self.txpos[b:b + WORD_BITS]))
        return out


def symbol_aligned(txpos: list[int], p: int) -> int | None:
    """The transmitted symbol index if the ten bits from ``p`` of a txpos
    window are exactly one whole transmitted symbol, else None."""
    t = txpos[p]
    if t < 0 or t % SYM_BITS or any(txpos[p + i] != t + i for i in range(SYM_BITS)):
        return None
    return t // SYM_BITS
