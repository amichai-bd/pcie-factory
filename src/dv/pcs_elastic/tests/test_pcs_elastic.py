"""pcie_pcs_elastic tests (wiki/src/dv/pcs_elastic/DV_PLAN.md).

Two clocks: clk_i has a 1 us period (the period only scales time; 1 us
gives offsets to 2 ppm in integer picoseconds) and ser_rx_clk_i runs
``ppm`` off it (positive: the RX clock is fast). The write side is driven
at the falling edge of ser_rx_clk_i with the aligned words of a
``Traffic``; the read side is sampled at the falling edge of clk_i. After
the run, models/pcie_pcs_elastic_model.check compares every word that
left with the stream that entered, and the test checks the visible fill
(fill_o) against the HAS targets.

The RX-domain reset comes from rst_i through the module's own reset
synchronizer; the writer holds ser_rx_valid_i low while ser_rx_rst_o is
high, and the test checks its assertion and release timing.
"""

import random
from bisect import bisect_left

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer
from cocotb.utils import get_sim_time

from models import pcie_pcs_elastic_model as m
from regress.funcov import Covergroup

CLK_PS = 1_000_000

COV = Covergroup("pcs_elastic")
COV.coverpoint("ppm", bins={
    "m600": -600, "m300": -300, "zero": 0, "p300": 300, "p600": 600,
    "random": lambda v: v not in (-600, -300, 0, 300, 600) and abs(v) <= 600,
    "beyond": lambda v: abs(v) > 600})
COV.coverpoint("change", bins=["add_com_slot0", "add_com_slot1", "rem_on_com",
                               "rem_on_next", "bare_com", "back_to_back_coms"])
COV.coverpoint("event", bins=["overflow", "underflow", "write_drop", "read_hold",
                              "lock_downgrade", "relock", "start", "reset_restart",
                              "write_pause", "lock_start_flush"])
COV.coverpoint("fill", bins={"4_5": [4, 5], "6_7": [6, 7], "8": [8], "9_10": [9, 10],
                             "11_12": [11, 12]})
# Corrections per offset class: only the reachable direction of each
# (a slow RX clock needs additions, a fast one removals).
COV.coverpoint("offset_correction", bins={
    "m600_add": ("m600", "add"), "m300_add": ("m300", "add"),
    "p300_rem": ("p300", "rem"), "p600_rem": ("p600", "rem"),
    "random_add": ("random", "add"), "random_rem": ("random", "rem")})


def offset_class(ppm: int) -> str:
    return {-600: "m600", -300: "m300", 0: "zero", 300: "p300", 600: "p600"}.get(
        ppm, "random" if abs(ppm) <= 600 else "beyond")


def rx_period(ppm: float) -> int:
    """An even period in ps, ``ppm`` faster than clk_i."""
    return 2 * round(CLK_PS / (1.0 + ppm * 1e-6) / 2)


class Env:
    """Clocks, reset, the write-side driver and the read-side monitor."""

    def __init__(self, dut, rng: random.Random, ppm: float):
        self.dut = dut
        self.rng = rng
        self.ppm = ppm
        self.cycle = 0
        self.traffic: m.Traffic | None = None
        self.words: list[m.Word] = []
        self.next_word = 0
        self.write_done_cycle: int | None = None
        self.started_cycle: int | None = None
        self.fill_min = 10**9
        self.fill_max = -1
        self.holds = 0
        self.fill_limits = True
        self.pause_p = 0.0     # chance per RX cycle to start a write pause
        self.pause_max = 0
        self.pause_left = 0
        self.pauses = 0
        self.track = True
        self.take_ps: list[int] = []   # RX edge that takes each word written
        self.fill_high: list[tuple[int, int]] = []  # (clk_i edge, fill it registers)
        self.prev_fill = 0
        dut.rst_i.value = 1
        dut.ser_rx_valid_i.value = 0
        dut.ser_rx_sym_i.value = 0
        dut.ser_rx_lock_i.value = 0
        dut.ser_rx_gen_i.value = 0
        dut.clk_i.value = 0
        dut.ser_rx_clk_i.value = 0
        self.rx_ps = rx_period(ppm)
        cocotb.start_soon(Clock(dut.clk_i, CLK_PS, "ps").start())
        cocotb.start_soon(self._rx_clock(rng.randrange(2, self.rx_ps, 2)))
        cocotb.start_soon(self._writer())
        cocotb.start_soon(self._reader())
        dut._log.info("ppm %+.1f: clk_i %d ps, ser_rx_clk_i %d ps", ppm, CLK_PS, self.rx_ps)

    async def _rx_clock(self, phase: int) -> None:
        await Timer(phase, "ps")
        await Clock(self.dut.ser_rx_clk_i, self.rx_ps, "ps").start(start_high=False)

    async def reset(self) -> None:
        """rst_i for 10 clk_i cycles (at least 8 clk_i and 7 ser_rx_clk_i
        cycles). ser_rx_rst_o must rise with rst_i, with no clock edge, and
        fall at the second ser_rx_clk_i edge after rst_i falls."""
        dut = self.dut
        await FallingEdge(dut.clk_i)
        dut.rst_i.value = 1
        await Timer(1, "ps")
        assert int(dut.ser_rx_rst_o.value) == 1, "ser_rx_rst_o did not assert with rst_i"
        for _ in range(10):
            await FallingEdge(dut.clk_i)
        dut.rst_i.value = 0
        await RisingEdge(dut.ser_rx_clk_i)
        await FallingEdge(dut.ser_rx_clk_i)
        assert int(dut.ser_rx_rst_o.value) == 1, "ser_rx_rst_o released one RX edge after rst_i"
        await RisingEdge(dut.ser_rx_clk_i)
        await FallingEdge(dut.ser_rx_clk_i)
        assert int(dut.ser_rx_rst_o.value) == 0, "ser_rx_rst_o not released two RX edges after rst_i"

    def send(self, traffic: m.Traffic) -> None:
        """Start writing ``traffic`` from the next RX cycle."""
        self.traffic = traffic
        self.words = []
        self.next_word = 0
        self.write_done_cycle = None
        self.started_cycle = None
        self.fill_min, self.fill_max = 10**9, -1
        self.take_ps = []
        self.fill_high = []
        self.prev_fill = 0

    async def _writer(self) -> None:
        dut = self.dut
        while True:
            await FallingEdge(dut.ser_rx_clk_i)
            t = self.traffic
            if t is None or int(dut.ser_rx_rst_o.value) or self.next_word >= t.words:
                if t is not None and self.next_word >= t.words and self.write_done_cycle is None:
                    self.write_done_cycle = self.cycle
                dut.ser_rx_valid_i.value = 0
                continue
            if self.pause_left or (self.pause_p and self.rng.random() < self.pause_p):
                if not self.pause_left:
                    self.pause_left = self.rng.randint(1, self.pause_max)
                    self.pauses += 1
                self.pause_left -= 1
                dut.ser_rx_valid_i.value = 0
                continue
            a, b = t.syms[2 * self.next_word], t.syms[2 * self.next_word + 1]
            dut.ser_rx_valid_i.value = 1
            dut.ser_rx_sym_i.value = a.code | (b.code << 10)
            dut.ser_rx_lock_i.value = a.lock
            dut.ser_rx_gen_i.value = a.gen
            self.take_ps.append(int(get_sim_time("ps")) + self.rx_ps // 2)
            self.next_word += 1

    async def _reader(self) -> None:
        dut = self.dut
        while True:
            await FallingEdge(dut.clk_i)
            self.cycle += 1
            if int(dut.rst_i.value) or self.traffic is None:
                continue
            fill = int(dut.fill_o.value)
            valid = int(dut.rx_valid_o.value)
            # fill_high_q takes (fill >= EB_HIGH) at the next rising edge.
            self.fill_high.append((int(get_sim_time("ps")) + CLK_PS // 2, fill))
            if self.started_cycle is None:
                # Start: the first word goes out in the first cycle whose
                # fill is 8 or more, and not before.
                assert valid == int(fill >= m.EB_NOMINAL), (
                    f"cycle {self.cycle}: rx_valid_o {valid} before the start at visible "
                    f"fill {fill} (previous cycle {self.prev_fill}); expected the first "
                    f"word in the first cycle at {m.EB_NOMINAL} or more")
                self.prev_fill = fill
                if not valid:
                    continue
                self.started_cycle = self.cycle
                COV.sample(event="start")
            writing = self.write_done_cycle is None
            if writing and self.track:
                self.fill_min = min(self.fill_min, fill)
                self.fill_max = max(self.fill_max, fill)
                if self.fill_limits:
                    assert m.FILL_MIN <= fill <= m.FILL_MAX, (
                        f"cycle {self.cycle}: visible fill {fill} outside "
                        f"{m.FILL_MIN}..{m.FILL_MAX} at {self.ppm:+.1f} ppm")
            if not valid:
                self.holds += writing
                continue
            sym = int(dut.rx_sym_o.value)
            lock = int(dut.rx_lock_o.value)
            gen = int(dut.rx_gen_o.value)
            w = m.Word(self.cycle, sym & 0x3FF, sym >> 10, (lock & 1, lock >> 1),
                       (gen & 1, gen >> 1), int(dut.rx_skp_add_o.value),
                       int(dut.rx_skp_rem_o.value), int(dut.rx_ovf_o.value),
                       int(dut.rx_udf_o.value), fill)
            self.words.append(w)
            if writing:
                COV.sample(fill=fill)

    def fill_seen(self, take_ps: int) -> int | None:
        """The visible fill behind the fill high the write side sees at the
        RX edge ``take_ps``: fill_high_q is (fill >= EB_HIGH) registered at
        a clk_i edge, and its synchronizer's first flop samples it at the RX
        edge two before ``take_ps``. None when a clk_i edge falls exactly on
        that RX edge (a race in zero-delay simulation)."""
        t1 = take_ps - 2 * self.rx_ps
        i = bisect_left(self.fill_high, (t1, -1))
        if i < len(self.fill_high) and self.fill_high[i][0] == t1:
            return None
        return self.fill_high[i - 1][1] if i else 0

    def check_thresholds(self, r: m.Result) -> dict:
        """Every SKP ordered set obeys the thresholds (RECEIVE.md, Elastic
        buffer): its first SKP is removed exactly when the write side sees
        fill high (visible fill 9 or more, through the synchronizer) as the
        word holding that SKP is written; a SKP is added exactly when the
        COM leaves at a visible fill of 7 or less and no SKP was removed."""
        checked = {"rem": [0, 0], "add": [0, 0], "rem_at": {}, "add_at": {}}
        for st in r.sets:
            if st["n"] == 0:
                continue
            removed, added = st["change"] == "rem", st["change"] == "add"
            if st["rem_ok"]:
                seen = self.fill_seen(self.take_ps[(st["com_in"] + 1) // 2])
                if seen is not None:
                    want = int(seen >= m.EB_HIGH)
                    assert removed == bool(want), (
                        f"SKP set at input symbol {st['com_in']}: removed {removed}, but the "
                        f"write side saw fill high {want} (visible fill {seen})")
                    checked["rem"][want] += 1
                    checked["rem_at"][seen] = checked["rem_at"].get(seen, 0) + 1
            fill = self.words[st["com_word"]].fill
            # Below fill 4 the symbol after the COM may not be visible yet,
            # and then nothing is added (MAS, SKP addition).
            if st["add_ok"] and not removed and fill >= m.FALLBACK_FILL:
                want = int(fill <= m.EB_LOW)
                assert added == bool(want), (
                    f"SKP set at input symbol {st['com_in']}: added {added}, but its COM "
                    f"left at visible fill {fill}")
                checked["add"][want] += 1
                checked["add_at"][fill] = checked["add_at"].get(fill, 0) + 1
        self.dut._log.info("threshold checks: removal %s, addition %s (no change, change); "
                           "removal decisions by fill %s, addition decisions by fill %s",
                           checked["rem"], checked["add"], sorted(checked["rem_at"].items()),
                           sorted(checked["add_at"].items()))
        return checked

    async def run_until_written(self, slack_cycles: int = 200) -> None:
        """Wait until the writer has sent every word, then ``slack_cycles``."""
        while self.write_done_cycle is None:
            await FallingEdge(self.dut.clk_i)
        for _ in range(slack_cycles):
            await FallingEdge(self.dut.clk_i)

    def check(self, loss: bool = False, udf_anywhere: bool = False) -> m.Result:
        try:
            r = m.check(self.traffic, self.words, loss=loss,
                        udf_allowed_from=None if udf_anywhere else self.write_done_cycle)
        except m.CheckError:
            path = m.dump(self.traffic, self.words, "elastic_dump.json")
            self.dut._log.error("input stream and output words written to %s", path)
            raise
        self.dut._log.info(
            "%+.1f ppm: %d words, %d symbols delivered, fill %d..%d, +%d -%d SKP "
            "(rem on COM %d, on next %d), %d dropped words, %d read holds, %d lost, "
            "%d overflows, %d underflows, %d downgraded, %d flushed at lock start, %d bare COMs, "
            "%d back-to-back COMs",
            self.ppm, len(self.words), r.delivered, self.fill_min, self.fill_max, r.added,
            r.removed, r.rem_on_com, r.rem_fallback, r.dropped_words, self.holds,
            r.lost_symbols, r.overflows, r.underflows, r.downgraded, r.flushed, r.bare_coms,
            r.back_to_back_coms)
        ppm = int(round(self.ppm))
        for name, n in (("add_com_slot0", r.add_slot[0]), ("add_com_slot1", r.add_slot[1]),
                        ("rem_on_com", r.rem_on_com), ("rem_on_next", r.rem_fallback),
                        ("bare_com", r.bare_coms), ("back_to_back_coms", r.back_to_back_coms)):
            for _ in range(min(n, 64)):
                COV.sample(change=name)
        for name, n in (("overflow", r.overflows), ("underflow", r.underflows),
                        ("write_drop", r.dropped_words), ("read_hold", self.holds),
                        ("lock_downgrade", r.downgraded), ("relock", self.traffic.relocks),
                        ("lock_start_flush", r.flushed)):
            if n:
                COV.sample(event=name)
        COV.sample(ppm=ppm)
        for kind, n in (("add", r.added), ("rem", r.removed)):
            if n:
                COV.sample(offset_correction=(offset_class(ppm), kind))
        return r


async def link(dut, ppm, kind, packets, skp_interval, idle_gaps=0, idle_symbols=0,
               relocks=0, boundary=False) -> tuple[Env, m.Result]:
    """One run of partner traffic: no loss, no overflow or underflow while
    writing, visible fill within the HAS window throughout."""
    rng = random.Random(random.getrandbits(32))
    env = Env(dut, rng, ppm)
    traffic = m.link_traffic(rng, kind, packets, skp_interval, idle_gaps, idle_symbols, relocks)
    dut._log.info("%s traffic: %d words, SKP interval %d, %d idle gaps of %d, %d relocks",
                  kind, traffic.words, skp_interval, idle_gaps, idle_symbols, relocks)
    await env.reset()
    env.send(traffic)
    await env.run_until_written()
    r = env.check()
    assert r.overflows == 0, f"{r.overflows} overflow(s)"
    assert m.FILL_MIN <= env.fill_min and env.fill_max <= m.FILL_MAX
    # Within the targets the symbol after a COM is always visible, so every
    # removal is reported on the COM's word.
    assert r.rem_fallback == 0, (
        f"{r.rem_fallback} removal(s) reported on the next word within the targets")
    checked = env.check_thresholds(r)
    if ppm >= 300 and not idle_gaps:
        assert checked["rem"][0] and checked["rem"][1], (
            f"threshold check vacuous: removal decisions {checked['rem']}")
    if ppm <= -300 and not idle_gaps:
        assert checked["add"][0] and checked["add"][1], (
            f"threshold check vacuous: addition decisions {checked['add']}")
    if boundary:
        # Decisions right at the thresholds: removal seen at 8 (none) and 9
        # (remove) on a fast RX clock, addition at 7 (add) and 8 (none) on a
        # slow one.
        at = checked["rem_at"] if ppm > 0 else checked["add_at"]
        pair = (m.EB_HIGH - 1, m.EB_HIGH) if ppm > 0 else (m.EB_LOW, m.EB_LOW + 1)
        assert all(at.get(f) for f in pair), (
            f"no decision at fill {pair[0]} and {pair[1]}: {sorted(at.items())}")
    if abs(ppm) >= 300 and not idle_gaps:
        changes = r.added if ppm < 0 else r.removed
        assert changes, f"vacuous run: no SKP {'added' if ppm < 0 else 'removed'} at {ppm} ppm"
    return env, r


def random_ppm(rng=random) -> float:
    return rng.uniform(-600.0, 600.0)


# ------------------------------------------------------------------ L0

@cocotb.test()
async def smoke(dut):
    """L0: reset, start at fill 8, random packets and SKP sets of 1 to 5
    SKPs at 0 ppm, every symbol delivered."""
    await link(dut, 0, "random", 40, 1180)


# ------------------------------------------------------------------ L1

@cocotb.test()
async def ppm_m600(dut):
    """L1: maximum TLPs, longest SKP interval, RX clock -600 ppm (adds)."""
    await link(dut, -600, "tlp", 8, 1538)


@cocotb.test()
async def ppm_m300(dut):
    """L1: random packets, random SKP interval, -300 ppm."""
    await link(dut, -300, "random", 200, random.randrange(1180, 1539))


@cocotb.test()
async def ppm_p300(dut):
    """L1: random packets, random SKP interval, +300 ppm."""
    await link(dut, 300, "random", 200, random.randrange(1180, 1539))


@cocotb.test()
async def ppm_p600(dut):
    """L1: maximum TLPs, longest SKP interval, RX clock +600 ppm (removals)."""
    await link(dut, 600, "tlp", 8, 1538)


@cocotb.test()
async def random_offset(dut):
    """L1: a random offset within +-600 ppm, random traffic and interval."""
    await link(dut, random_ppm(), "random", 200, random.randrange(1180, 1539))


@cocotb.test()
async def short_burst_m600(dut):
    """L1: the reviewer's case: a packet just under two SKP intervals, so a
    burst one set short follows it, then a maximum TLP, at -600 ppm."""
    await link(dut, -600, "short_burst", 8, 1538)


@cocotb.test()
async def stress_sizes(dut):
    """L1: packet sizes chosen to strand corrections (4122, 3070, 1534 and
    2 bytes) at -600 or +600 ppm, longest interval."""
    await link(dut, random.choice((-600, 600)), "stress", 16, 1538)


async def idle(dut, ppm):
    """Electrical-idle gaps (unlocked noise): the write side drops unlocked
    words (RX fast) or the read side holds (RX slow), and lock returns with
    a new generation near the nominal fill."""
    env, r = await link(dut, ppm, "random", 60, 1180, idle_gaps=2, idle_symbols=6000)
    assert (r.dropped_words if ppm > 0 else env.holds), "vacuous run: no recentring"


@cocotb.test()
async def thresholds_swing(dut):
    """L1: removal and addition decisions at both thresholds. The RX clock
    is 0.3% fast, and in alternate 2000-cycle phases the writer pauses for
    one cycle in about 0.6% of cycles, so the stream alternates between
    fast and slow. SKP ordered sets every 48 to 96 symbols then hold the
    fill at 7 to 9, with odd fills after additions. Every eligible SKP
    must be removed exactly when the write side sees fill 9 or more, and
    added exactly when its COM leaves at 7 or less."""
    rng = random.Random(random.getrandbits(32))
    env = Env(dut, rng, 3000)
    env.pause_max = 1
    env.fill_limits = False   # close pauses can dip the fill below 4

    async def phases():
        while True:
            for p in (0.0, 0.006):
                env.pause_p = p
                for _ in range(2000):
                    await FallingEdge(dut.ser_rx_clk_i)

    await env.reset()
    env.send(m.link_traffic(rng, "dense", 1500, rng.randrange(48, 97)))
    cocotb.start_soon(phases())
    await env.run_until_written()
    # A pause can cost an underflow word (nothing is lost); the stream
    # check stays exact.
    r = env.check(udf_anywhere=True)
    assert r.overflows == 0
    checked = env.check_thresholds(r)
    for name, pair in (("rem_at", (m.EB_HIGH - 1, m.EB_HIGH)),
                       ("add_at", (m.EB_LOW, m.EB_LOW + 1))):
        assert all(checked[name].get(f) for f in pair), (
            f"no {name} decision at fill {pair}: {sorted(checked[name].items())}")


@cocotb.test()
async def thresholds_m600(dut):
    """L1: the same at -600 ppm and the addition threshold: a SKP is added
    exactly when its COM leaves at fill 7 or less, never at 8."""
    await link(dut, -600, "dense", 600, random.randrange(48, 97), boundary=True)


@cocotb.test()
async def idle_m600(dut):
    """L1: electrical idle at -600 ppm: the read side recentres."""
    await idle(dut, -600)


@cocotb.test()
async def idle_p600(dut):
    """L1: electrical idle at +600 ppm: the write side recentres."""
    await idle(dut, 600)


async def lock_start(dut, ppm):
    """Lock start (MAS, Lock start): after electrical idle, the first locked
    word is never packed with an unlocked symbol, on the write side (the
    packing register) or the read side (the leftover register); that
    unlocked symbol is dropped instead. So no word leaves with an unlocked
    symbol before a locked one, and lock reaches PIPE one word earlier."""
    env, r = await link(dut, ppm, "random", 120, 1180, idle_gaps=12, idle_symbols=1500)
    late = [w for w in env.words if not w.udf and w.lock == (0, 1)]
    assert not late, (f"{len(late)} word(s) with an unlocked symbol before a locked one, "
                      f"first at cycle {late[0].cycle}")
    assert r.late_lock_start == 0, (
        f"{r.late_lock_start} locked symbol(s) left unlocked at a lock start")
    assert r.flushed, "vacuous run: no lock started behind a held unlocked symbol"


@cocotb.test()
async def lock_start_m600(dut):
    """L1: lock start behind held unlocked symbols, RX clock 600 ppm slow."""
    await lock_start(dut, -600)


@cocotb.test()
async def lock_start_p600(dut):
    """L1: the same, RX clock 600 ppm fast."""
    await lock_start(dut, 600)


@cocotb.test()
async def pause_corners(dut):
    """L1: SKP ordered sets of 1 to 5 SKPs every 24 to 64 symbols, with
    random write pauses (about 4% of RX cycles) and the RX clock 4% fast, so the fill swings
    across both thresholds: bare COMs, back-to-back sets, removals
    reported on the next symbol's word, underflow and overflow all occur,
    and every symbol is accounted for."""
    rng = random.Random(random.getrandbits(32))
    env = Env(dut, rng, 40_000)
    env.fill_limits = False
    env.pause_p, env.pause_max = 0.011, 6
    await env.reset()
    env.send(m.link_traffic(rng, "dense", 400, rng.randrange(24, 65)))
    await env.run_until_written()
    r = env.check(loss=True, udf_anywhere=True)
    assert env.pauses and r.added and r.removed, "vacuous run: no pause, add or removal"
    COV.sample(event="write_pause")


@cocotb.test()
async def relock_generation(dut):
    """L1: same-cycle relocks (the generation flips, lock stays): every
    locked symbol leaves with its generation."""
    await link(dut, random_ppm(), "random", 150, 1180, relocks=12)


async def flow_error(dut, ppm, overflow):
    """Data with no ordered set at a large offset: nothing can be
    corrected, so the buffer overflows (RX fast) or underflows (RX slow)."""
    rng = random.Random(random.getrandbits(32))
    env = Env(dut, rng, ppm)
    env.fill_limits = False
    await env.reset()
    env.send(m.plain_traffic(rng, 8000))
    await env.run_until_written()
    r = env.check(loss=overflow, udf_anywhere=not overflow)
    if overflow:
        assert r.overflows >= 2 and r.lost_symbols, "no overflow reported"
        assert r.underflows == 0 or env.write_done_cycle is not None
    else:
        assert r.underflows >= 2, "no underflow reported"
        assert r.overflows == 0 and r.lost_symbols == 0


@cocotb.test()
async def overflow(dut):
    """L1: RX clock 2% fast with no SKP: words are lost, and the first word
    after each loss carries the overflow flag; the rest arrive in order."""
    await flow_error(dut, 20_000, True)


@cocotb.test()
async def underflow(dut):
    """L1: RX clock 2% slow with no SKP: EDB words (underflow flag) are sent
    when fewer than two symbols are visible, and no symbol is lost."""
    await flow_error(dut, -20_000, False)


@cocotb.test()
async def reset_restart(dut):
    """L1: reset both domains with traffic in flight, then start again: the
    read side waits for fill 8 again and the new stream arrives intact."""
    rng = random.Random(random.getrandbits(32))
    ppm = random_ppm(rng)
    env = Env(dut, rng, ppm)
    await env.reset()
    env.send(m.link_traffic(rng, "random", 20, 1180))
    for _ in range(rng.randrange(200, 1500)):
        await FallingEdge(dut.clk_i)
    assert env.words, "nothing sent before the reset"
    env.traffic = None
    await env.reset()
    COV.sample(event="reset_restart")
    env.send(m.link_traffic(rng, "random", 60, 1180))
    await env.run_until_written()
    env.check()


# ------------------------------------------------------------------ L2

@cocotb.test()
async def long_m600(dut):
    """L2: long run of maximum TLPs at -600 ppm, both interval limits."""
    await link(dut, -600, "tlp", 40, random.choice((1180, 1538)))


@cocotb.test()
async def long_p600(dut):
    """L2: long run of maximum TLPs at +600 ppm, both interval limits."""
    await link(dut, 600, "tlp", 40, random.choice((1180, 1538)))


@cocotb.test()
async def long_random(dut):
    """L2: long random traffic at a random offset and interval."""
    await link(dut, random_ppm(), "random", 1200, random.randrange(1180, 1539))


@cocotb.test()
async def long_stress(dut):
    """L2: long run of correction-stranding sizes and short bursts at
    +-600 ppm."""
    kind = random.choice(("stress", "short_burst"))
    await link(dut, random.choice((-600, 600)), kind, 60, 1538)


@cocotb.test()
async def long_idle(dut):
    """L2: three 20000-symbol electrical-idle gaps at +-600 ppm."""
    await link(dut, random.choice((-600, 600)), "random", 300, 1180, idle_gaps=3,
               idle_symbols=20000)
