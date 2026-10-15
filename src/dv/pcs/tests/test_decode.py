"""pcie_pcs decode tests (wiki/src/dv/pcs/DV_PLAN.md): the decoder block's
checks through the pcie_pcs ports, with the exact-timing harness
tb.pcs_direct (0 ppm, every word compared on PIPE with the decoder block's
cycle model: RxData, RxDataK, RxValid and RxStatus).

The running disparity is not a port. A probe word after a tested code
shows the RD the code left: D0.0's RD- form is valid only at RD-, so it
reports a disparity error (RxStatus 7) after a code that leaves RD+. A
word's RxStatus covers both symbols, so the other symbol of a tested
word is D21.5, valid at either RD, and keeps the RD. The error counter
never reaches its limit: every errored word is followed by sixteen clean
ones (Stim.check_no_relock).
"""

from __future__ import annotations

import cocotb

from models import pcie_pcs_dec8b10b_model as dm
from regress.funcov import Covergroup
from tb.pcs_bench import seeded
from tb.pcs_direct import D21_5, PROBE, TS1, Direct, Stim

COM_M = dm.encode(0xBC, True, -1)[0]
COM_P = dm.encode(0xBC, True, 1)[0]
PER_COLUMN, VALID_ANY = 268, 464   # codes valid at one RD; at some RD (8B10B.md)

# Error class per symbol by the RD it was decoded at (DV plan, Decoder).
# A disparity error needs a known RD, so there is no unknown/disp bin.
COV_CLASS = Covergroup("pcs_dec_class")
COV_CLASS.coverpoint("lane", bins={"l0": 0, "l1": 1})
COV_CLASS.coverpoint("case", bins={
    f"{rd}_{cls}": (rd, cls)
    for rd in ("neg", "pos", "unk")
    for cls in ("valid", "disp", "code")
    if (rd, cls) != ("unk", "disp")})
COV_CLASS.cross("lane_x_case", "lane", "case")

# Why a symbol's RD was unknown, for each cause the pcie_pcs ports can
# produce (DV plan, Gaps: the others).
COV_UNKNOWN = Covergroup("pcs_dec_rd_unknown")
COV_UNKNOWN.coverpoint("cause", bins=["l0_unlocked", "l1_unlocked", "l0_polarity_change",
                                      "l0_after_reset", "l0_after_unlocked"])

# Recovery: the RD an errored symbol leaves, carried to the next symbol in
# the same word (lane) or in the next word (cycle).
COV_RECOVERY = Covergroup("pcs_dec_recovery")
COV_RECOVERY.coverpoint("err", bins=["code", "disp"])
COV_RECOVERY.coverpoint("rd_after", bins={"neg": -1, "pos": 1})
COV_RECOVERY.coverpoint("carry", bins=["lane", "cycle"])
COV_RECOVERY.cross("err_x_rd_x_carry", "err", "rd_after", "carry")

RD_NAME = {-1: "neg", 1: "pos", None: "unk"}


def cls(s: dm.Sym) -> str:
    return "code" if s.code_err else "disp" if s.disp_err else "valid"


def sample(st: Stim, cause: dict[int, str]) -> None:
    """Coverage from the checked words (sampled where they are compared);
    ``cause`` names why lane 0 of a locked word starts with the RD unknown."""
    prev = None
    for w, e in enumerate(st.exp):
        if e is None:
            prev = None
            continue
        for lane, s in enumerate(e.syms):
            COV_CLASS.sample(lane=lane, case=(RD_NAME[s.rd_in], cls(s)))
        if not e.valid:
            COV_UNKNOWN.sample(cause="l0_unlocked")
            COV_UNKNOWN.sample(cause="l1_unlocked")
        elif w in cause and e.syms[0].rd_in is None:
            COV_UNKNOWN.sample(cause=cause[w])
        s0, s1 = e.syms
        if prev is not None and prev.rd_in is not None and cls(prev) != "valid" \
                and s0.rd_in is not None:
            COV_RECOVERY.sample(err=cls(prev), rd_after=prev.rd_out, carry="cycle")
        if cls(s0) != "valid" and s1.rd_in is not None and s0.rd_in is not None:
            COV_RECOVERY.sample(err=cls(s0), rd_after=s0.rd_out, carry="lane")
        prev = s1 if e.valid else None


def counts_of(exps) -> dict[str, int]:
    out = {"valid": 0, "disp": 0, "code": 0}
    for lane, e in exps:
        out[cls(e.syms[lane])] += 1
    return out


async def run(dut, st: Stim) -> Direct:
    d = Direct(dut)
    await d.start(st)
    await d.run()
    counts = d.check()
    dut._log.info("%d words driven, checked words by RxStatus %s", len(st.codes), counts)
    return d


# ------------------------------------------------------------------ L0

@cocotb.test()
async def rx_decode_smoke(dut):
    """L0: commas at both RDs, a disparity error, code errors as EDB, a
    pipe_reset_ni that leaves the RD unknown, each word on PIPE."""
    st = Stim()
    st.idle_words(32)
    st.lock()
    st.clean(4)
    rd = st.rd
    com_same, com_other = (COM_M, COM_P) if rd == -1 else (COM_P, COM_M)
    e = st.add(com_same, D21_5, tag="COM at the RD")
    assert e.status == 0 and e.syms[0].data == 0xBC and e.syms[0].k
    st.clean(16)
    e = st.add(com_other if st.rd == rd else com_same, D21_5, tag="COM at the other RD")
    assert e.status == 7 and e.syms[0].data == 0xBC
    st.clean(16)
    e = st.add(0x000, 0x3FF, tag="code errors")
    assert e.status == 4 and all(s.data == 0xFE and s.k for s in e.syms)
    st.clean(16)
    for _ in range(3):
        st.add(D21_5, D21_5, rst=1)
    rd = -1 if st.model.rd == 1 else 1     # the RD the reset forgot
    e = st.add(COM_P if rd == -1 else COM_M, D21_5, tag="COM after reset")
    assert e.status == 0 and e.syms[0].rd_in is None
    st.clean(16)
    st.line(TS1 * 2, check=True)
    d = await run(dut, st)
    assert d.d is not None


# ------------------------------------------------------------------ L1

def table(st: Stim, cause: dict[int, str]) -> dict:
    """Every code in each lane at RD- and RD+ (known), in lane 0 at an
    unknown RD after a polarity change, and in both lanes unlocked (RD
    unknown, data and K only); each known or polarity case followed by the
    probe. Returns the tested (lane, Exp) lists by case."""
    cases: dict = {}
    for lane in (0, 1):
        for want in (-1, 1):
            got = cases.setdefault((lane, want), [])
            for c in range(1024):
                if lane == 0:
                    st.add(D21_5, dm.setter(st.rd, want))
                    got.append((0, st.add(c, D21_5, tag=f"lane 0 RD {want} code {c:#05x}")))
                else:
                    got.append((1, st.add(dm.setter(st.rd, want), c,
                                          tag=f"lane 1 RD {want} code {c:#05x}")))
                st.add(PROBE, D21_5, tag="probe")
                st.clean(16)
    # Lane 0 at an unknown RD: RxPolarity rises for the tested word (the
    # line carries it inverted), so the RD restarts; the probe follows at
    # the same polarity; RxPolarity falls again on a D21.5 word.
    got = cases.setdefault((0, None), [])
    for c in range(1024):
        w = len(st.codes)
        got.append((0, st.add(c, D21_5, pol=1, tag=f"lane 0 RD unknown code {c:#05x}")))
        cause[w] = "l0_polarity_change"
        st.add(PROBE, D21_5, pol=1, tag="probe")
        st.add(D21_5, D21_5)
        st.clean(16)
    # Both lanes unlocked: the line in electrical idle, still carrying the
    # codes, so lock drops and the words flow on unlocked. Lane 1 takes a
    # permutation of the codes.
    st.clean(2, check=False)
    st.add(D21_5, D21_5, lock=0, idle=1, check=False)
    st.add(D21_5, D21_5, lock=0, idle=1, check=False)
    for lane in (0, 1):
        cases[(lane, "unlocked")] = []
    for c in range(1024):
        e = st.add(c, (397 * c + 501) % 1024, lock=0, idle=1, tag=f"unlocked code {c:#05x}")
        cases[(0, "unlocked")].append((0, e))
        cases[(1, "unlocked")].append((1, e))
    # The last unlocked symbol leaves the RD positive (a COM at RD-), and
    # the training's first COM is the RD- form: decoded with the RD carried
    # over it would be a disparity error.
    st.add(D21_5, COM_M, lock=0, idle=1, check=False)
    st.tx_rd = -1
    return cases


@cocotb.test()
async def rx_decode_table(dut):
    """L1: all 1024 codes through the pcie_pcs ports: in each lane at RD-
    and RD+, in lane 0 at an unknown RD (after a polarity change), each with
    the RD it leaves (probe); in both lanes unlocked. RxData, RxDataK,
    RxValid and RxStatus equal the decoder model on every word; the class
    counts are the code's own table sizes (268 valid, 196 disparity, 560
    code errors at a known RD; 464 valid and 560 code at an unknown RD). After
    the unlocked words lock returns with the RD unknown: no error on the
    first locked word, although the RD the unlocked symbols left would make
    its COM a disparity error."""
    st = Stim()
    cause: dict[int, str] = {}
    st.idle_words(32)
    st.lock()
    st.clean(4)
    cases = table(st, cause)
    relock_word = len(st.codes)
    st.lock(8)
    st.clean(8, check=True)
    d = await run(dut, st)
    for (lane, rd), exps in cases.items():
        got = counts_of(exps)
        if rd in (-1, 1):
            want = {"valid": PER_COLUMN, "disp": VALID_ANY - PER_COLUMN,
                    "code": 1024 - VALID_ANY}
        else:
            want = {"valid": VALID_ANY, "disp": 0, "code": 1024 - VALID_ANY}
        assert got == want, f"lane {lane} RD {rd}: classes {got}, expected {want}"
    t, out = d.first_valid_after(relock_word)
    assert out["pipe_rx_status_o"] == 0 and out["pipe_rx_data_o"] & 0xFF == 0xBC, (
        f"first locked word after the unlocked run (cycle {t}): RxStatus "
        f"{out['pipe_rx_status_o']}, data {out['pipe_rx_data_o']:#06x}")
    assert t == relock_word + d.d, f"first locked word at cycle {t}, expected {relock_word + d.d}"
    sample(st, cause)
    COV_UNKNOWN.sample(cause="l0_after_unlocked")


def random_stim(rng, words: int) -> tuple[Stim, dict[str, int], dict[int, str]]:
    """Encoded random traffic (15% K symbols) with single-bit errors, random
    codes, partner RD slips, RxPolarity changes (the line inverted while it
    is 1, single-word pulses among them) and pipe_reset_ni pulses; every
    impairment followed by clean words so the relock limit is never met."""
    st = Stim()
    st.idle_words(32)
    st.lock()
    st.clean(4)
    seen = {"pol": 0, "pulse": 0, "rst": 0, "code_err": 0, "disp_err": 0, "rd_slip": 0,
            "bit": 0, "random": 0}
    cause: dict[int, str] = {}
    pol = 0
    while len(st.codes) < words:
        r = rng.random()
        if r < 0.004:
            for _ in range(rng.randrange(1, 4)):
                st.add(D21_5, D21_5, rst=1)
            cause[len(st.codes)] = "l0_after_reset"
            seen["rst"] += 1
        elif r < 0.02:
            pol ^= 1
            cause[len(st.codes)] = "l0_polarity_change"
            seen["pol"] += 1
            if rng.random() < 0.3:
                st.line([(0, rng.randrange(256)), (0, rng.randrange(256))], check=True, pol=pol)
                pol ^= 1                      # a one-word pulse
                cause[len(st.codes)] = "l0_polarity_change"
                seen["pulse"] += 1
        syms = []
        for _ in range(2):
            if rng.random() < 0.15:
                syms.append((1, rng.choice(dm.code.VALID_K)))
            else:
                syms.append((0, rng.randrange(256)))
        c = []
        rd = st.tx_rd
        for k, v in syms:
            code, rd = dm.code.encode(v, bool(k), rd)
            c.append(code)
        st.tx_rd = rd
        q = rng.random()
        impaired = True
        if q < 0.03:
            c[rng.randrange(2)] ^= 1 << rng.randrange(10)
            seen["bit"] += 1
        elif q < 0.04:
            c[rng.randrange(2)] = rng.randrange(1024)
            seen["random"] += 1
        elif q < 0.05:
            st.tx_rd = -st.tx_rd
            seen["rd_slip"] += 1
        else:
            impaired = False
        e = st.add(c[0], c[1], pol=pol, tag="random")
        if e is not None:
            seen["code_err"] += e.status == 4
            seen["disp_err"] += e.status == 7
        if impaired or (e is not None and e.status):
            st.clean(16, pol=pol)
    st.clean(8, pol=pol)
    return st, seen, cause


async def random_run(dut, words: int) -> None:
    rng = seeded(dut)
    st, seen, cause = random_stim(rng, words)
    await run(dut, st)
    dut._log.info("events %s", seen)
    assert all(seen.values()), f"vacuous run, an event never happened: {seen}"
    sample(st, cause)


@cocotb.test()
async def rx_decode_random(dut):
    """L1: 20000 words of random impaired traffic, every word on PIPE equal
    to the decoder model."""
    await random_run(dut, 20000)


@cocotb.test()
async def rx_decode_random_long(dut):
    """L2: 150000 words of the same."""
    await random_run(dut, 150000)
