"""pcie_pcs PIPE-control tests (wiki/src/dv/pcs/DV_PLAN.md).

The MAC side of PIPE is driven by tb.pcs_env.PipeMac, following the MAC's
rules of wiki/src/arch/pcs/PIPE_CONTROL.md (inputs held in reset, nothing
started before PhyStatus falls, one PowerDown change per acknowledge,
TxDetectRx held until the detection pulse). Every cycle the scoreboard
compares PhyStatus, RxStatus and the transmitter's electrical idle with
models.pcie_pcs_model.PipeRef (the architecture model's PipeControl plus the
two-cycle line latency), and every transmitted word with the architecture
model's encoder (tx_encode, running disparity kept across electrical idle,
TxCompliance, unsupported K to EDB). RxElecIdle is checked against the
ser_rx_elec_idle_i level and the crossing latency of the HAS.
"""

from __future__ import annotations

import random

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from models.pcie_pcs_model import (LEGAL_ARCS, P0, P0S, P1, P2, POWER_NAMES, PipeRef,
                                   arch)
from regress.funcov import Covergroup
from tb.pcs_env import EdgeLog, PipeMac, now_ps, start_clocks

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

# RxElecIdle edges seen through the crossing, by RX clock offset sign.
COV_RXIDLE = Covergroup("pcs_rx_elec_idle")
COV_RXIDLE.coverpoint("edge", bins={"enter": 1, "exit": 0})
COV_RXIDLE.coverpoint("ppm", bins={"slow": "slow", "fast": "fast"})
COV_RXIDLE.cross("edge_x_ppm", "edge", "ppm")

VALID_K = sorted(arch.code8b10b.VALID_K)


def seeded(dut) -> random.Random:
    dut._log.info("random seed %d", cocotb.RANDOM_SEED)
    return random.Random(cocotb.RANDOM_SEED)


# -------------------------------------------------------------- scoreboard

class Bench:
    """The MAC, the reference model and the per-cycle checks."""

    def __init__(self, dut, max_cycles: int) -> None:
        self.dut = dut
        self.mac = PipeMac(dut)
        self.ref = PipeRef()
        self.max_cycles = max_cycles
        self.out: dict[str, int] = {}
        self.ready = False
        # Transmit reference: words seen per cycle, running disparity.
        self.tx_words: list[tuple | None] = [None, None]
        self.tx_rd = {-1}          # possible running disparities
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
        for key, port in (("phy_status", "pipe_phy_status_o"),
                          ("rx_status", "pipe_rx_status_o"),
                          ("tx_elec_idle", "ser_tx_elec_idle_o")):
            assert out[port] == exp[key], (
                f"cycle {cyc}: {port} expected {exp[key]} got {out[port]} (inputs {ins})")
        if in_rst:
            for port, want in (("pipe_rx_valid_o", 0), ("pipe_rx_status_o", 0),
                               ("pipe_rx_elec_idle_o", 1), ("ser_tx_elec_idle_o", 1),
                               ("pipe_phy_status_o", 1)):
                assert out[port] == want, f"cycle {cyc}: {port} {out[port]} in reset, expected {want}"
        assert out["pipe_rx_valid_o"] == 0, f"cycle {cyc}: RxValid with no receive path"
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
            self.tx_rd = {-1}
            self.tx_words = [None, None]
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
        assert not out["ser_tx_elec_idle_o"], f"cycle {cyc}: word sent but line idle"
        if sent == "loopback":
            self.tx_rd = {-1, 1}
            return
        syms, comp = sent
        got = out["ser_tx_data_o"]
        tries, after = [], set()
        for rd in sorted(self.tx_rd):
            codes, rd_after = arch.tx_encode(syms, comp, rd)
            want = codes[0] | (codes[1] << 10)
            tries.append(want)
            if want == got:
                after.add(rd_after)
        assert after, (f"cycle {cyc}: ser_tx_data_o for {syms} (compliance {bool(comp)}) "
                       f"expected {' or '.join(f'{w:05x}' for w in tries)} got {got:05x}")
        # A word of balanced codes matches at both RDs and keeps both open.
        self.tx_rd = after
        self.checked_words += 1

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


# ------------------------------------------------------------------ tests

@cocotb.test()
async def pipe_reset(dut):
    """Reset values in reset, PhyStatus 16 cycles after rst_i and after
    pipe_reset_ni (one cycle and longer), and a PowerDown change within
    those 16 cycles taking effect with no pulse."""
    b = await start(dut)
    await b.run(5)
    await b.reset("pipe_reset", 1)
    await b.reset("pipe_reset", 6)
    await b.reset("rst_i", 12)
    # Change PowerDown 5 cycles after a reset: no pulse, and P0 holds after.
    b.mac.held_in_reset()
    await b.run(3, reset_n=0)
    b.mac.set(reset_n=1)
    outs = await b.run(5)
    outs += await b.run(30, power_down=P0)
    assert not any(o["pipe_phy_status_o"] for o in outs[17:]), "pulse for a change before ready"
    COV_CTRL.sample(change_before_ready=1)
    # The PCS is in P0: the next change, to P1, is acknowledged.
    await b.power(P1)


@cocotb.test()
async def pipe_detect(dut):
    """Receiver detection with a receiver present and absent, twice each,
    and TxDetectRx in P1 again after it was seen 0."""
    b = await start(dut)
    for present in (1, 0, 0, 1):
        await b.detect(present)
        await b.run(3)
    # Holding TxDetectRx after the pulse starts no second detection.
    b.mac.set(tx_detect_rx=1, ser_tx_rxdet=1)
    await b.until_pulse(80)
    outs = await b.run(100)
    assert not any(o["pipe_phy_status_o"] for o in outs), "second detection without TxDetectRx low"


@cocotb.test()
async def pipe_power(dut):
    """Every PowerDown transition the MAC uses, acknowledged after four
    cycles, with traffic in P0 and the transmitter idle elsewhere."""
    rng = seeded(dut)
    b = await start(dut, rng)
    await b.power(P0)
    for nxt in (P0S, P1, P2):
        await b.transmit(rng, 20)
        await b.power(nxt)
        outs = await b.run(10, tx_elec_idle=1)
        assert all(o["ser_tx_elec_idle_o"] for o in outs[2:]), "transmitter active outside P0"
        COV_CTRL.sample(tx="idle_not_p0")
        if nxt == P2:
            # No beacon: in P2 the transmitter stays idle with TxElecIdle 0.
            outs = await b.run(10, tx_elec_idle=0)
            assert all(o["ser_tx_elec_idle_o"] for o in outs), "beacon or data in P2"
            COV_CTRL.sample(tx="p2_no_beacon")
            await b.run(1, tx_elec_idle=1)
        await b.power(P0)
    assert b.checked_words > 0


async def random_mac(dut, ops: int) -> None:
    """Random legal MAC sequences: resets of both kinds, detection, every
    PowerDown change, traffic, compliance, loopback and idle, against the
    reference model every cycle."""
    rng = seeded(dut)
    b = await start(dut, rng, max_cycles=400 * ops + 2000)
    for _ in range(ops):
        pd = b.mac.ins["pipe_power_down_i"]
        r = rng.random()
        if r < 0.08:
            if rng.random() < 0.5:
                await b.reset("rst_i", rng.randrange(8, 20))
            else:
                await b.reset("pipe_reset", rng.choice([1, rng.randrange(2, 12)]))
        elif pd == P1 and r < 0.3:
            await b.detect(rng.randrange(2))
        elif pd == P0 and r < 0.75:
            await b.transmit(rng, rng.randrange(1, 60), loopback=rng.random() < 0.15)
        elif pd == P0:
            await b.power(rng.choice([P0S, P1, P2]))
            COV_CTRL.sample(tx="idle_not_p0")
        elif r < 0.9:
            await b.power(P0)
        else:
            await b.run(rng.randrange(1, 20))
    dut._log.info("%d cycles, %d transmitted words checked", b.mac.cycle, b.checked_words)
    assert b.checked_words > 0


@cocotb.test()
async def pipe_random(dut):
    """L1: 60 random MAC operations."""
    await random_mac(dut, 60)


@cocotb.test()
async def pipe_random_long(dut):
    """L2: 400 random MAC operations."""
    await random_mac(dut, 400)


@cocotb.test()
async def rx_elec_idle(dut):
    """RxElecIdle follows ser_rx_elec_idle_i through one RX flop and the
    two-flop synchronizer: every level change, in order, two or three clk_i
    edges after the RX edge that registered it; 1 while in reset."""
    rng = seeded(dut)
    # The seed's parity picks the sign: the regression's two explicit seeds
    # cover a fast and a slow RX clock.
    ppm = (1 if cocotb.RANDOM_SEED % 2 else -1) * rng.uniform(50, 600)
    rx_period = start_clocks(dut, ppm, rng.randrange(8000))
    dut._log.info("RX clock %+.1f ppm (%.3f ps)", ppm, rx_period)
    dut.ser_rx_elec_idle_i.value = 1
    dut.ser_rx_data_i.value = 0
    b = Bench(dut, 20000)
    clk_edges = EdgeLog(dut.clk_i)
    launches: list[tuple[int, int]] = []     # (RX edge time, idle value)

    async def drive():
        level = 1
        while True:
            await FallingEdge(dut.ser_rx_clk_i)
            level ^= 1
            dut.ser_rx_elec_idle_i.value = level
            await RisingEdge(dut.ser_rx_clk_i)
            launches.append((now_ps(), level))
            for _ in range(rng.randrange(3, 40)):
                await FallingEdge(dut.ser_rx_clk_i)
                dut.ser_rx_data_i.value = rng.getrandbits(20)

    await b.reset("rst_i", 8)
    seen: list[tuple[int, int]] = []
    last = b.out["pipe_rx_elec_idle_o"]
    assert last == 1, "RxElecIdle low with the line idle"
    cocotb.start_soon(drive())
    for _ in range(3000):
        out = await b.tick()
        v = out["pipe_rx_elec_idle_o"]
        if v != last:
            seen.append((clk_edges.times[-1], v))
            COV_RXIDLE.sample(edge=v, ppm="fast" if ppm > 0 else "slow")
            last = v
    n = min(len(seen), len(launches))
    assert n >= 20, f"only {n} RxElecIdle changes"
    for i in range(n):
        (t_in, v_in), (t_out, v_out) = launches[i], seen[i]
        assert v_in == v_out, f"change {i}: RxElecIdle {v_out}, line {v_in}"
        edges = clk_edges.count(t_in, t_out)
        assert edges in (2, 3), (
            f"change {i}: registered at {t_in} ps, out at {t_out} ps: {edges} clk_i edges")
