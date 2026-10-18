"""cocotb environment of the core back-to-back e2e (wiki/src/dv/core/DV_PLAN.md;
wiki/src/arch/core/HAS.md, APP.md, TL_CFG.md).

The top ``pcie_core_e2e_tb`` holds the endpoint ``pcie_core`` and the host's
``pcie_link`` (downstream) on one clock, their serial lines joined. One
coroutine runs every cycle: at the falling edge of ``clk_i`` it drives every
input, then, once the design has settled (ReadOnly), reads every output; a
handshake seen then (valid and ready both 1) happens at the next rising edge
and belongs to this cycle.

- The **host side** puts the independent host model
  (``models/pcie_core_host_model.py``, wiki/src/dv/core/HOST.md) on the host
  link's upper stream: ``pop_tx_beats`` with the link's credits available
  feeds ``tlp_tx_*`` one beat per cycle, every TLP delivered goes to
  ``receive_beats`` at once (the host never stalls), ``tick`` runs every
  cycle and ``link_down`` when the host link's ``dl_up_o`` falls. A fault
  hook can withhold the completions of chosen endpoint reads (completion
  timeout). The host model is the oracle: it checks every completion,
  interrupt write and endpoint request it receives.
- The **application model** serves the endpoint's application interface
  (APP): it takes ``cq`` beats (with probability ``cq_ready_p``), applies
  BAR0 writes to its BAR0 memory in ``cq`` order and answers each read with
  one ``cc`` packet after a random latency, in any order; it issues the
  ``rq`` requests a test queues and collects their ``rc`` completions (with
  probability ``rc_ready_p``); it raises the interrupts a test queues on the
  interrupt port. It keeps the APP write-completion obligation: random gaps
  only before a packet's ``sop`` beat, and once a write's ``sop`` is taken its
  remaining beats are offered every cycle, whatever ``cc_ready_o``,
  ``irq_ready_o``, ``irq_done_o`` and ``cq`` do.
- **Checks** from the HAS: reset values while ``rst_i`` is 1 and the
  application outputs at theirs while ``dl_up_o`` is 0 (HAS, reset); the
  stream contract on ``cq`` and ``rc`` (valid held with the beat unchanged
  until taken, header held from ``sop`` to ``eop``, lengths and handles in
  range, a handle never offered twice before its completion); ``rq_ready_o``
  0 on a ``sop`` beat while bus mastering is off or outside D0; ``rc``
  completions of a read in address order and its data, ``rc_done_o`` once
  per read; ``irq_ready_o`` 0 from a take until ``irq_done_o``; the link
  ``dl_err_o`` and ``rcv_err_o`` never pulse on the error-free line, at
  either end; liveness.
- The **shadow** is the ``cfg`` architecture model (``src/arch/cfg``) with
  the DUT's parameters: every configuration request and BAR2 table write
  the host sends is applied to it when the host link takes it, the
  Set_Slot_Power_Limit data likewise, and the ``tl_err`` events (tapped)
  and the ep link's ``dl_err_o`` and ``rcv_err_o`` as they pulse; each configuration read completion and BAR2 table read the
  host receives must equal the shadow's value when the request went out
  (Device Status Transactions Pending, which follows ``tl_np_pending``,
  excepted), and each error message the host receives must be one the
  shadow has due.
"""

from __future__ import annotations

import os
import random
import sys
from collections import deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly

from regress.funcov import Covergroup

_DV = Path(__file__).resolve().parents[1]
sys.path.append(str(_DV / "models"))
sys.path.append(str(_DV.parents[1] / "arch" / "cfg"))
sys.path.append(str(_DV.parents[1] / "arch" / "pcs"))
import pcie_cfg_arch as cfga  # noqa: E402  (the shadow; DV may import arch)
import pcie_pcs_8b10b as code8b10b  # noqa: E402  (the 8b/10b code, for the line test)
import pcie_core_host_model as hm  # noqa: E402
import pcie_core_host_tlp as ht  # noqa: E402

CLK_NS = 8
RESET_CYCLES = 16
LIVENESS = 40_000                  # cycles with work outstanding and nothing moving
HOST_RID = 0x0000
EP_BDF = (1, 0, 0)                 # the bus and device the host enumerates the ep at
MSI_ADDR = 0xFEE0_0000
HOST_MEM = 0x0010_0000             # host memory the application uses
HIGH_MEM = 0x0000_0001_0000_0000   # above 4 GiB: 4-DW headers

# rc_status_o (APP, requester completions)
RC_SC, RC_UR, RC_CA, RC_FAILED = 0, 1, 2, 3
# irq_status_o
IRQ_SENT, IRQ_PENDING, IRQ_DROPPED = 0, 1, 2
IRQ_NAMES = {IRQ_SENT: "sent", IRQ_PENDING: "pending", IRQ_DROPPED: "dropped"}

ERR_MSG = {ht.MSG_ERR_COR: cfga.MSG_ERR_COR, ht.MSG_ERR_NONFATAL: cfga.MSG_ERR_NONFATAL,
           ht.MSG_ERR_FATAL: cfga.MSG_ERR_FATAL}

# Reset values (HAS, reset): while rst_i is 1 every one; while dl_up_o is 0
# (after rst_i) the application and function-status ones.
RESET_LINK = {"dl_up_o": 0, "dl_state_o": 0, "dl_err_o": 0, "link_up_o": 0,
              "ltssm_state_o": 0, "rcv_err_o": 0, "ser_tx_elec_idle_o": 1}
RESET_FN = {"cq_valid_o": 0, "cc_ready_o": 0, "rq_ready_o": 0, "rc_valid_o": 0,
            "irq_ready_o": 0, "irq_done_o": 0, "bus_master_en_o": 0, "mem_space_en_o": 0,
            "d_state_o": 0, "max_payload_o": 0, "max_read_req_o": 2}

# Device Status Transactions Pending, as a bit of DW 0x78: follows
# tl_np_pending, which the shadow does not see.
DS_TP = 1 << 21

# ------------------------------------------------------------ coverage

COV_ENUM = Covergroup("core_e2e_enum")
COV_ENUM.coverpoint("step", bins={s: s for s in (
    "ids", "cap_pm", "cap_msi", "cap_msix", "cap_pcie", "bar0", "bar2", "enables",
    "msi", "msix")})

COV_TRAFFIC = Covergroup("core_e2e_traffic")
# Sizes in bytes each kind can carry: host reads 0 to 4096 (a zero-length
# read included), writes and application reads 1 to 128 (APP, F2).
_SIZES = {"dw1": range(1, 5), "small": range(5, 64), "mid": range(64, 129)}
COV_TRAFFIC.coverpoint("host_read", bins={"zero": 0, **_SIZES, "large": range(129, 4097)})
COV_TRAFFIC.coverpoint("host_write", bins=_SIZES)
COV_TRAFFIC.coverpoint("app_write", bins=_SIZES)
COV_TRAFFIC.coverpoint("app_read", bins=_SIZES)
# Completions per application read: at most 128 bytes, so one, or two or
# three when the host splits on 64-byte boundaries.
COV_TRAFFIC.coverpoint("split", bins={"one": 1, "two": 2, "three": 3})
COV_TRAFFIC.coverpoint("header", bins={"dw3": 3, "dw4": 4})

COV_IRQ = Covergroup("core_e2e_irq")
# The interrupt mode and irq_status_o outcomes the TL_CFG resolution allows.
COV_IRQ.coverpoint("outcome", bins={o: o for o in (
    "none_dropped", "msi_sent", "msi_pending", "msix_sent", "msix_pending")})
COV_IRQ.coverpoint("pending_sent", bins={"msi": "msi", "msix": "msix"})

COV_ERR = Covergroup("core_e2e_err")
COV_ERR.coverpoint("event", bins={n: n for n in (
    "ur_posted", "ur_np", "malformed", "poisoned_req", "poisoned_cpl", "unexpected_cpl",
    "cpl_timeout", "rcvd_ur", "rcvd_ca")})
COV_ERR.coverpoint("msg", bins={"cor": cfga.MSG_ERR_COR, "nonfatal": cfga.MSG_ERR_NONFATAL,
                                "fatal": cfga.MSG_ERR_FATAL})

TL_ERR_NAMES = ("ur_posted", "ur_np", "malformed", "poisoned_req", "poisoned_cpl",
                "unexpected_cpl", "cpl_timeout", "rcvd_ur", "rcvd_ca")


class CheckError(AssertionError):
    """A rule of the HAS the endpoint broke, as the bench saw it."""


# ------------------------------------------------------------ shadow

class CfgShadow:
    """The cfg architecture model as a shadow of what the host sees."""

    def __init__(self, env: "Env", params: cfga.Params):
        self.env = env
        self.model = cfga.Cfg(params)
        self.pending: dict[int, tuple[str, list[int]]] = {}   # tag -> (what, expected DWs)
        self.compared = 0
        self.msgs_checked = 0
        self.decided = {cfga.MSG_ERR_COR: 0, cfga.MSG_ERR_NONFATAL: 0, cfga.MSG_ERR_FATAL: 0}
        self.received_msgs = dict(self.decided)

    def reset(self) -> None:
        self.model.reset()
        self.pending.clear()
        self.decided = dict.fromkeys(self.decided, 0)
        self.received_msgs = dict(self.decided)

    def _bar2_offset(self, addr: int) -> int | None:
        m = self.model
        if not (m.mem_space_en and m.d_state == cfga.D0) or addr >> 32:
            return None
        base = m.bar2_base
        if base and base <= addr < base + cfga.BAR2_BYTES and not (
                m.bar0_base <= addr < m.bar0_base + (1 << m.p.bar0_addr_w)):
            return addr - base
        return None

    def sent(self, raw: bytes) -> None:
        """The host link took this TLP's sop."""
        t = ht.Tlp.decode(raw)
        m = self.model
        if t.kind == "CfgWr0" and t.cfg_fn == 0 and not t.ep:
            m.cfg_write(t.cfg_reg, int.from_bytes(t.data[:4], "little"), t.first_be,
                        t.cfg_bus, t.cfg_dev)
        elif t.kind == "CfgRd0" and t.cfg_fn == 0 and not t.ep:
            self.pending[t.tag] = (f"configuration read {t.cfg_reg:#05x}",
                                   [m.cfg_read(t.cfg_reg)])
        elif t.kind in ("MWr", "MRd") and not t.ep:
            off = self._bar2_offset(t.address)
            if off is None:
                return
            n = t.length_dw
            bes = [t.first_be] + [0xF] * (n - 2) + ([t.last_be] if n > 1 else [])
            if t.kind == "MWr":
                for k in range(n):
                    if bes[k]:
                        m.bar2_write(off + 4 * k,
                                     int.from_bytes(t.data[4 * k:4 * k + 4], "little"), bes[k])
            elif off + 4 * n <= 16 * m.p.msix_table_size:   # the table; the PBA follows irqs
                self.pending[t.tag] = (f"BAR2 read {off:#05x}",
                                       [m.bar2_read(off + 4 * k) for k in range(n)])
        elif t.kind == "MsgD" and t.msg_code == ht.MSG_SET_SLOT_POWER_LIMIT and not t.ep \
                and t.tc == 0:
            m.slot_power(t.data[0], t.data[1] & 3)

    def received(self, raw: bytes) -> None:
        """The host link delivered this TLP to the host model."""
        t = ht.Tlp.decode(raw)
        m = self.model
        if t.is_completion and t.requester_id == HOST_RID and t.tag in self.pending:
            what, want = self.pending.pop(t.tag)
            if t.status != ht.CPL_SC:
                return
            got = [int.from_bytes(t.data[4 * k:4 * k + 4], "little")
                   for k in range(t.length_dw)]
            if what.startswith("configuration read 0x078"):
                got = [g & ~DS_TP for g in got]
            self.compared += 1
            if got != want:
                raise CheckError(f"shadow: {what}: the endpoint returned "
                                 f"{[hex(g) for g in got]}, the cfg architecture model "
                                 f"{[hex(w) for w in want]} (cycle {self.env.cycle})")
        elif t.kind == "Msg" and t.msg_code in ERR_MSG:
            kind = ERR_MSG[t.msg_code]
            self.received_msgs[kind] += 1
            COV_ERR.sample(msg=kind)
            if self.received_msgs[kind] > self.decided[kind]:
                raise CheckError(f"shadow: error message {t.msg_code:#04x} number "
                                 f"{self.received_msgs[kind]} received at cycle {self.env.cycle}; "
                                 f"the cfg architecture model decided {self.decided[kind]}")
            m.err_due[kind] = False
            if kind != cfga.MSG_ERR_COR and m._reg(0x04) & cfga.CMD_SERR:
                m.r[1] |= cfga.STS_SSE
            self.msgs_checked += 1

    def check_messages(self) -> None:
        """At a quiet point: every kind the model decided was received at
        least once (decisions of one kind may merge into one message)."""
        for kind, n in self.decided.items():
            if n and not self.received_msgs[kind]:
                raise CheckError(f"shadow: the cfg architecture model decided {n} error "
                                 f"message(s) of kind {kind}, none received")

    def events(self, tl: int = 0, dl: int = 0, rcv: int = 0) -> None:
        """One cycle of tl_err, dl_err and rcv_err pulses: status bits, and
        the messages this cycle's events decide (counted apart from those
        still due)."""
        m = self.model
        due = dict(m.err_due)
        m.err_due = {k: False for k in due}
        m.error(tl=tl, dl=dl, rcv=rcv)
        for k, want in m.err_due.items():
            self.decided[k] += int(want)
        m.err_due = {k: due[k] or m.err_due[k] for k in due}


# ------------------------------------------------------------ host side

class HostSide:
    """The host model on the host link's upper stream."""

    def __init__(self, env: "Env", **kw):
        self.env = env
        self.model = hm.HostModel(requester_id=HOST_RID, **kw)
        self.cur: list[int] | None = None
        self.cur_raw = b""
        self.pos = 0
        self.wait = 0                       # cycles before the next pop (credits settle)
        self.rx: list[int] = []
        self.dl_up = False
        self.avail = {}
        self.drop_rd = None                 # f(Tlp) -> True: withhold this read's completions
        self.dropped: list[ht.Tlp] = []
        self.sent_log: list[tuple[int, bytes]] = []
        self.rx_log: list[tuple[int, bytes]] = []
        self.on_sent = []                   # callbacks f(cycle, raw)
        self.on_rx = []                     # callbacks f(cycle, raw), before the model sees it
        self.last_move = 0
        # The model's queued responses for dropped reads are removed from
        # its transmit queue (the host "loses" the read).

    def drive(self) -> None:
        e = self.env
        if not self.dl_up:
            self.cur = None
            e.wr("host_tlp_tx_valid_i", 0)
            return
        if self.cur is None:
            if self.wait:
                self.wait -= 1
            else:
                raw = self.model.pop_tx(self.avail)
                if raw is not None:
                    self.cur, self.cur_raw, self.pos = ht.to_beats(raw), raw, 0
        if self.cur is None:
            e.wr("host_tlp_tx_valid_i", 0)
            return
        e.wr("host_tlp_tx_valid_i", 1)
        e.wr("host_tlp_tx_data_i", self.cur[self.pos])
        e.wr("host_tlp_tx_sop_i", int(self.pos == 0))
        e.wr("host_tlp_tx_eop_i", int(self.pos == len(self.cur) - 1))

    def sample(self) -> None:
        e, rd = self.env, self.env.rd
        dl_up = bool(rd["host_dl_up_o"]())
        if self.dl_up and not dl_up:
            self.model.link_down()
            self.rx = []
            e.on_host_down()
        self.dl_up = dl_up
        if dl_up:
            self.avail = {"ph": rd["host_fc_ph_avail_o"](), "pd": rd["host_fc_pd_avail_o"](),
                          "nph": rd["host_fc_nph_avail_o"](), "npd": rd["host_fc_npd_avail_o"](),
                          "cplh": rd["host_fc_cplh_avail_o"](),
                          "cpld": rd["host_fc_cpld_avail_o"]()}
        if self.cur is not None and rd["host_tlp_tx_ready_o"]():
            if self.pos == 0:
                self.sent_log.append((e.cycle, self.cur_raw))
                for f in self.on_sent:
                    f(e.cycle, self.cur_raw)
            self.pos += 1
            self.last_move = e.cycle
            if self.pos == len(self.cur):
                self.cur = None
                self.wait = 1
        if rd["host_tlp_rx_valid_o"]():
            if rd["host_tlp_rx_sop_o"]():
                self.rx = []
            self.rx.append(rd["host_tlp_rx_data_o"]())
            if rd["host_tlp_rx_eop_o"]():
                raw = ht.from_beats(self.rx)
                self.rx = []
                self.last_move = e.cycle
                self._deliver(raw)
        self.model.tick(1)

    def _deliver(self, raw: bytes) -> None:
        e = self.env
        t = ht.Tlp.decode(raw)
        if t.kind == "MRd" and self.drop_rd is not None and self.drop_rd(t):
            self.dropped.append(t)
            e.log.info("host: withholding the completions of endpoint read tag %d at %#x "
                       "(cycle %d)", t.tag, t.address, e.cycle)
            return
        self.rx_log.append((e.cycle, raw))
        for f in self.on_rx:
            f(e.cycle, raw)
        try:
            self.model.receive(raw)
        except hm.HostCheckError as exc:
            raise CheckError(f"host model at cycle {e.cycle}: {exc}") from None

    def run(self, gen):
        """Start a host procedure; returns the Procedure."""
        return self.model.start(gen)


# ------------------------------------------------------------ application

class Read:
    """One application rq read and what came back on rc."""

    def __init__(self, tag: int, addr: int, nbytes: int, first_be: int, last_be: int,
                 dws: int):
        self.tag, self.addr, self.nbytes = tag, addr, nbytes
        self.first_be, self.last_be, self.dws = first_be, last_be, dws
        self.data: list[int] = []
        self.completions = 0
        self.status: int | None = None
        self.done = False
        self.issued_at: int | None = None
        self.done_at: int | None = None


class App:
    """The application model on the endpoint's application interface."""

    def __init__(self, env: "Env"):
        self.env = env
        self.rng = random.Random(env.rng.getrandbits(32))
        self.bar0 = bytearray(1 << env.bar0_addr_w)
        self.cq_ready_p = 1.0
        self.rc_ready_p = 1.0
        self.cc_latency = (1, 24)          # cycles from a read taken to its cc packet offered
        self.cc_gap_p = 0.1                # a gap before a cc packet's sop
        self.rq_gap_p = 0.1                # a gap before an rq packet's sop
        # cq
        self.cq_ready = 0
        self.cq_prev = None                # the beat offered last cycle and not taken
        self.cq_pkt: dict | None = None
        self.cq_hdr = None
        self.handles: dict[int, dict] = {}  # read handles offered, not yet answered
        self.cq_log: list[dict] = []
        self.cq_bytes = 0
        self.cq_last = 0
        # cc
        self.cc_due: list[tuple[int, int]] = []   # (cycle, handle)
        self.cc_cur: list | None = None
        self.cc_pos = 0
        self.cc_handle = 0
        self.cc_valid = 0
        self.cc_done = 0
        # rq
        self.rq_q: deque = deque()
        self.rq_cur: dict | None = None
        self.rq_pos = 0
        self.rq_valid = 0
        self.rq_taken: list[dict] = []
        self.rq_write_bytes = 0
        self.tags_busy: dict[int, Read] = {}
        self.reads: list[Read] = []
        # rc
        self.rc_ready = 0
        self.rc_prev = None
        self.rc_pkt: dict | None = None
        self.rc_seen = 0
        # irq
        self.irq_q: deque = deque()
        self.irq_valid = 0
        self.irq_open: dict | None = None    # taken, not done
        self.irq_log: list[dict] = []
        self.on_cq_write = []                # callbacks f(offset, nbytes)

    # -- requests a test makes ----------------------------------------------

    def write(self, addr: int, data: bytes) -> dict:
        """One rq write of ``data`` at byte ``addr`` (at most 128 bytes, no
        4 KiB crossing)."""
        dw = addr & ~3
        n = len(data)
        if not 1 <= n or addr >> 12 != (addr + n - 1) >> 12:
            raise ValueError("rq write: 1 or more bytes inside one 4 KiB block")
        dws = (addr + n - dw + 3) // 4
        if dws > 32:
            raise ValueError("rq write above 128 bytes")
        buf = bytearray(4 * dws)
        buf[addr - dw:addr - dw + n] = data
        fbe, lbe = self._bes(addr, n, dws)
        req = {"write": True, "addr": dw, "len": dws, "fbe": fbe, "lbe": lbe, "tag": 0,
               "beats": [int.from_bytes(buf[4 * k:4 * k + 4], "little") for k in range(dws)],
               "taken": None, "bytes": n}
        self.rq_q.append(req)
        return req

    def read(self, addr: int, nbytes: int, tag: int | None = None) -> Read:
        """One rq read of ``nbytes`` (1 to 128) at ``addr``; ``tag`` free or
        chosen when it goes out."""
        dw = addr & ~3
        dws = (addr + nbytes - dw + 3) // 4
        if not 1 <= nbytes or dws > 32 or addr >> 12 != (addr + nbytes - 1) >> 12:
            raise ValueError("rq read: 1 to 128 bytes inside one 4 KiB block")
        fbe, lbe = self._bes(addr, nbytes, dws)
        r = Read(-1 if tag is None else tag, addr, nbytes, fbe, lbe, dws)
        self.rq_q.append({"write": False, "addr": dw, "len": dws, "fbe": fbe, "lbe": lbe,
                          "tag": tag, "beats": [0], "read": r, "taken": None})
        self.reads.append(r)
        return r

    @staticmethod
    def _bes(addr: int, n: int, dws: int) -> tuple[int, int]:
        lo = addr & 3
        hi = (addr + n - 1) & 3
        if dws == 1:
            return (0xF << lo) & (0xF >> (3 - hi)) & 0xF, 0
        return (0xF << lo) & 0xF, 0xF >> (3 - hi)

    def irq(self, vector: int = 0) -> dict:
        req = {"vector": vector, "taken": None, "done": None, "status": None}
        self.irq_q.append(req)
        return req

    def idle(self) -> bool:
        return (not self.rq_q and self.rq_cur is None and not self.tags_busy
                and not self.handles and not self.irq_q and self.irq_open is None
                and self.cq_pkt is None and self.rc_pkt is None)

    def reset(self) -> None:
        """Link down: abandon everything in progress (HAS, link down)."""
        if self.rq_cur is not None and self.rq_cur["write"] and self.rq_pos:
            self.rq_cur = None
        self.handles.clear()
        self.cc_due.clear()
        self.cc_cur = None
        self.cq_pkt = None
        self.cq_prev = None
        self.rc_pkt = None
        self.rc_prev = None
        for r in self.tags_busy.values():
            r.done, r.status = True, None
        self.tags_busy.clear()
        if self.irq_open is not None:
            self.irq_open = None

    # -- one cycle -------------------------------------------------------------

    def drive(self) -> None:
        e, rng = self.env, self.rng
        up = e.dl_up
        # cq: take with probability cq_ready_p
        self.cq_ready = int(up and (self.cq_ready_p >= 1.0 or rng.random() < self.cq_ready_p))
        e.wr("cq_ready_i", self.cq_ready)
        # rc
        self.rc_ready = int(up and (self.rc_ready_p >= 1.0 or rng.random() < self.rc_ready_p))
        e.wr("rc_ready_i", self.rc_ready)
        # cc: one packet at a time, any order of the reads due
        self.cc_valid = 0
        if up:
            if self.cc_cur is None and self.cc_due:
                ready = [d for d in self.cc_due if d[0] <= e.cycle]
                if ready and rng.random() >= self.cc_gap_p:
                    pick = rng.choice(ready)
                    self.cc_due.remove(pick)
                    h = self.handles[pick[1]]
                    self.cc_cur, self.cc_pos, self.cc_handle = h["data"], 0, pick[1]
            if self.cc_cur is not None:
                self.cc_valid = 1
                e.wr("cc_data_i", self.cc_cur[self.cc_pos])
                e.wr("cc_sop_i", int(self.cc_pos == 0))
                e.wr("cc_eop_i", int(self.cc_pos == len(self.cc_cur) - 1))
                e.wr("cc_handle_i", self.cc_handle)
        e.wr("cc_valid_i", self.cc_valid)
        # rq: gaps only before sop; a write started is finished every cycle
        self.rq_valid = 0
        if up:
            if self.rq_cur is None and self.rq_q:
                req = self.rq_q[0]
                if not req["write"] and req["tag"] is None:
                    free = [t for t in range(8) if t not in self.tags_busy]
                    if free:
                        req["tag"] = free[0]
                        req["read"].tag = free[0]
                if (req["write"] or (req["tag"] is not None
                                     and req["tag"] not in self.tags_busy)) \
                        and rng.random() >= self.rq_gap_p:
                    self.rq_cur, self.rq_pos = self.rq_q.popleft(), 0
            if self.rq_cur is not None:
                r = self.rq_cur
                self.rq_valid = 1
                e.wr("rq_data_i", r["beats"][self.rq_pos])
                e.wr("rq_sop_i", int(self.rq_pos == 0))
                e.wr("rq_eop_i", int(self.rq_pos == len(r["beats"]) - 1))
                e.wr("rq_write_i", int(r["write"]))
                e.wr("rq_addr_i", r["addr"])
                e.wr("rq_len_i", r["len"])
                e.wr("rq_first_be_i", r["fbe"])
                e.wr("rq_last_be_i", r["lbe"])
                e.wr("rq_tag_i", r["tag"] or 0)
        e.wr("rq_valid_i", self.rq_valid)
        # irq: one request at a time
        self.irq_valid = 0
        if up and self.irq_open is None and self.irq_q:
            self.irq_valid = 1
            e.wr("irq_vector_i", self.irq_q[0]["vector"])
        e.wr("irq_valid_i", self.irq_valid)

    def sample(self) -> None:
        e, rd = self.env, self.env.rd
        n = e.cycle
        # -- cq
        if rd["cq_valid_o"]():
            write = rd["cq_write_o"]()
            # A read's data and a write's handle are don't care (APP).
            beat = (rd["cq_sop_o"](), rd["cq_eop_o"](), rd["cq_data_o"]() if write else 0,
                    write, rd["cq_addr_o"](), rd["cq_len_o"](), rd["cq_first_be_o"](),
                    rd["cq_last_be_o"](), 0 if write else rd["cq_handle_o"]())
            if self.cq_prev is not None and beat != self.cq_prev:
                e.fail(f"cq: the beat offered at cycle {n - 1} changed before it was taken: "
                       f"{self.cq_prev} then {beat}")
            if self.cq_ready:
                self.cq_prev = None
                self._cq_beat(beat)
            else:
                self.cq_prev = beat
        elif self.cq_prev is not None:
            e.fail(f"cq: cq_valid_o fell at cycle {n} with the beat {self.cq_prev} not taken")
        # -- cc
        if self.cc_valid and rd["cc_ready_o"]():
            self.cc_pos += 1
            if self.cc_pos == len(self.cc_cur):
                self.handles.pop(self.cc_handle)
                self.cc_cur = None
                self.cc_done += 1
        # -- rq
        if self.rq_valid:
            r = self.rq_cur
            ready = rd["rq_ready_o"]()
            if ready and self.rq_pos == 0:
                if not e.bus_master_en or e.d_state != 0:
                    e.fail(f"rq: sop taken at cycle {n} with bus_master_en_o "
                           f"{int(e.bus_master_en)}, d_state_o {e.d_state}")
                r["taken"] = n
                if not r["write"]:
                    rd_ = r["read"]
                    rd_.issued_at = n
                    self.tags_busy[r["tag"]] = rd_
                    COV_TRAFFIC.sample(app_read=rd_.nbytes, header=4 if r["addr"] >> 32 else 3)
                else:
                    COV_TRAFFIC.sample(app_write=r["bytes"], header=4 if r["addr"] >> 32 else 3)
            elif not ready and self.rq_pos and r["write"]:
                pass                       # tl may stall inside a packet
            if ready:
                self.rq_pos += 1
                if self.rq_pos == len(r["beats"]):
                    self.rq_taken.append(r)
                    if r["write"]:
                        self.rq_write_bytes += r["bytes"]
                    self.rq_cur = None
        # -- rc
        if rd["rc_valid_o"]():
            self.rc_seen += 1
            beat = (rd["rc_sop_o"](), rd["rc_eop_o"](), rd["rc_data_o"](), rd["rc_tag_o"](),
                    rd["rc_status_o"](), rd["rc_done_o"]())
            if self.rc_prev is not None and beat != self.rc_prev:
                e.fail(f"rc: the beat offered at cycle {n - 1} changed before it was taken: "
                       f"{self.rc_prev} then {beat}")
            if self.rc_ready:
                self.rc_prev = None
                self._rc_beat(beat)
            else:
                self.rc_prev = beat
        elif self.rc_prev is not None:
            e.fail(f"rc: rc_valid_o fell at cycle {n} with the beat {self.rc_prev} not taken")
        # -- irq
        ready = rd["irq_ready_o"]()
        done = rd["irq_done_o"]()
        if self.irq_open is not None and ready and not done:
            e.fail(f"irq: irq_ready_o 1 at cycle {n} while the request taken at cycle "
                   f"{self.irq_open['taken']} is not done")
        if done:
            if self.irq_open is None:
                e.fail(f"irq: irq_done_o at cycle {n} with no request open")
            else:
                req = self.irq_open
                req["done"], req["status"] = n, rd["irq_status_o"]()
                self.irq_log.append(req)
                COV_IRQ.sample(outcome=f"{e.irq_mode}_{IRQ_NAMES.get(req['status'])}")
                self.irq_open = None
        if self.irq_valid and ready and self.irq_open is None:
            req = self.irq_q.popleft()
            req["taken"] = n
            self.irq_open = req

    def _cq_beat(self, beat) -> None:
        e = self.env
        sop, eop, data, write, addr, ln, fbe, lbe, handle = beat
        n = e.cycle
        if sop:
            if self.cq_pkt is not None:
                e.fail(f"cq: sop at cycle {n} inside a packet")
            if addr & 3:
                e.fail(f"cq: cq_addr_o {addr:#x} not DW aligned at cycle {n}")
            if write and not 1 <= ln <= 32:
                e.fail(f"cq: write of {ln} DWs at cycle {n}; 1 to 32 allowed")
            if not write and not 1 <= ln <= 1024:
                e.fail(f"cq: read of {ln} DWs at cycle {n}; 1 to 1024 allowed")
            if ln == 1 and lbe:
                e.fail(f"cq: length 1 with last byte enables {lbe:04b} at cycle {n}")
            if addr + 4 * ln > len(self.bar0):
                e.fail(f"cq: request at {addr:#x}, {ln} DWs runs past BAR0 at cycle {n}")
            self.cq_hdr = (write, addr, ln, fbe, lbe, None)
            self.cq_pkt = {"write": write, "addr": addr, "len": ln, "fbe": fbe, "lbe": lbe,
                           "handle": handle, "data": [], "sop": n}
        elif self.cq_pkt is None:
            e.fail(f"cq: beat without sop at cycle {n}")
            return
        elif (write, addr, ln, fbe, lbe, None) != self.cq_hdr:
            e.fail(f"cq: header changed inside the packet at cycle {n}")
        p = self.cq_pkt
        if p["write"]:
            p["data"].append(data)
            last = len(p["data"]) == p["len"]
            if eop != last:
                e.fail(f"cq: write of {p['len']} DWs with eop {eop} on beat {len(p['data'])} "
                       f"at cycle {n}")
            if not eop:
                return
            self._apply_write(p)
        else:
            if not eop:
                e.fail(f"cq: read without eop on its sop beat at cycle {n}")
            if handle in self.handles:
                e.fail(f"cq: handle {handle} offered at cycle {n} while still open")
            nbytes = 4 * p["len"]
            snap = bytes(self.bar0[p["addr"]:p["addr"] + nbytes])
            beats = [int.from_bytes(snap[4 * k:4 * k + 4], "little") for k in range(p["len"])]
            self.handles[handle] = {"data": beats, "pkt": p}
            lat = self.rng.randint(*self.cc_latency)
            self.cc_due.append((n + lat, handle))
            COV_TRAFFIC.sample(host_read=self._bytes(p))
        self.cq_log.append(p)
        self.cq_last = n
        self.cq_pkt = None

    @staticmethod
    def _bytes(p) -> int:
        """Bytes a cq request enables (zero-length read 0)."""
        n = p["len"]
        if n == 1:
            return bin(p["fbe"]).count("1")
        return bin(p["fbe"]).count("1") + 4 * (n - 2) + bin(p["lbe"]).count("1")

    def _apply_write(self, p) -> None:
        n = p["len"]
        for k, d in enumerate(p["data"]):
            be = p["fbe"] if k == 0 else (p["lbe"] if k == n - 1 else 0xF)
            for b in range(4):
                if be >> b & 1:
                    self.bar0[p["addr"] + 4 * k + b] = d >> (8 * b) & 0xFF
        nbytes = self._bytes(p)
        self.cq_bytes += nbytes
        COV_TRAFFIC.sample(host_write=nbytes)
        for f in self.on_cq_write:
            f(p["addr"], 4 * n)

    def _rc_beat(self, beat) -> None:
        e = self.env
        sop, eop, data, tag, status, done = beat
        n = e.cycle
        r = self.tags_busy.get(tag)
        if r is None:
            e.fail(f"rc: beat for tag {tag} at cycle {n}, which has no read outstanding")
            return
        if sop:
            if self.rc_pkt is not None:
                e.fail(f"rc: sop at cycle {n} inside a completion")
            self.rc_pkt = {"tag": tag, "status": status, "data": []}
        elif self.rc_pkt is None or self.rc_pkt["tag"] != tag or self.rc_pkt["status"] != status:
            e.fail(f"rc: tag or status changed inside a completion at cycle {n}")
            return
        if done and not eop:
            e.fail(f"rc: rc_done_o without rc_eop_o at cycle {n}")
        if status != RC_SC:
            if not (sop and eop and done):
                e.fail(f"rc: status {status} for tag {tag} at cycle {n} is not a single beat "
                       "with sop, eop and done")
            r.status, r.done, r.done_at = status, True, n
            del self.tags_busy[tag]
            self.rc_pkt = None
            return
        self.rc_pkt["data"].append(data)
        if eop:
            r.data += self.rc_pkt["data"]
            r.completions += 1
            self.rc_pkt = None
            if len(r.data) > r.dws:
                e.fail(f"rc: tag {tag} got {len(r.data)} DWs, the read asked {r.dws}")
            if done:
                if len(r.data) != r.dws:
                    e.fail(f"rc: tag {tag} done at cycle {n} with {len(r.data)} of {r.dws} DWs")
                r.status, r.done, r.done_at = RC_SC, True, n
                del self.tags_busy[tag]
                COV_TRAFFIC.sample(split=r.completions)

    def read_bytes(self, r: Read) -> bytes:
        """The enabled bytes of a finished read, in address order."""
        raw = b"".join(d.to_bytes(4, "little") for d in r.data)
        off = r.addr & 3
        return raw[off:off + r.nbytes]


# ------------------------------------------------------------ env

class Env:
    """One back-to-back run; ``seed`` draws everything random."""

    def __init__(self, dut, seed: int, host_kw: dict | None = None):
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
            "fc_ph", "fc_pd", "fc_nph", "fc_npd", "cpl_timeout_div", "bar0_addr_w",
            "msix_table_size", "vendor_id", "device_id", "subsys_vendor_id", "subsys_id",
            "class_code", "revision_id")}
        self.p = p
        self.bar0_addr_w = p["bar0_addr_w"]
        self.msix_n = p["msix_table_size"]
        self.cpl_timeout = (1 << 21) // p["cpl_timeout_div"]
        # A test may wait out two completion timeouts on purpose.
        self.liveness = max(LIVENESS, 3 * self.cpl_timeout)
        self.log.info("core e2e: COCOTB_RANDOM_SEED %s, test seed %d, parameters %s",
                      os.environ.get("COCOTB_RANDOM_SEED", "-"), seed, p)
        self.params = cfga.Params(bar0_addr_w=p["bar0_addr_w"],
                                  msix_table_size=p["msix_table_size"],
                                  vendor_id=p["vendor_id"], device_id=p["device_id"],
                                  subsys_vendor_id=p["subsys_vendor_id"],
                                  subsys_id=p["subsys_id"], class_code=p["class_code"],
                                  revision_id=p["revision_id"])
        self.cycle = 0
        self.rst = True
        self.release = RESET_CYCLES
        self.host_rst = True
        self.dl_up = False
        self.dl_up_cycles = 0
        self.bus_master_en = False
        self.d_state = 0
        self.mem_space_en = False
        self.irq_mode = "none"
        self.violations: list[str] = []
        self.link_downs = 0
        self.host = HostSide(self, **(host_kw or {}))
        self.app = App(self)
        self.shadow = CfgShadow(self, self.params)
        self.host.on_sent.append(lambda c, raw: self.shadow.sent(raw))
        self.host.on_rx.append(lambda c, raw: self.shadow.received(raw))
        self.tl_err_log: list[tuple[int, int]] = []
        self.link_err_log: list[tuple[int, int, int]] = []   # (cycle, dl_err_o, rcv_err_o)
        self.tl_err_count = [0] * 9
        self.np_pending = 0
        self.allow_link_errors = False
        self.dev: hm.DeviceInfo | None = None
        for name in ("cq_ready_i", "cc_valid_i", "cc_sop_i", "cc_eop_i", "cc_data_i",
                     "cc_handle_i", "rq_valid_i", "rq_sop_i", "rq_eop_i", "rq_data_i",
                     "rq_write_i", "rq_addr_i", "rq_len_i", "rq_first_be_i", "rq_last_be_i",
                     "rq_tag_i", "rc_ready_i", "irq_valid_i", "irq_vector_i",
                     "host_tlp_tx_valid_i", "host_tlp_tx_data_i", "host_tlp_tx_sop_i",
                     "host_tlp_tx_eop_i", "host_hot_reset_req_i", "line_flip_i", "line_sub_i",
                     "line_sub_data_i"):
            self.wr(name, 0)
        self.wr("ser_tx_rxdet_i", 1)
        self.wr("rst_i", 1)
        self.wr("host_rst_i", 1)

    def wr(self, name: str, value: int) -> None:
        if self._last.get(name) != value:
            self._last[name] = value
            self.s[name].value = value

    def fail(self, msg: str) -> None:
        self.violations.append(f"cycle {self.cycle}: {msg}")

    def raise_violations(self) -> None:
        if self.violations:
            raise CheckError("; ".join(self.violations[:5]))

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
            self.wr("host_rst_i", int(self.rst))
            self.host.drive()
            self.app.drive()
            await ReadOnly()
            try:
                self._sample()
            except CheckError as exc:
                self.violations.append(str(exc))

    def _sample(self) -> None:
        rd, n = self.rd, self.cycle
        if self.rst:
            if n > 2:
                self._check(RESET_LINK, "in reset")
                self._check(RESET_FN, "in reset")
            return
        dl_up = bool(rd["dl_up_o"]())
        if self.dl_up and not dl_up:
            self.link_downs += 1
            self.app.reset()
            self.shadow.reset()
            self.irq_mode = "none"
            self.log.info("ep dl_up_o fell at cycle %d", n)
        self.dl_up = dl_up
        if not dl_up:
            self._check(RESET_FN, "while dl_up_o is 0")
        else:
            self.dl_up_cycles += 1
        self.bus_master_en = bool(rd["bus_master_en_o"]())
        self.mem_space_en = bool(rd["mem_space_en_o"]())
        self.d_state = rd["d_state_o"]()
        dl_err, rcv_err = rd["dl_err_o"](), rd["rcv_err_o"]()
        if dl_err or rcv_err:
            self.link_err_log.append((n, dl_err, rcv_err))
            self.shadow.events(dl=dl_err, rcv=rcv_err)
        if not self.allow_link_errors:
            for e, (de, re) in (("ep", ("dl_err_o", "rcv_err_o")),
                                ("host", ("host_dl_err_o", "host_rcv_err_o"))):
                if rd[de]() or rd[re]():
                    self.fail(f"{e}: {de} {rd[de]():#x}, {re} {rd[re]()} on the error-free line")
        err = rd["mon_tl_err_o"]()
        if err:
            self.tl_err_log.append((n, err))
            for b in range(9):
                if err >> b & 1:
                    self.tl_err_count[b] += 1
                    COV_ERR.sample(event=TL_ERR_NAMES[b])
            self.shadow.events(tl=err)
        self.np_pending = rd["mon_tl_np_pending_o"]()
        self.host.sample()
        self.app.sample()
        if (rd["host_tlp_tx_valid_i"]() or rd["host_tlp_rx_valid_o"]() or rd["cq_valid_o"]()
                or rd["rc_valid_o"]() or rd["rq_valid_i"]() or rd["cc_valid_i"]()):
            self.last_activity = n
        if dl_up and n - max(self.last_activity, self._live_from) > self.liveness \
                and not (self.host.model.idle and self.app.idle()) and not self.quiet_ok:
            self.fail(f"liveness: nothing moved for {self.liveness} cycles with work outstanding "
                      f"({self.state()})")
            self._live_from = n

    _live_from = 0
    last_activity = 0
    quiet_ok = False

    def _check(self, table: dict, when: str) -> None:
        for name, want in table.items():
            got = self.rd[name]()
            if got != want:
                self.fail(f"{when}: {name} expected {want}, observed {got}")

    def line_symbols(self, word: int) -> list[tuple[int, bool] | None]:
        """The two symbols of a line word as (byte, is K), None for a code
        that is not valid."""
        out = []
        for c in (word & 0x3FF, word >> 10):
            e = code8b10b.DECODE.get(c)
            out.append((e[0][0], e[0][1]) if e else None)
        return out

    def substitute_symbol(self, word: int) -> int | None:
        """``word`` (two 10-bit codes, the first in bits 9:0) with its first
        code, a data symbol, replaced by the code of another data byte that
        is valid at the same running disparity and leaves the same
        disparity after it: one byte changed on a valid line, so the
        receiver sees no receiver error. None when either code is not a
        data symbol."""
        codes = (word & 0x3FF, word >> 10)
        ents = [code8b10b.DECODE.get(c, []) for c in codes]
        if not all(e and not any(k for _, k, _ in e) for e in ents):
            return None
        # The code may be valid at both disparities (a balanced code): the
        # replacement must then be one code valid at both, with the same
        # disparity after it as the original at each.
        byte = ents[0][0][0]
        rds = {rd for _, _, rd in ents[0]}
        for b in range(256):
            if b == byte:
                continue
            outs = {rd: code8b10b.encode(b, False, rd) for rd in rds}
            c2s = {c for c, _ in outs.values()}
            if len(c2s) == 1 and all(r2 == code8b10b.rd_after(codes[0], rd)
                                     for rd, (_, r2) in outs.items()):
                return (word & ~0x3FF) | c2s.pop()
        return None

    def state(self) -> str:
        """A one-line summary of what is outstanding, for failure messages."""
        a, h = self.app, self.host.model
        return (f"cycle {self.cycle}, ep dl_up {int(self.dl_up)}, last activity "
                f"{self.last_activity}; host: outstanding "
                f"{[(r.kind, r.tag) for r in h.outstanding()]}, queue {h.tx_pending}, "
                f"sending {self.host.cur is not None}; app: rq queue {len(a.rq_q)}, rq "
                f"current {a.rq_cur is not None and (a.rq_cur['write'], a.rq_pos)}, reads out "
                f"{sorted(a.tags_busy)}, handles {sorted(a.handles)}, cc due {len(a.cc_due)}, "
                f"irq queue {len(a.irq_q)} open {a.irq_open is not None}; host received "
                f"{self._kinds(self.host.rx_log)}, sent {self._kinds(self.host.sent_log)}; "
                f"tl_err {self.tl_err_count}, rc beats seen {a.rc_seen}, rq taken {len(a.rq_taken)}")

    @staticmethod
    def _kinds(log) -> dict:
        out: dict[str, int] = {}
        for _, raw in log:
            k = ht.Tlp.decode(raw).kind
            out[k] = out.get(k, 0) + 1
        return out

    def on_host_down(self) -> None:
        self.log.info("host dl_up_o fell at cycle %d", self.cycle)

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

    async def link_up(self, limit: int = 40_000) -> int:
        n = await self.until(lambda: self.dl_up and self.host.dl_up, limit, "link up")
        self._live_from = self.cycle
        self.log.info("both ends DL_Up at cycle %d", self.cycle)
        return n

    async def run(self, gen, limit: int = 200_000, what: str = "host procedure"):
        """Run a host procedure to its end; returns its result."""
        proc = self.host.run(gen)
        await self.until(lambda: proc.done, limit, what)
        if proc.error is not None:
            raise CheckError(f"{what}: {proc.error}")
        return proc.result

    async def request(self, req: hm.Request, limit: int = 50_000, what: str = "request"):
        await self.until(lambda: req.done, limit, what)
        return req

    async def drain(self, limit: int = 200_000, quiet: int = 400) -> int:
        """Until host and application are idle, every rq request taken, and
        no stream has moved for ``quiet`` cycles (posted writes in flight
        on the link have arrived)."""
        return await self.until(lambda: self.host.model.idle and self.app.idle()
                                and self.host.cur is None and not self.app.cc_due
                                and self.cycle - self.last_activity >= quiet,
                                limit, "drain")

    # -- enumeration ---------------------------------------------------------------

    async def enumerate(self, bus_master: bool = True) -> hm.DeviceInfo:
        dev = await self.run(self.host.model.enumerate_device(EP_BDF, bus_master=bus_master),
                             what="enumeration")
        self.dev = dev
        return dev

    def finish(self) -> dict:
        """Final checks after a drain."""
        self.raise_violations()
        assert self.app.idle(), "application not idle at the end"
        assert not self.host.model.errors, f"host model errors: {self.host.model.errors[:3]}"
        self.shadow.check_messages()
        stats = {
            "cycles": self.cycle,
            "host_sent": len(self.host.sent_log),
            "host_received": len(self.host.rx_log),
            "cq_packets": len(self.app.cq_log),
            "cc_packets": self.app.cc_done,
            "rq_taken": len(self.app.rq_taken),
            "irqs": [(r["vector"], IRQ_NAMES.get(r["status"])) for r in self.app.irq_log],
            "interrupts": len(self.host.model.interrupts),
            "messages": [hex(m.code) for m in self.host.model.messages],
            "tl_err": dict(zip(TL_ERR_NAMES, self.tl_err_count)),
            "shadow_compared": self.shadow.compared,
            "shadow_msgs": self.shadow.msgs_checked,
            "link_downs": self.link_downs,
        }
        self.log.info("core e2e done: %s", stats)
        return stats
