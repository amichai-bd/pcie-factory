"""cocotb environment of the rc back-to-back e2e (wiki/src/dv/rc/DV_PLAN.md;
wiki/src/arch/rc/HAS.md, TL_SEQ.md; the rc_seq programs page).

The top ``pcie_rc_e2e_tb`` holds the root-port stack ``pcie_rc`` and the
endpoint ``pcie_core`` on one clock, their serial lines joined through one
register each way. One coroutine runs every cycle: at the falling edge of
``clk_i`` it drives every input, then, once the design has settled
(ReadOnly), reads every output; a handshake seen then belongs to this
cycle.

- The **host side** is ``rc`` alone: ``rp_start_i`` is held at 1 from
  reset, so ``rc_seq`` runs its program (``PROGRAM``) once; nothing else
  drives the root port.
- The **host model shadow** (``HostShadow`` of the core DV,
  wiki/src/dv/core/HOST.md, shadow mode) sees what crosses ``rc``'s
  ``link`` upper stream both ways, every command ``rc_tl`` takes, every
  result beat and every MSI capture ``rc_seq`` takes (the rc MAS nets,
  tapped), in the HOST.md call order, and checks both sides.
- The **endpoint side** keeps the core e2e's application model (``App`` of
  the core DV's tb/core_e2e_env.py) with the hooks the programs need
  (PROGRAMS, Boundary): BAR0 as memory, the DMA doorbell at 0xF00 to 0xF0C
  with a queue of doorbells, the never-answered DW at 0xF10; its stream
  checks, and the cfg architecture-model shadow (``CfgShadow``) on the
  configuration traffic, the endpoint's tl_err events and its error
  messages; and the core HAS reset values.
- The **line** (tb top: ``mon_*_tx_o``, ``*_sub_*``): a test may put a
  model on either direction: the rewriter of models/pcie_rc_line_model.py
  (TLPs replaced on the line), the link error injector of the link DV, or
  a cut (electrical idle both ways: a link down).
"""

from __future__ import annotations

import os
import random
import re
import sys
from collections import deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly

from regress.funcov import Covergroup

_DV = Path(__file__).resolve().parents[1]
_SRC = _DV.parents[1]
for _p in (_DV / "models", _SRC / "dv" / "core" / "tb", _SRC / "dv" / "link" / "models"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))
import core_e2e_env as core  # noqa: E402  (the core e2e's application model and cfg shadow)
import pcie_core_host_model as hm  # noqa: E402
import pcie_core_host_shadow as hs  # noqa: E402
import pcie_core_host_tlp as ht  # noqa: E402
import pcie_link_injector_model as inj  # noqa: E402
import pcie_rc_line_model as lm  # noqa: E402

CLK_NS = 8
RESET_CYCLES = 16
CheckError = core.CheckError

# Program ids (rc_seq MAS, Programs) and the address plan (PROGRAMS).
ENUM, MMIO, DMA_MSI, ERRORS, INJECT = 0, 1, 2, 3, 5
PROGRAM_NAMES = {ENUM: "ENUM", MMIO: "MMIO", DMA_MSI: "DMA_MSI", ERRORS: "ERRORS",
                 INJECT: "INJECT"}
BAR0 = 0x1000_0000
DB_SRC, DB_DST, DB_LEN, DB_RING, NO_ANSWER = 0xF00, 0xF04, 0xF08, 0xF0C, 0xF10
RUN_FLAG = 0xFFC
INJ_CA, INJ_POISONED, INJ_MALFORMED, INJ_UNEXPECTED, INJ_FATAL = (
    0x100, 0x104, 0x108, 0x10C, 0x110)

# rc parameters at the tb defaults (rc HAS, Parameters).
RC_PARAMS = {"vendor_id": 0, "device_id": 0, "revision_id": 0, "host_mem_base": 0,
             "host_mem_addr_w": 12, "msi_addr": 0xFEE0_0000}
TL_OPS = ("CFG_RD", "CFG_WR", "MEM_RD", "MEM_WR", "HDR_RD", "HDR_WR", "HOST_RD", "HOST_WR",
          "ERR_RD", "ERR_CLR")

# Reset values (rc HAS, Clocks and reset): rc while rp_rst_i is 1.
RESET_RP = {"rp_done_o": 0, "rp_pass_o": 0, "rp_fail_step_o": 0, "rp_dl_up_o": 0,
            "rp_dl_state_o": 0, "rp_dl_err_o": 0, "rp_link_up_o": 0, "rp_ltssm_state_o": 0,
            "rp_rcv_err_o": 0, "rp_ser_tx_elec_idle_o": 1}

# ------------------------------------------------------------ coverage

COV_RUN = Covergroup("rc_e2e_run")
COV_RUN.coverpoint("program", bins={n: n for n in PROGRAM_NAMES.values()})
COV_RUN.coverpoint("op", bins={n: n for n in TL_OPS})
COV_RUN.coverpoint("status", bins={n: n for n in hs.RESULT_NAMES})
COV_RUN.cross("op_x_status", "op", "status", unreachable=[
    (o, s) for o in TL_OPS for s in hs.RESULT_NAMES
    # TL_SEQ results: a local command finishes OK or LOCAL_UR; a MEM_WR has
    # no completion; CRS answers configuration requests only.
    if (o in TL_OPS[4:] and s not in ("OK", "LOCAL_UR"))
    or (o == "MEM_WR" and s not in ("OK", "LOCAL_UR", "LINK_DOWN"))
    or (o == "MEM_RD" and s == "CRS")])

COV_LINE = Covergroup("rc_e2e_line")
COV_LINE.coverpoint("rewrite", bins={n: n for n in (
    "split", "ca", "poisoned", "malformed_cpl", "stale_copy", "malformed_req")})
COV_LINE.coverpoint("splits", bins={"two": 2})   # 128 bytes split on 64: two at most
COV_LINE.coverpoint("event", bins={n: n for n in ("link_down", "hot_reset", "replay")})

COV_ERR = Covergroup("rc_e2e_err")
COV_ERR.coverpoint("bit", bins={f"b{b}": b for b in range(13)})


# ------------------------------------------------------------ endpoint app

class EpApp(core.App):
    """The core e2e application model with the programs' boundary
    (PROGRAMS, Boundary): BAR0 as memory (the core App's); a write that
    covers BAR0 0xF0C queues a doorbell (source at 0xF00, destination at
    0xF04, length in bytes at 0xF08, up to 128), served in order: a bus-
    master read of the source, then, if it completed successfully, a write
    of the destination and, once that write is taken, MSI vector 0; any
    number of doorbells wait in the queue. A read of 0xF10 is never
    answered."""

    def __init__(self, env):
        super().__init__(env)
        self.on_cq_write.append(self._doorbell)
        self.jobs: deque = deque()
        self.job: dict | None = None
        self.withheld: set[int] = set()
        self.copies: list[dict] = []

    def _dw(self, off: int) -> int:
        return int.from_bytes(self.bar0[off:off + 4], "little")

    def _doorbell(self, off: int, nbytes: int) -> None:
        if off <= DB_RING < off + nbytes:
            self.jobs.append({"src": self._dw(DB_SRC), "dst": self._dw(DB_DST),
                              "n": self._dw(DB_LEN), "rung": self.env.cycle})

    def tick(self) -> None:
        for d in list(self.cc_due):                  # 0xF10: never answered
            h = self.handles.get(d[1])
            if h is not None and h["pkt"]["addr"] == NO_ANSWER:
                self.cc_due.remove(d)
                self.withheld.add(d[1])
        j = self.job
        if j is None:
            if self.jobs and self.env.dl_up:
                j = self.job = self.jobs.popleft()
                n = j["n"]
                if not 1 <= n <= 128:
                    raise CheckError(f"doorbell of {n} bytes: 1 to 128 (PROGRAMS, Boundary)")
                j["rd"] = self.read(j["src"], n)
                j["state"] = "read"
            return
        if j["state"] == "read" and j["rd"].done:
            r = j["rd"]
            if r.status != core.RC_SC:
                j["result"] = "read failed"
                self.copies.append(j)
                self.job = None
                return
            j["wr"] = self.write(j["dst"], self.read_bytes(r))
            j["state"] = "write"
        if j["state"] == "write" and any(x is j["wr"] for x in self.rq_taken):
            j["irq"] = self.irq(0)
            j["state"] = "irq"
        if j["state"] == "irq" and j["irq"]["done"] is not None:
            j["result"] = core.IRQ_NAMES.get(j["irq"]["status"])
            self.copies.append(j)
            self.job = None

    def idle(self) -> bool:
        busy = {h for h in self.handles if h not in self.withheld}
        return (not self.rq_q and self.rq_cur is None and not self.tags_busy and not busy
                and not self.irq_q and self.irq_open is None and self.cq_pkt is None
                and self.rc_pkt is None and self.job is None and not self.jobs)

    def reset(self) -> None:
        super().reset()
        self.withheld.clear()
        self.jobs.clear()
        self.job = None
        self.rq_q.clear()
        self.irq_q.clear()


# ------------------------------------------------------------ line models

class Cut:
    """The line cut: electrical idle to the receiver."""

    def step(self, word: int, ei: bool) -> tuple[int, bool]:
        return 0, True


class InjectorLine:
    """The link DV's error injector as a line model."""

    def __init__(self, injector: inj.Injector):
        self.inj, self.t = injector, 0

    def step(self, word: int, ei: bool) -> tuple[int, bool]:
        self.t += 1
        return self.inj.step(word, ei, self.t)


class Line:
    """One direction: ``model`` None leaves the testbench register."""

    def __init__(self, env: "Env", name: str):
        self.env, self.name = env, name
        self.model = None
        self.out: tuple[int, bool] | None = None
        self.mon = env.rd[f"mon_{name}_tx_o"]
        self.mon_ei = env.rd[f"mon_{name}_ei_o"]

    def drive(self) -> None:
        e = self.env
        if self.out is None:
            e.wr(f"{self.name}_sub_i", 0)
            return
        e.wr(f"{self.name}_sub_data_i", self.out[0])
        e.wr(f"{self.name}_sub_ei_i", int(self.out[1]))
        e.wr(f"{self.name}_sub_i", 1)

    def sample(self) -> None:
        if self.model is None:
            self.out = None
            return
        try:
            self.out = self.model.step(self.mon(), bool(self.mon_ei()))
        except lm.LineError as exc:
            raise CheckError(f"line {self.name}: {exc}") from None


# ------------------------------------------------------------ rc monitor

class RcMonitor:
    """The rc taps into the host model shadow, in the HOST.md call order."""

    def __init__(self, env: "Env"):
        self.env = env
        self.shadow: hs.HostShadow | None = None
        self.tx: list[int] = []
        self.rx: list[int] = []
        self.dl_up = False
        self.tx_log: list[tuple[int, bytes]] = []
        self.rx_log: list[tuple[int, bytes]] = []
        self.results: list[tuple[int, int, int, int, int]] = []   # (cycle, op, status, data, last)
        self.cmds: deque = deque()                # ops awaiting their last result
        self.cmd_log: list[tuple[int, int, int, int, int, int, int]] = []
        self.msis: list[tuple[int, int]] = []
        self.on_tx = []                           # callbacks f(cycle, raw)
        self.on_cmd = []                          # f(op, addr, data)
        self.on_result = []                       # f(op, status, data, last)

    def build(self, **kw) -> None:
        self.shadow = hs.HostShadow(**{**RC_PARAMS, **kw})
        self.tx, self.rx, self.dl_up = [], [], False
        self.cmds.clear()

    def sample(self) -> None:
        e, rd = self.env, self.env.rd
        sh = self.shadow
        dl_up = bool(rd["rp_dl_up_o"]())
        try:
            if dl_up and not self.dl_up:
                sh.observe_dl_up(True)
            if rd["mon_tx_valid_o"]() and rd["mon_tx_ready_o"]():
                if rd["mon_tx_sop_o"]():
                    self.tx = []
                self.tx.append(rd["mon_tx_data_o"]())
                if rd["mon_tx_eop_o"]():
                    raw = ht.from_beats(self.tx)
                    self.tx = []
                    self.tx_log.append((e.cycle, raw))
                    for f in self.on_tx:
                        f(e.cycle, raw)
                    sh.observe_tx(raw)
            if rd["mon_rx_valid_o"]() and rd["mon_rx_ready_o"]():
                if rd["mon_rx_sop_o"]():
                    self.rx = []
                self.rx.append(rd["mon_rx_data_o"]())
                if rd["mon_rx_eop_o"]():
                    raw = ht.from_beats(self.rx)
                    self.rx = []
                    self.rx_log.append((e.cycle, raw))
                    e.cfg_shadow_rx(raw)
                    sh.observe_rx(raw)
            if rd["mon_cmd_valid_o"]() and rd["mon_cmd_ready_o"]():
                c = (rd["mon_cmd_op_o"](), rd["mon_cmd_addr_o"](), rd["mon_cmd_len_o"](),
                     rd["mon_cmd_first_be_o"](), rd["mon_cmd_last_be_o"](), rd["mon_cmd_data_o"]())
                self.cmd_log.append((e.cycle,) + c)
                self.cmds.append(c[0])
                for f in self.on_cmd:
                    f(c[0], c[1], c[5])
                sh.observe_command(*c)
            if rd["mon_rsp_valid_o"]():
                st, data, last = rd["mon_rsp_status_o"](), rd["mon_rsp_data_o"](), rd["mon_rsp_last_o"]()
                op = self.cmds[0] if self.cmds else -1
                if last and self.cmds:
                    self.cmds.popleft()
                    if op < len(TL_OPS):
                        COV_RUN.sample(op=TL_OPS[op], status=hs.RESULT_NAMES[st])
                self.results.append((e.cycle, op, st, data, last))
                for f in self.on_result:
                    f(op, st, data, last)
                sh.observe_result(st, data, bool(last))
            if rd["mon_msi_valid_o"]() and rd["mon_msi_ready_o"]():
                m = (rd["mon_msi_offset_o"](), rd["mon_msi_data_o"]())
                self.msis.append(m)
                sh.observe_msi(*m)
            if self.dl_up and not dl_up:
                sh.observe_dl_up(False)
        except hm.HostCheckError as exc:
            e.fail(f"host model shadow: {exc}")
        self.dl_up = dl_up
        sh.tick(1)


# ------------------------------------------------------------ env

class Env:
    """One back-to-back run; ``seed`` draws everything random."""

    def __init__(self, dut, seed: int, shadow_kw: dict | None = None):
        self.dut = dut
        self.log = dut._log
        self.rng = random.Random(seed)
        self.seed = seed
        self.s = {n: getattr(dut, n) for n in dir(dut)
                  if n.endswith(("_i", "_o")) and not n.startswith("_")}
        self.rd = {n: (lambda f=h._handle.get_signal_val_binstr: int(f(), 2))
                   for n, h in self.s.items()}
        self._last: dict[str, int] = {}
        p = {k: self.rd[f"cfg_{k}_o"]() for k in (
            "fc_ph", "fc_pd", "fc_nph", "fc_npd", "cpl_timeout_div", "rp_cpl_timeout_div",
            "program", "bar0_addr_w", "msix_table_size", "vendor_id", "device_id",
            "subsys_vendor_id", "subsys_id", "class_code", "revision_id")}
        self.p = p
        self.program = p["program"]
        self.bar0_addr_w = p["bar0_addr_w"]
        # rc_tl's completion timeout, N cycles from the request's sop (rc_tl
        # MAS, requester); the shadow times from the last beat, so its
        # window starts N less the longest request (4 beats) and ends N
        # plus a margin for the result beat.
        self.rc_timeout = (1 << 21) // p["rp_cpl_timeout_div"]
        self.log.info("rc e2e: COCOTB_RANDOM_SEED %s, test seed %d, PROGRAM %d (%s), "
                      "parameters %s", os.environ.get("COCOTB_RANDOM_SEED", "-"), seed,
                      self.program, PROGRAM_NAMES.get(self.program), p)
        self.params = core.cfga.Params(bar0_addr_w=p["bar0_addr_w"],
                                       msix_table_size=p["msix_table_size"],
                                       vendor_id=p["vendor_id"], device_id=p["device_id"],
                                       subsys_vendor_id=p["subsys_vendor_id"],
                                       subsys_id=p["subsys_id"], class_code=p["class_code"],
                                       revision_id=p["revision_id"])
        self.shadow_kw = {"timeout_min": self.rc_timeout - 4, "timeout_max": self.rc_timeout + 8,
                          "msi_depth": 4, "msi_depth_exact": True,
                          "zero_be_refused": (hs.CFG_RD, hs.CFG_WR, hs.MEM_WR),
                          **(shadow_kw or {})}
        self.cycle = 0
        self.rst = True
        self.release = RESET_CYCLES
        self.dl_up = False
        self.bus_master_en = False
        self.d_state = 0
        self.mem_space_en = False
        self.irq_mode = "msi"            # the programs enable MSI (E9)
        self.violations: list[str] = []
        self.link_downs = 0
        self.rp_link_downs = 0
        self.app = EpApp(self)
        self.cfg = core.CfgShadow(self, self.params)
        self.mon = RcMonitor(self)
        self.mon.build(**self.shadow_kw)
        self.mon.on_tx.append(lambda c, raw: self.cfg.sent(raw))
        self.up = Line(self, "up")
        self.dn = Line(self, "dn")
        self.tl_err_log: list[tuple[int, int]] = []
        self.link_err_log: list[tuple[int, int, int, int, int]] = []
        self.tl_err_count = [0] * 9
        self.allow_link_errors = False
        self.last_activity = 0
        self.done_at: int | None = None
        for name in ("cq_ready_i", "cc_valid_i", "cc_sop_i", "cc_eop_i", "cc_data_i",
                     "cc_handle_i", "rq_valid_i", "rq_sop_i", "rq_eop_i", "rq_data_i",
                     "rq_write_i", "rq_addr_i", "rq_len_i", "rq_first_be_i", "rq_last_be_i",
                     "rq_tag_i", "rc_ready_i", "irq_valid_i", "irq_vector_i", "dn_sub_i",
                     "dn_sub_data_i", "dn_sub_ei_i", "up_sub_i", "up_sub_data_i",
                     "up_sub_ei_i"):
            self.wr(name, 0)
        self.wr("ser_tx_rxdet_i", 1)
        self.wr("rst_i", 1)
        self.wr("rp_rst_i", 1)
        self.wr("rp_start_i", 1)         # held: one run per reset (rc HAS, Sequencer run)

    def wr(self, name: str, value: int) -> None:
        if self._last.get(name) != value:
            self._last[name] = value
            self.s[name].value = value

    def fail(self, msg: str) -> None:
        self.violations.append(f"cycle {self.cycle}: {msg}")

    def raise_violations(self) -> None:
        if self.violations:
            raise CheckError("; ".join(self.violations[:5]))

    def cfg_shadow_rx(self, raw: bytes) -> None:
        try:
            self.cfg.received(raw)
        except CheckError as exc:
            self.fail(str(exc))

    # -- running ------------------------------------------------------------------

    def start(self) -> None:
        cocotb.start_soon(Clock(self.dut.clk_i, CLK_NS, unit="ns").start())
        cocotb.start_soon(self._loop())

    async def _loop(self) -> None:
        while True:
            await FallingEdge(self.dut.clk_i)
            self.cycle += 1
            self.rst = self.cycle < self.release
            self.wr("rst_i", int(self.rst))
            self.wr("rp_rst_i", int(self.rst))
            self.up.drive()
            self.dn.drive()
            self.app.drive()
            await ReadOnly()
            try:
                self._sample()
            except CheckError as exc:
                self.violations.append(str(exc))

    def _sample(self) -> None:
        rd, n = self.rd, self.cycle
        self.up.sample()
        self.dn.sample()
        if self.rst:
            if n > 2:
                self._check(core.RESET_LINK, "in reset")
                self._check(core.RESET_FN, "in reset")
                self._check(RESET_RP, "in reset")
            return
        dl_up = bool(rd["dl_up_o"]())
        if self.dl_up and not dl_up:
            self.link_downs += 1
            self.app.reset()
            self.cfg.reset()
            self.log.info("ep dl_up_o fell at cycle %d", n)
        self.dl_up = dl_up
        if not dl_up:
            self._check(core.RESET_FN, "while dl_up_o is 0")
        self.bus_master_en = bool(rd["bus_master_en_o"]())
        self.mem_space_en = bool(rd["mem_space_en_o"]())
        self.d_state = rd["d_state_o"]()
        if self.mon.dl_up and not rd["rp_dl_up_o"]():
            self.rp_link_downs += 1
            self.log.info("rc dl_up_o fell at cycle %d", n)
        dl_err, rcv_err = rd["dl_err_o"](), rd["rcv_err_o"]()
        rp_dl_err, rp_rcv_err = rd["rp_dl_err_o"](), rd["rp_rcv_err_o"]()
        if dl_err or rcv_err or rp_dl_err or rp_rcv_err:
            self.link_err_log.append((n, dl_err, rcv_err, rp_dl_err, rp_rcv_err))
            if dl_err or rcv_err:
                self.cfg.events(dl=dl_err, rcv=rcv_err)
            if not self.allow_link_errors:
                self.fail(f"link error pulses on the error-free line: ep dl_err_o {dl_err:#x}, "
                          f"rcv_err_o {rcv_err}; rc dl_err_o {rp_dl_err:#x}, rcv_err_o "
                          f"{rp_rcv_err}")
        err = rd["mon_tl_err_o"]()
        if err:
            self.tl_err_log.append((n, err))
            for b in range(9):
                if err >> b & 1:
                    self.tl_err_count[b] += 1
                    core.COV_ERR.sample(event=core.TL_ERR_NAMES[b])
            self.cfg.events(tl=err)
        self.mon.sample()
        self.app.sample()
        self.app.tick()
        if rd["rp_done_o"]() and self.done_at is None:
            self.done_at = n

    def _check(self, table: dict, when: str) -> None:
        for name, want in table.items():
            got = self.rd[name]()
            if got != want:
                self.fail(f"{when}: {name} expected {want}, observed {got}")

    async def cycles(self, n: int) -> None:
        for _ in range(n):
            await FallingEdge(self.dut.clk_i)
        self.raise_violations()

    async def until(self, cond, limit: int, what: str, poll: int = 8) -> int:
        start = self.cycle
        while not cond():
            self.raise_violations()
            if self.cycle - start > limit:
                raise CheckError(f"{what}: not reached in {limit} cycles ({self.state()})")
            for _ in range(poll):
                await FallingEdge(self.dut.clk_i)
        self.raise_violations()
        return self.cycle - start

    def state(self) -> str:
        a = self.app
        last = self.mon.cmd_log[-1] if self.mon.cmd_log else None
        return (f"cycle {self.cycle}, rc dl_up {self.rd['rp_dl_up_o']()}, ep dl_up "
                f"{int(self.dl_up)}, rc ltssm {self.rd['rp_ltssm_state_o']():#x}, commands "
                f"{len(self.mon.cmd_log)}, last {last and (TL_OPS[last[1]] if last[1] < 10 else last[1], hex(last[2]))}, "
                f"results {len(self.mon.results)}; app jobs {len(a.jobs)} job "
                f"{a.job and a.job['state']}, rq queue {len(a.rq_q)}, reads out {sorted(a.tags_busy)}")

    async def run_program(self, limit: int) -> None:
        """Until done_o; the run must pass."""
        await self.until(lambda: self.done_at is not None, limit,
                         f"rc_seq {PROGRAM_NAMES.get(self.program)} done_o")
        await self.cycles(2)
        if not self.rd["rp_pass_o"]():
            step = self.rd["rp_fail_step_o"]()
            tail = [(c, TL_OPS[op] if 0 <= op < 10 else op, hs.RESULT_NAMES[st], hex(d), last)
                    for c, op, st, d, last in self.mon.results[-4:]]
            raise CheckError(f"rc_seq {PROGRAM_NAMES.get(self.program)} failed at step {step} "
                             f"(cycle {self.done_at}); last results {tail}")
        COV_RUN.sample(program=PROGRAM_NAMES[self.program])
        self.log.info("rc_seq %s passed at cycle %d", PROGRAM_NAMES[self.program], self.done_at)

    async def drain(self, quiet: int = 300, limit: int = 100_000) -> None:
        def quiet_now():
            r = self.rd
            if (r["mon_tx_valid_o"]() or r["mon_rx_valid_o"]() or r["cq_valid_o"]()
                    or r["rc_valid_o"]() or r["rq_valid_i"]()):
                self.last_activity = self.cycle
            return self.app.idle() and self.cycle - self.last_activity >= quiet
        await self.until(quiet_now, limit, "drain", poll=1)

    def finish(self, expected=()) -> dict:
        """Final checks after a drain: the shadow has every command's result
        and every endpoint request's answer and found nothing, but the
        ``expected`` findings (regular expressions) of injected errors, each
        blaming the endpoint and each seen; the cfg shadow had every error
        message it decided."""
        self.raise_violations()
        sh = self.mon.shadow
        try:
            sh.finish()
        except hm.HostCheckError as exc:
            raise CheckError(f"host model shadow at the end: {exc}") from None
        errs = list(sh.errors)
        unmatched = [x for x in errs if not any(re.search(p, x) for p in expected)]
        if unmatched:
            raise CheckError(f"host model shadow findings: {unmatched[:4]}")
        for p in expected:
            if not any(re.search(p, x) for x in errs):
                raise CheckError(f"injected error not seen by the shadow: {p}")
        assert all("] endpoint:" in x for x in errs), errs
        self.cfg.check_messages()
        assert self.app.idle(), "application not idle at the end"
        stats = {"cycles": self.cycle, "commands": len(self.mon.cmd_log),
                 "results": len(self.mon.results), "msis": len(self.mon.msis),
                 "rc_sent": len(self.mon.tx_log), "rc_received": len(self.mon.rx_log),
                 "shadow": dict(sh.stats), "shadow_findings": len(errs),
                 "cfg_compared": self.cfg.compared, "cfg_msgs": self.cfg.msgs_checked,
                 "copies": [c.get("result") for c in self.app.copies],
                 "tl_err": dict(zip(core.TL_ERR_NAMES, self.tl_err_count)),
                 "link_downs": (self.rp_link_downs, self.link_downs)}
        self.log.info("rc e2e done: %s", stats)
        return stats
