"""pcie_pcs_dec8b10b tests (wiki/src/dv/pcs_dec8b10b/DV_PLAN.md).

Inputs change at the falling edge of clk_i; the outputs are combinational,
so they are compared with the model 1 ns later, in the same cycle, and the
RD register updates at the next rising edge. The oracle is the cycle model
in models/, which imports the pcs architecture model's decode().
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

from models import pcie_pcs_dec8b10b_model as m
from regress.funcov import Covergroup

# Error class per symbol, by the RD it was decoded at (DV plan, Coverage).
# A disparity error needs a known RD, so there is no unknown/disp bin.
COV_CLASS = Covergroup("pcs_dec8b10b_class")
COV_CLASS.coverpoint("lane", bins={"l0": 0, "l1": 1})
COV_CLASS.coverpoint("case", bins={
    f"{rd}_{cls}": (rd, cls)
    for rd in ("neg", "pos", "unk")
    for cls in ("valid", "disp", "code")
    if (rd, cls) != ("unk", "disp")
})
COV_CLASS.coverpoint("kind", bins={"data": 0, "k": 1})
COV_CLASS.cross("lane_x_case", "lane", "case")

# Why the RD was unknown for a symbol, per lane: every (lane, cause) pair
# the MAS allows. Lane 1 follows lane 0 in the same word, so its RD is
# unknown only when it is unlocked, lane 0 is unlocked, or the two symbols'
# generations differ (a relock between them).
COV_UNKNOWN = Covergroup("pcs_dec8b10b_rd_unknown")
COV_UNKNOWN.coverpoint("lane_cause", bins={
    f"l{lane}_{cause}": (lane, cause)
    for lane, causes in (
        (0, ("unlocked", "gen_change", "polarity_change", "after_reset",
             "after_unlocked")),
        (1, ("unlocked", "lane0_unlocked", "gen_change")))
    for cause in causes
})

# Recovery: the RD an errored symbol leaves, carried to the next symbol in
# the same word (lane) or in the next word (cycle).
COV_RECOVERY = Covergroup("pcs_dec8b10b_recovery")
COV_RECOVERY.coverpoint("err", bins=["code", "disp"])
COV_RECOVERY.coverpoint("rd_after", bins={"neg": -1, "pos": 1})
COV_RECOVERY.coverpoint("carry", bins=["lane", "cycle"])
COV_RECOVERY.cross("err_x_rd_x_carry", "err", "rd_after", "carry")

RD_NAME = {-1: "neg", 1: "pos", None: "unk"}

# Codes per column and per class (8B10B.md): 268 codes per RD, 72 of them
# valid at both, 464 valid at some RD.
PER_COLUMN = 268
BOTH_COLUMNS = 72
VALID_ANY = 464


class Bench:
    """Drives one word per cycle, checks every output against the model and
    samples the covergroups."""

    def __init__(self, dut):
        self.dut = dut
        self.model = m.Decoder()
        self.cycle = 0
        self.gen = 0
        self.pol = 0
        self.unknown_why = "after_reset"   # why the stored RD is unknown
        self.last_err = None               # (err, rd_out) of the last stored symbol

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk_i, 10, unit="ns").start())
        await self.step(rst=1)
        await self.step(rst=1)

    async def step(self, syms=(0, 0), lock=(1, 1), gen=None, pol=None,
                   valid=1, rst=0):
        """Drive one cycle; return the two decoded symbols the model expects."""
        dut, model = self.dut, self.model
        gen = (self.gen, self.gen) if gen is None else gen
        pol = self.pol if pol is None else pol
        dut.rst_i.value = rst
        dut.in_valid_i.value = valid
        dut.in_sym_i.value = syms[0] | (syms[1] << 10)
        dut.in_lock_i.value = lock[0] | (lock[1] << 1)
        dut.in_gen_i.value = gen[0] | (gen[1] << 1)
        dut.polarity_i.value = pol
        await Timer(1, unit="ns")
        exp = model.outputs(syms, lock, gen, pol)
        got_data = int(dut.out_data_o.value)
        got_k = int(dut.out_k_o.value)
        got_ce = int(dut.out_code_err_o.value)
        got_de = int(dut.out_disp_err_o.value)
        for lane, s in enumerate(exp):
            got = ((got_data >> (8 * lane)) & 0xFF, (got_k >> lane) & 1,
                   (got_ce >> lane) & 1, (got_de >> lane) & 1)
            want = (s.data, s.k, s.code_err, s.disp_err)
            assert got == want, (
                f"cycle {self.cycle} lane {lane}: symbol {syms[lane]:#05x} "
                f"lock {lock} gen {gen} pol {pol} rd_in {s.rd_in}: "
                f"(data, k, code_err, disp_err) expected {want} got {got}")
        if valid and not rst:
            self._sample(exp, lock, gen, pol)
        prev_pol = model.pol
        model.clock(rst, valid, syms, lock, gen, pol)
        self._track(rst, valid, exp, lock, pol != prev_pol)
        self.cycle += 1
        await FallingEdge(dut.clk_i)
        return exp

    def _sample(self, exp, lock, gen, pol):
        for lane, s in enumerate(exp):
            cls = "code" if s.code_err else "disp" if s.disp_err else "valid"
            COV_CLASS.sample(lane=lane, case=(RD_NAME[s.rd_in], cls), kind=s.k)
            if s.rd_in is None:
                COV_UNKNOWN.sample(lane_cause=(lane, self._cause(lane, lock, gen, pol)))
        # Recovery: the RD after an errored symbol, used by the next one.
        if exp[0].rd_in is not None and self.last_err is not None:
            COV_RECOVERY.sample(err=self.last_err[0], rd_after=self.last_err[1],
                                carry="cycle")
        if exp[1].rd_in is not None and (exp[0].code_err or exp[0].disp_err):
            COV_RECOVERY.sample(err="code" if exp[0].code_err else "disp",
                                rd_after=exp[0].rd_out, carry="lane")

    def _cause(self, lane, lock, gen, pol):
        if not lock[lane]:
            return "unlocked"
        if lane == 1:
            return "lane0_unlocked" if not lock[0] else "gen_change"
        if pol != self.model.pol:
            return "polarity_change"
        if not self.model.known:
            return self.unknown_why
        return "gen_change"

    def _track(self, rst, valid, exp, lock, pol_changed):
        """Remember why the stored RD is unknown and whether the stored
        symbol was an error, for the coverage samples of the next word."""
        if rst:
            self.unknown_why = "after_reset"
            self.last_err = None
        elif valid:
            s1 = exp[1]
            if not lock[1]:
                self.unknown_why = "after_unlocked"
            err = "code" if s1.code_err else "disp" if s1.disp_err else None
            self.last_err = (err, s1.rd_out) if err and lock[1] else None
        elif pol_changed:
            self.unknown_why = "polarity_change"
            self.last_err = None

    async def prime(self, rd):
        """One locked word that leaves the RD known and equal to ``rd``."""
        s0 = self.model.outputs((m.PROBE, 0), (1, 1), (self.gen, self.gen), self.pol)[0]
        await self.step((m.PROBE, m.setter(s0.rd_out, rd)))
        assert self.model.known and self.model.rd == rd


# ------------------------------------------------------------------ tests

@cocotb.test()
async def smoke(dut):
    """L0: commas at both RDs, a disparity error, a code error, reset."""
    b = Bench(dut)
    await b.start()
    com_m, _ = m.encode(0xBC, True, -1)
    com_p, _ = m.encode(0xBC, True, 1)
    exp = await b.step((com_m, com_p))          # RD unknown, then carried
    assert [(s.data, s.k, s.code_err, s.disp_err) for s in exp] == [(0xBC, 1, 0, 0)] * 2
    exp = await b.step((com_p, m.PROBE))        # RD- after COM+: COM+ is a disparity error
    assert (exp[0].disp_err, exp[0].data, exp[0].k) == (1, 0xBC, 1)
    exp = await b.step((0x000, 0x3FF))          # no ones, all ones: code errors
    assert all(s.code_err and s.data == m.K_EDB and s.k for s in exp)
    await b.step(rst=1)
    exp = await b.step((com_p, com_m))          # unknown again after reset
    assert exp[0].rd_in is None and not exp[0].disp_err


async def _exhaustive_lane(b, lane, mode, gen_split=False):
    """Every code in ``lane`` at RD ``mode`` (-1, 1 or None), each followed
    by a probe that shows the RD it left. Lane 1's RD is made unknown by an
    unlocked lane 0, or with ``gen_split`` by a new lock generation on lane
    1 alone (both locked). Returns class counts."""
    counts = {"valid": 0, "disp": 0, "code": 0}
    for c in range(1024):
        await b.prime(-1 if mode is None else mode)
        if lane == 0:
            if mode is None:
                b.gen ^= 1                    # a new locked run: RD unknown
            exp = await b.step((c, m.PROBE))
        else:
            if mode is not None:
                set0 = m.setter(b.model.rd, mode)
                exp = await b.step((set0, c))
            elif gen_split:
                g = b.gen
                b.gen ^= 1                    # relock between the two symbols
                exp = await b.step((m.PROBE, c), gen=(g, g ^ 1))
            else:
                exp = await b.step((m.PROBE, c), lock=(0, 1))
        s = exp[lane]
        assert s.rd_in == mode, f"lane {lane} code {c:#05x}: decoded at {s.rd_in}, wanted {mode}"
        counts["code" if s.code_err else "disp" if s.disp_err else "valid"] += 1
        if lane == 1:
            await b.step((m.PROBE, m.PROBE))  # shows the RD the code left
    return counts


@cocotb.test()
async def exhaustive(dut):
    """L1: all 1024 codes, in both lanes, at RD-, RD+ and unknown (lane 1
    unknown both through an unlocked lane 0 and through a generation split);
    the class counts are checked against the code's own table sizes."""
    b = Bench(dut)
    await b.start()
    passes = [(0, -1, False), (0, 1, False), (0, None, False),
              (1, -1, False), (1, 1, False), (1, None, False), (1, None, True)]
    for lane, mode, gen_split in passes:
        counts = await _exhaustive_lane(b, lane, mode, gen_split)
        dut._log.info("lane %d RD %s gen_split %s: %s", lane, mode, gen_split, counts)
        if mode is None:
            want = {"valid": VALID_ANY, "disp": 0, "code": 1024 - VALID_ANY}
        else:
            want = {"valid": PER_COLUMN, "disp": VALID_ANY - PER_COLUMN,
                    "code": 1024 - VALID_ANY}
        assert counts == want, (f"lane {lane} RD {mode} gen_split {gen_split}: "
                                f"counts {counts}, expected {want}")
    assert VALID_ANY == 2 * PER_COLUMN - BOTH_COLUMNS


class Line:
    """A transmitter: encoded random bytes with its own RD, plus impairments."""

    def __init__(self, rng):
        self.rng = rng
        self.rd = -1

    def symbol(self):
        rng = self.rng
        if rng.random() < 0.15:
            byte, k = rng.choice(m.code.VALID_K), True
        else:
            byte, k = rng.randrange(256), False
        c, self.rd = m.encode(byte, k, self.rd)
        r = rng.random()
        if r < 0.03:
            c ^= 1 << rng.randrange(10)                  # single-bit error
        elif r < 0.04:
            c = rng.randrange(1024)                      # any pattern
        elif r < 0.05:
            self.rd = -self.rd                           # transmitter RD slip
        return c


async def random_run(b, rng, cycles):
    """Random traffic: valid gaps, lock drops, generation changes, polarity
    changes, errors and resets, compared with the model every cycle; fails
    when an event never happened."""
    line = Line(rng)
    seen = {"gap": 0, "unlock": 0, "gen": 0, "relock": 0, "gen_split": 0,
            "pol": 0, "rst": 0, "code_err": 0, "disp_err": 0}
    lock = 1
    for _ in range(cycles):
        r = rng.random()
        if r < 0.002:
            await b.step(rst=1)
            seen["rst"] += 1
            continue
        if rng.random() < 0.01:
            lock ^= 1
            seen["unlock"] += 1 - lock
            if lock:
                b.gen ^= 1
                seen["gen"] += 1
        gens = None
        if lock and rng.random() < 0.01:
            # A relock in the same cycle: a new generation from lane 0, or
            # from lane 1 (the word straddles the relock).
            g = b.gen
            b.gen ^= 1
            if rng.random() < 0.5:
                gens = (g, g ^ 1)
                seen["gen_split"] += 1
            else:
                seen["relock"] += 1
        if rng.random() < 0.01:
            b.pol ^= 1
            seen["pol"] += 1
        valid = int(rng.random() < 0.85)
        seen["gap"] += 1 - valid
        inv = 0x3FF if b.pol else 0
        syms = (line.symbol() ^ inv, line.symbol() ^ inv)
        locks = (lock, lock)
        if rng.random() < 0.01:
            locks = (rng.randrange(2), rng.randrange(2))
        exp = await b.step(syms, lock=locks, gen=gens, valid=valid)
        seen["code_err"] += sum(s.code_err for s in exp) * valid
        seen["disp_err"] += sum(s.disp_err for s in exp) * valid
    b.dut._log.info("events: %s", seen)
    assert all(seen.values()), f"vacuous run, an event never happened: {seen}"


@cocotb.test()
async def random_vs_model(dut):
    """L1: 20000 cycles of random impaired traffic against the model."""
    b = Bench(dut)
    await b.start()
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    await random_run(b, random.Random(cocotb.RANDOM_SEED), 20000)


@cocotb.test()
async def long_random(dut):
    """L2: 200000 cycles of random impaired traffic against the model."""
    b = Bench(dut)
    await b.start()
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    await random_run(b, random.Random(cocotb.RANDOM_SEED), 200000)
