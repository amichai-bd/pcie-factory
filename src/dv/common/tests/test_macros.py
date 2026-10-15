"""Shared vocabulary macro tests (wiki/src/dv/common/common_macros.md).

The top is tb/common_macros_fixture.sv: one call of every register,
register-array, memory, crossing and assertion form. Inputs change at
falling clk_i edges; outputs are compared with models/common_macros_model.py
after every rising edge. The assertions stay quiet under this legal
stimulus; a failing assertion stops the simulation, which fails the test.

Provenance: the directed reset/capture/hold sequence is adapted from
nand2anything src/common/dv/clock_reset/test_macros.py (commit 085ef4b),
adapted with the owner's permission.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, Timer

from models.common_macros_model import (ARRAY, ARST_EN_VAL, ARST_VAL, Inputs, MacroModel,
                                        lane_word)
from regress.funcov import Covergroup

CLK_NS = 10
SRC_CLK_NS = 14

# Functional coverage (DV plan): reset/enable combinations at each edge, and
# memory port activity including same-address read-during-write.
COV = Covergroup("common_macros")
COV.coverpoint("ctrl", bins={"rst": lambda c: c[0] == 1, "en": lambda c: c == (0, 1),
                             "hold": lambda c: c == (0, 0)})
COV.coverpoint("mem", bins={"write": "w", "read_en": "r", "collide": "c", "idle": "i"})
COV.coverpoint("lanes", bins={"none": 0, "low": 1, "high": 2, "both": 3})


def drive(dut, i: Inputs) -> None:
    dut.rst_i.value = i.rst
    dut.arst_i.value = i.arst
    dut.en_i.value = i.en
    dut.data_i.value = i.data
    dut.we_i.value = i.we
    dut.waddr_i.value = i.waddr
    dut.lanes_i.value = i.lanes
    dut.re_i.value = i.re
    dut.raddr_i.value = i.raddr


def observed(dut) -> dict[str, int]:
    out = {name: int(getattr(dut, f"{name}_o").value)
           for name in ("dff", "dff_en", "rst", "rst_val", "rst_en", "arst_val", "arst_en",
                        "rd", "rd_en", "wrd", "meta", "meta_val", "meta_arst", "rst_sync", "rst_sync3")}
    arr, arr_rst = int(dut.arr_en_o.value), int(dut.arr_rst_en_o.value)
    for k in range(ARRAY):
        out[f"arr_en_{k}"] = (arr >> (8 * k)) & 0xFF
        out[f"arr_rst_en_{k}"] = (arr_rst >> (8 * k)) & 0xFF
    return out


def compare(model: MacroModel, got: dict[str, int], cycle: int) -> None:
    for name, want in model.outputs().items():
        if want is not None and got[name] != want:
            raise AssertionError(f"cycle {cycle}: {name}_o expected {want:#x} got {got[name]:#x}")


async def start(dut) -> None:
    drive(dut, Inputs(rst=1, arst=1))
    dut.src_rst_i.value = 1
    dut.pulse_i.value = 0
    dut.forbidden_i.value = 0
    cocotb.start_soon(Clock(dut.clk_i, CLK_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.src_clk_i, SRC_CLK_NS, unit="ns").start())


async def step(dut, model: MacroModel, i: Inputs, cycle: int) -> dict[str, int]:
    """Drive ``i`` at a falling edge, take the rising edge, check outputs."""
    await FallingEdge(dut.clk_i)
    drive(dut, i)
    await RisingEdge(dut.clk_i)
    model.edge(i)
    await ReadOnly()
    got = observed(dut)
    compare(model, got, cycle)
    return got


@cocotb.test()
async def contract(dut):
    """Directed: reset values, capture, enable hold, asynchronous reset,
    arrays, read-during-write, lane enables, synchronizer latency and reset
    value, reset synchronizer, pulse crossing."""
    await start(dut)
    model = MacroModel()
    await Timer(1, "ns")  # asynchronous assertion, before any clock edge
    got = (int(dut.arst_val_o.value), int(dut.arst_en_o.value))
    assert got == (ARST_VAL, ARST_EN_VAL), f"async reset: expected 0x5a/0xc3 got {got}"
    assert int(dut.rst_sync_o.value) == 1, "COMMON_RESET_SYNC: not asserted before any edge"
    assert int(dut.rst_sync3_o.value) == 1, "COMMON_RESET_SYNC_STAGES: not asserted before any edge"
    assert int(dut.meta_arst_o.value) == 0, "COMMON_METAFLOP_ARST: not reset before any edge"
    n = 0
    await step(dut, model, Inputs(rst=1, arst=1, en=1, data=0x11), n)
    n += 1
    await step(dut, model, Inputs(en=1, data=0x12), n)          # capture
    n += 1
    got = await step(dut, model, Inputs(en=0, data=0x34), n)    # hold
    assert got["dff_en"] == 0x12 and got["dff"] == 0x34, got
    # Asynchronous assertion between edges.
    await FallingEdge(dut.clk_i)
    dut.arst_i.value = 1
    await Timer(1, "ns")
    assert int(dut.arst_val_o.value) == ARST_VAL and int(dut.arst_en_o.value) == ARST_EN_VAL
    assert int(dut.rst_sync_o.value) == 1, "COMMON_RESET_SYNC: not asserted between edges"
    assert int(dut.rst_sync3_o.value) == 1, "COMMON_RESET_SYNC_STAGES: not asserted between edges"
    assert int(dut.meta_arst_o.value) == 0, "COMMON_METAFLOP_ARST: not reset between edges"
    model.async_reset()
    n += 1
    got = await step(dut, model, Inputs(arst=0, data=0x35), n)
    assert got["rst_sync"] == 1, "COMMON_RESET_SYNC released one edge after the fall"
    n += 1
    got = await step(dut, model, Inputs(data=0x36), n)
    assert got["rst_sync"] == 0, "COMMON_RESET_SYNC not released two edges after the fall"
    assert got["rst_sync3"] == 1, "COMMON_RESET_SYNC_STAGES (3) released two edges after the fall"
    n += 1
    got = await step(dut, model, Inputs(data=0x37), n)
    assert got["rst_sync3"] == 0, "COMMON_RESET_SYNC_STAGES (3) not released three edges after the fall"
    # Memory: write, then read the same address while writing new data.
    for k, data in enumerate((0x40, 0x41)):
        n += 1
        await step(dut, model, Inputs(we=1, waddr=3, data=data, lanes=0b11 if k == 0 else 0b01,
                                      re=1, raddr=3), n)
    n += 1
    got = await step(dut, model, Inputs(raddr=3), n)
    assert got["rd"] == 0x41, got
    assert got["wrd"] == (lane_word(0x40) & 0xFF00) | (lane_word(0x41) & 0x00FF), got
    # Level synchronizers: a new value shows two edges later.
    for k in range(4):
        n += 1
        await step(dut, model, Inputs(data=0x77), n)
    # Synchronizer reset values.
    n += 1
    got = await step(dut, model, Inputs(rst=1, data=0x77), n)
    assert got["meta"] == 0 and got["meta_val"] == 0x96, got
    n += 1
    await step(dut, model, Inputs(data=0), n)

    # Pulse crossings: each source pulse gives one destination pulse, on
    # both forms.
    seen = 0
    seen_arst = 0

    async def count():
        nonlocal seen, seen_arst
        while True:
            await RisingEdge(dut.clk_i)
            await ReadOnly()
            seen += int(dut.pulse_o.value)
            seen_arst += int(dut.pulse_arst_o.value)

    cocotb.start_soon(count())
    await FallingEdge(dut.src_clk_i)
    dut.src_rst_i.value = 0
    for _ in range(5):
        await FallingEdge(dut.src_clk_i)
        dut.pulse_i.value = 1
        await FallingEdge(dut.src_clk_i)
        dut.pulse_i.value = 0
        await ClockCycles(dut.src_clk_i, 3)
    await ClockCycles(dut.clk_i, 8)
    assert seen == 5, f"pulse crossing: expected 5 destination pulses got {seen}"
    assert seen_arst == 5, f"COMMON_PULSE_SYNC_ARST: expected 5 destination pulses got {seen_arst}"

    # COMMON_PULSE_SYNC_ARST: an asynchronous destination reset drops a high
    # output pulse between edges; the synchronous form keeps it to the edge.
    await FallingEdge(dut.src_clk_i)
    dut.pulse_i.value = 1
    await FallingEdge(dut.src_clk_i)
    dut.pulse_i.value = 0
    for _ in range(6):
        await RisingEdge(dut.clk_i)
        await ReadOnly()
        if int(dut.pulse_arst_o.value):
            break
    else:
        raise AssertionError("COMMON_PULSE_SYNC_ARST: no destination pulse for the sixth event")
    await Timer(CLK_NS // 4, "ns")
    dut.arst_i.value = 1
    await ReadOnly()
    got = (int(dut.pulse_o.value), int(dut.pulse_arst_o.value))
    assert got == (1, 0), (f"asynchronous reset between edges: (pulse_o, pulse_arst_o) {got}, "
                           "expected (1, 0)")


@cocotb.test()
async def random_ops(dut):
    """Random reset, enable, data and memory traffic against the model."""
    seed = cocotb.RANDOM_SEED
    rng = random.Random(seed)
    dut._log.info("seed %d", seed)
    await start(dut)
    model = MacroModel()
    cycles = 2000
    for n in range(cycles):
        i = Inputs(rst=int(rng.random() < 0.05), arst=int(rng.random() < 0.05),
                   en=rng.randrange(2), data=rng.randrange(256), we=rng.randrange(2),
                   waddr=rng.randrange(8), lanes=rng.randrange(4), re=rng.randrange(2),
                   raddr=rng.randrange(8))
        if rng.random() < 0.2:
            i.raddr = i.waddr
        mem = "c" if i.we and i.raddr == i.waddr else "w" if i.we else "r" if i.re else "i"
        if i.we:
            COV.sample(ctrl=(i.rst, i.en), mem=mem, lanes=i.lanes)
        else:
            COV.sample(ctrl=(i.rst, i.en), mem=mem)
        await step(dut, model, i, n)
