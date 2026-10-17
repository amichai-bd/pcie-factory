"""Environment of the pcie_dll testbench (wiki/src/dv/dll/DV_PLAN.md,
Strategy). The DUT is driven only through its ports (tb/pcie_dll_tb.sv).

One cycle, at the falling edge of clk_i after rising edge n:

1. Read the DUT's outputs (all registered).
2. The **phy stand-in** decides ``pkt_tx_ready_i``: 1 from an accepted sop
   to its eop (phy HAS, packet stream), link up or down, 0 in the framing
   cycle after each eop, otherwise 1 with probability ``ready_p``. Like the
   MAC (``pcie_mac_frame.sv``), it keeps a packet open from its sop to its
   eop, also across a link-down, and flags a bubble or a sop inside it. An accepted beat goes
   into the line towards the far end and into the DUT packet checker.
3. The far end is either the **independent partner**
   (``models/pcie_dll_partner.py``) behind a fixed-delay ``Line`` each way,
   whose ``tx_ready`` drops with probability ``gap_p`` (gaps inside and
   between received packets), or **hand-driven** beats a test queues with
   ``inject``. The beat that reaches the DUT is driven on ``pkt_rx_*``.
4. The **shadow**, a ``pcie_dll_arch.Dll`` with the DUT's parameters, gets
   the same link state and the same received packets and steps once; its
   state sequence, DL_Active time and recorded credits are compared with
   the DUT's (``check_shadow``). It sends into nothing and is never
   steered.
5. Every DLLP the DUT sends is checked as it ends (``_tx_packet``): 6
   bytes, its CRC-16 equal to both the architecture model's and the
   partner's, its type allowed in the DUT's state, its values the DUT's
   parameters (InitFC) or its CREDITS_ALLOCATED (UpdateFC), and its bytes
   equal to what both models encode. Each UpdateFC must be due (entry,
   credits returned, or the refresh) and be the first due class in round
   robin.

The **slot drivers** (``slot_tx``, ``slot_rx``; DV plan, slot drivers)
stand in for the TLP paths until they attach (#212, #213): an upper-layer
TLP source whose sop the forced ready takes only when the credit gate
covers it, optionally forwarding the accepted TLPs, framed, to the far end
(``fwd``); and receive decisions plus an upper receive stream that returns
credits. Each cycle the gate and the overflow check are compared, after
the combinational settle, with the flow-control rules computed here and by
the architecture model's counters.
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

from regress.funcov import Covergroup

_DV = Path(__file__).resolve().parents[1]
sys.path.append(str(_DV / "models"))
sys.path.append(str(_DV.parents[1] / "arch" / "dll"))
import pcie_dll_arch as arch  # noqa: E402
import pcie_dll_crc as arch_crc  # noqa: E402
import pcie_dll_partner as ptn  # noqa: E402
import pcie_dll_partner_crc as ptn_crc  # noqa: E402

# dl_state_o codes (HAS, Status) and the partner's names for them.
INACTIVE, INIT1, INIT2, ACTIVE = 0, 1, 2, 3
STATE_NAMES = ("DL_Inactive", "FC_INIT1", "FC_INIT2", "DL_Active")
PARTNER_STATE = {ptn.INACTIVE: INACTIVE, ptn.FC_INIT1: INIT1, ptn.FC_INIT2: INIT2,
                 ptn.ACTIVE: ACTIVE}
CLASSES = ("P", "NP", "Cpl")        # the partner's names; arch index 0, 1, 2
ERR_BAD_DLLP = 1 << 1               # err_o bit 1 (HAS, errors)
ERR_OVERFLOW = 1 << 5               # err_o bit 5, Receiver overflow
MODS = (256, 4096)                  # header and data credit counters
RESERVE = (8, 24)                   # receive space of an infinite field
REFRESH = 2048                      # UpdateFC refresh, cycles
L0_CODE = 0x20                      # an L0 code of ltssm_state_i (mac LTSSM encoding)

# DLLP types the DUT may send in each state (DLLP.md, types used): the
# InitFC of the state's group in DL_Init, UpdateFC in DL_Active. Ack and
# Nak (#213) are not sent yet.
TX_KINDS = {INIT1: "init1", INIT2: "init2", ACTIVE: "update"}
ARCH_KINDS = {"init1": arch.INITFC1, "init2": arch.INITFC2, "update": arch.UPDATEFC}

# Functional coverage (DV plan, coverage).
COV_STATES = Covergroup("dll_dl_states")
COV_STATES.coverpoint("arc", bins={
    "inactive__init1": (INACTIVE, INIT1), "init1__init2": (INIT1, INIT2),
    "init2__active": (INIT2, ACTIVE), "init1__inactive": (INIT1, INACTIVE),
    "init2__inactive": (INIT2, INACTIVE), "active__inactive": (ACTIVE, INACTIVE)})

COV_TX = Covergroup("dll_dllp_tx")
COV_TX.coverpoint("kind", bins=["init1", "init2", "update"])
COV_TX.coverpoint("cls", bins=list(CLASSES))
COV_TX.cross("kind_x_cls", "kind", "cls")
COV_TX.coverpoint("resend", bins=["first", "resend"])
COV_TX.coverpoint("stall", bins=["sop_waited", "sop_at_once"])

COV_RX = Covergroup("dll_dllp_rx")
COV_RX.coverpoint("outcome", bins=["good", "bad_crc", "bad_length", "flagged",
                                   "inactive_discard"])
COV_RX.coverpoint("kind", bins=["ack", "nak", "init1", "init2", "update", "other_type",
                                "other_vc"])
COV_RX.coverpoint("gap", bins=["no_gap", "gap_inside"])

COV_INIT = Covergroup("dll_fc_init")
COV_INIT.coverpoint("init2_end", bins=["initfc2", "updatefc"])
COV_INIT.coverpoint("adv_hdr", bins=["finite", "infinite"])
COV_INIT.coverpoint("adv_data", bins=["finite", "infinite"])
COV_INIT.coverpoint("partner_hdr", bins=["finite", "infinite"])
COV_INIT.coverpoint("partner_data", bins=["finite", "infinite"])
COV_INIT.coverpoint("first_up", bins=["dut", "partner", "same"])

# A DLLP finished after the link went down (MAS, Transmit): the beats the
# phy took at or after the edge that sampled link_up_i 0.
COV_TAIL = Covergroup("dll_tx_tail")
COV_TAIL.coverpoint("beats_after_link_down", bins={"one": 1, "two": 2, "three": 3})

COV_UPDFC = Covergroup("dll_updatefc_tx")
COV_UPDFC.coverpoint("cause", bins=["entry", "refresh", "returned"])
COV_UPDFC.coverpoint("cls", bins=list(CLASSES))
COV_UPDFC.cross("cause_x_cls", "cause", "cls")
# The class sent, counted from the round-robin pointer: 0 the pointer's
# class, 1 and 2 the next ones (reached only when the pointer's class is
# not due while a later one is).
COV_UPDFC.coverpoint("rr_skip", bins={"pointer": 0, "next": 1, "next_but_one": 2})

# Transmit credit gate (FLOW_CONTROL.md, transmit accounting).
COV_FC_TX = Covergroup("dll_fc_tx")
COV_FC_TX.coverpoint("gate", bins=["open", "closed_hdr", "closed_data", "closed_both",
                                   "infinite"])
COV_FC_TX.coverpoint("cls", bins=list(CLASSES))
COV_FC_TX.cross("gate_x_cls", "gate", "cls")
COV_FC_TX.coverpoint("consumed_wrap", bins=["hdr", "data"])
COV_FC_TX.coverpoint("starve", bins=["refused", "recovered"])

# Receive accounting (FLOW_CONTROL.md, receive accounting and buffer).
COV_FC_RX = Covergroup("dll_fc_rx")
COV_FC_RX.coverpoint("decision", bins=["stored", "overflow", "malformed",
                                       "malformed_no_hdr"])
COV_FC_RX.coverpoint("cls", bins=list(CLASSES))
COV_FC_RX.cross("decision_x_cls", "decision", "cls")
COV_FC_RX.coverpoint("field", bins=["finite", "infinite"])
COV_FC_RX.coverpoint("returned", bins=["P", "NP", "Cpl"])


def fc_field(v: int) -> str:
    return "infinite" if v == 0 else "finite"


def dllp_bytes(typ: int, hdr: int, data: int) -> bytes:
    """An FC DLLP with its CRC, as the architecture model encodes it."""
    kinds = next(k for k in (arch.INITFC1, arch.INITFC2, arch.UPDATEFC) if typ & 0xF8 in k)
    return arch.fc_dllp(kinds, kinds.index(typ & 0xF8), hdr, data, typ & 0x07)


def tlp_dws(tlp: bytes) -> list[int]:
    """A TLP as upper-stream beats: DW i holds bytes 4i (bits 7:0) to 4i+3."""
    return [int.from_bytes(tlp[i:i + 4], "little") for i in range(0, len(tlp), 4)]


def tlp_need(head: bytes) -> tuple[int, tuple[int, int]]:
    """(class index, (header, data) credits) by the partner's reading of
    the flow-control page, from at least the first 4 TLP bytes."""
    return CLASSES.index(ptn.tlp_class(head)), ptn.tlp_credits(head)


def gate_rule(limit: list[int], inf: list[bool], used: list[int],
              need: tuple[int, int]) -> list[bool]:
    """Per field, the transmit gate (FLOW_CONTROL.md, transmit accounting)."""
    return [inf[f] or (limit[f] - (used[f] + need[f])) % MODS[f] <= MODS[f] // 2
            for f in (0, 1)]


def random_header(rng: random.Random) -> bytes:
    """A random header DW0 with a class (Fmt 0xx) and any Length."""
    b0 = rng.choice((rng.getrandbits(7), 0x40, 0x00, 0x4A, 0x0A, 0x70, 0x30, 0x44))
    return bytes([b0 & 0x7F, rng.getrandbits(8), rng.getrandbits(8), rng.getrandbits(8)])


def raw_dllp(body: bytes) -> bytes:
    """A DLLP: body and CRC-16, with both models agreeing on the CRC."""
    a, p = arch_crc.crc16(body), ptn_crc.crc16_bytes(body)
    assert a == p, f"CRC-16 of {body.hex()}: arch {a.hex()} partner {p.hex()}"
    return body + a


class Gate:
    """The receiving phy's view of a line: with its link down it delivers
    nothing, and a packet whose sop it missed is dropped up to its end, so
    every delivered beat belongs to a packet that started with a sop."""

    def __init__(self) -> None:
        self.drop = False

    def pass_beat(self, beat: ptn.Beat | None, up: bool) -> ptn.Beat | None:
        if beat is None:
            return None
        if beat.sop:
            self.drop = not up
        elif not up:
            self.drop = True
        return None if self.drop else beat


class Env:
    """The DUT, the phy stand-in, the far end and the shadow."""

    def __init__(self, dut, seed: int, *, partner: ptn.Partner | None = None,
                 ready_p: float = 1.0, gap_p: float = 0.0, delay: int = 22,
                 slot_tx: bool = False, slot_rx: bool = False, fwd: bool = False,
                 valid_p: float = 1.0) -> None:
        self.dut = dut
        self.rng = random.Random(seed)
        self.partner = partner
        self.ready_p = ready_p
        self.gap_p = gap_p
        self.to_far = ptn.Line(delay)
        self.to_dut = ptn.Line(delay)
        self.raw: deque[ptn.Beat | None] = deque()
        self.gate_far = Gate()
        self.gate_dut = Gate()
        self.adv: dict[str, tuple[int, int]] = {}
        self.shadow: arch.Dll | None = None
        self.cycle = 0
        self.dut_link = False
        self.partner_link = False
        self.state = INACTIVE
        self.states: list[tuple[int, int]] = [(0, INACTIVE)]       # DUT (cycle, state)
        self.shadow_states: list[tuple[int, int]] = [(0, INACTIVE)]
        self.partner_states: list[tuple[int, int]] = [(0, INACTIVE)]
        self.in_pkt = False                    # DUT packet accepted, eop not yet
        self.framing = False                   # the cycle after an eop
        self.tx_beats: list[ptn.Beat] = []
        self.tx_state = INACTIVE               # DUT state at the packet's sop
        self.tx_sop_wait = 0
        self.sent: list[tuple[int, str, int, bytes]] = []   # (sop cycle, kind, class, bytes)
        self.offers: list[int] = []            # per sent DLLP: first cycle its sop was offered
        self.tx_offer: int | None = None
        self.group_starts: list[int] = []      # sop cycles of each InitFC group's P
        self.upd_last: dict[int, int] = {}     # class -> sop cycle of its last UpdateFC
        self.upd_entry: set[int] = set()       # classes still owed their entry UpdateFC
        self.rx_beats: list[ptn.Beat] = []
        self.rx_gap = False
        self.rx_good: list[tuple[int, bytes]] = []   # (eop cycle, DLLP) good at the DUT
        self.bad_expected = 0                  # bad DLLPs the DUT must report
        self.bad_seen = 0                      # Bad DLLP pulses on err_o
        self.shadow_rx: deque[tuple[int, arch.Packet]] = deque()
        self.errs: list[tuple[int, int]] = []  # (cycle, err_o) when nonzero
        self.tail_beats = 0                    # beats taken after link_up_i fell
        self.tails: list[tuple[int, int]] = [] # (eop cycle, beats after link-down)
        self.violations: list[str] = []
        # Slot drivers: the TLP source (#212 stand-in).
        self.slot_tx = slot_tx
        self.slot_rx = slot_rx
        self.fwd = fwd
        self.valid_p = valid_p
        self.tlp_q: deque[bytes] = deque()     # TLPs the upper layer offers
        self.tlp_cur: list[int] | None = None  # DWs of the TLP being taken
        self.tlp_idx = 0
        self.accepted: list[bytes] = []        # TLPs whose sop the DUT took
        self.gate_checks = 0
        self.refused = 0                       # cycles a sop waited on the gate
        self.refusing = False
        self.refuse_since = 0
        self.fwd_q: deque[list[ptn.Beat]] = deque()
        self.fwd_cur: list[ptn.Beat] = []
        self.fwd_seq = 0
        # Slot drivers: receive decisions and the upper stream (#213 stand-in).
        self.up_ready_p = 1.0                  # upper layer takes a beat
        self.up_stall = 0                      # cycles left of an upper-layer stall
        self.rx_dec_q: deque[tuple[bytes, bool, bool]] = deque()   # (TLP, malformed, no_hdr)
        self.rx_store: deque[bytes] = deque()  # stored, not yet delivered
        self.rx_cur: list[int] | None = None
        self.rx_cur_tlp = b""
        self.rx_idx = 0
        self.delivered: list[bytes] = []
        self.decided: list[tuple[int, str, int]] = []   # (cycle, decision, class)
        self.ovf_due: list[int] = []           # cycles an overflow pulse is due
        self.ovf_seen = 0
        self.fits_checks = 0
        # Receive counters by the flow-control page (FLOW_CONTROL.md).
        self.rx_alloc = [[0, 0] for _ in CLASSES]
        self.rx_rcvd = [[0, 0] for _ in CLASSES]
        # UpdateFC: CREDITS_ALLOCATED as sent, per class (cycle, value) as
        # the DUT's registers show it; last sent; round-robin pointer.
        self.upd_hist: list[list[tuple[int, tuple[int, int]]]] = [[], [], []]
        self.upd_sent: list[tuple[int, int]] = [(0, 0)] * 3
        self.upd_offer: dict[int, int] = {}    # class -> offer cycle of its last UpdateFC
        self.upd_rr = 0
        self.upd_pending: dict[int, int] = {}  # class -> cycle a return became visible
        self.upd_lat_max = 0
        self.upd_bound: int | None = None      # latency bound for a returned credit
        self.active_since = 0

    # ------------------------------------------------------------ set-up

    async def start(self) -> None:
        d = self.dut
        cocotb.start_soon(Clock(d.clk_i, 8, unit="ns").start())
        for name in ("tlp_tx_valid_i", "tlp_tx_data_i", "tlp_tx_sop_i", "tlp_tx_eop_i",
                     "pkt_tx_ready_i", "pkt_rx_valid_i", "pkt_rx_data_i", "pkt_rx_sop_i",
                     "pkt_rx_eop_i", "pkt_rx_dllp_i", "pkt_rx_err_i", "pkt_rx_nullify_i",
                     "link_up_i", "ltssm_state_i"):
            getattr(d, name).value = 0
        # The DUT's upper receive stream never stalls (HAS, receive).
        d.tlp_rx_ready_i.value = 1
        d.rst_i.value = 1
        for _ in range(3):
            await FallingEdge(d.clk_i)
        d.rst_i.value = 0
        # The DUT's credit parameters, from the testbench's cfg_fc_o.
        cfg = int(d.cfg_fc_o.value) >> 20
        fields = []
        for w in (12, 8, 12, 8, 12, 8):         # FC_CPLD up to FC_PH
            fields.append(cfg & ((1 << w) - 1))
            cfg >>= w
        cpld, cplh, npd, nph, pd, ph = fields
        self.adv = {"P": (ph, pd), "NP": (nph, npd), "Cpl": (cplh, cpld)}
        self.shadow = arch.Dll("shadow", {"ph": ph, "pd": pd, "nph": nph, "npd": npd,
                                          "cplh": cplh, "cpld": cpld})
        self._rx_reset()
        for name in ("slot_rx_tlp_i", "slot_rx_valid_i", "slot_rx_data_i", "slot_rx_sop_i",
                     "slot_rx_eop_i"):
            getattr(d, name).value = 0
        d.slot_tx_i.value = int(self.slot_tx)
        d.slot_rx_i.value = int(self.slot_rx)

    def _rx_reset(self) -> None:
        """The receive counters and UpdateFC record on entering DL_Inactive."""
        for c, cls in enumerate(CLASSES):
            self.rx_alloc[c] = [self.adv[cls][f] or RESERVE[f] for f in (0, 1)]
            self.rx_rcvd[c] = [0, 0]
            self.upd_hist[c] = [(self.cycle, self.adv[cls])]
            self.upd_sent[c] = self.adv[cls]
        self.upd_offer = {}
        self.upd_rr = 0
        self.upd_pending = {}

    def upd_value(self, c: int) -> tuple[int, int]:
        """What an UpdateFC of class c carries now: CREDITS_ALLOCATED, an
        infinite field as 0."""
        adv = self.adv[CLASSES[c]]
        return tuple(self.rx_alloc[c][f] if adv[f] else 0 for f in (0, 1))

    def upd_value_at(self, c: int, cycle: int) -> tuple[int, int]:
        v = self.upd_hist[c][0][1]
        for t, val in self.upd_hist[c]:
            if t > cycle:
                break
            v = val
        return v

    def inject(self, pkt: bytes, *, dllp: bool = True, err: bool = False,
               gaps: int = 0) -> None:
        """Queue a raw packet towards the DUT (hand-driven far end), with
        ``gaps`` idle cycles after each beat but the last."""
        beats = ptn.to_beats(pkt, dllp)
        if err:
            beats[-1] = beats[-1]._replace(err=True)
        for i, b in enumerate(beats):
            self.raw.append(b)
            if i < len(beats) - 1:
                self.raw.extend([None] * gaps)

    # ------------------------------------------------------------ one cycle

    async def step(self) -> None:
        d, n = self.dut, self.cycle
        valid = int(d.pkt_tx_valid_o.value)
        state = int(d.dl_state_o.value)
        err = int(d.err_o.value)
        if err:
            self.errs.append((n, err))
            if err & ERR_BAD_DLLP:
                self.bad_seen += 1
            if err & ERR_OVERFLOW:
                if n in self.ovf_due:
                    self.ovf_due.remove(n)
                    self.ovf_seen += 1
                else:
                    self.violations.append(f"cycle {n}: Receiver overflow with no TLP "
                                           f"that its credits do not cover")
            if err & ~(ERR_BAD_DLLP | ERR_OVERFLOW):
                self.violations.append(f"cycle {n}: err_o {err:#04x}: only Bad DLLP and "
                                       f"Receiver overflow are reported before the TLP "
                                       f"paths exist")
        for due in [c for c in self.ovf_due if c < n]:
            self.violations.append(f"cycle {n}: no Receiver overflow pulse at cycle {due}")
            self.ovf_due.remove(due)
        if state != self.state:
            COV_STATES.sample(arc=(self.state, state))
            self.states.append((n, state))
            if state == ACTIVE:
                self.upd_entry = {0, 1, 2}
                self.upd_rr = 0
                self.active_since = n
            if state == INACTIVE:
                self._rx_reset()
            self.state = state
        self._check_counters(n)
        if int(d.dl_up_o.value) != int(state in (INIT2, ACTIVE)):
            self.violations.append(f"cycle {n}: dl_up_o {int(d.dl_up_o.value)} in "
                                   f"{STATE_NAMES[state]}")
        if state == INACTIVE:
            self._check_quiet(n)
        if self.in_pkt and not valid:
            # The MAC keeps the packet open until its eop whatever the link
            # does (mac MAS, Frame): a bubble is a contract break.
            self.violations.append(f"cycle {n}: pkt_tx_valid_o dropped inside a packet")
        if not valid:
            self.tx_offer = None

        # Slot drivers: the upper streams, settled and checked before the
        # shadow sees this cycle's received packets.
        if self.slot_tx or self.slot_rx:
            await self._slots(n)

        # phy stand-in: transmit ready, the line towards the far end. With
        # ``fwd`` it also sends the accepted TLPs, between the DUT's packets.
        fwd_beat = None
        if self.in_pkt:
            ready = 1
        elif self.fwd_cur:
            ready = 0
            fwd_beat = self.fwd_cur.pop(0)
        elif self.framing:
            ready = 0
        elif self.fwd_q and not valid:
            ready = 0
            self.fwd_cur = self.fwd_q.popleft()
            fwd_beat = self.fwd_cur.pop(0)
        else:
            ready = int(self.rng.random() < self.ready_p)
        self.framing = fwd_beat is not None and fwd_beat.eop
        d.pkt_tx_ready_i.value = ready
        to_far = fwd_beat
        if valid:
            beat = ptn.Beat(int(d.pkt_tx_data_o.value), bool(d.pkt_tx_sop_o.value),
                            bool(d.pkt_tx_eop_o.value), bool(d.pkt_tx_dllp_o.value))
            if beat.sop and not self.in_pkt and not self.tx_beats:
                self.tx_state = state
                if self.tx_offer is None:
                    self.tx_offer = n           # the arbiter took it at the edge before
            if beat.sop and not ready:
                self.tx_sop_wait += 1
            if ready:
                to_far = beat
                if (beat.sop or self.in_pkt) and not self.dut_link:   # same edge samples link
                    self.tail_beats += 1
                self._tx_beat(n, beat)
                if beat.eop:
                    if self.tail_beats:
                        COV_TAIL.sample(beats_after_link_down=self.tail_beats)
                        self.tails.append((n, self.tail_beats))
                    self.tail_beats = 0
        # far end
        if self.partner is not None:
            p = self.partner
            p.link_up = self.partner_link
            p.in_l0 = self.partner_link
            p.tx_ready = not (self.gap_p and self.rng.random() < self.gap_p)
            far_out = p.clock(self.gate_far.pass_beat(self.to_far.push(to_far),
                                                      self.partner_link))
            ps = PARTNER_STATE[p.state]
            if ps != self.partner_states[-1][1]:
                self.partner_states.append((n, ps))
            in_beat = self.gate_dut.pass_beat(self.to_dut.push(far_out), self.dut_link)
        else:
            self.to_far.push(to_far)
            in_beat = self.raw.popleft() if self.raw else None
        self._drive_rx(in_beat)
        d.link_up_i.value = int(self.dut_link)
        d.ltssm_state_i.value = L0_CODE if self.dut_link else 0

        # shadow: the DUT samples these inputs at the next edge; a received
        # packet reaches it one cycle later, as the DUT's DLLP receive
        # registers it.
        sh = self.shadow
        sh.set_link_up(self.dut_link, n)
        while self.shadow_rx and self.shadow_rx[0][0] <= n:
            sh.receive(self.shadow_rx.popleft()[1], n)
        if in_beat is not None:
            self._rx_collect(n, in_beat)
        elif self.rx_beats:
            self.rx_gap = True
        sh.tick(n, True, True, True)
        if sh.state != self.shadow_states[-1][1]:
            self.shadow_states.append((n, sh.state))

        await FallingEdge(d.clk_i)
        self.cycle += 1

    async def run(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.step()

    async def run_until(self, cond, limit: int, what: str) -> int:
        """Step until ``cond()``; fail after ``limit`` cycles."""
        for i in range(limit):
            if cond():
                return i
            await self.step()
        first = f"; first violation: {self.violations[0]}" if self.violations else ""
        raise AssertionError(f"cycle {self.cycle}: {what} not reached within {limit} cycles"
                             f"{first}")

    # ------------------------------------------------------------ slot drivers

    async def _slots(self, n: int) -> None:
        """Drive the upper streams and the receive decision for this cycle,
        let the combinational gate and fit settle, check them, and apply what
        the next edge takes."""
        d = self.dut
        live = self.dut_link and self.state != INACTIVE
        if not live:
            self.tlp_cur = None
            self.fwd_q.clear()
            self.fwd_cur = []
            self.fwd_seq = 0
            self.rx_dec_q.clear()
            self.rx_store.clear()
            self.rx_cur = None
        # Transmit: a beat of the TLP being taken, a new TLP's sop, or an
        # idle random header (the gate is checked on it too).
        tx_valid = tx_sop = tx_eop = 0
        tx_head = random_header(self.rng)
        if self.slot_tx:
            if self.state != ACTIVE:
                self.tlp_cur = None
            # Forwarding, the source waits while 3 TLPs are queued for the
            # line, as a full replay buffer would hold it back.
            if self.tlp_cur is None and self.tlp_q and self.state == ACTIVE \
                    and self.dut_link and self.rng.random() < self.valid_p \
                    and len(self.fwd_q) < 3:
                self.tlp_cur, self.tlp_idx = tlp_dws(self.tlp_q[0]), 0
            if self.tlp_cur is not None:
                # The sop is held until taken; valid may drop between beats.
                tx_valid = int(self.tlp_idx == 0 or self.rng.random() < self.valid_p)
                tx_sop = int(self.tlp_idx == 0)
                tx_eop = int(self.tlp_idx == len(self.tlp_cur) - 1)
                dw = self.tlp_cur[self.tlp_idx]
                if tx_sop:
                    tx_head = self.tlp_q[0][:4]
            else:
                tx_sop = 1
                dw = int.from_bytes(tx_head, "little")
            d.tlp_tx_valid_i.value = tx_valid
            d.tlp_tx_data_i.value = dw
            d.tlp_tx_sop_i.value = tx_sop
            d.tlp_tx_eop_i.value = tx_eop
        # Receive: one decision, the upper stream's next beat.
        dec = None
        rx_head = random_header(self.rng)
        if self.slot_rx:
            if live and self.state in (INIT2, ACTIVE) and self.rx_dec_q:
                dec = self.rx_dec_q.popleft()
                if not dec[2]:
                    rx_head = dec[0][:4]
            rx_dw0 = int.from_bytes(rx_head, "little")
            d.slot_rx_tlp_i.value = ((int(dec is not None) << 34) | (int(bool(dec and dec[1])) << 33)
                                     | (int(bool(dec and dec[2])) << 32) | rx_dw0)
            if self.rx_cur is None and self.rx_store and live:
                self.rx_cur_tlp = self.rx_store.popleft()
                self.rx_cur, self.rx_idx = tlp_dws(self.rx_cur_tlp), 0
            up_valid = int(self.rx_cur is not None and live)
            d.slot_rx_valid_i.value = up_valid
            d.slot_rx_data_i.value = self.rx_cur[self.rx_idx] if up_valid else 0
            d.slot_rx_sop_i.value = int(up_valid and self.rx_idx == 0)
            d.slot_rx_eop_i.value = int(up_valid and self.rx_idx == len(self.rx_cur) - 1)
            if self.up_stall:
                self.up_stall -= 1
                up_ready = 0
            else:
                up_ready = int(self.rng.random() < self.up_ready_p)
            d.tlp_rx_ready_i.value = up_ready
        await Timer(1, "ns")

        if self.slot_tx:
            self._tx_gate(n, tx_head, tx_valid, tx_sop, tx_eop)
        if self.slot_rx:
            self._rx_decide(n, rx_head, dec)
            if up_valid and up_ready:
                self.rx_idx += 1
                if self.rx_idx == len(self.rx_cur):
                    self._rx_return(self.rx_cur_tlp)
                    self.rx_cur = None

    def _tx_gate(self, n: int, head: bytes, valid: int, sop: int, eop: int) -> None:
        """Check the credit gate on the offered sop against the rule and the
        architecture model, then take the beat if the forced ready did."""
        d, sh = self.dut, self.shadow
        ok = int(d.probe_tx_ok_o.value)
        ready = int(d.tlp_tx_ready_o.value)
        if sop:
            c, need = tlp_need(head)
            fields = gate_rule(sh.cl[c], sh.inf[c], sh.cc[c], need)
            want = int(self.state == ACTIVE and all(fields))
            model = int(self.state == ACTIVE and sh.credits_ok(c, need))
            self.gate_checks += 1
            if ok != want or model != want:
                self.violations.append(
                    f"cycle {n}: gate {ok} for {CLASSES[c]} {need} ({head.hex()}), expected "
                    f"{want} (arch {model}); limit {sh.cl[c]} infinite {sh.inf[c]} consumed "
                    f"{sh.cc[c]}")
            if self.state == ACTIVE:
                if all(sh.inf[c]):
                    gate = "infinite"
                elif all(fields):
                    gate = "open"
                else:
                    gate = {(False, True): "closed_hdr", (True, False): "closed_data"}.get(
                        tuple(fields), "closed_both")
                COV_FC_TX.sample(gate=gate, cls=CLASSES[c])
        if self.tlp_cur is None or not valid:
            return
        if not ready:
            if sop:
                self.refused += 1
                if not self.refusing:
                    self.refuse_since = n
                self.refusing = True
                COV_FC_TX.sample(starve="refused")
            return
        if sop:
            if self.refusing:
                COV_FC_TX.sample(starve="recovered")
            self.refusing = False
            c, need = tlp_need(head)
            for f in (0, 1):
                old = sh.cc[c][f]
                sh.cc[c][f] = (old + need[f]) % MODS[f]
                if sh.cc[c][f] < old:
                    COV_FC_TX.sample(consumed_wrap=("hdr", "data")[f])
            self.accepted.append(self.tlp_q[0])
        self.tlp_idx += 1
        if eop:
            tlp = self.tlp_q.popleft()
            self.tlp_cur = None
            if self.fwd:
                self.fwd_q.append(ptn.to_beats(ptn.frame_tlp(self.fwd_seq, tlp), False))
                self.fwd_seq = (self.fwd_seq + 1) % 4096

    def _rx_decide(self, n: int, head: bytes, dec) -> None:
        """Check the overflow check on the decided (or idle) header against
        the rule and the architecture model; apply the decision."""
        d, sh = self.dut, self.shadow
        fits = int(d.probe_rx_fits_o.value)
        no_hdr = bool(dec and dec[2])
        c, need = tlp_need(head)
        if no_hdr:
            need = (0, 0)
        free = [(self.rx_alloc[c][f] - self.rx_rcvd[c][f]) % MODS[f] for f in (0, 1)]
        want = int(all(need[f] <= free[f] for f in (0, 1)))
        model = int(sh._rx_space(c, need))
        self.fits_checks += 1
        # In DL_Inactive the counters are being cleared and nothing is decided.
        if self.state != INACTIVE and (fits != want or model != want):
            self.violations.append(f"cycle {n}: fits {fits} for {CLASSES[c]} {need}, expected "
                                   f"{want} (arch {model}); free {free}")
        if dec is None:
            return
        tlp, malformed, _ = dec
        adv = self.adv[CLASSES[c]]
        COV_FC_RX.sample(field="infinite" if 0 in adv else "finite")
        if malformed:
            decision = "malformed_no_hdr" if no_hdr else "malformed"
            for f in (0, 1):
                self.rx_alloc[c][f] = (self.rx_alloc[c][f] + need[f]) % MODS[f]
                self.rx_rcvd[c][f] = (self.rx_rcvd[c][f] + need[f]) % MODS[f]
                if adv[f]:
                    sh.ca[c][f] = (sh.ca[c][f] + need[f]) % MODS[f]
                    sh.cr[c][f] = (sh.cr[c][f] + need[f]) % MODS[f]
        elif want:
            decision = "stored"
            for f in (0, 1):
                self.rx_rcvd[c][f] = (self.rx_rcvd[c][f] + need[f]) % MODS[f]
                if adv[f]:
                    sh.cr[c][f] = (sh.cr[c][f] + need[f]) % MODS[f]
                else:
                    sh.occ[c][f] += need[f]
            self.rx_store.append(tlp)
        else:
            decision = "overflow"
            self.ovf_due.append(n + 1)
        self.decided.append((n, decision, c))
        COV_FC_RX.sample(decision=decision, cls=CLASSES[c])

    def _rx_return(self, tlp: bytes) -> None:
        """The upper layer took a delivered TLP's eop: its credits return."""
        sh = self.shadow
        c, need = tlp_need(tlp)
        adv = self.adv[CLASSES[c]]
        for f in (0, 1):
            self.rx_alloc[c][f] = (self.rx_alloc[c][f] + need[f]) % MODS[f]
            if adv[f]:
                sh.ca[c][f] = (sh.ca[c][f] + need[f]) % MODS[f]
            else:
                sh.occ[c][f] -= need[f]
        self.delivered.append(tlp)
        COV_FC_RX.sample(returned=CLASSES[c])

    def _check_counters(self, n: int) -> None:
        """At the start of a cycle: credits available equal the
        architecture model's; the receive counters agree with its counters;
        the UpdateFC value record; a returned credit is not left unsent."""
        sh = self.shadow
        if self.slot_tx and self.state == ACTIVE and sh.state == arch.ACTIVE:
            got = self.dut_avail()
            for c, cls in enumerate(CLASSES):
                want = (sh.avail(c, 0), sh.avail(c, 1))
                if got[cls] != want:
                    self.violations.append(f"cycle {n}: {cls} credits available {got[cls]}, "
                                           f"arch {want}")
        if self.slot_rx:
            for c, cls in enumerate(CLASSES):
                for f in (0, 1):
                    mine = (self.rx_alloc[c][f] - self.rx_rcvd[c][f]) % MODS[f]
                    model = ((sh.ca[c][f] - sh.cr[c][f]) % MODS[f] if self.adv[cls][f]
                             else RESERVE[f] - sh.occ[c][f])
                    if mine != model:
                        self.violations.append(f"cycle {n}: {cls} field {f} free credits "
                                               f"{mine}, arch {model}")
        for c in range(3):
            v = self.upd_value(c)
            if v != self.upd_hist[c][-1][1]:
                self.upd_hist[c].append((n, v))
                if self.state == ACTIVE or self.state == INIT2:
                    self.upd_pending.setdefault(c, n)
            if self.state == ACTIVE and self.upd_bound is not None and c in self.upd_pending:
                since = max(self.upd_pending[c], self.active_since)
                if n - since > self.upd_bound:
                    self.violations.append(f"cycle {n}: credits returned for {CLASSES[c]} at "
                                           f"cycle {since} not sent within {self.upd_bound}")
                    self.upd_pending.pop(c)

    # ------------------------------------------------------------ helpers

    def _drive_rx(self, b: ptn.Beat | None) -> None:
        d = self.dut
        d.pkt_rx_valid_i.value = int(b is not None)
        if b is None:
            d.pkt_rx_sop_i.value = 0
            d.pkt_rx_eop_i.value = 0
            d.pkt_rx_err_i.value = 0
            d.pkt_rx_nullify_i.value = 0
            return
        d.pkt_rx_data_i.value = b.data
        d.pkt_rx_sop_i.value = int(b.sop)
        d.pkt_rx_eop_i.value = int(b.eop)
        d.pkt_rx_dllp_i.value = int(b.dllp)
        d.pkt_rx_err_i.value = int(b.err)
        d.pkt_rx_nullify_i.value = int(b.nullify)

    def _rx_collect(self, n: int, b: ptn.Beat) -> None:
        """Assemble what the DUT receives: for the shadow, for coverage and
        for the expected Bad DLLP pulses, judged by the models' CRC."""
        if b.sop:
            self.rx_beats = []
            self.rx_gap = False
        self.rx_beats.append(b)
        if not b.eop:
            return
        data = bytes(v for x in self.rx_beats for v in (x.data & 0xFF, x.data >> 8))
        self.shadow_rx.append((n + 1, arch.Packet(b.dllp, data, b.err, b.nullify)))
        self.rx_beats = []
        if not b.dllp:
            return
        if len(data) == 6 and arch_crc.crc16(data[:4]) != ptn_crc.crc16_bytes(data[:4]):
            self.violations.append(f"cycle {n}: CRC-16 of {data[:4].hex()}: arch "
                                   f"{arch_crc.crc16(data[:4]).hex()}, partner "
                                   f"{ptn_crc.crc16_bytes(data[:4]).hex()}")
        live = self.state != INACTIVE and self.dut_link
        if not live:
            outcome = "inactive_discard"
        elif b.err:
            outcome = "flagged"
        elif len(data) != 6:
            outcome = "bad_length"
        elif data[4:] != ptn_crc.crc16_bytes(data[:4]):
            outcome = "bad_crc"
        else:
            outcome = "good"
        if live and outcome != "good":
            self.bad_expected += 1
        kinds = {}
        if outcome == "good":
            kind, _, vc, _, _ = arch.decode_dllp(data)
            kinds["kind"] = {"other": "other_type"}.get(kind, kind)
            if kind in ("init1", "init2", "update") and vc:
                kinds["kind"] = "other_vc"
            self.rx_good.append((n, data))
        COV_RX.sample(outcome=outcome, gap="gap_inside" if self.rx_gap else "no_gap", **kinds)

    def _check_quiet(self, n: int) -> None:
        """In DL_Inactive every output holds its reset value (HAS); only the
        tail of a DLLP the phy took before the link went down may follow."""
        d = self.dut
        if int(d.pkt_tx_valid_o.value) and (int(d.pkt_tx_sop_o.value) or not self.in_pkt):
            self.violations.append(f"cycle {n}: a packet offered in DL_Inactive")
        for name in ("tlp_tx_ready_o", "tlp_rx_valid_o", "tlp_rx_sop_o",
                     "tlp_rx_eop_o", "fc_ph_avail_o", "fc_pd_avail_o", "fc_nph_avail_o",
                     "fc_npd_avail_o", "fc_cplh_avail_o", "fc_cpld_avail_o", "dl_up_o",
                     "err_o", "retrain_req_o"):
            v = int(getattr(d, name).value)
            if v:
                self.violations.append(f"cycle {n}: {name} {v:#x} in DL_Inactive")

    def _tx_beat(self, n: int, b: ptn.Beat) -> None:
        if b.sop:
            if self.in_pkt:
                self.violations.append(f"cycle {n}: sop inside a packet")
            self.tx_beats = []
            self.tx_sop_cycle = n
            self.tx_sop_offer = self.tx_offer if self.tx_offer is not None else n
            self.tx_offer = None
            COV_TX.sample(stall="sop_waited" if self.tx_sop_wait else "sop_at_once")
            self.tx_sop_wait = 0
        elif not self.in_pkt:
            self.violations.append(f"cycle {n}: beat outside a packet")
            return
        self.tx_beats.append(b)
        self.in_pkt = not b.eop
        if b.eop:
            self.framing = True
            self._tx_packet(n, self.tx_beats)
            self.tx_beats = []

    def _tx_packet(self, n: int, beats: list[ptn.Beat]) -> None:
        """Check one DLLP the DUT sent against the HAS and both models."""
        sop = self.tx_sop_cycle
        data = bytes(v for x in beats for v in (x.data & 0xFF, x.data >> 8))
        st = self.tx_state
        where = f"cycle {sop}: DLLP {data.hex()} in {STATE_NAMES[st]}"
        if not all(x.dllp for x in beats) or len(data) != 6:
            self.violations.append(f"{where}: not a 6-byte DLLP")
            return
        body, crc = data[:4], data[4:]
        if crc != arch_crc.crc16(body) or crc != ptn_crc.crc16_bytes(body):
            self.violations.append(f"{where}: CRC-16 {crc.hex()}, arch "
                                   f"{arch_crc.crc16(body).hex()}, partner "
                                   f"{ptn_crc.crc16_bytes(body).hex()}")
        kind, cls, vc, hdr, dat = arch.decode_dllp(data)
        want = TX_KINDS.get(st)
        if kind != want or vc != 0:
            self.violations.append(f"{where}: kind {kind} VC {vc}, expected {want} VC 0")
            return
        h, dd = self.adv[CLASSES[cls]]
        if kind == "update":
            h, dd = self._check_update(sop, cls)
        expect = arch.fc_dllp(ARCH_KINDS[kind], cls, h, dd)
        typ = {"init1": ptn.INITFC1, "init2": ptn.INITFC2, "update": ptn.UPDATEFC}[kind]
        if data != expect or data != ptn.make_dllp(typ[CLASSES[cls]], h, dd):
            self.violations.append(f"{where}: expected {expect.hex()} for "
                                   f"{kind}-{CLASSES[cls]} {h}, {dd}")
        prev = self.sent[-1] if self.sent else None
        if kind in ("init1", "init2"):
            # Groups go P, NP, Cpl back to back.
            if cls == 0:
                resend = bool(self.group_starts) and prev is not None and prev[1] == kind
                self.group_starts.append(sop)
                COV_TX.sample(resend="resend" if resend else "first")
            elif prev is None or prev[1] != kind or prev[2] != cls - 1:
                self.violations.append(f"{where}: {kind} class {cls} out of group order")
        else:
            self.upd_last[cls] = sop
        COV_TX.sample(kind=kind, cls=CLASSES[cls])
        self.sent.append((sop, kind, cls, data))
        self.offers.append(self.tx_sop_offer)

    def _check_update(self, sop: int, c: int) -> tuple[int, int]:
        """An UpdateFC of class c whose request the arbiter took at the edge
        before cycle ``o`` (its first offer): it carries CREDITS_ALLOCATED as
        the registers showed it at o - 1, it was due then (entry, credits
        returned, or 2048 cycles since the class's last), and it is the first
        due class from the round-robin pointer. Returns the expected values."""
        o = self.tx_sop_offer
        t = o - 1
        due = []
        for k in range(3):
            q = (self.upd_rr + k) % 3
            entry = q in self.upd_entry
            changed = self.upd_value_at(q, t) != self.upd_sent[q]
            refresh = q in self.upd_offer and t - self.upd_offer[q] >= REFRESH - 1
            if entry or changed or refresh:
                due.append((q, "entry" if entry else "returned" if changed else "refresh"))
        where = f"cycle {sop}: UpdateFC-{CLASSES[c]} offered at {o}"
        if not due:
            self.violations.append(f"{where}: no class due")
        elif due[0][0] != c:
            self.violations.append(f"{where}: due in round-robin order {due}")
        cause = next((why for q, why in due if q == c), "refresh")
        COV_UPDFC.sample(cause=cause, cls=CLASSES[c], rr_skip=(c - self.upd_rr) % 3)
        value = self.upd_value_at(c, t)
        self.upd_entry.discard(c)
        self.upd_sent[c] = value
        self.upd_offer[c] = o
        self.upd_rr = (c + 1) % 3
        if c in self.upd_pending and self.upd_pending[c] <= t:
            since = max(self.upd_pending.pop(c), self.active_since)
            self.upd_lat_max = max(self.upd_lat_max, o - since)
            later = [tc for tc, _ in self.upd_hist[c] if tc > t]
            if later:
                self.upd_pending[c] = later[0]
        return value

    # ------------------------------------------------------------ checks

    def dut_avail(self) -> dict[str, tuple[int, int]]:
        d = self.dut
        return {"P": (int(d.fc_ph_avail_o.value), int(d.fc_pd_avail_o.value)),
                "NP": (int(d.fc_nph_avail_o.value), int(d.fc_npd_avail_o.value)),
                "Cpl": (int(d.fc_cplh_avail_o.value), int(d.fc_cpld_avail_o.value))}

    def check_avail(self, expect: dict[str, tuple[int, int]]) -> None:
        """The credits-available outputs in DL_Active: ``expect`` gives the
        partner's advertisement (0 infinite, shown as all ones)."""
        got = self.dut_avail()
        for i, cls in enumerate(CLASSES):
            want = tuple((0xFF, 0xFFF)[f] if expect[cls][f] == 0 else expect[cls][f]
                         for f in (0, 1))
            shadow = (self.shadow.avail(i, 0), self.shadow.avail(i, 1))
            assert got[cls] == want, f"{cls} credits available {got[cls]}, expected {want}"
            assert shadow == want, f"{cls}: shadow (arch) shows {shadow}, expected {want}"

    def check_shadow(self, bound: int) -> int:
        """The DUT and the shadow visit the same states in the same order,
        each change within ``bound`` cycles of the other; returns the
        largest difference."""
        a = [s for _, s in self.states]
        b = [s for _, s in self.shadow_states]
        assert a == b, (f"state sequence: DUT {[STATE_NAMES[s] for s in a]}, shadow "
                        f"(arch) {[STATE_NAMES[s] for s in b]}")
        worst = 0
        for (ca, sa), (cb, _) in zip(self.states, self.shadow_states):
            diff = abs(ca - cb)
            assert diff <= bound, (f"{STATE_NAMES[sa]}: DUT at cycle {ca}, shadow (arch) at "
                                   f"cycle {cb}, more than {bound} apart")
            worst = max(worst, diff)
        return worst

    def check_clean(self) -> None:
        assert not self.violations, "DUT violations:\n" + "\n".join(self.violations[:20])
        assert not self.ovf_due, f"Receiver overflow pulses missing at cycles {self.ovf_due}"
        assert self.bad_seen == self.bad_expected, (
            f"Bad DLLP pulses: {self.bad_seen}, expected {self.bad_expected} from the "
            f"received packets")
        if self.partner is not None:
            assert not self.partner.violations, ("partner violations:\n"
                                                 + "\n".join(self.partner.violations[:20]))


# ---------------------------------------------------------------------------
# The architecture model against the partner, without the RTL.


class ArchEnd:
    """A ``pcie_dll_arch.Dll`` on the partner's beat stream: packets it
    starts go out one beat per cycle (its ``tick`` keeps one framing cycle
    between packets), received beats are collected into packets."""

    def __init__(self, dll: arch.Dll) -> None:
        self.dll = dll
        self.link_up = False
        self.now = 0
        self.out: deque[ptn.Beat] = deque()
        self.rx: list[ptn.Beat] = []
        self.queue: deque[bytes] = deque()     # TLPs its upper layer offers

    def clock(self, beat_in: ptn.Beat | None) -> ptn.Beat | None:
        d, n = self.dll, self.now
        self.now += 1
        d.set_link_up(self.link_up, n)
        if not self.link_up:
            self.out.clear()
            self.rx = []
            return None
        if beat_in is not None:
            if beat_in.sop:
                self.rx = []
            self.rx.append(beat_in)
            if beat_in.eop:
                data = bytes(v for x in self.rx for v in (x.data & 0xFF, x.data >> 8))
                d.receive(arch.Packet(beat_in.dllp, data, beat_in.err, beat_in.nullify), n)
                self.rx = []
        if self.queue and d.offer(self.queue[0], n):
            self.queue.popleft()
        pkt = d.tick(n, True, not self.out, True)
        if pkt is not None:
            self.out.extend(ptn.to_beats(pkt.data, pkt.dllp))
        return self.out.popleft() if self.out else None


def arch_partner_init(adv: dict[str, tuple[int, int]], partner: ptn.Partner, *,
                      arch_up: int, partner_up: int, delay: int = 22,
                      limit: int = 20000) -> tuple[int, arch.Dll]:
    """Link the architecture model (credits ``adv``) and ``partner`` up at
    the given cycles; return the cycle both are in DL_Active, and the model."""
    fc = {"ph": adv["P"][0], "pd": adv["P"][1], "nph": adv["NP"][0], "npd": adv["NP"][1],
          "cplh": adv["Cpl"][0], "cpld": adv["Cpl"][1]}
    a = ArchEnd(arch.Dll("arch", fc))
    ab, ba = ptn.Line(delay), ptn.Line(delay)
    gate_a, gate_p = Gate(), Gate()
    to_a = to_p = None
    for n in range(limit):
        a.link_up = n >= arch_up
        partner.link_up = n >= partner_up
        out_a = a.clock(to_a)
        out_p = partner.clock(to_p)
        to_p = gate_p.pass_beat(ab.push(out_a), partner.link_up)
        to_a = gate_a.pass_beat(ba.push(out_p), a.link_up)
        if a.dll.state == arch.ACTIVE and partner.state == ptn.ACTIVE:
            return n, a.dll
    raise AssertionError(f"arch model and partner not both in DL_Active within {limit} cycles")


def arch_partner_traffic(adv: dict[str, tuple[int, int]], partner: ptn.Partner,
                         tlps: list[bytes], schedule, rng: random.Random, *,
                         delay: int = 22, drain: int = 20000) -> arch.Dll:
    """The architecture model (credits ``adv``) sends ``tlps`` to
    ``partner``, whose upper layer follows ``schedule`` (cycles, take
    probability), then takes everything until the model has sent them all.
    Returns the model; the caller checks what the partner received."""
    fc = {"ph": adv["P"][0], "pd": adv["P"][1], "nph": adv["NP"][0], "npd": adv["NP"][1],
          "cplh": adv["Cpl"][0], "cpld": adv["Cpl"][1]}
    a = ArchEnd(arch.Dll("arch", fc))
    a.queue.extend(tlps)
    ab, ba = ptn.Line(delay), ptn.Line(delay)
    a.link_up = partner.link_up = True
    to_a = to_p = None

    def cycle() -> None:
        nonlocal to_a, to_p
        out_a = a.clock(to_a)
        out_p = partner.clock(to_p)
        to_p, to_a = ab.push(out_a), ba.push(out_p)

    for cycles, take in schedule:
        for _ in range(cycles):
            partner.upper_ready = rng.random() < take
            cycle()
    partner.upper_ready = True
    for _ in range(drain):
        if not a.queue and not a.dll.rbuf and len(partner.received) == len(tlps):
            break
        cycle()
    return a.dll
