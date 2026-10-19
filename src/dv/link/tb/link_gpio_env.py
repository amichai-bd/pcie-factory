"""cocotb environment of the link e2e over the GPIO lower layer
(wiki/src/dv/link/DV_PLAN.md, GPIO lower layer; wiki/src/arch/link/HAS.md,
lower layer).

Two ``pcie_link`` instances at LOWER 1 on the top ``pcie_link_gpio_tb``,
joined by the modelled GPIO wires. It reuses the phy e2e environment
(``tb/link_e2e_env.py``): the ``LinkEnd`` drivers and checks of each
upper stream, the scoreboards, the credit checks, the error pulses,
liveness and the reset values. What the phy line adds there does not
apply here and is left out: the line injector, the channel model, the
passive checker and its 140-cycle line Ack bound, and the 118-cycle Ack
bound at the dll boundary, which the gpio HAS says cannot hold over the
adapter (dll timers). In their place:

- b's clock is a's times 1/2, 1 or 2 (the top's RATIO), the gpio clock
  contract's range;
- a wire injector that damages the marker of a chosen slot of a direction
  (the sender's transfer schedule is known from its reset release: one
  transfer every GPIO_CLK_DIV cycles);
- the replay round trip of every TLP at the sender's dll boundary, from
  its eop taken by the adapter to the first Ack that covers it, to compare
  with the replay timeout the dlls run with (link HAS, replay timeout at
  LOWER 1).
"""

from __future__ import annotations

import cocotb
from cocotb.clock import Clock

from tb.link_e2e_env import (CORE_PS, SEQ_MOD, BoundaryAck, Env, ppm_clock, ptn)

RATIO_PPM = {0: 1_000_000, 1: 0, 2: -500_000}   # b's period: twice, equal, half of a's
RATIO_NAMES = {0: "half", 1: "equal", 2: "double"}


class OpenAck(BoundaryAck):
    """The dll-boundary Ack latency, measured only: over the adapter an Ack
    may wait behind a maximum TLP in the transmit buffer (gpio HAS, dll
    timers), so the 118-cycle bound does not apply."""

    def tx_beat(self, n, data, sop, eop, dllp) -> None:
        super().tx_beat(n, data, sop, eop, dllp)
        self.violations.clear()


class RoundTrip:
    """One end's replay round trips: each TLP's eop taken by its adapter to
    the first Ack received that covers its sequence number."""

    def __init__(self) -> None:
        self.tx: list[int] = []
        self.pending: list[tuple[int, int]] = []   # (eop cycle, seq)
        self.rx: list[int] = []
        self.max = 0
        self.count = 0

    def tx_beat(self, n, data, sop, eop, dllp) -> None:
        if dllp:
            return
        if sop:
            self.tx = []
        self.tx += [data & 0xFF, data >> 8]
        if eop and len(self.tx) >= 2:
            self.pending.append((n, (self.tx[0] & 0x0F) << 8 | self.tx[1]))

    def rx_beat(self, n, data, sop, eop, dllp, err) -> None:
        if not dllp:
            return
        if sop:
            self.rx = []
        self.rx += [data & 0xFF, data >> 8]
        if eop and not err and len(self.rx) == 6 and self.rx[0] == ptn.ACK:
            s = (self.rx[2] & 0x0F) << 8 | self.rx[3]
            keep = []
            for t, seq in self.pending:
                if (s - seq) % SEQ_MOD < SEQ_MOD // 2:
                    self.max = max(self.max, n - t)
                    self.count += 1
                else:
                    keep.append((t, seq))
            self.pending = keep

    def reset(self) -> None:
        self.pending.clear()


class GpioEnv(Env):
    """One GPIO back-to-back run; ``seed`` draws everything random."""

    def __init__(self, dut, seed: int) -> None:
        self.ratio = int(dut.cfg_ratio_o.value)
        self.clk_div = int(dut.cfg_gpio_clk_div_o.value)
        super().__init__(dut, seed, ppm=RATIO_PPM[self.ratio])
        self.log.info("link over GPIO: GPIO_CLK_DIV %d, b clock %s of a's", self.clk_div,
                      RATIO_NAMES[self.ratio])
        self.rt = (RoundTrip(), RoundTrip())
        self.damage_at: dict[str, set[int]] = {"ab": set(), "ba": set()}
        self.codes: dict[str, list[int]] = {"ab": [], "ba": []}   # each slot's code, sent
        self.ovr = {d: (getattr(dut, f"{d}_gpio_ovr_i"), getattr(dut, f"{d}_gpio_ovr_ctrl_i"))
                    for d in ("ab", "ba")}
        for d in ("ab", "ba"):
            for h in self.ovr[d]:
                h.value = 0
        for e in self.ends:
            e.ack = OpenAck(e.name)

    def start(self) -> None:
        cocotb.start_soon(Clock(self.dut.a_clk_i, CORE_PS, unit="ps").start())
        cocotb.start_soon(ppm_clock(self.dut.b_clk_i, self.ppm, 2960))
        for i in (0, 1):
            cocotb.start_soon(self._loop(i))
        self.started = True

    async def _loop(self, i: int) -> None:
        from cocotb.triggers import FallingEdge, Timer
        from cocotb.utils import get_sim_time
        end = self.ends[i]
        d = end.dir
        clk = (self.dut.a_clk_i, self.dut.b_clk_i)[i]
        rt = self.rt[i]
        tx_ctrl = getattr(self.dut, f"{end.name}_gpio_tx_ctrl_o")
        ctrl: list[int] = []
        rd = end.rd
        ovr_on, ovr_ctrl = self.ovr[d]
        on = False
        while True:
            await FallingEdge(clk)
            end.edge(True, get_sim_time("ps"))
            n = end.cycle
            if rd["mon_pkt_tx_valid_o"]() and rd["mon_pkt_tx_ready_o"]():
                rt.tx_beat(n, rd["mon_pkt_tx_data_o"](), rd["mon_pkt_tx_sop_o"](),
                           rd["mon_pkt_tx_eop_o"](), rd["mon_pkt_tx_dllp_o"]())
            if rd["mon_pkt_rx_valid_o"]():
                rt.rx_beat(n, rd["mon_pkt_rx_data_o"](), rd["mon_pkt_rx_sop_o"](),
                           rd["mon_pkt_rx_eop_o"](), rd["mon_pkt_rx_dllp_o"](),
                           rd["mon_pkt_rx_err_o"]())
            if not end.link_up:
                rt.reset()
            # The wire injector: transfer k of the sender starts at the edge
            # before falling edge release + 1 + k * clk_div.
            k = n - (end.release + 1)
            if k >= 0 and k % self.clk_div == 0:
                x = k // self.clk_div
                # The slot codes on the sender's control pin (gpio HAS, slots).
                if x % 4 == 0:
                    ctrl = []
                ctrl.append(int(tx_ctrl.value))
                if x % 4 == 3:
                    self.codes[d].append(ctrl[1] | ctrl[2] << 1 | ctrl[3] << 2)
                want = x % 4 == 0 and x // 4 in self.damage_at[d]
                if want != on:
                    on = want
                    ovr_on.value = int(on)
                    ovr_ctrl.value = 0          # the marker reads 0
            if end.drove_valid:
                await Timer(1, "ns")
                end.after_drive()
            self._liveness(end)

    def slot_now(self, i: int) -> int:
        """The slot end i's transmitter is sending."""
        e = self.ends[i]
        return max(0, (e.cycle - e.release - 1) // (4 * self.clk_div))

    def damage(self, d: str, ahead: int = 4) -> int:
        """Damage the marker of a slot of ``d`` ``ahead`` slots from now."""
        si = self.slot_now(0 if d == "ab" else 1) + ahead
        self.damage_at[d].add(si)
        return si

    def find_slot(self, d: str, since: int, code: int) -> int:
        """The first slot of ``d`` from ``since`` sent with ``code``, or -1."""
        c = self.codes[d]
        return next((i for i in range(since, len(c)) if c[i] == code), -1)

    def finish(self) -> dict:
        """After a drain: every TLP delivered, credits back at the far end's
        advertisement, and the round trips against the replay timeout."""
        self.raise_violations()
        for d, sb in self.sb.items():
            assert sb.outstanding() == 0, f"{d}: {sb.outstanding()} TLPs accepted but not delivered"
        for e in self.ends:
            far = self.ends[1 - self.ends.index(e)].fc
            for i, port in enumerate(("fc_ph_avail_o", "fc_pd_avail_o", "fc_nph_avail_o",
                                      "fc_npd_avail_o", "fc_cplh_avail_o", "fc_cpld_avail_o")):
                if far[i]:
                    assert e.avail[i] == far[i], (f"end {e.name}: drained, {port} {e.avail[i]}, "
                                                  f"expected the far end's {far[i]}")
        stats = {
            "delivered": {d: sb.delivered for d, sb in self.sb.items()},
            "err": {e.name: e.err[:4] for e in self.ends},
            "rcv_err": {e.name: e.rcv_err for e in self.ends},
            "round_trip_max": {e.name: self.rt[i].max for i, e in enumerate(self.ends)},
            "round_trips": {e.name: self.rt[i].count for i, e in enumerate(self.ends)},
            "ack_dll_max": {e.name: e.ack.max for e in self.ends},
            "lost_at_link_down": {d: sb.lost for d, sb in self.sb.items()},
            "replay_timeout": self.replay_timeout,
        }
        self.log.info("link over GPIO done: %s", stats)
        return stats
