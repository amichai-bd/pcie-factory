"""Receive-side bench of the pcs top (wiki/src/dv/pcs/DV_PLAN.md): the
clocks, the line driver, the MAC in P0 and the PIPE receive monitor, with
the receive checks every receive test shares (stream, SKP sets, RX
latency, lock latency and lock start) and their functional coverage.

A test builds a ``Link`` (the symbols a partner sent and the received
line: tb.pcs_channel.build_link for the architecture model's channel, or
``align_link`` for a line built bit by bit with models.pcie_pcs_align_model,
whose every bit carries its transmitted index), drives it with
``RxBench`` and checks the words seen on PIPE against the symbols sent
(models.pcie_pcs_rx_check).
"""

from __future__ import annotations

import random
from collections import deque

import cocotb
from cocotb.triggers import FallingEdge

from models.pcie_pcs_model import P0
from models.pcie_pcs_rx_check import K_COM, K_SKP, Stream, Word, check_runs, check_skp_status
from regress.funcov import Covergroup
from tb.pcs_bench import Bench, regress_seed
from tb.pcs_channel import Link, RxChannel
from tb.pcs_env import CLK_PS, now_ps, start_clocks

CYCLE = CLK_PS
# Words from the one that makes the relock request to RxValid low: crossing
# 4 (two or three RX edges), the aligner, the elastic buffer at its highest
# fill (12 symbols) and the PIPE register.
RELOCK_DELAY = 20
RX_LATENCY_MAX = 12.0             # HAS RX latency target, cycles
LOCK_LATENCY_MAX = 10.0           # HAS lock target, cycles

# Functional coverage of the receive path (DV plan, Strategy): every
# RxStatus code a receive word carries (5 and 6 only outside the targets),
# the clock offsets, the scenarios and the relock causes.
COV_RX = Covergroup("pcs_rx")
COV_RX.coverpoint("status", bins={"ok": 0, "skp_added": 1, "skp_removed": 2, "code_err": 4,
                                  "overflow": 5, "underflow": 6, "disp_err": 7})
COV_RX.coverpoint("ppm", bins={"slow": "slow", "zero": "zero", "fast": "fast"})
COV_RX.coverpoint("scenario", bins=["clean", "max_tlp", "dllp", "random", "polarity",
                                    "polarity_toggle", "errors", "close_errors", "slips",
                                    "error_bursts", "elec_idle", "loopback", "round_trip",
                                    "eb_stress"])
COV_RX.coverpoint("relock", bins=["slip", "close_errors", "noise_lock"])

# The RxStatus codes the receive path gives within the targets, with the
# clock offset that produces them (required at L2: pcs_rx_status floor 100).
# A slow RX clock drains the buffer, so SKP symbols are added; a fast one
# fills it, so they are removed. Errors and plain data at either sign.
COV_STATUS = Covergroup("pcs_rx_status")
COV_STATUS.coverpoint("code_ppm", bins={
    f"{name}__{sign}": (code, sign)
    for name, code, signs in (("ok", 0, ("slow", "fast")), ("code_err", 4, ("slow", "fast")),
                              ("disp_err", 7, ("slow", "fast")), ("skp_added", 1, ("slow",)),
                              ("skp_removed", 2, ("fast",)))
    for sign in signs})


def ppm_bin(ppm: float) -> str:
    return "zero" if ppm == 0 else ("fast" if ppm > 0 else "slow")


def seeded_ppm(rng: random.Random, lo: float = 300, hi: float = 600) -> float:
    """A clock offset of seeded magnitude; the seed's parity picks the sign."""
    return (1 if regress_seed() % 2 else -1) * rng.uniform(lo, hi)


def align_link(syms: list[tuple[int, int]], line) -> Link:
    """A Link for a line built with models.pcie_pcs_align_model.Line (its
    bits carry their transmitted bit index, so filler, slips and noise are
    placed exactly); ``rx_bit`` maps a transmitted bit to the received one."""
    line.pad()
    link = Link(syms, line, [], 0)
    link.rx_bit = {t: b for b, t in enumerate(line.txpos) if t >= 0}
    if not hasattr(line, "errors"):
        line.errors = list(line.flips)
    return link


FILL_MIN, FILL_MAX = 4, 12        # HAS elastic-buffer target, visible fill in symbols


class FillCheck:
    """The HAS visible-fill window (4 to 12 symbols), on the MAS-named net
    `eb_fill` of the top (the elastic buffer's `fill_o`), read every clk_i
    cycle as an observation net (DV BKM, scope and oracle). The window holds
    only within the targets (clock offset up to 600 ppm, SKP ordered sets as
    the receive path requires), so a test turns the check on with ``on``;
    the overflow, underflow and out-of-target stress tests leave it off. It
    starts once the buffer has started (fill 8), stops while `rst_i` is high
    and after the line ends (``done``)."""

    def __init__(self, dut, done) -> None:
        self.dut = dut
        self.done = done
        self.on = False
        self.started = False
        self.lo, self.hi = 99, -1
        self.samples = 0
        cocotb.start_soon(self._run())

    async def _run(self) -> None:
        dut = self.dut
        while True:
            await FallingEdge(dut.clk_i)
            if int(dut.rst_i.value):
                self.started = False
                continue
            fill = int(dut.eb_fill.value)
            if not self.started:
                self.started = fill >= 8
                continue
            if not self.on or self.done():
                continue
            self.samples += 1
            self.lo, self.hi = min(self.lo, fill), max(self.hi, fill)
            assert FILL_MIN <= fill <= FILL_MAX, (
                f"t={now_ps()} ps: visible fill {fill} outside {FILL_MIN}..{FILL_MAX}")


class Recentre:
    """Counts the elastic buffer's recentring on its MAS-named observation
    nets: `u_elastic.r_recentre` (read side holds, clk_i) and
    `u_elastic.w_drop` (write side drops an unlocked word, ser_rx_clk_i),
    outside reset. A test asserts the count its offset needs, so the
    recentring check is never vacuous."""

    def __init__(self, dut) -> None:
        self.holds = 0
        self.drops = 0
        eb = dut.u_elastic
        cocotb.start_soon(self._count(dut.clk_i, dut.rst_i, eb.r_recentre, "holds"))
        cocotb.start_soon(self._count(dut.ser_rx_clk_i, dut.rst_i, eb.w_drop, "drops"))

    async def _count(self, clk, rst, net, field: str) -> None:
        while True:
            await FallingEdge(clk)
            if not int(rst.value) and int(net.value):
                setattr(self, field, getattr(self, field) + 1)


class RxBench:
    """Clocks, the channel, the MAC in P0 and the PIPE receive monitor. The
    visible-fill window check (``FillCheck``) is on unless ``fill_window``
    is False (tests outside the targets)."""

    def __init__(self, dut, rng: random.Random, ppm: float, fill_window: bool = True) -> None:
        self.fill_window = fill_window
        self.fill: FillCheck | None = None
        self.dut = dut
        self.rng = rng
        self.ppm = ppm
        self.words: list[Word] = []
        self.b: Bench | None = None
        self.chan: RxChannel | None = None
        self.link: Link | None = None

    async def start(self, link: Link | None = None, source: deque | None = None,
                    polarity: int = 0, max_cycles: int = 200_000, phase_ps: int | None = None,
                    lead: int = 96) -> None:
        dut = self.dut
        dut._log.info("RX clock %+.1f ppm", self.ppm)
        start_clocks(dut, self.ppm, self.rng.randrange(CLK_PS) if phase_ps is None else phase_ps)
        self.link = link
        self.chan = RxChannel(dut, self.rng, link.line if link else None, lead=lead,
                              source=source)
        self.fill = FillCheck(dut, lambda: self.chan.done)
        self.recentre = Recentre(dut)
        self.fill.on = self.fill_window
        self.b = Bench(dut, max_cycles, receive=True)
        self.b.tick = self._wrap(self.b.tick)
        await self.b.reset("rst_i", 8)
        self.b.mac.set(rx_polarity=polarity)
        await self.b.power(P0)

    def _wrap(self, tick):
        async def inner():
            out = await tick()
            k, d = out["pipe_rx_datak_o"], out["pipe_rx_data_o"]
            w = Word(now_ps() - CLK_PS // 2, out["pipe_rx_valid_o"],
                     ((k & 1, d & 0xFF), ((k >> 1) & 1, d >> 8)), out["pipe_rx_status_o"],
                     out["pipe_rx_elec_idle_o"])
            # Electrical idle is in order with the words: never a word under
            # RxValid while RxElecIdle is 1.
            assert not (w.valid and w.elec_idle), f"t={w.t} ps: RxValid 1 with RxElecIdle 1"
            self.words.append(w)
            if w.status != 3 and (w.valid or w.status in (5, 6)):
                COV_RX.sample(status=w.status, ppm=ppm_bin(self.ppm))
                if self.ppm and w.status in (0, 1, 2, 4, 7):
                    COV_STATUS.sample(code_ppm=(w.status, ppm_bin(self.ppm)))
            return out
        return inner

    async def run_line(self, drain: int = 200) -> None:
        """Idle in P0 (TxElecIdle 1) until the line has been sent, then
        drain."""
        self.b.mac.set(tx_elec_idle=1)
        while not self.chan.done:
            await self.b.tick()
        await self.b.run(drain)
        if self.fill.on:
            assert self.fill.samples, "fill window never checked"
            self.dut._log.info("visible fill %d..%d over %d cycles", self.fill.lo, self.fill.hi,
                               self.fill.samples)

    def rx_bit(self, tx_bit: int) -> int:
        """The received index of a transmitted bit (a dropped bit: the
        received index of the bit before it)."""
        m = getattr(self.link, "rx_bit", None) if self.link else None
        if m is None:
            return tx_bit
        for b in range(tx_bit, max(-1, tx_bit - 20), -1):
            if b in m:
                return m[b]
        return tx_bit

    def tx_time(self, tx_bit: int) -> float:
        """When the word holding transmitted bit ``tx_bit`` was sampled
        (for an arch-model line, a slip moves it by a bit)."""
        b = self.rx_bit(tx_bit)
        w = self.chan.lead + b // 20
        return self.chan.times[w] if w < len(self.chan.times) else float("inf")

    def arrival(self, s: int) -> float:
        """When the last bit of sent symbol s was sampled."""
        return self.tx_time(10 * s + 9)

    def check(self, link, where: str, **kw):
        """check_runs with this bench's arrival times."""
        return check_runs(link.syms, self.words, where=where, arrival=self.arrival, **kw)

    def at_end(self, i: int) -> bool:
        """Word i is after the last word of the line reached PIPE."""
        return self.words[i].t > self.chan.times[min(len(self.chan.times) - 1,
                                                     self.chan.lead + len(self.chan.words))]

    def statuses(self) -> dict[int, int]:
        c: dict[int, int] = {}
        for w in self.words:
            c[w.status] = c.get(w.status, 0) + 1
        return c


def check_latency(rx: RxBench, runs, target: float = RX_LATENCY_MAX,
                  settle: list[float] = ()) -> tuple[float, float]:
    """RX latency of every delivered symbol: from the RX edge that sampled
    its last bit to the clk_i edge that put it on PIPE. The first word of a
    lock is left out: it may hold the symbol before the comma, received
    before lock (the lock target times that word). So are the symbols that
    leave within 96 cycles after a time in ``settle`` (a slip: a relock that
    moves the symbol boundary shifts the words by up to one cycle).
    Returns the lowest and highest."""
    best, worst, at = float("inf"), 0.0, None
    for r in runs:
        for t, s in r.placed:
            if t == r.words[0].t or any(x <= t <= x + 96 * CYCLE for x in settle):
                continue
            lat = (t - rx.arrival(s)) / CYCLE
            if lat > worst:
                worst, at = lat, (s, t, r.words[0].t)
            best = min(best, lat)
    rx.dut._log.info("RX latency %.2f to %.2f cycles", best, worst)
    if worst > target:
        rx.dut._log.info("highest: sent symbol %d on PIPE at %d ps, run from %d ps", *at)
    assert worst <= target, f"RX latency {worst:.2f} cycles above the {target} target"
    return best, worst


def check_clean(rx: RxBench, link, where: str) -> list:
    """One lock, everything delivered in order, no error or buffer status,
    SKP changes on the COM's word, latency within target."""
    runs = rx.check(link, where)
    assert len(runs) == 1, f"{where}: {len(runs)} RxValid runs, expected one lock"
    r = runs[0]
    assert r.mismatches == 0, f"{where}: {r.mismatches} wrong symbols"
    bad = {c: n for c, n in rx.statuses().items() if c in (4, 5, 6, 7)}
    assert not bad, f"{where}: RxStatus counts {bad} on a clean line"
    n_sets = check_skp_status(link.syms, runs, where)
    sent_nonskp = sum(1 for s in link.syms if s != (1, K_SKP))
    tail = len(Stream(link.syms).syms) - r.last
    assert tail <= 2, f"{where}: {tail} symbols at the end never delivered (of {sent_nonskp})"
    check_latency(rx, runs)
    rx.dut._log.info("%s: %d symbols, %d SKP sets checked, statuses %s", where, sent_nonskp,
                     n_sets, rx.statuses())
    return runs


def lock_com(syms, r) -> int:
    """The sent COM that gave a run its lock: the first or second symbol of
    the aligner's first locked word, so at most a word before the run's first
    symbol once the elastic buffer has packed it (an unlocked symbol and a
    locked one never share a PIPE word). ``r.first`` skips SKP symbols, so
    a COM of a SKP ordered set may sit up to five symbols before it; the
    nearest COM is the one."""
    for i in range(r.first + 1, max(-1, r.first - 7), -1):
        if 0 <= i < len(syms) and syms[i] == (1, K_COM):
            return i
    raise AssertionError(f"run at {r.words[0].t} ps does not start at a COM: "
                         f"{syms[max(0, r.first - 6):r.first + 2]}")


def check_lock_latency(rx: RxBench, link, runs, where: str,
                       target: float = LOCK_LATENCY_MAX) -> float:
    """HAS Lock target: the first word of each new lock on PIPE within
    ``target`` cycles of the RX edge that sampled the last bit of the comma
    that gave lock. Lock start: that first word holds the comma, or the
    symbol before it and the comma (the aligner's first locked word, never
    an unlocked symbol's partner), so the run starts at the comma or at the
    symbol before it. A run locked on noise (a comma in the idle-exit
    noise, model.pcie_pcs_rx_check) has no sent comma; it is counted, not
    timed."""
    worst, noise = 0.0, 0
    for r in runs:
        if r.noise_lock:
            noise += 1
            continue
        com = lock_com(link.syms, r)
        before = max([i for i in range(com) if link.syms[i] != (1, K_SKP)], default=com)
        assert r.start in (com, before), (
            f"{where}: run at {r.words[0].t} ps starts at sent symbol {r.start}, not at its "
            f"lock comma {com} or the symbol {before} before it (lock start)")
        lat = (r.words[0].t - rx.tx_time(10 * com + 6)) / CYCLE
        worst = max(worst, lat)
    rx.dut._log.info("%s: lock to RxValid max %.2f cycles over %d locks (%d on noise)",
                     where, worst, len(runs) - noise, noise)
    assert worst <= target, f"{where}: first locked word {worst:.2f} cycles after the comma"
    return worst


def flips(link) -> set[int]:
    """Sent symbols with a flipped bit (slip-free lines)."""
    return {b // 10 for b in link.line.errors}


def rd_settle_word(run) -> float:
    """The time of the word that settles a run's RD. A run that starts with
    the RD unknown decodes a symbol valid at both RDs as if at RD- (8B10B.md,
    Decoder), so the first symbol that is not the same code at both RDs may
    show a disparity error; its word is the one returned."""
    from models.pcie_pcs_model import arch
    for w in run.words:
        for k, v in w.syms:
            if k and v not in arch.code8b10b.VALID_K:
                return w.t
            if arch.code8b10b.encode(v, bool(k), -1)[0] != arch.code8b10b.encode(v, bool(k), 1)[0]:
                return w.t
    return run.words[-1].t


def check_idle(rx: RxBench, link, where: str) -> list:
    """Electrical-idle gaps: one run per gap and one before them, no buffer
    status, data intact, each lock started at its comma within the lock
    target. Errors are expected only where a lock starts: the first word of
    a lock may hold the symbol before the comma, and a lock taken on a
    comma in the idle-exit noise gives error words until the relock in the
    FTS sets (the same cycle, so RxValid stays high)."""
    runs = rx.check(link, where, lead_skip=lambda t: True)
    assert len(runs) == len(link.idles) + 1, f"{where}: {len(runs)} runs for {len(link.idles)} gaps"
    allowed = set()
    for r in runs:
        allowed.add(r.words[0].t)
        if r.noise_lock:
            clean_from = r.placed[0][0] if r.placed else r.words[-1].t
            allowed.update(w.t for w in r.words if w.t < clean_from)
    bad = [(w.t, w.status) for w in rx.words if w.status in (5, 6)
           or w.valid and w.status in (4, 7) and w.t not in allowed]
    assert not bad, f"{where}: error or buffer status after idle: {bad[:6]}"
    for r in runs:
        if r.noise_lock:
            COV_RX.sample(relock="noise_lock")
    check_skp_status(link.syms, [r for r in runs if not r.noise_lock], where)
    check_latency(rx, runs)
    check_lock_latency(rx, link, runs[1:], where)
    return runs
