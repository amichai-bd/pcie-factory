"""pcie_pcs data-path tests (wiki/src/dv/pcs/DV_PLAN.md): PIPE <- channel
<- partner, PIPE -> channel -> PIPE, and loopback.

A partner stream (training sets, packets, SKP ordered sets) is encoded,
serialized and impaired by tb.pcs_channel (the architecture model's
channel: ppm, bit slips, single-bit errors, electrical idle, inversion) and
driven at the offset RX clock. The MAC side runs through tb.pcs_bench.Bench
(PIPE control still checked every cycle). The receive scoreboard,
models.pcie_pcs_rx_check, aligns every RxValid run with the sent stream:
no symbol lost, duplicated or reordered (SKP aside), wrong symbols only
where an error is reported, SKP changes reported on the COM's word. Each
test adds the checks of its impairment against the HAS targets.
"""

from __future__ import annotations

from collections import deque

import cocotb

from models.pcie_pcs_model import arch
from models.pcie_pcs_rx_check import (K_COM, CheckError, Stream, check_relocks, check_runs,
                                      check_skp_status)
from tb.pcs_bench import COV_CTRL, regress_seed, seeded
from tb.pcs_channel import build_link
from tb.pcs_rxbench import (COV_RX, CYCLE, RELOCK_DELAY, RxBench, check_clean,
                            check_lock_latency, flips, lock_com, seeded_ppm)

# ------------------------------------------------------------------ tests

async def clean_link(dut, mix: str, packets: int, tlp_bytes: int, interval: int,
                     scenario: str, ppm: float | None = None, polarity: bool = False):
    rng = seeded(dut)
    ppm = seeded_ppm(rng) if ppm is None else ppm
    link = build_link(rng, packets, mix, tlp_bytes, interval, polarity=polarity)
    rx = RxBench(dut, rng, ppm)
    await rx.start(link, polarity=int(polarity))
    await rx.run_line()
    check_clean(rx, link, scenario)
    COV_RX.sample(scenario=scenario)
    return rx


@cocotb.test()
async def rx_smoke(dut):
    """L0: a short partner stream at a small offset arrives intact."""
    await clean_link(dut, "dllp", 60, 0, arch.SKP_INTERVAL_MIN, "clean", ppm=100.0)


@cocotb.test()
async def rx_ppm(dut):
    """Random packet sizes at up to ±600 ppm with the shortest SKP interval:
    lossless, SKP added or removed and reported on the COM's word."""
    await clean_link(dut, "random", 60, 0, arch.SKP_INTERVAL_MIN, "random")


@cocotb.test()
async def rx_max_tlp(dut):
    """Maximum TLPs (4122 bytes) at ±600 ppm with the longest SKP interval:
    the worst drift between corrections, still no overflow or underflow."""
    rng = seeded(dut)
    await clean_link(dut, "tlp", 6, arch.TLP_BYTES_MAX, arch.SKP_INTERVAL_MAX, "max_tlp",
                     ppm=seeded_ppm(rng, 550, 600))


@cocotb.test()
async def rx_dllp(dut):
    """Back-to-back DLLPs at ±600 ppm."""
    await clean_link(dut, "dllp", 600, 0, arch.SKP_INTERVAL_MIN, "dllp")


@cocotb.test()
async def rx_polarity(dut):
    """An inverted lane with RxPolarity 1 from the start: data intact."""
    await clean_link(dut, "random", 30, 0, arch.SKP_INTERVAL_MIN, "polarity", polarity=True)


@cocotb.test()
async def rx_polarity_toggle(dut):
    """RxPolarity applies from the next word: on an inverted lane with
    RxPolarity 0 the data is wrong; once the MAC raises RxPolarity in
    cycle T, every word from T + 1 on (the PIPE register's cycle) decodes
    correctly, with no error, as the decoder restarts with RD unknown."""
    rng = seeded(dut)
    link = build_link(rng, 40, "random", 0, arch.SKP_INTERVAL_MIN, polarity=True)
    rx = RxBench(dut, rng, seeded_ppm(rng))
    await rx.start(link, polarity=0)
    rx.b.mac.set(tx_elec_idle=1)
    # Raise RxPolarity once the packets have run for 400 symbols.
    w_start = rx.chan.lead + 10 * (link.training + 400) // 20
    while len(rx.chan.times) < w_start + 20:
        await rx.b.tick()
    # The complement of a code is mostly another valid code, so the inverted
    # lane shows mainly as wrong bytes rather than errors.
    wrong = False
    try:
        runs = rx.check(link, "inverted")
        wrong = any(r.mismatches for r in runs) or not runs
    except CheckError:
        wrong = True
    assert wrong, "inverted lane with RxPolarity 0 decoded correctly"
    i0 = len(rx.words)            # the first cycle that sees RxPolarity 1
    rx.b.mac.set(rx_polarity=1)
    await rx.run_line()
    after = rx.words[i0 + 1:]
    bad = [w for w in after if w.valid and w.status in (4, 7)]
    assert not bad, f"{len(bad)} error words after RxPolarity, first at {bad[0].t} ps"
    runs = check_runs(link.syms, after, where="polarity_toggle", arrival=rx.arrival)
    assert sum(r.mismatches for r in runs) == 0
    COV_RX.sample(scenario="polarity_toggle")


@cocotb.test()
async def rx_errors(dut):
    """Isolated single-bit errors: each reported as RxStatus 4 or 7, none
    costs lock (one RxValid run), the data around them intact."""
    rng = seeded(dut)
    n = 8
    link = build_link(rng, 40, "tlp", 274, arch.SKP_INTERVAL_MIN, errors=n)
    rx = RxBench(dut, rng, seeded_ppm(rng))
    await rx.start(link)
    await rx.run_line()
    runs = rx.check(link, "errors",
                      flipped=flips(link))
    assert len(runs) == 1, f"errors: {len(runs)} runs, an isolated error cost lock"
    reports = sum(1 for w in rx.words if w.valid and w.status in (4, 7))
    assert reports >= len(link.line.errors), \
        f"errors: {reports} error words for {len(link.line.errors)} flipped bits"
    assert not any(w.status in (5, 6) for w in rx.words)
    assert check_relocks(rx.words, RELOCK_DELAY, "errors", rx.at_end) == 0
    COV_RX.sample(scenario="errors")


@cocotb.test()
async def rx_error_bursts(dut):
    """Bursts of two to four bit errors, 1 to 40 symbols apart: the relock
    requests, and the RxValid drops they cause, follow the RECEIVE.md rule
    (ERR_LIMIT 3, one forgiven per ERR_DECAY_WORDS 8 clean words, one
    request per lock) applied to the words seen on PIPE; every run delivers
    intact data and every relock resumes at a COM."""
    rng = seeded(dut)
    link = build_link(rng, 80, "tlp", 274, arch.SKP_INTERVAL_MIN, bursts=16)
    rx = RxBench(dut, rng, seeded_ppm(rng))
    await rx.start(link)
    await rx.run_line()
    runs = rx.check(link, "error_bursts",
                      flipped=flips(link))
    n = check_relocks(rx.words, RELOCK_DELAY, "error_bursts", rx.at_end)
    dut._log.info("error_bursts: %d bits, %d relock requests, %d runs",
                  len(link.line.errors), n, len(runs))
    assert len(runs) == n + 1
    check_lock_latency(rx, link, runs[1:], "error_bursts")
    COV_RX.sample(scenario="error_bursts")


@cocotb.test()
async def rx_close_errors(dut):
    """Two close bit errors (a few symbols apart) whose errored symbols span
    three PIPE words reach the relock limit, by design: lock drops, RxValid
    stays low until the next comma (up to one SKP interval), and the data
    resumes intact from that COM."""
    rng = seeded(dut)
    link = build_link(rng, 60, "tlp", 274, arch.SKP_INTERVAL_MIN, close_pairs=8,
                      pair_gap=rng.choice([2, 3, 4]))
    rx = RxBench(dut, rng, seeded_ppm(rng))
    await rx.start(link)
    await rx.run_line()
    runs = rx.check(link, "close_errors",
                      flipped=flips(link))
    check_relocks(rx.words, RELOCK_DELAY, "close_errors", rx.at_end)
    relocks = len(runs) - 1
    dut._log.info("close_errors: %d pairs, %d relocks", len(link.close_errors), relocks)
    assert link.close_errors, "no relocking pair found in the stream"
    assert relocks == len(link.close_errors), \
        f"{relocks} relocks for {len(link.close_errors)} pairs that reach the limit"
    for prev, r in zip(runs, runs[1:]):
        # The new lock starts at the next COM after the error.
        com = lock_com(link.syms, r)
        gap = com - prev.last
        assert gap <= arch.SKP_INTERVAL_MIN + 274 + 20, f"RxValid low over {gap} symbols"
        COV_RX.sample(relock="close_errors")
    check_lock_latency(rx, link, runs[1:], "close_errors")
    COV_RX.sample(scenario="close_errors")


@cocotb.test()
async def rx_slips(dut):
    """Bit slips in packet traffic: each is caught by the relock rule (lock
    drops within the HAS 32 cycles plus the receive latency), lock is
    regained at the next comma, and every run delivers intact data."""
    rng = seeded(dut)
    link = build_link(rng, 60, "tlp", 274, arch.SKP_INTERVAL_MIN, slips=4)
    rx = RxBench(dut, rng, seeded_ppm(rng, 0, 300))
    await rx.start(link)
    await rx.run_line()
    slip_t = [rx.chan.time_of_bit(b) for b in link.line.slips]

    def slip_possible(t0, t):
        # A slip inside this run: after its start (less the RX latency) and
        # before the symbol that lost the stream.
        return any(t0 - 12 * CYCLE <= s <= t for s in slip_t)

    runs = rx.check(link, "slips", slip_possible=slip_possible)
    falls = [r.words[-1].t for r in runs if not rx.at_end(rx.words.index(r.words[-1]) + 1)]
    # A slip that arrives while the aligner already hunts (lock dropped by
    # an earlier slip, not yet retaken) needs no detection, as in the arch
    # model; a slip is in lock when PIPE is valid as its bits arrive there.
    in_lock = [t for t in slip_t
               if any(r.words[0].t <= t + 12 * CYCLE <= r.words[-1].t for r in runs)]
    for s in in_lock:
        nxt = [f for f in falls if f >= s]
        assert nxt, f"slip at {s} ps never dropped lock"
        detect = (nxt[0] - s) / CYCLE
        assert detect <= 32 + 16, f"slip at {s} ps: lock dropped {detect:.1f} cycles later"
        COV_RX.sample(relock="slip")
    dut._log.info("slips: %d injected, %d in lock, %d runs", len(slip_t), len(in_lock), len(runs))
    assert in_lock, "no slip arrived in lock"
    # Lock returns after the last slip if a COM follows it well after the
    # relock (64 symbols); a COM in the line's last 8 symbols may or may not
    # give lock before the line goes idle.
    last = link.line.slips[-1] // 10
    late = [i for i, x in enumerate(link.syms) if x == (1, K_COM) and i >= last + 64]
    if any(i < len(link.syms) - 8 for i in late):
        want = {len(in_lock) + 1}
    elif late:
        want = {len(in_lock), len(in_lock) + 1}
    else:
        want = {len(in_lock)}
    assert len(runs) in want, f"{len(runs)} runs for {len(in_lock)} slips in lock"
    check_lock_latency(rx, link, [r for r in runs[1:] if not r.lost], "slips")
    COV_RX.sample(scenario="slips")


@cocotb.test()
async def rx_elec_idle_gaps(dut):
    """Electrical-idle gaps of noise between packets: RxValid low while
    idle, no buffer overflow or underflow, lock regained on the first FTS
    comma within the HAS 10 cycles, data intact after every gap."""
    rng = seeded(dut)
    link = build_link(rng, 30, "random", 0, arch.SKP_INTERVAL_MIN, idle_gaps=3,
                      idle_symbols=rng.randrange(400, 3000))
    rx = RxBench(dut, rng, seeded_ppm(rng))
    await rx.start(link)
    await rx.run_line()
    runs = rx.check(link, "elec_idle", lead_skip=lambda t: True)
    assert len(runs) == len(link.idles) + 1, f"{len(runs)} runs for {len(link.idles)} gaps"
    # Errors are expected only where a lock starts on noise: the first word
    # of a lock may hold the symbol before the comma, and a lock taken on a
    # comma in the idle-exit noise gives error words until the relock in the
    # FTS sets (the same cycle, so RxValid stays high).
    allowed = set()
    for r in runs:
        allowed.add(r.words[0].t)
        if r.noise_lock:
            clean_from = r.placed[0][0] if r.placed else r.words[-1].t
            allowed.update(w.t for w in r.words if w.t < clean_from)
    bad = [(w.t, w.status) for w in rx.words if w.status in (5, 6)
           or w.valid and w.status in (4, 7) and w.t not in allowed]
    assert not bad, f"error or buffer status after idle: {bad[:6]}"
    noise = [r for r in runs if r.noise_lock]
    for r in noise:
        COV_RX.sample(relock="noise_lock")
    check_lock_latency(rx, link, runs[1:], "elec_idle")
    COV_RX.sample(scenario="elec_idle")


@cocotb.test()
async def loopback(dut):
    """Loopback: the received words, as they leave on PIPE, are re-encoded
    two cycles later at the transmit RD, code errors as EDB; the receive
    side keeps presenting them."""
    rng = seeded(dut)
    link = build_link(rng, 30, "random", 0, arch.SKP_INTERVAL_MIN, errors=3)
    rx = RxBench(dut, rng, seeded_ppm(rng))
    await rx.start(link)
    rx.b.mac.set(tx_elec_idle=0, tx_detect_rx=1)
    hist: list[dict] = []
    rds = {-1, 1}
    checked = 0
    while not rx.chan.done:
        out = await rx.b.tick()
        hist.append(out)
        if len(hist) < 3:
            continue
        src = hist[-3]
        assert not out["ser_tx_elec_idle_o"], "line idle in loopback"
        k, d = src["pipe_rx_datak_o"], src["pipe_rx_data_o"]
        syms = [(k & 1, d & 0xFF), ((k >> 1) & 1, d >> 8)]
        after = set()
        for rd in rds:
            codes, rd2 = arch.tx_encode(syms, None, rd)
            if codes[0] | (codes[1] << 10) == out["ser_tx_data_o"]:
                after.add(rd2)
        assert after, (f"cycle {rx.b.mac.cycle}: looped word {syms} sent as "
                       f"{out['ser_tx_data_o']:05x}")
        rds = after
        checked += src["pipe_rx_valid_o"]
    rx.b.mac.set(tx_detect_rx=0, tx_elec_idle=1)
    await rx.b.run(50)
    rx.check(link, "loopback",
                      flipped=flips(link))
    assert checked > 500
    COV_CTRL.sample(tx="loopback")
    COV_RX.sample(scenario="loopback")


@cocotb.test()
async def round_trip(dut):
    """PIPE -> encoder -> line -> aligner, elastic buffer, decoder -> PIPE:
    the MAC sends training sets, packets and SKP ordered sets on TxData and
    receives them intact on RxData (both ends on clk_i's frequency)."""
    rng = seeded(dut)
    syms, _ = arch.make_stream(rng, 40, "random", 0, arch.SKP_INTERVAL_MIN, 0, 0, lead_ts=32)
    if len(syms) % 2:
        syms.append((1, 0x1C))
    queue: deque = deque()
    rx = RxBench(dut, rng, 0.0)
    await rx.start(source=queue)
    b = rx.b
    for i in range(0, len(syms), 2):
        (k0, b0), (k1, b1) = syms[i], syms[i + 1]
        b.mac.set(tx_elec_idle=0, tx_data=b0 | (b1 << 8), tx_datak=k0 | (k1 << 1))
        out = await b.tick()
        if not out["ser_tx_elec_idle_o"]:
            queue.append(out["ser_tx_data_o"])
    b.mac.set(tx_elec_idle=1)
    for _ in range(2):
        out = await b.tick()
        if not out["ser_tx_elec_idle_o"]:
            queue.append(out["ser_tx_data_o"])
    await b.run(200)
    runs = check_runs(syms, rx.words, where="round_trip")
    assert len(runs) == 1 and runs[0].mismatches == 0
    stream = Stream(syms)
    assert runs[0].last >= len(stream.syms) - 2, "round trip lost the end of the stream"
    assert not any(w.status in (4, 5, 6, 7) for w in rx.words if w.valid)
    check_skp_status(syms, runs, "round_trip")
    COV_RX.sample(scenario="round_trip")


@cocotb.test()
async def eb_stress(dut):
    """Outside the targets on purpose: a clock offset of 4000 ppm with
    long packets overruns the SKP corrections. A fast RX clock overflows
    (RxStatus 5 on the first word after the loss); a slow one underflows
    (RxStatus 6 with EDB, EDB as K symbols and RxValid 0)."""
    rng = seeded(dut)
    ppm = (1 if regress_seed() % 2 else -1) * 4000.0
    link = build_link(rng, 8, "tlp", 2000, arch.SKP_INTERVAL_MAX, training=8)
    rx = RxBench(dut, rng, ppm)
    await rx.start(link)
    await rx.run_line()
    st = rx.statuses()
    if ppm > 0:
        assert st.get(5, 0) > 0, f"no overflow at {ppm} ppm: {st}"
    else:
        udf = [w for w in rx.words if w.status == 6]
        assert udf, f"no underflow at {ppm} ppm: {st}"
        for w in udf:
            assert not w.valid and w.syms == ((1, 0xFE), (1, 0xFE)), \
                f"underflow word at {w.t} ps: valid {w.valid}, {w.syms}"
    COV_RX.sample(scenario="eb_stress")
