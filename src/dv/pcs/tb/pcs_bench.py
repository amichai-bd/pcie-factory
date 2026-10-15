"""PIPE-side bench of the pcs top (wiki/src/dv/pcs/DV_PLAN.md): the MAC
driver (tb.pcs_env.PipeMac) with the per-cycle PIPE-control scoreboard
against models.pcie_pcs_model.PipeRef, the transmit-word check against the
architecture model's encoder, and the MAC procedures (reset, PowerDown,
receiver detection, transmit) with their functional coverage.
"""

from __future__ import annotations

import os
import random

import cocotb

from models.pcie_pcs_model import LEGAL_ARCS, P0, P1, POWER_NAMES, PipeRef, arch
from regress.funcov import Covergroup
from tb.pcs_env import PipeMac, start_clocks

# ---------------------------------------------------------------- coverage

# Every PowerDown transition the MAC uses, acknowledged (floor 100 at L2).
COV_POWER = Covergroup("pcs_pipe_power")
COV_POWER.coverpoint("arc", bins={
    f"{POWER_NAMES[a]}__{POWER_NAMES[b]}": (POWER_NAMES[a], POWER_NAMES[b])
    for a, b in sorted(LEGAL_ARCS)})

# Reset kinds and lengths, receiver detection results, transmit states.
COV_CTRL = Covergroup("pcs_pipe_control")
COV_CTRL.coverpoint("reset", bins={"rst_i": "rst_i", "pipe_reset_short": "pipe_reset_short",
                                   "pipe_reset_long": "pipe_reset_long"})
COV_CTRL.coverpoint("detect", bins={"present": 3, "absent": 0})
COV_CTRL.coverpoint("tx", bins=["data", "compliance", "k_invalid", "idle_p0", "loopback",
                                "idle_not_p0", "p2_no_beacon"])
COV_CTRL.coverpoint("change_before_ready", bins={"no_pulse": 1})

VALID_K = sorted(arch.code8b10b.VALID_K)

# The encoder through PIPE TX (the encoder block's checks): every valid
# symbol at each RD in each lane (268 x 2 x 2 crosses), unsupported K bytes
# sent as EDB in each lane, TxCompliance at each RD held before the word,
# the RD carried between lanes, and the RD held across TxElecIdle. Sampled
# for every word checked on ser_tx_data_o whose RD the reference knows.
SYMBOLS = {f"D{b & 31}.{b >> 5}": (0, b) for b in range(256)}
SYMBOLS.update({f"K{b & 31}.{b >> 5}": (1, b) for b in VALID_K})
COV_TX_CODES = Covergroup("pcs_tx_codes")
COV_TX_CODES.coverpoint("symbol", bins=SYMBOLS)
COV_TX_CODES.coverpoint("rd", bins={"neg": -1, "pos": 1})
COV_TX_CODES.coverpoint("lane", bins={"lane0": 0, "lane1": 1})
COV_TX_CODES.cross("symbol_x_rd_x_lane", "symbol", "rd", "lane")
COV_TX_CTRL = Covergroup("pcs_tx_controls")
COV_TX_CTRL.coverpoint("compliance", bins={"off": 0, "on": 1})
COV_TX_CTRL.coverpoint("rd_word", bins={"neg": -1, "pos": 1})      # RD held before the word
COV_TX_CTRL.coverpoint("rd_mid", bins={"neg": -1, "pos": 1})       # RD between the lanes
COV_TX_CTRL.coverpoint("edb_lane", bins={"lane0": 0, "lane1": 1})  # unsupported K sent as EDB
COV_TX_CTRL.coverpoint("rd_held", bins={"neg": -1, "pos": 1})      # RD kept across TxElecIdle
COV_TX_CTRL.coverpoint("reset", bins={"in_flight": 1})            # a reset drops a word in flight
COV_TX_CTRL.cross("compliance_x_rd_word", "compliance", "rd_word")
COV_TX_CTRL.cross("rd_word_x_rd_mid", "rd_word", "rd_mid")


def regress_seed() -> int:
    """The seed the regression entry passed (COCOTB_RANDOM_SEED). cocotb
    derives each test's RANDOM_SEED from it, so a property chosen from the
    entry's seed list, such as a clock offset sign, uses this one."""
    return int(os.environ.get("COCOTB_RANDOM_SEED", cocotb.RANDOM_SEED))


def seeded(dut) -> random.Random:
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    return random.Random(cocotb.RANDOM_SEED)


# -------------------------------------------------------------- scoreboard

class Bench:
    """The MAC, the reference model and the per-cycle checks."""

    def __init__(self, dut, max_cycles: int, receive: bool = False) -> None:
        # receive: the line carries data, so RxValid and the receive RxStatus
        # codes belong to the receive scoreboard (test_datapath), not here.
        self.receive = receive
        self.dut = dut
        self.mac = PipeMac(dut)
        self.ref = PipeRef()
        self.max_cycles = max_cycles
        self.out: dict[str, int] = {}
        self.ready = False
        # Transmit reference: words seen per cycle, running disparity.
        self.tx_words: list[tuple | None] = [None, None]
        self.tx_rd = {-1}          # possible running disparities
        self.tx_after_idle = False           # idle cycles in P0 since the last word
        self.checked_words = 0

    async def tick(self) -> dict[str, int]:
        m = self.mac
        if m.cycle >= self.max_cycles:
            raise AssertionError(f"cycle bound {self.max_cycles} exceeded")
        ins = m.model_inputs()
        out = await m.step()
        exp = self.ref.step(**ins)
        cyc = m.cycle
        in_rst = ins["rst"] or not ins["reset_n"]
        keys = [("phy_status", "pipe_phy_status_o"), ("tx_elec_idle", "ser_tx_elec_idle_o")]
        if not self.receive or exp["phy_status"] and exp["rx_status"]:
            keys.append(("rx_status", "pipe_rx_status_o"))
        for key, port in keys:
            assert out[port] == exp[key], (
                f"cycle {cyc}: {port} expected {exp[key]} got {out[port]} (inputs {ins})")
        if in_rst:
            for port, want in (("pipe_rx_valid_o", 0), ("pipe_rx_status_o", 0),
                               ("pipe_rx_elec_idle_o", 1), ("ser_tx_elec_idle_o", 1),
                               ("pipe_phy_status_o", 1)):
                assert out[port] == want, f"cycle {cyc}: {port} {out[port]} in reset, expected {want}"
        assert self.receive or out["pipe_rx_valid_o"] == 0, f"cycle {cyc}: RxValid with the line idle"
        self._check_tx(ins, in_rst, out, cyc)
        if in_rst:
            self.ready = False
        elif not out["pipe_phy_status_o"]:
            self.ready = True
        self.out = out
        return out

    def _check_tx(self, ins, in_rst, out, cyc) -> None:
        """The word seen two cycles ago, encoded at the reference RD. After
        loopback words the RD is unknown to the reference, so words are
        accepted at either RD until one fixes it."""
        word = None
        if in_rst:
            if any(w not in (None, "idle") for w in self.tx_words):
                COV_TX_CTRL.sample(reset=1)
            self.tx_rd = {-1}
            self.tx_words = [None, None]
            self.tx_after_idle = False
        elif ins["power_down"] == P0 and ins["tx_elec_idle"]:
            word = "idle"                  # no word: the RD holds across it
        elif ins["power_down"] == P0 and not ins["tx_elec_idle"]:
            m = self.mac.ins
            if m["pipe_tx_detect_rx_i"]:
                word = "loopback"
            else:
                data, datak = m["pipe_tx_data_i"], m["pipe_tx_datak_i"]
                syms = [((datak >> i) & 1, (data >> (8 * i)) & 0xFF) for i in range(2)]
                word = (syms, {0} if m["pipe_tx_compliance_i"] else None)
        sent = self.tx_words.pop(0)
        self.tx_words.append(word)
        if sent is None:
            return
        if sent == "idle":
            self.tx_after_idle = True
            return
        assert not out["ser_tx_elec_idle_o"], f"cycle {cyc}: word sent but line idle"
        if sent == "loopback":
            self.tx_rd = {-1, 1}
            self.tx_after_idle = False
            return
        syms, comp = sent
        got = out["ser_tx_data_o"]
        tries, after, hits = [], set(), []
        for rd in sorted(self.tx_rd):
            codes, rd_after = arch.tx_encode(syms, comp, rd)
            want = codes[0] | (codes[1] << 10)
            tries.append(want)
            if want == got:
                after.add(rd_after)
                hits.append(rd)
        assert after, (f"cycle {cyc}: ser_tx_data_o for {syms} (compliance {bool(comp)}) "
                       f"expected {' or '.join(f'{w:05x}' for w in tries)} got {got:05x}")
        if len(self.tx_rd) == 1:
            self._sample_tx(syms, bool(comp), hits[0])
        # A word of balanced codes matches at both RDs and keeps both open.
        self.tx_rd = after
        self.checked_words += 1

    def _sample_tx(self, syms, comp: bool, rd_word: int) -> None:
        """Coverage of a word encoded from the one RD the reference knows."""
        if self.tx_after_idle:
            COV_TX_CTRL.sample(rd_held=rd_word)   # the RD the idle cycles held
            self.tx_after_idle = False
        rd0 = -1 if comp else rd_word
        _, rd_mid = arch.tx_encode(syms[:1], None, rd0)
        COV_TX_CTRL.sample(compliance=int(comp), rd_word=rd_word, rd_mid=rd_mid)
        for lane, rd in ((0, rd0), (1, rd_mid)):
            k, b = syms[lane]
            if k and b not in arch.code8b10b.VALID_K:
                COV_TX_CTRL.sample(edb_lane=lane)
            else:
                COV_TX_CODES.sample(symbol=(k, b), rd=rd, lane=lane)

    # ---------------------------------------------------------- MAC steps

    async def run(self, n: int, **kw) -> list[dict[str, int]]:
        self.mac.set(**kw)
        return [await self.tick() for _ in range(n)]

    async def until_pulse(self, bound: int) -> int:
        """Ticks until a PhyStatus pulse; returns its index (from 1)."""
        for i in range(1, bound + 1):
            if (await self.tick())["pipe_phy_status_o"]:
                return i
        raise AssertionError(f"cycle {self.mac.cycle}: no PhyStatus pulse within {bound} cycles")

    async def reset(self, kind: str, length: int) -> None:
        """Hold the PCS in reset, then wait for PhyStatus to fall."""
        self.mac.held_in_reset()
        if kind == "rst_i":
            self.mac.set(rst=1)
        else:
            self.mac.set(reset_n=0)
        await self.run(length)
        self.mac.set(rst=0, reset_n=1)
        n = 0
        while True:
            n += 1
            if not (await self.tick())["pipe_phy_status_o"]:
                break
            assert n <= 17, f"cycle {self.mac.cycle}: PhyStatus still high {n} cycles after reset"
        assert n == 17, f"cycle {self.mac.cycle}: PhyStatus fell {n} cycles after reset, expected 17"
        cov = kind if kind == "rst_i" else ("pipe_reset_short" if length == 1 else "pipe_reset_long")
        COV_CTRL.sample(reset=cov)

    async def power(self, new: int) -> None:
        """One PowerDown change and its acknowledge, four cycles later."""
        old = self.mac.ins["pipe_power_down_i"]
        assert (old, new) in LEGAL_ARCS, (old, new)
        if old == P0:                 # TxElecIdle rises before leaving P0
            await self.run(1, tx_elec_idle=1, tx_detect_rx=0, tx_compliance=0)
        self.mac.set(power_down=new)
        i = await self.until_pulse(8)
        assert i == 5, f"cycle {self.mac.cycle}: PowerDown acknowledged {i - 1} cycles after the change, expected 4"
        COV_POWER.sample(arc=(POWER_NAMES[old], POWER_NAMES[new]))

    async def detect(self, present: int) -> None:
        """Receiver detection in P1: 64 cycles, then RxStatus 3 or 0."""
        assert self.mac.ins["pipe_power_down_i"] == P1
        # ser_tx_rxdet_i carries the result only in detection cycle 63, the
        # cycle the HAS samples it, and the opposite value in every other.
        self.mac.set(tx_elec_idle=1, tx_detect_rx=1, ser_tx_rxdet=1 - present)
        for i in range(1, 81):
            self.mac.set(ser_tx_rxdet=present if i == 63 else 1 - present)
            if (await self.tick())["pipe_phy_status_o"]:
                break
        assert i == 64, f"cycle {self.mac.cycle}: detection took {i} cycles, expected 64"
        code = self.out["pipe_rx_status_o"]
        assert code == (3 if present else 0), f"cycle {self.mac.cycle}: RxStatus {code}"
        COV_CTRL.sample(detect=code)
        await self.run(1, tx_detect_rx=0)

    async def transmit(self, rng: random.Random, n: int, loopback: bool = False) -> None:
        """P0 traffic: data words (some K, some invalid K, some with
        TxCompliance), idle gaps and, optionally, loopback."""
        assert self.mac.ins["pipe_power_down_i"] == P0
        for _ in range(n):
            r = rng.random()
            if loopback:
                self.mac.set(tx_elec_idle=0, tx_detect_rx=1)
                COV_CTRL.sample(tx="loopback")
            elif r < 0.15:
                self.mac.set(tx_elec_idle=1, tx_detect_rx=0, tx_compliance=0)
                COV_CTRL.sample(tx="idle_p0")
            else:
                data, datak = 0, 0
                for i in range(2):
                    q = rng.random()
                    if q < 0.15:
                        b, k = rng.choice(VALID_K), 1
                    elif q < 0.18:
                        b, k = rng.choice([x for x in range(256) if x not in VALID_K]), 1
                        COV_CTRL.sample(tx="k_invalid")
                    else:
                        b, k = rng.randrange(256), 0
                    data |= b << (8 * i)
                    datak |= k << i
                comp = int(rng.random() < 0.05)
                self.mac.set(tx_elec_idle=0, tx_detect_rx=0, tx_data=data, tx_datak=datak,
                             tx_compliance=comp, rx_polarity=rng.randrange(2))
                COV_CTRL.sample(tx="compliance" if comp else "data")
            await self.tick()
        self.mac.set(tx_detect_rx=0, tx_compliance=0)


async def start(dut, rng: random.Random | None = None, max_cycles: int = 20000) -> Bench:
    ppm = rng.uniform(-600, 600) if rng else 0.0
    start_clocks(dut, ppm, rng.randrange(8000) if rng else 0)
    dut.ser_rx_data_i.value = 0
    dut.ser_rx_elec_idle_i.value = 1
    b = Bench(dut, max_cycles)
    await b.reset("rst_i", 8)
    return b
