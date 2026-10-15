"""pcie_pcs lock tests (wiki/src/dv/pcs/DV_PLAN.md): the aligner block's
checks through the pcie_pcs ports.

Lines are built bit by bit (tb.pcs_lines): filler shifts the comma to any
bit offset, electrical idle and noise sit at exact bits, and slips and
flips are placed on chosen symbols. The receive scoreboard
(models.pcie_pcs_rx_check) places every RxValid run in the sent stream
(with numbered TS1 sets, training is never ambiguous) and re-anchors a run
after a slip, since a relock in comma-dense traffic retakes lock in the
same cycle and keeps RxValid high. The checks of every test:

- data intact: every run is a piece of the sent stream, wrong symbols only
  next to a reported error;
- lock start and the lock target: each run that follows a loss of lock
  starts at its comma, or the symbol before it, within 10 cycles of the
  comma (tb.pcs_rxbench.check_lock_latency);
- slip and error windows: no errored word (RxStatus 4 or 7) except within
  48 cycles after a slip (the HAS 32-cycle relock target plus the receive
  latency) or 24 cycles after a flipped bit; so every slip is detected and
  answered, and isolated errors never cost lock.
"""

from __future__ import annotations

import cocotb

from models import pcie_pcs_align_model as am
from models.pcie_pcs_align_model import K_COM, K_END, K_STP
from models.pcie_pcs_rx_check import ERROR_CODES
from regress.funcov import Covergroup
from tb.pcs_lines import Builder, fts_traffic, skp_slip_events, slip_events, ts_traffic
from tb.pcs_bench import regress_seed, seeded
from tb.pcs_rxbench import CYCLE, K_IDL, RxBench, check_ei_order, check_latency, check_lock_latency

SLIP_WINDOW = 48                  # cycles: relock target 32 plus the receive latency
FLIP_WINDOW = 24                  # cycles: a flipped bit's errored words reach PIPE

# Lock from every comma position of the aligner's 20-bit window, both forms.
COV_LOCK = Covergroup("pcs_align_lock")
COV_LOCK.coverpoint("position", bins={str(p): p for p in range(20)})
COV_LOCK.coverpoint("comma", bins={"minus": "minus", "plus": "plus"})
COV_LOCK.cross("position_x_comma", "position", "comma")

# How lock is lost and retaken, as PIPE shows it.
COV_RELOCK = Covergroup("pcs_align_relock")
COV_RELOCK.coverpoint("event", bins=["lock_from_hunt", "relock_same_cycle", "relock_to_hunt",
                                     "idle_drop", "reset_drop"])

# Where slips and isolated errors land.
COV_EIOS = Covergroup("pcs_align_eios")
COV_EIOS.coverpoint("phase", bins={str(p): p for p in range(20)})

COV_IMPAIR = Covergroup("pcs_align_impairments")
COV_IMPAIR.coverpoint("slip", bins=["ts_drop", "ts_repeat", "fts_drop", "fts_repeat",
                                    "packet_drop", "packet_repeat", "skp_com", "skp_skp",
                                    "close_pair"])
COV_IMPAIR.coverpoint("flip", bins=["ts", "packet"])


def model_locks(line) -> list[tuple[int, str]]:
    """The comma position and form of each lock the aligner block's model
    takes on ``line`` with no relock request (open loop)."""
    al, out = am.Aligner(), []
    for data, idle, _ in line.words():
        window = al.prev | (al.cur << 20)
        al.clock(data, idle, 0)
        if al.acquired:
            p = next(p for p in range(20) if am.comma_at(window, p))
            form = "minus" if (window >> p) & 0x7F == am.code8b10b.COMMA_MINUS else "plus"
            out.append((p, form))
    return out


def impairment_times(rx: RxBench) -> tuple[list[float], list[float]]:
    line = rx.link.line
    return ([rx.chan.time_of_bit(b) for b in line.slips],
            [rx.chan.time_of_bit(b) for b in line.flips])


def covered(rx: RxBench, t: float) -> bool:
    """The aligner was locked when the line carried time ``t``: RxValid on
    PIPE a few cycles later, before anything received at ``t`` can reach it."""
    ws = [w for w in rx.words if t + 2 * CYCLE <= w.t <= t + 6 * CYCLE]
    return bool(ws) and all(w.valid for w in ws)


def check_windows(rx: RxBench, runs, where: str) -> None:
    """No errored word outside the window after a slip or a flip, except
    the first word of a lock: its symbol before the comma may be filler or
    noise received ahead of the comma, and the comma after it is decoded
    from the RD that symbol leaves (8B10B.md, Decoder)."""
    slips, flips = impairment_times(rx)
    wins = ([(s, s + SLIP_WINDOW * CYCLE) for s in slips]
            + [(f, f + (FLIP_WINDOW if covered(rx, f) else 2 * SLIP_WINDOW) * CYCLE)
               for f in flips])
    wins += [(r.words[0].t, r.words[0].t) for r in runs]
    bad = [w.t for w in rx.words if w.valid and w.status in ERROR_CODES
           and not any(a <= w.t <= b for a, b in wins)]
    assert not bad, (f"{where}: errored words outside every slip and flip window, first at "
                     f"{bad[0]} ps (slips at {[int(s) for s in slips[:6]]})")


def check_link(rx: RxBench, where: str, lead_skip=True):
    """Data intact, run starts and lock latency, impairment windows."""
    slips, flips = impairment_times(rx)
    # While the aligner hunts, a flipped bit can form a comma: a false lock
    # that the relock rule answers like a slip.
    hunting = [f for f in flips if not covered(rx, f)]

    def slip_possible(t0, t):
        return any(t0 - SLIP_WINDOW * CYCLE <= s <= t for s in slips + hunting)

    line = rx.link.line
    flipped = {line.txpos[b] // 10 for b in line.flips if line.txpos[b] >= 0}
    runs = rx.check(rx.link, where, slip_possible=slip_possible, resync=True,
                    lead_skip=lambda t: lead_skip, flipped=flipped)
    check_windows(rx, runs, where)
    idle_at = [rx.chan.times[rx.chan.lead + i] for i, (_, idle) in enumerate(rx.chan.words)
               if idle and i and not rx.chan.words[i - 1][1] and rx.chan.lead + i < len(rx.chan.times)]
    # An isolated flip in lock never costs lock (unless the line goes idle).
    for f in flips:
        if f in hunting or any(abs(f - s) < 2 * SLIP_WINDOW * CYCLE for s in slips + idle_at):
            continue
        r = next((r for r in runs if r.words[0].t < f < r.words[-1].t), None)
        assert r is None or not (f <= r.words[-1].t <= f + SLIP_WINDOW * CYCLE) or rx.at_end(
            rx.words.index(r.words[-1]) + 1), f"{where}: lock lost after a single bit error at {f} ps"
    check_latency(rx, runs, settle=slips)
    # A run starts after a loss of lock: its comma or the symbol before it
    # (a false lock on a comma that a slip, or a flip while hunting, formed
    # aside: the relock rule answers it like a slip).
    fresh = [r for r in runs if not r.noise_lock and r.start >= 0
             and not any(f <= r.words[0].t <= f + 2 * SLIP_WINDOW * CYCLE
                         for f in hunting + slips)]
    check_lock_latency(rx, rx.link, fresh, where)
    for r in runs[1:]:
        COV_RELOCK.sample(event="lock_from_hunt")
    for s in slips:
        if same_cycle(runs, s):
            COV_RELOCK.sample(event="relock_same_cycle")
    lost = [r for r in runs if r.lost and not rx.at_end(rx.words.index(r.words[-1]))]
    for r in lost:
        # A run that loses the stream must end within the slip window.
        t_end = r.words[-1].t
        assert any(s <= t_end <= s + 2 * SLIP_WINDOW * CYCLE for s in slips + hunting), (
            f"{where}: run at {r.words[0].t} ps lost the stream and kept RxValid until {t_end}")
    return runs


def same_cycle(runs, s: float) -> bool:
    """A slip answered with RxValid kept high: lock retaken in the same
    cycle (the scoreboard then finds the data intact after it)."""
    return any(r.words[0].t < s and r.words[-1].t > s + 2 * SLIP_WINDOW * CYCLE for r in runs)


async def run_link(dut, b: Builder, ppm: float, where: str, **kw) -> tuple[RxBench, list]:
    link = b.link()
    rx = RxBench(dut, b.rng, ppm)
    await rx.start(link, max_cycles=len(link.line.bits) // 20 + 4000)
    await rx.run_line()
    return rx, check_link(rx, where, **kw)


# ------------------------------------------------------------------ L0

@cocotb.test()
async def rx_lock_offsets(dut):
    """L0: lock from every bit offset of the 20-bit window with both comma
    forms (training from RD- and from RD+), after electrical idle: one run
    per lock, intact from its comma, within the lock target, no relock."""
    rng = seeded(dut)
    b = Builder(rng)
    for rd in (-1, 1):
        for shift in range(20):
            b.idle(12)
            b.filler(40 + shift)
            b.send(ts_traffic(6, start=len(b.syms), skp_every=1000) + [(1, K_END)] * 2, rd=rd)
    b.idle(4)
    locks = model_locks(b.line)
    rx, runs = await run_link(dut, b, 0.0, "lock_offsets")
    assert len(runs) == 40 == len(locks), f"{len(runs)} runs and {len(locks)} model locks for 40"
    for p, form in locks:
        COV_LOCK.sample(position=p, comma=form)
    for _ in runs:
        COV_RELOCK.sample(event="idle_drop")


@cocotb.test()
async def rx_eios_phases(dut):
    """L0 (#182): an EIOS before electrical idle at every RX bit phase 0 to
    19 of the transmitter's word boundary (filler bits shift it; the idle
    starts at the transmitted word boundary right after the EIOS, so the
    word holding it is flagged idle from its last bit). At every phase the
    whole EIOS leaves under RxValid before RxElecIdle rises: at phases 10
    to 19 its last IDL starts inside the idle-flagged word, and the
    aligner's EIOS completion keeps it locked. The seed's parity picks a
    clock offset of +300 or -300 ppm, so SKP corrections also move the
    elastic buffer's word parity."""
    rng = seeded(dut)
    b = Builder(rng)
    phases = list(range(20))
    rng.shuffle(phases)
    idles = []
    b.idle(12)
    for phase in phases:
        b.filler(phase)
        seg = len(b.syms)
        syms = fts_traffic(12, start=seg) + [(1, K_STP)] + [(0, rng.randrange(256))
                                                           for _ in range(rng.randrange(20, 60))]
        syms += [(1, K_END)]
        if (len(syms)) % 2:
            syms += [(0, rng.randrange(256))]
        syms += [(1, K_COM), (1, K_IDL), (1, K_IDL), (1, K_IDL)]
        b.send(syms)
        idles.append((len(b.syms), len(b.syms)))
        b.idle(16)
    link = b.link()
    link.idles = idles
    ppm = (1 if regress_seed() % 2 else -1) * 300.0
    rx = RxBench(dut, b.rng, ppm)
    await rx.start(link, max_cycles=len(link.line.bits) // 20 + 4000)
    await rx.run_line()
    runs = check_link(rx, "eios_phases")
    eo = check_ei_order(rx, link, runs, "eios_phases")
    assert eo["gaps"] == eo["eios_whole"] == 20, f"eios_phases: {eo}"
    for phase in phases:
        COV_EIOS.sample(phase=phase)


@cocotb.test()
async def rx_lock_controls(dut):
    """L0: one-word electrical-idle pulses at every phase of the TS1 COM
    period (lock drops, and the window that still holds the idle word never
    gives lock), and rst_i with traffic in flight (lock and the buffer
    restart, lock returns at the next comma, intact)."""
    rng = seeded(dut)
    b = Builder(rng)
    b.idle(16)
    b.send(ts_traffic(20, skp_every=1000))
    for k in range(8):
        # An idle pulse on the word holding TS1 symbols 2k and 2k + 1.
        b.line.pad()
        b.send(ts_traffic(3, start=len(b.syms), skp_every=1000))
        first = len(b.line.bits) // 20
        b.send(ts_traffic(10, start=len(b.syms), skp_every=1000))
        w = first + k
        for i in range(20 * w, 20 * w + 20):
            b.line.idle[i] = 1
    b.send(ts_traffic(400, start=len(b.syms), skp_every=70))
    link = b.link()
    rx = RxBench(dut, rng, 0.0)
    await rx.start(link)
    rx.b.mac.set(tx_elec_idle=1)
    # rst_i once the training after the pulses has run for a while.
    t_line = rx.chan.lead + len(rx.chan.words) - 300
    while len(rx.chan.times) < t_line:
        await rx.b.tick()
    await rx.b.reset("rst_i", 10)
    COV_RELOCK.sample(event="reset_drop")
    await rx.run_line()
    runs = check_link(rx, "lock_controls")
    assert len(runs) == 1 + 8 + 1, f"{len(runs)} runs: expected the first lock, 8 idle pulses " \
                                   f"and the reset"
    for _ in range(8):
        COV_RELOCK.sample(event="idle_drop")


# ------------------------------------------------------------------ L1

@cocotb.test()
async def rx_slips_training(dut):
    """L1: bit slips in comma-dense traffic (numbered TS1 sets, and FTS sets
    with a COM every four symbols), on the COM and the SKPs of SKP ordered
    sets, and slip pairs 2 to 90 symbols apart, at a seeded offset of up to
    300 ppm: every slip detected and answered within the window, the data
    intact once lock is retaken (in the same cycle or at the next comma);
    isolated bit flips never cost lock."""
    rng = seeded(dut)
    ppm = (1 if rng.random() < 0.5 else -1) * rng.uniform(0, 300)
    b = Builder(rng)
    b.idle(16)
    for name, make in (("ts", ts_traffic), ("fts", fts_traffic)):
        for kind in ("drop", "repeat"):
            syms = make(300, start=len(b.syms))
            b.send(syms, slip_events(rng, len(syms), 8, (kind,)))
            COV_IMPAIR.sample(slip=f"{name}_{kind}")
        syms = ts_traffic(300, start=len(b.syms))
        ev, bins = skp_slip_events(syms, rng, 4)
        b.send(syms, ev)
        for x in bins:
            COV_IMPAIR.sample(slip=x)
    for gap in (2, 6, 10, 20, 40, 90):
        syms = ts_traffic(120, start=len(b.syms))
        kind = rng.choice(("drop", "repeat"))
        b.send(syms, {300: [(kind, rng.randrange(10))], 300 + gap: [(kind, rng.randrange(10))]})
        COV_IMPAIR.sample(slip="close_pair")
    # Isolated flips: reported, never a relock.
    syms = ts_traffic(150, start=len(b.syms))
    b.send(syms, {p: [("flip", rng.randrange(10))] for p in range(200, len(syms) - 50, 150)})
    COV_IMPAIR.sample(flip="ts")
    b.idle(4)
    rx, runs = await run_link(dut, b, ppm, "slips_training")
    n_slips = len(b.line.slips)
    resyncs = sum(same_cycle(runs, t) for t in impairment_times(rx)[0])
    dut._log.info("slips_training: %d slips, %d runs, %d same-cycle relocks", n_slips,
                  len(runs), resyncs)
    assert resyncs, "no relock retook lock in the same cycle"
    assert len(runs) > 1, "no relock fell back to hunting"
    COV_RELOCK.sample(event="relock_to_hunt")


@cocotb.test()
async def rx_slips_packets(dut):
    """L1: bit slips in packet traffic and on SKP ordered sets inside it;
    isolated bit flips in packets never cost lock."""
    rng = seeded(dut)
    ppm = (1 if rng.random() < 0.5 else -1) * rng.uniform(0, 300)
    b = Builder(rng)
    b.idle(16)
    b.send(ts_traffic(8))
    for kind in ("drop", "repeat"):
        syms = am.packet_traffic(rng, 60)
        b.send(syms, slip_events(rng, len(syms), 6, (kind,)))
        b.send(ts_traffic(2, start=len(b.syms)))
        COV_IMPAIR.sample(slip=f"packet_{kind}")
    syms = am.packet_traffic(rng, 60)
    ev, bins = skp_slip_events(syms, rng, 4)
    b.send(syms, ev)
    for x in bins:
        COV_IMPAIR.sample(slip=x)
    syms = am.packet_traffic(rng, 40)
    b.send(syms, {p: [("flip", rng.randrange(10))] for p in range(200, len(syms) - 50, 150)})
    COV_IMPAIR.sample(flip="packet")
    b.send(ts_traffic(4, start=len(b.syms)))
    b.idle(4)
    await run_link(dut, b, ppm, "slips_packets")


@cocotb.test()
async def rx_no_false_lock(dut):
    """L1: encoded random data at every bit offset after electrical idle
    never gives lock (no RxValid); random bits flagged idle never give lock;
    a window holding commas at two symbol boundaries locks at the lowest
    one; random bits not flagged idle may lock falsely, and the relock rule
    then finds the true boundary in the packet traffic that follows."""
    rng = seeded(dut)
    b = Builder(rng)
    for shift in range(20):
        b.idle(8)
        b.filler(40 + shift)
        b.send([(0, rng.randrange(256)) for _ in range(300)], rd=rng.choice((-1, 1)))
    b.idle(2000)
    quiet_end = len(b.line.bits)
    # Two comma positions in the aligner's first window: a false comma at
    # bit 2 of the first active word, then a true COM at bit 13 (another
    # symbol boundary). Lock is taken at the lowest one (RTL assertion
    # a_lock_at_lowest_comma); the relock rule then finds the true boundary.
    for bit in (1, 1, 0, 0, 1, 1, 1, 1, 1, 1, 0, 1, 0):
        b.line.add(bit, -1)
    b.send(ts_traffic(8, start=len(b.syms), skp_every=1000))
    b.noise(20 * 300)
    noise_end = len(b.line.bits)
    b.send(am.packet_traffic(rng, 20))
    b.idle(4)
    link = b.link()
    rx = RxBench(dut, rng, 0.0)
    await rx.start(link, max_cycles=len(link.line.bits) // 20 + 4000)
    await rx.run_line()
    t_quiet = rx.chan.time_of_bit(quiet_end)
    early = [w.t for w in rx.words if w.valid and w.t <= t_quiet + 4 * CYCLE]
    dut._log.info("idle ends at %d ps, noise at %d ps", t_quiet, rx.chan.time_of_bit(noise_end))
    assert not early, f"RxValid at {early[0]} ps on data or idle noise"
    t_noise = rx.chan.time_of_bit(noise_end)
    late = [w.t for w in rx.words if w.valid and w.status in ERROR_CODES
            and w.t > t_noise + 2 * SLIP_WINDOW * CYCLE]
    assert not late, f"errored words {len(late)} after the noise, first at {late[0]} ps"
    runs = rx.check(link, "no_false_lock", slip_possible=lambda t0, t: t0 < t_noise + 60 * CYCLE,
                    resync=True, lead_skip=lambda t: True)
    good = [r for r in runs if r.words[0].t > t_noise]
    assert runs and not runs[-1].lost and runs[-1].last >= len(b.syms) - 8 - \
        sum(1 for s in b.syms if s == (1, am.K_SKP)), "packet traffic never delivered intact"
    dut._log.info("no_false_lock: %d runs after the noise, %d false locks", len(good),
                  len(runs) - len(good))


# ------------------------------------------------------------------ L2

@cocotb.test()
async def rx_align_long(dut):
    """L2: forty segments of training, packets and training with random
    slips, flips and electrical-idle gaps, at a seeded offset; every check."""
    rng = seeded(dut)
    ppm = (1 if rng.random() < 0.5 else -1) * rng.uniform(0, 600)
    b = Builder(rng)
    for seg in range(40):
        b.idle(rng.randrange(4, 400))
        b.filler(rng.randrange(20))
        syms = (ts_traffic(rng.randrange(20, 120), start=len(b.syms))
                + am.packet_traffic(rng, rng.randrange(10, 40))
                + ts_traffic(rng.randrange(5, 40), start=len(b.syms) + 4000))
        ev = slip_events(rng, len(syms), rng.randrange(1, 6))
        for p in range(250, len(syms) - 50, 600):
            if not any(abs(p - q) < 150 for q in ev):
                ev[p] = [("flip", rng.randrange(10))]
        b.send(syms, ev, rd=rng.choice((-1, 1)))
    b.idle(4)
    rx, runs = await run_link(dut, b, ppm, "align_long")
    dut._log.info("align_long: %d slips, %d flips, %d runs", len(b.line.slips),
                  len(b.line.flips), len(runs))
