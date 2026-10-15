"""pcie_mac_scrambler tests (wiki/src/dv/mac_scrambler/DV_PLAN.md).

The DUT is driven directly. Inputs are driven and outputs sampled at the
falling edge of clk_i; the RTL registers its output at the rising edge, so
each check compares the outputs with the cycle model after the previous
edge. The reference is models/pcie_mac_scrambler_model.py, which runs every
symbol through the mac architecture model's Scrambler.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from models.pcie_mac_scrambler_model import WordScrambler
from regress.funcov import Covergroup

# Known answer (wiki/src/arch/mac/SYMBOLS.md#scrambling): key bytes after a
# reset, that is 00h data scrambled.
KAT = [0xFF, 0x17, 0xC0, 0x14, 0xB2, 0xE7, 0x02, 0x82,
       0x72, 0x6E, 0x28, 0xA6, 0xBE, 0x6D, 0xBF, 0x8D]

COM, SKP = 0xBC, 0x1C
# The other K symbols of SYMBOLS.md: STP, SDP, END, EDB, PAD, FTS, IDL.
K_OTHER = [0xFB, 0x5C, 0xFD, 0xFE, 0xF7, 0x3C, 0x7C]

KINDS = ["com", "skp", "k_other", "data_scr", "data_raw"]

# Functional coverage (DV plan, Strategy). Symbol kinds per lane, with the
# data bytes that equal COM or SKP as kinds of their own; the kinds of the
# two symbols of one word crossed (the LFSR passes from [7:0] to [15:8]);
# and the controls.
COV_LANE = Covergroup("mac_scrambler_lanes")
COV_LANE.coverpoint("lane", bins={"lo": 0, "hi": 1})
COV_LANE.coverpoint("kind", bins={k: k for k in KINDS + ["data_com_byte", "data_skp_byte"]})
COV_LANE.cross("lane_x_kind", "lane", "kind")

COV_PAIR = Covergroup("mac_scrambler_pairs")
COV_PAIR.coverpoint("kind_lo", bins={k: k for k in KINDS})
COV_PAIR.coverpoint("kind_hi", bins={k: k for k in KINDS})
COV_PAIR.cross("lo_x_hi", "kind_lo", "kind_hi")

COV_CTL = Covergroup("mac_scrambler_controls")
COV_CTL.coverpoint("rst", bins={"low": 0, "high": 1})
COV_CTL.coverpoint("valid", bins={"low": 0, "high": 1})
COV_CTL.cross("rst_x_valid", "rst", "valid")


def kind(k, b, scr, lookalike=False):
    if k:
        return "com" if b == COM else "skp" if b == SKP else "k_other"
    if lookalike and b in (COM, SKP):
        return "data_com_byte" if b == COM else "data_skp_byte"
    return "data_scr" if scr else "data_raw"


def sample(rst, valid, data, datak, scr):
    COV_CTL.sample(rst=rst, valid=valid)
    if rst or not valid:
        return
    kinds = []
    for i in range(2):
        k, b, s = (datak >> i) & 1, (data >> (8 * i)) & 0xFF, (scr >> i) & 1
        COV_LANE.sample(lane=i, kind=kind(k, b, s, lookalike=True))
        kinds.append(kind(k, b, s))
    COV_PAIR.sample(kind_lo=kinds[0], kind_hi=kinds[1])


class Bench:
    """Drives the DUT and checks it against the cycle model every cycle."""

    def __init__(self, dut):
        self.dut = dut
        self.model = WordScrambler()
        self.cycle = 0

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk_i, 8, unit="ns").start())
        await self.step(rst=1, check=False)
        await self.step(rst=1)

    def check(self):
        dut, mdl = self.dut, self.model
        got_v = int(dut.valid_o.value)
        assert got_v == mdl.valid, f"cycle {self.cycle}: valid_o expected {mdl.valid} got {got_v}"
        if mdl.valid:
            got_d, got_k = int(dut.data_o.value), int(dut.datak_o.value)
            assert (got_d, got_k) == (mdl.data, mdl.datak), (
                f"cycle {self.cycle}: data_o/datak_o expected {mdl.data:#06x}/{mdl.datak:#03b} "
                f"got {got_d:#06x}/{got_k:#03b}")

    async def step(self, rst=0, valid=0, data=0, datak=0, scr=0, check=True):
        """Check the outputs, drive one cycle, return at the next falling
        edge with the outputs of that cycle; returns them as (valid, data)."""
        if check:
            self.check()
        dut = self.dut
        dut.rst_i.value = rst
        dut.valid_i.value = valid
        dut.data_i.value = data
        dut.datak_i.value = datak
        dut.scr_i.value = scr
        sample(rst, valid, data, datak, scr)
        self.model.clock(rst, valid, data, datak, scr)
        await FallingEdge(dut.clk_i)
        self.cycle += 1
        return int(dut.valid_o.value), int(dut.data_o.value)

    async def word(self, data, datak=0, scr=3):
        """One valid word; return its output data."""
        _, out = await self.step(valid=1, data=data, datak=datak, scr=scr)
        return out


def random_word(rng, p_k=0.3, p_scr=0.85):
    data, datak = 0, 0
    for i in range(2):
        if rng.random() < p_k:
            datak |= 1 << i
            r = rng.random()
            b = COM if r < 0.2 else SKP if r < 0.45 else rng.choice(K_OTHER)
        else:
            b = rng.choice([COM, SKP]) if rng.random() < 0.08 else rng.getrandbits(8)
        data |= b << (8 * i)
    scr = sum((rng.random() < p_scr) << i for i in range(2))
    return data, datak, scr


async def random_run(bench, rng, cycles, p_valid=0.9, p_rst=0.002):
    seen = {"rst": 0, "invalid": 0, "com": 0, "skp": 0}
    for _ in range(cycles):
        rst = int(rng.random() < p_rst)
        valid = int(rng.random() < p_valid)
        data, datak, scr = random_word(rng)
        seen["rst"] += rst
        seen["invalid"] += 1 - valid
        for i in range(2):
            if (datak >> i) & 1:
                b = (data >> (8 * i)) & 0xFF
                seen["com"] += b == COM
                seen["skp"] += b == SKP
        await bench.step(rst, valid, data, datak, scr)
    await bench.step()  # check the last word
    bench.dut._log.info("events: %s", seen)
    assert all(seen.values()), f"vacuous run, an event never happened: {seen}"


def rng_for(dut):
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    return random.Random(cocotb.RANDOM_SEED)


def lo_hi(word):
    return word & 0xFF, word >> 8


@cocotb.test()
async def known_answer(dut):
    """L0: the HAS known answer and each rule once, with expected bytes from
    the HAS key sequence, not from the model."""
    b = Bench(dut)
    await b.start()
    keys = []
    for _ in range(8):
        keys += lo_hi(await b.word(0x0000))
    assert keys == KAT, f"key bytes after reset: expected {KAT} got {keys}"

    # COM in [15:8]: the next word starts at the seed; COM is not changed.
    # (16 symbols since reset: the key of 00h in [7:0] is key 16 of the stream.)
    out = await b.word(COM << 8 | 0x00, datak=0b10)
    assert lo_hi(out)[1] == COM, "COM was changed"
    assert lo_hi(await b.word(0x0000)) == (KAT[0], KAT[1]), "COM did not reset the LFSR"
    # COM in [7:0]: the data symbol in [15:8] takes the seed's key.
    assert lo_hi(await b.word(0x00 << 8 | COM, datak=0b01)) == (COM, KAT[0])
    # SKP holds the LFSR, in either lane and in both.
    assert lo_hi(await b.word(SKP << 8 | SKP, datak=0b11)) == (SKP, SKP)
    assert lo_hi(await b.word(SKP << 8 | 0x00, datak=0b10)) == (KAT[1], SKP)
    assert lo_hi(await b.word(0x00 << 8 | SKP, datak=0b01)) == (SKP, KAT[2])
    # Other K symbols advance but are never changed, even with scr_i set.
    assert lo_hi(await b.word(0x00 << 8 | 0xFB, datak=0b01)) == (0xFB, KAT[4])
    # scr_i low: sent as is, and the LFSR still advances (TS symbols).
    assert lo_hi(await b.word(0x4A4A, scr=0)) == (0x4A, 0x4A)
    assert lo_hi(await b.word(0x0000, scr=0b10)) == (0x00, KAT[8])
    # Data bytes equal to COM and SKP are data: scrambled, and they advance.
    assert lo_hi(await b.word(SKP << 8 | COM)) == (COM ^ KAT[9], SKP ^ KAT[10])
    # valid_i low holds the LFSR.
    await b.step(valid=0, data=0xFFFF, datak=0b11, scr=3)
    assert lo_hi(await b.word(0x0000)) == (KAT[11], KAT[12])
    # Reset loads the seed.
    await b.step(rst=1, valid=1)
    assert lo_hi(await b.word(0x0000)) == (KAT[0], KAT[1]), "reset did not load the seed"
    await b.step()


@cocotb.test()
async def random_vs_model(dut):
    """L1: random words with K, COM, SKP, data and scr mixes in both lanes,
    valid gaps and resets, against the model."""
    b = Bench(dut)
    await b.start()
    await random_run(b, rng_for(dut), 5000)


@cocotb.test()
async def disable(dut):
    """L1: stretches with scr_i low pass every symbol unchanged while the
    LFSR keeps following the stream, so the scrambled stretch after each
    one uses the key the model expects."""
    b = Bench(dut)
    await b.start()
    rng = rng_for(dut)
    disabled_words = 0
    for stretch in range(40):
        off = stretch % 2 == 0
        for _ in range(rng.randint(1, 60)):
            data, datak, scr = random_word(rng)
            if off:
                scr = 0
            valid, out = await b.step(valid=1, data=data, datak=datak, scr=scr)
            if off:
                disabled_words += 1
                assert valid and out == data, (
                    f"cycle {b.cycle}: disabled word changed: in {data:#06x} out {out:#06x}")
    await b.step()
    assert disabled_words > 0


@cocotb.test()
async def round_trip(dut):
    """L1: a random stream scrambled, then the scrambled words fed back after
    a reset (the descrambler is the same operation) give the stream back."""
    b = Bench(dut)
    await b.start()
    rng = rng_for(dut)
    words = [random_word(rng) for _ in range(3000)]
    scrambled = []
    for data, datak, scr in words:
        valid = int(rng.random() < 0.9)
        if not valid:
            await b.step(valid=0, data=rng.getrandbits(16), datak=rng.getrandbits(2), scr=3)
        scrambled.append((await b.word(data, datak, scr), datak, scr))
    assert any(s != w[0] for s, w in zip((s for s, _, _ in scrambled), words)), \
        "scrambling changed nothing"
    await b.step(rst=1)
    for i, ((data, _, _), (sdata, datak, scr)) in enumerate(zip(words, scrambled)):
        out = await b.word(sdata, datak, scr)
        assert out == data, f"word {i}: descrambled {out:#06x}, sent {data:#06x}"
    await b.step()


@cocotb.test()
async def long_random(dut):
    """L2: a full LFSR period of scrambled 00h (the key returns to FFh after
    65535 symbols, every key against the model), then long random traffic."""
    b = Bench(dut)
    await b.start()
    keys = []
    for _ in range(2**15):
        keys += lo_hi(await b.word(0x0000))
    assert keys[:16] == KAT and keys[2**16 - 1] == 0xFF, (
        f"period: first keys {keys[:4]}, key {2**16 - 1} is {keys[2**16 - 1]:#04x}")
    await random_run(b, rng_for(dut), 100000)
