"""cocotb environment of pcie_rc_tl (wiki/src/dv/rc_tl/DV_PLAN.md).

One coroutine drives and checks: `tick()` applies the driven inputs for a
cycle, waits for the falling edge of clk_i (after the rising edge that used
them), steps the reference model with the same inputs and compares every
output with it. So every cycle of every test is checked against the model:
cmd_ready_o, rsp_valid_o and rsp_o exactly, and the outputs of the blocks
not built yet at their reset values.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

from regress.funcov import Covergroup

sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
from pcie_rc_tl_model import (ENDPOINT_OPS, ERR_CLR, LINK_DOWN, LOCAL_UR, OK,  # noqa: E402
                              ST_NAMES, Cmd, FrontEnd, Params, Rsp, failed_check)

# Inputs of the blocks not built yet, with their widths: driven at random by
# the tests that toggle them, ignored by this build.
IDLE_INPUTS = {
    "msi_ready_i": 1, "tlp_tx_ready_i": 1, "tlp_rx_valid_i": 1, "tlp_rx_data_i": 32,
    "tlp_rx_sop_i": 1, "tlp_rx_eop_i": 1, "fc_ph_avail_i": 8, "fc_pd_avail_i": 12,
    "fc_nph_avail_i": 8, "fc_npd_avail_i": 12, "fc_cplh_avail_i": 8, "fc_cpld_avail_i": 12,
}
# Outputs of the blocks not built yet: 0 always (MAS, Reset and link down).
QUIET_OUTPUTS = ("msi_valid_o", "msi_o", "tlp_tx_valid_o", "tlp_tx_data_o", "tlp_tx_sop_o",
                 "tlp_tx_eop_o", "fc_rel_p_o", "fc_rel_pd_o", "fc_rel_np_o", "fc_rel_npd_o")

MAX_WAIT = 16    # cycles a command may wait to be taken or answered


def op_bin(op: int) -> str:
    return str(op) if op <= ERR_CLR else "undef"


# Functional coverage of the front end (DV plan, Functional coverage).
COV = Covergroup("rc_tl_cmd")
COV.coverpoint("op", bins={str(i): str(i) for i in range(10)} | {"undef": "undef"})
COV.coverpoint("check", bins={"pass": 0, "op": 2, "len": 3, "addr": 4, "4k": 5, "be": 6,
                              "route": 7})
COV.coverpoint("status", bins={"OK": OK, "LOCAL_UR": LOCAL_UR, "LINK_DOWN": LINK_DOWN})
COV.coverpoint("dl_up", bins={"down": 0, "up": 1})
COV.cross("op_x_status", "op", "status", unreachable=(
    [(str(op), "OK") for op in (0, 1, 2, 3, 6, 7, 8, 9)] + [("undef", "OK")]
    + [(str(op), "LINK_DOWN") for op in range(4, 10)] + [("undef", "LINK_DOWN")]))
COV.cross("op_x_check", "op", "check", unreachable=(
    [(str(op), "op") for op in range(10)]
    + [("undef", c) for c in ("pass", "len", "addr", "4k", "be", "route")]
    + [(str(op), "4k") for op in (0, 1, 3, 4, 5, 6, 7, 8, 9)]
    + [(str(op), "route") for op in range(4, 10)]
    + [(str(op), "addr") for op in (8, 9)]))
# An endpoint command past the checks, interim (MAS, Leaf blocks).
COV.cross("ep_x_dl_up", "status", "dl_up", unreachable=[("OK", "down"), ("OK", "up"),
                                                        ("LINK_DOWN", "up"),
                                                        ("LOCAL_UR", "down")])
# Header access: each DW with RW fields written, and the byte-enable count.
COV.coverpoint("hdr_wr_dw", bins={"cmd": 1, "cls": 3, "bus": 6, "mem": 8,
                                  "ro": lambda v: v not in (1, 3, 6, 8)})
COV.coverpoint("hdr_wr_be", bins={"none": 0, "one": [1, 2, 4, 8], "some": [3, 5, 6, 9, 10, 12],
                                  "three": [7, 11, 13, 14], "all": 15})
COV.cross("hdr_wr_dw_x_be", "hdr_wr_dw", "hdr_wr_be")


class Env:
    """Drives pcie_rc_tl_tb through its ports and checks it every cycle."""

    def __init__(self, dut, seed: int = 0) -> None:
        self.dut = dut
        self.rng = random.Random(seed)
        self.p = Params()
        self.fe = FrontEnd(self.p)
        self.cycle = 0
        self.toggle_idle = False      # drive IDLE_INPUTS at random each cycle
        self.drv = {"rst_i": 1, "cmd_valid_i": 0, "cmd_i": 0, "dl_up_i": 1}
        self.drv |= {name: 0 for name in IDLE_INPUTS}
        self.takes: list[tuple[int, Cmd, Rsp]] = []    # (cycle, command, its beat)

    # ------------------------------------------------------------ set-up

    async def start(self, rst_cycles: int = 8) -> None:
        cocotb.start_soon(Clock(self.dut.clk_i, 8, unit="ns").start())
        await self.reset(rst_cycles)

    async def reset(self, cycles: int = 8) -> None:
        self.drv["rst_i"] = 1
        self.drv["cmd_valid_i"] = 0
        for _ in range(cycles):
            await self.tick()
        prm = int(self.dut.params_o.value)
        p = Params(vendor_id=prm & 0xFFFF, device_id=(prm >> 16) & 0xFFFF,
                   revision_id=(prm >> 32) & 0xFF, host_mem_addr_w=(prm >> 40) & 0x1F)
        if p != self.p:
            # The model is in reset like the DUT: a fresh one with the
            # parameters is the same state.
            self.p = p
            self.fe = FrontEnd(p)
        self.drv["rst_i"] = 0

    # ------------------------------------------------------------ the cycle

    async def tick(self) -> tuple[bool, Rsp | None]:
        """One cycle: apply the inputs, wait for the falling edge, step the
        model and compare every output. Returns (taken, beat)."""
        d = self.dut
        if self.toggle_idle:
            for name, width in IDLE_INPUTS.items():
                self.drv[name] = self.rng.getrandbits(width)
        applied = dict(self.drv)
        for name, value in applied.items():
            getattr(d, name).value = value
        await FallingEdge(d.clk_i)
        cmd = Cmd.unpack(applied["cmd_i"])
        check = failed_check(cmd, self.fe.hdr, self.p)    # on the state before the edge
        ready, beat, take = self.fe.step(applied["rst_i"], applied["cmd_valid_i"], cmd,
                                         applied["dl_up_i"])
        where = f"cycle {self.cycle}"
        self.expect(where, "cmd_ready_o", ready)
        self.expect(where, "rsp_valid_o", int(beat is not None))
        got = int(d.rsp_o.value)
        want = beat.pack() if beat is not None else 0
        assert got == want, (f"{where}: rsp_o expected {beat} got {Rsp.unpack(got)}"
                             + (f" for {cmd}" if take else ""))
        for name in QUIET_OUTPUTS:
            self.expect(where, name, 0)
        self.expect(where, "tlp_rx_ready_o", 1 - applied["rst_i"])
        if take:
            assert beat is not None
            self.takes.append((self.cycle, cmd, beat))
            self.sample(cmd, check, beat, applied["dl_up_i"])
        self.cycle += 1
        return take, beat

    async def apply(self) -> None:
        """Apply the driven inputs now and settle, inside the current cycle,
        to read combinational outputs; the next tick() keeps them."""
        for name, value in self.drv.items():
            getattr(self.dut, name).value = value
        await Timer(1, unit="ns")

    def expect(self, where: str, name: str, want: int) -> None:
        got = int(getattr(self.dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"

    @staticmethod
    def sample(c: Cmd, check: int, beat: Rsp, dl_up: int) -> None:
        kw = {"op": op_bin(c.op), "check": check, "status": beat.status}
        if c.op in ENDPOINT_OPS and check == 0:
            kw["dl_up"] = dl_up
        if c.op == 5 and beat.status == OK:
            kw |= {"hdr_wr_dw": (c.addr >> 2) & 0x3FF, "hdr_wr_be": c.first_be}
        COV.sample(**kw)

    # ------------------------------------------------------------ commands

    async def command(self, c: Cmd, dl_up: int | None = None) -> Rsp:
        """Offer c until taken; return its one result beat, which must come
        in the cycle after the take."""
        if dl_up is not None:
            self.drv["dl_up_i"] = dl_up
        self.drv["cmd_valid_i"] = 1
        self.drv["cmd_i"] = c.pack()
        for _ in range(MAX_WAIT):
            take, beat = await self.tick()
            if take:
                break
        else:
            raise AssertionError(f"cycle {self.cycle}: {c} not taken in {MAX_WAIT} cycles")
        self.drv["cmd_valid_i"] = 0
        assert beat is not None and beat.last == 1, f"{c}: no last beat after the take"
        return beat

    async def expect_cmd(self, c: Cmd, status: int, data: int = 0,
                         dl_up: int | None = None) -> None:
        """c finishes with one beat of this status and data (a spec value,
        given by the test, besides the model's per-cycle check)."""
        beat = await self.command(c, dl_up)
        assert beat == Rsp(status, data), (f"cycle {self.cycle}: {c}: expected "
                                           f"{ST_NAMES[status]} 0x{data:08x}, got {beat}")

    async def hdr_rd(self, idx: int) -> int:
        beat = await self.command(Cmd(4, idx << 2))
        assert beat.status == OK, f"HDR_RD {idx}: {beat}"
        return beat.data

    async def hdr_wr(self, idx: int, data: int, be: int = 0xF) -> None:
        beat = await self.command(Cmd(5, idx << 2, first_be=be, data=data))
        assert beat == Rsp(OK), f"HDR_WR {idx}: {beat}"

    async def idle(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.tick()
