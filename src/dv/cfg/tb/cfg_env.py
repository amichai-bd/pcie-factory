"""Environment of the pcie_cfg testbench (wiki/src/dv/cfg/DV_PLAN.md,
Strategy). The DUT is driven only through its ports (tb/pcie_cfg_tb.sv).

One cycle, at the falling edge of clk_i after rising edge n:

1. Read the DUT's outputs (all registered), and work out what was taken at
   edge n from the inputs driven before it and the outputs shown before it:
   a register access (``reg_req_valid_i`` with ``reg_req_ready_o``), a
   message (``msg_valid_o`` with ``msg_ready_i``), an interrupt request
   (``irq_valid_i`` with ``irq_ready_o``).
2. Apply edge n to the **shadow**, the architecture model ``Cfg`` with the
   DUT's parameters, in the edge order of the MAS (Decisions): a read sees
   the state before the edge; an offer the DUT raised at n is chosen from
   the state before the edge (the model's ``offer`` is called first); then
   the take, the register write, the error events and the interrupt
   request. The model's own rules then withdraw an offer or turn a waiting
   request pending.
3. Compare: the offer (``msg_valid_o`` and, while it is up, its kind,
   address and data) with the model's; ``irq_done_o`` and ``irq_status_o``
   with the request the model resolved at n; every register-state output
   with the model's; each read's ``reg_cpl_data_o`` with the model's read.
   ``reg_cpl_valid_o`` and ``irq_done_o`` must come within the HAS
   latency target (4 cycles).
4. Drive the next inputs: the queued register accesses (one at a time,
   the next after the last one's ``reg_cpl_valid_o``), interrupt requests
   (one at a time), error-event cycles, ``msg_ready_i`` (with probability
   ``msg_ready_p``) and ``tl_np_pending_i`` (with probability ``np_p``).

Tests add their own checks written from the HAS on top (the register map in
``tb/cfg_regmap.py``, the error table, the interrupt rules).
"""

from __future__ import annotations

import random
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

_DV = Path(__file__).resolve().parents[1]
sys.path.append(str(_DV.parents[1] / "arch" / "cfg"))
import pcie_cfg_arch as arch  # noqa: E402

STATUS = {"sent": 0, "pending": 1, "dropped": 2}
STATUS_NAME = {v: k for k, v in STATUS.items()}
KIND_NAME = {0: "irq", 1: "cor", 2: "nonfatal", 3: "fatal"}
LATENCY = 4          # HAS targets: reg_cpl_valid_o and irq_done_o within 4 cycles

INPUTS = {
    "reg_req_valid_i": 1, "reg_req_bar2_i": 1, "reg_req_write_i": 1, "reg_req_addr_i": 10,
    "reg_req_be_i": 4, "reg_req_data_i": 32, "reg_req_bus_i": 8, "reg_req_dev_i": 5,
    "tl_np_pending_i": 1, "tl_err_i": 9, "dl_err_i": 7, "rcv_err_i": 1, "msg_ready_i": 1,
    "irq_valid_i": 1, "irq_vector_i": 6, "rst_i": 1,
}
OUTPUTS = ("reg_req_ready_o", "reg_cpl_valid_o", "reg_cpl_data_o", "bus_num_o", "dev_num_o",
           "bar0_base_o", "bar2_base_o", "cpl_timeout_dis_o", "mem_space_en_o",
           "bus_master_en_o", "d_state_o", "msg_valid_o", "msg_kind_o", "msg_addr_o",
           "msg_data_o", "irq_ready_o", "irq_done_o", "irq_status_o", "max_payload_o",
           "max_read_req_o")
# Reset values of the qualified and state outputs (HAS, Reset); data
# outputs are don't care.
RESET_VALUES = {
    "reg_req_ready_o": 0, "reg_cpl_valid_o": 0,
    "bus_num_o": 0, "dev_num_o": 0, "bar0_base_o": 0, "bar2_base_o": 0, "cpl_timeout_dis_o": 0,
    "mem_space_en_o": 0, "bus_master_en_o": 0, "d_state_o": 0,
    "msg_valid_o": 0, "irq_ready_o": 0, "irq_done_o": 0,
    "max_payload_o": 0, "max_read_req_o": 2,
}

# ---------------------------------------------------------------- coverage

COV_ACCESS = Covergroup("cfg_access")
COV_ACCESS.coverpoint("space", bins={"cfg": 0, "bar2": 1})
COV_ACCESS.coverpoint("dir", bins={"read": 0, "write": 1})
COV_ACCESS.coverpoint("be", bins={"none": 0, "partial": range(1, 15), "full": 15})
COV_ACCESS.coverpoint("region", bins={"header": "header", "cap": "cap", "gap": "gap",
                                      "ext": "ext", "table": "table", "mask": "mask",
                                      "pba": "pba", "bar2_other": "bar2_other"})
COV_ACCESS.cross("space_x_dir_x_be", "space", "dir", "be")

COV_MSG = Covergroup("cfg_msg")
COV_MSG.coverpoint("src", bins={"err_cor": "err_cor", "err_nonfatal": "err_nonfatal",
                                "err_fatal": "err_fatal", "app_msi": "app_msi",
                                "app_msix": "app_msix", "pend_msi": "pend_msi",
                                "pend_msix": "pend_msix"})
COV_MSG.coverpoint("end", bins={"taken": "taken", "withdrawn": "withdrawn"})
COV_MSG.coverpoint("wait", bins={"at_once": 0, "held": range(1, 1 << 30)})
COV_MSG.cross("src_x_end", "src", "end")

COV_IRQ = Covergroup("cfg_irq")
COV_IRQ.coverpoint("mode", bins={"none": "none", "msi": "msi", "msix": "msix"})
COV_IRQ.coverpoint("status", bins={"sent": "sent", "pending": "pending", "dropped": "dropped"})
COV_IRQ.coverpoint("when", bins={"at_request": "at_request", "waiting": "waiting",
                                 "withdrawn": "withdrawn", "taken": "taken"})
COV_IRQ.cross("mode_x_status", "mode", "status",
              unreachable=[("none", "sent"), ("none", "pending"), ("msix", "dropped")])

COV_POWER = Covergroup("cfg_power")
COV_POWER.coverpoint("write", bins={"d0_to_d3": (0, 3), "d3_to_d0": (3, 0), "d1_ignored": 1,
                                    "d2_ignored": 2})


def offer_src(m: arch.Msg) -> str:
    if m.src[0] == "err":
        return "err_" + KIND_NAME[m.kind]
    return f"{m.src[0]}_{m.src[1]}"


def cfg_region(dw: int) -> str:
    if dw < 0x10:
        return "header"
    if dw >= 0x40:
        return "ext"
    if dw in (0x12, 0x13, 0x1B) or dw >= 0x2B:
        return "gap"
    return "cap"


def bar2_region(dw: int, n: int) -> str:
    if dw < 4 * n:
        return "mask" if dw % 4 == 3 else "table"
    if dw in (0x200, 0x201):
        return "pba"
    return "bar2_other"


@dataclass
class Access:
    """One register access on the tl-cfg interface; addr is the DW index."""
    bar2: bool
    write: bool
    addr: int
    be: int = 0xF
    data: int = 0
    bus: int = 0
    dev: int = 0
    result: int | None = None
    done: bool = False
    taken_at: int = -1


@dataclass
class Irq:
    vector: int
    status: str | None = None
    taken_at: int = -1
    mode: str = ""


@dataclass
class Taken:
    cycle: int
    kind: int
    addr: int
    data: int
    src: tuple = field(default_factory=tuple)


class Env:
    """Drivers, the architecture-model shadow and the per-cycle checks."""

    def __init__(self, dut, seed: int = 0):
        self.dut = dut
        self.rng = random.Random(seed)
        self.cycle = 0
        self.msg_ready_p = 1.0
        self.np_p = 0.0
        self.err_p = 0.0               # probability of a random error-event cycle
        self.accesses: deque[Access] = deque()
        self.irqs: deque[Irq] = deque()
        self.events: deque[tuple[int, int, int]] = deque()
        self.taken: list[Taken] = []
        self.withdrawn = 0
        self.resolved: list[Irq] = []
        self.drv = {name: 0 for name in INPUTS}
        self.prev = {name: 0 for name in OUTPUTS}
        self.offering: Access | None = None   # driven, not yet taken
        self.inflight: tuple[Access, int | None] | None = None
        self.irq_offering: Irq | None = None
        self.irq_open: Irq | None = None
        self.offer_since = 0
        self.rst_cycles = 0
        self.model: arch.Cfg | None = None
        self.p: arch.Params | None = None

    # ------------------------------------------------------------ set-up

    async def start(self) -> None:
        d = self.dut
        cocotb.start_soon(Clock(d.clk_i, 8, unit="ns").start())
        self.drv["rst_i"] = 1
        self._drive()
        for _ in range(3):
            await FallingEdge(d.clk_i)
        cfg = int(d.cfg_params_o.value)
        fields = []
        for w in (8, 24, 16, 16, 16, 16, 8, 8):   # REVISION_ID up to BAR0_ADDR_W
            fields.append(cfg & ((1 << w) - 1))
            cfg >>= w
        rev, cls, ssid, ssvid, did, vid, n, w = fields
        self.p = arch.Params(bar0_addr_w=w, msix_table_size=n, vendor_id=vid, device_id=did,
                             subsys_vendor_id=ssvid, subsys_id=ssid, class_code=cls,
                             revision_id=rev)
        self.model = arch.Cfg(self.p)
        self.check_reset_values("in reset")
        self.drv["rst_i"] = 0
        self._drive()
        self.prev = self._read()

    def _read(self) -> dict[str, int]:
        return {name: int(getattr(self.dut, name).value) for name in OUTPUTS}

    def _drive(self) -> None:
        for name, value in self.drv.items():
            getattr(self.dut, name).value = value

    def check_reset_values(self, where: str) -> None:
        for name, want in RESET_VALUES.items():
            got = int(getattr(self.dut, name).value)
            assert got == want, f"cycle {self.cycle} {where}: {name} expected {want} got {got}"

    def fail(self, msg: str) -> None:
        raise AssertionError(f"cycle {self.cycle}: {msg}")

    # ------------------------------------------------------------ one cycle

    async def step(self) -> None:
        await FallingEdge(self.dut.clk_i)
        self.cycle += 1
        now = self._read()
        if self.drv["rst_i"]:
            self._edge_reset(now)
        else:
            self._edge(now)
        self.prev = now
        self._next_inputs(now)
        self._drive()

    def _edge_reset(self, now: dict[str, int]) -> None:
        self.model.reset()
        self.offering = None
        self.inflight = None
        self.irq_offering = None
        self.irq_open = None
        for name, want in RESET_VALUES.items():
            if now[name] != want:
                self.fail(f"after a reset edge: {name} expected {want} got {now[name]}")

    def _edge(self, now: dict[str, int]) -> None:
        m, drv, prev = self.model, self.drv, self.prev
        reg_take = bool(drv["reg_req_valid_i"] and prev["reg_req_ready_o"])
        msg_take = bool(prev["msg_valid_o"] and drv["msg_ready_i"])
        irq_take = bool(drv["irq_valid_i"] and prev["irq_ready_o"])
        m.irq_status = None
        acc = None
        exp_read = None
        if reg_take:
            acc = self.offering
            if self.inflight is not None:
                self.fail("access taken while the previous one has not completed")
            acc.taken_at = self.cycle
            self.offering = None
            region = (bar2_region(acc.addr, self.p.msix_table_size) if acc.bar2
                      else cfg_region(acc.addr))
            COV_ACCESS.sample(space=int(acc.bar2), dir=int(acc.write), be=acc.be, region=region)
            # (a) a read sees the state before the edge.
            if not acc.write:
                m.np_pending = bool(drv["tl_np_pending_i"])
                exp_read = m.bar2_read(4 * acc.addr) if acc.bar2 else m.cfg_read(4 * acc.addr)
            self.inflight = (acc, exp_read)
        # (b) an offer raised at this edge was chosen before it.
        if now["msg_valid_o"] and not prev["msg_valid_o"]:
            if m._offer is not None:
                self.fail(f"model holds offer {m._offer} while the DUT offered none")
            if m.offer() is None:
                self.fail("DUT offers a message the model does not have")
            self.offer_since = self.cycle
        # (c) the take.
        if msg_take:
            if m._offer is None:
                self.fail("message taken that the model does not offer")
            got = m.take()
            self.taken.append(Taken(self.cycle, got.kind, got.addr, got.data, got.src))
            COV_MSG.sample(src=offer_src(got), end="taken", wait=self.cycle - 1 - self.offer_since)
        held = m._offer
        # (d) the register write.
        if acc is not None and acc.write:
            if acc.bar2:
                m.bar2_write(4 * acc.addr, acc.data, acc.be)
            else:
                before = m.d_state
                m.cfg_write(4 * acc.addr, acc.data, acc.be, acc.bus, acc.dev)
                if acc.addr == 0x11 and acc.be & 1:
                    w = acc.data & 3
                    if w in (1, 2):
                        COV_POWER.sample(write=w)
                    elif (before, m.d_state) in ((0, 3), (3, 0)):
                        COV_POWER.sample(write=(before, m.d_state))
        # (e) the error events.
        tl, dl, rcv = drv["tl_err_i"], drv["dl_err_i"], drv["rcv_err_i"]
        if tl or dl or rcv:
            m.error(tl=tl, dl=dl, rcv=rcv)
        if held is not None and m._offer is None and not msg_take:
            self.withdrawn += 1
            COV_MSG.sample(src=offer_src(held), end="withdrawn",
                           wait=self.cycle - self.offer_since)
        # (f) the interrupt request.
        if irq_take:
            irq = self.irq_offering
            self.irq_offering = None
            irq.taken_at = self.cycle
            irq.mode = "msix" if m.msix_en else "msi" if m.msi_en else "none"
            if m.irq_busy:
                self.fail("irq_ready_o was 1 while the model's request is open")
            m.irq_request(irq.vector)
            self.irq_open = irq
            if m.irq_status is not None:
                self._resolved(irq, "at_request")
        elif m.irq_status is not None:
            when = ("taken" if m.irq_status == "sent" else
                    "withdrawn" if held is not None and held.src[0] == "app" else "waiting")
            self._resolved(self.irq_open, when)
        self._compare(now, reg_take)

    def _resolved(self, irq: Irq, when: str) -> None:
        irq.status = self.model.irq_status
        self.resolved.append(irq)
        self.irq_open = None
        COV_IRQ.sample(mode=irq.mode, status=irq.status, when=when)

    def _compare(self, now: dict[str, int], reg_take: bool) -> None:
        m = self.model
        # The offer.
        off = m._offer
        if now["msg_valid_o"]:
            if off is None:
                self.fail(f"DUT offers kind {now['msg_kind_o']}, the model withdrew or "
                          "has no offer")
            if now["msg_kind_o"] != off.kind:
                self.fail(f"offer kind expected {off.kind} ({offer_src(off)}) got "
                          f"{now['msg_kind_o']}")
            if off.kind == arch.MSG_IRQ and (now["msg_addr_o"], now["msg_data_o"]) != (
                    off.addr, off.data):
                self.fail(f"interrupt write ({offer_src(off)}) expected addr 0x{off.addr:x} "
                          f"data 0x{off.data:x} got addr 0x{now['msg_addr_o']:x} data "
                          f"0x{now['msg_data_o']:x}")
        elif off is not None:
            self.fail(f"model offers {offer_src(off)} but msg_valid_o is 0")
        # The interrupt resolution.
        if m.irq_status is not None:
            if not now["irq_done_o"]:
                self.fail(f"request resolved {m.irq_status} by the model, irq_done_o is 0")
            if now["irq_status_o"] != STATUS[m.irq_status]:
                self.fail(f"irq_status_o expected {STATUS[m.irq_status]} ({m.irq_status}) got "
                          f"{now['irq_status_o']}")
        elif now["irq_done_o"]:
            self.fail(f"irq_done_o with status {now['irq_status_o']}, the model resolved "
                      "nothing")
        if now["irq_ready_o"] and m.irq_busy:
            self.fail("irq_ready_o is 1 while a request is open")
        if self.irq_open is not None and self.cycle - self.irq_open.taken_at > LATENCY:
            if m.irq_status is None and not m.irq_busy:
                self.fail("request not resolved within the latency target")
        # The register state.
        state = {
            "bus_num_o": m.bus_num, "dev_num_o": m.dev_num, "bar0_base_o": m.bar0_base,
            "bar2_base_o": m.bar2_base, "cpl_timeout_dis_o": int(m.cpl_timeout_dis),
            "mem_space_en_o": int(m.mem_space_en), "bus_master_en_o": int(m.bus_master_en),
            "d_state_o": m.d_state, "max_payload_o": m.max_payload,
            "max_read_req_o": m.max_read_req,
        }
        for name, want in state.items():
            if now[name] != want:
                self.fail(f"{name} expected 0x{want:x} got 0x{now[name]:x}")
        # The completion.
        if self.inflight is not None:
            acc, exp = self.inflight
            if now["reg_cpl_valid_o"]:
                if exp is not None and now["reg_cpl_data_o"] != exp:
                    space = "BAR2" if acc.bar2 else "cfg"
                    self.fail(f"{space} read of DW 0x{acc.addr:x} expected 0x{exp:08x} got "
                              f"0x{now['reg_cpl_data_o']:08x}")
                acc.result = now["reg_cpl_data_o"] if exp is not None else None
                acc.done = True
                self.inflight = None
            elif self.cycle - acc.taken_at >= LATENCY:
                self.fail(f"no reg_cpl_valid_o within {LATENCY} cycles of the take")
        elif now["reg_cpl_valid_o"]:
            self.fail("reg_cpl_valid_o with no access open")

    # ------------------------------------------------------------ drivers

    def _next_inputs(self, now: dict[str, int]) -> None:
        drv, rng = self.drv, self.rng
        if self.rst_cycles:
            self.rst_cycles -= 1
            drv.update({name: 0 for name in INPUTS})
            drv["rst_i"] = 1
            for acc in self.accesses:
                acc.done = True
            self.accesses.clear()
            for irq in self.irqs:
                irq.status = "reset"
            self.irqs.clear()
            return
        drv["rst_i"] = 0
        # Register access: one at a time, held until taken.
        if self.offering is None and self.inflight is None and self.accesses:
            self.offering = self.accesses.popleft()
        a = self.offering
        if a is not None:
            drv.update(reg_req_valid_i=1, reg_req_bar2_i=int(a.bar2),
                       reg_req_write_i=int(a.write), reg_req_addr_i=a.addr, reg_req_be_i=a.be,
                       reg_req_data_i=a.data, reg_req_bus_i=a.bus, reg_req_dev_i=a.dev)
        else:
            drv.update(reg_req_valid_i=0, reg_req_addr_i=rng.getrandbits(10),
                       reg_req_data_i=rng.getrandbits(32), reg_req_be_i=rng.getrandbits(4),
                       reg_req_write_i=rng.getrandbits(1), reg_req_bar2_i=rng.getrandbits(1),
                       reg_req_bus_i=rng.getrandbits(8), reg_req_dev_i=rng.getrandbits(5))
        # Interrupt request: one at a time, held until taken.
        if self.irq_offering is None and self.irq_open is None and self.irqs:
            self.irq_offering = self.irqs.popleft()
        if self.irq_offering is not None:
            drv.update(irq_valid_i=1, irq_vector_i=self.irq_offering.vector)
        else:
            drv.update(irq_valid_i=0, irq_vector_i=rng.randrange(self.p.msix_table_size))
        # Error events.
        if self.events:
            tl, dl, rcv = self.events.popleft()
        elif self.err_p and rng.random() < self.err_p:
            tl, dl, rcv = rng.getrandbits(9), rng.getrandbits(7), rng.getrandbits(1)
        else:
            tl, dl, rcv = 0, 0, 0
        drv.update(tl_err_i=tl, dl_err_i=dl, rcv_err_i=rcv)
        drv["msg_ready_i"] = int(rng.random() < self.msg_ready_p)
        drv["tl_np_pending_i"] = int(rng.random() < self.np_p)

    # ------------------------------------------------------------ test API

    async def run(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.step()

    async def run_until(self, cond, limit: int, what: str) -> int:
        for i in range(limit):
            if cond():
                return i
            await self.step()
        raise AssertionError(f"cycle {self.cycle}: {what} not reached within {limit} cycles")

    async def reset(self, cycles: int = 2) -> None:
        self.rst_cycles = cycles
        await self.run(cycles + 1)

    async def access(self, acc: Access) -> Access:
        self.accesses.append(acc)
        await self.run_until(lambda: acc.done, 64 + 4 * len(self.accesses), "access completion")
        return acc

    async def cfg_read(self, dw: int, be: int = 0xF) -> int:
        return (await self.access(Access(False, False, dw, be))).result

    async def cfg_write(self, dw: int, data: int, be: int = 0xF, bus: int | None = None,
                        dev: int | None = None) -> None:
        bus = self.rng.getrandbits(8) if bus is None else bus
        dev = self.rng.getrandbits(5) if dev is None else dev
        await self.access(Access(False, True, dw, be, data & 0xFFFFFFFF, bus, dev))

    async def bar2_read(self, dw: int, be: int = 0xF) -> int:
        return (await self.access(Access(True, False, dw, be))).result

    async def bar2_write(self, dw: int, data: int, be: int = 0xF) -> None:
        await self.access(Access(True, True, dw, be, data & 0xFFFFFFFF))

    async def irq(self, vector: int, limit: int = 200) -> str:
        """Request an interrupt and wait for its resolution."""
        irq = Irq(vector)
        self.irqs.append(irq)
        await self.run_until(lambda: irq.status is not None, limit, f"irq {vector} resolution")
        return irq.status

    def request(self, vector: int) -> Irq:
        """Queue an interrupt request without waiting."""
        irq = Irq(vector)
        self.irqs.append(irq)
        return irq

    async def next_msg(self, limit: int = 100) -> Taken:
        """Wait for the next message tl takes."""
        n = len(self.taken)
        await self.run_until(lambda: len(self.taken) > n, limit, "a message taken")
        return self.taken[n]

    async def init_table(self) -> None:
        """Software writes every MSI-X entry before it unmasks one (HAS,
        Reset): address 0xFEE0_0000 + 16 v, upper 0, data 0x100 + v."""
        for v in range(self.p.msix_table_size):
            await self.bar2_write(4 * v, 0xFEE0_0000 + 16 * v)
            await self.bar2_write(4 * v + 1, 0)
            await self.bar2_write(4 * v + 2, 0x100 + v)

    async def drain(self, limit: int = 2000) -> None:
        await self.run_until(lambda: (not self.accesses and self.offering is None
                                      and self.inflight is None and not self.irqs
                                      and self.irq_offering is None and self.irq_open is None
                                      and not self.events),
                             limit, "drain")
