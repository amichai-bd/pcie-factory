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
from cocotb.utils import get_sim_time

from regress.funcov import Covergroup

sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
# The mac transmit scrambler's cycle model (src/dv/mac/models), a block.
sys.path.append(str(Path(__file__).resolve().parents[2] / "mac" / "models"))
import pcie_mac_scrambler_model  # noqa: E402
# The pcs_enc8b10b sub-IP's cycle model, a transmit-encoder block.
sys.path.append(str(Path(__file__).resolve().parents[2] / "pcs_enc8b10b" / "models"))
import pcie_pcs_enc8b10b_model  # noqa: E402
import pcie_phy_stack_model as stack  # noqa: E402
from pcie_phy_pcs_model import code8b10b  # noqa: E402
from pcie_phy_stack_model import L0_CODE, RECOVERY_CODES, ModelPort  # noqa: E402

# The 10-bit codes of STP and SDP at either running disparity: a packet's
# first symbol on the line (always in [9:0] of a word, HAS Packet stream).
FRAME_START_CODES = frozenset(code8b10b.encode(k, True, rd)[0]
                              for k in (code8b10b.K_STP, code8b10b.K_SDP) for rd in (-1, 1))
POLLING_ACTIVE_CODE = stack.mac_arch.CODES[stack.mac_arch.PA]
REQUESTS = ("hot_reset_req_i", "disable_req_i", "loopback_req_i", "pm_state_req_i")

CORE_PS = 8000
B_PHASE_PS = 2960            # b's first edge, so the two clocks rarely coincide
LAYERS = ("mac", "pcs", "channel")

# ------------------------------------------------------------ coverage

COV_LAYERS = Covergroup("phy_e2e_layers")
for _layer in LAYERS:
    COV_LAYERS.coverpoint(_layer, bins={"model": "model", "rtl": "rtl"})
COV_LAYERS.cross("mac_x_pcs", "mac", "pcs")
# Block-level swaps inside a model layer (the DV plan's switch table).
COV_LAYERS.coverpoint("tx_scrambler", bins={"mac_model": "mac_model", "block_model": "block_model",
                                             "rtl": "rtl"})
COV_LAYERS.coverpoint("tx_encoder", bins={"pcs_model": "pcs_model", "block_model": "block_model",
                                           "rtl": "rtl"})

COV_RUN = Covergroup("phy_e2e_run")
COV_RUN.coverpoint("a_role", bins={"downstream": "downstream", "upstream": "upstream"})
COV_RUN.coverpoint("ppm", bins={"pos_300": 300, "neg_300": -300, "pos_600": 600, "neg_600": -600,
                                "other": lambda v: v not in (300, -300, 600, -600)})
COV_RUN.cross("role_x_ppm", "a_role", "ppm")

COV_EVENTS = Covergroup("phy_e2e_events")
COV_EVENTS.coverpoint("impairment", bins={"flip": "flip", "slip": "slip", "idle": "idle"})
COV_EVENTS.coverpoint("recovery", bins={"directed": "directed", "autonomous": "autonomous"})
# A port reset while packets flow (RTL ports): reset values held, retrained.
COV_EVENTS.coverpoint("reset", bins={"a": "a", "b": "b"})

# Reset values of pcie_phy's outputs, held while rst_i is high
# (wiki/src/arch/phy/HAS.md#clocks-and-resets); ltssm_state_o 0 is
# Detect.Quiet.
RESET_VALUES = {
    "pkt_tx_ready_o": 0, "pkt_rx_valid_o": 0, "pkt_rx_sop_o": 0, "pkt_rx_eop_o": 0,
    "pkt_rx_err_o": 0, "pkt_rx_nullify_o": 0, "link_up_o": 0, "ltssm_state_o": 0,
    "rcv_err_o": 0, "ser_tx_elec_idle_o": 1,
}

# Upper-layer inputs an RtlPort toggles at random while its rst_i is high
# (port_role_i stays: it changes only in reset, and the role is the run's).
RESET_NOISE = {
    "pkt_tx_valid_i": 1, "pkt_tx_data_i": 16, "pkt_tx_sop_i": 1, "pkt_tx_eop_i": 1,
    "pkt_tx_dllp_i": 1, "pkt_tx_nullify_i": 1, "retrain_req_i": 1, "hot_reset_req_i": 1,
    "disable_req_i": 1, "loopback_req_i": 1, "pm_state_req_i": 2,
}

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
    (the HAS makes ready a function of the MAC's state, not of valid; it
    holds from one rising edge to the next)."""

    kind = "rtl"

    def __init__(self, dut, prefix: str, role: str, rng: random.Random | None = None) -> None:
        self.role = role
        self.name = prefix.rstrip("_")
        self.rng = rng                     # set: random upper-layer inputs while in reset
        self._rst_q = False                # rst_i was driven 1 at the last falling edge
        self._take = False                 # the beat on offer is taken at the next rising edge
        self.edges = 0
        self.reset_checks = 0              # sampled edges checked against RESET_VALUES
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
        self.req = dict.fromkeys(REQUESTS, 0)   # level requests, driven outside reset
        self.trace = False                 # log beats and frame starts with times
        self.tx_log: list = []             # (edge, sop, eop, rising-edge time ps) per beat taken
        self.tx_starts: list = []          # (edge, time ps) per STP/SDP sent in [9:0]
        self.rx_sops: list = []            # (edge, time ps) per sop beat delivered
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
        self.edges += 1
        # Sample (outputs registered at the last rising edge). After a rising
        # edge with rst_i high, every output holds its reset value.
        if self._rst_q:
            for name, want in RESET_VALUES.items():
                got = int(s[name].value)
                assert got == want, (
                    f"port {self.name} ({self.role}) in reset, edge {self.edges}: {name} "
                    f"expected {want}, observed {got}")
            self.reset_checks += 1
        if self.trace:
            t_rise = get_sim_time("ps") - CORE_PS // 2
        if self.offer is not None and self._take:
            if self.trace:
                self.tx_log.append((self.edges, self.offer[1], self.offer[2], t_rise))
            self.offer = None              # taken at the rising edge just passed
        ready = int(s["pkt_tx_ready_o"].value)   # holds through the next rising edge
        if int(s["pkt_rx_valid_o"].value):
            beat = int(s["pkt_rx_data_o"].value)
            if int(s["pkt_rx_sop_o"].value):
                if self.trace:
                    self.rx_sops.append((self.edges, t_rise))
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
        if self.trace and not ei and (word & 0x3FF) in FRAME_START_CODES:
            self.tx_starts.append((self.edges, t_rise))
        # Drive.
        s["rst_i"].value = int(self.rst)
        s["ser_tx_rxdet_i"].value = int(rxdet)
        self._rst_q = self.rst
        self._take = False
        if self.rst:
            # A reset drops the packet in progress (the data-link layer would
            # replay it); whole queued packets stay queued.
            self.offer = None
            while self.tx and not self.tx[0][1]:
                self.tx.popleft()
            for name, width in RESET_NOISE.items():
                s[name].value = self.rng.getrandbits(width) if self.rng else 0
            return word, ei
        for name, v in self.req.items():
            s[name].value = v
        s["retrain_req_i"].value = int(self._retrain)
        self._retrain = False
        if self.offer is None and self.tx:
            self.offer = self.tx.popleft()
        if self.offer is None:
            s["pkt_tx_valid_i"].value = 0
        else:
            data, sop, eop, dllp, nullify = self.offer
            self._take = bool(ready)
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

    def request(self, **kw) -> None:
        """Set level requests: ``hot_reset``, ``disable``, ``loopback`` (0/1)
        and ``pm_state`` (0..3); held until changed, dropped in reset."""
        for k, v in kw.items():
            name = f"{k}_req_i"
            assert name in self.req, f"request: unknown {k}"
            self.req[name] = int(v)


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
        self.err_timeout_div = int(dut.cfg_err_timeout_div_o.value)
        self.a_role, self.ppm = a_role, ppm
        self.log.info("phy e2e: COCOTB_RANDOM_SEED %s, test seed %d, layers %s, a %s, b %s, b clock %+g ppm, "
                      "TIMEOUT_DIV %d, ERR_TIMEOUT_DIV %d, tx scrambler %s, tx encoder %s",
                      os.environ.get("COCOTB_RANDOM_SEED", "-"), seed,
                      " ".join(f"{k}={v}" for k, v in self.layers.items()),
                      a_role, stack.other(a_role), ppm, self.timeout_div, self.err_timeout_div,
                      tx_scrambler, tx_encoder)
        if tx_scrambler not in ("mac_model", "block_model"):
            raise ValueError(f"tx_scrambler: expected mac_model or block_model, got {tx_scrambler}")
        if tx_encoder not in ("pcs_model", "block_model"):
            raise ValueError(f"tx_encoder: expected pcs_model or block_model, got {tx_encoder}")
        # With a layer in RTL, its transmit scrambler or encoder is the RTL
        # block inside it (the DV plan's step 0).
        COV_LAYERS.sample(**self.layers,
                          tx_scrambler="rtl" if self.layers["mac"] == "rtl" else tx_scrambler,
                          tx_encoder="rtl" if self.layers["pcs"] == "rtl" else tx_encoder)
        COV_RUN.sample(a_role=a_role, ppm=ppm)
        roles = (a_role, stack.other(a_role))
        if self.layers["mac"] != self.layers["pcs"]:
            raise NotImplementedError(
                "mac and pcs switch together until the stack RTL exposes PIPE "
                "(wiki/src/dv/phy/DV_PLAN.md, model-to-RTL switch plan)")
        if self.layers["channel"] == "rtl" and self.layers["pcs"] == "model":
            raise ValueError("an RTL channel needs the pcs as RTL (CHAN_RTL needs PCS_RTL)")
        if self.layers["mac"] == "model":
            ports = [ModelPort(r, self.timeout_div, err_timeout_div=self.err_timeout_div,
                               tx_scrambler=(pcie_mac_scrambler_model.WordScrambler()
                                             if tx_scrambler == "block_model" else None),
                               tx_encoder=(pcie_pcs_enc8b10b_model.Encoder()
                                           if tx_encoder == "block_model" else None))
                     for r in roles]
        else:
            if (tx_scrambler, tx_encoder) != ("mac_model", "pcs_model"):
                raise ValueError("block swaps apply only inside the model layers")
            ports = [RtlPort(dut, f"{p}_", r, random.Random(self.rng.getrandbits(32)))
                     for p, r in zip("ab", roles)]
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
        # Link-up times per port, in its own cycles: reset release, first
        # Polling.Active, link_up_o rising (wiki/src/arch/phy/HAS.md#targets).
        self.linkups: tuple[list, list] = ([], [])
        self._lu = [{"release": None, "poll": None}, {"release": None, "poll": None}]

    def _monitor(self, bench, i: int) -> None:
        p = self.ports[i]
        lu, n = self._lu[i], bench.cycles[i]
        if p.rst:
            lu.update(release=n + 1, poll=None, up=p.link_up)
        elif lu["release"] is not None:
            if lu["poll"] is None and p.code == POLLING_ACTIVE_CODE:
                lu["poll"] = n
            if p.link_up and not lu["up"]:
                self.linkups[i].append((lu["release"], lu["poll"], n))
                lu["release"] = None
            lu["up"] = p.link_up
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

    def check_link_up(self, max_us: float, train_us: tuple = (65.536, 80.0),
                      min_us: float = 0.0) -> list:
        """The HAS link-up targets on each port's last link-up from reset:
        release of rst_i to link_up_o within [``min_us``, ``max_us``], and
        Polling.Active entry to L0 within ``train_us``. Returns the times."""
        out = []
        for i in (0, 1):
            assert self.linkups[i], f"port {'ab'[i]}: no link-up from reset recorded"
            rel, poll, up = self.linkups[i][-1]
            assert poll is not None, f"port {'ab'[i]}: link-up without Polling.Active"
            lu_us, tr_us = (up - rel) * CORE_PS / 1e6, (up - poll) * CORE_PS / 1e6
            self.log.info("port %s: reset release to link_up %.3f us (%d cycles), "
                          "Polling.Active to L0 %.3f us", "ab"[i], lu_us, up - rel, tr_us)
            assert min_us <= lu_us <= max_us, (
                f"port {'ab'[i]}: link-up {lu_us:.3f} us, expected {min_us} to {max_us} us")
            assert train_us[0] <= tr_us <= train_us[1], (
                f"port {'ab'[i]}: training {tr_us:.3f} us, expected {train_us[0]} to {train_us[1]} us")
            out.append((lu_us, tr_us))
        return out

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

    async def lossless(self, n: int, l0s: bool = False, **kw) -> None:
        """A lossless phase: ``n`` packets each way, all delivered exactly,
        no flagged packet and no receiver error, both ports in L0 at the end
        (with ``l0s``, L0 with either L0s sub-machine anywhere)."""
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
        in_l0 = (lambda c: c & 0xF0 == L0_CODE) if l0s else (lambda c: c == L0_CODE)
        assert all(in_l0(p.code) and p.link_up for p in self.ports), (
            f"left L0 in a lossless phase: codes {[hex(p.code) for p in self.ports]}")

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

    async def reset_ports(self, which: tuple = (0, 1), cycles: int = 16, skew: int = 0,
                          traffic: int = 12, limit_cycles: int = 40_000) -> int:
        """Assert the rst_i of the RTL ports in ``which`` (0 a, 1 b) while
        packets flow both ways: a's for ``cycles`` of its edges, b's for
        ``cycles`` + ``skew`` of its own. Every output of a port in reset
        holds its reset value from the first rising edge in reset (RtlPort
        checks each sampled edge). Both ports then leave L0 (link_up_o
        falls on each; a port not reset loses its partner and goes through
        Recovery to Detect) and train back to L0. Packets cut by the reset
        are the data-link layer's to replay, so the scoreboards skip what
        was in flight. Returns a's cycles from the reset to both in L0.

        A port reset alone keeps its partner in Recovery until the error-path
        timeouts, so that case needs a small ``ERR_TIMEOUT_DIV`` and a
        larger ``limit_cycles``."""
        assert all(isinstance(p, RtlPort) for p in self.ports), "reset_ports drives RTL ports"
        self.send(0, traffic)
        self.send(1, traffic)
        await ClockCycles(self.dut.a_clk_i, 60, rising=False)   # inside the traffic
        checks = [p.reset_checks for p in self.ports]
        downs = [False, False]

        def watch(bench, j):
            downs[j] |= not bench.ports[j].link_up

        self.bench.hooks.append(watch)
        held = {i: cycles + (skew if i else 0) for i in which}
        for i, n in held.items():
            self.bench.reset(i, n)
        n = await self.until(lambda: all(downs) and all(self.bench.cycles[i] > self.bench.release[i]
                                                        for i in which)
                             and self.bench.both_l0(), limit_cycles,
                             f"reset of {'+'.join('ab'[i] for i in which)} mid-traffic: both ports back in L0")
        self.bench.hooks.remove(watch)
        for i, want in held.items():
            p = self.ports[i]
            assert p.reset_checks - checks[i] >= want - 1, (
                f"port {'ab'[i]}: expected at least {want - 1} edges checked in reset, "
                f"observed {p.reset_checks - checks[i]}")
            COV_EVENTS.sample(reset="ab"[i])
        self.log.info("reset of %s mid-traffic (%d cycles, skew %d): both in L0 again after %d cycles; "
                      "scoreboards: %s", "+".join("ab"[i] for i in which), cycles, skew, n,
                      [(s.name, s.delivered, s.lost, s.flagged, s.check_failed) for s in self.sb])
        await self.settle()
        for s in self.sb:
            s.next = len(s.sent)
        return n

    def final_checks(self) -> None:
        for i, p in enumerate(self.ports):
            if isinstance(p, ModelPort):
                st = p.pcs.stats
                assert st["overflow"] == 0, f"port {'ab'[i]}: elastic buffer overflowed {st['overflow']} times"
                self.log.info("port %s pcs: %s", "ab"[i], st)
