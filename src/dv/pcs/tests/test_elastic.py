"""pcie_pcs elastic-buffer tests (wiki/src/dv/pcs/DV_PLAN.md): the elastic
buffer block's checks through the pcie_pcs ports.

Partner streams of every traffic kind (models.pcie_pcs_traffic: maximum
TLPs, random sizes with SKP ordered sets of one to five SKPs, sizes that
strand corrections, a burst one set short, dense sets) cross the
architecture model's channel at a fixed or seeded clock offset. The
receive scoreboard checks every RxValid run against the sent stream with
SKP set aside; every SKP ordered set leaves with one SKP fewer, the same
number or one more, with RxStatus 2, 0 or 1 on its COM's word; the RX
latency stays within the HAS 12 cycles, which bounds the visible fill at
12 symbols (RECEIVE.md, Depth versus clock offset); no overflow or
underflow inside the targets. Outside them, overflow and underflow are
reported as the RECEIVE.md rules say.
"""

from __future__ import annotations

import random

import cocotb

from models import pcie_pcs_traffic as tr
from models.pcie_pcs_model import P0, arch
from models.pcie_pcs_rx_check import ERROR_CODES, check_runs, check_skp_status
from regress.funcov import Covergroup
from tb.pcs_bench import regress_seed, seeded
from tb.pcs_channel import Link, build_link
from tb.pcs_rxbench import (COV_RX, CYCLE, RxBench, check_clean, check_idle, check_latency,
                            check_lock_latency, rd_settle_word, seeded_ppm)

COV_EB = Covergroup("pcs_elastic")
COV_EB.coverpoint("ppm", bins={
    "m600": -600, "m300": -300, "p300": 300, "p600": 600,
    "random": lambda v: v not in (-600, -300, 300, 600) and abs(v) <= 600,
    "beyond": lambda v: abs(v) > 600})
COV_EB.coverpoint("change", bins=["add_slot0", "add_slot1", "removed", "bare_com",
                                  "back_to_back"])
COV_EB.coverpoint("event", bins=["overflow", "underflow", "recentre_slow", "recentre_fast",
                                 "lock_start", "reset_restart", "pipe_reset"])
# Corrections per offset class: only the reachable direction of each (a
# slow RX clock needs additions, a fast one removals).
COV_EB.coverpoint("offset_correction", bins={
    "m600_add": ("m600", "add"), "m300_add": ("m300", "add"),
    "p300_rem": ("p300", "rem"), "p600_rem": ("p600", "rem"),
    "random_add": ("random", "add"), "random_rem": ("random", "rem")})


def offset_class(ppm: float) -> str:
    p = round(ppm)
    return {-600: "m600", -300: "m300", 300: "p300", 600: "p600"}.get(
        p, "random" if abs(p) <= 600 else "beyond")


def make_link(rng: random.Random, syms: list[tuple[int, int]]) -> Link:
    """``syms`` encoded and serialized by the architecture model's channel
    (no impairment)."""
    if len(syms) % 2:
        syms = syms + [(0, 0)]
    codes, _ = arch.tx_encode(syms)
    return Link(syms, arch.channel(rng, codes, [], 0, 0), [], 0)


def sample_changes(ppm: float, stats: dict[str, int]) -> None:
    COV_EB.sample(ppm=round(ppm))
    for k, n in stats.items():
        for _ in range(min(n, 16)):
            COV_EB.sample(change=k)
    adds = stats.get("add_slot0", 0) + stats.get("add_slot1", 0)
    rems = stats.get("removed", 0) + stats.get("bare_com", 0)
    if adds:
        COV_EB.sample(offset_correction=(offset_class(ppm), "add"))
    if rems:
        COV_EB.sample(offset_correction=(offset_class(ppm), "rem"))


async def eb_link(dut, kind: str, packets: int, interval: int, ppm: float,
                  where: str) -> dict[str, int]:
    """One clean run of ``kind`` traffic: lossless, every SKP set within the
    rules, latency within target; at 300 ppm or more, mostly additions on a
    slow RX clock and mostly removals on a fast one (the fill swings across
    both thresholds, so a few of the other kind occur)."""
    rng = seeded(dut)
    link = make_link(rng, tr.link_symbols(rng, kind, packets, interval))
    rx = RxBench(dut, rng, ppm)
    await rx.start(link, max_cycles=len(link.syms) // 2 + 4000)
    await rx.run_line()
    runs = check_clean(rx, link, where)
    stats: dict[str, int] = {}
    check_skp_status(link.syms, runs, where, stats)
    dut._log.info("%s at %+.1f ppm: SKP changes %s", where, ppm, stats)
    adds = stats.get("add_slot0", 0) + stats.get("add_slot1", 0)
    rems = stats.get("removed", 0) + stats.get("bare_com", 0)
    if ppm <= -300:
        assert adds > rems, f"{where}: {adds} additions, {rems} removals at {ppm} ppm"
    if ppm >= 300:
        assert rems > adds, f"{where}: {adds} additions, {rems} removals at {ppm} ppm"
    sample_changes(ppm, stats)
    return stats


def sign() -> int:
    return 1 if regress_seed() % 2 else -1


# ------------------------------------------------------------------ L1

@cocotb.test()
async def eb_tlp_m600(dut):
    """L1: maximum TLPs, the longest SKP interval, RX clock -600 ppm: SKP
    added after the COM, never an underflow, fill (latency) within target."""
    await eb_link(dut, "tlp", 8, arch.SKP_INTERVAL_MAX, -600.0, "tlp_m600")


@cocotb.test()
async def eb_tlp_p600(dut):
    """L1: the same at +600 ppm: SKP removed, never an overflow."""
    await eb_link(dut, "tlp", 8, arch.SKP_INTERVAL_MAX, 600.0, "tlp_p600")


@cocotb.test()
async def eb_random_m300(dut):
    """L1: random packet sizes and SKP sets of one to five SKPs, a random
    interval, -300 ppm."""
    rng = random.Random(regress_seed())
    await eb_link(dut, "random", 200, rng.randrange(1180, 1539), -300.0, "random_m300")


@cocotb.test()
async def eb_random_p300(dut):
    """L1: the same at +300 ppm."""
    rng = random.Random(regress_seed())
    await eb_link(dut, "random", 200, rng.randrange(1180, 1539), 300.0, "random_p300")


@cocotb.test()
async def eb_random_offset(dut):
    """L1: a seeded offset within +-600 ppm, random traffic and interval."""
    rng = random.Random(regress_seed())
    await eb_link(dut, "random", 200, rng.randrange(1180, 1539), rng.uniform(-600, 600),
                  "random_offset")


@cocotb.test()
async def eb_short_burst(dut):
    """L1: a packet just under two SKP intervals, so one set too few follows
    it, then a maximum TLP, at -600 ppm (the case the #73 sign-off carried
    forward)."""
    await eb_link(dut, "short_burst", 8, arch.SKP_INTERVAL_MAX, -600.0, "short_burst")


@cocotb.test()
async def eb_stress_sizes(dut):
    """L1: sizes that strand corrections (4122, 3070, 1534 and 2 bytes) at
    -600 or +600 ppm (the seed's parity)."""
    await eb_link(dut, "stress", 16, arch.SKP_INTERVAL_MAX, sign() * 600.0, "stress_sizes")


@cocotb.test()
async def eb_dense(dut):
    """L1: SKP ordered sets of one to five SKPs every 48 to 96 symbols at
    -600 or +600 ppm: additions after a COM in either half of a PIPE word,
    removals that leave a bare COM, sets back to back."""
    rng = random.Random(regress_seed())
    stats = await eb_link(dut, "dense", 600, rng.randrange(48, 97), sign() * 600.0, "dense")
    want = ("add_slot0", "add_slot1") if sign() < 0 else ("removed", "bare_com")
    assert all(stats.get(k) for k in want + ("back_to_back",)), f"dense: changes {stats}"


async def eb_idle(dut, ppm: float, gaps: int, symbols: int, where: str):
    rng = seeded(dut)
    link = build_link(rng, 10 * gaps, "random", 0, arch.SKP_INTERVAL_MIN, idle_gaps=gaps,
                      idle_symbols=symbols)
    rx = RxBench(dut, rng, ppm)
    await rx.start(link, max_cycles=len(link.syms) // 2 + 4000)
    await rx.run_line()
    runs = check_idle(rx, link, where)
    COV_EB.sample(event="recentre_slow" if ppm < 0 else "recentre_fast")
    COV_EB.sample(event="lock_start")
    COV_EB.sample(ppm=round(ppm))
    return runs


@cocotb.test()
async def eb_idle_m600(dut):
    """L1: two 6000-symbol electrical-idle gaps at -600 ppm: the read side
    recentres while unlocked, no underflow or overflow, lock and data return
    after each gap."""
    await eb_idle(dut, -600.0, 2, 6000, "idle_m600")


@cocotb.test()
async def eb_idle_p600(dut):
    """L1: the same at +600 ppm: the write side drops unlocked words."""
    await eb_idle(dut, 600.0, 2, 6000, "idle_p600")


@cocotb.test()
async def eb_lock_start(dut):
    """L1: twelve electrical-idle gaps of 1500 symbols at -600 or +600 ppm
    (the seed's parity): after each, the first locked word is never paired
    with an unlocked symbol (the elastic buffer drops it, on its write side
    on a fast RX clock, on its read side on a slow one), so every run starts
    at its lock comma or the symbol before it, within the lock target."""
    await eb_idle(dut, sign() * 600.0, 12, 1500, "lock_start")


async def flow_error(dut, ppm: float, overflow: bool) -> None:
    """Data with no ordered set at 2% off: nothing can be corrected."""
    rng = seeded(dut)
    link = make_link(rng, tr.plain_symbols(rng, 12000))
    rx = RxBench(dut, rng, ppm, fill_window=False)     # outside the targets
    await rx.start(link, max_cycles=len(link.syms) // 2 + 4000)
    await rx.run_line()
    st = rx.statuses()
    if overflow:
        ovf = [w.t for w in rx.words if w.status == 5]
        assert len(ovf) >= 2, f"no overflow at {ppm} ppm: {st}"

        def lost(t0, t):
            return any(t - 24 * CYCLE <= o <= t + 24 * CYCLE for o in ovf)

        runs = check_runs(link.syms, rx.words, where="overflow", slip_possible=lost,
                          resync=True, arrival=rx.arrival)
        # Words are lost only just before an overflow word; the decoder may
        # then see a disparity error where the RD jumped.
        for r in runs:
            for a, b in r.resyncs:
                assert lost(a, a), f"overflow: stream lost at {a} ps with no overflow word"
        bad = [w.t for w in rx.words if w.valid and w.status in ERROR_CODES
               and not any(o <= w.t <= o + 24 * CYCLE for o in ovf)]
        assert not bad, f"overflow: errored words away from the losses, first at {bad[0]}"
        COV_EB.sample(event="overflow")
    else:
        udf = [w for w in rx.words if w.status == 6]
        assert len(udf) >= 2, f"no underflow at {ppm} ppm: {st}"
        for w in udf:
            assert not w.valid and w.syms == ((1, 0xFE), (1, 0xFE)), \
                f"underflow word at {w.t} ps: RxValid {w.valid}, {w.syms}"
        # Nothing lost, and the RD holds across the underflow words.
        runs = check_runs(link.syms, rx.words, where="underflow", arrival=rx.arrival)
        assert all(r.mismatches == 0 for r in runs)
        assert not any(w.valid and w.status in ERROR_CODES for w in rx.words), \
            "underflow: an error after an underflow word"
        COV_EB.sample(event="underflow")
    COV_EB.sample(ppm=round(ppm))


@cocotb.test()
async def eb_overflow(dut):
    """L1: RX clock 2% fast with no SKP: words are lost, RxStatus 5 on the
    first word after each loss, the rest in order."""
    await flow_error(dut, 20_000.0, True)


@cocotb.test()
async def eb_underflow(dut):
    """L1: RX clock 2% slow with no SKP: underflow words (RxStatus 6, EDB EDB
    as K, RxValid 0) and no symbol lost; the RD holds across them."""
    await flow_error(dut, -20_000.0, False)


@cocotb.test()
async def rx_reset_traffic(dut):
    """L1: resets with traffic in flight at a seeded offset: rst_i (the
    whole PCS, the RX domain and the elastic buffer restart; lock and data
    return at the next comma within the lock target) and then pipe_reset_ni
    (the clk_i side only: RxValid low and the HAS reset values while it is
    low, the stream resumes after it with the RD unknown, no error, no
    symbol out of order)."""
    rng = seeded(dut)
    ppm = seeded_ppm(rng, 0, 600)
    link = make_link(rng, tr.link_symbols(rng, "random", 120, arch.SKP_INTERVAL_MIN))
    rx = RxBench(dut, rng, ppm)
    await rx.start(link, max_cycles=len(link.syms) // 2 + 6000)
    rx.b.mac.set(tx_elec_idle=1)
    n = len(rx.chan.words)
    resets = []
    for frac, kind in ((1 / 3, "rst_i"), (2 / 3, "pipe_reset")):
        while len(rx.chan.times) < rx.chan.lead + int(n * frac):
            await rx.b.tick()
        t0 = rx.words[-1].t
        await rx.b.reset(kind, rng.randrange(8, 12) if kind == "rst_i" else rng.randrange(1, 4))
        await rx.b.power(P0)
        rx.b.mac.set(tx_elec_idle=1)
        resets.append((kind, t0, rx.words[-1].t))
    await rx.run_line()
    runs = rx.check(link, "reset_traffic", lead_skip=lambda t: True)
    assert len(runs) == 3, f"reset_traffic: {len(runs)} runs for two resets"
    # A run starts with the RD unknown: until a symbol settles it, RD- is
    # assumed (8B10B.md, Decoder), so that symbol may show a disparity error.
    first = {rd_settle_word(r) for r in runs[1:]}
    bad = [(w.t, w.status) for w in rx.words if w.valid and w.status in (4, 5, 6, 7)
           and not (w.t in first and w.status == 7)]
    assert not bad, f"reset_traffic: error or buffer status {bad[:4]}"
    check_skp_status(link.syms, runs, "reset_traffic")
    check_latency(rx, runs)
    # After rst_i the lock starts again at a comma; after pipe_reset_ni the
    # stream simply resumes (the RX domain kept its lock).
    check_lock_latency(rx, link, [runs[1]], "reset_traffic")
    assert runs[2].words[0].t > resets[1][1], "no RxValid drop at pipe_reset_ni"
    COV_EB.sample(event="reset_restart")
    COV_EB.sample(event="pipe_reset")
    COV_RX.sample(scenario="random")


# ------------------------------------------------------------------ L2

@cocotb.test()
async def eb_long_tlp(dut):
    """L2: forty maximum TLPs at -600 or +600 ppm, either interval limit."""
    rng = random.Random(regress_seed())
    await eb_link(dut, "tlp", 40, rng.choice((arch.SKP_INTERVAL_MIN, arch.SKP_INTERVAL_MAX)),
                  sign() * 600.0, "long_tlp")


@cocotb.test()
async def eb_long_random(dut):
    """L2: 1200 random packets at a seeded offset and interval."""
    rng = random.Random(regress_seed())
    await eb_link(dut, "random", 1200, rng.randrange(1180, 1539), rng.uniform(-600, 600),
                  "long_random")


@cocotb.test()
async def eb_long_stress(dut):
    """L2: sizes that strand corrections, or short bursts, at +-600 ppm."""
    rng = random.Random(regress_seed())
    kind = rng.choice(("stress", "short_burst"))
    await eb_link(dut, kind, 60, arch.SKP_INTERVAL_MAX, sign() * 600.0, "long_stress")


@cocotb.test()
async def eb_long_idle(dut):
    """L2: three 20000-symbol electrical-idle gaps at -600 or +600 ppm."""
    await eb_idle(dut, sign() * 600.0, 3, 20000, "long_idle")
