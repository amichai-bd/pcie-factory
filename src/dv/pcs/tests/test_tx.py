"""pcie_pcs transmit tests (wiki/src/dv/pcs/DV_PLAN.md): the encoder
block's checks through PIPE TX and ser_tx_data_o, and every symbol through
PIPE TX, the line and PIPE RX.

tb.pcs_bench.Bench drives the MAC side and checks every cycle: PhyStatus
and the transmitter's electrical idle against the PIPE-control reference,
and every word on ser_tx_data_o, two cycles after the PIPE word, against
the architecture model's encoder (running disparity, TxCompliance,
unsupported K as EDB, RD held across TxElecIdle, RD negative after reset).
It samples the encoder coverage (pcs_tx_codes, pcs_tx_controls).
"""

from __future__ import annotations

from collections import deque

import cocotb

from models.pcie_pcs_model import P0, arch
from models.pcie_pcs_rx_check import check_runs
from regress.funcov import Covergroup, groups
from tb.pcs_bench import COV_CTRL, VALID_K, Bench, seeded, start
from tb.pcs_rxbench import COV_RX, RxBench

D21_5 = 0xB5   # balanced in both columns: keeps RD
K28_5 = 0xBC   # COM: six ones at RD-, so RD- becomes RD+

# Every valid symbol received at each RD in each PIPE RX lane, after the
# round trip through the encoder, the line, the aligner, the elastic buffer
# and the decoder.
SYMBOLS = {f"D{b & 31}.{b >> 5}": (0, b) for b in range(256)}
SYMBOLS.update({f"K{b & 31}.{b >> 5}": (1, b) for b in VALID_K})
COV_RX_CODES = Covergroup("pcs_rx_codes")
COV_RX_CODES.coverpoint("symbol", bins=SYMBOLS)
COV_RX_CODES.coverpoint("rd", bins={"neg": -1, "pos": 1})
COV_RX_CODES.coverpoint("lane", bins={"lane0": 0, "lane1": 1})
COV_RX_CODES.cross("symbol_x_rd_x_lane", "symbol", "rd", "lane")


def holes(group: str, item: str) -> list[str]:
    return [b for b, n in groups()[group][item].items() if n == 0]


async def p0_bench(dut, max_cycles: int) -> Bench:
    b = await start(dut, max_cycles=max_cycles)
    await b.power(P0)
    return b


async def send(b: Bench, data: int, datak: int, comp: int = 0) -> dict:
    return (await b.run(1, tx_elec_idle=0, tx_detect_rx=0, tx_data=data, tx_datak=datak,
                        tx_compliance=comp))[0]


async def idle(b: Bench, n: int = 1) -> list[dict]:
    return await b.run(n, tx_elec_idle=1, tx_compliance=0)


def line(code: int) -> str:
    return "".join(str((code >> i) & 1) for i in range(10))


# ------------------------------------------------------------------ L0

@cocotb.test()
async def tx_smoke(dut):
    """L0: latency 2, the published K28.5 code at RD-, a COM run, TxCompliance,
    unsupported K as EDB, RD held across TxElecIdle, and a reset that drops
    the words in flight (pipe_reset_ni) and restarts at RD-."""
    b = await p0_bench(dut, 2000)
    await idle(b, 2)
    await send(b, (0x00 << 8) | K28_5, 0b01)            # K28.5 D0.0 from RD-
    out = (await idle(b))[0]
    assert out["ser_tx_elec_idle_o"], "a word on the line one cycle after PIPE (latency 2)"
    out = (await idle(b))[0]
    assert not out["ser_tx_elec_idle_o"]
    assert line(out["ser_tx_data_o"] & 0x3FF) == "0011111010", "K28.5 at RD- is not 0011111010"
    await send(b, (K28_5 << 8) | K28_5, 0b11)
    await send(b, (0x4A << 8) | D21_5, 0b00, comp=1)
    await send(b, (0x3D << 8) | 0x99, 0b11)              # two unsupported K: EDB, EDB
    await idle(b, 3)
    await send(b, (K28_5 << 8) | 0x00, 0b10)
    await send(b, K28_5, 0b01)
    # pipe_reset_ni with words in flight: they are dropped, RD restarts at RD-.
    b.mac.held_in_reset()
    await b.run(2, reset_n=0)
    b.mac.set(reset_n=1)
    while (await b.tick())["pipe_phy_status_o"]:
        pass
    await b.power(P0)
    await send(b, (D21_5 << 8) | K28_5, 0b01)
    await idle(b, 3)
    # Seven words sent, the two in flight at the reset dropped.
    assert b.checked_words == 5, f"{b.checked_words} words checked, expected 5"


# ------------------------------------------------------------------ L1

def table_words() -> list[tuple[int, int, int]]:
    """For each byte as data and as a K byte (valid K codes, and the others
    as EDB), each lane and each starting RD: words that put that RD before
    that lane. TxCompliance sets RD- before lane 0; D21.5 keeps RD and
    K28.5 turns RD- into RD+."""
    words = []
    for k in (0, 1):
        for v in range(256):
            words.append(((D21_5 << 8) | v, k, 1))                  # lane 0, RD-
            words.append(((K28_5 << 8) | D21_5, 0b10, 1))           # ends at RD+
            words.append(((D21_5 << 8) | v, k, 0))                  # lane 0, RD+
            words.append(((v << 8) | D21_5, k << 1, 1))             # lane 1, RD-
            words.append(((v << 8) | K28_5, (k << 1) | 1, 1))       # lane 1, RD+
    return words


@cocotb.test()
async def tx_table(dut):
    """L1: every data byte and every K-flagged byte at both RD values in both
    lanes, back to back, then again with an idle cycle (TxElecIdle) after
    every word: each word on ser_tx_data_o equals the architecture model's
    code. Fails on any hole in the symbol x RD x lane cross or the EDB lanes."""
    words = table_words()
    b = await p0_bench(dut, 4 * len(words) + 400)
    for data, datak, comp in words:
        await send(b, data, datak, comp)
    await idle(b, 2)
    for data, datak, comp in words:
        await send(b, data, datak, comp)
        await idle(b)
    await idle(b, 2)
    assert b.checked_words == 2 * len(words), f"{b.checked_words} of {2 * len(words)} words"
    h = holes("pcs_tx_codes", "symbol_x_rd_x_lane")
    assert not h, f"{len(h)} symbol/RD/lane combinations not encoded: {h[:8]}"
    h = holes("pcs_tx_controls", "edb_lane") + holes("pcs_tx_controls", "rd_held")
    assert not h, f"EDB substitution or RD hold not seen in {h}"


async def tx_random_run(dut, cycles: int) -> None:
    """Random PIPE transmit traffic: data, valid and unsupported K bytes,
    TxCompliance, TxElecIdle gaps and resets of both kinds with words in
    flight, every word against the model; fails when a control never
    happened."""
    rng = seeded(dut)
    b = await p0_bench(dut, cycles + 3000)
    seen = {"idle": 0, "compliance": 0, "k": 0, "k_invalid": 0, "rst_i": 0, "pipe_reset": 0}
    for _ in range(cycles):
        r = rng.random()
        if r < 0.002:
            kind = rng.choice(("rst_i", "pipe_reset"))
            await b.reset(kind, rng.randrange(8, 12) if kind == "rst_i" else rng.randrange(1, 4))
            await b.power(P0)
            seen[kind] += 1
            continue
        if r < 0.1:
            await idle(b)
            seen["idle"] += 1
            continue
        data = datak = 0
        for lane in range(2):
            q = rng.random()
            if q < 0.15:
                v, k = rng.choice(VALID_K), 1
                seen["k"] += 1
            elif q < 0.18:
                v, k = rng.choice([x for x in range(256) if x not in VALID_K]), 1
                seen["k_invalid"] += 1
                COV_CTRL.sample(tx="k_invalid")
            else:
                v, k = rng.randrange(256), 0
            data |= v << (8 * lane)
            datak |= k << lane
        comp = int(rng.random() < 0.05)
        seen["compliance"] += comp
        await send(b, data, datak, comp)
    await idle(b, 3)
    dut._log.info("%d words checked, controls %s", b.checked_words, seen)
    assert all(seen.values()) and b.checked_words > cycles // 2, (
        f"vacuous run: {b.checked_words} words, controls {seen}")


@cocotb.test()
async def tx_random(dut):
    """L1: 5000 cycles of random transmit traffic."""
    await tx_random_run(dut, 5000)


@cocotb.test()
async def tx_random_long(dut):
    """L2: 50000 cycles of random transmit traffic."""
    await tx_random_run(dut, 50000)


def rd_setter(rd_now: int, rd_want: int) -> int:
    """A data byte that, sent at RD ``rd_now``, leaves the RD at ``rd_want``."""
    for v in range(256):
        if arch.tx_encode([(0, v)], None, rd_now)[1] == rd_want:
            return v
    raise ValueError("no setter")  # pragma: no cover


def table_stream() -> list[tuple[int, int]]:
    """TS1 sets for lock, then every symbol (data, valid K and, as EDB,
    every unsupported K byte) at RD- and RD+ in each half of a word, each
    test word followed by a D21.5 pair, so no COM is ever followed by a
    SKP; no TxCompliance, so the receiver sees a valid code stream. The
    symbols at odd positions of the stream sit in lane 1 of a PIPE word."""
    ts1 = ([(1, 0xBC), (1, 0xF7), (1, 0xF7), (0, 0x80), (0, 0x02), (0, 0x00)]
           + [(0, 0x4A)] * 10)
    syms = ts1 * 16
    rd = arch.tx_encode(syms)[1]
    pad = [(0, D21_5), (0, D21_5)]
    for lane in (0, 1):
        for want in (-1, 1):
            for k in (0, 1):
                for v in range(256):
                    if lane == 0:
                        word = [(0, D21_5), (0, rd_setter(rd, want)), (k, v), (0, D21_5)]
                    else:
                        word = [(0, rd_setter(rd, want)), (k, v)]
                    syms += word + pad
                    rd = arch.tx_encode(word + pad, None, rd)[1]
    return syms + ts1 * 2


@cocotb.test()
async def table_round_trip(dut):
    """L1: PIPE TX -> encoder -> line -> aligner, elastic buffer, decoder ->
    PIPE RX for every symbol at both RDs in both lanes: one lock, every
    symbol back intact (unsupported K bytes as EDB), no error status. Fails
    on any hole in the received symbol x RD x lane cross."""
    rng = seeded(dut)
    syms = table_stream()
    expect = [(k, v if not k or v in VALID_K else arch.code8b10b.K_EDB) for k, v in syms]
    queue: deque = deque()
    rx = RxBench(dut, rng, 0.0)
    await rx.start(source=queue, max_cycles=len(syms) // 2 + 2000)
    b = rx.b
    for i in range(0, len(syms), 2):
        (k0, b0), (k1, b1) = syms[i], syms[i + 1]
        out = await send(b, b0 | (b1 << 8), k0 | (k1 << 1))
        if not out["ser_tx_elec_idle_o"]:
            queue.append(out["ser_tx_data_o"])
    for _ in range(2):
        out = (await idle(b))[0]
        if not out["ser_tx_elec_idle_o"]:
            queue.append(out["ser_tx_data_o"])
    await b.run(200)
    runs = check_runs(expect, rx.words, where="table_round_trip")
    assert len(runs) == 1 and runs[0].mismatches == 0, \
        f"{len(runs)} runs, {sum(r.mismatches for r in runs)} wrong symbols"
    assert not any(w.status in (4, 5, 6, 7) for w in rx.words if w.valid)
    # The RD before every sent symbol, and its received lane.
    rd_before, rd = [], -1
    for k, v in syms:
        rd_before.append(rd)
        rd = arch.tx_encode([(k, v)], None, rd)[1]
    # A SKP (never after a COM here) is the symbol after the one before it.
    r = runs[0]
    j, prev = -1, None
    for w in r.words:
        for lane, s in enumerate(w.syms):
            if s == (1, 0x1C):
                p = None if prev is None else prev + 1
            else:
                j += 1
                p = r.sent_of.get(j)
            prev = p
            if p is not None and expect[p] == s and s in SYMBOLS.values():
                COV_RX_CODES.sample(symbol=s, rd=rd_before[p], lane=lane)
    h = holes("pcs_rx_codes", "symbol_x_rd_x_lane")
    assert not h, f"{len(h)} symbol/RD/lane combinations not received: {h[:8]}"
    COV_RX.sample(scenario="round_trip")
