"""The DV channel model of the pcs top (wiki/src/dv/pcs/DV_PLAN.md): a link
partner's symbol stream, serialized and impaired, driven onto the lower
receive interface at the RX clock.

The stream, the 8b/10b encoding and the impairments (bit slips, single-bit
errors, electrical-idle gaps with noise, lane inversion) come from the pcs
architecture model (make_stream, tx_encode, channel), imported through
models.pcie_pcs_model, so the channel is the one the HAS targets were
measured with. Close bit-error pairs, which that channel never places, are
added here. ``RxChannel`` drives twenty bits per ser_rx_clk_i cycle at the
falling edge and records the rising edge that samples each word.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge

from models.pcie_pcs_model import arch
from tb.pcs_env import now_ps


@dataclass
class Link:
    """One partner transmission: the symbols sent, the received line and
    where the impairments are (symbol indices; with no slip, symbol s is
    line bits 10 s to 10 s + 9)."""
    syms: list[tuple[int, int]]
    line: arch.Line
    idles: list[tuple[int, int]]
    training: int
    close_errors: list[int] = field(default_factory=list)
    rx_bit: dict[int, int] | None = None   # transmitted bit -> received bit (align lines)


def build_link(rng: random.Random, packets: int, mix: str = "tlp", tlp_bytes: int = 274,
               skp_interval: int = arch.SKP_INTERVAL_MIN, slips: int = 0, errors: int = 0,
               idle_gaps: int = 0, idle_symbols: int = 0, polarity: bool = False,
               training: int = 32, close_pairs: int = 0, pair_gap: int = 4,
               bursts: int = 0, eios: bool = False) -> Link:
    syms, idles = arch.make_stream(rng, packets, mix, tlp_bytes, skp_interval, idle_gaps,
                                   idle_symbols, lead_ts=training, eios=eios)
    codes, _ = arch.tx_encode(syms)
    line = arch.channel(rng, codes, idles, slips, errors, polarity)
    link = Link(syms, line, idles, 16 * training)
    if close_pairs:
        assert not slips, "close pairs need the slip-free bit positions"
        # Two single-bit errors pair_gap symbols apart in packet data, chosen
        # (by decoding the corrupted line with the architecture model) so
        # that they mark errored symbols in at least three PIPE words at
        # either word parity: the pairs that reach the relock limit. Pairs
        # are spread evenly, far from each other and from other errors.
        rd_before, rd = [], -1
        for k, b in syms:
            rd_before.append(rd)
            _, rd = arch.code8b10b.encode(b, bool(k), rd)
        data = [s for s, (k, _) in enumerate(syms) if s > 2000 and not k
                and all(not syms[s + d][0] for d in range(-8, pair_gap + 24))]
        step = max(1, len(data) // (close_pairs + 1))
        for n in range(1, close_pairs + 1):
            for s in data[n * step:n * step + step // 2]:
                bits = (rng.randrange(10), rng.randrange(10))
                errs = _errored(codes, rd_before[s], s, pair_gap, bits)
                if all(len({(i + p) // 2 for i in errs}) >= 3 for p in (0, 1)):
                    break
            else:
                continue
            for t, bit in zip((s, s + pair_gap), bits):
                b = 10 * t + bit
                line.bits[b] ^= 1
                line.errors.append(b)
            link.close_errors.append(s)
    if bursts:
        # Bursts of two to four single-bit errors, 1 to 40 symbols apart:
        # error densities around the relock limit and its decay.
        assert not slips
        lo, hi = 2000, len(syms) - 200
        step = (hi - lo) // (bursts + 1)
        for n in range(1, bursts + 1):
            s = lo + n * step
            for _ in range(rng.randrange(2, 5)):
                if any(a <= s < b + 200 for a, b in idles):
                    break
                b = 10 * s + rng.randrange(10)
                line.bits[b] ^= 1
                line.errors.append(b)
                s += rng.randrange(1, 41)
    return link


def _errored(codes: list[int], rd: int, s: int, gap: int, bits: tuple[int, int]) -> list[int]:
    """Symbols decoded with an error when bits[0] of code s and bits[1] of
    code s + gap are flipped, decoding from the sender's RD at s."""
    errs = []
    cur: int | None = rd
    for i in range(s, s + gap + 24):
        c = codes[i]
        if i == s:
            c ^= 1 << bits[0]
        if i == s + gap:
            c ^= 1 << bits[1]
        _, _, cur, ce, de = arch.code8b10b.decode(c, cur)
        if ce or de:
            errs.append(i)
    return errs


class RxChannel:
    """Drives ser_rx_data_i and ser_rx_elec_idle_i, one word per RX cycle
    (the flag of a word is that of its last bit, so it is 1 for any word
    that holds an idle bit, as the HAS contract says, and the bits before
    the idle are line data):
    ``lead`` words of idle noise, then the link's line (or, with
    ``source``, words taken from a queue), then idle noise. ``times[w]`` is
    the rising edge that sampled word w."""

    def __init__(self, dut, rng: random.Random, line: arch.Line | None = None,
                 lead: int = 64, source: deque | None = None) -> None:
        self.dut = dut
        self.rng = rng
        self.lead = lead
        self.source = source
        self.words: list[tuple[int, int]] = []
        if line is not None:
            for base in range(0, len(line.bits) - 19, 20):
                v = 0
                for i in range(19, -1, -1):
                    v = (v << 1) | line.bits[base + i]
                self.words.append((v, line.idle[base + 19]))
        self.times: list[int] = []
        self.done = False
        dut.ser_rx_elec_idle_i.value = 1
        dut.ser_rx_data_i.value = 0
        cocotb.start_soon(self._run())

    def time_of_bit(self, bit: int) -> int:
        """When the word holding line bit ``bit`` was sampled."""
        return self.times[self.lead + bit // 20]

    async def _run(self) -> None:
        w = 0
        while True:
            await FallingEdge(self.dut.ser_rx_clk_i)
            i = w - self.lead
            if self.source is not None and i >= 0:
                if self.source:
                    data, idle = self.source.popleft(), 0
                else:
                    data, idle = self.rng.getrandbits(20), 1
            elif 0 <= i < len(self.words):
                data, idle = self.words[i]
            else:
                data, idle = self.rng.getrandbits(20), 1
                if i >= len(self.words) and self.source is None:
                    self.done = True
            self.dut.ser_rx_data_i.value = data
            self.dut.ser_rx_elec_idle_i.value = idle
            await RisingEdge(self.dut.ser_rx_clk_i)
            self.times.append(now_ps())
            w += 1
