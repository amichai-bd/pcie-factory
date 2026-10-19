"""common_tlp_rx_track tests (wiki/src/dv/common/common_tlp_rx_track.md).

The top is tb/common_tlp_rx_track_fixture.sv, which splits the tracker's
t_tlp_rx_pos output into one port per field. Inputs change at falling
clk_i edges; in every cycle, once they settle, every field is compared
with models/common_tlp_rx_track_model.py. A beat counted from the wrong
start, a count that wraps instead of saturating, a header DW enable or a
last header DW off by one, a header size taken from the wrong beat or
state that survives the eop or a reset fails at that cycle.

`contract` and `random_tlps` keep to the whole-TLP contract, so the
tracker's assertions stay quiet. The `framing_*` cases break it on
purpose and pass only when the named assertion stops the simulation; no
regression list holds them (a stopped simulation is a failed run). They
are run by hand to show each framing assertion fires.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from models.common_tlp_rx_track_model import BEAT_MAX, TlpRxTrackModel
from regress.funcov import Covergroup

CLK_NS = 10
FIELDS = ("sop", "mid", "hdr_last", "en_dw", "busy", "hdr4", "beat", "hdr_end")

# Functional coverage (DV plan, Strategy): the kind of each beat taken, the
# header size, TLP lengths up to and past saturation, gaps inside a TLP,
# back-to-back TLPs and resets inside one.
COV = Covergroup("common_tlp_rx_track")
COV.coverpoint("beat", bins=["sop", "hdr1", "hdr2", "hdr3", "data", "saturated", "gap"])
COV.coverpoint("hdr", bins=["3dw", "4dw"])
COV.coverpoint("length", bins={"header_only": lambda n: n in (3, 4),
                               "short": lambda n: 5 <= n <= 40,
                               "long": lambda n: 41 <= n <= 64,
                               "saturating": lambda n: n > 64})
COV.coverpoint("next", bins=["back_to_back", "after_gap"])
COV.coverpoint("reset", bins=["inside", "outside"])
COV.cross("hdr_x_length", "hdr", "length")


class Track:
    """Drives one tracker, keeps the model and checks every field."""

    def __init__(self, dut):
        self.dut = dut
        self.model = TlpRxTrackModel()
        self.cycle = 0
        self.saturated = 0       # beats taken with the count at 63
        self.ended = True        # the last edge took an eop (or reset)

    async def start(self):
        d = self.dut
        self.drive(False, False, False, False)
        cocotb.start_soon(Clock(d.clk_i, CLK_NS, unit="ns").start())
        await FallingEdge(d.clk_i)
        await self.reset()

    def drive(self, take: bool, sop: bool, eop: bool, hdr4: bool) -> None:
        d = self.dut
        d.take_i.value = int(take)
        d.sop_i.value = int(sop)
        d.eop_i.value = int(eop)
        d.hdr4_i.value = int(hdr4)

    async def reset(self):
        d = self.dut
        COV.sample(reset="inside" if self.model.busy else "outside")
        self.drive(False, False, False, False)
        d.rst_i.value = 1
        await RisingEdge(d.clk_i)
        self.model.reset()
        self.ended = True
        await FallingEdge(d.clk_i)
        d.rst_i.value = 0

    def _check(self, take: bool, sop: bool, what: str) -> None:
        d = self.dut
        want = self.model.pos(take, sop)
        for f in FIELDS:
            got = int(getattr(d, f"{f}_o").value)
            exp = int(getattr(want, f))
            if got != exp:
                raise AssertionError(f"cycle {self.cycle} {what}: {f} expected {exp} got {got}")

    async def step(self, take: bool, sop: bool = False, eop: bool = False,
                   hdr4: bool = False) -> None:
        """Drive at the falling edge, check the settled fields, take the
        rising edge. Nonsense on sop, eop and hdr4 with take low is part of
        the stimulus: the tracker must ignore it."""
        m = self.model
        what = "sop" if take and sop else "beat" if take else "gap"
        if take and not sop:
            b = m.beat
            what = ("saturated" if b == BEAT_MAX else f"hdr{b}" if b <= 3 and b <= m.hdr_end
                    else "data")
            self.saturated += b == BEAT_MAX
        elif not take and m.busy:
            COV.sample(beat="gap")
        if take:
            COV.sample(beat=what)
        self.drive(take, sop, eop, hdr4)
        await ReadOnly()
        self._check(take, sop, what)
        await RisingEdge(self.dut.clk_i)
        m.edge(take, sop, eop, hdr4)
        self.cycle += 1
        await FallingEdge(self.dut.clk_i)

    async def tlp(self, n: int, hdr4: bool, rng: random.Random | None = None,
                  p_gap: float = 0.0) -> None:
        """One whole TLP of n beats, with gaps (take low, random sop, eop
        and hdr4) between beats when rng is given."""
        COV.sample(hdr="4dw" if hdr4 else "3dw", length=n,
                   next="back_to_back" if self.ended else "after_gap")
        for i in range(n):
            while rng is not None and i > 0 and rng.random() < p_gap:
                await self.step(False, rng.random() < 0.5, rng.random() < 0.5,
                                rng.random() < 0.5)
            await self.step(True, i == 0, i == n - 1,
                            hdr4 if i == 0 else rng is not None and rng.random() < 0.5)
        self.ended = True


@cocotb.test()
async def contract(dut):
    """Directed: a 3-DW header alone, a 4-DW header alone, TLPs with data
    back to back, gaps inside a TLP, a TLP past 63 beats, a reset inside
    a TLP and the next sop after it."""
    t = Track(dut)
    await t.start()
    await t.tlp(3, False)
    await t.tlp(4, True)
    await t.tlp(8, False)
    await t.tlp(6, True)
    # Gaps inside a TLP, with sop and eop high while take is low.
    await t.step(True, sop=True, hdr4=True)
    await t.step(False, sop=True, eop=True)
    await t.step(True)
    await t.step(False, sop=True, eop=True, hdr4=False)
    await t.step(True)
    await t.step(True, eop=True)
    # Saturation: 70 beats.
    await t.tlp(70, False)
    assert t.saturated >= 6, f"{t.saturated} saturated beats"
    # A reset inside a TLP, then a new TLP.
    await t.step(True, sop=True, hdr4=True)
    await t.step(True)
    await t.reset()
    assert not t.model.busy
    await t.tlp(5, False)
    await t.step(False)


@cocotb.test()
async def random_tlps(dut):
    """Random whole TLPs of 3 to 80 beats, both header sizes, with random
    gaps and idle cycles and a rare reset, against the model."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    t = Track(dut)
    await t.start()
    tlps = 0
    for _ in range(300):
        hdr4 = rng.random() < 0.5
        lo = 4 if hdr4 else 3
        n = rng.choice((lo, rng.randint(lo, 40), rng.randint(41, 80)))
        while rng.random() < 0.3:
            t.ended = False
            await t.step(False, rng.random() < 0.5, rng.random() < 0.5, rng.random() < 0.5)
        await t.tlp(n, hdr4, rng, p_gap=0.1)
        tlps += 1
        if rng.random() < 0.02:
            # A reset inside the next TLP.
            await t.step(True, sop=True, hdr4=rng.random() < 0.5)
            for _ in range(rng.randrange(4)):
                await t.step(True)
            await t.reset()
    assert tlps == 300 and t.saturated > 0, f"{t.saturated} saturated beats"


async def framing(dut, beats, label: str) -> None:
    """Drive beats (take, sop, eop, hdr4) and expect the assertion `label`
    to stop the simulation at the last one."""
    t = Track(dut)
    await t.start()
    for b in beats:
        t.drive(*b)
        await RisingEdge(dut.clk_i)
        await FallingEdge(dut.clk_i)
    for _ in range(3):
        await RisingEdge(dut.clk_i)
    raise AssertionError(f"assertion {label} did not stop the simulation")


@cocotb.test()
async def framing_beat_outside(dut):
    """Negative, no regression: a beat with no TLP open."""
    await framing(dut, [(1, 0, 0, 0)], "a_beat_outside_tlp")


@cocotb.test()
async def framing_sop_inside(dut):
    """Negative, no regression: a sop while a TLP is open."""
    await framing(dut, [(1, 1, 0, 0), (1, 1, 0, 0)], "a_sop_inside_tlp")


@cocotb.test()
async def framing_short_sop(dut):
    """Negative, no regression: an eop on the sop beat."""
    await framing(dut, [(1, 1, 1, 0)], "a_short_tlp")


@cocotb.test()
async def framing_short_hdr4(dut):
    """Negative, no regression: a 4-DW header ending at its third DW."""
    await framing(dut, [(1, 1, 0, 1), (1, 0, 0, 0), (1, 0, 1, 0)], "a_short_tlp")
