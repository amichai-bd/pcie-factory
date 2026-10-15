"""Testbench of pcie_mac_ltssm against the link partner
(wiki/src/dv/mac_ltssm/DV_PLAN.md, Strategy).

One cycle, at the falling edge of clk_i:

1. Read the LTSSM's outputs (registers, so stable).
2. Step the **shadow**, a ``pcie_mac_arch.Mac`` in the DUT's role fed the
   same received words and requests as the DUT, and compare the DUT with it
   every cycle: ``ltssm_state_o``, ``link_up_o``, every PIPE control output
   and the transmitted symbols. The shadow is the reference model; nothing
   ever injects into it.
3. Build the DUT's PIPE word with the ordered-set BFM, step the partner
   (``Mac`` in the other role, the only thing the tests steer) and the
   ``Link``, decode the DUT's received word into the receive events.
4. Drive the DUT's inputs for the next rising edge.

The DUT side therefore sees exactly the stimulus a ``Pair`` gives its first
model, and the tests replay the model's partner-only recipes
(wiki/src/arch/mac/MODEL.md#partner-only-recipes).
"""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "mac"))
import pcie_mac_arch as arch  # noqa: E402

from tb.os_bfm import OsBfm  # noqa: E402

# The states this sub-IP implements (Detect, Polling, Configuration, L0,
# Recovery, and the Off/L0 states of the sub-machines) and the arcs of the
# HAS arc list among them: the arc-coverage target of #77.
CORE_STATES = {arch.DQ, arch.DA, arch.PA, arch.PC, arch.PCFG, arch.LWS, arch.LWA,
               arch.LNW, arch.LNA, arch.CC, arch.CI, arch.L0, arch.RL, arch.RC,
               arch.RI, arch.TOFF, arch.TL0, arch.ROFF, arch.RL0}
CORE_ARCS = {name: arc for name, arc in arch.ARCS.items()
             if arc[0] in CORE_STATES and arc[1] in CORE_STATES}

COV = Covergroup("mac_ltssm_arcs")
COV.coverpoint("arc", bins=dict(CORE_ARCS))
COV.coverpoint("port_role", bins={r: r for r in arch.ROLES})
COV.cross("arc_x_port_role", "arc", "port_role")

CODE_STATE = {c: n for n, _, c in arch.MAIN_STATES}
TX_SUB = {c: n for n, _, c in arch.TX_STATES if c is not None}
RX_SUB = {c: n for n, _, c in arch.RX_STATES if c is not None}

# The packed buses of the testbench top (tb/pcie_mac_ltssm_tb.sv), first
# field in the most significant bits: (name, width).
FIELDS_IN = (("pipe_phy_status_i", 1), ("pipe_rx_elec_idle_i", 1), ("pipe_rx_status_i", 3),
             ("pipe_rx_valid_i", 1), ("os_tx_ack_i", 1), ("os_tx_valid_i", 1),
             ("os_tx_last_i", 1), ("os_rx_ts_i", 1), ("os_rx_ts_bad_i", 1),
             ("os_rx_ts2_i", 1), ("os_rx_ts_inv_i", 1), ("os_rx_link_i", 8),
             ("os_rx_link_pad_i", 1), ("os_rx_link_num_i", 1), ("os_rx_lane_i", 8),
             ("os_rx_lane_pad_i", 1), ("os_rx_lane_num_i", 1), ("os_rx_n_fts_i", 8),
             ("os_rx_ctl_i", 5), ("os_rx_skp_i", 1), ("os_rx_eios_i", 1), ("os_rx_fts_i", 1),
             ("os_rx_os_sym_i", 2), ("fr_tx_idle_i", 1), ("fr_rx_idle_i", 2),
             ("retrain_req_i", 1), ("hot_reset_req_i", 1), ("disable_req_i", 1),
             ("loopback_req_i", 1), ("pm_state_req_i", 2))
FIELDS_OUT = (("pipe_reset_no", 1), ("pipe_tx_elec_idle_o", 1), ("pipe_tx_detect_rx_o", 1),
              ("pipe_rx_polarity_o", 1), ("pipe_power_down_o", 2), ("tx_ts_req_o", 1),
              ("tx_compliance_req_o", 1), ("tx_ts2_o", 1), ("tx_link_pad_o", 1),
              ("tx_link_o", 8), ("tx_lane_pad_o", 1), ("tx_lane_o", 8), ("tx_ctl_o", 3),
              ("tx_l0_o", 1), ("tx_stop_o", 1), ("rx_l0_o", 1), ("link_up_o", 1),
              ("ltssm_state_o", 6), ("partner_n_fts_o", 8))
REQUESTS = ("retrain_req_i", "hot_reset_req_i", "disable_req_i", "loopback_req_i",
            "pm_state_req_i")


def _layout(fields):
    out, pos = [], sum(w for _, w in fields)
    for name, w in fields:
        pos -= w
        out.append((name, pos, (1 << w) - 1))
    return out


LAYOUT_IN = _layout(FIELDS_IN)
LAYOUT_OUT = _layout(FIELDS_OUT)


def decode(code: int) -> tuple[str, str, str]:
    """(main, tx, rx) state names of an ltssm_state_o code."""
    if code & 0x20 and code & 0x30 == 0x20:
        return arch.L0, TX_SUB[(code >> 2) & 3], RX_SUB[code & 3]
    if code not in CODE_STATE:
        raise AssertionError(f"ltssm_state_o {code:#04x} is not a code of the encoding")
    return CODE_STATE[code], arch.TOFF, arch.ROFF


class Dut:
    """The DUT as the recipes see it: its state from ltssm_state_o, the
    arcs it took, and its role."""

    def __init__(self) -> None:
        self.state, self.tx_sub, self.rx_sub = arch.DQ, arch.TOFF, arch.ROFF
        self.arcs: list[tuple[int, str, str]] = []
        self.role = "downstream"
        self.dsp = True
        self.link_up = False


class Harness:
    def __init__(self, dut, timeout_div: int = 1024, err_timeout_div: int = 1024) -> None:
        self.dut = dut
        self.timeout_div = timeout_div
        self.err_timeout_div = err_timeout_div
        self.a = Dut()
        self.now = 0
        self.log = dut._log
        self.checks = 0

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
        self.partner = arch.Mac(other, self.timeout_div, self.err_timeout_div,
                                link_number=partner_link)
        self.shadow = arch.Mac(role, self.timeout_div, self.err_timeout_div)
        self.link = arch.Link(delay)
        self.bfm = OsBfm(arch)
        self.rx_dut = arch.PipeRx()         # what the DUT sampled at the last edge
        self.rx_partner = arch.PipeRx()
        self.req = {n: 0 for n in REQUESTS}  # what the DUT sampled at the last edge
        self.drive_req = dict(self.req)
        self.rst = 1
        self.release = (reset_cycles, reset_cycles + skew)
        self.t0 = self.now
        self.dut.port_role_i.value = int(role == "upstream")
        for name in REQUESTS:
            self.inputs[name] = 0
        self._write()
        self.dut.rst_i.value = 1
        # One edge in reset (reset is not an arc), then the DUT and the
        # shadow step together from reset.
        await FallingEdge(self.dut.clk_i)
        self.rst_sampled = 1
        self.a.state, self.a.tx_sub, self.a.rx_sub = arch.DQ, arch.TOFF, arch.ROFF

    # ------------------------------------------------------------ requests

    def request(self, **kw) -> None:
        """Drive the DUT's own link-control inputs (``retrain`` is a one-
        cycle pulse)."""
        for k, v in kw.items():
            self.drive_req[f"{k}_req_i" if k != "pm_state" else "pm_state_req_i"] = int(v)

    # ---------------------------------------------------------------- step

    def _read(self) -> dict:
        v = int(self.dut.out_o.value)
        return {n: (v >> pos) & mask for n, pos, mask in LAYOUT_OUT}

    def _write(self) -> None:
        f = self.inputs
        v = 0
        for n, pos, mask in LAYOUT_IN:
            v |= (f[n] & mask) << pos
        if v != self._last_in:
            self.dut.in_i.value = v
            self._last_in = v

    def _drive_rx(self, rx, ev=None, tx_ev=None) -> None:
        f = self.inputs
        f["pipe_phy_status_i"] = int(rx.phy_status)
        f["pipe_rx_elec_idle_i"] = int(rx.elec_idle)
        f["pipe_rx_status_i"] = rx.status
        f["pipe_rx_valid_i"] = int(rx.valid)
        tx_ev = tx_ev or {}
        f["os_tx_ack_i"] = tx_ev.get("ack", 0)
        f["os_tx_valid_i"] = tx_ev.get("valid", 0)
        f["os_tx_last_i"] = tx_ev.get("last", 0)
        f["fr_tx_idle_i"] = tx_ev.get("idle", 0)
        ev = ev or {}
        for n, k in (("os_rx_ts_i", "ts"), ("os_rx_ts_bad_i", "ts_bad"),
                     ("os_rx_ts2_i", "ts2"), ("os_rx_ts_inv_i", "ts_inv"),
                     ("os_rx_link_i", "link"), ("os_rx_link_pad_i", "link_pad"),
                     ("os_rx_link_num_i", "link_num"), ("os_rx_lane_i", "lane"),
                     ("os_rx_lane_pad_i", "lane_pad"), ("os_rx_lane_num_i", "lane_num"),
                     ("os_rx_n_fts_i", "n_fts"), ("os_rx_ctl_i", "ctl"),
                     ("os_rx_skp_i", "skp"), ("os_rx_eios_i", "eios"), ("os_rx_fts_i", "fts"),
                     ("os_rx_os_sym_i", "os_sym"), ("fr_rx_idle_i", "idle")):
            f[n] = ev.get(k, 0)

    def _check_sibling_outputs(self, o: dict) -> None:
        """The requests to the sibling blocks follow the state: one request
        at a time, the frame's line only in Configuration.Idle,
        Recovery.Idle and L0, packets only in L0, receive only in L0."""
        main = CODE_STATE.get(o["ltssm_state_o"], arch.L0 if o["ltssm_state_o"] & 0x20 else "")
        where = f"cycle {self.now - self.t0} ({self.role}, {main})"
        reqs = o["tx_ts_req_o"] + o["tx_compliance_req_o"] + o["tx_l0_o"]
        assert reqs <= 1, f"{where}: more than one of TS, compliance and frame requested"
        if o["pipe_tx_elec_idle_o"]:
            assert reqs == 0, f"{where}: a request in electrical idle"
        if o["tx_l0_o"]:
            assert main in (arch.CI, arch.RI, arch.L0), f"{where}: tx_l0_o outside Idle and L0"
        assert o["tx_stop_o"] == int(not (o["tx_l0_o"] and main == arch.L0)), (
            f"{where}: tx_stop_o {o['tx_stop_o']}")
        assert o["rx_l0_o"] == int(main == arch.L0), f"{where}: rx_l0_o {o['rx_l0_o']}"
        if o["tx_compliance_req_o"]:
            assert main == arch.PC, f"{where}: compliance requested outside Polling.Compliance"

    def _compare(self, o: dict, sh_tx, sh) -> None:
        where = f"cycle {self.now - self.t0} ({self.role}, shadow in {sh.state})"
        want = {
            "ltssm_state_o": sh.code,
            "link_up_o": int(sh.link_up),
            "pipe_reset_no": int(sh_tx.reset_n),
            "pipe_tx_elec_idle_o": int(sh_tx.elec_idle),
            "pipe_tx_detect_rx_o": int(sh_tx.detect_rx),
            "pipe_rx_polarity_o": int(sh_tx.polarity),
            "pipe_power_down_o": sh_tx.power_down,
        }
        for k, v in want.items():
            if o[k] != v:
                raise AssertionError(f"{where}: {k} expected {v:#x} got {o[k]:#x}")
        self.checks += 1

    async def step(self) -> None:
        """One clk_i cycle; returns at the next falling edge."""
        o = self._read()
        # The shadow takes the step the DUT took at the last rising edge.
        sh = self.shadow
        sh.rst = bool(self.rst_sampled)
        sh.retrain_req = bool(self.req["retrain_req_i"])
        sh.hot_reset_req = bool(self.req["hot_reset_req_i"])
        sh.disable_req = bool(self.req["disable_req_i"])
        sh.loopback_req = bool(self.req["loopback_req_i"])
        sh.pm_state_req = self.req["pm_state_req_i"]
        sh_tx = sh.step(self.rx_dut)
        self._compare(o, sh_tx, sh)
        self._observe(o)
        # The DUT's word, the partner, the link.
        self._check_sibling_outputs(o)
        dut_tx, tx_ev = self.bfm.tx({
            "reset_n": o["pipe_reset_no"], "elec_idle": o["pipe_tx_elec_idle_o"],
            "detect_rx": o["pipe_tx_detect_rx_o"], "polarity": o["pipe_rx_polarity_o"],
            "pd": o["pipe_power_down_o"], "ts_req": o["tx_ts_req_o"],
            "compliance_req": o["tx_compliance_req_o"], "tx_l0": o["tx_l0_o"],
            "ts2": o["tx_ts2_o"], "link_pad": o["tx_link_pad_o"], "link": o["tx_link_o"],
            "lane_pad": o["tx_lane_pad_o"], "lane": o["tx_lane_o"], "ctl": o["tx_ctl_o"]})
        if not sh_tx.elec_idle and sh_tx.reset_n and dut_tx.syms != sh_tx.syms:
            raise AssertionError(f"cycle {self.now - self.t0} ({self.role}, {self.a.state}): "
                                 f"transmitted symbols expected {sh_tx.syms} got {dut_tx.syms}")
        k = self.now - self.t0
        p = self.partner
        p.rst = k < self.release[1]
        p_tx = p.step(self.rx_partner)
        rx_d, rx_p = self.link.step(dut_tx, p_tx)
        ev = self.bfm.rx(rx_d)
        # Drive the next edge's inputs.
        rst = int(k + 1 < self.release[0])
        if rst != self.rst:
            self.dut.rst_i.value = rst
        self.rst = self.rst_sampled = rst
        self._drive_rx(rx_d, ev, tx_ev)
        self.inputs.update(self.drive_req)
        self._write()
        self.req = dict(self.drive_req)
        self.drive_req["retrain_req_i"] = 0
        self.rx_dut, self.rx_partner = rx_d, rx_p
        self.now += 1
        await FallingEdge(self.dut.clk_i)

    def _observe(self, o: dict) -> None:
        main, tx, rx = decode(o["ltssm_state_o"])
        a = self.a
        a.link_up = bool(o["link_up_o"])
        if self.rst_sampled:                    # reset is not an arc
            a.state, a.tx_sub, a.rx_sub = main, tx, rx
            return
        for old, new in ((a.state, main), (a.tx_sub, tx), (a.rx_sub, rx)):
            if old != new:
                a.arcs.append((self.now, old, new))
                COV.sample(arc=(old, new), port_role=self.role)
                if (old, new) not in arch.ARC_SET:
                    raise AssertionError(f"cycle {self.now - self.t0}: {old} -> {new} "
                                         "is not an arc of the HAS arc list")
        a.state, a.tx_sub, a.rx_sub = main, tx, rx
        self.partner_n_fts = o["partner_n_fts_o"]

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

    # ----------------------------------------------------------- predicates

    def took(self, a: str, b: str, after: int | None = None):
        """The DUT took arc ``a`` to ``b`` at or after cycle ``after``
        (default: since the last ``fresh``)."""
        after = self.t0 if after is None else after
        return lambda h: any(f == a and t == b and c >= after for c, f, t in h.a.arcs)

    def in_state(self, s: str):
        return lambda h: h.a.state == s

    def both_l0(self, h=None) -> bool:
        p = self.partner
        return (self.a.state == arch.L0 and p.state == arch.L0 and p.tx_sub == arch.TL0
                and p.rx_sub == arch.RL0)
