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
   parameters, and its bytes equal to what both models encode; an Ack or
   Nak carries the sequence number the receive reference expects.
6. The **receive reference** (``RxRef``) judges every TLP packet the DUT
   receives by the HAS receive rules, with the partner's CRC and header
   helpers: the TLPs the upper stream must deliver, the Bad TLP and
   Malformed TLP pulses, NEXT_RCV_SEQ and each NAK_SCHEDULED setting. The
   **upper layer** takes received beats (``tlp_rx_ready_i``, stalling with
   ``rx_ready_p`` below 1 only when every class is finite) and checks
   each delivered TLP against the reference.
7. The **upper transmit driver** offers queued TLPs on ``tlp_tx_*`` (valid
   may drop inside a TLP) and records each one the DUT takes; every TLP the
   DUT sends is checked (``_tx_tlp``): its sequence number, the TLP taken
   with it, its LCRC equal to both models' and zlib's, and the transmit
   order (``_tx_take``): new TLPs in order, each replay from the oldest
   unacknowledged TLP, in order and complete, against the Acks and Naks the
   DUT received. A **retrain stand-in** answers ``retrain_req_o`` with a
   pass through Recovery (``ltssm_state_i`` out of L0, nothing on the line).
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
ERR_ALLOWED = ERR_BAD_TLP | ERR_BAD_DLLP | ERR_MALFORMED   # the bits the DUT drives today
L0_CODE = 0x20                      # an L0 code of ltssm_state_i (mac LTSSM encoding)
RECOVERY_CODE = 0x10                # Recovery.RcvrLock: not L0
# err_o bits of the TLP transmit path (HAS, errors); bits 0, 5 and 6 are the
# receive path's (#213, #214) and never pulse here.
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
COV_UPDFC.coverpoint("cause", bins=["entry", "refresh"])
COV_UPDFC.coverpoint("cls", bins=list(CLASSES))
COV_UPDFC.cross("cause_x_cls", "cause", "cls")

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
                                       "not_live"])
COV_TLP_RX.coverpoint("cls", bins=list(CLASSES))
COV_TLP_RX.coverpoint("gap", bins=["no_gap", "gap_inside"])
COV_TLP_RX.coverpoint("nak", bins=["scheduled", "already_scheduled"])

COV_MALFORMED = Covergroup("dll_malformed")
COV_MALFORMED.coverpoint("cause", bins=["not_whole_dw", "short", "length", "payload",
                                        "no_header"])

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
    The DUT's overflow check is #214's, so every expected, well-formed TLP
    is stored here."""

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
            if ptn.tlp_malformed(tlp):
                self.malformed += 1
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
                                       f"drive yet (#214)")
        self._retrain_check(n, err)
        if state != self.state and self.state == ACTIVE:
            self._tlp_link_reset()
        if state != self.state:
            COV_STATES.sample(arc=(self.state, state))
            self.states.append((n, state))
            if state == ACTIVE:
                self.upd_entry = {0, 1, 2}
            self.state = state
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
            out = self.ref.judge(n, eop, data, err_b, null_b, live, state == ACTIVE,
                                 len(self.delivered))
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
            sh.receive(self.shadow_rx.popleft()[1], n)
        if in_beat is not None:
            self._rx_collect(n, in_beat)
        elif self.rx_beats:
            self.rx_gap = True
        if self.shadow_pending:
            self._shadow_offer(n)
        sh.tick(n, self.l0, True, True)
        if sh.state != self.shadow_states[-1][1]:
            self.shadow_states.append((n, sh.state))

        if self.tlp_drive:
            # tlp_tx_ready_o depends on the sop beat just driven.
            await Timer(1, "ns")
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
        raise AssertionError(f"cycle {self.cycle}: {what} not reached within {limit} cycles")

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
            if cls in self.upd_entry:
                cause = "entry"
                self.upd_entry.discard(cls)
            else:
                cause = "refresh"
            COV_UPDFC.sample(cause=cause, cls=CLASSES[cls])
            self.upd_last[cls] = sop
        COV_TX.sample(kind=kind, cls=CLASSES[cls])
        self.sent.append((sop, kind, cls, data))
        self.offers.append(self.tx_sop_offer)

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
            if i >= len(self.ref.expect):
                self.violations.append(f"cycle {n}: TLP {tlp[:8].hex()} delivered, none expected")
            elif tlp != self.ref.expect[i]:
                self.violations.append(f"cycle {n}: TLP {i} delivered {tlp[:16].hex()}..., "
                                       f"expected {self.ref.expect[i][:16].hex()}...")

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

    def check_shadow_rx(self) -> None:
        """The architecture-model shadow, given the same packets, delivers
        the same TLPs and counts the same Bad TLP, Malformed TLP and Nak
        events (HAS open items get any disagreement)."""
        sh, ref = self.shadow, self.ref
        diff = next((i for i, (x, y) in enumerate(zip(sh.delivered, self.delivered)) if x != y),
                    min(len(sh.delivered), len(self.delivered)))
        assert sh.delivered == self.delivered, (
            f"shadow (arch) delivered {len(sh.delivered)} TLPs, DUT {len(self.delivered)}; "
            f"first difference at TLP {diff}")
        assert sh.errors[arch.E_BAD_TLP] == ref.bad, (
            f"shadow (arch) Bad TLP {sh.errors[arch.E_BAD_TLP]}, reference {ref.bad}")
        assert sh.errors[arch.E_MALFORMED] == ref.malformed, (
            f"shadow (arch) Malformed TLP {sh.errors[arch.E_MALFORMED]}, reference "
            f"{ref.malformed}")
        assert sh.errors[arch.E_OVERFLOW] == 0, "shadow (arch) reports a receiver overflow"
        assert sh.counts["naks"] == ref.nak_sets, (
            f"shadow (arch) sent {sh.counts['naks']} Naks, reference {ref.nak_sets}")
        assert sh.counts["duplicates"] == len(ref.dups), (
            f"shadow (arch) duplicates {sh.counts['duplicates']}, reference {len(ref.dups)}")

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
