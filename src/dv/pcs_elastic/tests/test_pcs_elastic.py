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

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

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
                              "write_pause"])
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
            if self.started_cycle is None:
                if not valid:
                    continue
                assert fill >= m.EB_NOMINAL, (
                    f"cycle {self.cycle}: first word sent at visible fill {fill}, "
                    f"expected at least {m.EB_NOMINAL}")
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
                       int(dut.rx_udf_o.value))
            self.words.append(w)
            if writing:
                COV.sample(fill=fill)

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
            "%d overflows, %d underflows, %d downgraded, %d bare COMs, %d back-to-back COMs",
            self.ppm, len(self.words), r.delivered, self.fill_min, self.fill_max, r.added,
            r.removed, r.rem_on_com, r.rem_fallback, r.dropped_words, self.holds,
            r.lost_symbols, r.overflows, r.underflows, r.downgraded, r.bare_coms,
            r.back_to_back_coms)
        ppm = int(round(self.ppm))
        for name, n in (("add_com_slot0", r.add_slot[0]), ("add_com_slot1", r.add_slot[1]),
                        ("rem_on_com", r.rem_on_com), ("rem_on_next", r.rem_fallback),
                        ("bare_com", r.bare_coms), ("back_to_back_coms", r.back_to_back_coms)):
            for _ in range(min(n, 64)):
                COV.sample(change=name)
        for name, n in (("overflow", r.overflows), ("underflow", r.underflows),
                        ("write_drop", r.dropped_words), ("read_hold", self.holds),
                        ("lock_downgrade", r.downgraded), ("relock", self.traffic.relocks)):
            if n:
                COV.sample(event=name)
        COV.sample(ppm=ppm)
        for kind, n in (("add", r.added), ("rem", r.removed)):
            if n:
                COV.sample(offset_correction=(offset_class(ppm), kind))
        return r


async def link(dut, ppm, kind, packets, skp_interval, idle_gaps=0, idle_symbols=0,
               relocks=0) -> tuple[Env, m.Result]:
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
async def idle_m600(dut):
    """L1: electrical idle at -600 ppm: the read side recentres."""
    await idle(dut, -600)


@cocotb.test()
async def idle_p600(dut):
    """L1: electrical idle at +600 ppm: the write side recentres."""
    await idle(dut, 600)


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
