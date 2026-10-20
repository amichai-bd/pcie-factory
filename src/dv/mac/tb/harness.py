"""Testbench of pcie_mac against the link partner (wiki/src/dv/mac/DV_PLAN.md,
Strategy). The DUT is driven only through its ports.

One cycle, at the falling edge of clk_i after rising edge k:

1. Read the DUT's outputs (all registered but ``pkt_tx_ready_o``, which
   depends only on registers and ``rst_i``) and the observation bus.
2. Step the **shadow**, a ``pcie_mac_arch.Mac`` in the DUT's role, with the
   PIPE word the DUT received before edge k-1 (the mac's receive stage,
   MAS, Top), the requests and reset the DUT sampled at edge k, and the
   packets the upper layer offers. Compare every cycle: ``ltssm_state_o``,
   ``link_up_o``, ``pipe_reset_no`` and the partner's N_FTS with the
   shadow's step; the other PIPE outputs and the transmitted symbols with
   the shadow's previous step (the transmit stage); ``rcv_err_o`` with the
   shadow's receiver errors of the step; every delivered packet with the
   shadow's received packets. Nothing ever injects into the shadow.
3. Check the registered ordered-set receive events and TS fields
   (observation bus) against ``RxDecoder``, an independent decoder of the
   same word written from the symbols page.
4. Step the partner (a ``Mac`` in the other role, the only thing the tests
   steer) and the ``Link`` with the DUT's PIPE word; drive the DUT's next
   inputs, optionally rewritten by a test's line hook.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "mac"))
import pcie_mac_arch as arch  # noqa: E402

# The states the mac implements today (Detect, Polling, Configuration, L0,
# Recovery, and the Off/L0 states of the sub-machines; the power states, L0s
# sub-states, L1 and L2; the control states) and the arcs of the HAS arc list
# among them: all of it.
CORE_STATES = {arch.DQ, arch.DA, arch.PA, arch.PC, arch.PCFG, arch.LWS, arch.LWA,
               arch.LNW, arch.LNA, arch.CC, arch.CI, arch.L0, arch.RL, arch.RC,
               arch.RI, arch.TOFF, arch.TL0, arch.ROFF, arch.RL0}
POWER_STATES = {arch.L1E, arch.L1I, arch.L2I, arch.TE, arch.TI, arch.TF,
                arch.RE, arch.RXI, arch.RF}
CORE_ARCS = {name: arc for name, arc in arch.ARCS.items()
             if arc[0] in CORE_STATES | POWER_STATES and arc[1] in CORE_STATES | POWER_STATES}
CONTROL_STATES = {arch.DIS, arch.HR, arch.LBE, arch.LBA, arch.LBX}
CONTROL_ARCS = {name: arc for name, arc in arch.ARCS.items()
                if (arc[0] in CONTROL_STATES or arc[1] in CONTROL_STATES)}
DONE_ARCS = {**CORE_ARCS, **CONTROL_ARCS}
assert DONE_ARCS == arch.ARCS, "the harness arc sets must cover the HAS arc list"

COV_ARCS = Covergroup("mac_ltssm_arcs")
COV_ARCS.coverpoint("arc", bins=dict(DONE_ARCS))
COV_ARCS.coverpoint("port_role", bins={r: r for r in arch.ROLES})
COV_ARCS.cross("arc_x_port_role", "arc", "port_role")

COV_OS_TX = Covergroup("mac_os_tx")
COV_OS_TX.coverpoint("kind", bins=["TS1", "TS2", "SKP", "compliance", "EIOS", "FTS"])
COV_OS_TX.coverpoint("ctl", bins={"none": 0, "hot_reset": arch.CTL_HOT_RESET,
                                  "disable": arch.CTL_DISABLE, "loopback": arch.CTL_LOOPBACK})
COV_OS_TX.coverpoint("link", bins={"pad": "pad", "zero": 0, "mid": range(1, 255), "max": 255})
COV_OS_TX.coverpoint("lane", bins={"pad": "pad", "zero": 0})
COV_OS_TX.coverpoint("skp_burst", bins=["single", "back_to_back", "after_fts"])

COV_OS_RX = Covergroup("mac_os_rx")
COV_OS_RX.coverpoint("event", bins=["TS1", "TS2", "TS1_inv", "TS2_inv", "SKP", "EIOS", "FTS"])
COV_OS_RX.coverpoint("phase", bins=[0, 1])
COV_OS_RX.cross("event_x_phase", "event", "phase")
COV_OS_RX.coverpoint("ts_bad", bins=["cut", "mismatch", "k_id", "unknown"])
COV_OS_RX.coverpoint("link_kind", bins=["pad", "num", "other_k"])
COV_OS_RX.coverpoint("lane_kind", bins=["pad", "num", "other_k"])
COV_OS_RX.coverpoint("link", bins={"zero": 0, "mid": range(1, 255), "max": 255})
COV_OS_RX.coverpoint("n_fts", bins={"zero": 0, "mid": range(1, 255), "max": 255})
COV_OS_RX.coverpoint("ctl_bit", bins=["hot_reset", "disable", "loopback", "no_scramble",
                                      "compliance_rx"])

# Control states (wiki/src/arch/mac/ARCS.md#control-states): how the DUT
# entered each (directed or following, master or slave) in each role, how
# a Disabled port left, which request took it out of L0, and Disable
# Scrambling in each role.
COV_CTL = Covergroup("mac_control")
COV_CTL.coverpoint("entry", bins=[
    "hot_reset_directed_downstream", "hot_reset_following_downstream",
    "hot_reset_following_upstream", "disabled_directed_downstream",
    "disabled_following_downstream", "disabled_following_upstream",
    "loopback_master_downstream", "loopback_master_upstream",
    "loopback_slave_downstream", "loopback_slave_upstream"])
COV_CTL.coverpoint("disabled_exit", bins=["directed", "following_eios", "following_timeout"])
COV_CTL.coverpoint("l0_exit_request", bins=["hot_reset", "disable", "loopback"])
COV_CTL.coverpoint("scrambling_off", bins=list(arch.ROLES))

# A following Hot Reset's dwell restarted by a TS1 at the cycles around its
# timeout (the LTSSM's registered timeout, MAS Timing): the restart in the
# step whose timer is limit - 3, limit - 2 or limit - 1 (the timeout step).
COV_HR_DWELL = Covergroup("mac_hr_dwell")
COV_HR_DWELL.coverpoint("restart_at", bins=["limit_minus_3", "limit_minus_2", "limit_minus_1"])

COV_PKT = Covergroup("mac_frame")
COV_PKT.coverpoint("tx_outcome", bins=["tlp_end", "tlp_nullify", "dllp_end",
                                       "dllp_nullify_ignored", "abort_l0_exit",
                                       "abort_bubble"])
COV_PKT.coverpoint("tx_size", bins={"one_beat": 2, "short": range(4, 64),
                                    "long": range(64, 1180),
                                    "over_skp_interval": range(1180, 8192)})
COV_PKT.coverpoint("rx_outcome", bins=["good", "nullify", "err"])
COV_PKT.coverpoint("rx_kind", bins=["tlp", "dllp"])
COV_PKT.coverpoint("rx_lane", bins=[0, 1])
COV_PKT.coverpoint("rx_latency", bins=["3_lane0", "3_lane1", "4_lane1", "4_lane0", "4_lane1_edb"])
COV_PKT.cross("rx_kind_x_lane", "rx_kind", "rx_lane")
COV_PKT.coverpoint("rx_error", bins=["framing", "status", "cut_valid", "cut_elec_idle",
                                     "cut_rx_l0", "empty", "idle_tail", "idle_tail_out"])

# The packed buses of tb/pcie_mac_tb.sv, first field in the most
# significant bits: (name, width).
FIELDS_IN = (("pkt_tx_valid_i", 1), ("pkt_tx_data_i", 16), ("pkt_tx_sop_i", 1),
             ("pkt_tx_eop_i", 1), ("pkt_tx_dllp_i", 1), ("pkt_tx_nullify_i", 1),
             ("retrain_req_i", 1), ("hot_reset_req_i", 1), ("disable_req_i", 1),
             ("loopback_req_i", 1), ("pm_state_req_i", 2), ("pipe_rx_data_i", 16),
             ("pipe_rx_datak_i", 2), ("pipe_rx_valid_i", 1), ("pipe_phy_status_i", 1),
             ("pipe_rx_elec_idle_i", 1), ("pipe_rx_status_i", 3))
FIELDS_OUT = (("pkt_tx_ready_o", 1), ("pkt_rx_valid_o", 1), ("pkt_rx_data_o", 16),
              ("pkt_rx_sop_o", 1), ("pkt_rx_eop_o", 1), ("pkt_rx_dllp_o", 1),
              ("pkt_rx_err_o", 1), ("pkt_rx_nullify_o", 1), ("link_up_o", 1),
              ("ltssm_state_o", 6), ("rcv_err_o", 1), ("pipe_reset_no", 1),
              ("pipe_tx_data_o", 16), ("pipe_tx_datak_o", 2), ("pipe_tx_detect_rx_o", 1),
              ("pipe_tx_elec_idle_o", 1), ("pipe_tx_compliance_o", 1),
              ("pipe_rx_polarity_o", 1), ("pipe_power_down_o", 2))
# os_rx_q (t_os_rx), os_ts_q (t_ts_rx), partner_n_fts.
FIELDS_OBS = (("os_sym", 2), ("ts", 1), ("ts_bad", 1), ("skp", 1), ("eios", 1), ("fts", 1),
              ("ts2", 1), ("ts_inv", 1), ("link", 8), ("link_pad", 1), ("link_num", 1),
              ("lane", 8), ("lane_pad", 1), ("lane_num", 1), ("n_fts", 8), ("ctl", 5),
              ("partner_n_fts", 8))
REQUESTS = ("retrain_req_i", "hot_reset_req_i", "disable_req_i", "loopback_req_i",
            "pm_state_req_i")
TS_FIELDS = ("ts2", "ts_inv", "link", "link_pad", "link_num", "lane", "lane_pad",
             "lane_num", "n_fts", "ctl")
EVENTS = ("os_sym", "ts", "ts_bad", "skp", "eios", "fts")

CODE_STATE = {c: n for n, _, c in arch.MAIN_STATES}
SUB_STATES = {n for n, _, _ in arch.TX_STATES + arch.RX_STATES}
TX_SUB = {c: n for n, _, c in arch.TX_STATES if c is not None}
RX_SUB = {c: n for n, _, c in arch.RX_STATES if c is not None}

PAD = 0xF7
TS_IDS = {0x4A: (False, False), 0x45: (True, False),      # (ts2, inverted)
          0x4A ^ 0xFF: (False, True), 0x45 ^ 0xFF: (True, True)}
CTL_BITS = ("hot_reset", "disable", "loopback", "no_scramble", "compliance_rx")


def _layout(fields):
    out, pos = [], sum(w for _, w in fields)
    for name, w in fields:
        pos -= w
        out.append((name, pos, (1 << w) - 1))
    return out


LAYOUT_IN = _layout(FIELDS_IN)
LAYOUT_OUT = _layout(FIELDS_OUT)
LAYOUT_OBS = _layout(FIELDS_OBS)


def decode(code: int) -> tuple[str, str, str]:
    """(main, tx, rx) state names of an ltssm_state_o code."""
    if code & 0x30 == 0x20:
        return arch.L0, TX_SUB[(code >> 2) & 3], RX_SUB[code & 3]
    if code not in CODE_STATE:
        raise AssertionError(f"ltssm_state_o {code:#04x} is not a code of the encoding")
    return CODE_STATE[code], arch.TOFF, arch.ROFF


# ------------------------------------------------------------- references

class RxDecoder:
    """Ordered-set receive events of each word, written from the symbols
    page (wiki/src/arch/mac/SYMBOLS.md#ordered-sets): what the mac's
    parser must report for the word, its fields with a TS, and the reason
    a TS failed (for coverage)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._p = 0
        self._ts: list = []

    def word(self, rx) -> dict:
        ev = {k: 0 for k in EVENTS}
        ev.update({k: 0 for k in TS_FIELDS})
        ev["why"] = None
        ev["phase"] = None
        if rx.valid and not rx.elec_idle:
            for i, (k, b) in enumerate(rx.syms):
                self._sym(i, k, b, ev)
        return ev

    def _end_ts(self, ev, ts, why=None) -> None:
        assert not (ev["ts"] or ev["ts_bad"]), "two TS ended in one word"
        if ts is None:
            ev["ts_bad"], ev["why"] = 1, why
            return
        ids = ts[5:]
        first = ids[0]
        if first[0]:
            ev["ts_bad"], ev["why"] = 1, "k_id"
        elif any(s != first for s in ids):
            ev["ts_bad"], ev["why"] = 1, "mismatch"
        elif first[1] not in TS_IDS:
            ev["ts_bad"], ev["why"] = 1, "unknown"
        if ev["ts_bad"]:
            return
        ts2, inv = TS_IDS[first[1]]
        ev["phase"] = self._ts_phase
        link, lane = ts[0], ts[1]
        ev.update(ts=1, ts_inv=int(inv), ts2=int(ts2), link=link[1],
                  link_pad=int(link == (1, PAD)), link_num=int(not link[0]),
                  lane=lane[1], lane_pad=int(lane == (1, PAD)), lane_num=int(not lane[0]),
                  n_fts=ts[2][1], ctl=ts[4][1] & 0x1F)

    def _sym(self, i, k, b, ev) -> None:
        bit = 1 << i
        if k and b == 0xBC:
            ev["os_sym"] |= bit
            if self._p == 2:
                self._end_ts(ev, None, "cut")
            self._p, self._com = 1, i
            return
        p = self._p
        if p == 1:
            ev["os_sym"] |= bit
            ev["phase"] = self._com
            if k and b == 0x1C:
                self._p, ev["skp"] = 3, 1
            elif k and b == 0x7C:
                self._p, ev["eios"] = 4, 1
            elif k and b == 0x3C:
                self._p, ev["fts"] = 4, 1
            else:
                self._p, self._ts, self._ts_phase = 2, [(k, b)], self._com
            return
        if p == 2:
            ev["os_sym"] |= bit
            self._ts.append((k, b))
            if len(self._ts) == 15:
                self._p = 0
                self._end_ts(ev, self._ts)
            return
        if p == 3 and k and b == 0x1C:
            ev["os_sym"] |= bit
            return
        if p == 4 and k and b in (0x7C, 0x3C):
            ev["os_sym"] |= bit
            return
        self._p = 0


class TxMonitor:
    """The DUT's transmitted symbols, decoded for coverage: the ordered sets
    it starts and the TS fields it sends (TS symbols are not scrambled)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._p = 0
        self._ts: list = []
        self._last_skp_end = None
        self._last_kind = None
        self.n = 0
        self.syms: deque = deque(maxlen=4096)

    def word(self, syms, compliance: bool) -> None:
        for k, b in syms:
            self.syms.append((k, b))
            self.n += 1
            if self._p == 2:
                self._ts.append((k, b))
                if len(self._ts) == 15:
                    self._p = 0
                    link, lane, ident = self._ts[0], self._ts[1], self._ts[5]
                    COV_OS_TX.sample(kind="TS2" if ident == (0, 0x45) else "TS1",
                                     link="pad" if link == (1, PAD) else link[1],
                                     lane="pad" if lane == (1, PAD) else lane[1],
                                     ctl=self._ts[4][1])
                continue
            if self._p == 1:
                self._p = 0
                if k and b == 0x1C:
                    burst = "back_to_back" if self._last_skp_end == self.n - 2 else "single"
                    if self._last_kind == "FTS":
                        burst = "after_fts"
                    COV_OS_TX.sample(kind="SKP", skp_burst=burst)
                    self._p, self._last_kind = 3, "SKP"
                elif k and b in (0x7C, 0x3C):
                    self._last_kind = "EIOS" if b == 0x7C else "FTS"
                    COV_OS_TX.sample(kind=self._last_kind)
                elif not k and b == arch.D21_5:
                    COV_OS_TX.sample(kind="compliance")
                elif not k or b == PAD:
                    self._p, self._ts, self._last_kind = 2, [(k, b)], "TS"
                continue
            if self._p == 3:
                if k and b == 0x1C:
                    continue
                self._last_skp_end = self.n - 1
                self._p = 0
            if k and b == 0xBC:
                self._p = 1


class Dut:
    """The DUT as the recipes see it: its state from ltssm_state_o, the
    arcs it took, and its role."""

    def __init__(self) -> None:
        self.state, self.tx_sub, self.rx_sub = arch.DQ, arch.TOFF, arch.ROFF
        self.arcs: list[tuple[int, str, str]] = []
        self.role = "downstream"
        self.dsp = True
        self.link_up = False


class Upper:
    """The data-link stub on the DUT's upper stream. Transmit: offers queued
    ``arch.Packet``s beat by beat, keeping valid from sop to eop unless a
    bubble is scripted, with ``gap`` cycles between packets. Receive:
    assembles beats into packets and checks the beat rules."""

    def __init__(self) -> None:
        self.txq: deque = deque()
        self.cur = None          # (packet, beat index, bubble beat or None)
        self.started = False
        self.gap = 0
        self.sent: list = []     # (packet, outcome) as accepted
        self.rx_beats: list = []
        self.rx_open = False
        self.rx_dllp = 0
        self.got: list = []      # (dllp, bytes, err, nullify)

    def drive(self, ready: int, rst_next: int) -> tuple[dict, object]:
        """The transmit inputs for the next cycle; returns them and the packet
        whose sop is offered for the first time (for the shadow)."""
        new = None
        if self.cur is None and self.txq and self.gap <= 0:
            pkt, bubble = self.txq.popleft()
            self.cur = [pkt, 0, bubble]
            self.started = False
            new = pkt
        self.gap -= 1
        f = {"pkt_tx_valid_i": 0, "pkt_tx_data_i": 0, "pkt_tx_sop_i": 0, "pkt_tx_eop_i": 0,
             "pkt_tx_dllp_i": 0, "pkt_tx_nullify_i": 0}
        if self.cur is None:
            return f, None
        pkt, beat, bubble = self.cur
        nbeats = (len(pkt.data) + 1) // 2
        if bubble is not None and beat == bubble:
            self.cur[2] = None   # one bubble cycle
            return f, None
        lo = pkt.data[2 * beat]
        hi = pkt.data[2 * beat + 1] if 2 * beat + 1 < len(pkt.data) else 0
        f.update(pkt_tx_valid_i=1, pkt_tx_data_i=lo | hi << 8, pkt_tx_sop_i=int(beat == 0),
                 pkt_tx_eop_i=int(beat == nbeats - 1), pkt_tx_dllp_i=int(pkt.dllp),
                 pkt_tx_nullify_i=int(pkt.nullify and beat == nbeats - 1))
        self._offer = (ready and not rst_next)
        return f, new

    def accepted(self) -> bool:
        """Called after ``drive``: the offered beat is taken at the next edge."""
        if self.cur is None or not getattr(self, "_offer", False):
            return False
        self._offer = False
        pkt, beat, _ = self.cur
        self.cur[1] += 1
        if self.cur[1] == (len(pkt.data) + 1) // 2:
            self.sent.append(pkt)
            self.cur = None
            COV_PKT.sample(tx_size=len(pkt.data))
        return True

    def receive(self, o: dict, where, now: int = 0) -> object:
        """Take one cycle of receive outputs; returns a finished packet.
        ``where()`` names the cycle, formatted only for a failure message."""
        if not o["pkt_rx_valid_o"]:
            assert not (o["pkt_rx_sop_o"] or o["pkt_rx_eop_o"] or o["pkt_rx_err_o"]
                        or o["pkt_rx_nullify_o"]), f"{where()}: receive flags without valid"
            return None
        if o["pkt_rx_sop_o"]:
            assert not self.rx_open, f"{where()}: sop inside a packet"
            self.rx_open, self.rx_beats, self.rx_dllp = True, [], o["pkt_rx_dllp_o"]
            self.rx_sop_at = now
        assert self.rx_open, f"{where()}: a beat outside a packet"
        assert o["pkt_rx_dllp_o"] == self.rx_dllp, f"{where()}: dllp changed inside a packet"
        if not o["pkt_rx_eop_o"]:
            assert not (o["pkt_rx_err_o"] or o["pkt_rx_nullify_o"]), (
                f"{where()}: err or nullify without eop")
        assert not (o["pkt_rx_err_o"] and o["pkt_rx_nullify_o"]), f"{where()}: err and nullify"
        d = o["pkt_rx_data_o"]
        self.rx_beats += [d & 0xFF, d >> 8]
        if not o["pkt_rx_eop_o"]:
            return None
        self.rx_open = False
        got = (bool(self.rx_dllp), bytes(self.rx_beats), bool(o["pkt_rx_err_o"]),
               bool(o["pkt_rx_nullify_o"]), self.rx_sop_at)
        self.got.append(got)
        return got


# ---------------------------------------------------------------- harness

class Harness:
    def __init__(self, dut, timeout_div: int = 1024, err_timeout_div: int = 1024) -> None:
        self.dut = dut
        self.timeout_div = timeout_div
        self.err_timeout_div = err_timeout_div
        self.n_fts = int(dut.cfg_n_fts_o.value)
        self._bus_get: dict = {}
        self._fall = FallingEdge(dut.clk_i)
        self.a = Dut()
        self.now = 0
        self.log = dut._log
        self.checks = 0
        self.compare_tx = True      # transmitted symbols against the shadow
        self.line_hook = None       # rewrites the DUT's received PipeRx
        self.upper = Upper()
        self.decoder = RxDecoder()
        self.txmon = TxMonitor()
        self.rcv_pulses = 0
        self.rx_matched = 0
        self.abort_kind = "abort_l0_exit"
        self.ctl_directed = False
        self.ctl_entered = 0

    async def start(self) -> None:
        self.dut.rst_i.value = 1
        self.dut.port_role_i.value = 0
        self.inputs = {n: 0 for n, _ in FIELDS_IN}
        self._last_in = None
        self._drive_rx(arch.PipeRx())
        self._write()
        cocotb.start_soon(Clock(self.dut.clk_i, 8, unit="ns").start())
        await FallingEdge(self.dut.clk_i)

    # --------------------------------------------------------------- setup

    async def fresh(self, role: str, skew: int = 0, delay: int = 12,
                    partner_link: int = 0, reset_cycles: int = 8) -> None:
        """Reset the DUT in ``role`` with a new partner, shadow and link;
        the partner leaves reset ``skew`` cycles after the DUT."""
        other = "upstream" if role == "downstream" else "downstream"
        self.role = role
        self.a.role, self.a.dsp = role, role == "downstream"
        # The partner advertises the DUT's N_FTS when the DUT is downstream
        # and the default when it is upstream, so an N_FTS variant sends
        # both counts of FTS on its own L0s exits.
        self.partner = arch.Mac(other, self.timeout_div, self.err_timeout_div,
                                link_number=partner_link,
                                n_fts=self.n_fts if role == "downstream" else arch.N_FTS_DEFAULT)
        self.shadow = arch.Mac(role, self.timeout_div, self.err_timeout_div,
                               n_fts=self.n_fts)
        self.link = arch.Link(delay)
        self.rx_dut = arch.PipeRx()         # the word the DUT sampled at the last edge
        self.rx_prev = arch.PipeRx()        # the word before it (the shadow's input)
        self.rx_partner = arch.PipeRx()
        self.req = {n: 0 for n in REQUESTS}
        self.drive_req = dict(self.req)
        self.rst = 1
        self.release = (reset_cycles, reset_cycles + skew)
        self.t0 = self.now
        self.sh_tx = arch.PipeTx(reset_n=False)
        self.upper = Upper()
        self.decoder.reset()
        self.txmon.reset()
        self.compare_tx = True
        self.line_hook = None
        self.rx_matched = 0
        self.sh_starts: list = []
        self.sh_open = None
        self.sh_pkt = None
        self.dut.port_role_i.value = int(role == "upstream")
        for name in REQUESTS:
            self.inputs[name] = 0
        self._drive_rx(arch.PipeRx())
        self._write()
        self.dut.rst_i.value = 1
        await FallingEdge(self.dut.clk_i)
        await FallingEdge(self.dut.clk_i)
        self.rst_sampled = 1
        self.a.state, self.a.tx_sub, self.a.rx_sub = arch.DQ, arch.TOFF, arch.ROFF

    # ------------------------------------------------------------ requests

    def request(self, **kw) -> None:
        """Drive the DUT's own link-control inputs (``retrain`` is a one-
        cycle pulse)."""
        for k, v in kw.items():
            self.drive_req[f"{k}_req_i" if k != "pm_state" else "pm_state_req_i"] = int(v)

    def send(self, pkt, bubble=None, gap: int = 0) -> None:
        """Queue a packet on the DUT's transmit stream; ``bubble`` drops
        valid before that beat for one cycle (an abort)."""
        self.upper.txq.append((pkt, bubble))
        self.upper.gap = max(self.upper.gap, gap)

    # ---------------------------------------------------------------- step

    def _read(self, fields=LAYOUT_OUT, bus="out_o") -> dict:
        # Each bus as an int straight from its binary string, read every
        # cycle (the simulator is two-state, so every bit is 0 or 1).
        get = self._bus_get.get(bus)
        if get is None:
            get = self._bus_get[bus] = getattr(self.dut, bus)._handle.get_signal_val_binstr
        v = int(get(), 2)
        return {n: (v >> pos) & mask for n, pos, mask in fields}

    def _write(self) -> None:
        f = self.inputs
        v = 0
        for n, pos, mask in LAYOUT_IN:
            v |= (f[n] & mask) << pos
        if v != self._last_in:
            self.dut.in_i.value = v
            self._last_in = v

    def _drive_rx(self, rx) -> None:
        f = self.inputs
        f["pipe_phy_status_i"] = int(rx.phy_status)
        f["pipe_rx_elec_idle_i"] = int(rx.elec_idle)
        f["pipe_rx_status_i"] = rx.status
        f["pipe_rx_valid_i"] = int(rx.valid)
        (k0, b0), (k1, b1) = rx.syms
        f["pipe_rx_data_i"] = b0 | b1 << 8
        f["pipe_rx_datak_i"] = k0 | k1 << 1

    def where(self) -> str:
        return f"cycle {self.now - self.t0} ({self.role}, shadow in {self.shadow.state})"

    def _compare(self, o: dict, obs: dict, sh, sh_tx, prev_tx, errs: int) -> None:
        want = {"ltssm_state_o": sh.code, "link_up_o": int(sh.link_up),
                "pipe_reset_no": int(sh_tx.reset_n)}
        if not self.rst_sampled:
            want["partner_n_fts"] = sh.partner_n_fts
        want.update({"pipe_tx_elec_idle_o": int(prev_tx.elec_idle),
                     "pipe_tx_detect_rx_o": int(prev_tx.detect_rx),
                     "pipe_rx_polarity_o": int(prev_tx.polarity),
                     "pipe_power_down_o": prev_tx.power_down,
                     "pipe_tx_compliance_o": int(prev_tx.compliance),
                     "rcv_err_o": int(errs > 0)})
        for k, v in want.items():
            got = obs[k] if k == "partner_n_fts" else o[k]
            if got != v:
                raise AssertionError(f"{self.where()}: {k} expected {v:#x} got {got:#x}")
        if not prev_tx.elec_idle and prev_tx.reset_n and self.compare_tx:
            d, dk = o["pipe_tx_data_o"], o["pipe_tx_datak_o"]
            syms = ((dk & 1, d & 0xFF), (dk >> 1, d >> 8))
            if syms != tuple(prev_tx.syms):
                raise AssertionError(f"{self.where()}: transmitted symbols expected "
                                     f"{prev_tx.syms} got {syms}")
        self.checks += 1

    def _check_events(self, obs: dict, ev: dict) -> None:
        for k in EVENTS:
            if obs[k] != ev[k]:
                raise AssertionError(f"{self.where()}: ordered-set event {k} expected {ev[k]} "
                                     f"got {obs[k]}")
        if ev["ts"]:
            for k in TS_FIELDS:
                if obs[k] != ev[k]:
                    raise AssertionError(f"{self.where()}: received TS field {k} expected "
                                         f"{ev[k]:#x} got {obs[k]:#x}")
            kind = ("TS2" if ev["ts2"] else "TS1") + ("_inv" if ev["ts_inv"] else "")
            COV_OS_RX.sample(event=kind, phase=ev["phase"])
            kinds = {"link_kind": (ev["link_pad"], ev["link_num"]),
                     "lane_kind": (ev["lane_pad"], ev["lane_num"])}
            for name, (pad, num) in kinds.items():
                COV_OS_RX.sample(**{name: "pad" if pad else ("num" if num else "other_k")})
            if ev["link_num"]:
                COV_OS_RX.sample(link=ev["link"])
            COV_OS_RX.sample(n_fts=ev["n_fts"])
            for i, name in enumerate(CTL_BITS):
                if ev["ctl"] >> i & 1:
                    COV_OS_RX.sample(ctl_bit=name)
        for k, name in (("skp", "SKP"), ("eios", "EIOS"), ("fts", "FTS")):
            if ev[k]:
                COV_OS_RX.sample(event=name, phase=ev["phase"])
        if ev["ts_bad"]:
            COV_OS_RX.sample(ts_bad=ev["why"])

    def _track_starts(self, sh, open_start, delivered) -> None:
        """Note where each packet the shadow delivers started: the iteration
        whose step parsed its STP or SDP, that symbol's lane, and whether the
        two words after it were received without a gap (the HAS latency
        assumes a packet's bytes follow)."""
        for _ in range(len(sh.rx_packets) - delivered):
            self.sh_starts.append(open_start)
        rx = self.rx_prev
        if self.sh_open is not None and self.now - self.sh_open[0] <= 2 \
                and (not rx.valid or rx.elec_idle):
            self.sh_open[2] = False
        pkt = sh._pkt
        if pkt is not None and pkt is not self.sh_pkt:
            lanes = [i for i, (k, b) in enumerate(rx.syms) if k and b in (0xFB, 0x5C)]
            self.sh_open = [self.now, lanes[-1] if lanes else 0, True]
        self.sh_pkt = pkt              # held, so a new packet is a new object

    def _check_rx_packet(self, got) -> None:
        where = self.where()
        sh = self.shadow.rx_packets
        assert self.rx_matched < len(sh), f"{where}: packet delivered the shadow did not get: {got}"
        want = sh[self.rx_matched]
        self.rx_matched += 1
        dllp, data, err, nullify, sop_at = got
        start = self.sh_starts[self.rx_matched - 1]
        if start is not None and start[2]:
            # HAS RX latency: at most 3 cycles from the STP or SDP on
            # pipe_rx_data_i to the sop beat, plus 1 from [15:8]. The shadow's
            # step at iteration k parses the word on the pins in the cycle
            # before edge k-1; a beat read at iteration j is on the pins in
            # the cycle after edge j.
            # A packet whose only beat ends with EDB in [15:8] waits one more
            # word (HAS, Receive and Targets).
            at, lane, _ = start
            latency = sop_at - at + 2
            allow = 3 + lane + int(getattr(want, "tail", False) and len(want.data) <= 2)
            if latency > allow:
                raise AssertionError(f"{where}: sop beat {latency} cycles after its "
                                     f"{'SDP' if dllp else 'STP'} in lane {lane}, the HAS "
                                     f"allows {allow}")
            latency_bin = f"{latency}_lane{lane}"
            if getattr(want, "tail", False) and len(want.data) <= 2 and lane == 1:
                latency_bin += "_edb"
            assert latency_bin in ("3_lane0", "3_lane1", "4_lane1", "4_lane0", "4_lane1_edb"), (
                f"{where}: unexpected RX latency sample {latency_bin}")
            COV_PKT.sample(rx_latency=latency_bin)
        n = len(want.data)
        ok = (dllp == want.dllp and err == want.err and nullify == want.nullify
              and data[:n] == want.data and len(data) in (n, n + 1)
              and (len(data) == n or err))
        if not ok:
            raise AssertionError(f"{where}: delivered packet {len(data)} bytes dllp={dllp} "
                                 f"err={err} nullify={nullify}, expected {want} "
                                 f"(bytes {want.data[:16].hex()} got {data[:16].hex()})")
        COV_PKT.sample(rx_outcome="err" if err else ("nullify" if nullify else "good"))

    async def step(self) -> None:
        """One clk_i cycle; returns at the next falling edge."""
        o = self._read()
        obs = self._read(LAYOUT_OBS, "obs_o")
        up = self.upper
        # The upper layer's beat for the next edge; a new packet goes to the
        # shadow before its step, which builds the word of that edge.
        k = self.now - self.t0
        rst_next = int(k + 1 < self.release[0])
        tx_in, new = up.drive(o["pkt_tx_ready_o"], rst_next)
        sh = self.shadow
        if new is not None:
            sh.tx_packets.append(arch.Packet(new.dllp, new.data, new.nullify))
        sh.rst = bool(self.rst_sampled)
        sh.retrain_req = bool(self.req["retrain_req_i"])
        sh.hot_reset_req = bool(self.req["hot_reset_req_i"])
        sh.disable_req = bool(self.req["disable_req_i"])
        sh.loopback_req = bool(self.req["loopback_req_i"])
        sh.pm_state_req = self.req["pm_state_req_i"]
        errs = sh.rcv_errors
        prev_tx = self.sh_tx
        open_start = self.sh_open
        delivered = len(sh.rx_packets)
        self.sh_eios_before = sh.eios_rx       # the follower's Disabled exit cause
        self.sh_tx = sh.step(self.rx_prev)
        self._track_starts(sh, open_start, delivered)
        errs = sh.rcv_errors - errs if not self.rst_sampled else 0
        self._compare(o, obs, sh, self.sh_tx, prev_tx, errs)
        if o["rcv_err_o"]:
            self.rcv_pulses += 1
        rxp = self.rx_prev
        if rxp.valid and not rxp.elec_idle and sh.state == arch.L0:
            for i, (sk, sb) in enumerate(rxp.syms):
                if sk and sb in (0xFB, 0x5C):
                    COV_PKT.sample(rx_lane=i, rx_kind="dllp" if sb == 0x5C else "tlp")
        if self.rst_sampled:
            self.decoder.reset()
        else:
            self._check_events(obs, self.decoder.word(self.rx_dut))
        got = up.receive(o, self.where, self.now)
        if got is not None:
            self._check_rx_packet(got)
        self._observe(o)
        # The DUT's PIPE word, the partner, the link.
        dut_tx = arch.PipeTx(
            syms=((o["pipe_tx_datak_o"] & 1, o["pipe_tx_data_o"] & 0xFF),
                  (o["pipe_tx_datak_o"] >> 1, o["pipe_tx_data_o"] >> 8)),
            elec_idle=bool(o["pipe_tx_elec_idle_o"]), detect_rx=bool(o["pipe_tx_detect_rx_o"]),
            compliance=bool(o["pipe_tx_compliance_o"]), polarity=bool(o["pipe_rx_polarity_o"]),
            power_down=o["pipe_power_down_o"], reset_n=bool(o["pipe_reset_no"]))
        if not dut_tx.elec_idle and dut_tx.reset_n:
            self.txmon.word(dut_tx.syms, dut_tx.compliance)
        p = self.partner
        p.rst = k < self.release[1]
        p_tx = p.step(self.rx_partner)
        rx_d, rx_p = self.link.step(dut_tx, p_tx)
        if self.line_hook is not None:
            rx_d = self.line_hook(self, rx_d)
        # Drive the next edge's inputs.
        if rst_next != self.rst:
            self.dut.rst_i.value = rst_next
        self.rst = rst_next
        self.rst_sampled = rst_next
        self._drive_rx(rx_d)
        self.inputs.update(tx_in)
        self.inputs.update(self.drive_req)
        self._write()
        up.accepted()
        self.req = dict(self.drive_req)
        self.drive_req["retrain_req_i"] = 0
        self.rx_prev, self.rx_dut, self.rx_partner = self.rx_dut, rx_d, rx_p
        self.now += 1
        await self._fall

    def _observe(self, o: dict) -> None:
        main, tx, rx = decode(o["ltssm_state_o"])
        a = self.a
        a.link_up = bool(o["link_up_o"])
        if self.rst_sampled:                    # reset is not an arc
            a.state, a.tx_sub, a.rx_sub = main, tx, rx
            return
        for old, new in ((a.state, main), (a.tx_sub, tx), (a.rx_sub, rx)):
            if old == new:
                continue
            path = [old, new]
            if (old, new) not in arch.ARC_SET:
                # A sub-machine may take a timed arc and leave with the main
                # machine in one step (Rx_L0s.Entry to .Idle, Tx_L0s.FTS to
                # Tx_L0, then Off); the code shows only the ends.
                mids = [m for m in SUB_STATES
                        if (old, m) in arch.ARC_SET and (m, new) in arch.ARC_SET]
                if len(mids) != 1:
                    raise AssertionError(f"cycle {self.now - self.t0}: {old} -> {new} "
                                         "is not an arc of the HAS arc list")
                path = [old, mids[0], new]
            for x, y in zip(path, path[1:]):
                a.arcs.append((self.now, x, y))
                COV_ARCS.sample(arc=(x, y), port_role=self.role)
        if a.state != main:
            self._control_cov(a.state, main)
        a.state, a.tx_sub, a.rx_sub = main, tx, rx

    def _control_cov(self, old: str, new: str) -> None:
        """Control-state coverage of a main-state change, from the requests
        the DUT sampled for the step that took it."""
        req, dsp = self.req, self.a.dsp
        if new in (arch.HR, arch.DIS):
            name = "hot_reset" if new == arch.HR else "disabled"
            on = req["hot_reset_req_i" if new == arch.HR else "disable_req_i"]
            self.ctl_directed = bool(dsp and on)
            self.ctl_entered = self.now
            how = "directed" if self.ctl_directed else "following"
            COV_CTL.sample(entry=f"{name}_{how}_{self.role}")
        elif new == arch.LBE:
            how = "master" if req["loopback_req_i"] else "slave"
            COV_CTL.sample(entry=f"loopback_{how}_{self.role}")
        elif old == arch.DIS:
            if self.ctl_directed:
                how = "directed"
            elif self.sh_eios_before:
                how = "following_eios"         # by cause: an EIOS was received
            else:
                how = "following_timeout"
            COV_CTL.sample(disabled_exit=how)
        elif old == arch.L0 and new == arch.RL:
            for name, on in (("hot_reset", dsp and req["hot_reset_req_i"]),
                             ("disable", dsp and req["disable_req_i"]),
                             ("loopback", req["loopback_req_i"])):
                if on:
                    COV_CTL.sample(l0_exit_request=name)

    # ------------------------------------------------------------- waiting

    async def run(self, n: int) -> None:
        for _ in range(n):
            await self.step()

    async def run_until(self, cond, limit: int, what: str = "") -> int:
        start = self.now
        while not cond(self):
            if self.now - start > limit:
                raise AssertionError(
                    f"{what or 'condition'} not met in {limit} cycles ({self.role}): "
                    f"DUT in {self.a.state}, partner in {self.partner.state}")
            await self.step()
        return self.now - start

    async def drain(self, limit: int = 20_000) -> None:
        """Wait until every queued packet is sent and every packet the shadow
        and the partner received has been delivered; then check that the
        partner got exactly what the DUT sent."""
        await self.run_until(lambda h: not h.upper.txq and h.upper.cur is None, limit,
                             "transmit queue empty")
        await self.run(64)
        assert self.rx_matched == len(self.shadow.rx_packets), (
            f"{self.where()}: the shadow received {len(self.shadow.rx_packets)} packets, "
            f"the DUT delivered {self.rx_matched}")

    def check_partner(self, aborted: int = 0) -> None:
        """Every packet the DUT sent arrived at the partner, in order, with its
        flags; ``aborted`` of them may instead arrive bad (an abort)."""
        sent, got = self.upper.sent, self.partner.rx_packets
        assert len(got) == len(sent), (
            f"{self.where()}: sent {len(sent)} packets, the partner got {len(got)}")
        bad = 0
        for i, (s, g) in enumerate(zip(sent, got)):
            if g.err:
                bad += 1
                COV_PKT.sample(tx_outcome=self.abort_kind)
                continue
            want = arch.Packet(s.dllp, s.data, s.nullify and not s.dllp)
            assert g == want, f"{self.where()}: packet {i} sent {want} arrived {g}"
            if s.dllp:
                COV_PKT.sample(tx_outcome="dllp_nullify_ignored" if s.nullify else "dllp_end")
            else:
                COV_PKT.sample(tx_outcome="tlp_nullify" if s.nullify else "tlp_end")
        assert bad <= aborted, f"{self.where()}: {bad} packets arrived bad, {aborted} allowed"

    # ----------------------------------------------------------- predicates

    def took(self, a: str, b: str, after: int | None = None):
        after = self.t0 if after is None else after
        return lambda h: any(f == a and t == b and c >= after for c, f, t in h.a.arcs)

    def in_state(self, s: str):
        return lambda h: h.a.state == s

    def both_l0(self, h=None) -> bool:
        p = self.partner
        return (self.a.state == arch.L0 and p.state == arch.L0 and p.tx_sub == arch.TL0
                and p.rx_sub == arch.RL0)
