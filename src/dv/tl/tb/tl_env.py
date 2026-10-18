"""Environment of the pcie_tl testbench (wiki/src/dv/tl/DV_PLAN.md,
Strategy). The DUT is driven only through its ports (tb/pcie_tl_tb.sv);
the slot nets of the blocks not built yet are read through the bind tap.

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
  PME_TO_Ack requests, the error pulses and the slot power events
  (exactly one cycle after the eop, the event with its value and scale),
  the completion beats to the tracker slot, the posted releases in arrival
  order with their data credits, and the non-posted release totals.
  Ordering is checked at every leave: nothing that must wait for earlier
  posted requests goes before their releases were seen.
- The **shadow**, the architecture model's ``classify`` and
  ``slot_power``, judges the same TLP; any disagreement with the reference
  fails the test.
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

# Packed structs of pcie_tl_pkg, most significant field first.
CPL_REC = (("kind", 2), ("rid", 16), ("tag", 8), ("tc", 3), ("attr", 2), ("mem", 1),
           ("locked", 1), ("addr", 12), ("dw_len", 11), ("fbe", 4), ("lbe", 4), ("dw_idx", 11),
           ("dw_last", 1), ("data", 32))
HD_CTX = (("handle", 3), ("rid", 16), ("tag", 8), ("tc", 3), ("attr", 2), ("addr", 10),
          ("dw_len", 11), ("fbe", 4), ("lbe", 4))
RX_BEAT = (("valid", 1), ("data", 32), ("sop", 1), ("eop", 1))
CR_UR, CR_CFG_WR, CR_CFG_RD, CR_BAR2_RD = range(4)

# Outputs with a reset value (HAS, reset), checked in every reset cycle.
RESET_ZERO = ("tlp_tx_valid_o", "tlp_rx_ready_o", "fc_rel_p_o", "fc_rel_np_o", "reg_req_valid_o",
              "tl_err_o", "tl_np_pending_o", "slot_pwr_valid_o", "msg_ready_o", "cq_valid_o",
              "cc_ready_o", "rq_ready_o", "rc_valid_o")
# Outputs of the blocks not built yet (#291, #292): 0 in every cycle.
UNBUILT_ZERO = ("tlp_tx_valid_o", "tl_np_pending_o", "msg_ready_o", "cc_ready_o", "rq_ready_o",
                "rc_valid_o")

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
COV_SPL = Covergroup("tl_rx_slot_power")
COV_SPL.coverpoint("case", ("captured", "no_data", "poisoned", "malformed"))
COV_SPL.coverpoint("scale", (0, 1, 2, 3))
COV_ORDER = Covergroup("tl_rx_order")
COV_ORDER.coverpoint("event", ("read_waits_handle", "write_passes_read", "np_waits_posted",
                               "cfg_passes_cq_read", "two_np_leave", "three_np_leave",
                               "np_data_carry", "cq_stall", "reg_stall",
                               "posted_queue_full", "np_queue_full"))


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
                 reg_ready_p: float = 1.0, reg_lat: int = 2, gap_p: float = 0.0):
        self.dut = dut
        self.log = dut._log
        self.rng = random.Random(seed)
        self.seed = seed
        self.fc: dict[str, int] = {}
        self.bar0_w = 12
        self.regs = regs or ref.Regs()
        self.cq_ready_p, self.reg_ready_p, self.reg_lat, self.gap_p = (cq_ready_p, reg_ready_p,
                                                                       reg_lat, gap_p)
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
        self.spl_seen = 0                     # slot power events checked

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
        self.spl_at: dict[int, tuple] = {}    # cycle -> (value, scale) of a slot power event
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
                    or self.spl_at)

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
        for name, v in (("fc_ph_avail_i", 0xFF), ("fc_pd_avail_i", 0xFFF), ("fc_nph_avail_i", 0xFF),
                        ("fc_npd_avail_i", 0xFFF), ("fc_cplh_avail_i", 0xFF),
                        ("fc_cpld_avail_i", 0xFFF)):
            getattr(self.dut, name).value = v
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
                      f"rel {self.np_rel}, held {self.held}")

    def _drained(self, stuck: int) -> bool:
        if (self.txq or self.beats or self.cq_writes or self.cfg_jobs or self.bar2_writes
                or self.posted or self.access or self.pme or self.cpl_beats or self.err_at
                or self.spl_at or self.cq_pkt):
            return False
        if len(self.cq_reads) != stuck:
            return False
        return self.np_rel == [self.np_rx[0] - stuck, self.np_rx[1]]

    async def link_down(self, cycles: int = 4) -> None:
        """dl_up_i low: tl drops everything (HAS, reset); so does the host."""
        self.dl_up = 0
        await self.run(cycles)
        self._clear()
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
        if v.slot_power is not None:
            self.spl_at[err_cycle] = v.slot_power
        if v.kind == "msg" and h["code"] == ref.SET_SLOT_POWER_LIMIT:
            case = ("captured" if v.slot_power else "malformed" if v.outcome == "malformed"
                    else "poisoned" if v.err is not None else "no_data")
            COV_SPL.sample(case=case)
            if v.slot_power:
                COV_SPL.sample(scale=v.slot_power[1])
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
        h = arch.parse(tlp)
        kind, bit = arch.classify(h, c, self.regs.bar0_addr_w)
        names = {"pme_ack": "pme", "malformed": "malformed"}
        got = names.get(kind, kind)
        if kind == "drop" and bit == arch.E_UR_P:
            got = "ur"
        if (got, bit) != (v.outcome, v.err):
            raise TlError(f"shadow disagrees on {tlp.hex()}: reference {v.outcome}/{v.err}, "
                          f"architecture model {kind}/{bit}")
        spl = arch.slot_power(h, kind, bit)
        if spl != v.slot_power:
            raise TlError(f"shadow disagrees on {tlp.hex()}: reference slot power "
                          f"{v.slot_power}, architecture model {spl}")

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
        self._check_err(t)
        self._check_slot_power(t)
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
        if self._val("probe_pme_ack_o"):
            if not self.pme:
                raise TlError(f"cycle {t}: PME_TO_Ack requested with no PME_Turn_Off held")
            item = self.pme.popleft()
            self.p_rel_due[t + 1] = item
            if self.p_rel < item["idx"]:
                raise TlError(f"cycle {t}: PME_Turn_Off left before an earlier posted request")

    def _check_err(self, t: int) -> None:
        got = self._val("tl_err_o")
        want = self.err_at.pop(t, 0)
        if got != want:
            raise TlError(f"cycle {t}: tl_err_o expected {want:#05x} got {got:#05x}")

    def _check_slot_power(self, t: int) -> None:
        """The slot power event (TL_CFG slot power limit): a pulse exactly
        when the reference raised one for the TLP whose eop was the cycle
        before, with its value and scale."""
        want = self.spl_at.pop(t, None)
        if not self._val("slot_pwr_valid_o"):
            if want is not None:
                raise TlError(f"cycle {t}: no slot power event, expected value {want[0]:#04x} "
                              f"scale {want[1]}")
            return
        got = (self._val("slot_pwr_value_o"), self._val("slot_pwr_scale_o"))
        if want is None:
            raise TlError(f"cycle {t}: slot power event {got} with none due")
        if got != want:
            raise TlError(f"cycle {t}: slot power event (value, scale) expected {want} got {got}")
        self.spl_seen += 1

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
        take = bool(self._val("cq_ready_i"))
        self.cq_prev = None if take else f
        if not take:
            if f["cq_sop_o"] and not f["cq_write_o"]:
                pass
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
        free = [i for i in range(8) if i not in self.handles]
        if not free or f["cq_handle_o"] != free[0]:
            raise TlError(f"cycle {t}: cq handle {f['cq_handle_o']}, lowest free {free}")
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


async def started(dut, seed: int, **kw) -> TlEnv:
    env = TlEnv(dut, seed, **kw)
    await env.start()
    env.log.info("tl env: seed %d, credits %s, BAR0_ADDR_W %d", seed, env.fc, env.bar0_w)
    return env
