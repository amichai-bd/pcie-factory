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
   equal to what both models encode; an Ack or Nak carries the sequence
   number the receive reference expects. Each UpdateFC must be due (entry,
   credits returned, or the refresh) and be the first due class in round
   robin.
6. The **receive reference** (``RxRef``) judges every TLP packet the DUT
   receives by the HAS receive rules, with the partner's CRC and header
   helpers, and keeps the receive credit counters: the TLPs the upper
   stream must deliver, the Bad TLP, Malformed TLP and Receiver overflow
   pulses, NEXT_RCV_SEQ and each NAK_SCHEDULED setting. The **upper
   layer** takes received beats (``tlp_rx_ready_i``, stalling with
   ``rx_ready_p`` below 1 only when every class is finite), checks each
   delivered TLP against the reference and returns its credits. With
   FC_RELEASE 1 (HAS, credit release) it returns a posted or non-posted
   TLP's finite credits later, on ``fc_rel_*_i``: the **releaser** holds
   each one at least ``rel_delay`` cycles, releases per class in delivery
   order with probability ``rel_p``, none before ``rel_stall_until``, and
   checks it never holds more than the DUT advertises.
7. The **upper transmit driver** offers queued TLPs on ``tlp_tx_*`` (valid
   may drop inside a TLP) and records each one the DUT takes; every TLP the
   DUT sends is checked (``_tx_tlp``): its sequence number, the TLP taken
   with it, its LCRC equal to both models' and zlib's, and the transmit
   order (``_tx_take``): new TLPs in order, each replay from the oldest
   unacknowledged TLP, in order and complete, against the Acks and Naks the
   DUT received. A **retrain stand-in** answers ``retrain_req_o`` with a
   pass through Recovery (``ltssm_state_i`` out of L0, nothing on the line).
8. The **credit gate** (``probe_tx_ok_o``, from the bind tap on the
   MAS-named ``fc_tx_ok``) is checked every cycle on the offered first DW
   (a TLP's sop, or a random header while idle) against the flow-control
   rule and the architecture model, with the limits as the DUT's registers
   hold them and CREDITS_CONSUMED counted here from the sops the DUT took;
   a taken sop needs the gate open; the credits available match.
"""

from __future__ import annotations

import random
import sys
import zlib
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
ERR_BAD_TLP = 1 << 0                # err_o bit 0 (HAS, errors)
ERR_BAD_DLLP = 1 << 1               # err_o bit 1
ERR_MALFORMED = 1 << 6              # err_o bit 6
ERR_ALLOWED = ERR_BAD_TLP | ERR_BAD_DLLP | ERR_MALFORMED | (1 << 5)   # with ERR_TX: all bits
ERR_OVERFLOW = 1 << 5               # err_o bit 5, Receiver overflow
# Receive verdicts as the reference names them, in the classes the shadow's
# counters tell apart.
OUTCOME_CLASS = {"bad_lcrc": "bad", "flagged": "bad", "too_short": "bad", "edb_bad": "bad",
                 "ahead": "bad", "nullified": "none", "not_live": "none"}
MODS = (256, 4096)                  # header and data credit counters
RESERVE = (8, 24)                   # receive space of an infinite field
REFRESH = 2048                      # UpdateFC refresh, cycles
L0_CODE = 0x20                      # an L0 code of ltssm_state_i (mac LTSSM encoding)
RECOVERY_CODE = 0x10                # Recovery.RcvrLock: not L0
# err_o bits of the TLP transmit path (HAS, errors); bits 0, 5 and 6 are the
# receive path's and the receive credit check's.
ERR_TIMEOUT, ERR_ROLLOVER, ERR_PROTOCOL = 2, 3, 4
ERR_TX = (1 << ERR_TIMEOUT) | (1 << ERR_ROLLOVER) | (1 << ERR_PROTOCOL)
SEQ_MOD = 4096
ACK_BOUND = 118                     # HAS targets: an Ack starts within 118 cycles of the eop
RX_LATENCY = 4                      # HAS targets: eop from phy to upper sop, buffer empty

# DLLP types the DUT may send in each state (DLLP.md, types used): the
# InitFC of the state's group in DL_Init, UpdateFC in DL_Active; Ack and
# Nak only in DL_Active (checked in _tx_packet).
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
# A malformed TLP under 4 bytes has no header, so no class.
COV_FC_RX.cross("decision_x_cls", "decision", "cls",
                unreachable=[("malformed_no_hdr", c) for c in CLASSES])
COV_FC_RX.coverpoint("field", bins=["finite", "infinite"])
COV_FC_RX.coverpoint("returned", bins=["P", "NP", "Cpl"])
# Credit release (FC_RELEASE 1): released per class, data credits released,
# and the upper layer holding every advertised header credit of a class.
COV_FC_RX.coverpoint("released", bins=["P", "NP"])
COV_FC_RX.coverpoint("release_data", bins={"zero": 0, "some": range(1, 8), "max": 8})
COV_FC_RX.coverpoint("held_all", bins=["P", "NP"])
COV_FC_RX.coverpoint("release_state", bins=["fc_init2", "dl_active"])

# TLP transmit (DV plan, coverage): TLPs sent, their shape and whether new
# or replayed; replays and what ended them; the replay timer and rollover;
# received Acks and Naks; replay-buffer back-pressure.
COV_TLP_TX = Covergroup("dll_tlp_tx")
COV_TLP_TX.coverpoint("hdr", bins=["3dw", "4dw"])
COV_TLP_TX.coverpoint("payload", bins={"none": 0, "one_dw": 1, "some": range(2, 32), "max": 32})
COV_TLP_TX.coverpoint("send", bins=["new", "replay"])
COV_TLP_TX.cross("send_x_payload", "send", "payload")
COV_TLP_TX.coverpoint("upper_gap", bins=["no_gap", "gap_inside"])
COV_TLP_TX.coverpoint("seq_wrap", bins=["from_4095"])

COV_REPLAY = Covergroup("dll_replay")
COV_REPLAY.coverpoint("cause", bins=["nak", "timeout"])
COV_REPLAY.coverpoint("run", bins=["complete", "skipped_by_ack", "pending_after"])
COV_REPLAY.coverpoint("rollover", bins=["retrained", "fallback"])
COV_REPLAY.coverpoint("timer", bins=["held_outside_l0", "restarted_by_ack", "stopped_by_ack"])

COV_ACKNAK_RX = Covergroup("dll_acknak_rx")
COV_ACKNAK_RX.coverpoint("kind", bins=["ack", "nak"])
COV_ACKNAK_RX.coverpoint("effect", bins=["progress_one", "progress_many", "no_progress",
                                      "out_of_range"])
COV_ACKNAK_RX.cross("kind_x_effect", "kind", "effect")

COV_RBUF = Covergroup("dll_rbuf")
COV_RBUF.coverpoint("backpressure", bins=["full_max_tlps", "refused_by_bytes",
                                          "freed_by_ack", "held_until_replayed_eop"])


# TLP receive (DV plan, coverage): the outcome of each TLP packet the DUT
# receives, by the HAS rules (RETRY.md, receiving TLPs).
COV_TLP_RX = Covergroup("dll_tlp_rx")
COV_TLP_RX.coverpoint("outcome", bins=["stored", "duplicate", "ahead", "bad_lcrc", "flagged",
                                       "too_short", "nullified", "edb_bad", "malformed",
                                       "overflow", "not_live"])
COV_TLP_RX.coverpoint("cls", bins=list(CLASSES))
COV_TLP_RX.coverpoint("gap", bins=["no_gap", "gap_inside"])
COV_TLP_RX.coverpoint("nak", bins=["scheduled", "already_scheduled"])

COV_MALFORMED = Covergroup("dll_malformed")
COV_MALFORMED.coverpoint("cause", bins=["not_whole_dw", "short", "length", "payload",
                                        "no_header"])

# TLPs with a digest (TD 1, HAS receive): stored with it, or malformed
# (no digest, or a payload above 128 bytes); 3- and 4-DW headers.
COV_DIGEST = Covergroup("dll_digest")
COV_DIGEST.coverpoint("outcome", bins=["stored", "malformed"])
COV_DIGEST.coverpoint("hdr", bins=["3dw", "4dw"])

COV_ACKNAK = Covergroup("dll_acknak_tx")
COV_ACKNAK.coverpoint("kind", bins=["ack", "nak"])
COV_ACKNAK.coverpoint("cause", bins=["timer", "duplicate", "nak"])

COV_RX_STREAM = Covergroup("dll_rx_stream")
COV_RX_STREAM.coverpoint("beat", bins=["taken", "stalled"])
COV_RX_STREAM.coverpoint("sop", bins=["back_to_back", "after_idle"])

# TLP header byte 0 (Fmt, Type) to the class names of the partner.
def tlp_cls(tlp: bytes) -> str:
    return ptn.has_class(tlp) if len(tlp) >= 4 else "P"


class RxRef:
    """The HAS receive rules for TLPs (RETRY.md, receiving TLPs) applied
    to every TLP packet the DUT receives, written with the partner's CRC
    and header helpers only: neither the RTL nor the architecture model.
    With ``credits`` (the Env) it also keeps the receive credit counters
    (FLOW_CONTROL.md, receive accounting): an expected, well-formed TLP its
    class's free credits do not cover overflows (no Ack or Nak, NEXT_RCV_SEQ
    stays), a stored one adds to CREDITS_RECEIVED, a malformed one to both
    counters (nothing under 4 bytes)."""

    def __init__(self) -> None:
        self.expect: list[bytes] = []          # TLPs the upper stream must deliver, in order
        self.store_cycle: list[int] = []       # eop cycle of each, at the DUT input
        self.empty_at_store: list[bool] = []   # nothing older waiting when it was stored
        self.bad = 0                           # Bad TLP pulses due
        self.malformed = 0                     # Malformed TLP pulses due
        self.nak_sets = 0                      # NAK_SCHEDULED settings: one Nak each
        self.dups: list[int] = []              # eop cycle of each duplicate (Ack at once)
        self.bads: list[int] = []              # eop cycle of each packet that set NAK_SCHEDULED
        self.accepts: list[tuple[int, int, bool]] = []   # (eop cycle, seq, in DL_Active)
        self.hist: list[tuple[int, int]] = [(-1, 0)]     # (judging step, NEXT_RCV_SEQ after)
        self.nrs = 0
        self.nak_sched = False
        self.credits = None                    # the Env's receive counters
        self.overflow = 0                      # Receiver overflow pulses due

    def reset(self, step: int, delivered: int) -> None:
        """DL_Inactive: the receiver state clears, and stored TLPs not yet
        delivered are dropped (HAS, control states)."""
        if self.nrs:
            self.hist.append((step, 0))
        self.nrs = 0
        self.nak_sched = False
        del self.expect[delivered:], self.store_cycle[delivered:]
        del self.empty_at_store[delivered:]
        # TLPs accepted before the link went down may never be acknowledged.
        if any(a[2] for a in self.accepts[-1:]):
            self.accepts = [(e, q, False) for e, q, _ in self.accepts]

    def judge(self, step: int, eop: int, data: bytes, err: bool, nullify: bool, live: bool,
              active: bool, delivered: int) -> str:
        if not live:
            return "not_live"
        out = self._judge(eop, data, err, nullify, active, delivered)
        if out in ("bad_lcrc", "flagged", "too_short", "edb_bad", "ahead"):
            self.bad += 1
            COV_TLP_RX.sample(nak="already_scheduled" if self.nak_sched else "scheduled")
            if not self.nak_sched:
                self.nak_sched = True
                self.nak_sets += 1
                self.bads.append(eop)
        self.hist.append((step, self.nrs))
        return out

    def _judge(self, eop: int, data: bytes, err: bool, nullify: bool, active: bool,
               delivered: int) -> str:
        if err:
            return "flagged"
        if len(data) < 6:
            return "too_short"
        crc = ptn_crc.lcrc_bytes(data[:-4])
        if nullify:
            return "nullified" if data[-4:] == bytes(x ^ 0xFF for x in crc) else "edb_bad"
        if data[-4:] != crc:
            return "bad_lcrc"
        seq = ((data[0] & 0x0F) << 8) | data[1]
        tlp = data[2:-4]
        d = (seq - self.nrs) % 4096
        if d == 0:
            if len(tlp) >= 4 and ptn.tlp_digest(tlp):
                COV_DIGEST.sample(outcome="malformed" if ptn.tlp_malformed(tlp) else "stored",
                                  hdr="4dw" if tlp[0] & 0x20 else "3dw")
            if ptn.tlp_malformed(tlp):
                self.malformed += 1
                if self.credits is not None and len(tlp) >= 4:
                    c, need = CLASSES.index(ptn.has_class(tlp)), ptn.tlp_credits(tlp)
                    self.credits.rx_charge(c, need, alloc=True, rcvd=True)
                    COV_FC_RX.sample(decision="malformed", cls=CLASSES[c])
                elif self.credits is not None:
                    COV_FC_RX.sample(decision="malformed_no_hdr")
                if len(tlp) < 4:
                    cause = "no_header"
                elif len(tlp) % 4:
                    cause = "not_whole_dw"
                elif len(tlp) < 12:
                    cause = "short"
                elif len(tlp) != ptn.tlp_size(tlp):
                    cause = "length"
                else:
                    cause = "payload"
                COV_MALFORMED.sample(cause=cause)
                out = "malformed"
            else:
                if self.credits is not None:
                    c, need = CLASSES.index(ptn.has_class(tlp)), ptn.tlp_credits(tlp)
                    adv = self.credits.adv[CLASSES[c]]
                    COV_FC_RX.sample(field="infinite" if 0 in adv else "finite")
                    if not self.credits.rx_fits(c, need):
                        self.overflow += 1
                        COV_FC_RX.sample(decision="overflow", cls=CLASSES[c])
                        return "overflow"
                    self.credits.rx_charge(c, need, alloc=False, rcvd=True)
                    COV_FC_RX.sample(decision="stored", cls=CLASSES[c])
                self.empty_at_store.append(delivered == len(self.expect))
                self.expect.append(tlp)
                self.store_cycle.append(eop)
                out = "stored"
            self.accepts.append((eop, seq, active))
            self.nrs = (self.nrs + 1) % 4096
            self.nak_sched = False
            return out
        if d < 2048:
            return "ahead"
        self.dups.append(eop)
        return "duplicate"


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


class Drop:
    """Recovery on a line: a packet that starts while ``ok`` is false is
    lost whole; one already started is delivered."""

    def __init__(self) -> None:
        self.drop = False

    def pass_beat(self, beat: ptn.Beat | None, ok: bool) -> ptn.Beat | None:
        if beat is None:
            return None
        if beat.sop:
            self.drop = not ok
        return None if self.drop else beat


class Env:
    """The DUT, the phy stand-in, the far end and the shadow."""

    def __init__(self, dut, seed: int, *, partner: ptn.Partner | None = None,
                 ready_p: float = 1.0, gap_p: float = 0.0, delay: int = 22,
                 rx_ready_p: float = 1.0, tlp_valid_p: float = 1.0,
                 retrain_cycles: int | None = None) -> None:
        self.dut = dut
        self.rx_ready_p = rx_ready_p           # upper layer takes a beat (stalls below 1)
        self.rx_stall_until = 0                # upper layer stalls until this cycle
        self.ref = RxRef()
        self.ref_q: deque[tuple[int, int, bytes, bool, bool, bool]] = deque()
        self.rx_gaps: dict[int, bool] = {}
        self.up_beats: list[int] = []          # the TLP being delivered upward
        self.up_sop: list[int] = []            # cycle of each delivered sop
        self.delivered: list[bytes] = []
        self.up_last_eop = -10
        self.acknaks: list[tuple[int, int, str, int]] = []  # (sop cycle, offer cycle, kind, seq)
        self.bad_tlp_seen = 0
        self.malformed_seen = 0
        self.rx_outcomes: list[tuple[int, str]] = []   # (eop cycle, reference outcome)
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
        # TLP transmit (#212): the upper driver, the transmit order model
        # and the retrain stand-in.
        self.tlp_q: deque[bytes] = deque()     # upper TLPs still to offer
        self.tlp_cur: bytes | None = None      # the TLP being offered
        self.tlp_pos = 0                       # its next DW
        self.tlp_gap = False                   # valid dropped inside it
        self.tlp_valid_p = tlp_valid_p
        self.tlp_drive = False                 # a beat is driven this cycle
        self.tlp_eop_cycle = -10               # cycle the last upper eop was taken
        self.accepted: list[bytes] = []        # TLPs taken since the last link-up; seq = index mod 4096
        self.acc_eop: list[int] = []           # the edge each one's eop was taken
        self.seq_tlp: dict[int, bytes] = {}
        self.seq_idx: dict[int, int] = {}      # seq -> latest accepted index
        self.tlp_sent: list[tuple[int, int, bool]] = []   # (take edge, seq, replay)
        self.first_sop: dict[int, int] = {}    # accepted index -> edge its sop was taken by phy
        self.tx_tlp_take: tuple | None = None  # (take edge, seq, replay, TLP) on the stream
        self.tlp_packets: list[tuple[int, bytes]] = []   # (sop cycle, bytes) of every TLP sent
        self.tx_tlp_end: tuple[int, int] = (-1, 0)        # (cycle, seq) of the last TLP's eop
        self.tx_tlp_beats = 0                  # TLP beats the phy took
        self.m_nts = 0                         # model NEXT_TRANSMIT_SEQ
        self.m_ackd = SEQ_MOD - 1              # model ACKD_SEQ
        self.m_run: int | None = None          # last seq of the replay run being sent
        self.m_last_replay = False             # the last TLP taken was a replay
        self.m_owed = False                    # a Nak requested a replay not yet started
        self.live_prev = False                 # DL_Active with the link up, last cycle
        self.acknak_due: deque[tuple[int, str, int]] = deque()   # (DUT cycle, kind, seq)
        self.acknak_log: list[tuple[int, str, int, str]] = []    # (cycle, kind, seq, effect)
        self.last_acknak = -10                 # cycle the last Ack or Nak reached the DUT
        self.proto_expected = 0
        self.replay_runs = 0
        self.err_counts = [0] * 7
        self.err_cycles: dict[int, list[int]] = {ERR_TIMEOUT: [], ERR_ROLLOVER: [],
                                                 ERR_PROTOCOL: []}
        self.retrain_cycles = retrain_cycles   # None: Recovery is not modelled
        self.recovery_left = 0
        self.l0 = True                         # ltssm_state_i is L0 (with dut_link)
        self.hold_tx_in_recovery = True        # the stand-in takes no new packet outside L0
        self.ready_at: dict[int, int] = {}     # cycle -> ready between packets, set by a test
        self.retrains: list[int] = []
        self.drop_tx_tlps = False              # DUT TLPs that start now are lost
        self.tx_drop = False                   # the DUT packet on the line is lost
        self.drop_dut_in = Drop()
        self.lost_tx_tlps = 0
        self.xcheck: list[str] = []            # RTL and arch shadow disagreements
        self.shadow_offers = 0
        self.shadow_pending: deque[tuple[int, int, bytes]] = deque()   # (cycle, index, TLP)
        # Credit gate (#214): checks and transmit credits consumed here.
        self.gate_checks = 0
        self.refused = 0                       # cycles a sop waited on the closed gate
        self.refusing = False
        self.refuse_since = 0
        self.tx_used = [[0, 0] for _ in CLASSES]   # CREDITS_CONSUMED, from taken sops
        self.gate_rng = random.Random(seed ^ 0x214)   # idle headers; the stimulus rng is untouched
        self.lim_snap: list = []               # (limits, infinite) at the start of the cycle
        self.ovf_seen = 0                      # Receiver overflow pulses
        # Per received TLP, in order: (cycle, verdict class, (class, credits),
        # free credits of that class just before) by the reference (on the
        # DUT's counters) and by the arch shadow (on its own).
        self.ref_verdicts: list = []
        self.sh_verdicts: list = []
        self.epochs: list = []                 # (cycle, DUT and shadow deliveries) at each link-down
        self.up_ready_now = 1                  # the upper layer's ready this cycle
        # Receive counters by the flow-control page (FLOW_CONTROL.md),
        # kept by the receive reference.
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
        # Credit release (FC_RELEASE 1): the releaser's queues per class
        # (delivery cycle, data credits), the finite credits held, and the
        # releases the arch shadow takes this cycle.
        self.rel = False
        self.rel_q: dict[int, deque[tuple[int, int]]] = {0: deque(), 1: deque()}
        self.held = [[0, 0], [0, 0]]
        self.held_max = [[0, 0], [0, 0]]
        self.rel_delay = 2                     # cycles after delivery, at least 2
        self.rel_p = 0.5                       # release probability per class and cycle
        self.rel_stall_until = 0               # no release before this cycle
        self.released = [0, 0]                 # releases driven, per class
        self.released_init2 = 0                # of them, in FC_INIT2
        self.rel_shadow: list[tuple[int, int]] = []
        self.rel_rng = random.Random(seed ^ 0x303)   # the stimulus rng is untouched

    # ------------------------------------------------------------ set-up

    async def start(self) -> None:
        d = self.dut
        cocotb.start_soon(Clock(d.clk_i, 8, unit="ns").start())
        for name in ("tlp_tx_valid_i", "tlp_tx_data_i", "tlp_tx_sop_i", "tlp_tx_eop_i",
                     "pkt_tx_ready_i", "pkt_rx_valid_i", "pkt_rx_data_i", "pkt_rx_sop_i",
                     "pkt_rx_eop_i", "pkt_rx_dllp_i", "pkt_rx_err_i", "pkt_rx_nullify_i",
                     "link_up_i", "ltssm_state_i", "fc_rel_p_i", "fc_rel_pd_i", "fc_rel_np_i",
                     "fc_rel_npd_i"):
            getattr(d, name).value = 0
        # The DUT's upper receive stream never stalls (HAS, receive).
        d.tlp_rx_ready_i.value = 1
        d.rst_i.value = 1
        for _ in range(3):
            await FallingEdge(d.clk_i)
        d.rst_i.value = 0
        # The DUT's credit parameters, from the testbench's cfg_fc_o.
        self.rel = bool(int(d.cfg_fc_o.value) & 1)
        cfg = int(d.cfg_fc_o.value) >> 20
        fields = []
        for w in (12, 8, 12, 8, 12, 8):         # FC_CPLD up to FC_PH
            fields.append(cfg & ((1 << w) - 1))
            cfg >>= w
        cpld, cplh, npd, nph, pd, ph = fields
        self.adv = {"P": (ph, pd), "NP": (nph, npd), "Cpl": (cplh, cpld)}
        self.shadow = arch.Dll("shadow", {"ph": ph, "pd": pd, "nph": nph, "npd": npd,
                                          "cplh": cplh, "cpld": cpld}, release_port=self.rel)
        self._rx_reset()
        self.ref.credits = self

    def _rx_reset(self) -> None:
        """The receive counters and UpdateFC record on entering DL_Inactive."""
        for c, cls in enumerate(CLASSES):
            self.rx_alloc[c] = [self.adv[cls][f] or RESERVE[f] for f in (0, 1)]
            self.rx_rcvd[c] = [0, 0]
            self.tx_used[c] = [0, 0]
            self.upd_hist[c] = [(self.cycle, self.adv[cls])]
            self.upd_sent[c] = self.adv[cls]
        self.upd_offer = {}
        self.upd_rr = 0
        self.upd_pending = {}
        # The upper layer is reset with DL_Up: it releases nothing it held.
        for q in self.rel_q.values():
            q.clear()
        self.held = [[0, 0], [0, 0]]

    def _tlp_need(self, data: bytes):
        """(class, credits) of a framed TLP packet's header, or None."""
        tlp = data[2:-4]
        if len(tlp) < 4:
            return None
        return CLASSES.index(ptn.has_class(tlp)), ptn.tlp_credits(tlp)

    def rx_fits(self, c: int, need: tuple[int, int]) -> bool:
        """The overflow check (FLOW_CONTROL.md, receive buffer)."""
        return all(need[f] <= (self.rx_alloc[c][f] - self.rx_rcvd[c][f]) % MODS[f]
                   for f in (0, 1))

    def rx_charge(self, c: int, need: tuple[int, int], *, alloc: bool, rcvd: bool) -> None:
        for f in (0, 1):
            if alloc:
                self.rx_alloc[c][f] = (self.rx_alloc[c][f] + need[f]) % MODS[f]
            if rcvd:
                self.rx_rcvd[c][f] = (self.rx_rcvd[c][f] + need[f]) % MODS[f]

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
               gaps: int = 0, nullify: bool = False) -> None:
        """Queue a raw packet towards the DUT (hand-driven far end), with
        ``gaps`` idle cycles after each beat but the last; ``nullify`` marks
        its eop as ended by EDB."""
        beats = ptn.to_beats(pkt, dllp, nullify)
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
            if err & ERR_BAD_TLP:
                self.bad_tlp_seen += 1
            if err & ERR_MALFORMED:
                self.malformed_seen += 1
            for b in range(7):
                if err >> b & 1:
                    self.err_counts[b] += 1
                    if b in self.err_cycles:
                        self.err_cycles[b].append(n)
            if err >> ERR_TIMEOUT & 1:
                COV_REPLAY.sample(cause="timeout")
            if err & ~(ERR_ALLOWED | ERR_TX):
                self.violations.append(f"cycle {n}: err_o {err:#04x}: a bit the DUT does not "
                                       f"drive yet")
            if err & ERR_OVERFLOW:
                self.ovf_seen += 1
        self._retrain_check(n, err)
        if state != self.state and self.state == ACTIVE:
            self._tlp_link_reset()
        if state != self.state:
            COV_STATES.sample(arc=(self.state, state))
            self.states.append((n, state))
            if state == ACTIVE:
                self.upd_entry = {0, 1, 2}
                self.upd_rr = 0
                self.active_since = n
            if state == INACTIVE:
                self._rx_reset()
                self.epochs.append((n, len(self.delivered), len(self.shadow.delivered)))
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
        nts_before = self.m_nts
        # The DUT judged last cycle's TLP eops in this cycle's state.
        while self.ref_q and self.ref_q[0][0] <= n:
            _, eop, data, err_b, null_b, gap = self.ref_q.popleft()
            live = state in (INIT2, ACTIVE) and self.dut_link
            cn = self._tlp_need(data) if len(data) >= 6 else None
            free = (None if cn is None else
                    tuple((self.rx_alloc[cn[0]][f] - self.rx_rcvd[cn[0]][f]) % MODS[f]
                          for f in (0, 1)))
            out = self.ref.judge(n, eop, data, err_b, null_b, live, state == ACTIVE,
                                 len(self.delivered))
            self.ref_verdicts.append((n, OUTCOME_CLASS.get(out, out), cn, free))
            self.rx_outcomes.append((eop, out))
            kinds = {"cls": tlp_cls(data[2:-4])} if out == "stored" else {}
            COV_TLP_RX.sample(outcome=out, gap="gap_inside" if gap else "no_gap", **kinds)
        if state == INACTIVE:
            # Everything received is cleared, a TLP being delivered abandoned.
            if self.up_beats:
                self.up_beats = []
                self.up_sop.pop()
            self.ref.reset(n, len(self.delivered))
        self._upper(n)
        self._release(n, state)

        # The credit limits as the DUT's registers hold them this cycle,
        # before the shadow takes this cycle's received packets.
        self.lim_snap = ([list(x) for x in self.shadow.cl], [list(x) for x in self.shadow.inf])

        # phy stand-in: transmit ready, the line towards the far end.
        if self.in_pkt:
            ready = 1
        elif self.framing or (not self.l0 and self.hold_tx_in_recovery):
            ready = 0
        elif n in self.ready_at:
            ready = self.ready_at[n]           # a test sets ready in this cycle
        else:
            ready = int(self.rng.random() < self.ready_p)
        self.framing = False
        d.pkt_tx_ready_i.value = ready
        to_far = None
        if valid:
            beat = ptn.Beat(int(d.pkt_tx_data_o.value), bool(d.pkt_tx_sop_o.value),
                            bool(d.pkt_tx_eop_o.value), bool(d.pkt_tx_dllp_o.value))
            if beat.sop and not self.in_pkt and not self.tx_beats:
                self.tx_state = state
                if self.tx_offer is None:
                    self.tx_offer = n           # the arbiter took it at the edge before
                    if not beat.dllp:
                        self._tx_take(n, ((beat.data & 0x0F) << 8) | (beat.data >> 8))
            if beat.sop and not ready:
                self.tx_sop_wait += 1
            if ready:
                if beat.sop:
                    # A packet that starts in Recovery, or a TLP in a loss
                    # window, never reaches the far end.
                    self.tx_drop = not self.l0 or (self.drop_tx_tlps and not beat.dllp)
                    if self.tx_drop and not beat.dllp:
                        self.lost_tx_tlps += 1
                to_far = None if self.tx_drop else beat
                self.tx_tlp_beats += not beat.dllp
                if (beat.sop or self.in_pkt) and not self.dut_link:   # same edge samples link
                    self.tail_beats += 1
                self._tx_beat(n, beat)
                if beat.eop:
                    if self.tail_beats:
                        COV_TAIL.sample(beats_after_link_down=self.tail_beats)
                        self.tails.append((n, self.tail_beats))
                    self.tail_beats = 0
        # Acks and Naks the DUT acted on in the cycle before this edge's
        # take (both on that cycle's state; the range check before the take).
        self._acknak_apply(n - 1, nts_before)
        self.live_prev = self.state == ACTIVE and self.dut_link
        # far end
        if self.partner is not None:
            p = self.partner
            p.link_up = self.partner_link
            p.in_l0 = self.partner_link and self.l0
            p.tx_ready = not (self.gap_p and self.rng.random() < self.gap_p)
            far_out = p.clock(self.gate_far.pass_beat(self.to_far.push(to_far),
                                                      self.partner_link))
            ps = PARTNER_STATE[p.state]
            if ps != self.partner_states[-1][1]:
                self.partner_states.append((n, ps))
            in_beat = self.drop_dut_in.pass_beat(
                self.gate_dut.pass_beat(self.to_dut.push(far_out), self.dut_link), self.l0)
        else:
            self.to_far.push(to_far)
            in_beat = self.raw.popleft() if self.raw else None
        self._drive_rx(in_beat)
        d.link_up_i.value = int(self.dut_link)
        d.ltssm_state_i.value = (L0_CODE if self.l0 else RECOVERY_CODE) if self.dut_link else 0
        self._tlp_drive_beat(n, state)

        # shadow: the DUT samples these inputs at the next edge; a received
        # packet reaches it one cycle later, as the DUT's DLLP receive
        # registers it.
        sh = self.shadow
        sh.set_link_up(self.dut_link, n)
        while self.shadow_rx and self.shadow_rx[0][0] <= n:
            pkt = self.shadow_rx.popleft()[1]
            if pkt.dllp:
                sh.receive(pkt, n)
                continue
            # The shadow's verdict on a TLP, from its counters, with its free
            # credits for the TLP's class just before (receive accounting).
            cn = self._tlp_need(pkt.data) if len(pkt.data) >= 6 else None
            free = None
            if cn is not None:
                c = cn[0]
                free = tuple((sh.ca[c][f] - sh.cr[c][f]) % MODS[f] if self.adv[CLASSES[c]][f]
                             else RESERVE[f] - sh.occ[c][f] for f in (0, 1))
            before = (sh.errors[arch.E_BAD_TLP], sh.errors[arch.E_MALFORMED],
                      sh.errors[arch.E_OVERFLOW], sh.counts["duplicates"], sh.nrs)
            sh.receive(pkt, n)
            after = (sh.errors[arch.E_BAD_TLP], sh.errors[arch.E_MALFORMED],
                     sh.errors[arch.E_OVERFLOW], sh.counts["duplicates"], sh.nrs)
            out = ("bad" if after[0] > before[0] else "malformed" if after[1] > before[1]
                   else "overflow" if after[2] > before[2] else "duplicate"
                   if after[3] > before[3] else "stored" if after[4] != before[4] else "none")
            self.sh_verdicts.append((n, out, cn, free))
        if in_beat is not None:
            self._rx_collect(n, in_beat)
        elif self.rx_beats:
            self.rx_gap = True
        if self.shadow_pending:
            self._shadow_offer(n)
        for c, data in self.rel_shadow:
            sh.release(c, data)                 # sampled at the edge, as the DUT does
        self.rel_shadow = []
        sh.tick(n, self.l0, True, bool(self.up_ready_now))
        if sh.state != self.shadow_states[-1][1]:
            self.shadow_states.append((n, sh.state))

        # tlp_tx_ready_o and the credit gate depend on the beat just driven.
        await Timer(1, "ns")
        self._gate_check(n, state)
        if self.tlp_drive:
            self._tlp_ready(n, state)
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

    def _gate_check(self, n: int, state: int) -> None:
        """After the settle: the gate on the first DW driven now (the TLP's
        sop, or the idle random header) against the rule and the arch
        model, with the cycle's limits and CREDITS_CONSUMED counted here."""
        d = self.dut
        head = int(d.tlp_tx_data_i.value).to_bytes(4, "little")
        if self.tlp_cur is not None and self.tlp_pos:
            return                              # not a first DW
        lim, inf = self.lim_snap
        c, need = tlp_need(head)
        fields = gate_rule(lim[c], inf[c], self.tx_used[c], need)
        want = int(state == ACTIVE and self.dut_link and all(fields))
        model = self.shadow.credits_ok
        cc_save = self.shadow.cc[c]
        self.shadow.cc[c] = list(self.tx_used[c])
        cl_save, inf_save = self.shadow.cl[c], self.shadow.inf[c]
        self.shadow.cl[c], self.shadow.inf[c] = lim[c], inf[c]
        arch_ok = int(state == ACTIVE and self.dut_link and model(c, need))
        self.shadow.cc[c], self.shadow.cl[c], self.shadow.inf[c] = cc_save, cl_save, inf_save
        ok = int(d.probe_tx_ok_o.value)
        self.gate_checks += 1
        if state == ACTIVE and self.dut_link and (ok != want or arch_ok != want):
            self.violations.append(
                f"cycle {n}: gate {ok} for {CLASSES[c]} {need} ({head.hex()}), expected "
                f"{want} (arch {arch_ok}); limit {lim[c]} infinite {inf[c]} consumed "
                f"{self.tx_used[c]}")
        if state == ACTIVE:
            if all(inf[c]):
                gate = "infinite"
            elif all(fields):
                gate = "open"
            else:
                gate = {(False, True): "closed_hdr", (True, False): "closed_data"}.get(
                    tuple(fields), "closed_both")
            COV_FC_TX.sample(gate=gate, cls=CLASSES[c])
        if self.tlp_cur is None or not self.tlp_drive:
            return
        ready = int(d.tlp_tx_ready_o.value)
        if ready and not ok:
            self.violations.append(f"cycle {n}: a sop taken with the credit gate closed")
        if not ready and not ok and state == ACTIVE:
            self.refused += 1
            if not self.refusing:
                self.refuse_since = n
            self.refusing = True
            COV_FC_TX.sample(starve="refused")
        elif ready:
            if self.refusing:
                COV_FC_TX.sample(starve="recovered")
            self.refusing = False
            for f in (0, 1):
                old = self.tx_used[c][f]
                self.tx_used[c][f] = (old + need[f]) % MODS[f]
                if self.tx_used[c][f] < old:
                    COV_FC_TX.sample(consumed_wrap=("hdr", "data")[f])

    def _check_counters(self, n: int) -> None:
        """At the start of a cycle: credits available equal the shadow's
        limits less the consumed credits counted here; the UpdateFC value
        record; a returned credit
        is not left unsent."""
        sh = self.shadow
        if self.state == ACTIVE and sh.state == arch.ACTIVE:
            # Limits from the arch shadow, consumed counted here.
            got = self.dut_avail()
            for c, cls in enumerate(CLASSES):
                want = tuple(MODS[f] - 1 if sh.inf[c][f]
                             else (sh.cl[c][f] - self.tx_used[c][f]) % MODS[f] for f in (0, 1))
                if got[cls] != want:
                    self.violations.append(f"cycle {n}: {cls} credits available {got[cls]}, "
                                           f"expected {want}")
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
            self.ref_q.append((n + 1, n, data, b.err, b.nullify, self.rx_gap))
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
            kind, _, vc, seq, _ = arch.decode_dllp(data)
            kinds["kind"] = {"other": "other_type"}.get(kind, kind)
            if kind in ("init1", "init2", "update") and vc:
                kinds["kind"] = "other_vc"
            self.rx_good.append((n, data))
            if kind in ("ack", "nak"):
                # Decoded at the next edge; the TLP transmit block acts on
                # it in that cycle (MAS, Timing).
                self.acknak_due.append((n + 1, kind, seq))
                self.last_acknak = n
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
        if not any(x.dllp for x in beats):
            self._tx_tlp(data)
            return
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
        if kind in ("ack", "nak"):
            self._tx_acknak(where, kind, hdr, st, sop)
            return
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

    # ------------------------------------------------------------ TLP transmit

    def send(self, *tlps: bytes) -> None:
        """Queue TLPs for the upper transmit stream."""
        self.tlp_q.extend(tlps)

    def _retrain_check(self, n: int, err: int) -> None:
        """retrain_req_o pulses with REPLAY_NUM rollover (HAS, errors); the
        stand-in answers it with ``retrain_cycles`` of Recovery."""
        rt = int(self.dut.retrain_req_o.value)
        roll = err >> ERR_ROLLOVER & 1
        if rt != roll:
            self.violations.append(f"cycle {n}: retrain_req_o {rt} with REPLAY_NUM rollover "
                                   f"{roll}")
        if rt:
            self.retrains.append(n)
            # The rollover replay starts now; a TLP taken but not started is
            # withdrawn unseen (MAS, Transmit), so the running replay ends here.
            self.m_run = None
            self.m_owed = False
            self.drop_tx_tlps = False           # a loss window ends at the retrain
            if self.retrain_cycles is not None:
                self.l0 = False
                self.recovery_left = self.retrain_cycles
        elif self.recovery_left:
            self.recovery_left -= 1
            if not self.recovery_left:
                self.l0 = True

    def _tlp_link_reset(self) -> None:
        """DL_Active left: the DUT clears its sequence numbers and replay
        buffer and abandons a TLP being taken; so does the upper driver."""
        self.tlp_cur = None
        self.accepted, self.acc_eop = [], []
        self.seq_tlp, self.seq_idx = {}, {}
        self.m_nts, self.m_ackd, self.m_run = 0, SEQ_MOD - 1, None
        self.m_owed = False
        self.acknak_due.clear()
        self.shadow_pending.clear()

    def _tlp_drive_beat(self, n: int, state: int) -> None:
        """Drive this cycle's upper transmit beat; valid may drop between
        the beats of a TLP."""
        d = self.dut
        if self.tlp_cur is None and self.tlp_q:
            self.tlp_cur, self.tlp_pos, self.tlp_gap = self.tlp_q.popleft(), 0, False
        if self.tlp_cur is None:
            self.tlp_drive = False
            # An idle random header: the credit gate is checked on it too.
            d.tlp_tx_data_i.value = int.from_bytes(random_header(self.gate_rng), "little")
            d.tlp_tx_valid_i.value = 0
            d.tlp_tx_sop_i.value = 0
            d.tlp_tx_eop_i.value = 0
            return
        i = self.tlp_pos
        valid = i == 0 or self.rng.random() < self.tlp_valid_p
        self.tlp_gap |= not valid
        d.tlp_tx_valid_i.value = int(valid)
        d.tlp_tx_data_i.value = int.from_bytes(self.tlp_cur[4 * i:4 * i + 4], "little")
        d.tlp_tx_sop_i.value = int(i == 0)
        d.tlp_tx_eop_i.value = int(i == len(self.tlp_cur) // 4 - 1)
        self.tlp_drive = valid

    def _tlp_ready(self, n: int, state: int) -> None:
        """The beat driven this cycle is taken at the next edge when
        tlp_tx_ready_o is 1 (HAS, transmit)."""
        d, tlp = self.dut, self.tlp_cur
        ready = int(d.tlp_tx_ready_o.value)
        if ready and state != ACTIVE:
            self.violations.append(f"cycle {n}: tlp_tx_ready_o 1 in {STATE_NAMES[state]}")
        if self.tlp_pos and not ready and state == ACTIVE:
            self.violations.append(f"cycle {n}: tlp_tx_ready_o 0 inside a TLP")
        if self.tlp_pos == 0:
            if ready:
                idx = len(self.accepted)
                self.accepted.append(tlp)
                self.seq_tlp[idx % SEQ_MOD] = tlp
                self.seq_idx[idx % SEQ_MOD] = idx
                if self.shadow is not None and self.dut_link:
                    self.shadow_pending.append((n, idx, tlp))
            elif state == ACTIVE and self.dut_link:
                self._backpressure_xcheck(n, tlp)
        if ready:
            self.tlp_pos += 1
            if self.tlp_pos == len(tlp) // 4:
                self.acc_eop.append(n + 1)
                self.tlp_eop_cycle = n
                COV_TLP_TX.sample(upper_gap="gap_inside" if self.tlp_gap else "no_gap")
                self.tlp_cur = None

    def _shadow_offer(self, n: int) -> None:
        """Each TLP the DUT takes goes to the arch shadow's upper stream. The
        shadow sends at its own pace (no phy stalls), so its space can lag
        the DUT's by a packet: a refusal is a disagreement only when it lasts
        longer than a maximum TLP's time on the line (200 cycles)."""
        sh = self.shadow
        while self.shadow_pending:
            c, idx, tlp = self.shadow_pending[0]
            if sh.offer(tlp, n):
                self.shadow_offers += 1
                self.shadow_pending.popleft()
            else:
                if n - c == 200:
                    self.xcheck.append(f"cycle {c}: the DUT takes TLP {idx}, the arch shadow "
                                       f"refuses it for 200 cycles (replay buffer "
                                       f"{sh.rbuf_bytes()} bytes)")
                break

    def _backpressure_xcheck(self, n: int, tlp: bytes) -> None:
        """A sop the DUT refuses: the arch shadow must refuse it too, unless
        the DUT is writing the last TLP's LCRC, an Ack is still on its way
        (the shadow acts on it one cycle earlier), the shadow is behind, or
        the DUT is sending a TLP an Ack removed (its space returns at its
        eop, which the shadow reaches at its own pace)."""
        sh = self.shadow
        if sh is None or n - self.tlp_eop_cycle <= 1 or n - self.last_acknak <= 3:
            return
        # A refusal by the credit gate is judged by the gate check against
        # the DUT's own limit timing (the shadow records an UpdateFC a cycle
        # earlier); this cross-check covers the replay-buffer conditions.
        if not int(self.dut.probe_tx_ok_o.value):
            return
        if self.shadow_pending:
            return
        # The TLP on the stream, or the one whose eop the phy takes now.
        take = self.tx_tlp_take or (self.tx_tlp_end if self.tx_tlp_end[0] == n else None)
        if take is not None and self._pos(take[1]) >= 2048:
            return
        cls, h, dc, size = arch.tlp_info(tlp)
        if (sh.state == arch.ACTIVE and not sh.src_busy and sh.credits_ok(cls, (h, dc))
                and sh.rbuf_bytes() + size + 6 <= arch.REPLAY_BUF_BYTES):
            self.xcheck.append(f"cycle {n}: the DUT refuses a {size}-byte TLP, the arch "
                               f"shadow takes it (replay buffer {sh.rbuf_bytes()} bytes)")

    def _pos(self, s: int) -> int:
        """Position of sequence number ``s`` among the TLPs held: 0 is the
        oldest unacknowledged."""
        return (s - self.m_ackd - 1) % SEQ_MOD

    def _tx_take(self, n: int, s: int) -> None:
        """The arbiter took TLP ``s`` at edge ``n``, on the state of the
        cycle before (Acks and Naks acted on up to then). New TLPs go in
        order; a replay runs from the oldest unacknowledged TLP, in order,
        skipping what Acks removed, and completes before any new TLP
        (RETRY.md, replay)."""
        tlp = self.seq_tlp.get(s)
        where = f"cycle {n}: TLP {s} taken"
        window = self._pos(self.m_nts)         # TLPs sent and not acknowledged
        prev = self.m_run
        nxt = None                             # the next TLP the running replay owes
        if prev is not None:
            p = (prev - self.m_ackd) % SEQ_MOD
            nxt = (self.m_ackd + 1 + p) % SEQ_MOD if p < 2048 and p < window else None
            if p >= 2048 and window:
                nxt = (self.m_ackd + 1) % SEQ_MOD
        replay = False
        if tlp is None:
            self.violations.append(f"{where}: no such TLP was taken from the upper stream")
        elif s == self.m_nts:
            if nxt is not None:
                self.violations.append(f"{where}: a new TLP before the replay sent TLP {nxt}")
            if self.m_owed and window:
                self.violations.append(f"{where}: a new TLP before the replay a Nak requested")
            self.m_nts = (self.m_nts + 1) % SEQ_MOD
            self.m_run = None
        elif self._pos(s) < window:
            replay = True
            if nxt is not None and s == nxt:
                if prev is not None and s != (prev + 1) % SEQ_MOD:
                    COV_REPLAY.sample(run="skipped_by_ack")
            elif s == (self.m_ackd + 1) % SEQ_MOD and nxt is None:
                self.replay_runs += 1
                self.m_owed = False
                if self.m_last_replay:
                    COV_REPLAY.sample(run="pending_after")
            else:
                want = nxt if nxt is not None else (self.m_ackd + 1) % SEQ_MOD
                self.violations.append(f"{where}: replay out of order, expected TLP {want} "
                                       f"(ACKD_SEQ {self.m_ackd}, NTS {self.m_nts})")
            self.m_run = s
            if self._pos(s) == window - 1:
                COV_REPLAY.sample(run="complete")
        elif self._pos(s) >= 2048 or s == self.m_ackd:
            self.violations.append(f"{where}: already acknowledged (ACKD_SEQ {self.m_ackd})")
        else:
            self.violations.append(f"{where}: ahead of NEXT_TRANSMIT_SEQ {self.m_nts}")
        self.m_last_replay = replay
        self.tx_tlp_take = (n, s, replay, tlp)
        self.tlp_sent.append((n, s, replay))

    def _acknak_apply(self, n: int, nts: int) -> None:
        """Received Acks and Naks the DUT acts on in cycle ``n`` (RETRY.md,
        received Ack and Nak): the range check against NEXT_TRANSMIT_SEQ
        ``nts`` of that cycle, forward progress, a Nak's replay. Only in
        DL_Active with the link up."""
        while self.acknak_due and self.acknak_due[0][0] <= n:
            _, kind, seq = self.acknak_due.popleft()
            if not self.live_prev:
                continue
            last = (nts - 1) % SEQ_MOD
            if (last - seq) % SEQ_MOD > (last - self.m_ackd) % SEQ_MOD:
                effect = "out_of_range"
                self.proto_expected += 1
            elif seq == self.m_ackd:
                effect = "no_progress"
            else:
                effect = "progress_one" if self._pos(seq) == 0 else "progress_many"
                self.m_ackd = seq
            if kind == "nak" and effect != "out_of_range" and self._pos(nts):
                self.m_owed = True
                COV_REPLAY.sample(cause="nak")
            elif not self._pos(nts):
                self.m_owed = False             # nothing sent is unacknowledged: no replay
            COV_ACKNAK_RX.sample(kind=kind, effect=effect)
            self.acknak_log.append((n, kind, seq, effect))

    def _tx_tlp(self, data: bytes) -> None:
        """Check one TLP the DUT sent: framing, the TLP taken with its
        sequence number, and the LCRC of both models and zlib (RETRY.md)."""
        sop = self.tx_sop_cycle
        take, self.tx_tlp_take = self.tx_tlp_take, None
        if take is not None:
            self.tx_tlp_end = (self.cycle, take[1])
        self.tlp_packets.append((sop, data))
        where = f"cycle {sop}: TLP packet {data[:6].hex()}.. ({len(data)} bytes)"
        if take is None:
            self.violations.append(f"{where}: sent without a take")
            return
        _, s, replay, tlp = take
        if len(data) < 18 or len(data) % 4 != 2:
            self.violations.append(f"{where}: not 2 bytes, whole DWs and 4 bytes")
            return
        if data[0] & 0xF0 or ((data[0] & 0x0F) << 8 | data[1]) != s:
            self.violations.append(f"{where}: sequence bytes {data[:2].hex()}, expected {s}")
        crc_a = arch_crc.lcrc(data[:-4])
        crc_p = ptn_crc.lcrc_bytes(data[:-4])
        crc_z = zlib.crc32(data[:-4]).to_bytes(4, "little")
        if not data[-4:] == crc_a == crc_p == crc_z:
            self.violations.append(f"{where}: LCRC {data[-4:].hex()}, arch {crc_a.hex()}, "
                                   f"partner {crc_p.hex()}, zlib {crc_z.hex()}")
        if tlp is not None and data[2:-4] != tlp:
            at = next(i for i, (x, y) in enumerate(zip(data[2:-4] + bytes(1), tlp)) if x != y)
            self.violations.append(f"{where}, {'replay' if replay else 'new'}: TLP byte {at} "
                                   f"{data[2 + at:10 + at].hex()}.., taken as "
                                   f"{tlp[at:at + 8].hex()}..")
        idx = self.seq_idx.get(s)
        if not replay and idx is not None:
            self.first_sop[idx] = sop + 1
        if tlp is not None:
            payload = len(tlp) // 4 - (4 if tlp[0] & 0x20 else 3)
            COV_TLP_TX.sample(hdr="4dw" if tlp[0] & 0x20 else "3dw", payload=payload,
                              send="replay" if replay else "new")
            if s == 0 and idx is not None and idx >= SEQ_MOD and not replay:
                COV_TLP_TX.sample(seq_wrap="from_4095")

    def tx_latency(self) -> list[int]:
        """Edges from each TLP's upper eop taken to its sop taken by phy."""
        return [self.first_sop[i] - e for i, e in enumerate(self.acc_eop) if i in self.first_sop]

    def check_tlp_delivered(self, partner: ptn.Partner, sent: list[bytes]) -> None:
        """Every TLP delivered to the far end's upper layer exactly once, in
        order."""
        got = partner.received
        assert len(got) == len(sent), f"{len(got)} TLPs delivered, {len(sent)} sent"
        for i, (a, b) in enumerate(zip(got, sent)):
            assert a == b, f"TLP {i} delivered as {a[:8].hex()}.., sent {b[:8].hex()}.."

    def check_shadow_tx(self) -> dict[str, tuple[int, int]]:
        """The DUT's replay events against the arch shadow's: replay
        timeouts, rollovers, DL protocol errors and replays. Returns the
        pairs (DUT, shadow)."""
        sh = self.shadow
        pairs = {
            "replay_timeout": (self.err_counts[ERR_TIMEOUT], sh.errors[arch.E_TIMEOUT]),
            "rollover": (self.err_counts[ERR_ROLLOVER], sh.errors[arch.E_ROLLOVER]),
            "dl_protocol": (self.err_counts[ERR_PROTOCOL], sh.errors[arch.E_PROTOCOL]),
            "replays": (self.replay_runs, sh.counts["replays"]),
        }
        for k, (a, b) in pairs.items():
            if a != b:
                self.xcheck.append(f"{k}: DUT {a}, arch shadow {b}")
        return pairs

    def _tx_acknak(self, where: str, kind: str, seq: int, st: int, sop: int) -> None:
        """An Ack or Nak: only in DL_Active, with NEXT_RCV_SEQ - 1 as the
        reference had it when the arbiter took the request (the cycle
        before the sop was first offered, from the judgements made up to
        the cycle before that)."""
        offer = self.tx_sop_offer
        if st != ACTIVE:
            self.violations.append(f"{where}: {kind} outside DL_Active")
        nrs = [v for j, v in self.ref.hist if j <= offer - 2][-1]
        if seq != (nrs - 1) % 4096:
            self.violations.append(f"{where}: {kind} {seq}, expected NEXT_RCV_SEQ - 1 = "
                                   f"{(nrs - 1) % 4096}")
        cause = "nak" if kind == "nak" else (
            "duplicate" if any(offer - 8 <= c <= offer for c in self.ref.dups) else "timer")
        COV_ACKNAK.sample(kind=kind, cause=cause)
        self.acknaks.append((sop, offer, kind, seq))
        self.sent.append((sop, kind, 0, b""))
        self.offers.append(offer)

    def _upper(self, n: int) -> None:
        """The upper layer: decide tlp_rx_ready_i for the next edge and
        take the beat offered now if it is 1."""
        d = self.dut
        stall = n < self.rx_stall_until or (self.rx_ready_p < 1.0
                                            and self.rng.random() >= self.rx_ready_p)
        if stall and any(v == 0 for c in CLASSES for v in self.adv.get(c, (1, 1))):
            raise AssertionError("the upper layer may stall only with every class finite "
                                 "(HAS, receive)")
        ready = int(not stall)
        self.up_ready_now = ready
        d.tlp_rx_ready_i.value = ready
        if not int(d.tlp_rx_valid_o.value):
            return
        COV_RX_STREAM.sample(beat="taken" if ready else "stalled")
        if not ready:
            return
        data = int(d.tlp_rx_data_o.value)
        sop, eop = int(d.tlp_rx_sop_o.value), int(d.tlp_rx_eop_o.value)
        if sop:
            if self.up_beats:
                self.violations.append(f"cycle {n}: upper sop inside a TLP")
            self.up_beats = []
            self.up_sop.append(n)
            COV_RX_STREAM.sample(sop="back_to_back" if self.up_last_eop == n - 1
                                 else "after_idle")
        elif not self.up_beats:
            self.violations.append(f"cycle {n}: upper beat outside a TLP")
            return
        self.up_beats.append(data)
        if eop:
            self.up_last_eop = n
            tlp = b"".join(x.to_bytes(4, "little") for x in self.up_beats)
            self.up_beats = []
            i = len(self.delivered)
            self.delivered.append(tlp)
            # Its credits return as its eop is taken (FLOW_CONTROL.md); with
            # FC_RELEASE 1 a posted or non-posted TLP returns only its
            # infinite fields (the reserve) now, its finite ones on release.
            c = CLASSES.index(ptn.has_class(tlp))
            need = ptn.tlp_credits(tlp)
            if self.rel and c < 2:
                adv = self.adv[CLASSES[c]]
                self.rx_charge(c, tuple(0 if adv[f] else need[f] for f in (0, 1)),
                               alloc=True, rcvd=False)
                self.rel_q[c].append((n, need[1]))
                for f in (0, 1):
                    if adv[f]:
                        self.held[c][f] += need[f]
                        self.held_max[c][f] = max(self.held_max[c][f], self.held[c][f])
                        if self.held[c][f] > adv[f]:
                            self.violations.append(
                                f"cycle {n}: the upper layer holds {self.held[c][f]} "
                                f"{CLASSES[c]} {('header', 'data')[f]} credits, above the "
                                f"{adv[f]} advertised")
                if adv[0] and self.held[c][0] == adv[0]:
                    COV_FC_RX.sample(held_all=CLASSES[c])
            else:
                self.rx_charge(c, need, alloc=True, rcvd=False)
                COV_FC_RX.sample(returned=CLASSES[c])
            if i >= len(self.ref.expect):
                self.violations.append(f"cycle {n}: TLP {tlp[:8].hex()} delivered, none expected")
            elif tlp != self.ref.expect[i]:
                self.violations.append(f"cycle {n}: TLP {i} delivered {tlp[:16].hex()}..., "
                                       f"expected {self.ref.expect[i][:16].hex()}...")

    def _release(self, n: int, state: int) -> None:
        """The releaser (FC_RELEASE 1): drive fc_rel_*_i for the next edge,
        at most one posted and one non-posted release, each for the oldest
        TLP of its class held at least ``rel_delay`` cycles; its finite
        credits return to CREDITS_ALLOCATED at that edge. Only while DL_Up
        holds at that edge (a release in a flushed cycle is lost, and the
        upper layer is reset then)."""
        if not self.rel:
            return
        d = self.dut
        live = state in (INIT2, ACTIVE) and self.dut_link
        for c, (valid, data) in enumerate(((d.fc_rel_p_i, d.fc_rel_pd_i),
                                           (d.fc_rel_np_i, d.fc_rel_npd_i))):
            q = self.rel_q[c]
            go = (live and q and q[0][0] + self.rel_delay <= n and n >= self.rel_stall_until
                  and self.rel_rng.random() < self.rel_p)
            valid.value = int(bool(go))
            if not go:
                data.value = self.rel_rng.getrandbits(4) & 7     # don't care without valid
                continue
            v = q.popleft()[1]
            data.value = v
            adv = self.adv[CLASSES[c]]
            self.rx_charge(c, tuple(x if adv[f] else 0 for f, x in enumerate((1, v))),
                           alloc=True, rcvd=False)
            for f, x in enumerate((1, v)):
                if adv[f]:
                    self.held[c][f] -= x
            self.released[c] += 1
            self.released_init2 += state == INIT2
            self.rel_shadow.append((c, v))
            COV_FC_RX.sample(released=CLASSES[c], release_data=v,
                             release_state="fc_init2" if state == INIT2 else "dl_active")

    # ------------------------------------------------------------ checks

    def check_rx(self, *, ack_bound: int | None = None) -> dict[str, int]:
        """After a drain: every expected TLP delivered and nothing else, the
        Bad TLP and Malformed TLP pulses and the Naks as the reference
        counts them, every TLP accepted in DL_Active covered by an Ack or
        Nak within ``ack_bound`` cycles of its eop (when given), and the
        receive latency of a TLP stored into an empty buffer within the
        HAS's 4 cycles when the upper layer never stalls. Returns the worst
        latencies."""
        ref = self.ref
        assert len(self.delivered) == len(ref.expect), (
            f"{len(self.delivered)} TLPs delivered, {len(ref.expect)} expected")
        assert self.bad_tlp_seen == ref.bad, (
            f"Bad TLP pulses {self.bad_tlp_seen}, expected {ref.bad}")
        assert self.malformed_seen == ref.malformed, (
            f"Malformed TLP pulses {self.malformed_seen}, expected {ref.malformed}")
        assert self.ovf_seen == ref.overflow, (
            f"Receiver overflow pulses {self.ovf_seen}, expected {ref.overflow}")
        naks = sum(1 for a in self.acknaks if a[2] == "nak")
        assert naks == ref.nak_sets, f"{naks} Naks sent, expected {ref.nak_sets}"
        worst_ack = 0
        for eop, seq, active in ref.accepts:
            if not active:
                continue
            cover = next((s for s, _, _, q in self.acknaks
                          if s > eop and (q - seq) % 4096 < 2048), None)
            assert cover is not None, f"TLP {seq} (eop cycle {eop}) never acknowledged"
            worst_ack = max(worst_ack, cover - eop)
        if ack_bound is not None:
            assert worst_ack <= ack_bound, (
                f"an Ack started {worst_ack} cycles after its TLP's eop, HAS bound {ack_bound}")
        worst_rx = 0
        never_stalls = self.rx_ready_p == 1.0 and self.rx_stall_until == 0
        for i, (eop, empty) in enumerate(zip(ref.store_cycle, ref.empty_at_store)):
            if empty and never_stalls:
                worst_rx = max(worst_rx, self.up_sop[i] - eop)
        assert worst_rx <= RX_LATENCY, (
            f"a TLP reached the upper stream {worst_rx} cycles after its eop, HAS bound "
            f"{RX_LATENCY}")
        return {"ack": worst_ack, "rx": worst_rx}


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

    def check_shadow_rx(self) -> list[dict]:
        """The architecture-model shadow, given the same packets, judges
        every received TLP as the reference does on the DUT's counters
        (stored, overflow, malformed, bad, duplicate, discarded; overflows
        included), counts the same Bad TLP, Malformed TLP, Receiver
        overflow, Nak and duplicate events, and delivers the same TLPs. At a
        link-down the TLPs stored and not yet delivered are abandoned, and
        which of them each side finished first is a matter of its delivery
        latency: within a link-up epoch ended by a link-down the two
        delivery lists are equal, or the longer is the shorter plus exactly
        one TLP; the final epoch must match exactly. Returns those
        link-down differences, diagnosed. Both sides' free credits for the
        TLP's class must be equal before every TLP, so a counter that drifts
        fails at the first TLP it affects, before any verdict differs; a
        verdict that differs on the same free credits would be a rule
        difference (HAS open items)."""
        sh, ref = self.shadow, self.ref
        rv, sv = self.ref_verdicts, self.sh_verdicts
        assert len(rv) == len(sv), f"{len(rv)} TLPs judged by the reference, {len(sv)} by the shadow"
        i = next((k for k, (a, b) in enumerate(zip(rv, sv)) if a[2:] != b[2:]), None)
        assert i is None, (f"TLP {i}: free credits before it differ: reference (DUT counters) "
                           f"{rv[i]}, shadow (arch) {sv[i]} (cycle, verdict, credits, free)")
        i = next((k for k, (a, b) in enumerate(zip(rv, sv)) if a[1] != b[1]), None)
        assert i is None, (f"TLP {i}: reference (DUT counters) {rv[i]}, shadow (arch) {sv[i]}; "
                           f"verdict, credits and free credits before it")
        diffs = []
        cuts = [(c, d, a) for c, d, a in self.epochs] + [(None, len(self.delivered),
                                                          len(sh.delivered))]
        d0 = a0 = 0
        for cyc, d1, a1 in cuts:
            dseg, aseg = self.delivered[d0:d1], sh.delivered[a0:a1]
            if cyc is None or len(dseg) == len(aseg):
                assert dseg == aseg, (f"epoch from DUT TLP {d0}: shadow (arch) delivered "
                                      f"{len(aseg)} TLPs, DUT {len(dseg)}")
            else:
                short, long_ = sorted((dseg, aseg), key=len)
                assert long_[:len(short)] == short and len(long_) - len(short) == 1, (
                    f"link-down at cycle {cyc}: shadow (arch) delivered {len(aseg)} TLPs in the "
                    f"epoch, DUT {len(dseg)}")
                k = d0 + len(short)
                diffs.append({"link_down": cyc, "tlp": k,
                              "delivered_by": "DUT" if len(dseg) > len(aseg) else "shadow",
                              "dut_sop": self.up_sop[k] if len(dseg) > len(aseg) else None})
            d0, a0 = d1, a1
        assert sh.errors[arch.E_BAD_TLP] == ref.bad, (
            f"shadow (arch) Bad TLP {sh.errors[arch.E_BAD_TLP]}, reference {ref.bad}")
        assert sh.errors[arch.E_MALFORMED] == ref.malformed, (
            f"shadow (arch) Malformed TLP {sh.errors[arch.E_MALFORMED]}, reference "
            f"{ref.malformed}")
        assert sh.errors[arch.E_OVERFLOW] == ref.overflow, (
            f"shadow (arch) Receiver overflow {sh.errors[arch.E_OVERFLOW]}, reference "
            f"{ref.overflow}")
        assert sh.counts["naks"] == ref.nak_sets, (
            f"shadow (arch) sent {sh.counts['naks']} Naks, reference {ref.nak_sets}")
        assert sh.counts["duplicates"] == len(ref.dups), (
            f"shadow (arch) duplicates {sh.counts['duplicates']}, reference {len(ref.dups)}")
        return diffs

    def check_clean(self) -> None:
        assert not self.violations, "DUT violations:\n" + "\n".join(self.violations[:20])
        assert not self.xcheck, ("RTL and arch shadow disagree:\n"
                                 + "\n".join(self.xcheck[:20]))
        assert self.err_counts[ERR_PROTOCOL] == self.proto_expected, (
            f"DL protocol error pulses: {self.err_counts[ERR_PROTOCOL]}, expected "
            f"{self.proto_expected} from the received Acks and Naks")
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
        self.in_l0 = True
        self.tlps: deque[bytes] = deque()      # offered to the model's upper stream in order
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
        if self.tlps and d.offer(self.tlps[0], n):
            self.tlps.popleft()
        pkt = d.tick(n, self.in_l0, not self.out, True)
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


def arch_to_partner_traffic(adv: dict[str, tuple[int, int]], partner: ptn.Partner,
                         tlps: list[bytes], schedule, rng: random.Random, *,
                         delay: int = 22, drain: int = 20000) -> arch.Dll:
    """The architecture model (credits ``adv``) sends ``tlps`` to
    ``partner``, whose upper layer follows ``schedule`` (cycles, take
    probability), then takes everything until the model has sent them all.
    Returns the model; the caller checks what the partner received."""
    fc = {"ph": adv["P"][0], "pd": adv["P"][1], "nph": adv["NP"][0], "npd": adv["NP"][1],
          "cplh": adv["Cpl"][0], "cpld": adv["Cpl"][1]}
    a = ArchEnd(arch.Dll("arch", fc))
    a.tlps.extend(tlps)
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
        if not a.tlps and not a.dll.rbuf and len(partner.received) == len(tlps):
            break
        cycle()
    return a.dll


def arch_partner_send(tlps: list[bytes], partner: ptn.Partner, *,
                         drop_from: int | None = None, retrain_cycles: int = 500,
                         delay: int = 22, limit: int = 200000) -> tuple[arch.Dll, int]:
    """The architecture model (default credits) sends ``tlps`` to
    ``partner``; from cycle ``drop_from`` (counted from both in DL_Active)
    every TLP it starts is lost until its retrain request, which runs
    ``retrain_cycles`` of Recovery (no packet starts on either line, the
    LTSSM out of L0). Returns the model and the cycles taken once every
    TLP is delivered."""
    a = ArchEnd(arch.Dll("arch", {}))
    ab, ba = ptn.Line(delay), ptn.Line(delay)
    drop_a, drop_p = Drop(), Drop()
    to_a = to_p = None
    a.link_up = partner.link_up = True
    t0, l0, recovery, window, retrains = None, True, 0, False, 0
    for n in range(limit):
        if t0 is None and a.dll.state == arch.ACTIVE and partner.state == ptn.ACTIVE:
            t0 = n
            a.tlps.extend(tlps)
        if t0 is not None and drop_from is not None and n - t0 == drop_from:
            window = True
        a.in_l0 = partner.in_l0 = l0
        out_a = a.clock(to_a)
        out_p = partner.clock(to_p)
        ok_a = l0 and not (window and out_a is not None and not out_a.dllp)
        # As in Env: the model's packets are lost at the source, the
        # partner's on arrival (those in flight when Recovery starts too).
        to_p = ab.push(drop_a.pass_beat(out_a, ok_a))
        to_a = drop_p.pass_beat(ba.push(out_p), l0)
        if a.dll.counts["retrains"] > retrains:
            retrains = a.dll.counts["retrains"]
            window, l0, recovery = False, False, retrain_cycles
        elif recovery:
            recovery -= 1
            l0 = recovery == 0
        if t0 is not None and len(partner.received) >= len(tlps):
            return a.dll, n - t0
    raise AssertionError(f"arch model: {len(partner.received)} of {len(tlps)} TLPs delivered "
                         f"within {limit} cycles")


def arch_partner_traffic(adv: dict[str, tuple[int, int]], errors: dict[str, float], seed: int,
                         tlps: list[bytes], *, delay: int = 22, limit: int = 400000) -> int:
    """The architecture model (credits ``adv``) receiving ``tlps`` from a
    fresh partner with the same error injection: every TLP delivered once
    and in order, all acknowledged, no partner violation. Returns the
    cycles it took."""
    fc = {"ph": adv["P"][0], "pd": adv["P"][1], "nph": adv["NP"][0], "npd": adv["NP"][1],
          "cplh": adv["Cpl"][0], "cpld": adv["Cpl"][1]}
    a = ArchEnd(arch.Dll("arch", fc))
    partner = ptn.Partner("fresh", inject=ptn.Inject(**errors), seed=seed)
    ab, ba = ptn.Line(delay), ptn.Line(delay)
    a.link_up = partner.link_up = True
    to_a = to_p = None
    sent = False
    for n in range(limit):
        out_a = a.clock(to_a)
        out_p = partner.clock(to_p)
        to_p, to_a = ab.push(out_a), ba.push(out_p)
        if not sent and a.dll.state == arch.ACTIVE and partner.state == ptn.ACTIVE:
            for t in tlps:
                partner.send(t)
            sent = True
        if sent and len(a.dll.delivered) >= len(tlps) and not partner.rbuf \
                and not partner.tx_queue:
            assert a.dll.delivered == tlps, "arch model: TLPs not delivered once and in order"
            assert not partner.violations, "\n".join(partner.violations[:10])
            return n
    raise AssertionError(f"arch model and partner: not all delivered within {limit} cycles")
