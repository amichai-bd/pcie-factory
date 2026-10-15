"""pcie_pcs_align tests (wiki/src/dv/pcs_align/DV_PLAN.md).

cocotb drives pcie_pcs_align directly (no SV testbench). Each cycle, at the
falling clock edge, the outputs are compared with the cycle model
(models/pcie_pcs_align_model.py) and the next input word is driven.

Two further oracles, independent of the cycle model:

- **Alignment.** Every bit carries its transmitted bit index, so each
  locked output symbol is checked to be one whole transmitted symbol,
  except between a bit slip and the relock that answers it.
- **Closed relock loop.** The pcs top's relock rule (RECEIVE.md, Relock
  request) runs in the testbench: output words reach an error counter
  ``FWD`` cycles later (the elastic buffer and decoder), and its request
  reaches ``relock_i`` 2 to 5 cycles after that (the pulse crossing).
  Each slip must be answered by a relock request within the HAS target of
  32 cycles; isolated bit errors must never request one.
"""

from __future__ import annotations

import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from models import pcie_pcs_align_model as m
from regress.funcov import Covergroup

FWD = 8           # aligner output to the error counter, cycles
BACK = (2, 5)     # relock request to relock_i, cycles (pulse crossing jitter)
SLIP_DETECT = 32  # HAS target: relock requested within 32 cycles of a slip

# Lock from every comma position of the 20-bit window, both comma forms.
LOCK = Covergroup("pcs_align_lock")
LOCK.coverpoint("position", bins={str(p): p for p in range(20)})
LOCK.coverpoint("comma", bins={"minus": "minus", "plus": "plus"})
LOCK.cross("position_x_comma", "position", "comma")

# How lock is lost and retaken (RECEIVE.md, Hunt, Loss of lock, Relock).
RELOCK = Covergroup("pcs_align_relock")
RELOCK.coverpoint("event", bins={
    "lock_from_hunt": "lock_from_hunt",        # lock taken after unlocked words
    "relock_same_cycle": "relock_same_cycle",  # relock_i and a comma: no unlocked word
    "relock_to_hunt": "relock_to_hunt",        # relock_i with no comma: unlocked
    "idle_drop": "idle_drop",                  # lock lost to electrical idle
    "relock_while_hunting": "relock_while_hunting",
})

# Where slips and isolated errors land (DV plan, Relock).
IMPAIR = Covergroup("pcs_align_impairments")
IMPAIR.coverpoint("slip", bins={k: k for k in (
    "ts_drop", "ts_repeat", "fts_drop", "fts_repeat", "packet_drop", "packet_repeat",
    "skp_com", "skp_skp", "close_pair")})
IMPAIR.coverpoint("flip", bins={"ts": "ts", "packet": "packet"})


class Env:
    """Drives one line through the DUT next to the model and the relock
    loop, and checks every cycle."""

    def __init__(self, dut, rng: random.Random, loop: bool = True) -> None:
        self.dut = dut
        self.rng = rng
        self.model = m.Aligner()
        self.loop = loop
        self.cycle = 0
        self.after_reset = True
        self.fwd: deque = deque()
        self.relock_at: set[int] = set()
        self.counter = m.ErrorCounter()
        self.requests: list[int] = []      # cycle of each relock request
        self.acquisitions: list[int] = []  # cycle of each lock acquisition
        self.misaligned: list[int] = []    # cycles with a misaligned locked symbol
        self.locked_symbols = 0
        self.same_cycle = 0                # relocks with no unlocked word between runs
        self.txwin: deque = deque([[-1] * 20, [-1] * 20], maxlen=2)

    async def start(self) -> None:
        cocotb.start_soon(Clock(self.dut.clk_i, 8, unit="ns").start())
        self.dut.data_i.value = 0
        self.dut.elec_idle_i.value = 1
        self.dut.relock_i.value = 0
        self.dut.rst_i.value = 1
        await FallingEdge(self.dut.clk_i)
        await self.reset(4)

    async def reset(self, n: int) -> None:
        """Hold reset for ``n`` edges with idle inputs."""
        for _ in range(n):
            await self.step(0, 1, [-1] * 20, rst=1)
        self.counter = m.ErrorCounter()
        self.fwd.clear()
        self.relock_at.clear()

    def check(self) -> None:
        dut, mod = self.dut, self.model
        got = (int(dut.lock_o.value), int(dut.gen_o.value))
        exp = (mod.lock, mod.gen)
        assert got == exp, (f"cycle {self.cycle}: (lock_o, gen_o) expected {exp} got {got}")
        if not self.after_reset:
            sym = int(dut.sym_o.value)
            assert sym == mod.sym, (f"cycle {self.cycle}: sym_o expected {mod.sym:#07x} "
                                    f"got {sym:#07x} (lock {mod.lock}, offset {mod.offset})")

    async def step(self, data: int, idle: int, txpos: list[int], rst: int = 0) -> None:
        """Check the outputs of the last edge, then drive one word."""
        self.check()
        mod = self.model
        relock = int(self.cycle in self.relock_at)
        window = mod.prev | (mod.cur << 20)
        was_locked = mod.lock
        self.dut.data_i.value = data
        self.dut.elec_idle_i.value = idle
        self.dut.relock_i.value = relock
        self.dut.rst_i.value = rst
        mod.clock(data, idle, relock, rst)
        self.after_reset = bool(rst)
        if not rst:
            self.observe(window, was_locked, relock)
        self.txwin.append(txpos)
        self.cycle += 1
        await FallingEdge(self.dut.clk_i)

    def observe(self, window: int, was_locked: int, relock: int) -> None:
        """Coverage, alignment and the relock loop for the edge just modelled."""
        mod = self.model
        if mod.acquired:
            self.acquisitions.append(self.cycle)
            p = next(p for p in range(20) if m.comma_at(window, p))
            form = "minus" if (window >> p) & 0x7F == m.code8b10b.COMMA_MINUS else "plus"
            LOCK.sample(position=p, comma=form)
            if was_locked and relock:
                self.same_cycle += 1
                RELOCK.sample(event="relock_same_cycle")
            else:
                RELOCK.sample(event="lock_from_hunt")
        elif was_locked and not mod.lock:
            RELOCK.sample(event="relock_to_hunt" if relock else "idle_drop")
        elif relock and not was_locked:
            RELOCK.sample(event="relock_while_hunting")
        if mod.lock:
            win = self.txwin[0] + self.txwin[1]
            for j in range(2):
                p = mod.offset + 10 * j
                if all(t < 0 for t in win[p:p + 10]):
                    continue  # filler or noise ahead of the first comma
                self.locked_symbols += 1
                if m.symbol_aligned(win, p) is None:
                    self.misaligned.append(self.cycle)
        if self.loop:
            self.fwd.append(((mod.sym & 0x3FF, mod.sym >> 10), mod.lock, mod.gen))
            if len(self.fwd) > FWD:
                codes, lock, gen = self.fwd.popleft()
                if self.counter.word(codes, lock, gen):
                    self.requests.append(self.cycle)
                    self.relock_at.add(self.cycle + self.rng.randint(*BACK))

    async def send(self, line: m.Line) -> list[int]:
        """Drive every word of ``line``; return the first cycle of each."""
        line.pad()
        first = self.cycle
        for data, idle, txpos in line.words():
            await self.step(data, idle, txpos)
        return [first + b // 20 for b in line.slips]

    async def drain(self, n: int = 16) -> None:
        for _ in range(n):
            await self.step(0, 1, [-1] * 20)


def check_slips(env: Env, slip_cycles: list[int], tag: str) -> None:
    """Every slip that hits a locked aligner is answered by a relock request
    within SLIP_DETECT cycles, and misaligned locked symbols occur only
    between a slip and the first lock taken after the last relock request
    that follows it (before the next slip): a comma received just before
    the slip can give one more lock at the old boundary, which the relock
    rule answers in turn."""
    windows = []
    ends = sorted(slip_cycles)[1:] + [env.cycle + 1]
    for s, end in zip(sorted(slip_cycles), ends):
        hunting = any(r < s and not any(r < a <= s for a in env.acquisitions)
                      for r in env.requests)
        if not hunting:  # a slip while hunting needs no detection
            reqs = [r for r in env.requests if r >= s]
            assert reqs and reqs[0] - s <= SLIP_DETECT, (
                f"{tag}: slip at cycle {s} not answered within {SLIP_DETECT} cycles "
                f"(next request {reqs[0] if reqs else None})")
        reqs = [r for r in env.requests if s <= r < end]
        acq = [a for a in env.acquisitions if reqs and reqs[-1] < a < end]
        windows.append((s, acq[0] if acq else end))
    for c in env.misaligned:
        assert any(a <= c <= b for a, b in windows), (
            f"{tag}: misaligned locked symbol at cycle {c} outside every slip window {windows}")
    for r in env.requests:  # a request answers misalignment, never isolated errors
        assert any(r - FWD - SLIP_DETECT <= c <= r for c in env.misaligned), (
            f"{tag}: relock request at cycle {r} with no misaligned symbol before it")


def check_no_requests(env: Env, tag: str) -> None:
    assert not env.requests, f"{tag}: relock requested at cycles {env.requests} with no slip"
    assert not env.misaligned, f"{tag}: misaligned locked symbols at cycles {env.misaligned[:8]}"


# ------------------------------------------------------------------ L0


@cocotb.test()
async def lock_offsets(dut):
    """L0: lock from every bit offset of the 20-bit window, both comma forms
    (start RD - and +), with every locked symbol whole and no relock."""
    env = Env(dut, random.Random(0))
    await env.start()
    for rd in (-1, 1):
        codes = m.encode(m.ts_traffic(6), rd=rd)
        for shift in range(20):
            await env.reset(3)
            env.requests.clear()
            env.misaligned.clear()
            env.acquisitions.clear()
            line = m.Line()
            line.filler(40 + shift)
            line.send(codes)
            await env.send(line)
            assert len(env.acquisitions) == 1, (
                f"rd {rd} shift {shift}: {len(env.acquisitions)} lock acquisitions, expected 1")
            assert env.model.offset == shift % 10, (
                f"rd {rd} shift {shift}: boundary {env.model.offset}, expected {shift % 10}")
            check_no_requests(env, f"rd {rd} shift {shift}")
    assert env.locked_symbols > 0


@cocotb.test()
async def controls(dut):
    """L0: relock_i, electrical idle and reset around lock, open loop: a
    relock while hunting does nothing, a relock with no comma in the window
    drops to hunting, a relock with a comma retakes lock in the same cycle
    (the generation flips, lock_o stays 1), idle drops lock a cycle after it
    is seen and blocks hunting for two words, reset clears lock and the
    generation."""
    env = Env(dut, random.Random(1), loop=False)
    await env.start()
    ts = m.encode(m.ts_traffic(60))
    line = m.Line()
    line.filler(40)
    line.send(ts)
    line.pad()
    words = line.words()
    # Relock while hunting: in the filler before the first comma.
    env.relock_at.add(env.cycle + 1)
    for w in words[:3]:
        await env.step(*w)
    assert not env.model.lock
    # Lock, then relock at every phase of the 8-word COM period.
    for w in words[3:20]:
        await env.step(*w)
    assert env.model.lock
    base = env.cycle
    for k in range(8):
        env.relock_at.add(base + 4 + 9 * k)  # phase k of the period
    for w in words[20:120]:
        await env.step(*w)
    assert env.same_cycle >= 1, "no same-cycle relock over all eight phases"
    # Electrical idle: lock drops the cycle after it is seen; with the line
    # active again, lock waits until both window words are active.
    idle = [(d, 1, t) for d, _, t in words[120:124]]
    for w in idle + words[124:140]:
        await env.step(*w)
    assert env.model.lock
    # One-word idle pulses at every phase of the COM period: the window
    # that still holds the idle word must not give lock, comma or not.
    for k in range(8):
        i = 140 + 20 * k + k
        for j, w in enumerate(words[i:i + 20]):
            await env.step(w[0], int(j == 0), w[2])
        assert env.model.lock
    # Reset while locked.
    await env.reset(2)
    assert not env.model.lock and env.model.gen == 0
    for w in words[310:370]:
        await env.step(*w)
    assert env.model.lock


# ------------------------------------------------------------------ L1


def slip_events(rng: random.Random, syms, n: int, kinds) -> dict[int, list]:
    """``n`` slips at chosen symbol kinds, at least 300 symbols apart, after
    symbol 200; returns the events and the bins they hit."""
    ev: dict[int, list] = {}
    places = list(range(200, len(syms) - 100, 300))
    rng.shuffle(places)
    for p in sorted(places[:n]):
        ev[p] = [(rng.choice(kinds), rng.randrange(10))]
    return ev


def skp_slip_events(syms, rng: random.Random, n: int) -> tuple[dict[int, list], list[str]]:
    """Slips on the COM or on one of the SKPs of SKP ordered sets."""
    sets = [i for i in range(200, len(syms) - 8)
            if syms[i] == (1, m.K_COM) and syms[i + 1] == (1, m.K_SKP)]
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


async def run_line(env: Env, syms, ev: dict[int, list], tag: str, bins: list[str]) -> None:
    await env.reset(3)
    env.requests.clear()
    env.misaligned.clear()
    env.acquisitions.clear()
    line = m.Line()
    line.filler(40)
    line.send(m.encode(syms), ev)
    slips = await env.send(line)
    await env.drain()
    check_slips(env, slips, tag)
    for b in bins:
        IMPAIR.sample(slip=b)
    assert env.acquisitions, f"{tag}: never locked"
    dut = env.dut
    dut._log.info("%s: %d slips, %d relock requests, %d locks", tag, len(slips),
                  len(env.requests), len(env.acquisitions))


@cocotb.test()
async def slips(dut):
    """L1: relock after slips in comma-dense training traffic, packet
    traffic and inside SKP ordered sets, and after slip pairs a few symbols
    apart; isolated bit errors never request a relock."""
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    rng = random.Random(cocotb.RANDOM_SEED)
    env = Env(dut, rng)
    await env.start()
    ts = m.ts_traffic(400)
    pk = m.packet_traffic(rng, 60)
    fts = m.fts_traffic(600)
    for name, syms in (("ts", ts), ("fts", fts), ("packet", pk)):
        for kind in ("drop", "repeat"):
            ev = slip_events(rng, syms, 8, (kind,))
            await run_line(env, syms, ev, f"{name} {kind}", [f"{name}_{kind}"])
        ev, bins = skp_slip_events(syms, rng, 6)
        await run_line(env, syms, ev, f"{name} in SKP", bins)
    # Slip pairs in the same direction: a second slip a few symbols after
    # the first, possibly while the first relock is still under way.
    for gap in (2, 6, 10, 20, 40, 90):
        for name, syms in (("ts", ts), ("packet", pk)):
            ev = {}
            for k in range(4):
                p = 300 + 900 * k
                kind = ("drop", "repeat")[k % 2]   # same direction: a 2-bit shift
                ev[p] = [(kind, rng.randrange(10))]
                ev[p + gap] = [(kind, rng.randrange(10))]
            await run_line(env, syms, ev, f"{name} pair gap {gap}", ["close_pair"])
    # Isolated single-bit errors: reported downstream, never a relock.
    for name, syms in (("ts", ts), ("packet", pk)):
        ev = {p: [("flip", rng.randrange(10))] for p in range(200, len(syms) - 50, 150)}
        await run_line(env, syms, ev, f"{name} flips", [])
        check_no_requests(env, f"{name} flips")
        IMPAIR.sample(flip=name)
    # The generation bit must have re-armed the loop with no unlocked word
    # between two lock runs, and later slips were still answered.
    assert env.same_cycle > 0, "no relock retook lock in the same cycle (vacuous re-arm check)"
    dut._log.info("same-cycle relocks: %d", env.same_cycle)


@cocotb.test()
async def no_false_lock(dut):
    """L1: encoded random data at every bit offset never locks; random bits
    flagged electrical idle never lock; random bits not flagged idle may
    lock falsely, and the relock rule then recovers the right boundary."""
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    rng = random.Random(cocotb.RANDOM_SEED)
    env = Env(dut, rng)
    await env.start()
    for shift in range(20):
        await env.reset(3)
        env.acquisitions.clear()
        data = [(0, rng.randrange(256)) for _ in range(600)]
        line = m.Line()
        line.filler(40 + shift)
        line.send(m.encode(data, rd=rng.choice((-1, 1))))
        await env.send(line)
        assert not env.acquisitions, f"shift {shift}: locked on encoded data at {env.acquisitions}"
    # Electrical-idle noise.
    await env.reset(3)
    env.acquisitions.clear()
    line = m.Line()
    line.noise(rng, 20 * 2000, idle=1)
    await env.send(line)
    assert not env.acquisitions, f"locked during electrical idle at {env.acquisitions[:4]}"
    # Noise that is not flagged idle, then packet traffic: any false lock
    # must give way to the true boundary.
    await env.reset(3)
    env.requests.clear()
    env.misaligned.clear()
    env.acquisitions.clear()
    line = m.Line()
    line.noise(rng, 20 * 300, idle=0)
    start = env.cycle
    line.send(m.encode(m.packet_traffic(rng, 20)))
    await env.send(line)
    noise_end = start + 300
    late = [c for c in env.misaligned if c > noise_end + 2 * SLIP_DETECT]
    assert not late, f"still misaligned {len(late)} cycles after the noise, first at {late[0]}"
    assert env.model.lock, "not locked at the end of the packet traffic"
    dut._log.info("noise: %d false locks answered by %d relock requests",
                  len(env.acquisitions) - 1, len(env.requests))


# ------------------------------------------------------------------ L2


@cocotb.test()
async def long_random(dut):
    """L2: long mixed traffic with random slips, bit errors and electrical
    idle gaps, closed relock loop, every check on."""
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    rng = random.Random(cocotb.RANDOM_SEED)
    env = Env(dut, rng)
    await env.start()
    for seg in range(40):
        syms = (m.ts_traffic(rng.randrange(20, 120)) + m.packet_traffic(rng, rng.randrange(10, 40))
                + m.ts_traffic(rng.randrange(5, 40)))
        ev = slip_events(rng, syms, rng.randrange(1, 6), ("drop", "repeat"))
        for p in range(250, len(syms) - 50, 600):
            if not any(abs(p - q) < 150 for q in ev):
                ev[p] = [("flip", rng.randrange(10))]
        line = m.Line()
        line.noise(rng, 20 * rng.randrange(4, 400), idle=1)   # electrical idle gap
        line.filler(rng.randrange(20))
        env.requests.clear()
        env.misaligned.clear()
        env.acquisitions.clear()
        line.send(m.encode(syms, rd=rng.choice((-1, 1))), ev)
        slips = await env.send(line)
        check_slips(env, slips, f"segment {seg}")
        dut._log.info("segment %d: %d symbols, %d slips, %d relock requests", seg,
                      len(syms), len(slips), len(env.requests))
    await env.drain()
