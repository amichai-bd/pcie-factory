"""cocotb environment of the phy back-to-back e2e (wiki/src/dv/phy/DV_PLAN.md).

Two ports, a and b, on the testbench top ``pcie_phy_e2e_tb``. Each layer of
each port is a Python model or the RTL, per the top's parameters (echoed on
``cfg_*_o``):

=========  ===================================  ==============================
Layer      model                                rtl
=========  ===================================  ==============================
mac        ``pcie_mac_arch.Mac``                the RTL in ``u_a``/``u_b``
pcs        ``PcsModel`` (models/)               the RTL in ``u_a``/``u_b``
channel    ``Channel`` (models/): ppm through   one register per direction in
           the clocks, slips, flips, idle       the top (ideal)
=========  ===================================  ==============================

mac and pcs switch together until the stack RTL exposes PIPE (the DV plan
has the per-layer recipe). The clocks: ``a_clk_i`` runs at 125 MHz,
``b_clk_i`` ``ppm`` off it with period 8 ns x (1 + ppm 1e-6), so a
positive ppm makes b the slower clock (dithered to the picosecond, so the
average offset is exact), and each port's ``ser_rx_clk_i`` is the far
clock. One coroutine per clock calls ``Bench.edge`` at every falling edge:
that port drives and samples its pins (RTL) or steps its models, and its
transmit word crosses the channel to the far port's receive front end
(with ``CHAN_RTL`` 1 the top joins the lines and Python only steps ports).

The upper layer is a data-link stub: packets carry a 16-bit sequence
number first and a check code last (CRC-32 on a TLP, CRC-16 on a DLLP),
so the scoreboard tells a dropped or flagged packet (allowed only while an
impairment is in flight) from an undetected corruption (never allowed).
"""

from __future__ import annotations

import binascii
import os
import random
import sys
from collections import deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer

from regress.funcov import Covergroup

sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
# The mac transmit scrambler's cycle model (src/dv/mac/models), a block.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mac" / "models"))
import pcie_mac_scrambler_model  # noqa: E402
# The pcs_enc8b10b sub-IP's cycle model, a transmit-encoder block.
sys.path.append(str(Path(__file__).resolve().parents[2] / "pcs_enc8b10b" / "models"))
import pcie_pcs_enc8b10b_model  # noqa: E402
import pcie_phy_stack_model as stack  # noqa: E402
from pcie_phy_stack_model import L0_CODE, RECOVERY_CODES, ModelPort  # noqa: E402

CORE_PS = 8000
B_PHASE_PS = 2960            # b's first edge, so the two clocks rarely coincide
LAYERS = ("mac", "pcs", "channel")

# ------------------------------------------------------------ coverage

COV_LAYERS = Covergroup("phy_e2e_layers")
for _layer in LAYERS:
    COV_LAYERS.coverpoint(_layer, bins={"model": "model", "rtl": "rtl"})
COV_LAYERS.cross("mac_x_pcs", "mac", "pcs")
# Block-level swaps inside a model layer (the DV plan's switch table).
COV_LAYERS.coverpoint("tx_scrambler", bins={"mac_model": "mac_model", "block_model": "block_model"})
COV_LAYERS.coverpoint("tx_encoder", bins={"pcs_model": "pcs_model", "block_model": "block_model"})

COV_RUN = Covergroup("phy_e2e_run")
COV_RUN.coverpoint("a_role", bins={"downstream": "downstream", "upstream": "upstream"})
COV_RUN.coverpoint("ppm", bins={"pos_300": 300, "neg_300": -300, "pos_600": 600, "neg_600": -600,
                                "other": lambda v: v not in (300, -300, 600, -600)})
COV_RUN.cross("role_x_ppm", "a_role", "ppm")

COV_EVENTS = Covergroup("phy_e2e_events")
COV_EVENTS.coverpoint("impairment", bins={"flip": "flip", "slip": "slip", "idle": "idle"})
COV_EVENTS.coverpoint("recovery", bins={"directed": "directed", "autonomous": "autonomous"})

COV_PKT = Covergroup("phy_e2e_packets")
COV_PKT.coverpoint("dir", bins={"a_to_b": "a_to_b", "b_to_a": "b_to_a"})
COV_PKT.coverpoint("kind", bins={"tlp": "tlp", "dllp": "dllp", "tlp_nullified": "tlp_nullified"})
COV_PKT.coverpoint("bytes", bins={"dllp_6": 6, "tlp_min": range(18, 23),
                                  "tlp_mid": range(23, 200), "tlp_large": range(200, 4123)})
COV_PKT.cross("dir_x_kind", "dir", "kind")


# --------------------------------------------------------------- traffic

def check_ok(dllp: bool, data: bytes) -> bool:
    """The stub's check code: CRC-16 on a DLLP, CRC-32 on a TLP."""
    if dllp:
        return len(data) == 6 and binascii.crc_hqx(data[:4], 0xFFFF).to_bytes(2, "little") == data[4:]
    return len(data) >= 8 and binascii.crc32(data[:-4]).to_bytes(4, "little") == data[-4:]


def make_packet(rng: random.Random, seq: int, kind: str, max_dw: int = 64) -> tuple:
    """(dllp, data, nullify) for one packet as the data-link layer hands it
    down: a DLLP is 6 bytes, a TLP 2 + 12|16 + 4n + 4 bytes."""
    head = (seq & 0xFFFF).to_bytes(2, "little")
    if kind == "dllp":
        body = head + rng.randbytes(2)
        return True, body + binascii.crc_hqx(body, 0xFFFF).to_bytes(2, "little"), False
    body = head + rng.randbytes(rng.choice((12, 16)) + 4 * rng.randrange(0, max_dw + 1))
    return False, body + binascii.crc32(body).to_bytes(4, "little"), kind == "tlp_nullified"


class Scoreboard:
    """One direction. Every packet delivered without ``err`` and with a good
    check code must be the next sent one or a later one (a gap is a lost
    packet), byte for byte with the same tag and nullify flag; a mismatch
    asserts at once. A packet whose TLP/DLLP tag was swapped on the way
    fails the check code (CRC-16 versus CRC-32) instead: it is counted, not
    asserted, and fails ``check_lossless`` in a lossless phase. ``mark``
    opens a lossless phase: from then on nothing may be lost or flagged."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.sent: list[tuple] = []
        self.next = 0                      # index of the next packet expected
        self.delivered = 0
        self.lost = 0
        self.flagged = 0                   # err on the eop beat
        self.check_failed = 0              # good flags, bad check code (the DLL drops it)
        self.mark_at = 0
        self.mark_counts = (0, 0, 0)

    def add(self, pkt: tuple) -> None:
        self.sent.append(pkt)
        kind = "dllp" if pkt[0] else ("tlp_nullified" if pkt[2] else "tlp")
        COV_PKT.sample(dir=self.name, kind=kind, bytes=len(pkt[1]))

    def got(self, dllp: bool, data: bytes, nullify: bool, err: bool) -> None:
        if err:
            self.flagged += 1
            return
        if not check_ok(dllp, data):
            self.check_failed += 1
            return
        seq = int.from_bytes(data[:2], "little")
        # The sequence number is 16 bits; find it at or after the expected index.
        idx = next((i for i in range(self.next, len(self.sent))
                    if i & 0xFFFF == seq), None)
        assert idx is not None, (
            f"{self.name}: delivered seq {seq} was not sent after index {self.next} "
            f"(duplicate or out of order); {self.delivered} delivered so far")
        want = self.sent[idx]
        assert (dllp, data, nullify) == want, (
            f"{self.name}: packet {idx} corrupted: expected dllp={want[0]} nullify={want[2]} "
            f"{want[1].hex()}, observed dllp={dllp} nullify={nullify} {data.hex()}")
        self.lost += idx - self.next
        self.next = idx + 1
        self.delivered += 1

    def mark(self) -> None:
        self.mark_at = len(self.sent)
        self.mark_counts = (self.lost, self.flagged, self.check_failed)

    def drained(self) -> bool:
        return self.next >= len(self.sent)

    def check_lossless(self) -> None:
        lost = self.lost - self.mark_counts[0]
        flagged = self.flagged - self.mark_counts[1]
        bad = self.check_failed - self.mark_counts[2]
        assert self.next >= len(self.sent), (
            f"{self.name}: {len(self.sent) - self.next} of {len(self.sent) - self.mark_at} "
            f"packets sent since the mark not delivered")
        assert (lost, flagged, bad) == (0, 0, 0), (
            f"{self.name}: since the mark (packet {self.mark_at}): expected no loss, "
            f"observed {lost} lost, {flagged} flagged err, {bad} failed the check code")


# ------------------------------------------------------------ RTL port

class RtlPort:
    """One pcie_phy instance of the top (``u_a`` or ``u_b``), driven and
    sampled at the falling edge of its clock, with the same protocol as
    ``ModelPort``. A beat offered at a falling edge is taken at the next
    rising edge when ``pkt_tx_ready_o`` read at that falling edge is 1
    (the HAS makes ready a function of the MAC's state, not of valid)."""

    kind = "rtl"

    def __init__(self, dut, prefix: str, role: str) -> None:
        self.role = role
        self.s = {n[len(prefix):]: getattr(dut, n) for n in dir(dut) if n.startswith(prefix)}
        self.rst = True
        self.tx: deque = deque()           # beats: (data16, sop, eop, dllp, nullify)
        self.offer = None
        self.rx_beats: list = []
        self.rx_new: list = []
        self._code = 0
        self._up = False
        self._rcv = 0
        self._retrain = False
        for name in ("pkt_tx_valid_i", "pkt_tx_data_i", "pkt_tx_sop_i", "pkt_tx_eop_i",
                     "pkt_tx_dllp_i", "pkt_tx_nullify_i", "retrain_req_i", "hot_reset_req_i",
                     "disable_req_i", "loopback_req_i", "pm_state_req_i", "ser_tx_rxdet_i",
                     "ser_rx_data_i"):
            self.s[name].value = 0
        self.s["ser_rx_elec_idle_i"].value = 1
        self.s["port_role_i"].value = stack.ROLES.index(role)
        self.s["rst_i"].value = 1

    def present(self) -> bool:
        return not self.rst

    def core_edge(self, rxdet: bool) -> tuple[int, bool]:
        s = self.s
        # Sample (outputs registered at the last rising edge).
        if self.offer is not None and int(s["pkt_tx_ready_o"].value):
            self.offer = None              # taken at the rising edge just passed
        if int(s["pkt_rx_valid_o"].value):
            beat = int(s["pkt_rx_data_o"].value)
            if int(s["pkt_rx_sop_o"].value):
                self.rx_beats = []
                self.rx_dllp = bool(int(s["pkt_rx_dllp_o"].value))
            self.rx_beats += [beat & 0xFF, beat >> 8]
            if int(s["pkt_rx_eop_o"].value):
                self.rx_new.append((self.rx_dllp, bytes(self.rx_beats),
                                    bool(int(s["pkt_rx_nullify_o"].value)),
                                    bool(int(s["pkt_rx_err_o"].value))))
        self._code = int(s["ltssm_state_o"].value)
        self._up = bool(int(s["link_up_o"].value))
        self._rcv += int(s["rcv_err_o"].value)
        word, ei = int(s["ser_tx_data_o"].value), bool(int(s["ser_tx_elec_idle_o"].value))
        # Drive.
        s["rst_i"].value = int(self.rst)
        s["ser_tx_rxdet_i"].value = int(rxdet)
        s["retrain_req_i"].value = int(self._retrain)
        self._retrain = False
        if self.offer is None and self.tx and not self.rst:
            self.offer = self.tx.popleft()
        if self.offer is None:
            s["pkt_tx_valid_i"].value = 0
        else:
            data, sop, eop, dllp, nullify = self.offer
            s["pkt_tx_valid_i"].value = 1
            s["pkt_tx_data_i"].value = data
            s["pkt_tx_sop_i"].value = sop
            s["pkt_tx_eop_i"].value = eop
            s["pkt_tx_dllp_i"].value = dllp
            s["pkt_tx_nullify_i"].value = nullify
        return word, ei

    def rx_edge(self, word: int, ei: bool) -> None:
        self.s["ser_rx_data_i"].value = word
        self.s["ser_rx_elec_idle_i"].value = int(ei)

    def send(self, dllp: bool, data: bytes, nullify: bool = False) -> None:
        n = len(data) // 2
        for i in range(n):
            self.tx.append((data[2 * i] | data[2 * i + 1] << 8, int(i == 0), int(i == n - 1),
                            int(dllp), int(nullify and i == n - 1)))

    def received(self) -> list:
        new, self.rx_new = self.rx_new, []
        return new

    def tx_idle(self) -> bool:
        return not self.tx and self.offer is None

    @property
    def code(self) -> int:
        return self._code

    @property
    def link_up(self) -> bool:
        return self._up

    @property
    def rcv_errors(self) -> int:
        return self._rcv

    def retrain(self) -> None:
        self._retrain = True


# ------------------------------------------------------------- clocks

async def ppm_clock(sig, ppm: float, phase_ps: int) -> None:
    """``sig`` toggling with period CORE_PS * (1 + ppm 1e-6): each edge is
    the ideal edge rounded to the picosecond, so the average is exact."""
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
    """One back-to-back run. ``a_role`` is port a's role; ``ppm`` is b's
    clock offset from a's (positive: b's period is longer, b is slower);
    ``seed`` draws everything random."""

    def __init__(self, dut, a_role: str = "downstream", ppm: float = 0, seed: int = 0,
                 skew_max: int = 64, delay_words: int = 4,
                 tx_scrambler: str = "mac_model", tx_encoder: str = "pcs_model") -> None:
        self.dut = dut
        self.log = dut._log
        self.rng = random.Random(seed)
        self.layers = {
            "mac": "rtl" if int(dut.cfg_mac_rtl_o.value) else "model",
            "pcs": "rtl" if int(dut.cfg_pcs_rtl_o.value) else "model",
            "channel": "rtl" if int(dut.cfg_chan_rtl_o.value) else "model",
        }
        self.timeout_div = int(dut.cfg_timeout_div_o.value)
        self.a_role, self.ppm = a_role, ppm
        self.log.info("phy e2e: COCOTB_RANDOM_SEED %s, test seed %d, layers %s, a %s, b %s, b clock %+g ppm, "
                      "TIMEOUT_DIV %d, tx scrambler %s, tx encoder %s", os.environ.get("COCOTB_RANDOM_SEED", "-"), seed,
                      " ".join(f"{k}={v}" for k, v in self.layers.items()),
                      a_role, stack.other(a_role), ppm, self.timeout_div, tx_scrambler, tx_encoder)
        if tx_scrambler not in ("mac_model", "block_model"):
            raise ValueError(f"tx_scrambler: expected mac_model or block_model, got {tx_scrambler}")
        if tx_encoder not in ("pcs_model", "block_model"):
            raise ValueError(f"tx_encoder: expected pcs_model or block_model, got {tx_encoder}")
        COV_LAYERS.sample(**self.layers, tx_scrambler=tx_scrambler, tx_encoder=tx_encoder)
        COV_RUN.sample(a_role=a_role, ppm=ppm)
        roles = (a_role, stack.other(a_role))
        if self.layers["mac"] != self.layers["pcs"]:
            raise NotImplementedError(
                "mac and pcs switch together until the stack RTL exposes PIPE "
                "(wiki/src/dv/phy/DV_PLAN.md, model-to-RTL switch plan)")
        if self.layers["channel"] == "rtl" and self.layers["pcs"] == "model":
            raise ValueError("an RTL channel needs the pcs as RTL (CHAN_RTL needs PCS_RTL)")
        if self.layers["mac"] == "model":
            ports = [ModelPort(r, self.timeout_div,
                               tx_scrambler=(pcie_mac_scrambler_model.WordScrambler()
                                             if tx_scrambler == "block_model" else None),
                               tx_encoder=(pcie_pcs_enc8b10b_model.Encoder()
                                           if tx_encoder == "block_model" else None))
                     for r in roles]
        else:
            if (tx_scrambler, tx_encoder) != ("mac_model", "pcs_model"):
                raise ValueError("block swaps apply only inside the model layers")
            ports = [RtlPort(dut, f"{p}_", r) for p, r in zip("ab", roles)]
        skew = self.rng.randrange(skew_max + 1)
        self.bench = stack.Bench(ports, self.rng, delay_words,
                                 release=(stack.RESET_CYCLES, stack.RESET_CYCLES + skew))
        if self.layers["channel"] == "rtl":
            self.bench.channels = None
        self.ports = self.bench.ports
        self.sb = (Scoreboard("a_to_b"), Scoreboard("b_to_a"))
        self.codes: tuple[list, list] = ([0], [0])   # distinct ltssm codes in order
        self.bench.hooks.append(self._monitor)
        self.seq = [0, 0]

    def _monitor(self, bench, i: int) -> None:
        p = self.ports[i]
        if p.code != self.codes[i][-1]:
            self.codes[i].append(p.code)
        for pkt in p.received():           # port i receives what the far port sent
            self.sb[1 - i].got(*pkt)

    async def _clock_loop(self, i: int) -> None:
        clk = (self.dut.a_clk_i, self.dut.b_clk_i)[i]
        while True:
            await FallingEdge(clk)
            self.bench.edge(i)

    def start(self) -> None:
        cocotb.start_soon(Clock(self.dut.a_clk_i, CORE_PS, unit="ps").start())
        cocotb.start_soon(ppm_clock(self.dut.b_clk_i, self.ppm, B_PHASE_PS))
        for i in (0, 1):
            cocotb.start_soon(self._clock_loop(i))

    # -------------------------------------------------------- waiting

    async def until(self, cond, limit_cycles: int, what: str, poll: int = 8) -> int:
        """Wait until ``cond()`` in a's clock cycles; fail past the limit."""
        start = self.bench.cycles[0]
        while not cond():
            n = self.bench.cycles[0] - start
            assert n <= limit_cycles, (
                f"{what}: not reached in {limit_cycles} cycles (a code {self.ports[0].code:#04x}, "
                f"b code {self.ports[1].code:#04x}, a link_up {self.ports[0].link_up}, "
                f"b link_up {self.ports[1].link_up})")
            await ClockCycles(self.dut.a_clk_i, poll, rising=False)
        return self.bench.cycles[0] - start

    async def train(self, limit_cycles: int = 40_000) -> int:
        """Both ports in L0 with link_up; returns a's cycles from start."""
        n = await self.until(self.bench.both_l0, limit_cycles, "training to L0")
        self.log.info("both ports in L0 at a cycle %d", self.bench.cycles[0])
        for i, p in enumerate(self.ports):
            assert p.rcv_errors == 0, f"port {'ab'[i]}: {p.rcv_errors} receiver errors in training"
        return n

    # -------------------------------------------------------- traffic

    def send(self, i: int, n: int, mix: str = "random", nullify_ratio: float = 0.1,
             max_dw: int = 64) -> None:
        """Queue ``n`` packets on port i (0 a, 1 b) for the far port."""
        for _ in range(n):
            if mix == "random":
                kind = "dllp" if self.rng.random() < 0.3 else (
                    "tlp_nullified" if self.rng.random() < nullify_ratio else "tlp")
            else:
                kind = mix
            pkt = make_packet(self.rng, self.seq[i], kind, max_dw)
            self.seq[i] += 1
            self.sb[i].add(pkt)
            self.ports[i].send(*pkt)

    async def drain(self) -> None:
        """Wait until both directions have delivered every packet sent,
        bounded by twice the line time of the larger direction plus slack."""
        line = max(sum(len(p[1]) // 2 + 4 for p in s.sent[s.next:]) for s in self.sb)
        await self.until(lambda: all(s.drained() for s in self.sb), 5000 + 2 * line,
                         "traffic drain")

    async def settle(self, cycles: int = 2000) -> None:
        """Let queued traffic leave the transmitters, then let the line go quiet."""
        await self.until(lambda: all(p.tx_idle() for p in self.ports), 200_000, "transmit queues empty")
        await ClockCycles(self.dut.a_clk_i, cycles, rising=False)

    async def lossless(self, n: int, **kw) -> None:
        """A lossless phase: ``n`` packets each way, all delivered exactly,
        no flagged packet and no receiver error."""
        for s in self.sb:
            s.mark()
        rcv = [p.rcv_errors for p in self.ports]
        self.send(0, n, **kw)
        self.send(1, n, **kw)
        await self.drain()
        for i, s in enumerate(self.sb):
            s.check_lossless()
            p = self.ports[1 - i]
            assert p.rcv_errors == rcv[1 - i], (
                f"port {'ab'[1 - i]}: {p.rcv_errors - rcv[1 - i]} receiver errors in a lossless phase")
        assert all(p.code == L0_CODE and p.link_up for p in self.ports), "left L0 in a lossless phase"

    # ---------------------------------------------------- impairments

    async def impair(self, kind: str, direction: int, traffic: int = 12) -> None:
        """Inject ``kind`` (flip, slip, idle) on the channel ``direction``
        (0 a to b, 1 b to a) while packets flow, then recover through
        Recovery: on its own after an idle line, else by a retrain request
        from the receiving port (the data-link layer's replay timeout).
        Every packet delivered meanwhile must still be exact. Packets still
        in flight when it returns are skipped: the scoreboards expect only
        packets sent afterwards, and one delivered late would fail as out
        of order (``settle`` makes that unlikely)."""
        assert self.bench.channels is not None, "impairments need the channel model"
        ch = self.bench.channels[direction]
        rx = self.ports[1 - direction]
        mark = [len(c) for c in self.codes]
        self.send(direction, traffic)
        await ClockCycles(self.dut.a_clk_i, 60, rising=False)   # inside the traffic
        rcv = rx.rcv_errors
        if kind == "flip":
            ch.flip(3)
        elif kind == "slip":
            ch.slip(self.rng.choice((1, -1)))
        elif kind == "idle":
            ch.idle(48)
        else:
            raise ValueError(f"impairment: expected flip, slip or idle, got {kind}")
        await self.until(lambda: not ch.pending(), 10_000, f"{kind} applied")
        await self.settle()
        assert ch.log, f"{kind}: the channel applied nothing"
        recovered = any(c in RECOVERY_CODES for c in self.codes[1 - direction][mark[1 - direction]:])
        if kind != "idle":
            assert rx.rcv_errors > rcv, f"{kind}: the receiver reported no error"
        if recovered:
            how = "autonomous"
        else:
            how = "directed"
            rx.retrain()
        await self.until(lambda: all(any(c in RECOVERY_CODES for c in self.codes[i][mark[i]:])
                                     for i in (0, 1)), 20_000, f"{kind}: Recovery on both ports")
        await self.until(self.bench.both_l0, 50_000, f"{kind}: back to L0")
        COV_EVENTS.sample(impairment=kind, recovery=how)
        self.log.info("%s on %s: %s recovery; codes a %s b %s; scoreboards: %s", kind,
                      ("a_to_b", "b_to_a")[direction], how,
                      [hex(c) for c in self.codes[0][mark[0]:]],
                      [hex(c) for c in self.codes[1][mark[1]:]],
                      [(s.name, s.delivered, s.lost, s.flagged, s.check_failed) for s in self.sb])
        # Any packet still in flight is replayed by the data-link layer; the
        # stub does not replay, so the next lossless phase starts clean.
        for s in self.sb:
            s.next = len(s.sent)

    def final_checks(self) -> None:
        for i, p in enumerate(self.ports):
            if isinstance(p, ModelPort):
                st = p.pcs.stats
                assert st["overflow"] == 0, f"port {'ab'[i]}: elastic buffer overflowed {st['overflow']} times"
                self.log.info("port %s pcs: %s", "ab"[i], st)
