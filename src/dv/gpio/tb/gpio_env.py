"""cocotb environment of the gpio unit DV (wiki/src/dv/gpio/DV_PLAN.md;
wiki/src/arch/gpio/HAS.md and LINK_UP.md).

Two ``pcie_gpio`` instances on the top ``pcie_gpio_tb``: a (downstream,
``port_role_i`` 0) and b (upstream), each on its own clock (b's at 1/2, 1 or
2 times a's, from the top's RATIO), joined pin to pin by the modelled
wires. One coroutine per clock runs ``End.edge`` at every falling edge of
that end's clock. Each end has:

- a dll-shaped transmit driver: packets of 3-beat DLLPs and 9- to 75-beat
  TLPs (the dll's packet sizes), valid held from sop to eop unless a test
  drops it, offered while the end is up;
- a receive sink that rebuilds packets from ``pkt_rx_*``;
- a wire monitor of its own transmit pins with a model of its transmitter
  from the HAS (``TxModel``): the transfer schedule (one transfer every
  CLK_DIV cycles from the first edge after reset), the slot each slot start
  must send (buffered beats in order, IDLE, TRAIN with bit 8 per the
  quiet run, HOT_RESET, ABORT at link-down), the ready rule (between
  packets ready only up and with the buffer empty: room for a maximum
  packet) and the quiet run and Hot Reset counts;
- a wire injector that overrides whole slots or single transfers of its
  direction, logging every slot as the receiver sees it.

Checks, all from the HAS: the transmitter model above at every slot; one
scoreboard per direction (every packet taken whole while up is delivered
once, in order, byte for byte with no error, unless a test allows
losses); the receive stream contract (every sop has exactly one eop,
nothing delivered while down but the closing error eop, nullify 0); the
status codes (only the four ltssm codes, link_up_o exactly at 0x20); the
pin checker in the top; and the RTL assertions (the receive crossing never
overflows, the buffer never overflows).
"""

from __future__ import annotations

import os
import random
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from cocotb.utils import get_sim_time

from regress.funcov import Covergroup

sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "gpio"))
import pcie_gpio_arch as arch  # noqa: E402  (latency and link-up targets; DV may import arch)
import pcie_gpio_model as gm  # noqa: E402

A_PS = 8000                                  # a's clock period
B_PS = {0: 16000, 1: 8002, 2: 4000}          # b's period per RATIO: 1/2, 1 (250 ppm), 2
B_PHASE_PS = 1370                            # b's first edge, so edges rarely coincide
RATIO_NAMES = {0: "half", 1: "equal", 2: "double"}
RESET_CYCLES = 10

# ------------------------------------------------------------ coverage

COV_CONFIG = Covergroup("gpio_config")
COV_CONFIG.coverpoint("ratio", bins={n: n for n in RATIO_NAMES.values()})
COV_CONFIG.coverpoint("clk_div", bins={"div2": 2, "div4": 4, "other": lambda v: v not in (2, 4)})

COV_SLOT = Covergroup("gpio_slot")
SLOT_BINS = ("TRAIN", "TRAIN_OK", "IDLE", "HOT_RESET", "MID", "SOP_TLP", "SOP_DLLP", "EOP",
             "ABORT")
COV_SLOT.coverpoint("sent", bins={n: n for n in SLOT_BINS})

COV_RX = Covergroup("gpio_rx_table")
RX_BINS = ("marker_zero", "idle_open", "sop_open", "one_open", "one_closed", "mid_closed",
           "eop_closed", "abort_closed", "abort_open", "unknown_word", "discarded", "valid_drop",
           "down_open")
COV_RX.coverpoint("case", bins={n: n for n in RX_BINS})

COV_LINK = Covergroup("gpio_link")
LINK_BINS = ("up", "single_damage", "double_damage", "restart_same_phase", "restart_new_phase",
             "restart_watchdog", "hot_reset", "hot_reset_long", "forged_train", "forged_hot_reset",
             "forged_train_any",
             "sustained_load", "rate_backpressure", "hr_from_training", "loss_in_training",
             "us_hr_from_training", "loss_in_us_hr")
COV_LINK.coverpoint("event", bins={n: n for n in LINK_BINS})
COV_LINK.coverpoint("ratio", bins={n: n for n in RATIO_NAMES.values()})
COV_LINK.cross("event_x_ratio", "event", "ratio")


@dataclass
class Pkt:
    """A packet on one end's transmit stream."""
    beats: list[int]
    dllp: bool
    tag: int = 0
    drop_at: int | None = None       # valid falls for drop_len cycles before this beat
    drop_len: int = 0
    clean: bool = True               # every beat went into the buffer: deliverable whole
    maybe_lost: bool = False         # outstanding when its sender reset: may arrive or not


@dataclass
class SlotCtx:
    """A slot the transmitter started: the expectation at its slot start."""
    start: int                       # falling-edge index of its transfer 0
    t0: float                        # time of the edge that started transfer 0, ps
    want: tuple[int, int] | None     # expected (code, data); None: not checked
    sent: list = field(default_factory=list)    # transfers on the pins
    seen: list = field(default_factory=list)    # transfers the receiver sees


class End:
    """One pcie_gpio instance of the top (``u_a`` or ``u_b``)."""

    def __init__(self, env: "Env", prefix: str, role: int) -> None:
        dut = env.dut
        self.env, self.name, self.role = env, prefix.rstrip("_"), role
        self.dir = "ab" if self.name == "a" else "ba"
        self.s = {n[len(prefix):]: getattr(dut, n) for n in dir(dut) if n.startswith(prefix)}
        # Two-state simulator: read each output as an int from its binary
        # string, and write an input only when it changes.
        self.rd = {n: (lambda f=h._handle.get_signal_val_binstr: int(f(), 2))
                   for n, h in self.s.items()}
        self.ovr = {k: getattr(dut, f"{self.dir}_ovr{k}") for k in ("_i", "_ctrl_i", "_data_i")}
        self.period = A_PS if self.name == "a" else B_PS[env.ratio]
        self.release_t: float | None = None    # time of the first edge out of reset, ps
        self.up_t: list[float] = []
        self._last: dict[str, int] = {}
        self.rng = random.Random(env.rng.getrandbits(32))
        self.cycle = 0
        self.rst = True
        self.release = RESET_CYCLES
        self.reset_at: deque = deque()     # (cycle, length) of scheduled resets
        self.violations: list[str] = []
        # transmit driver
        self.tx_q: deque = deque()
        self.cur: Pkt | None = None
        self.pos = 0
        self.dropping = 0
        self.sent: list[Pkt] = []          # packets taken whole (sop to eop), in order
        self.valid_p = 1.0                 # chance of offering a new packet each cycle
        self.hold_tx = False
        # receive sink
        self.rx_beats: list[int] | None = None
        self.rx_dllp = False
        self.delivered: list[tuple[list[int], bool, bool]] = []   # (beats, dllp, err)
        self.rx_log: list[gm.Beat] = []    # every beat delivered, for model windows
        self.rcv_err = 0
        # status
        self.up = False
        self.ltssm = 0
        self.ups = 0
        self.downs = 0
        self.down_at: list[int] = []
        self.up_at: list[int] = []
        self.ltssm_log: list[tuple[int, int]] = []
        self.hr_entries = 0
        # transmitter model and wire monitor
        self.tx = TxModel(self)
        for name in ("pkt_tx_valid_i", "pkt_tx_data_i", "pkt_tx_sop_i", "pkt_tx_eop_i",
                     "pkt_tx_dllp_i", "hot_reset_req_i"):
            self.s[name].value = 0
        for h in self.ovr.values():
            h.value = 0
        self.s["port_role_i"].value = role
        self.s["rst_i"].value = 1

    def wr(self, name: str, value: int) -> None:
        if self._last.get(name) != value:
            self._last[name] = value
            self.s[name].value = value

    def ovr_wr(self, on: int, ctrl: int = 0, data: int = 0) -> None:
        key = ("ovr", on, ctrl, data)
        if self._last.get("ovr") != key:
            self._last["ovr"] = key
            self.ovr["_i"].value = on
            self.ovr["_ctrl_i"].value = ctrl
            self.ovr["_data_i"].value = data

    @property
    def far(self) -> "End":
        return self.env.ends[1 - self.env.ends.index(self)]

    # -- one falling edge ----------------------------------------------------

    def edge(self) -> None:
        rd = self.rd
        self.cycle += 1
        n = self.cycle
        rst_q = self.rst
        up = bool(rd["link_up_o"]())
        ltssm = rd["ltssm_state_o"]()
        if rst_q and n > 1:
            for port, want in (("pkt_tx_ready_o", 0), ("pkt_rx_valid_o", 0), ("link_up_o", 0),
                               ("ltssm_state_o", 0), ("rcv_err_o", 0), ("pkt_rx_nullify_o", 0)):
                if rd[port]() != want:
                    self.violations.append(f"end {self.name} cycle {n} in reset: {port} "
                                           f"{rd[port]()}, expected {want}")
        if ltssm not in gm.LTSSM_CODES:
            self.violations.append(f"end {self.name} cycle {n}: ltssm_state_o {ltssm:#04x}")
        if up != (ltssm == gm.LTSSM_UP):
            self.violations.append(f"end {self.name} cycle {n}: link_up_o {int(up)} with "
                                   f"ltssm_state_o {ltssm:#04x}")
        # Resets scheduled for this edge.
        if self.reset_at and n >= self.reset_at[0][0]:
            _, length = self.reset_at.popleft()
            self._reset_now(n, length)
        rst_next = n < self.release
        if rst_q:
            self.rx_beats = None             # the receive stream restarts closed
        # Wire monitor and transmitter model: the slot started at the edge
        # just passed is judged on the state sampled before it (self.up,
        # self.ltssm still hold the previous sample).
        self.tx.edge(n, rst_q, rst_next)
        # Receive stream.
        self._sink(n, up)
        self.rcv_err += rd["rcv_err_o"]()
        # Status changes (after the slot: a change made at the edge just
        # passed acts from the next slot).
        if ltssm != self.ltssm:
            if (self.ltssm == gm.LTSSM_RESET and ltssm == gm.LTSSM_TRAINING
                    and not any(c != gm.LTSSM_RESET for _, c in self.ltssm_log)):
                self._check_first_alignment(n)
            self.ltssm_log.append((n, ltssm))
            self.tx.ltssm_change(self.ltssm, ltssm)
            if ltssm == gm.LTSSM_HOT_RESET:
                self.hr_entries += 1
        if up and not self.up:
            # LINK_UP.md, up: only after the end's own quiet run ended.
            if self.tx.quiet < gm.QUIET_WORDS:
                self.violations.append(f"end {self.name} cycle {n}: up after {self.tx.quiet} "
                                       f"quiet TRAIN words, at least {gm.QUIET_WORDS} required")
            self.ups += 1
            self.up_at.append(n)
            self.up_t.append(get_sim_time("ps") - self.period / 2)
        if self.up and not up:
            self.downs += 1
            self.down_at.append(n)
        self.up, self.ltssm = up, ltssm
        # Drive the next cycle.
        if self.rst and not rst_next:
            self.release_t = get_sim_time("ps") + self.period / 2
        self.rst = rst_next
        self.wr("rst_i", int(self.rst))
        self.tx.flush_if_down(up)
        self._drive_tx(n, up)

    def _check_first_alignment(self, n: int) -> None:
        """LINK_UP.md, alignment: the first alignment after reset needs 8
        marker slots received, so the partner started at least 8 slots
        after this end's reset release (less the one in flight then)."""
        far = self.far
        since = max(self.release_t or 0, far.release_t or 0) - 4 * self.env.clk_div * far.period
        sent = sum(1 for sc in far.tx.slots if sc.t0 >= since and len(sc.sent) == 4
                   and gm.decode(sc.sent)[:2] == (1, gm.IDLE))
        if sent < gm.ALIGN_SLOTS:
            self.violations.append(f"end {self.name} cycle {n}: aligned after {sent} marker "
                                   f"slots from the partner, at least {gm.ALIGN_SLOTS} required")

    def _reset_now(self, n: int, length: int) -> None:
        """rst_i high from the next edge for ``length`` cycles."""
        self.release = n + length
        if self.cur is not None and self.pos:
            self.cur.clean = False
        self.cur, self.dropping = None, 0
        for p in self.sent[self.env.sb[self.dir].next:]:
            p.maybe_lost = True      # buffered (lost) or already on the wire (delivered)

    # -- transmit driver -------------------------------------------------------

    def _drive_tx(self, n: int, up: bool) -> None:
        rd = self.rd
        ready = bool(rd["pkt_tx_ready_o"]())
        tx = self.tx
        if self.rst:
            self.wr("pkt_tx_valid_i", 0)
            tx.in_pkt, tx.drop = False, False
            return
        # The ready rule (HAS, transmit), checked against the model.
        if not tx.in_pkt:
            want = up and tx.occupancy() == 0
            if ready != want:
                self.violations.append(f"end {self.name} cycle {n}: pkt_tx_ready_o {int(ready)} "
                                       f"between packets, expected {int(want)} (up {int(up)}, "
                                       f"{tx.occupancy()} beats buffered)")
        elif not ready:
            self.violations.append(f"end {self.name} cycle {n}: pkt_tx_ready_o 0 inside a packet")
        if self.cur is None:
            if self.tx_q and not self.hold_tx and up and (
                    self.valid_p >= 1.0 or self.rng.random() < self.valid_p):
                self.cur, self.pos = self.tx_q.popleft(), 0
            else:
                self.wr("pkt_tx_valid_i", 0)
                return
        p = self.cur
        if self.pos == 0 and not ready:
            # A sop waits for ready; the dll offers it held.
            self._offer(p, 0)
            return
        if self.dropping:
            self.dropping -= 1
            self.wr("pkt_tx_valid_i", 0)
            tx.accept_gap(n, up)
            p.clean = False
            return
        if p.drop_at is not None and self.pos == p.drop_at and p.drop_len:
            self.dropping = p.drop_len - 1
            p.drop_len = 0
            self.wr("pkt_tx_valid_i", 0)
            tx.accept_gap(n, up)
            p.clean = False
            COV_RX.sample(case="valid_drop")
            return
        self._offer(p, self.pos)
        # ready does not depend on valid: the beat is taken at the next edge.
        if not tx.accept(n, up, p, self.pos):
            p.clean = False
        if self.pos == 0:
            self.sent.append(p)
        self.pos += 1
        if self.pos == len(p.beats):
            self.cur = None

    def _offer(self, p: Pkt, i: int) -> None:
        self.wr("pkt_tx_valid_i", 1)
        self.wr("pkt_tx_data_i", p.beats[i])
        self.wr("pkt_tx_sop_i", int(i == 0))
        self.wr("pkt_tx_eop_i", int(i == len(p.beats) - 1))
        self.wr("pkt_tx_dllp_i", int(p.dllp))

    def send(self, pkts) -> None:
        self.tx_q.extend(pkts)

    def tx_idle(self) -> bool:
        return self.cur is None and not self.tx_q

    # -- receive sink ----------------------------------------------------------

    def _sink(self, n: int, up: bool) -> None:
        rd = self.rd
        if rd["pkt_rx_nullify_o"]():
            self.violations.append(f"end {self.name} cycle {n}: pkt_rx_nullify_o 1")
        if not rd["pkt_rx_valid_o"]():
            return
        sop, eop = rd["pkt_rx_sop_o"](), rd["pkt_rx_eop_o"]()
        err, dllp = rd["pkt_rx_err_o"](), rd["pkt_rx_dllp_o"]()
        data = rd["pkt_rx_data_o"]()
        if not up and not (eop and err and not sop):
            self.violations.append(f"end {self.name} cycle {n}: a beat delivered while down "
                                   f"(sop {sop} eop {eop} err {err})")
        self.rx_log.append(gm.Beat(bool(sop), bool(eop), bool(dllp), bool(err), data))
        if sop:
            ends = self.far.tx.sop_end.get(data)
            if ends:
                beat_t = get_sim_time("ps") - self.period / 2
                self.env.lat["l_rx"].append((beat_t - ends.popleft()) / self.period)
            if self.rx_beats is not None:
                self.violations.append(f"end {self.name} cycle {n}: sop inside an open packet")
            self.rx_beats, self.rx_dllp = [], bool(dllp)
        elif self.rx_beats is None:
            self.violations.append(f"end {self.name} cycle {n}: a beat with no packet open")
            return
        elif bool(dllp) != self.rx_dllp:
            self.violations.append(f"end {self.name} cycle {n}: pkt_rx_dllp_o changed in a packet")
        if not (eop and err):
            self.rx_beats.append(data)
        if eop:
            self.delivered.append((self.rx_beats, self.rx_dllp, bool(err)))
            self.env.sb[self.far.dir].deliver(self.rx_beats, self.rx_dllp, bool(err), n)
            self.rx_beats = None


class TxModel:
    """The transmitter of one end, from the HAS, against its pins."""

    def __init__(self, end: End) -> None:
        self.end = end
        self.reset()

    def reset(self) -> None:
        self.buf: deque = deque()          # (code, data, available-from cycle)
        self.in_pkt = False
        self.drop = False
        self.pins_open = False
        self.first = None                  # cycle of the first transfer after reset
        self.slot: SlotCtx | None = None
        self.slots: list[SlotCtx] = []     # every slot, for tests that read the wire
        self.quiet = 0                     # bit-8-clear TRAIN words since the quiet run started
        self.hr_words = 0
        self.inject: dict[int, list] = {}  # slot index -> 4 transfers seen by the receiver
        self.sop_end: dict[int, deque] = {}  # sop beat -> end times of its slots, ps
        self.flip: dict[int, list] = {}    # slot index -> per-transfer (ctrl, data) xor masks

    def occupancy(self) -> int:
        return len(self.buf)

    # -- accept side -----------------------------------------------------------

    def accept(self, n: int, up: bool, p: Pkt, i: int) -> bool:
        """The beat i of p is offered with valid and taken at the next edge;
        returns whether it goes into the buffer."""
        last = i == len(p.beats) - 1
        if i == 0:
            self.in_pkt = not last
            self.drop = False
            self.buf.append((gm.sop_code(p.dllp, last), p.beats[0], n + 3))
            return True
        pushed = up and not self.drop
        if pushed:
            self.buf.append((gm.EOP if last else gm.MID, p.beats[i], n + 3))
        elif not up:
            self.drop = True
        if last:
            self.in_pkt, self.drop = False, False
        return pushed

    def accept_gap(self, n: int, up: bool) -> None:
        """Valid low inside a packet: an ABORT ends it, then the rest drops."""
        if self.in_pkt and not self.drop and up:
            self.buf.append((gm.ABORT, 0, n + 3))
        if self.in_pkt:
            self.drop = True

    def flush_if_down(self, up: bool) -> None:
        if not up:
            self.buf.clear()
            if self.in_pkt:
                self.drop = True

    # -- pins --------------------------------------------------------------------

    def edge(self, n: int, rst_q: bool, rst_next: bool) -> None:
        e = self.end
        if rst_q or rst_next:
            if rst_q:
                self.reset()
                # The first edge out of reset starts transfer 0.
                self.first = None if rst_next else n + 1
            e.ovr_wr(0)
            if rst_q:
                return
        if self.first is None:
            return
        k = n - self.first
        if k < 0:
            return
        div = e.env.clk_div
        tclk = e.rd["gpio_tx_clk_o"]()
        if k % div == 0:
            if tclk:
                e.violations.append(f"end {e.name} cycle {n}: forwarded clock high at a transfer "
                                    "start")
            self._transfer(n, k // div, rst_next)
        elif k % div == div // 2 and not tclk:
            e.violations.append(f"end {e.name} cycle {n}: forwarded clock low at mid-transfer")

    def _transfer(self, n: int, x: int, rst_next: bool) -> None:
        e = self.end
        t = x % 4
        si = x // 4
        if t == 0:
            self.slot = SlotCtx(n, get_sim_time("ps") - e.period / 2, self._expect(n))
            self.slots.append(self.slot)
        ctrl, data = e.rd["gpio_tx_ctrl_o"](), e.rd["gpio_tx_data_o"]()
        self.slot.sent.append((ctrl, data))
        seen = (ctrl, data)
        if si in self.inject:
            seen = self.inject[si][t]
        if si in self.flip:
            fc, fd = self.flip[si][t]
            seen = (seen[0] ^ fc, seen[1] ^ fd)
        if seen != (ctrl, data) and not rst_next:
            e.ovr_wr(1, seen[0], seen[1])
        else:
            e.ovr_wr(0)
        self.slot.seen.append(seen)
        if t == 3:
            self._check(self.slot, si)

    def _expect(self, n: int) -> tuple[int, int] | None:
        """The slot the HAS says starts now (state as at the slot start)."""
        e = self.end
        up = e.up
        hr_ds = e.ltssm == gm.LTSSM_HOT_RESET and e.role == 0
        if self.pins_open and not up:
            self.pins_open = False
            return gm.ABORT, 0
        if up and self.buf and self.buf[0][2] <= n:
            code, data, avail = self.buf.popleft()
            if code in (gm.SOP_TLP, gm.SOP_DLLP, gm.ONE_TLP, gm.ONE_DLLP):
                # A sop is taken only on an empty buffer: it leaves at the
                # first slot start from L_tx after the edge that took it.
                e.env.lat["l_tx"].append(n + 2 - avail)
            if code in (gm.SOP_TLP, gm.SOP_DLLP):
                self.pins_open = True
            elif code in (gm.EOP, gm.ABORT, gm.ONE_TLP, gm.ONE_DLLP):
                self.pins_open = False
            return code, data
        if up:
            if self.pins_open:
                e.violations.append(f"end {e.name} cycle {n}: inside a packet on the pins "
                                    "with no beat buffered")
            return gm.IDLE, gm.IDLE_WORD
        if hr_ds:
            self.hr_words += 1
            return gm.IDLE, gm.HOT_RESET
        aligned = (e.ltssm == gm.LTSSM_TRAINING
                   or (e.ltssm == gm.LTSSM_HOT_RESET and e.role == 1))
        if aligned and self.quiet >= gm.QUIET_WORDS:
            return gm.IDLE, gm.TRAIN_OK
        self.quiet += 1
        return gm.IDLE, gm.TRAIN

    def _check(self, slot: SlotCtx, si: int) -> None:
        e = self.end
        marker, code, data = gm.decode(slot.sent)
        COV_SLOT.sample(sent=gm.slot_name(code, data) if code == gm.IDLE else gm.CODE_NAMES[code])
        if not marker:
            e.violations.append(f"end {e.name} slot {si} (cycle {slot.start}): marker 0 sent")
        if marker and code in (gm.SOP_TLP, gm.SOP_DLLP, gm.ONE_TLP, gm.ONE_DLLP):
            self.sop_end.setdefault(data, deque()).append(
                slot.t0 + 4 * e.env.clk_div * e.period)
        if slot.want is not None and (code, data) != slot.want:
            wc, wd = slot.want
            e.violations.append(f"end {e.name} slot {si} (cycle {slot.start}): sent "
                                f"{gm.slot_name(code, data)} {data:#06x}, expected "
                                f"{gm.slot_name(wc, wd)} {wd:#06x}")

    def ltssm_change(self, old: int, new: int) -> None:
        """Quiet-run starts and the Hot Reset dwell (LINK_UP.md, terms)."""
        e = self.end
        start = False
        if old == gm.LTSSM_UP and new != gm.LTSSM_UP:
            start = not (new == gm.LTSSM_HOT_RESET and e.role == 0)
        elif old == gm.LTSSM_TRAINING and new == gm.LTSSM_RESET:
            start = True                                  # alignment loss
        elif old == gm.LTSSM_TRAINING and new == gm.LTSSM_HOT_RESET:
            start = e.role == 1
        elif old == gm.LTSSM_HOT_RESET:
            if e.role == 0:
                start = True                              # end of the dwell
                if self.hr_words < gm.HR_WORDS:
                    e.violations.append(f"end {e.name} cycle {e.cycle}: left Hot Reset after "
                                        f"{self.hr_words} HOT_RESET words, at least "
                                        f"{gm.HR_WORDS} required")
            else:
                start = new == gm.LTSSM_RESET             # alignment loss
        if new == gm.LTSSM_HOT_RESET and e.role == 0:
            self.hr_words = 0
        if start:
            self.quiet = 0


class Scoreboard:
    """One direction: the packets the sender took whole, against those the
    receiver delivers good, in order and beat for beat."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.sender: End | None = None
        self.next = 0
        self.good = 0
        self.cut = 0
        self.lost = 0
        self.allow_loss = False          # packets taken whole may be lost (link-down, reset)
        self.allow_cut = False           # packets may arrive cut (error eop)
        self.injected: deque = deque()   # (beats, dllp) forged on the wire, delivered good
        self.lost_ok = 0                 # losses allowed before the run turned strict
        self.violations: list[str] = []

    def deliver(self, beats: list[int], dllp: bool, err: bool, n: int) -> None:
        sent = self.sender.sent
        if err:
            self.cut += 1
            if not (self.allow_loss or self.allow_cut):
                self.violations.append(f"{self.name}: a packet delivered with pkt_rx_err_o at "
                                       f"cycle {n} in a run with no cut allowed")
            # In order: the cut packet is the oldest one not yet delivered.
            if self.next < len(sent):
                self.next += 1
            return
        if self.injected and self.injected[0] == (beats, dllp):
            self.injected.popleft()
            self.good += 1
            return
        i = self.next
        while i < len(sent):
            p = sent[i]
            if p.clean and p.beats == beats and p.dllp == dllp:
                break
            if p.clean and not p.maybe_lost and not self.allow_loss:
                i = len(sent)
                break
            i += 1
        if i >= len(sent):
            want = next((p.beats[:4] for p in sent[self.next:] if p.clean), None)

            self.violations.append(f"{self.name}: packet delivered at cycle {n} "
                                   f"({len(beats)} beats, {[hex(b) for b in beats[:4]]}...) is not "
                                   f"the next one sent whole ({want}): lost, reordered, "
                                   "duplicated or corrupted")
            return
        self.lost += sum(1 for p in sent[self.next:i] if p.clean and not p.maybe_lost)
        self.next = i + 1
        self.good += 1

    def strict(self) -> None:
        """From now on nothing may be lost; earlier losses and cuts stay."""
        self.allow_loss, self.allow_cut, self.lost_ok = False, True, self.lost

    def outstanding(self) -> int:
        sent = self.sender.sent
        return sum(1 for p in sent[self.next:] if p.clean and not p.maybe_lost)


# ---------------------------------------------------------------- env

class Env:
    """One pair run; ``seed`` draws everything random."""

    def __init__(self, dut, seed: int) -> None:
        self.dut = dut
        self.log = dut._log
        self.rng = random.Random(seed)
        self.seed = seed
        self.clk_div = int(dut.cfg_clk_div_o.value)
        self.ratio = int(dut.cfg_ratio_o.value)
        self.ratio_name = RATIO_NAMES[self.ratio]
        self.log.info("gpio pair: COCOTB_RANDOM_SEED %s, test seed %d, CLK_DIV %d, b clock %s "
                      "(%d ps against %d ps)", os.environ.get("COCOTB_RANDOM_SEED", "-"), seed,
                      self.clk_div, self.ratio_name, B_PS[self.ratio], A_PS)
        COV_CONFIG.sample(ratio=self.ratio_name, clk_div=self.clk_div)
        self.lat: dict[str, list[float]] = {"l_tx": [], "l_rx": []}
        self.ends = (End(self, "a_", 0), End(self, "b_", 1))
        self.ends[1].release = RESET_CYCLES + self.rng.randrange(40)
        self.sb = {"ab": Scoreboard("ab"), "ba": Scoreboard("ba")}
        self.sb["ab"].sender, self.sb["ba"].sender = self.ends
        self.tag = 0

    # -- running -----------------------------------------------------------------

    def start(self) -> None:
        cocotb.start_soon(Clock(self.dut.a_clk_i, A_PS, unit="ps").start())
        cocotb.start_soon(self._b_clock())
        for i in (0, 1):
            cocotb.start_soon(self._loop(i))

    async def _b_clock(self) -> None:
        self.dut.b_clk_i.value = 0
        await Timer(B_PHASE_PS, "ps")
        await Clock(self.dut.b_clk_i, B_PS[self.ratio], unit="ps").start()

    async def _loop(self, i: int) -> None:
        end = self.ends[i]
        clk = (self.dut.a_clk_i, self.dut.b_clk_i)[i]
        while True:
            await FallingEdge(clk)
            end.edge()

    def raise_violations(self) -> None:
        for e in self.ends:
            assert not e.violations, "; ".join(e.violations[:5])
        for sb in self.sb.values():
            assert not sb.violations, "; ".join(sb.violations[:5])

    async def until(self, cond, limit: int, what: str, poll: int = 8) -> int:
        """Wait in a's cycles until ``cond()``; fail past ``limit``."""
        start = self.ends[0].cycle
        while not cond():
            self.raise_violations()
            n = self.ends[0].cycle - start
            assert n <= limit, (f"{what}: not reached in {limit} cycles (a ltssm "
                                f"{self.ends[0].ltssm:#04x}, b ltssm {self.ends[1].ltssm:#04x}; "
                                f"outstanding ab {self.sb['ab'].outstanding()}, "
                                f"ba {self.sb['ba'].outstanding()})")
            await ClockCycles(self.dut.a_clk_i, poll, rising=False)
        return self.ends[0].cycle - start

    async def cycles(self, n: int) -> None:
        await ClockCycles(self.dut.a_clk_i, n, rising=False)
        self.raise_violations()

    def slot_cycles(self) -> int:
        """One slot of the slower end, in a's cycles."""
        return 4 * self.clk_div * (2 if self.ratio == 0 else 1)

    async def link_up(self, limit: int | None = None, from_reset: bool = True) -> int:
        """Both ends up; the bound is the LINK_UP.md figure: 8 + 16 + 8 slots
        plus 3 W, counted in the slower end's slots, from the later start,
        plus reset and alignment (8 slots), with margin."""
        limit = limit or 80 * self.slot_cycles() + 200
        n = await self.until(lambda: all(e.up for e in self.ends), limit, "both ends up")
        # The arch target (HAS, targets): both up within 8 + 16 + 8 slots
        # plus 3 W of the later reset release, in slots of the slower end.
        if not from_reset:
            return n
        later = max(e.release_t for e in self.ends)
        slot = 4 * self.clk_div * max(e.period for e in self.ends)
        took = (max(e.up_t[-1] for e in self.ends) - later) / slot
        bound = 8 + 16 + 8 + 3 * arch.W_BOUND_SLOTS
        self.log.info("both ends up at a cycle %d (a up at %s, b up at %s): %.2f slots of the "
                      "slower end after the later release (bound %d)", self.ends[0].cycle,
                      self.ends[0].up_at[-1:], self.ends[1].up_at[-1:], took, bound)
        assert took <= bound, f"link-up took {took:.2f} slots of the slower end, bound {bound}"
        COV_LINK.sample(event="up", ratio=self.ratio_name)
        return n

    # -- traffic -------------------------------------------------------------------

    def packets(self, n: int, dllp_p: float = 0.4, beats: int | None = None) -> list[Pkt]:
        """``n`` dll-shaped packets: 3-beat DLLPs and 9- to 75-beat TLPs."""
        out = []
        for _ in range(n):
            self.tag += 1
            dllp = beats is None and self.rng.random() < dllp_p
            if beats is not None:
                ln = beats
            elif dllp:
                ln = 3
            else:
                ln = self.rng.choice((9, 75, self.rng.randint(9, 75)))
            data = [self.rng.getrandbits(16) for _ in range(ln)]
            data[0] = (data[0] & 0xFF00) | (self.tag & 0xFF)
            out.append(Pkt(data, dllp, self.tag))
        return out

    def send(self, i: int, n: int, **kw) -> None:
        self.ends[i].send(self.packets(n, **kw))

    def drained(self) -> bool:
        return all(e.tx_idle() for e in self.ends) and all(
            sb.outstanding() == 0 for sb in self.sb.values())

    async def drain(self, limit: int = 400_000) -> int:
        return await self.until(self.drained, limit, "traffic drain")

    # -- wire injection -----------------------------------------------------------

    def next_slot(self, d: str, ahead: int = 2) -> int:
        """Index of a slot of direction ``d`` that has not started yet."""
        tx = self.ends[0 if d == "ab" else 1].tx
        return len(tx.slots) + ahead

    def replace(self, d: str, si: int, code: int, data: int, marker: int = 1) -> None:
        """The receiver of ``d`` sees slot ``si`` as (marker, code, data)."""
        xs = gm.encode(code, data)
        xs[0] = (marker, xs[0][1])
        self.ends[0 if d == "ab" else 1].tx.inject[si] = xs

    def damage(self, d: str, si: int) -> None:
        """Slot ``si`` of ``d`` arrives with its marker read as 0."""
        self.ends[0 if d == "ab" else 1].tx.flip[si] = [(1, 0), (0, 0), (0, 0), (0, 0)]

    def slip(self, d: str, si: int) -> None:
        """Slot ``si`` of ``d``: a stray marker in transfer 2 and a marker
        lost in transfer 0 (damage that a receiver cannot align on)."""
        self.ends[0 if d == "ab" else 1].tx.flip[si] = [(1, 0), (0, 0), (1, 0), (0, 0)]

    async def at_slot(self, d: str, si: int, limit: int = 20_000) -> None:
        tx = self.ends[0 if d == "ab" else 1].tx
        await self.until(lambda: len(tx.slots) > si, limit, f"slot {si} of {d}", poll=1)

    def model_window(self, d: str, si0: int, si1: int) -> tuple[int, list[gm.Beat]]:
        """The HAS receive table (models/) over slots si0 to si1 - 1 of ``d``
        as its receiver saw them, from a closed state with the link up:
        (rcv_err pulses, beats delivered)."""
        tx = self.ends[0 if d == "ab" else 1].tx
        table, pulses, beats = gm.RxTable(), 0, []
        for sc in tx.slots[si0:si1]:
            r = table.slot(*gm.decode(sc.seen))
            pulses += r.pulse
            if r.beat is not None:
                beats.append(r.beat)
        return pulses, beats

    def find_slot(self, d: str, since: int, codes) -> int:
        """Index of the first slot of ``d`` from ``since`` sent with a code in ``codes``."""
        tx = self.ends[0 if d == "ab" else 1].tx
        for i in range(since, len(tx.slots)):
            if len(tx.slots[i].sent) == 4 and gm.decode(tx.slots[i].sent)[1] in codes:
                return i
        return -1

    def reset(self, i: int, length: int, phase: int | None = None) -> None:
        """Reset end i for ``length`` cycles. ``phase`` (0 to 3) picks the
        slot transfer at whose start rst_i is first sampled high: 0 is the
        slot boundary (the partner resumes at the same slot phase), 1 to 3
        cut a slot (a new phase); None resets now."""
        e = self.ends[i]
        if phase is None:
            e.reset_at.append((e.cycle, length))
            return
        div = self.clk_div
        k = e.cycle + 1 - e.tx.first          # transfers count from first
        x = (k + div - 1) // div + 4
        x += (phase - x) % 4
        # rst_i driven at falling edge f is sampled at the edge before f + 1,
        # where transfer x would start: f + 1 = first + x * div.
        e.reset_at.append((e.tx.first + x * div - 1, length))

    def check_latency(self) -> None:
        """The HAS latency targets (arch model): L_tx is 2 cycles plus the
        wait for the next slot start (at most S - 1); L_rx, in receiver
        cycles, at most the arch model's bound for the sender's clock."""
        s = 4 * self.clk_div
        for v in self.lat["l_tx"]:
            assert arch.L_TX <= v <= arch.L_TX + s - 1, (
                f"L_tx {v} cycles: expected {arch.L_TX} plus a slot-phase wait under {s}")
        a, b = self.ends
        bound = max(arch.l_rx(self.clk_div, a.period / b.period),
                    arch.l_rx(self.clk_div, b.period / a.period))
        for v in self.lat["l_rx"]:
            assert v <= bound + 1e-6, f"L_rx {v:.2f} receiver cycles, arch bound {bound:.2f}"

    def finish(self) -> dict:
        self.raise_violations()
        for d, sb in self.sb.items():
            if not sb.allow_loss:
                assert sb.outstanding() == 0, f"{d}: {sb.outstanding()} packets not delivered"
                assert sb.lost == sb.lost_ok, f"{d}: lost {sb.lost - sb.lost_ok}"
            if not (sb.allow_loss or sb.allow_cut):
                assert sb.cut == 0, f"{d}: cut {sb.cut}"
        self.check_latency()
        stats = {
            "l_tx": (min(self.lat["l_tx"], default=0), max(self.lat["l_tx"], default=0)),
            "l_rx_max": round(max(self.lat["l_rx"], default=0), 2),
            "delivered": {d: sb.good for d, sb in self.sb.items()},
            "cut": {d: sb.cut for d, sb in self.sb.items()},
            "lost": {d: sb.lost for d, sb in self.sb.items()},
            "rcv_err": {e.name: e.rcv_err for e in self.ends},
            "downs": {e.name: e.downs for e in self.ends},
            "slots": {e.name: len(e.tx.slots) for e in self.ends},
        }
        self.log.info("gpio pair done: %s", stats)
        return stats
