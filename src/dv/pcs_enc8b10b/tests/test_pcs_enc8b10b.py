"""pcie_pcs_enc8b10b tests (wiki/src/dv/pcs_enc8b10b/DV_PLAN.md).

Inputs are driven and outputs sampled at the falling clk_i edge; the RTL
updates on the rising edge. Every cycle, valid_o (and data_o while valid_o
is 1) is compared with the cycle model, which encodes each word with the
pcs architecture model's tx_encode (the Python reference).
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from models import pcie_pcs_enc8b10b_model as m
from regress.funcov import Covergroup, groups

D21_5 = 0xB5   # balanced in both columns: keeps RD
K28_5 = 0xBC   # COM: six ones at RD-, so RD- becomes RD+

# The valid symbols as (k, byte) tuple bins: 256 data and 12 K codes.
SYMBOLS = {f"D{b & 31}.{b >> 5}": (0, b) for b in range(256)}
SYMBOLS.update({f"K{b & 31}.{b >> 5}": (1, b) for b in m.VALID_K})

# Every valid symbol at each RD in each lane (DV plan, codes).
CODES = Covergroup("pcs_enc8b10b_codes")
CODES.coverpoint("symbol", bins=SYMBOLS)
CODES.coverpoint("rd", bins={"neg": -1, "pos": 1})
CODES.coverpoint("lane", bins={"lane0": 0, "lane1": 1})
CODES.cross("symbol_x_rd_x_lane", "symbol", "rd", "lane")

# Controls and RD carry (DV plan, controls).
CTRL = Covergroup("pcs_enc8b10b_controls")
CTRL.coverpoint("rst", bins={"low": 0, "high": 1})
CTRL.coverpoint("valid", bins={"idle": 0, "word": 1})
CTRL.coverpoint("compliance", bins={"off": 0, "on": 1})
CTRL.coverpoint("rd_word", bins={"neg": -1, "pos": 1})      # RD held before the word
CTRL.coverpoint("rd_mid", bins={"neg": -1, "pos": 1})       # RD between the lanes
CTRL.coverpoint("edb_lane", bins={"lane0": 0, "lane1": 1})  # unsupported K sent as EDB
CTRL.cross("compliance_x_rd_word", "compliance", "rd_word")
CTRL.cross("rd_word_x_rd_mid", "rd_word", "rd_mid")
IDLE = Covergroup("pcs_enc8b10b_idle")
IDLE.coverpoint("rd_held", bins={"neg": -1, "pos": 1})      # RD kept across an idle cycle


async def start(dut):
    """Start the clock and hold reset for three edges; return at a falling edge."""
    cocotb.start_soon(Clock(dut.clk_i, 8, unit="ns").start())
    dut.rst_i.value = 1
    dut.valid_i.value = 0
    dut.data_i.value = 0
    dut.datak_i.value = 0
    dut.compliance_i.value = 0
    for _ in range(3):
        await FallingEdge(dut.clk_i)
        assert int(dut.valid_o.value) == 0, "valid_o is 1 in reset"


def sample_word(model, data, datak, compliance):
    """Coverage of the word the model just encoded (checked right after)."""
    rd_word = -1 if compliance else model.rd_before
    _, rd_mid, _ = m.encode_word(model.rd_before, data, datak, compliance)
    CTRL.sample(compliance=compliance, rd_word=model.rd_before, rd_mid=rd_mid)
    for lane, rd in ((0, rd_word), (1, rd_mid)):
        k, b = (datak >> lane) & 1, (data >> (8 * lane)) & 0xFF
        if k and b not in m.VALID_K:
            CTRL.sample(edb_lane=lane)
        else:
            CODES.sample(symbol=(k, b), rd=rd, lane=lane)


class Bench:
    """Drives one word per cycle and scoreboards every cycle."""

    def __init__(self, dut):
        self.dut = dut
        self.model = m.Encoder()
        self.cycle = 0
        self.words = 0
        self.pending = []   # stage-1 words, for coverage once encoded

    def check(self):
        dut, model = self.dut, self.model
        got_v = int(dut.valid_o.value)
        assert got_v == model.valid_o, (
            f"cycle {self.cycle}: valid_o expected {model.valid_o} got {got_v}")
        if got_v:
            got = int(dut.data_o.value)
            assert got == model.data_o, (
                f"cycle {self.cycle}: data_o expected {model.data_o:#07x} got {got:#07x} "
                f"(word {self.pending_word}, RD before {model.rd_before:+d})")
            self.words += 1
            sample_word(model, *self.pending_word)

    async def step(self, rst=0, valid=1, data=0, datak=0, compliance=0):
        """Drive one cycle, then check the outputs after the edge."""
        dut, model = self.dut, self.model
        dut.rst_i.value = rst
        dut.valid_i.value = valid
        dut.data_i.value = data
        dut.datak_i.value = datak
        dut.compliance_i.value = compliance
        CTRL.sample(rst=rst, valid=valid)
        if not rst and not model.s1[0]:
            IDLE.sample(rd_held=model.rd)
        model.rd_before = model.rd
        self.pending_word = model.s1[1:]
        model.clock(rst, valid, data, datak, compliance)
        await FallingEdge(dut.clk_i)
        self.cycle += 1
        self.check()

    async def drain(self):
        for _ in range(m.LATENCY):
            await self.step(valid=0)


def coverage_holes(group, item):
    return [b for b, n in groups()[group][item].items() if n == 0]


@cocotb.test()
async def smoke(dut):
    """L0: reset, latency, published anchors, COM run, compliance, EDB, idle hold."""
    await start(dut)
    bench = Bench(dut)
    await bench.step(rst=1)
    await bench.step(valid=1, data=(0x00 << 8) | K28_5, datak=0b01)     # K28.5 D0.0 from RD-
    assert int(dut.valid_o.value) == 0, "data_o after one cycle (latency must be 2)"
    await bench.step(valid=0)
    want = m.encode_word(-1, K28_5, 0b01, 0)[0]
    assert int(dut.valid_o.value) == 1 and int(dut.data_o.value) == want
    # K28.5 at RD- (line 0011111010) from the 8b/10b page
    assert int(dut.data_o.value) & 0x3FF == int("0011111010"[::-1], 2)
    await bench.step(valid=0, data=K28_5, datak=0b01)   # idle: RD (+) holds, data ignored
    await bench.step(valid=1, data=(K28_5 << 8) | K28_5, datak=0b11)
    await bench.step(valid=1, data=(0x4A << 8) | D21_5, datak=0b00, compliance=1)
    await bench.step(valid=1, data=(0x3D << 8) | 0x99, datak=0b11)        # two unsupported K
    await bench.drain()
    await bench.step(rst=1, valid=1, data=K28_5, datak=0b01)              # reset drops it
    await bench.drain()
    assert bench.words == 4, f"expected 4 words out, saw {bench.words}"


def exhaustive_words():
    """For each symbol (data byte, or K flag on any byte), each lane and
    each starting RD: words that put that RD before that lane. Compliance
    sets RD- before lane 0; D21.5 keeps RD and K28.5 turns RD- into RD+."""
    words = []
    for k in (0, 1):
        for b in range(256):
            words.append(((D21_5 << 8) | b, k, 1))                  # lane 0, RD-
            words.append(((K28_5 << 8) | D21_5, 0b10, 1))           # ends at RD+
            words.append(((D21_5 << 8) | b, k, 0))                  # lane 0, RD+
            words.append(((b << 8) | D21_5, k << 1, 1))             # lane 1, RD-
            words.append(((b << 8) | K28_5, (k << 1) | 1, 1))       # lane 1, RD+
    return words


@cocotb.test()
async def exhaustive(dut):
    """L1: every data byte and every K-flagged byte (valid K codes, and the
    others as EDB) at both RD values in both lanes, back to back; then the
    same with an idle cycle between words. Fails on any coverage hole."""
    await start(dut)
    bench = Bench(dut)
    await bench.step(rst=1)
    words = exhaustive_words()
    for data, datak, comp in words:
        await bench.step(valid=1, data=data, datak=datak, compliance=comp)
    await bench.drain()
    for data, datak, comp in words:
        await bench.step(valid=1, data=data, datak=datak, compliance=comp)
        await bench.step(valid=0, data=K28_5, datak=0b01)  # ignored: RD holds
    await bench.drain()
    assert bench.words == 2 * len(words), f"{bench.words} words out of {2 * len(words)}"
    holes = coverage_holes("pcs_enc8b10b_codes", "symbol_x_rd_x_lane")
    assert not holes, f"{len(holes)} symbol/RD/lane combinations not encoded: {holes[:8]}"
    holes = coverage_holes("pcs_enc8b10b_controls", "edb_lane")
    assert not holes, f"EDB substitution not seen in {holes}"


async def random_run(dut, cycles, p_idle, p_comp, p_rst):
    """Random words: mostly data, K bytes from the valid set and some
    unsupported, idle gaps, compliance and resets, against the model."""
    dut._log.info("random seed %d, %d cycles", cocotb.RANDOM_SEED, cycles)
    bench = Bench(dut)
    await bench.step(rst=1)
    counts = {"idle": 0, "compliance": 0, "rst": 0, "k": 0}
    for _ in range(cycles):
        rst = int(random.random() < p_rst)
        valid = int(random.random() >= p_idle)
        comp = int(random.random() < p_comp)
        data = datak = 0
        for lane in range(2):
            r = random.random()
            if r < 0.15:
                byte, k = random.choice(m.VALID_K), 1
            elif r < 0.18:
                byte, k = random.getrandbits(8), 1
            else:
                byte, k = random.getrandbits(8), 0
            data |= byte << (8 * lane)
            datak |= k << lane
            counts["k"] += k
        counts["idle"] += 1 - valid
        counts["compliance"] += comp
        counts["rst"] += rst
        await bench.step(rst=rst, valid=valid, data=data, datak=datak, compliance=comp)
    await bench.drain()
    dut._log.info("words checked %d, controls %s", bench.words, counts)
    assert all(counts.values()) and bench.words > cycles // 2, (
        f"vacuous run: {bench.words} words, controls {counts}")


@cocotb.test()
async def random_vs_model(dut):
    """L1: random traffic against the model."""
    await start(dut)
    await random_run(dut, 5000, p_idle=0.1, p_comp=0.05, p_rst=0.002)


@cocotb.test()
async def long_random(dut):
    """L2: long random traffic against the model."""
    await start(dut)
    await random_run(dut, 100000, p_idle=0.1, p_comp=0.02, p_rst=0.0002)
