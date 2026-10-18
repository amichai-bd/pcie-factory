"""Environment of the pcie_tl testbench (wiki/src/dv/tl/DV_PLAN.md,
Strategy). The DUT is driven only through its ports (tb/pcie_tl_tb.sv);
the MAS-named slot nets are read through the bind tap.

One cycle: at the falling edge of clk_i every input is driven, then, once
the design has settled (ReadOnly), every output is read. A handshake seen
then (valid and ready both 1) happens at the next rising edge and belongs
to this cycle.

- The **link driver** sends queued TLPs on ``tlp_rx_*``, one beat per
  cycle with no gap inside a TLP (the dll delivers whole TLPs) and random
  gaps between them. Like a host behind the dll's flow control it sends a
  posted or non-posted TLP only while the credits it holds in ``tl`` plus
  its own stay within the advertised ones, and it gets them back from the
  release pulses (HAS, credit release).
- The **cfg stand-in** drives the register state (``Regs``) and serves
  ``reg_req``: it takes an access with probability ``reg_ready_p`` and
  finishes it 1 to ``reg_lat`` cycles later with ``reg_cpl_valid``, the
  data of a read from its register map (TL_CFG register access).
- The **application** takes ``cq`` beats with probability ``cq_ready_p``
  and checks the stream contract (APP: valid held until taken, header held
  from sop to eop).
- The **reference** (``models/pcie_tl_rx_ref.py``, written from the
  ROUTING and TLP pages) judges each TLP as it is sent; the expectations
  it makes are checked as the DUT acts: the cq packets, the register
  accesses and completion records of the cfg line and of BAR2 writes, the
  PME_TO_Ack requests, the error pulses (exactly one cycle after the eop),
  the completion beats to the tracker slot, the posted releases in arrival
  order with their data credits, and the non-posted release totals.
  Ordering is checked at every leave: nothing that must wait for earlier
  posted requests goes before their releases were seen.
- The **shadow**, the architecture model's ``classify``, judges the same
  TLP; any disagreement with the reference fails the test.

Transmit (#291):

- The **dll stand-in** takes ``tlp_tx_*`` beats with probability
  ``tx_ready_p`` and keeps the partner's credits per class, as the dll
  shows them on ``fc_*_avail_i``: a TLP's credits are consumed when its
  sop beat is taken and come back some cycles later, unless a test holds
  that class. Every offered sop must find its credits available, and an
  offered beat stays unchanged until taken.
- The **application** answers each read taken on ``cq`` with one ``cc``
  packet after a random latency, in any order, with random data and gaps;
  issues the requests a test queues on ``rq``; the **cfg stand-in** offers
  the messages a test queues on ``msg_*``, some withdrawn after a few
  cycles, and captures the Bus and Device Number of each configuration
  write it takes.
- The **transmit reference** (``models/pcie_tl_tx_ref.py``, written from
  the TLP and COMPLETIONS pages) forms every TLP tl must send: each rq
  request, interrupt write, error message and PME_TO_Ack when it is taken,
  each read's completions when cc or cfg gives its data, each
  Unsupported Request completion when it is handed on. Each sent TLP must
  equal one of them byte for byte, the ID bytes as bus_num and dev_num
  then: posted requests in the order taken, rq reads in the order taken,
  each read's completions in order; and no non-posted request or
  completion before every posted request taken before it.
"""

from __future__ import annotations

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
sys.path.append(str(_DV.parents[1] / "arch" / "tl"))
import pcie_tl_arch as arch  # noqa: E402
import pcie_tl_rx_ref as ref  # noqa: E402
import pcie_tl_tx_ref as txr  # noqa: E402

# Packed structs of pcie_tl_pkg, most significant field first.
CPL_REC = (("kind", 2), ("rid", 16), ("tag", 8), ("tc", 3), ("attr", 2), ("mem", 1),
           ("locked", 1), ("addr", 12), ("dw_len", 11), ("fbe", 4), ("lbe", 4), ("dw_idx", 11),
           ("dw_last", 1), ("data", 32))
HD_CTX = (("handle", 3), ("rid", 16), ("tag", 8), ("tc", 3), ("attr", 2), ("addr", 10),
          ("dw_len", 11), ("fbe", 4), ("lbe", 4))
RX_BEAT = (("valid", 1), ("data", 32), ("sop", 1), ("eop", 1))
RQ_CTX = (("tag", 3), ("addr", 10), ("dw_len", 6), ("fbe", 4), ("lbe", 4))
CR_UR, CR_CFG_WR, CR_CFG_RD, CR_BAR2_RD = range(4)

# Outputs with a reset value (HAS, reset), checked in every reset cycle.
RESET_ZERO = ("tlp_tx_valid_o", "tlp_rx_ready_o", "fc_rel_p_o", "fc_rel_np_o", "reg_req_valid_o",
              "tl_err_o", "tl_np_pending_o", "msg_ready_o", "cq_valid_o", "cc_ready_o",
              "rq_ready_o", "rc_valid_o")
# Outputs of the block not built yet (#292): 0 in every cycle.
UNBUILT_ZERO = ("tl_np_pending_o", "rc_valid_o")
# Credit field widths (dll credits available): all ones is infinite.
CR_MAX = {"h": 0xFF, "d": 0xFFF}

OUTCOMES = ("cq", "bar2", "cfg", "ur", "drop", "pme", "malformed", "cpl")
KIND_NAMES = ("mem", "mem_locked", "io", "cfg0", "cfg1", "atomic", "msg", "cpl", "cpl_locked",
              "undefined")

# Functional coverage (DV plan, coverage): bins from ROUTING.md.
COV_RX = Covergroup("tl_rx_outcome")
COV_RX.coverpoint("outcome", OUTCOMES)
COV_RX.coverpoint("cls", ("P", "NP", "CPL"))
COV_RX.coverpoint("kind", KIND_NAMES)
COV_RX.coverpoint("err", {"none": None, "ur_posted": 0, "ur_nonposted": 1, "malformed": 2,
                          "poisoned": 3})
COV_RX.cross("outcome_x_cls", "outcome", "cls", unreachable=(
    ("cq", "CPL"), ("bar2", "CPL"), ("cfg", "P"), ("cfg", "CPL"), ("ur", "CPL"), ("drop", "NP"),
    ("drop", "CPL"), ("pme", "NP"), ("pme", "CPL"), ("cpl", "P"), ("cpl", "NP")))
COV_ORDER = Covergroup("tl_rx_order")
COV_ORDER.coverpoint("event", ("read_waits_handle", "write_passes_read", "np_waits_posted",
                               "cfg_passes_cq_read", "two_np_leave", "three_np_leave",
                               "np_data_carry", "cq_stall", "reg_stall",
                               "posted_queue_full", "np_queue_full"))
TX_KINDS = ("mwr32", "mwr64", "mrd32", "mrd64", "irq32", "irq64", "err_cor", "err_nonfatal",
            "err_fatal", "pme_to_ack", "cpl_data", "cpl_nodata", "cpl_ur", "cpl_locked")
COV_TX = Covergroup("tl_tx")
COV_TX.coverpoint("kind", TX_KINDS)
COV_TX.coverpoint("split", {"one": 1, "two": 2, "three_plus": range(3, 33)})
COV_TX.coverpoint("event", ("posted_passes_np", "cpl_interleave", "credit_wait_p",
                            "credit_wait_np", "credit_wait_cpl", "tx_stall", "cc_wait",
                            "cc_gap", "rq_blocked", "msg_withdrawn", "msg_wait",
                            "two_posted_one_edge", "completion_after_posted"))


def unpack(value: int, fields) -> dict:
    out, pos = {}, sum(w for _, w in fields)
    for name, w in fields:
        pos -= w
        out[name] = (value >> pos) & ((1 << w) - 1)
    return out


class TlError(AssertionError):
    pass


class TlEnv:
    def __init__(self, dut, seed: int, *, regs: ref.Regs | None = None, cq_ready_p: float = 1.0,
                 reg_ready_p: float = 1.0, reg_lat: int = 2, gap_p: float = 0.0,
                 tx_ready_p: float = 1.0, cc_lat: int = 8, cc_gap_p: float = 0.0,
                 rq_gap_p: float = 0.0, credits: dict | None = None, cr_lat: int = 20):
        self.dut = dut
        self.log = dut._log
        self.rng = random.Random(seed)
        self.seed = seed
        self.fc: dict[str, int] = {}
        self.bar0_w = 12
        self.regs = regs or ref.Regs()
        self.cq_ready_p, self.reg_ready_p, self.reg_lat, self.gap_p = (cq_ready_p, reg_ready_p,
                                                                       reg_lat, gap_p)
        self.tx_ready_p, self.cc_lat, self.cc_gap_p, self.rq_gap_p = (tx_ready_p, cc_lat, cc_gap_p,
                                                                      rq_gap_p)
        # The partner's credits per class (h, d); None: infinite (dll credits
        # available). cr_hold: classes whose credits do not come back.
        self.credits = {"P": (16, 64), "NP": (8, 8), "CPL": None, **(credits or {})}
        self.cr_lat = cr_lat
        self.cr_hold: set[str] = set()
        self.cc_hold = False                  # the application answers no cq read
        self.bm_en = True                     # Bus Master Enable
        self.bus, self.dev = 0, 0             # captured by configuration writes
        self.cycle = 0
        self.rst = 1
        self.dl_up = 0
        self.regmap: dict[tuple[int, int], int] = {}
        self.cq_stall_until = 0
        self.reg_stall_until = 0
        self._clear()
        self.sent = 0
        self.two_np = 0                       # cycles where a cq read and a cfg-line request left
        self.three_np = 0                     # cycles where three non-posted requests left
        self.last_start = (0, 0)              # (cycle, beats) of the last TLP started
        self.cq_spans: list = []              # (first beat cycle, last beat cycle, beats) per cq write
        self.records: list = []               # (cycle, kind) of every completion record
        self.tx_kinds: list = []              # kind of every TLP sent
        self.splits: list = []                # completions per read answered

    # ------------------------------------------------------------ state
    def _clear(self) -> None:
        """Everything tl drops in reset."""
        self.txq: deque = deque()             # TLPs waiting to be sent
        self.beats: deque = deque()           # beats of the TLP being sent
        self.held = {"ph": 0, "pd": 0, "nph": 0, "npd": 0}
        self.posted: deque = deque()          # posted TLPs not yet released, in arrival order
        self.p_rx = 0                         # posted TLPs received
        self.p_rel = 0                        # posted release pulses seen
        self.cq_writes: deque = deque()
        self.cq_reads: deque = deque()
        self.cfg_jobs: deque = deque()        # cfg line, in arrival order
        self.bar2_writes: deque = deque()     # posted BAR2 writes, in arrival order
        self.pme: deque = deque()
        self.err_at: dict[int, int] = {}
        self.cpl_beats: deque = deque()
        self.np_rx = [0, 0]                   # non-posted headers, data credits received
        self.np_rel = [0, 0]                  # released
        self.np_left = [0, 0]                 # left tl: headers, data credits
        self.np_owe = [0, 0]                  # owed by the HAS release rule
        self.np_leaves: list = []             # this cycle's non-posted leaves (data credits)
        self.np_mal_at: dict[int, list] = {}  # cycle -> malformed non-posted leaves then
        self.p_rel_due: dict[int, dict] = {}  # cycle -> the posted request released then
        self.handles: set[int] = set()
        self.job = None                       # the register job being served
        self.access = None                    # the access cfg took, not yet finished
        self.cq_pkt = None                    # the cq packet being taken
        self.cq_prev = None                   # last cycle's cq offer, not taken
        self.reg_prev = None                  # last cycle's reg_req offer, not taken
        self.outcomes: list = []
        # Transmit.
        self.tx_post: deque = deque()         # posted TLPs expected, in the order taken
        self.tx_np: deque = deque()           # rq reads expected, in the order taken
        self.cpl_reqs: list = []              # per request: its completions expected, in order
        self.p_taken = 0                      # posted requests taken into the transmit line
        self.p_taken_prev = 0                 # ... before this cycle
        self.p_sent = 0                       # posted TLPs sent (eop taken)
        self.tx_cur = None                    # the TLP being taken from tlp_tx_*
        self.tx_prev = None                   # last cycle's tlp_tx offer, not taken
        self.cr_avail = {c: (None if v is None else list(v)) for c, v in self.credits.items()}
        self.cr_back: list = []               # (cycle, class, data credits) coming back
        self.cc_out: list = []                # reads taken on cq, not yet answered
        self.cc_cur = None                    # the cc packet being given
        self.cc_drv = None                    # this cycle's cc beat
        self.rq_cur = None                    # the rq request being given
        self.rq_drv = None                    # this cycle's rq beat
        self.msg_cur = None                   # the message offered
        self.rq_q: deque = deque()            # requests the application will issue (dropped
        self.msg_q: deque = deque()           # at reset), messages cfg will offer
        self.msg_drv = None                   # this cycle's msg offer
        self.last_cpl_req = None

    def set_regs(self, **kw) -> None:
        """Change the register state; only while nothing is in flight, so
        each TLP is judged with the state it meets."""
        if self.busy():
            raise TlError(f"cycle {self.cycle}: register state changed with traffic in flight")
        for k, v in kw.items():
            setattr(self.regs, k, v)

    def busy(self) -> bool:
        return bool(self.txq or self.beats or self.cq_writes or self.cq_reads or self.cfg_jobs
                    or self.bar2_writes or self.posted or self.access or self.pme
                    or self.cpl_beats or self.np_rel != self.np_rx or self.err_at
                    or not self._tx_idle(False))

    def _tx_idle(self, hold_ok: bool) -> bool:
        """Nothing left to send or answer (``hold_ok``: unanswered reads
        held by cc_hold do not count)."""
        return not (self.tx_post or self.tx_np or self.cpl_reqs or self.tx_cur or self.rq_q
                    or self.rq_cur or self.msg_q or self.msg_cur or self.cc_cur
                    or (self.cc_out and not (hold_ok and self.cc_hold)))

    # ------------------------------------------------------------ start
    async def start(self, cycles_in_reset: int = 8, clock: bool = True) -> None:
        if clock:
            cocotb.start_soon(Clock(self.dut.clk_i, 8, unit="ns").start())
        await FallingEdge(self.dut.clk_i)
        self._read_cfg()
        for name in ("tlp_rx_valid_i", "tlp_rx_data_i", "tlp_rx_sop_i", "tlp_rx_eop_i",
                     "tlp_tx_ready_i", "reg_req_ready_i", "reg_cpl_valid_i", "reg_cpl_data_i",
                     "msg_valid_i", "msg_kind_i", "msg_addr_i", "msg_data_i", "cq_ready_i",
                     "cc_valid_i", "cc_sop_i", "cc_eop_i", "cc_data_i", "cc_handle_i",
                     "rq_valid_i", "rq_sop_i", "rq_eop_i", "rq_data_i", "rq_write_i",
                     "rq_addr_i", "rq_len_i", "rq_first_be_i", "rq_last_be_i", "rq_tag_i",
                     "rc_ready_i", "bus_num_i", "dev_num_i", "bus_master_en_i",
                     "cpl_timeout_dis_i"):
            getattr(self.dut, name).value = 0
        self._drive_credits()
        self.rst, self.dl_up = 1, 0
        await self.run(cycles_in_reset)
        self.rst = 0
        await self.run(2)
        self.dl_up = 1

    def _read_cfg(self) -> None:
        """The DUT's parameters, from the testbench's cfg_o."""
        cfg = int(self.dut.cfg_o.value)
        self.fc = {"ph": cfg >> 48, "pd": (cfg >> 36) & 0xFFF, "nph": (cfg >> 28) & 0xFF,
                   "npd": (cfg >> 16) & 0xFFF}
        self.bar0_w = cfg & 0x1F
        self.regs.bar0_addr_w = self.bar0_w

    def send(self, tlp: bytes) -> None:
        self.txq.append(tlp)

    def request(self, write: bool, addr: int, length: int, fbe: int = 0xF, lbe: int | None = None,
                tag: int = 0, data=None) -> None:
        """Queue an rq request (APP requester requests)."""
        if lbe is None:
            lbe = 0 if length == 1 else 0xF
        if write and data is None:
            data = [self.rng.getrandbits(32) for _ in range(length)]
        self.rq_q.append({"write": write, "addr": addr, "len": length, "fbe": fbe, "lbe": lbe,
                          "tag": tag, "data": data})

    def offer_msg(self, kind: int, addr: int = 0, data: int = 0, withdraw: int | None = None) -> None:
        """Queue a message for cfg to offer (TL_CFG messages); withdrawn
        when not taken within ``withdraw`` cycles."""
        self.msg_q.append({"kind": kind, "addr": addr, "data": data, "withdraw": withdraw})

    @property
    def my_id(self) -> int:
        return txr.my_id_of(self.bus, self.dev)

    async def run(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.step()

    async def drain(self, limit: int = 20000, stuck_reads: int = 0) -> None:
        """Run until every expectation is met (``stuck_reads`` BAR0 reads may
        still wait for a handle, with their credits held)."""
        for _ in range(limit):
            await self.step()
            if self._drained(stuck_reads):
                await self.run(4)
                if self._drained(stuck_reads):
                    return
        raise TlError(f"not drained after {limit} cycles: txq {len(self.txq)}, cq writes "
                      f"{len(self.cq_writes)}, cq reads {len(self.cq_reads)}, cfg jobs "
                      f"{len(self.cfg_jobs)}, BAR2 writes {len(self.bar2_writes)}, posted "
                      f"unreleased {len(self.posted)}, PME {len(self.pme)}, np rx {self.np_rx} "
                      f"rel {self.np_rel}, held {self.held}; transmit: posted {len(self.tx_post)}, "
                      f"reads {len(self.tx_np)}, completion requests {len(self.cpl_reqs)}, rq "
                      f"{len(self.rq_q)}, msg {len(self.msg_q)}, cc out {len(self.cc_out)}, "
                      f"credits {self.cr_avail}")

    def _drained(self, stuck: int) -> bool:
        if (self.txq or self.beats or self.cq_writes or self.cfg_jobs or self.bar2_writes
                or self.posted or self.access or self.pme or self.cpl_beats or self.err_at
                or self.cq_pkt or not self._tx_idle(True)):
            return False
        if len(self.cq_reads) != stuck:
            return False
        return self.np_rel == [self.np_rx[0] - stuck, self.np_rx[1]]

    async def link_down(self, cycles: int = 4) -> None:
        """dl_up_i low: tl drops everything (HAS, reset); so do the host, the
        dll and cfg (its captured numbers too)."""
        self.dl_up = 0
        await self.run(cycles)
        self._clear()
        self.bus, self.dev = 0, 0
        self.dl_up = 1

    # ------------------------------------------------------------- cycle
    async def step(self) -> None:
        await FallingEdge(self.dut.clk_i)
        self.cycle += 1
        self._drive()
        await ReadOnly()
        self._observe()

    def _in_reset(self) -> bool:
        return bool(self.rst or not self.dl_up)

    def _drive(self) -> None:
        d, r = self.dut, self.regs
        d.rst_i.value = self.rst
        d.dl_up_i.value = self.dl_up
        d.mem_space_en_i.value = int(r.mem_space_en)
        d.d_state_i.value = r.d_state
        d.bar0_base_i.value = r.bar0_base
        d.bar2_base_i.value = r.bar2_base
        # Link: the next beat, or a new TLP when credits allow.
        beat = None
        if not self._in_reset():
            if not self.beats and self.txq and self.rng.random() >= self.gap_p:
                self._start_tlp()
            if self.beats:
                beat = self.beats.popleft()
        if beat is None:
            d.tlp_rx_valid_i.value = 0
            d.tlp_rx_sop_i.value = 0
            d.tlp_rx_eop_i.value = 0
        else:
            data, sop, eop = beat
            d.tlp_rx_valid_i.value = 1
            d.tlp_rx_data_i.value = data
            d.tlp_rx_sop_i.value = sop
            d.tlp_rx_eop_i.value = eop
            self.beat_now = beat
        self.beat_driven = beat
        # Application.
        cq_ready = self.cycle >= self.cq_stall_until and self.rng.random() < self.cq_ready_p
        d.cq_ready_i.value = int(cq_ready)
        # cfg stand-in: take only with nothing outstanding; finish after its latency.
        reg_ready = (self.access is None and self.cycle >= self.reg_stall_until
                     and self.rng.random() < self.reg_ready_p)
        d.reg_req_ready_i.value = int(reg_ready)
        self.reg_cpl_now = None
        if self.access is not None and self.cycle >= self.access["done_at"]:
            self.reg_cpl_now = self.access
            d.reg_cpl_valid_i.value = 1
            d.reg_cpl_data_i.value = self.access["rdata"]
        else:
            d.reg_cpl_valid_i.value = 0
            d.reg_cpl_data_i.value = self.rng.getrandbits(32)
        if self.cycle < self.cq_stall_until or self.cycle < self.reg_stall_until:
            COV_ORDER.sample(event="cq_stall" if self.cycle < self.cq_stall_until else "reg_stall")
        # Transmit side.
        d.bus_num_i.value = self.bus
        d.dev_num_i.value = self.dev
        self.id_driven = self.my_id
        d.bus_master_en_i.value = int(self.bm_en)
        d.tlp_tx_ready_i.value = int(self.rng.random() < self.tx_ready_p)
        for c, h, dd in [x for x in self.cr_back if x[0] <= self.cycle and x[1] not in self.cr_hold]:
            self.cr_back.remove((c, h, dd))
            if self.cr_avail[h] is not None:          # a test may have made it infinite
                self.cr_avail[h][0] += 1
                self.cr_avail[h][1] += dd
        self._drive_credits()
        self._drive_cc()
        self._drive_rq()
        self._drive_msg()

    def _drive_credits(self) -> None:
        for cls, (ph, pd) in (("P", ("fc_ph_avail_i", "fc_pd_avail_i")),
                              ("NP", ("fc_nph_avail_i", "fc_npd_avail_i")),
                              ("CPL", ("fc_cplh_avail_i", "fc_cpld_avail_i"))):
            v = self.cr_avail[cls]
            getattr(self.dut, ph).value = CR_MAX["h"] if v is None else v[0]
            getattr(self.dut, pd).value = CR_MAX["d"] if v is None else v[1]

    def _drive_cc(self) -> None:
        """The application: one cc packet per read taken, in any order."""
        d = self.dut
        if self.cc_cur is None and self.cc_out and not self.cc_hold and not self._in_reset():
            ready = [r for r in self.cc_out if r["at"] <= self.cycle]
            if ready:
                r = self.rng.choice(ready)
                self.cc_out.remove(r)
                n = r["h"]["length"]
                data = [self.rng.getrandbits(32) for _ in range(n)]
                self.cc_cur = {"r": r, "data": data, "k": 0}
        self.cc_drv = None
        if self.cc_cur is not None and not self._in_reset():
            c = self.cc_cur
            held = c.get("offered", False)
            if held or c["k"] == 0 or self.rng.random() >= self.cc_gap_p:
                n = len(c["data"])
                self.cc_drv = (c["data"][c["k"]], int(c["k"] == 0), int(c["k"] == n - 1),
                               c["r"]["hd"])
            elif c["k"]:
                COV_TX.sample(event="cc_gap")
        if self.cc_drv is None:
            d.cc_valid_i.value = 0
            d.cc_sop_i.value = 0
            d.cc_eop_i.value = 0
        else:
            d.cc_valid_i.value = 1
            d.cc_data_i.value, d.cc_sop_i.value, d.cc_eop_i.value, d.cc_handle_i.value = self.cc_drv

    def _drive_rq(self) -> None:
        d = self.dut
        if self.rq_cur is None and self.rq_q and not self._in_reset():
            self.rq_cur = {"req": self.rq_q.popleft(), "k": 0}
        self.rq_drv = None
        if self.rq_cur is not None and not self._in_reset():
            c, req = self.rq_cur, self.rq_cur["req"]
            if c.get("offered") or c["k"] == 0 or self.rng.random() >= self.rq_gap_p:
                n = req["len"] if req["write"] else 1
                data = req["data"][c["k"]] if req["write"] else self.rng.getrandbits(32)
                self.rq_drv = (data, int(c["k"] == 0), int(c["k"] == n - 1))
        if self.rq_drv is None:
            d.rq_valid_i.value = 0
            d.rq_sop_i.value = 0
            d.rq_eop_i.value = 0
            return
        req = self.rq_cur["req"]
        d.rq_valid_i.value = 1
        d.rq_data_i.value, d.rq_sop_i.value, d.rq_eop_i.value = self.rq_drv
        d.rq_write_i.value = int(req["write"])
        d.rq_addr_i.value = req["addr"]
        d.rq_len_i.value = req["len"]
        d.rq_first_be_i.value = req["fbe"]
        d.rq_last_be_i.value = req["lbe"]
        d.rq_tag_i.value = req["tag"]

    def _drive_msg(self) -> None:
        d = self.dut
        if self.msg_cur is None and self.msg_q and not self._in_reset():
            self.msg_cur = {"m": self.msg_q.popleft(), "since": self.cycle}
        self.msg_drv = None
        if self.msg_cur is not None and not self._in_reset():
            m = self.msg_cur["m"]
            if m["withdraw"] is not None and self.cycle - self.msg_cur["since"] >= m["withdraw"]:
                COV_TX.sample(event="msg_withdrawn")
                self.msg_cur = None
            else:
                self.msg_drv = m
        if self.msg_drv is None:
            d.msg_valid_i.value = 0
            return
        d.msg_valid_i.value = 1
        d.msg_kind_i.value = self.msg_drv["kind"]
        d.msg_addr_i.value = self.msg_drv["addr"]
        d.msg_data_i.value = self.msg_drv["data"]

    def _fits(self, cls: str, d: int) -> bool:
        if cls == "P":
            return self.held["ph"] + 1 <= self.fc["ph"] and self.held["pd"] + d <= self.fc["pd"]
        if cls == "NP":
            return self.held["nph"] + 1 <= self.fc["nph"] and self.held["npd"] + d <= self.fc["npd"]
        return True

    def _start_tlp(self) -> None:
        tlp = self.txq[0]
        cls, d = ref.credit_class(tlp)
        if not self._fits(cls, d):
            COV_ORDER.sample(event="posted_queue_full" if cls == "P" else "np_queue_full")
            return
        self.txq.popleft()
        if cls == "P":
            self.held["ph"] += 1
            self.held["pd"] += d
        elif cls == "NP":
            self.held["nph"] += 1
            self.held["npd"] += d
        beats = ref.to_beats(tlp)
        n = len(beats)
        self.last_start = (self.cycle, n)
        self.beats.extend((b, int(i == 0), int(i == n - 1)) for i, b in enumerate(beats))
        # The eop is driven n - 1 cycles from now; its error pulse one after.
        self._expect(tlp, self.cycle + n)

    def _expect(self, tlp: bytes, err_cycle: int) -> None:
        v = ref.judge(tlp, self.regs)
        self._shadow(tlp, v)
        self.sent += 1
        self.outcomes.append(v.outcome)
        COV_RX.sample(outcome=v.outcome, cls=v.cls, kind=v.kind, err=v.err)
        h = v.fields
        if v.err is not None:
            if err_cycle in self.err_at:
                raise TlError(f"two error pulses expected in cycle {err_cycle}")
            self.err_at[err_cycle] = 1 << v.err
        if v.cls == "CPL":
            if v.outcome == "cpl":
                self.cpl_beats.extend(ref.to_beats(tlp))
            return
        if v.cls == "P":
            item = {"idx": self.p_rx, "d": v.data_credits, "outcome": v.outcome, "h": h}
            self.posted.append(item)
            if v.outcome == "cq":
                self.cq_writes.append(item)
            elif v.outcome == "bar2":
                item["acc"] = deque(ref.bar2_accesses(h, True))
                self.bar2_writes.append(item)
            elif v.outcome == "pme":
                self.pme.append(item)
            self.p_rx += 1
            return
        self.np_rx[0] += 1
        self.np_rx[1] += v.data_credits
        item = {"p_before": self.p_rx, "h": h, "outcome": v.outcome, "d": v.data_credits}
        if v.outcome == "cq":
            self.cq_reads.append(item)
        elif v.outcome == "cfg":
            write = bool(h["fmt"] & 2)
            item["job"] = CR_CFG_WR if write else CR_CFG_RD
            item["acc"] = deque([ref.cfg_access(h, write)])
            self.cfg_jobs.append(item)
        elif v.outcome == "bar2":
            item["job"] = CR_BAR2_RD
            item["acc"] = deque(ref.bar2_accesses(h, False))
            self.cfg_jobs.append(item)
        elif v.outcome == "ur":
            item["job"] = CR_UR
            item["acc"] = deque()
            self.cfg_jobs.append(item)
        else:
            # Malformed: leaves at once, at the edge ending its push cycle.
            self.np_mal_at.setdefault(err_cycle, []).append(v.data_credits)

    def _shadow(self, tlp: bytes, v: ref.Verdict) -> None:
        """The architecture model's classification of the same TLP."""
        c = arch.Cfg()
        c.mem_en, c.d_state = self.regs.mem_space_en, self.regs.d_state
        c.bar0, c.bar2 = self.regs.bar0_base, self.regs.bar2_base
        kind, bit = arch.classify(arch.parse(tlp), c, self.regs.bar0_addr_w)
        names = {"pme_ack": "pme", "malformed": "malformed"}
        got = names.get(kind, kind)
        if kind == "drop" and bit == arch.E_UR_P:
            got = "ur"
        if (got, bit) != (v.outcome, v.err):
            raise TlError(f"shadow disagrees on {tlp.hex()}: reference {v.outcome}/{v.err}, "
                          f"architecture model {kind}/{bit}")

    # ----------------------------------------------------------- observe
    def _val(self, name: str) -> int:
        return int(getattr(self.dut, name).value)

    def _observe(self) -> None:
        t = self.cycle
        if self._in_reset():
            for name in RESET_ZERO:
                if self._val(name):
                    raise TlError(f"cycle {t}: {name} is {self._val(name)} in reset")
            return
        for name in UNBUILT_ZERO:
            if self._val(name):
                raise TlError(f"cycle {t}: {name} is {self._val(name)}; its block is not built")
        if not self._val("tlp_rx_ready_o"):
            raise TlError(f"cycle {t}: tlp_rx_ready_o 0 with the data link up")
        self.p_taken_prev = self.p_taken
        self._check_err(t)
        self._check_release(t)
        self._check_cpl_beats(t)
        cq_read_take = self._check_cq(t)
        np_leave = self._check_reg(t)
        if cq_read_take:
            self.np_leaves.append(0)
        if cq_read_take and np_leave:
            COV_ORDER.sample(event="two_np_leave")
            self.two_np += 1
        self.np_leaves.extend(self.np_mal_at.pop(t, []))
        if len(self.np_leaves) >= 3:
            COV_ORDER.sample(event="three_np_leave")
            self.three_np += 1
        # This cycle's leaves are owed from the next cycle (HAS, credit release).
        for d in self.np_leaves:
            self.np_left[0] += 1
            self.np_left[1] += d
            self.np_owe[0] += 1
            self.np_owe[1] += d
        self.np_leaves = []
        takes0 = self.p_taken
        self._check_rq(t)
        self._check_msg(t)
        if self._val("probe_pme_ack_o"):
            if not self.pme:
                raise TlError(f"cycle {t}: PME_TO_Ack requested with no PME_Turn_Off held")
            item = self.pme.popleft()
            self.p_rel_due[t + 1] = item
            if self.p_rel < item["idx"]:
                raise TlError(f"cycle {t}: PME_Turn_Off left before an earlier posted request")
            self._posted_taken(t, txr.pme_to_ack(0), "pme_to_ack")
        if self.p_taken - takes0 >= 2:
            COV_TX.sample(event="two_posted_one_edge")
        self._check_cc(t)
        self._check_tx(t)

    def _check_err(self, t: int) -> None:
        got = self._val("tl_err_o")
        want = self.err_at.pop(t, 0)
        if got != want:
            raise TlError(f"cycle {t}: tl_err_o expected {want:#05x} got {got:#05x}")

    def _check_release(self, t: int) -> None:
        """Releases by the HAS rule: a request that left at the edge ending
        cycle t - 1 or earlier; a posted one whose leave is seen exactly in
        the next cycle; non-posted ones one header per cycle while any is
        owed, with the smaller of 8 and the data credits owed."""
        due = self.p_rel_due.pop(t, None)
        if due is not None:
            if not self._val("fc_rel_p_o") or not self.posted or self.posted[0] is not due:
                raise TlError(f"cycle {t}: posted request {due['idx']} ({due['outcome']}) left "
                              "in the last cycle but is not released now")
        want_np = self.np_owe[0] > 0
        want_npd = min(8, self.np_owe[1])
        got_np = bool(self._val("fc_rel_np_o"))
        if got_np != want_np or (got_np and self._val("fc_rel_npd_o") != want_npd):
            raise TlError(f"cycle {t}: fc_rel_np_o {int(got_np)} data {self._val('fc_rel_npd_o')}, "
                          f"the release rule wants {int(want_np)} data {want_npd} (owed "
                          f"{self.np_owe})")
        if want_np:
            if self.np_owe[1] > 8:
                COV_ORDER.sample(event="np_data_carry")
            self.np_owe[0] -= 1
            self.np_owe[1] -= want_npd
        if self._val("fc_rel_p_o"):
            d = self._val("fc_rel_pd_o")
            if not self.posted:
                raise TlError(f"cycle {t}: posted release with no posted request held")
            item = self.posted.popleft()
            if d != item["d"]:
                raise TlError(f"cycle {t}: posted release {item['idx']} ({item['outcome']}) "
                              f"data credits expected {item['d']} got {d}")
            if any(x is item for q in (self.cq_writes, self.bar2_writes, self.pme) for x in q):
                raise TlError(f"cycle {t}: posted request {item['idx']} ({item['outcome']}) "
                              "released before it left")
            self.p_rel += 1
            self.held["ph"] -= 1
            self.held["pd"] -= d
        if self._val("fc_rel_np_o"):
            d = self._val("fc_rel_npd_o")
            if d > 8:
                raise TlError(f"cycle {t}: non-posted release of {d} data credits")
            self.np_rel[0] += 1
            self.np_rel[1] += d
            self.held["nph"] -= 1
            self.held["npd"] -= d
            if self.np_rel[0] > self.np_left[0] or self.np_rel[1] > self.np_left[1]:
                raise TlError(f"cycle {t}: released {self.np_rel} non-posted credits, only "
                              f"{self.np_left} left tl")

    def _check_cpl_beats(self, t: int) -> None:
        b = unpack(self._val("probe_cpl_beat_o"), RX_BEAT)
        if b["valid"]:
            if not self.cpl_beats or self.cpl_beats[0] != b["data"]:
                want = f"{self.cpl_beats[0]:#010x}" if self.cpl_beats else "none"
                raise TlError(f"cycle {t}: completion beat to the tracker {b['data']:#010x}, "
                              f"expected {want}")
            self.cpl_beats.popleft()

    # cq: the application side.
    def _check_cq(self, t: int) -> bool:
        if not self._val("cq_valid_o"):
            if self.cq_prev is not None:
                raise TlError(f"cycle {t}: cq_valid_o dropped before its beat was taken")
            if self._val("probe_rd_take_o"):
                raise TlError(f"cycle {t}: a read taken with cq idle")
            return False
        f = {n: self._val(n) for n in ("cq_sop_o", "cq_eop_o", "cq_data_o", "cq_write_o",
                                       "cq_addr_o", "cq_len_o", "cq_first_be_o", "cq_last_be_o",
                                       "cq_handle_o")}
        if not f["cq_write_o"]:
            f.pop("cq_data_o")
        if self.cq_prev is not None and self.cq_prev != f:
            raise TlError(f"cycle {t}: cq beat changed before it was taken: {self.cq_prev} to {f}")
        if self.cq_pkt is not None:
            hdr = ("cq_write_o", "cq_addr_o", "cq_len_o", "cq_first_be_o", "cq_last_be_o")
            if any(f[k] != self.cq_pkt["f"][k] for k in hdr):
                raise TlError(f"cycle {t}: cq header changed inside a packet")
        if self.cq_prev is None and not f["cq_write_o"]:
            # A read is offered with the lowest handle free then; it keeps it
            # until taken, even if a lower one is freed meanwhile.
            free = [i for i in range(8) if i not in self.handles]
            self.cq_rd_lowest = free[0] if free else None
        take = bool(self._val("cq_ready_i"))
        self.cq_prev = None if take else f
        if not take:
            return False
        mask = (1 << self.bar0_w) - 1
        if f["cq_write_o"]:
            if f["cq_sop_o"]:
                if self.cq_pkt is not None or not self.cq_writes:
                    raise TlError(f"cycle {t}: unexpected cq write sop")
                item = self.cq_writes[0]
                h = item["h"]
                want = (h["addr"] & mask, h["length"], h["fbe"], h["lbe"])
                got = (f["cq_addr_o"], f["cq_len_o"], f["cq_first_be_o"], f["cq_last_be_o"])
                if got != want:
                    raise TlError(f"cycle {t}: cq write (addr, len, fbe, lbe) expected {want} "
                                  f"got {got}")
                if self.p_rel < item["idx"]:
                    raise TlError(f"cycle {t}: BAR0 write {item['idx']} before an earlier posted "
                                  "request left")
                if self.cq_reads and self.cq_reads[0]["p_before"] <= item["idx"]:
                    COV_ORDER.sample(event="write_passes_read")
                self.cq_pkt = {"f": f, "item": item, "k": 0, "t0": t}
            pkt = self.cq_pkt
            if pkt is None:
                raise TlError(f"cycle {t}: cq write beat outside a packet")
            k = pkt["k"]
            want = pkt["item"]["h"]["data"][k]
            if f["cq_data_o"] != want:
                raise TlError(f"cycle {t}: cq write beat {k} expected {want:#010x} got "
                              f"{f['cq_data_o']:#010x}")
            last = k == pkt["item"]["h"]["length"] - 1
            if bool(f["cq_eop_o"]) != last:
                raise TlError(f"cycle {t}: cq eop {f['cq_eop_o']} at beat {k}")
            pkt["k"] += 1
            if last:
                self.cq_writes.popleft()
                self.p_rel_due[t + 1] = pkt["item"]
                self.cq_spans.append((pkt["t0"], t, pkt["item"]["h"]["length"]))
                self.cq_pkt = None
            if self._val("probe_rd_take_o"):
                raise TlError(f"cycle {t}: a read taken on a write beat")
            return False
        # A read: one beat.
        if not (f["cq_sop_o"] and f["cq_eop_o"]) or not self.cq_reads or self.cq_pkt is not None:
            raise TlError(f"cycle {t}: unexpected cq read {f}")
        item = self.cq_reads.popleft()
        h = item["h"]
        want = (h["addr"] & mask, h["length"], h["fbe"], h["lbe"])
        got = (f["cq_addr_o"], f["cq_len_o"], f["cq_first_be_o"], f["cq_last_be_o"])
        if got != want:
            raise TlError(f"cycle {t}: cq read (addr, len, fbe, lbe) expected {want} got {got}")
        if self.p_rel < item["p_before"]:
            raise TlError(f"cycle {t}: BAR0 read passed an earlier posted request")
        if f["cq_handle_o"] in self.handles or f["cq_handle_o"] != self.cq_rd_lowest:
            raise TlError(f"cycle {t}: cq handle {f['cq_handle_o']}, lowest free when offered "
                          f"{self.cq_rd_lowest}, in use {sorted(self.handles)}")
        self.handles.add(f["cq_handle_o"])
        if not self._val("probe_rd_take_o"):
            raise TlError(f"cycle {t}: cq read taken without rd_take")
        ctx = unpack(self._val("probe_rd_ctx_o"), HD_CTX)
        want = {"handle": f["cq_handle_o"], "rid": h["rid"], "tag": h["tag"], "tc": h["tc"],
                "attr": h["attr"], "addr": (h["addr"] >> 2) & 0x3FF, "dw_len": h["length"],
                "fbe": h["fbe"], "lbe": h["lbe"]}
        if ctx != want:
            raise TlError(f"cycle {t}: read context expected {want} got {ctx}")
        if len(self.handles) == 8 and self.cq_reads:
            COV_ORDER.sample(event="read_waits_handle")
        self.cc_out.append({"hd": f["cq_handle_o"], "h": h,
                            "at": t + 1 + self.rng.randint(0, self.cc_lat)})
        return True

    # cfg stand-in: register access and completion records.
    def _check_reg(self, t: int) -> bool:
        leave = False
        rec_valid = self._val("probe_rec_valid_o")
        rec = unpack(self._val("probe_rec_o"), CPL_REC) if rec_valid else None
        if rec_valid:
            self.records.append((t, rec["kind"]))
        rec_used = False
        # An access finishing now.
        if self.reg_cpl_now is not None:
            a = self.reg_cpl_now
            self.access = None
            job = a["job"]
            if not job.get("posted"):
                h = job["h"]
                want = {"kind": job["job"], "rid": h["rid"], "tag": h["tag"], "tc": h["tc"],
                        "attr": h["attr"], "mem": 0, "locked": 0,
                        "addr": 0 if job["job"] != CR_BAR2_RD else h["addr"] & 0xFFF,
                        "dw_len": h["length"], "fbe": h["fbe"], "lbe": h["lbe"],
                        "dw_idx": a["i"], "dw_last": int(a["last"]),
                        "data": a["rdata"]}
                if not rec_valid or rec != want:
                    raise TlError(f"cycle {t}: completion record expected {want} got {rec}")
                rec_used = True
                self._cfg_completion(job, a)
            if a["last"]:
                self.job = None
        # An offered access.
        if self._val("reg_req_valid_o"):
            if self.access is not None:
                raise TlError(f"cycle {t}: reg_req offered with an access outstanding")
            f = {n: self._val(n) for n in ("reg_req_bar2_o", "reg_req_write_o", "reg_req_addr_o",
                                           "reg_req_be_o", "reg_req_data_o", "reg_req_bus_o",
                                           "reg_req_dev_o")}
            if not f["reg_req_write_o"]:
                f.pop("reg_req_data_o")
            if self.reg_prev is not None and self.reg_prev != f:
                raise TlError(f"cycle {t}: reg_req fields changed while offered")
            if self._val("reg_req_ready_i"):
                self.reg_prev = None
                leave = self._take_access(t, f)
            else:
                self.reg_prev = f
        elif self.reg_prev is not None:
            raise TlError(f"cycle {t}: reg_req_valid_o dropped before cfg took it")
        # An Unsupported Request handed on.
        if rec_valid and not rec_used:
            if rec["kind"] != CR_UR or not self.cfg_jobs or self.cfg_jobs[0]["job"] != CR_UR:
                raise TlError(f"cycle {t}: unexpected completion record {rec}")
            job = self.cfg_jobs.popleft()
            self.np_leaves.append(job["d"])
            self._order(t, job, "Unsupported Request")
            h = job["h"]
            mem = int(h["type"] in (ref.TYPE_MEM, ref.TYPE_MEM_LOCKED))
            want = {"kind": CR_UR, "rid": h["rid"], "tag": h["tag"], "tc": h["tc"],
                    "attr": h["attr"], "mem": mem, "locked": int(h["type"] == ref.TYPE_MEM_LOCKED),
                    "addr": h["addr"] & 0x7C if mem else 0,
                    "dw_len": h["length"], "fbe": h["fbe"], "lbe": h["lbe"], "dw_idx": 0,
                    "dw_last": 0, "data": 0}
            if rec != want:
                raise TlError(f"cycle {t}: UR record expected {want} got {rec}")
            leave = True
            self._expect_cpls([txr.ur_completion(0, h, bool(mem), h["type"] == ref.TYPE_MEM_LOCKED)],
                              True)
        return leave

    def _order(self, t: int, job: dict, what: str) -> None:
        if self.p_rel < job["p_before"]:
            raise TlError(f"cycle {t}: {what} passed an earlier posted request")
        if job["p_before"] < self.p_rx and self.p_rel < self.p_rx:
            COV_ORDER.sample(event="np_waits_posted")
        if self.cq_reads and self.cq_reads[0]["p_before"] <= job["p_before"]:
            COV_ORDER.sample(event="cfg_passes_cq_read")

    def _take_access(self, t: int, f: dict) -> bool:
        acc = (bool(f["reg_req_bar2_o"]), bool(f["reg_req_write_o"]), f["reg_req_addr_o"],
               f["reg_req_be_o"], f.get("reg_req_data_o"))
        if self.job is None:
            # A new job: the cfg-line head or the oldest BAR2 write.
            cands = []
            if self.cfg_jobs and self.cfg_jobs[0]["job"] != CR_UR:
                cands.append(self.cfg_jobs[0])
            if self.bar2_writes:
                cands.append(self.bar2_writes[0])
            for c in cands:
                if c["acc"] and c["acc"][0][:5] == acc:
                    self.job = c
                    break
            if self.job is None:
                raise TlError(f"cycle {t}: access {acc} starts no expected request; heads "
                              f"{[c['acc'][0] if c['acc'] else None for c in cands]}")
            if self.job in self.bar2_writes:
                self.job["posted"] = True
                if self.p_rel < self.job["idx"]:
                    raise TlError(f"cycle {t}: BAR2 write before an earlier posted request left")
            else:
                self._order(t, self.job, "cfg-line request")
            self.job["n"] = len(self.job["acc"])
            self.job["i"] = 0
        job = self.job
        want = job["acc"][0]
        if want[:5] != acc:
            raise TlError(f"cycle {t}: access expected {want} got {acc}")
        if not acc[0] and acc[1]:
            bus_dev = (f["reg_req_bus_o"], f["reg_req_dev_o"])
            if bus_dev != want[5:7]:
                raise TlError(f"cycle {t}: configuration write bus/dev expected {want[5:7]} got "
                              f"{bus_dev}")
        job["acc"].popleft()
        last = not job["acc"]
        if not acc[0] and acc[1]:
            # cfg captures the Bus and Device Number of every configuration
            # write it takes (TL_CFG register access).
            self.bus, self.dev = f["reg_req_bus_o"], f["reg_req_dev_o"]
        key = (int(acc[0]), acc[2])
        if acc[1]:
            mask = sum(0xFF << (8 * b) for b in range(4) if acc[3] >> b & 1)
            self.regmap[key] = (self.regmap.get(key, 0) & ~mask) | (acc[4] & mask)
            rdata = self.rng.getrandbits(32)
        else:
            rdata = self.regmap.setdefault(key, self.rng.getrandbits(32))
        self.access = {"job": job, "i": job["i"], "last": last, "rdata": rdata,
                       "done_at": t + self.rng.randint(1, self.reg_lat)}
        job["i"] += 1
        if last:
            if job.get("posted"):
                self.bar2_writes.popleft()
                self.p_rel_due[t + 1] = job
            else:
                self.cfg_jobs.popleft()
                self.np_leaves.append(job["d"])
                return True
        return False

    # ------------------------------------------------------- transmit
    def _expect_cpls(self, tlps: list, done: bool, req: dict | None = None) -> dict:
        """Completions formed now: each waits for the posted requests taken
        before this cycle."""
        if req is None:
            req = {"cpls": deque(), "done": False}
            self.cpl_reqs.append(req)
        for x in tlps:
            req["cpls"].append({"tlp": x, "p_before": self.p_taken_prev})
        req["done"] = done
        return req

    def _cfg_completion(self, job: dict, a: dict) -> None:
        """A configuration access or BAR2 read DW finished: the completions
        it completes (COMPLETIONS.md, configuration and BAR2 completions)."""
        h = job["h"]
        if job["job"] == CR_CFG_WR:
            self._expect_cpls([txr.cfg_completion(0, h, None)], True)
        elif job["job"] == CR_CFG_RD:
            self._expect_cpls([txr.cfg_completion(0, h, a["rdata"])], True)
        else:
            if "parts" not in job:
                job["parts"] = deque(txr.parts(h["addr"], h["length"], h["fbe"], h["lbe"]))
                job["dws"] = []
                job["creq"] = None
            job["dws"].append(a["rdata"])
            i0, n, bc, la = job["parts"][0]
            if len(job["dws"]) == i0 + n:
                job["parts"].popleft()
                d = txr.masked(job["dws"], h["length"], h["fbe"], h["lbe"])[i0:i0 + n]
                x = txr.completion(0, h["rid"], h["tag"], h["tc"], h["attr"], txr.SC, bc, la, d)
                job["creq"] = self._expect_cpls([x], not job["parts"], job["creq"])

    def _posted_taken(self, t: int, tlp: bytes, kind: str) -> None:
        self.tx_post.append({"tlp": tlp, "t": t, "kind": kind})
        self.p_taken += 1

    def _check_rq(self, t: int) -> None:
        """rq takes a request only on a sop beat with Bus Master Enable 1 and
        D0 (APP); each taken request is formed now (TLP.md)."""
        if self.rq_drv is None:
            if self._val("probe_rq_take_o"):
                raise TlError(f"cycle {t}: an rq read taken with rq idle")
            return
        c, req = self.rq_cur, self.rq_cur["req"]
        ready = self._val("rq_ready_o")
        sop = self.rq_drv[1]
        if sop and (not self.bm_en or self.regs.d_state != 0):
            if ready:
                raise TlError(f"cycle {t}: rq took a sop beat with bus mastering off or outside D0")
            COV_TX.sample(event="rq_blocked")
        if not ready:
            c["offered"] = True
            if self._val("probe_rq_take_o"):
                raise TlError(f"cycle {t}: an rq read taken with rq_ready_o 0")
            return
        c["offered"] = False
        took_read = sop and not req["write"]
        if bool(self._val("probe_rq_take_o")) != took_read:
            raise TlError(f"cycle {t}: rq_rd_take {self._val('probe_rq_take_o')}, a read taken "
                          f"{int(took_read)}")
        if sop:
            if req["write"]:
                self._posted_taken(t, txr.mem_write(0, req["addr"], req["data"], req["fbe"],
                                                    req["lbe"]),
                                   "mwr64" if req["addr"] >> 32 else "mwr32")
            else:
                ctx = unpack(self._val("probe_rq_ctx_o"), RQ_CTX)
                want = {"tag": req["tag"], "addr": (req["addr"] >> 2) & 0x3FF,
                        "dw_len": req["len"], "fbe": req["fbe"], "lbe": req["lbe"]}
                if ctx != want:
                    raise TlError(f"cycle {t}: rq read context expected {want} got {ctx}")
                self.tx_np.append({"tlp": txr.mem_read(0, req["addr"], req["len"], req["fbe"],
                                                       req["lbe"], req["tag"]),
                                   "p_before": self.p_taken_prev, "tag": req["tag"], "t": t})
        c["k"] += 1
        if c["k"] == (req["len"] if req["write"] else 1):
            self.rq_cur = None

    def _check_msg(self, t: int) -> None:
        m = self.msg_drv
        if m is None:
            return
        if not self._val("msg_ready_o"):
            COV_TX.sample(event="msg_wait")
            return
        self.msg_cur = None
        if m["kind"] == 0:
            self._posted_taken(t, txr.interrupt_write(0, m["addr"], m["data"]),
                               "irq64" if m["addr"] >> 32 else "irq32")
        else:
            self._posted_taken(t, txr.error_message(0, m["kind"]),
                               ("err_cor", "err_nonfatal", "err_fatal")[m["kind"] - 1])

    def _check_cc(self, t: int) -> None:
        """cc: the application's packet for a handle; its completions are
        formed from the read's context and the packet's data."""
        if self.cc_drv is None:
            return
        c = self.cc_cur
        if not self._val("cc_ready_o"):
            c["offered"] = True
            COV_TX.sample(event="cc_wait")
            return
        c["offered"] = False
        if c["k"] == 0:
            # Its completions wait for the posted requests taken before its
            # sop beat (APP ordering).
            h = c["r"]["h"]
            tlps = txr.read_completions(0, h, c["data"])
            self._expect_cpls(tlps, True)
            self.splits.append(len(tlps))
            COV_TX.sample(split=len(tlps))
        c["k"] += 1
        if c["k"] == len(c["data"]):
            self.handles.discard(c["r"]["hd"])
            self.cc_cur = None

    @staticmethod
    def _cpl_kind(x: bytes) -> str:
        if x[0] & 1:
            return "cpl_locked"
        if x[6] >> 5:
            return "cpl_ur"
        return "cpl_data" if x[0] >> 6 & 1 else "cpl_nodata"

    def _check_tx(self, t: int) -> None:
        """The dll stand-in: credits at every offered sop, beats held until
        taken, each TLP against the transmit reference and the order."""
        if self._val("probe_rq_sent_o") and not (self._val("tlp_tx_valid_o")
                                                  and self._val("tlp_tx_sop_o")
                                                  and self._val("tlp_tx_ready_i")):
            raise TlError(f"cycle {t}: rq_sent with no sop beat taken")
        for cls, waiting in (("P", self.tx_post), ("NP", self.tx_np),
                             ("CPL", [r for r in self.cpl_reqs if r["cpls"]])):
            av = self.cr_avail[cls]
            if waiting and av is not None and av[0] == 0:
                COV_TX.sample(event="credit_wait_" + cls.lower())
        if not self._val("tlp_tx_valid_o"):
            if self.tx_prev is not None:
                raise TlError(f"cycle {t}: tlp_tx_valid_o dropped before its beat was taken")
            if self.tx_cur is not None:
                # A TLP goes out with no gap: tl starts it only once all its
                # data is queued (MAS, transmit arbiter).
                raise TlError(f"cycle {t}: tlp_tx_valid_o 0 inside a TLP")
            return
        f = (self._val("tlp_tx_data_o"), self._val("tlp_tx_sop_o"), self._val("tlp_tx_eop_o"))
        if self.tx_prev is not None and self.tx_prev != f:
            raise TlError(f"cycle {t}: tlp_tx beat changed before it was taken: "
                          f"{self.tx_prev} to {f}")
        data, sop, eop = f
        if sop:
            if self.tx_cur is not None:
                raise TlError(f"cycle {t}: tlp_tx sop inside a TLP")
            first = data.to_bytes(4, "little")
            cls, dcr = txr.credits(first + bytes(4))
            av = self.cr_avail[cls]
            if av is not None and (av[0] < 1 or av[1] < dcr):
                raise TlError(f"cycle {t}: a {cls} TLP offered needing (1, {dcr}) credits with "
                              f"{av} available")
            if self.tx_prev is None:
                # The ID bytes are bus_num_i and dev_num_i as the sop is
                # first offered.
                self.tx_id = self.id_driven
        elif self.tx_cur is None:
            raise TlError(f"cycle {t}: tlp_tx beat outside a TLP")
        take = bool(self._val("tlp_tx_ready_i"))
        self.tx_prev = None if take else f
        if not take:
            COV_TX.sample(event="tx_stall")
            return
        if sop:
            first = data.to_bytes(4, "little")
            cls, dcr = txr.credits(first + bytes(4))
            if self.cr_avail[cls] is not None:
                self.cr_avail[cls][0] -= 1
                self.cr_avail[cls][1] -= dcr
                self.cr_back.append((t + 1 + self.rng.randint(0, self.cr_lat), cls, dcr))
            self.tx_cur = {"beats": [], "t0": t, "p_sent": self.p_sent, "cls": cls,
                           "id": self.tx_id}
            rq_sent = bool(self._val("probe_rq_sent_o"))
            if rq_sent != (cls == "NP"):
                raise TlError(f"cycle {t}: rq_sent {int(rq_sent)} at the sop of a {cls} TLP")
            if rq_sent and self.tx_np and self._val("probe_rq_sent_tag_o") != self.tx_np[0]["tag"]:
                raise TlError(f"cycle {t}: rq_sent tag {self._val('probe_rq_sent_tag_o')}, "
                              f"expected {self.tx_np[0]['tag']}")
        self.tx_cur["beats"].append(data)
        if eop:
            cur, self.tx_cur = self.tx_cur, None
            self._tx_tlp(t, ref.from_beats(cur["beats"]), cur)

    def _tx_tlp(self, t: int, x: bytes, cur: dict) -> None:
        my = cur["id"]
        cls = cur["cls"]
        if cls == "P":
            if not self.tx_post:
                raise TlError(f"cycle {t}: posted TLP {x.hex()} sent, none expected")
            t0 = self.tx_post[0]["t"]
            group = [e for e in self.tx_post if e["t"] == t0]
            hit = next((e for e in group if txr.with_id(e["tlp"], my) == x), None)
            if hit is None:
                raise TlError(f"cycle {t}: posted TLP {x.hex()} sent; the next posted taken "
                              f"(cycle {t0}) are {[txr.with_id(e['tlp'], my).hex() for e in group]}")
            self.tx_post.remove(hit)
            self.p_sent += 1
            if self.tx_np and self.tx_np[0]["t"] < hit["t"]:
                COV_TX.sample(event="posted_passes_np")
            kind = hit["kind"]
        elif cls == "NP":
            if not self.tx_np or txr.with_id(self.tx_np[0]["tlp"], my) != x:
                want = txr.with_id(self.tx_np[0]["tlp"], my).hex() if self.tx_np else "none"
                raise TlError(f"cycle {t}: rq read {x.hex()} sent, expected {want}")
            e = self.tx_np.popleft()
            if cur["p_sent"] < e["p_before"]:
                raise TlError(f"cycle {t}: rq read passed a posted request taken before it")
            kind = "mrd64" if x[0] >> 5 & 1 else "mrd32"
        else:
            hit = None
            for req in self.cpl_reqs:
                if req["cpls"] and txr.with_id(req["cpls"][0]["tlp"], my) == x:
                    hit = req
                    break
            if hit is None:
                heads = [txr.with_id(r["cpls"][0]["tlp"], my).hex() for r in self.cpl_reqs
                         if r["cpls"]]
                raise TlError(f"cycle {t}: completion {x.hex()} sent; expected one of {heads}")
            e = hit["cpls"].popleft()
            if cur["p_sent"] < e["p_before"]:
                raise TlError(f"cycle {t}: completion passed a posted request taken before it")
            if e["p_before"] > cur["p_sent"] - 1 >= 0:
                COV_TX.sample(event="completion_after_posted")
            if self.last_cpl_req is not None and self.last_cpl_req is not hit \
                    and self.last_cpl_req["cpls"]:
                COV_TX.sample(event="cpl_interleave")
            self.last_cpl_req = hit
            if hit["done"] and not hit["cpls"]:
                self.cpl_reqs.remove(hit)
            kind = self._cpl_kind(x)
        self.tx_kinds.append(kind)
        COV_TX.sample(kind=kind)


async def started(dut, seed: int, **kw) -> TlEnv:
    env = TlEnv(dut, seed, **kw)
    await env.start()
    env.log.info("tl env: seed %d, credits %s, BAR0_ADDR_W %d", seed, env.fc, env.bar0_w)
    return env
