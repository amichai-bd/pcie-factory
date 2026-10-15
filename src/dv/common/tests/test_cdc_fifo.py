"""common_cdc_fifo tests (wiki/src/dv/common/cdc/common_cdc_fifo.md).

Each side is driven and sampled at the falling edge of its own clock: the
writer sets src_valid_i/src_data_i and, when src_ready_o is high, records
the word as accepted by the next rising edge; the reader sets dst_ready_i
and, when dst_valid_o is high, pops and compares dst_data_o with the
scoreboard. The depth is 2**ADDR_W, read from the parameter. The
asynchronous-reset test stops both clocks and checks the reset state in the
same time step the resets are driven.
"""

import random
from collections import deque

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, Timer

from regress.funcov import Covergroup
from tb.cdc_env import (CLOCK_BINS, EdgeLog, GatedClock, clock_ratio, falling_edges, now,
                        random_periods, reset_step, start_clock)

# Functional coverage (DV plan, Strategy): each side's handshake outcome per
# cycle under each clock ratio class, and resets with words in flight.
COV = Covergroup("common_cdc_fifo")
COV.coverpoint("clocks", bins=CLOCK_BINS)
COV.coverpoint("src", bins=["push", "full_stall", "idle"])
COV.coverpoint("dst", bins=["pop", "empty_stall", "hold", "idle"])
COV.coverpoint("reset", bins={"in_flight": lambda words: words > 0})
COV.coverpoint("fill", bins={"empty": lambda f: f[0] == 0, "one": lambda f: f[0] == 1,
                             "some": lambda f: 1 < f[0] < f[1], "full": lambda f: f[0] == f[1]})
# Asynchronous reset of a full FIFO with both clocks stopped: a pulse
# wholly inside the stop, or held into the restart.
COV.coverpoint("async_reset", bins=["stopped_pulse", "stopped_held"])
COV.cross("clocks_x_src", "clocks", "src")
COV.cross("clocks_x_dst", "clocks", "dst")


class Fifo:
    """Drivers, scoreboard and coverage for one test. A word is recorded
    as written (read) at the falling edge before the rising edge that
    takes it, when the newly driven valid (ready) meets the current, stable
    ready (valid)."""

    def __init__(self, dut, rng, src_period, dst_period):
        self.dut = dut
        self.rng = rng
        self.depth = 2 ** int(dut.ADDR_W.value)
        self.width = len(dut.src_data_i)
        self.src_period = src_period
        self.dst_period = dst_period
        self.src_edges = EdgeLog(dut.src_clk_i)
        self.dst_edges = EdgeLog(dut.dst_clk_i)
        self.queue = deque()  # (word index, data) accepted, not yet read
        self.pushed = 0
        self.popped = 0
        self.push_times = []  # rising src edge that takes each word
        self.pop_times = []   # rising dst edge that takes each word
        self.valid_rises = []  # dst edge after which dst_valid_o went high
        self.ready_rises = []  # src edge after which src_ready_o went high
        self.full_seen = 0    # src cycles with src_ready_o low
        self.empty_after_data = 0
        self.p_write = 0.0
        self.p_read = 0.0
        self.write_limit = None  # stop offering words after this many pushes
        self.fill_max = 0
        self.ratio = clock_ratio(src_period, dst_period)
        cocotb.start_soon(self._writer())
        cocotb.start_soon(self._reader())

    async def _writer(self):
        dut = self.dut
        was_ready = 1
        while True:
            await FallingEdge(dut.src_clk_i)
            ready = int(dut.src_ready_o.value)
            if ready and not was_ready:
                self.ready_rises.append(self.src_edges.last_before(now()))
            was_ready = ready
            self.full_seen += not ready
            offer = (self.write_limit is None or self.pushed < self.write_limit) \
                and self.rng.random() < self.p_write
            data = self.rng.getrandbits(self.width)
            dut.src_valid_i.value = int(offer)
            dut.src_data_i.value = data
            COV.sample(clocks=self.ratio,
                       src="push" if offer and ready else "full_stall" if offer else "idle")
            if offer and ready:
                self.queue.append((self.pushed, data))
                self.push_times.append(now() + self.src_period // 2)
                self.pushed += 1

    async def _reader(self):
        dut = self.dut
        was_valid = 0
        while True:
            await FallingEdge(dut.dst_clk_i)
            valid = int(dut.dst_valid_o.value)
            self.check_fill(valid)
            if valid and not was_valid:
                self.valid_rises.append(self.dst_edges.last_before(now()))
            was_valid = valid
            take = self.rng.random() < self.p_read
            dut.dst_ready_i.value = int(take)
            COV.sample(clocks=self.ratio, dst="pop" if take and valid else
                       "empty_stall" if take else "hold" if valid else "idle")
            if take and valid:
                assert self.queue, (
                    f"read {self.popped}: dst_valid_o high but no word was written")
                index, want = self.queue.popleft()
                got = int(dut.dst_data_o.value)
                assert got == want, (
                    f"read {self.popped} (word {index}): dst_data_o expected {want:#x} "
                    f"got {got:#x}")
                self.pop_times.append(now() + self.dst_period // 2)
                self.popped += 1
                if not self.queue:
                    self.empty_after_data += 1

    def check_fill(self, valid):
        """dst_fill_o counts the visible words: non-zero exactly when
        dst_valid_o is high, never more than the words accepted and not
        read, and dst_next_data_o is the second-oldest word whenever the
        fill is 2 or more (MAS, Behavior)."""
        dut = self.dut
        fill = int(dut.dst_fill_o.value)
        assert (fill != 0) == bool(valid), (
            f"read {self.popped}: dst_fill_o {fill} with dst_valid_o {valid}")
        assert fill <= len(self.queue), (
            f"read {self.popped}: dst_fill_o {fill} but only {len(self.queue)} word(s) written")
        if fill >= 2:
            got, want = int(dut.dst_next_data_o.value), self.queue[1][1]
            assert got == want, (
                f"read {self.popped}: dst_next_data_o expected {want:#x} got {got:#x}")
        COV.sample(fill=(fill, self.depth))
        self.fill_max = max(self.fill_max, fill)

    async def run(self, p_write, p_read, src_cycles):
        self.p_write, self.p_read = p_write, p_read
        await falling_edges(self.dut.src_clk_i, src_cycles)

    async def fill(self):
        """Write with the reader stalled until src_ready_o drops (full),
        which must happen within 2 * depth + 8 source cycles."""
        self.p_write, self.p_read = 1.0, 0.0
        limit = 2 * self.depth + 8
        for _ in range(limit):
            await FallingEdge(self.dut.src_clk_i)
            if not int(self.dut.src_ready_o.value):
                return
        raise AssertionError(f"fill: src_ready_o still high after {limit} src cycles "
                             "of writes with the reader stalled")

    async def drain(self):
        """Stop writing and read until every accepted word came out, then
        check the FIFO reports empty and not full."""
        self.p_write, self.p_read = 0.0, 1.0
        limit = 8 + 4 * self.depth
        for _ in range(limit):
            await FallingEdge(self.dut.dst_clk_i)
            if not self.queue:
                break
        assert not self.queue, (
            f"drain: {len(self.queue)} accepted word(s) never read after {limit} dst cycles")
        await falling_edges(self.dut.dst_clk_i, 2)
        assert int(self.dut.dst_valid_o.value) == 0, "drained FIFO still shows dst_valid_o"
        assert int(self.dut.dst_fill_o.value) == 0, "drained FIFO shows a non-zero dst_fill_o"
        await falling_edges(self.dut.src_clk_i, 4)
        assert int(self.dut.src_ready_o.value) == 1, "drained FIFO still shows src_ready_o low"


async def start(dut, src_period, dst_period, phase):
    """Start both clocks and reset both sides together; check the reset
    state: empty (dst_valid_o low) and not full (src_ready_o high)."""
    dut._log.info("ADDR_W %d, WIDTH %d, src period %d ps, dst period %d ps, "
                  "dst phase %d ps", int(dut.ADDR_W.value),
                  len(dut.src_data_i), src_period, dst_period, phase)
    dut.src_rst_i.value = 1
    dut.dst_rst_i.value = 1
    dut.src_valid_i.value = 0
    dut.src_data_i.value = 0
    dut.dst_ready_i.value = 0
    start_clock(dut.src_clk_i, src_period)
    start_clock(dut.dst_clk_i, dst_period, phase)
    await falling_edges(dut.src_clk_i, 4)
    await falling_edges(dut.dst_clk_i, 4)
    dut.src_rst_i.value = 0
    dut.dst_rst_i.value = 0
    await falling_edges(dut.src_clk_i, 2)
    await falling_edges(dut.dst_clk_i, 2)
    assert int(dut.dst_valid_o.value) == 0, "after reset: dst_valid_o high (not empty)"
    assert int(dut.src_ready_o.value) == 1, "after reset: src_ready_o low (full)"


def lag(edges, start, rises, what):
    """The first rise in ``rises`` at or after ``start`` is the flag
    latency, which the MAS bounds to two or three edges."""
    rise = next((r for r in rises if r >= start), None)
    assert rise is not None, f"{what} never rose after {start} ps"
    edges.check_lag(start, rise, what)


async def fill_and_drain(dut, f, round_):
    """Write with the reader stalled until full: exactly depth words are
    accepted and src_ready_o stays low; then read until empty: the words
    come out in order and dst_valid_o drops right after the last one. The
    first word reaches dst_valid_o, and the first read reaches src_ready_o,
    two or three edges of the far clock later."""
    depth = f.depth
    pushed, popped = f.pushed, f.popped
    f.p_write, f.p_read = 1.0, 0.0
    await falling_edges(dut.src_clk_i, depth + 8)
    accepted = f.pushed - pushed
    assert accepted == depth, f"round {round_}: full after {accepted} words, expected {depth}"
    assert int(dut.src_ready_o.value) == 0, f"round {round_}: src_ready_o high when full"
    await falling_edges(dut.dst_clk_i, 4)
    lag(f.dst_edges, f.push_times[pushed], f.valid_rises, f"round {round_}: dst_valid_o")
    fill = int(dut.dst_fill_o.value)
    assert fill == depth, f"round {round_}: dst_fill_o {fill} when full, expected {depth}"

    f.p_write, f.p_read = 0.0, 1.0
    while f.popped - popped < depth:
        await FallingEdge(dut.dst_clk_i)
    await FallingEdge(dut.dst_clk_i)
    assert f.popped - popped == depth, f"round {round_}: read {f.popped - popped} words"
    assert int(dut.dst_valid_o.value) == 0, f"round {round_}: dst_valid_o high when empty"
    await falling_edges(dut.src_clk_i, 4)
    lag(f.src_edges, f.pop_times[popped], f.ready_rises, f"round {round_}: src_ready_o")


def directed_periods(rng, src_fast):
    """A random ratio between 1:1 and 8:1, with the given side faster."""
    fast = rng.randrange(2_000, 6_000, 2)
    slow = rng.randrange(fast + 2, 8 * fast, 2)
    return (fast, slow) if src_fast else (slow, fast)


@cocotb.test()
async def smoke(dut):
    """L0: reset state, one word through, then a burst with both sides
    running at a fixed 2:5 ratio."""
    await start(dut, 4_000, 10_000, 1_000)
    f = Fifo(dut, random.Random(1), 4_000, 10_000)
    f.write_limit = 1
    await f.run(1.0, 1.0, 20)
    assert f.popped == 1, f"one word written, {f.popped} read"
    f.write_limit = 1 + 4 * f.depth
    await f.run(1.0, 1.0, 20 * f.depth)
    await f.drain()
    assert f.popped == 1 + 4 * f.depth, f"{f.pushed} words written, {f.popped} read"


async def full_empty(dut, src_fast):
    """Fill to full and drain to empty five times (the pointers wrap), with
    the flag latencies checked."""
    src, dst = directed_periods(random, src_fast)
    await start(dut, src, dst, random.randrange(2, dst, 2))
    f = Fifo(dut, random, src, dst)
    for round_ in range(5):
        await fill_and_drain(dut, f, round_)
    assert f.pushed == f.popped == 5 * f.depth


@cocotb.test()
async def full_empty_src_fast(dut):
    """L1: full, empty and wrap with the source clock faster."""
    await full_empty(dut, True)


@cocotb.test()
async def full_empty_dst_fast(dut):
    """L1: full, empty and wrap with the destination clock faster."""
    await full_empty(dut, False)


async def traffic(dut, rounds, max_ratio):
    """Random rate phases at a random clock ratio; every word checked in
    order, and the run must have hit full, empty and a pointer wrap.

    Each rate phase lasts a number of cycles of the faster clock, and each
    round then fills the FIFO only until it is full, so the run's clock
    edges, and its wall time, do not grow with the ratio in either
    direction."""
    src, dst = random_periods(random, max_ratio)
    await start(dut, src, dst, random.randrange(2, dst, 2))
    f = Fifo(dut, random, src, dst)
    rates = (0.0, 0.2, 0.5, 0.8, 1.0)
    src_per_fast = min(1.0, dst / src)  # source cycles per faster-clock cycle
    for _ in range(rounds):
        p_write, p_read = random.choice(rates), random.choice(rates)
        fast_cycles = random.randrange(4, 16 * f.depth)
        await f.run(p_write, p_read, max(1, round(fast_cycles * src_per_fast)))
        await f.fill()  # always reach full once per round
    await f.drain()
    dut._log.info("%d words, %d full src cycles, %d empties", f.popped, f.full_seen,
                  f.empty_after_data)
    assert f.full_seen and f.empty_after_data, "vacuous run: full or empty never reached"
    assert f.popped > 2 * f.depth, f"only {f.popped} words: the pointers never wrapped"


@cocotb.test()
async def reset_in_flight(dut):
    """L1: reset both sides together while words are queued and both sides
    are busy, three times: the FIFO comes back empty and not full, and
    traffic after each reset is checked word by word."""
    src, dst = random_periods(random, 8.0)
    await start(dut, src, dst, random.randrange(2, dst, 2))
    f = Fifo(dut, random, src, dst)
    for round_ in range(3):
        await f.run(1.0, 0.3, 4 * f.depth)
        assert f.queue, f"round {round_}: nothing in flight before the reset"
        lost = len(f.queue)
        COV.sample(reset=lost)
        # Stop both sides, then assert both resets over edges of both clocks
        # (words offered in reset would be dropped; the MAS reset rule).
        f.p_write, f.p_read = 0.0, 0.0
        await falling_edges(dut.src_clk_i, 1)
        await falling_edges(dut.dst_clk_i, 1)
        dut.src_rst_i.value = 1
        dut.dst_rst_i.value = 1
        await falling_edges(dut.src_clk_i, 3)
        await falling_edges(dut.dst_clk_i, 3)
        dut.src_rst_i.value = 0
        dut.dst_rst_i.value = 0
        f.queue.clear()
        await falling_edges(dut.src_clk_i, 4)
        await falling_edges(dut.dst_clk_i, 4)
        assert int(dut.dst_valid_o.value) == 0, (
            f"round {round_}: dst_valid_o high after reset ({lost} words were queued)")
        assert int(dut.src_ready_o.value) == 1, f"round {round_}: src_ready_o low after reset"
        popped = f.popped
        await f.run(0.7, 0.7, 8 * f.depth)
        await f.drain()
        assert f.popped > popped, f"round {round_}: no traffic after the reset"


@cocotb.test()
async def random_traffic(dut):
    """L1: random traffic at a random clock ratio up to 8:1 either way."""
    await traffic(dut, 40, 8.0)


@cocotb.test()
async def long_traffic(dut):
    """L2: long random traffic at a random clock ratio up to 16:1."""
    await traffic(dut, 400, 16.0)


@cocotb.test()
async def async_reset(dut):
    """L1, SRC_ASYNC_RESET and DST_ASYNC_RESET 1: a full FIFO, both clocks
    stopped, both resets asserted: in that same time step it is empty and
    not full (dst_valid_o 0, dst_fill_o 0, src_ready_o 1). Twice: first
    filled from pointer 0 and the reset a pulse wholly inside the stop,
    then filled with both pointers at 2**ADDR_W (the read Gray pointer is
    then the full pattern) and the reset held into the restart
    and released synchronously. So every pointer register and both pointer
    synchronizers are seen to reset with no clock edge. Traffic after each
    reset is checked word by word."""
    assert int(dut.SRC_ASYNC_RESET.value) == 1 and int(dut.DST_ASYNC_RESET.value) == 1, \
        "async_reset needs SRC_ASYNC_RESET and DST_ASYNC_RESET 1"
    src, dst = random_periods(random, 8.0)
    phase = random.randrange(2, dst, 2)
    dut._log.info("ADDR_W %d, WIDTH %d, src period %d ps, dst period %d ps, dst phase %d ps",
                  int(dut.ADDR_W.value), len(dut.src_data_i), src, dst, phase)
    sclk = GatedClock(dut.src_clk_i, src)
    dclk = GatedClock(dut.dst_clk_i, dst)
    slow = max(src, dst)

    async def expect_reset_state(what):
        await ReadOnly()
        got = (int(dut.dst_valid_o.value), int(dut.dst_fill_o.value),
               int(dut.src_ready_o.value))
        assert got == (0, 0, 1), (f"{what}: (dst_valid_o, dst_fill_o, src_ready_o) expected "
                                  f"(0, 0, 1) got {got} at {now()} ps")
        await Timer(1, "ps")

    async def release_both():
        sclk.start()
        dclk.start(phase)
        task = cocotb.start_soon(reset_step(sclk, dut.src_rst_i))
        await reset_step(dclk, dut.dst_rst_i)
        await task

    dut.src_rst_i.value = 1
    dut.dst_rst_i.value = 1
    dut.src_valid_i.value = 0
    dut.src_data_i.value = 0
    dut.dst_ready_i.value = 0
    await expect_reset_state("reset at time zero")
    await release_both()
    f = Fifo(dut, random, src, dst)
    depth = f.depth
    base = 0  # f.popped at the last reset: both pointers count from there
    for round_, pos in enumerate((0, depth)):
        # Bring both pointers (the FIFO is empty) to `pos` modulo 2 * depth
        # by passing exactly `through` words.
        through = (pos - (f.popped - base)) % (2 * depth)
        if through:
            target = f.popped + through
            f.write_limit = f.pushed + through
            f.p_write, f.p_read = 1.0, 1.0
            for _ in range(8 * through + 16):
                await FallingEdge(dut.dst_clk_i)
                if f.popped == target:
                    break
            assert f.popped == target, f"round {round_}: {f.popped - target + through} read"
            f.write_limit = None
            f.p_write, f.p_read = 0.0, 0.0
            # The last read's dst_clk_i edge, then the read pointer crosses
            # back: rgray_q and its source copy hold the full pattern.
            await falling_edges(dut.dst_clk_i, 1)
            await falling_edges(dut.src_clk_i, 4)
        await f.fill()
        f.p_write, f.p_read = 0.0, 0.0
        await falling_edges(dut.dst_clk_i, 4)
        fill = int(dut.dst_fill_o.value)
        assert fill == depth, f"round {round_}: dst_fill_o {fill} when full, expected {depth}"
        # Both clocks stop low with src_valid_i and dst_ready_i low.
        await dclk.stop()
        await sclk.stop()
        assert int(dut.src_ready_o.value) == 0, f"round {round_}: not full before the reset"
        await Timer(random.randrange(1, 3 * slow), "ps")
        dut.src_rst_i.value = 1
        dut.dst_rst_i.value = 1
        f.queue.clear()
        base = f.popped
        COV.sample(async_reset="stopped_pulse" if round_ == 0 else "stopped_held")
        await expect_reset_state(f"round {round_}: resets asserted with the clocks stopped")
        await Timer(random.randrange(1, 3 * slow), "ps")
        if round_ == 0:
            dut.src_rst_i.value = 0
            dut.dst_rst_i.value = 0
            await expect_reset_state(f"round {round_}: resets released with the clocks stopped")
            await Timer(random.randrange(1, 3 * slow), "ps")
            sclk.start()
            dclk.start(phase)
        else:
            await release_both()
        await falling_edges(dut.src_clk_i, 4)
        await falling_edges(dut.dst_clk_i, 4)
        await expect_reset_state(f"round {round_}: after the restart")
        popped = f.popped
        await f.run(0.7, 0.7, 8 * depth)
        await f.drain()
        assert f.popped > popped, f"round {round_}: no traffic after the reset"
