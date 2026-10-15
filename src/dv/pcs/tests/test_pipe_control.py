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

import cocotb
from cocotb.triggers import FallingEdge, RisingEdge

from models.pcie_pcs_model import P0, P0S, P1, P2
from regress.funcov import Covergroup
from tb.pcs_bench import COV_CTRL, Bench, regress_seed, seeded, start
from tb.pcs_env import EdgeLog, now_ps, start_clocks
from tb.pcs_rxbench import RX_LATENCY_MAX

# RxElecIdle edges seen through the crossing, by RX clock offset sign.
COV_RXIDLE = Covergroup("pcs_rx_elec_idle")
COV_RXIDLE.coverpoint("edge", bins={"enter": 1, "exit": 0})
COV_RXIDLE.coverpoint("ppm", bins={"slow": "slow", "fast": "fast"})
COV_RXIDLE.cross("edge_x_ppm", "edge", "ppm")


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
    """RxElecIdle travels with the received words (RECEIVE.md, Electrical
    idle): every level change of ser_rx_elec_idle_i reaches PIPE, in order,
    with the first word received at the new level, so within the RX latency
    target (12 cycles) of the RX edge that registered it; 1 while in reset."""
    rng = seeded(dut)
    # The seed's parity picks the sign: the regression's two explicit seeds
    # cover a fast and a slow RX clock.
    ppm = (1 if regress_seed() % 2 else -1) * rng.uniform(50, 600)
    rx_period = start_clocks(dut, ppm, rng.randrange(8000))
    dut._log.info("RX clock %+.1f ppm (%.3f ps)", ppm, rx_period)
    dut.ser_rx_elec_idle_i.value = 1
    dut.ser_rx_data_i.value = 0
    b = Bench(dut, 20000)
    # The line leaves electrical idle with all-zero words: no comma, so no
    # lock and no receive status to compare.
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
    worst = 0
    for i in range(n):
        (t_in, v_in), (t_out, v_out) = launches[i], seen[i]
        assert v_in == v_out, f"change {i}: RxElecIdle {v_out}, line {v_in}"
        edges = clk_edges.count(t_in, t_out)
        worst = max(worst, edges)
        assert 1 <= edges <= RX_LATENCY_MAX, (
            f"change {i}: registered at {t_in} ps, out at {t_out} ps: {edges} clk_i edges")
    dut._log.info("%d RxElecIdle changes, at most %d clk_i edges after the RX edge", n, worst)
