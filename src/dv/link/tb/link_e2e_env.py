"""cocotb environment of the link back-to-back e2e (wiki/src/dv/link/DV_PLAN.md;
wiki/src/arch/link/HAS.md, end-to-end plan).

Two ``pcie_link`` instances on the top ``pcie_link_e2e_tb``: a (downstream)
and b (upstream), each on its own clock (b's period ``ppm`` off a's), each
one's ``ser_rx_clk_i`` the far clock. One coroutine per clock runs
``edge(i)`` at every falling edge of end i's clock:

1. ``LinkEnd.edge`` samples end i's outputs (registered at the rising edge
   just passed), checks them, and drives its inputs: reset, the upper
   transmit driver (``tlp_tx_*``, valid may drop inside a TLP), the upper
   receive taker (``tlp_rx_ready_i``) and receiver detection.
2. End i's serial word goes through its direction's line ``Injector``
   (``models/``), which records the sent and delivered packet views and
   the injection log for the partner's passive checker, then through the
   phy e2e's ``Channel`` model (delay, bit flips, slips, electrical idle),
   and is driven on the far end's ``ser_rx_*``.

Checks, all from the HAS (checks): one ``Scoreboard`` per direction (every
TLP accepted on one end's upper stream is the next delivered on the other,
byte for byte; drained at the end); the error pulses (DL protocol error,
Malformed TLP and Receiver overflow never); the link (``link_up_o`` 1 and
DL_Active once reached); credits available never above the far end's
advertisement, and back at it when drained; liveness (no TLP delivered
for 20000 cycles while any is outstanding fails); reset values while
``rst_i`` is high and the dll outputs at theirs until ``link_up_o``; the
Ack latency at each end's dll-to-phy boundary (118 cycles, on the
packet-stream nets the top taps); and the passive checker
(``pcie_dll_partner_passive``) on the two views, with the line Ack bound of
140 cycles.
"""

from __future__ import annotations

import os
import random
import sys
from collections import deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from cocotb.utils import get_sim_time

from regress.funcov import Covergroup

_DV = Path(__file__).resolve().parents[1]
sys.path.append(str(_DV / "models"))
sys.path.append(str(_DV.parent / "dll" / "models"))      # the independent partner
sys.path.append(str(_DV.parent / "phy" / "models"))      # the channel model
import pcie_dll_partner as ptn  # noqa: E402
from pcie_dll_partner_crc import lcrc_bytes  # noqa: E402
from pcie_dll_partner_passive import PassiveChecker, ViewPacket  # noqa: E402
from pcie_link_injector_model import Injector  # noqa: E402
from pcie_phy_channel_model import Channel  # noqa: E402

CORE_PS = 8000
B_PHASE_PS = 2960                    # b's first edge, so the two clocks rarely coincide
RESET_CYCLES = 16
SEQ_MOD = 4096
L0_LO, L0_HI = 0x20, 0x2F
ACTIVE = 3

# dl_err_o bits (dll HAS, errors).
BAD_TLP, BAD_DLLP, TIMEOUT, ROLLOVER, PROTOCOL, OVERFLOW, MALFORMED = range(7)
ERR_NAMES = ("bad_tlp", "bad_dllp", "replay_timeout", "rollover", "protocol",
             "overflow", "malformed")
NEVER = (PROTOCOL, OVERFLOW, MALFORMED)

ACK_DLL_BOUND = 118                  # HAS targets: at the dll-to-phy boundary
ACK_LINE_BOUND = 140                 # HAS targets: derived bound on the line
LIVENESS = 20_000

CREDIT_CONFIGS = ("default", "minimum", "finite", "infinite", "asymmetric")
CLASS_FIELDS = ("P", "NP", "Cpl")
FC_PORTS = ("fc_ph_avail_o", "fc_pd_avail_o", "fc_nph_avail_o", "fc_npd_avail_o",
            "fc_cplh_avail_o", "fc_cpld_avail_o")
FC_WIDTH = (8, 12, 8, 12, 8, 12)

# The traffic mix (HAS, credit configurations): each kind at least 20%.
KINDS = ("MWr", "MRd", "CfgWr", "CplD")

# Reset values (HAS, reset values); the dll ones also hold while link_up_o is 0.
RESET_DLL = {"tlp_tx_ready_o": 0, "tlp_rx_valid_o": 0, "tlp_rx_sop_o": 0, "tlp_rx_eop_o": 0,
             "fc_ph_avail_o": 0, "fc_pd_avail_o": 0, "fc_nph_avail_o": 0,
             "fc_npd_avail_o": 0, "fc_cplh_avail_o": 0, "fc_cpld_avail_o": 0,
             "dl_up_o": 0, "dl_state_o": 0, "dl_err_o": 0}
RESET_PHY = {"link_up_o": 0, "ltssm_state_o": 0, "rcv_err_o": 0, "ser_tx_elec_idle_o": 1}

# ------------------------------------------------------------ coverage

COV_CONFIG = Covergroup("link_e2e_config")
COV_CONFIG.coverpoint("credits", bins={c: c for c in CREDIT_CONFIGS})
COV_CONFIG.coverpoint("ppm", bins={"zero": 0, "pos_600": 600, "neg_600": -600,
                                   "other": lambda v: v not in (0, 600, -600)})

COV_INJECT = Covergroup("link_e2e_inject")
INJECT_BINS = ("lcrc", "corrupt_tlp", "drop_tlp", "corrupt_dllp", "drop_dllp", "nak",
               "replay_tlp", "duplicate", "rollover", "line_flip", "line_slip", "line_idle")
COV_INJECT.coverpoint("cls", bins={c: c for c in INJECT_BINS})
COV_INJECT.coverpoint("dir", bins={"a_to_b": "ab", "b_to_a": "ba"})
COV_INJECT.cross("cls_x_dir", "cls", "dir")

COV_RECOVERY = Covergroup("link_e2e_recovery")
COV_RECOVERY.coverpoint("event", bins={n: n for n in (
    "bad_tlp", "bad_dllp", "replay_timeout", "rollover", "retrain", "rcv_err", "nak_sent",
    "duplicate_acked")})

COV_TRAFFIC = Covergroup("link_e2e_traffic")
COV_TRAFFIC.coverpoint("kind", bins={k: k for k in KINDS})
COV_TRAFFIC.coverpoint("dir", bins={"a_to_b": "ab", "b_to_a": "ba"})
COV_TRAFFIC.coverpoint("payload", bins={"none": 0, "dw1": 4, "small": range(8, 64),
                                        "large": range(64, 128), "max": 128})
COV_TRAFFIC.cross("kind_x_dir", "kind", "dir")

COV_LINK = Covergroup("link_e2e_control")
COV_LINK.coverpoint("request", bins={r: r for r in ("hot_reset", "disable", "loopback")})

COV_CREDIT = Covergroup("link_e2e_credit")
COV_CREDIT.coverpoint("starved", bins={c: c for c in CLASS_FIELDS})
COV_CREDIT.coverpoint("stall", bins={"stall_4000": 4000, "stall_10000": 10000})


def tlp_kind(tlp: bytes) -> str:
    return {0x40: "MWr", 0x00: "MRd", 0x44: "CfgWr", 0x4A: "CplD"}.get(tlp[0], "other")


def payload_bytes(tlp: bytes) -> int:
    return 4 * ptn.tlp_payload_dw(tlp)


def now_ps() -> int:
    return get_sim_time("ps")


class Scoreboard:
    """One direction: the TLPs one end's upper layer had accepted, against
    those the other end delivers, in order and byte for byte."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.accepted: list[bytes] = []
        self.delivered = 0
        self.lost = 0                   # dropped at a requested link-down (HAS, stack wiring)
        self.last_delivery = 0          # cycle of the receiving end

    def accept(self, tlp: bytes) -> None:
        self.accepted.append(tlp)
        COV_TRAFFIC.sample(kind=tlp_kind(tlp), dir=self.name, payload=payload_bytes(tlp))

    def deliver(self, tlp: bytes, cycle: int) -> None:
        i = self.delivered
        assert i < len(self.accepted), (
            f"{self.name}: delivered TLP {i} at cycle {cycle} was never accepted "
            f"(a duplicate or invented TLP): {tlp[:16].hex()}")
        want = self.accepted[i]
        assert tlp == want, (
            f"{self.name}: TLP {i} delivered at cycle {cycle}: expected {want[:16].hex()}... "
            f"({len(want)} bytes), observed {tlp[:16].hex()}... ({len(tlp)} bytes): lost, "
            "reordered or corrupted")
        self.delivered += 1
        self.last_delivery = cycle

    def outstanding(self) -> int:
        return len(self.accepted) - self.delivered


class BoundaryAck:
    """The Ack latency at one end's dll-to-phy boundary (HAS targets): every
    TLP the dll receives good and in order from the phy is covered by an
    Ack or Nak whose sop the phy takes within 118 cycles of its eop."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.rx: list[int] = []
        self.next_rcv = 0
        self.pending: deque = deque()   # (eop cycle, seq)
        self.tx: list[int] = []
        self.tx_sop = 0
        self.max = 0
        self.checked = 0
        self.exempt = 0
        self.violations: list[str] = []

    def reset(self) -> None:
        self.rx, self.tx, self.next_rcv = [], [], 0
        self.pending.clear()

    def rx_beat(self, n: int, data: int, sop: bool, eop: bool, dllp: bool, err: bool) -> None:
        if sop:
            self.rx = []
        self.rx += [data & 0xFF, data >> 8]
        if not eop or dllp:
            return
        pkt = bytes(self.rx)
        if err or len(pkt) < 6 or lcrc_bytes(pkt[:-4]) != pkt[-4:]:
            return
        seq = ((pkt[0] & 0x0F) << 8) | pkt[1]
        if seq == self.next_rcv:
            self.pending.append((n, seq))
            self.next_rcv = (seq + 1) % SEQ_MOD

    def tx_beat(self, n: int, data: int, sop: bool, eop: bool, dllp: bool) -> None:
        if not dllp:
            return
        if sop:
            self.tx, self.tx_sop = [], n
        self.tx += [data & 0xFF, data >> 8]
        if eop and len(self.tx) == 6 and self.tx[0] in (ptn.ACK, ptn.NAK):
            s = ((self.tx[2] & 0x0F) << 8) | self.tx[3]
            while self.pending and (s - self.pending[0][1]) % SEQ_MOD < SEQ_MOD // 2:
                eop_n, seq = self.pending.popleft()
                gap = self.tx_sop - eop_n
                self.max = max(self.max, gap)
                self.checked += 1
                if gap > ACK_DLL_BOUND:
                    self.violations.append(f"{self.name}: TLP {seq} received at cycle {eop_n} "
                                           f"covered by an Ack or Nak taken at {self.tx_sop}: "
                                           f"{gap} cycles, bound {ACK_DLL_BOUND}")

    def drop_pending(self) -> None:
        """Outside L0 the bound does not apply (HAS targets: error-free)."""
        self.exempt += len(self.pending)
        self.pending.clear()


class LinkEnd:
    """One pcie_link instance of the top (``u_a`` or ``u_b``)."""

    def __init__(self, env: "Env", prefix: str, role: int, fc: tuple[int, ...]) -> None:
        dut = env.dut
        self.env, self.name, self.role = env, prefix.rstrip("_"), role
        self.dir = "ab" if self.name == "a" else "ba"           # what this end sends
        self.s = {n[len(prefix):]: getattr(dut, n) for n in dir(dut) if n.startswith(prefix)}
        self.fc = fc                     # this end's FC_*, as advertised (0 infinite)
        self.any_inf = 0 in fc
        self.rng = random.Random(env.rng.getrandbits(32))
        self.cycle = 0
        self.rst = True
        self.release = RESET_CYCLES
        # upper transmit
        self.tx_q: deque = deque()
        self.cur: bytes | None = None
        self.pos = 0
        self.valid_p = 0.9
        self.drove_valid = False
        self.sop_wait = 0
        self.starved: set[str] = set()
        # upper receive
        self.rx_beats: list[int] = []
        self.take_p = 1.0                # probability of taking a beat (finite credits only)
        self.stall_until = 0
        # status
        self.link_up = False
        self.ltssm = 0
        self.dl_state = 0
        self.live = False                # DL_Active reached; link checks on
        self.err = [0] * 7
        self.err_log: list[tuple[int, int]] = []   # (time ps, dl_err_o) per nonzero pulse
        self.rcv_err = 0
        self.not_l0: list[list[int]] = []          # [left, back] in ps, live only
        self.l0_leaves = 0
        self.avail = [0] * 6
        self.ack = BoundaryAck(self.name)
        self.violations: list[str] = []
        for name in ("tlp_tx_valid_i", "tlp_tx_data_i", "tlp_tx_sop_i", "tlp_tx_eop_i",
                     "retrain_req_i", "hot_reset_req_i", "disable_req_i", "loopback_req_i",
                     "ser_tx_rxdet_i", "ser_rx_data_i"):
            self.s[name].value = 0
        self.s["tlp_rx_ready_i"].value = 1
        self.s["ser_rx_elec_idle_i"].value = 1
        self.s["port_role_i"].value = role
        self.s["rst_i"].value = 1

    def present(self) -> bool:
        return not self.rst

    # -- one falling edge ----------------------------------------------------

    def edge(self, rxdet: bool, t: int) -> tuple[int, bool]:
        s = self.s
        self.cycle += 1
        n = self.cycle
        rst_q = self.rst                  # rst_i as sampled at the rising edge just passed
        # Sample.
        link_up = bool(int(s["link_up_o"].value))
        if rst_q and n > 2:
            self._check_values(RESET_DLL, "in reset")
            self._check_values(RESET_PHY, "in reset")
        elif not link_up and not self.link_up and not rst_q:
            # The dll leaves DL_Active at the edge after it samples link_up_i 0.
            self._check_values(RESET_DLL, "while link_up_o is 0")
        ltssm = int(s["ltssm_state_o"].value)
        dl_state = int(s["dl_state_o"].value)
        err = int(s["dl_err_o"].value)
        if err:
            self.err_log.append((t, err))
            for b in range(7):
                if err >> b & 1:
                    self.err[b] += 1
                    if b in NEVER:
                        self.violations.append(f"end {self.name} cycle {n}: "
                                               f"{ERR_NAMES[b]} pulsed on dl_err_o")
        self.rcv_err += int(s["rcv_err_o"].value)
        if self.live and (not link_up or dl_state != ACTIVE):
            if self.env.expect_down and not link_up:
                self.live = False
                self.env.on_down(self, t)
            else:
                self.violations.append(f"end {self.name} cycle {n}: left DL_Active "
                                       f"(link_up_o {int(link_up)}, dl_state_o {dl_state})")
                self.live = False
        if self.live:
            in_l0 = L0_LO <= ltssm <= L0_HI
            if in_l0 and ltssm != L0_LO:
                self.violations.append(f"end {self.name} cycle {n}: ltssm_state_o {ltssm:#04x}, "
                                       "an L0s sub-state (link holds the phy at L0)")
            was_l0 = L0_LO <= self.ltssm <= L0_HI
            if was_l0 and not in_l0:
                self.not_l0.append([t, None])
                self.l0_leaves += 1
                self.ack.drop_pending()
            elif in_l0 and not was_l0 and self.not_l0 and self.not_l0[-1][1] is None:
                self.not_l0[-1][1] = t
            self._credits()
        elif dl_state == ACTIVE and link_up:
            self.live = True
            self.env.on_active(self)
        self.link_up, self.ltssm, self.dl_state = link_up, ltssm, dl_state
        # The dll-to-phy packet stream (top taps).
        if int(s["mon_pkt_rx_valid_o"].value):
            self.ack.rx_beat(n, int(s["mon_pkt_rx_data_o"].value),
                             bool(int(s["mon_pkt_rx_sop_o"].value)),
                             bool(int(s["mon_pkt_rx_eop_o"].value)),
                             bool(int(s["mon_pkt_rx_dllp_o"].value)),
                             bool(int(s["mon_pkt_rx_err_o"].value)))
        if int(s["mon_pkt_tx_valid_o"].value) and int(s["mon_pkt_tx_ready_o"].value):
            self.ack.tx_beat(n, int(s["mon_pkt_tx_data_o"].value),
                             bool(int(s["mon_pkt_tx_sop_o"].value)),
                             bool(int(s["mon_pkt_tx_eop_o"].value)),
                             bool(int(s["mon_pkt_tx_dllp_o"].value)))
        # Upper receive: the beat offered in the cycle just ended was taken
        # when ready was 1 at its rising edge.
        if int(s["tlp_rx_valid_o"].value) and self._ready:
            data = int(s["tlp_rx_data_o"].value)
            if int(s["tlp_rx_sop_o"].value):
                self.rx_beats = []
            self.rx_beats += list(data.to_bytes(4, "little"))
            if int(s["tlp_rx_eop_o"].value):
                self.env.delivered(self, bytes(self.rx_beats), n)
        word = int(s["ser_tx_data_o"].value)
        ei = bool(int(s["ser_tx_elec_idle_o"].value))
        # Drive.
        self.rst = n < self.release
        s["rst_i"].value = int(self.rst)
        s["ser_tx_rxdet_i"].value = int(rxdet)
        self._ready = self._take(n)
        s["tlp_rx_ready_i"].value = int(self._ready)
        self._drive_tx()
        return word, ei

    _ready = True

    def _check_values(self, table: dict, when: str) -> None:
        for name, want in table.items():
            got = int(self.s[name].value)
            if got != want:
                self.violations.append(f"end {self.name} cycle {self.cycle} {when}: {name} "
                                       f"expected {want}, observed {got}")

    def _credits(self) -> None:
        """Credits available never exceed the far end's advertisement; an
        infinite field shows all ones."""
        far = self.env.ends[1 - self.env.ends.index(self)].fc
        for i, port in enumerate(FC_PORTS):
            v = int(self.s[port].value)
            self.avail[i] = v
            top = (1 << FC_WIDTH[i]) - 1
            if far[i] == 0:
                if v != top:
                    self.violations.append(f"end {self.name} cycle {self.cycle}: {port} {v}, "
                                           f"expected all ones (infinite)")
            elif v > far[i]:
                self.violations.append(f"end {self.name} cycle {self.cycle}: {port} {v} above "
                                       f"the {far[i]} the far end advertises")

    def _take(self, n: int) -> bool:
        if self.any_inf:
            return True                  # the upper-layer obligation (dll HAS, receive)
        if n < self.stall_until:
            return False
        return self.take_p >= 1.0 or self.rng.random() < self.take_p

    def _drive_tx(self) -> None:
        s = self.s
        if self.rst or not self.live:
            self.drove_valid = False
            s["tlp_tx_valid_i"].value = 0
            return
        if self.cur is None and self.tx_q:
            self.cur, self.pos = self.tx_q.popleft(), 0
        if self.cur is None:
            self.drove_valid = False
            s["tlp_tx_valid_i"].value = 0
            return
        i, beats = self.pos, len(self.cur) // 4
        valid = i == 0 or self.rng.random() < self.valid_p
        s["tlp_tx_valid_i"].value = int(valid)
        s["tlp_tx_data_i"].value = int.from_bytes(self.cur[4 * i:4 * i + 4], "little")
        s["tlp_tx_sop_i"].value = int(i == 0)
        s["tlp_tx_eop_i"].value = int(i == beats - 1)
        self.drove_valid = valid

    def after_drive(self) -> None:
        """Read tlp_tx_ready_o for the beat just driven (it depends on it)."""
        if not self.drove_valid:
            return
        ready = int(self.s["tlp_tx_ready_o"].value)
        if self.pos and not ready:
            self.violations.append(f"end {self.name} cycle {self.cycle}: tlp_tx_ready_o 0 "
                                   "inside a TLP")
        if not ready:
            if self.pos == 0:
                self.sop_wait += 1
                cls = ptn.tlp_class(self.cur)
                need = ptn.tlp_credits(self.cur)
                h, d = CLASS_FIELDS.index(cls) * 2, CLASS_FIELDS.index(cls) * 2 + 1
                far = self.env.ends[1 - self.env.ends.index(self)].fc
                if (far[h] and self.avail[h] < need[0]) or (far[d] and self.avail[d] < need[1]):
                    if cls not in self.starved:
                        self.starved.add(cls)
                        COV_CREDIT.sample(starved=cls)
            return
        if self.pos == 0:
            self.env.sb[self.dir].accept(self.cur)
        self.pos += 1
        if self.pos == len(self.cur) // 4:
            self.cur = None

    def send(self, tlps) -> None:
        self.tx_q.extend(tlps)

    def tx_idle(self) -> bool:
        return self.cur is None and not self.tx_q

    def l0(self) -> bool:
        return self.link_up and L0_LO <= self.ltssm <= L0_HI

    def pulses(self, bit: int, since: int) -> int:
        return sum(1 for t, e in self.err_log if t >= since and e >> bit & 1)


# ------------------------------------------------------------- clocks

async def ppm_clock(sig, ppm: float, phase_ps: int) -> None:
    """``sig`` toggling with period CORE_PS * (1 + ppm 1e-6), each edge the
    ideal edge rounded to the picosecond."""
    half = CORE_PS * (1.0 + ppm * 1e-6) / 2.0
    sig.value = 0
    await Timer(phase_ps, "ps")
    now, ideal, level = phase_ps, float(phase_ps), 0
    while True:
        level ^= 1
        sig.value = level
        ideal += half
        step = round(ideal) - now
        now += step
        await Timer(step, "ps")


# ---------------------------------------------------------------- env

class Env:
    """One back-to-back run; ``seed`` draws everything random."""

    def __init__(self, dut, seed: int, ppm: float = 0, delay_words: int = 4,
                 look_words: int = 4, skew: int | None = None) -> None:
        self.dut = dut
        self.log = dut._log
        self.rng = random.Random(seed)
        self.seed, self.ppm = seed, ppm
        self.credits = CREDIT_CONFIGS[int(dut.cfg_credits_o.value)]
        self.timeout_div = int(dut.cfg_timeout_div_o.value)
        self.err_timeout_div = int(dut.cfg_err_timeout_div_o.value)
        fcs = []
        for e in "ab":
            v = int(getattr(dut, f"{e}_cfg_fc_o").value)
            fcs.append(tuple((v >> (12 * i)) & 0xFFF for i in range(6)))
        self.log.info("link e2e: COCOTB_RANDOM_SEED %s, test seed %d, credits %s (a %s, b %s), "
                      "b clock %+g ppm, TIMEOUT_DIV %d, ERR_TIMEOUT_DIV %d",
                      os.environ.get("COCOTB_RANDOM_SEED", "-"), seed, self.credits, fcs[0],
                      fcs[1], ppm, self.timeout_div, self.err_timeout_div)
        COV_CONFIG.sample(credits=self.credits, ppm=ppm)
        self.ends = (LinkEnd(self, "a_", 0, fcs[0]), LinkEnd(self, "b_", 1, fcs[1]))
        skew = self.rng.randrange(64) if skew is None else skew
        self.ends[1].release = RESET_CYCLES + skew
        self.sb = {"ab": Scoreboard("ab"), "ba": Scoreboard("ba")}
        self.periods = (CORE_PS, CORE_PS * (1.0 + ppm * 1e-6))
        self.chk = PassiveChecker(cycle=CORE_PS, ack_bound=ACK_LINE_BOUND)
        self.delay_words = delay_words
        self.injectors = []
        self.channels = []
        for i, d in enumerate(("ab", "ba")):
            self.injectors.append(Injector(
                random.Random(self.rng.getrandbits(32)), look_words,
                delivered_delay=delay_words * self.periods[i],
                on_sent=lambda p, d=d: self.chk.sent(d, ViewPacket(*p)),
                on_delivered=lambda p, d=d: self.chk.delivered(d, ViewPacket(*p)),
                on_inject=lambda t, cls, what, d=d: self._injected(t, d, cls, what)))
            self.channels.append(Channel(random.Random(self.rng.getrandbits(32)), delay_words))
        self.line_events: list[tuple[int, str, str]] = []
        self.active_at: list[int | None] = [None, None]
        self.tag = [0, 0]
        self.started = False
        self.expect_down = False         # a requested link-down: leaving DL_Active allowed
        self.down_since: int | None = None
        self.link_downs = 0

    # -- callbacks -------------------------------------------------------------

    def _injected(self, t: int, d: str, cls: str, what: str) -> None:
        self.chk.inject(t, d, cls, what)
        bin_ = {"drop_ack": "duplicate", "drop_tlp_window": "rollover"}.get(cls, cls)
        COV_INJECT.sample(cls=bin_, dir=d)

    def on_active(self, end: LinkEnd) -> None:
        self.active_at[self.ends.index(end)] = end.cycle
        if self.down_since is not None and all(e.live or e is end for e in self.ends):
            bound = ACK_LINE_BOUND * CORE_PS
            self.chk.exempt(self.down_since - bound, now_ps() + bound, "link down")
            self.down_since = None

    def on_down(self, end: LinkEnd, t: int) -> None:
        """An expected link-down (expect_down): the end's dll went to
        DL_Inactive, abandoning the TLP being accepted and every TLP it
        held; once both are down, what was not delivered is lost."""
        self.chk.link_down(end.name, t)
        self.link_downs += 1
        end.ack.reset()
        end.cur = None
        end.rx_beats = []
        if self.down_since is None:
            self.down_since = t
        if not any(e.live for e in self.ends):
            for sb in self.sb.values():
                sb.lost += sb.outstanding()
                sb.delivered = len(sb.accepted)

    def delivered(self, end: LinkEnd, tlp: bytes, n: int) -> None:
        self.sb["ba" if end.name == "a" else "ab"].deliver(tlp, n)

    # -- running ------------------------------------------------------------------

    def start(self) -> None:
        cocotb.start_soon(Clock(self.dut.a_clk_i, CORE_PS, unit="ps").start())
        cocotb.start_soon(ppm_clock(self.dut.b_clk_i, self.ppm, B_PHASE_PS))
        for i in (0, 1):
            cocotb.start_soon(self._loop(i))
        self.started = True

    async def _loop(self, i: int) -> None:
        end, far = self.ends[i], self.ends[1 - i]
        clk = (self.dut.a_clk_i, self.dut.b_clk_i)[i]
        inj, ch = self.injectors[i], self.channels[i]
        while True:
            await FallingEdge(clk)
            t = now_ps()
            word, ei = end.edge(far.present(), t)
            word, ei = inj.step(word, ei, t)
            word, ei = ch.step(word, ei)
            if far.rst:
                word, ei = far.rng.getrandbits(20), bool(far.rng.getrandbits(1))
            far.s["ser_rx_data_i"].value = word
            far.s["ser_rx_elec_idle_i"].value = int(ei)
            if end.drove_valid:
                await Timer(1, "ns")
                end.after_drive()
            self._liveness(end)

    def _liveness(self, end: LinkEnd) -> None:
        if end.name != "a":
            return
        for d, sb in self.sb.items():
            rx = self.ends[1] if d == "ab" else self.ends[0]
            if sb.outstanding() and rx.cycle - max(sb.last_delivery, self._live_from) > LIVENESS:
                end.violations.append(f"liveness: {d} delivered nothing for {LIVENESS} cycles "
                                      f"with {sb.outstanding()} TLPs outstanding")
                sb.last_delivery = rx.cycle

    _live_from = 0

    async def until(self, cond, limit: int, what: str, poll: int = 16) -> int:
        """Wait in a's cycles until ``cond()``; fail past ``limit``."""
        start = self.ends[0].cycle
        while not cond():
            self.raise_violations()
            n = self.ends[0].cycle - start
            assert n <= limit, (f"{what}: not reached in {limit} cycles (a ltssm "
                                f"{self.ends[0].ltssm:#04x} dl {self.ends[0].dl_state}, b ltssm "
                                f"{self.ends[1].ltssm:#04x} dl {self.ends[1].dl_state}; "
                                f"outstanding ab {self.sb['ab'].outstanding()}, "
                                f"ba {self.sb['ba'].outstanding()})")
            await ClockCycles(self.dut.a_clk_i, poll, rising=False)
        return self.ends[0].cycle - start

    async def cycles(self, n: int) -> None:
        await ClockCycles(self.dut.a_clk_i, n, rising=False)
        self.raise_violations()

    def raise_violations(self) -> None:
        for e in self.ends:
            assert not e.violations, "; ".join(e.violations[:5])
            assert not e.ack.violations, "; ".join(e.ack.violations[:5])

    async def link_up(self, limit: int = 40_000) -> int:
        """Both ends from reset to DL_Active."""
        n = await self.until(lambda: all(e.live for e in self.ends), limit, "both ends DL_Active")
        self._live_from = self.ends[0].cycle
        self.log.info("both ends DL_Active at a cycle %d (a reached it at %s, b at %s)",
                      self.ends[0].cycle, self.active_at[0], self.active_at[1])
        return n

    # -- traffic ------------------------------------------------------------------

    def tlps(self, i: int, n: int, kinds=KINDS) -> list[bytes]:
        """``n`` TLPs of the HAS mix for end i (0 a, 1 b); each kind in turn,
        drawn in order, so every kind is a quarter of any long run."""
        out = []
        for k in range(n):
            kind = kinds[self.rng.randrange(len(kinds))] if k % 2 else kinds[(k // 2) % len(kinds)]
            dw = self.rng.choice((1, self.rng.randint(1, 32), 32))
            out.append(ptn.make_tlp(kind, dw, (self.tag[i] + 0x4000 * i) & 0xFFFF))
            self.tag[i] += 1
        return out

    def send(self, i: int, n: int, **kw) -> None:
        self.ends[i].send(self.tlps(i, n, **kw))

    def drained(self) -> bool:
        return all(e.tx_idle() for e in self.ends) and all(
            sb.outstanding() == 0 for sb in self.sb.values())

    async def drain(self, limit: int = 200_000) -> int:
        n = await self.until(self.drained, limit, "traffic drain")
        return n

    # -- injection ----------------------------------------------------------------

    async def inject(self, d: str, cls: str, limit: int = 20_000) -> int:
        """Request one ``cls`` on direction ``d`` and wait until it hit a
        packet; returns its time (ps)."""
        inj = self.injectors[0 if d == "ab" else 1]
        before = len(self.chk.log)
        inj.request(cls)
        await self.until(lambda: len(self.chk.log) > before, limit, f"{cls} on {d} applied")
        return self.chk.log[-1].time

    def line(self, d: str, kind: str, n: int = 1) -> None:
        """A line impairment of the phy channel (flip, slip, idle) on ``d``;
        the passive views sit before it, so its time range is exempt."""
        ch = self.channels[0 if d == "ab" else 1]
        t = now_ps()
        if kind == "flip":
            ch.flip(n)
        elif kind == "slip":
            ch.slip(self.rng.choice((1, -1)))
        elif kind == "idle":
            ch.idle(n)
        else:
            raise ValueError(f"line impairment: expected flip, slip or idle, got {kind}")
        self.line_events.append((t, d, kind))
        self.chk.inject(t, d, f"line_{kind}", f"{n}")
        COV_INJECT.sample(cls=f"line_{kind}", dir=d)

    # -- final checks ---------------------------------------------------------------

    def finish(self) -> dict:
        """After a drain: every TLP delivered, credits back at the far end's
        advertisement, the boundary Ack latency, and the passive checker."""
        self.raise_violations()
        for d, sb in self.sb.items():
            assert sb.outstanding() == 0, f"{d}: {sb.outstanding()} TLPs accepted but not delivered"
        for e in self.ends:
            far = self.ends[1 - self.ends.index(e)].fc
            for i, port in enumerate(FC_PORTS):
                if far[i]:
                    assert e.avail[i] == far[i], (f"end {e.name}: drained, {port} {e.avail[i]}, "
                                                  f"expected the far end's {far[i]}")
        # The passive checker: Ack timing does not apply outside L0 or after
        # a line impairment the views cannot see.
        bound = ACK_LINE_BOUND * CORE_PS
        for e in self.ends:
            for left, back in e.not_l0:
                self.chk.exempt(left - bound, (back if back is not None else now_ps()) + bound,
                                f"{e.name} out of L0")
        for t, d, kind in self.line_events:
            self.chk.exempt(t - bound, t + 3000 * CORE_PS, f"line {kind} on {d}", line=True)
        v = self.chk.check()
        assert not v, f"passive checker: {len(v)} violations: " + "; ".join(v[:5])
        for d in ("ab", "ba"):
            sent = self.sb[d].accepted
            if sent and not self.link_downs:
                # Every accepted TLP went out once as new: judged on credits,
                # or cut short on the line before its Length (Recovery).
                seen = (self.chk.stats.get(f"credit_checked_{d}", 0)
                        + self.chk.stats.get(f"credit_unread_{d}", 0))
                assert seen == len(sent), (f"passive {d}: {seen} first transmissions seen, "
                                           f"{len(sent)} TLPs accepted")
        stats = {
            "delivered": {d: sb.delivered for d, sb in self.sb.items()},
            "err": {e.name: dict(zip(ERR_NAMES, e.err)) for e in self.ends},
            "rcv_err": {e.name: e.rcv_err for e in self.ends},
            "ack_dll_max": {e.name: e.ack.max for e in self.ends},
            "ack_dll_checked": {e.name: e.ack.checked for e in self.ends},
            "ack_line_max": round(self.chk.ack_gap_max, 1),
            "injected": {d: dict(self.injectors[i].counts) for i, d in enumerate(("ab", "ba"))},
            "l0_leaves": {e.name: e.l0_leaves for e in self.ends},
            "lost_at_link_down": {d: sb.lost for d, sb in self.sb.items()},
        }
        for e in self.ends:
            if e.err[BAD_TLP]:
                COV_RECOVERY.sample(event="bad_tlp")
            if e.err[BAD_DLLP]:
                COV_RECOVERY.sample(event="bad_dllp")
            if e.err[TIMEOUT]:
                COV_RECOVERY.sample(event="replay_timeout")
            if e.err[ROLLOVER]:
                COV_RECOVERY.sample(event="rollover")
            if e.l0_leaves:
                COV_RECOVERY.sample(event="retrain")
            if e.rcv_err:
                COV_RECOVERY.sample(event="rcv_err")
        self.log.info("link e2e done: %s", stats)
        return stats
