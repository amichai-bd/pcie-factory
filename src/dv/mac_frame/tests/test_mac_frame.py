"""pcie_mac_frame tests (wiki/src/dv/mac_frame/DV_PLAN.md).

Inputs are driven at the falling edge of clk_i; outputs are sampled 1 ns
later, so the combinational transmit outputs (ready, grant, the line word)
are seen for the inputs of the same cycle, and the registered receive
outputs for the words of earlier cycles.

References: ``TxRef`` (the MAS transmit rules, cycle exact), ``RxRef`` (the
mac architecture model's receive parser, imported) and the upper packets
themselves (the HAS lossless rule). Transmit tests loop the line back into
the receive half one cycle later, with ``SkpSched`` standing in for the
ordered-set transmitter's SKP schedule.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

from models import pcie_mac_frame_model as m
from regress.funcov import Covergroup

arch = m.arch

# ------------------------------------------------------------- coverage

COV_TX = Covergroup("mac_frame_tx")
COV_TX.coverpoint("outcome", bins=[
    "tlp_end", "tlp_nullify", "tlp_abort_l0", "tlp_abort_bubble",
    "dllp_end", "dllp_nullify_ignored", "dllp_abort_l0", "dllp_abort_bubble"])
COV_TX.coverpoint("ready_low", bins=["skp", "stop", "not_l0", "tail"])
COV_TX.coverpoint("size", bins={"one_beat": 2, "short": range(4, 64),
                                "long": range(64, 1180), "over_skp_interval": range(1180, 8192)})

COV_SKP = Covergroup("mac_frame_skp")
COV_SKP.coverpoint("placement", bins=["immediate", "deferred"])
COV_SKP.coverpoint("burst", bins=["single", "back_to_back"])

COV_RX = Covergroup("mac_frame_rx")
COV_RX.coverpoint("outcome", bins=["good", "nullify", "cut", "status", "odd",
                                   "dllp_edb", "dllp_len"])
COV_RX.coverpoint("kind", bins=["tlp", "dllp"])
COV_RX.coverpoint("start_lane", bins=[0, 1])
COV_RX.cross("kind_x_start_lane", "kind", "start_lane")

COV_RX_EV = Covergroup("mac_frame_rx_events")
COV_RX_EV.coverpoint("event", bins=[
    "com_in_pkt", "start_in_pkt", "k_in_pkt", "resync_discard", "end_outside",
    "k_outside", "data_outside", "cut_l0", "cut_valid", "cut_ei", "status", "empty"])


# ------------------------------------------------------------------ env

class Driver:
    """The upper layer's transmit side: offers packets, keeps valid from
    sop to eop (or leaves a bubble with probability ``p_bubble``)."""

    def __init__(self, rng, p_offer, p_bubble, max_tlp, p_big=0.0):
        self.rng, self.p_offer, self.p_bubble = rng, p_offer, p_bubble
        self.max_tlp, self.p_big = max_tlp, p_big
        self.cur = None          # packet being offered or sent
        self.beat = 0
        self.started = False     # its sop was accepted
        self.valid = 0
        self.script = []         # directed packets, sent first

    def next_packet(self):
        if self.script:
            return self.script.pop(0)
        if self.rng.random() < self.p_big:
            return arch.Packet(False, m.rand_bytes(self.rng, 2 * self.rng.randint(700, 1400)))
        p = m.rand_packet(self.rng, self.max_tlp)
        if p.dllp and self.rng.random() < 0.1:
            p.nullify = True     # ignored for a DLLP
        return p

    def drive(self):
        if self.cur is None and (self.script or self.rng.random() < self.p_offer):
            self.cur, self.beat, self.started = self.next_packet(), 0, False
        if self.cur is None:
            self.valid = 0
            return 0, 0, 0, 0, 0, 0
        p, i = self.cur, self.beat
        n = len(p.data) // 2
        self.valid = int(not self.started or self.rng.random() >= self.p_bubble)
        data = p.data[2 * i] | p.data[2 * i + 1] << 8
        return self.valid, data, int(i == 0), int(i == n - 1), int(p.dllp), int(p.nullify)

    def accepted(self):
        self.started = True
        self.beat += 1
        if self.beat == len(self.cur.data) // 2:
            self.cur = None

    def abandon(self):
        self.cur = None


class Env:
    def __init__(self, dut, rng):
        self.dut, self.rng = dut, rng
        self.cycle = 0
        self.new_refs()

    def new_refs(self):
        self.tx = m.TxRef()
        self.skp = m.SkpSched()
        self.rx = m.RxRef()
        self.line_prev = None        # line word of the previous cycle (loopback)
        self.ref_pkts = []           # (packet, cycle, cause, start lane) from RxRef
        self.rtl_beats = []
        self.ferr_pipe = [0]         # RxRef framing error of the previous cycle
        self.idle_pipe = [[0, 0]]
        self.expected = []           # what the upper layer sent: packets or ("abort", dllp)
        self.line_pkt = None         # (packet, beats) on the transmit line
        self.delivered = 0
        self.skp_wait = False        # SKP due last cycle and not granted
        self.skp_words = 0

    async def start(self):
        cocotb.start_soon(Clock(self.dut.clk_i, 10, unit="ns").start())
        await self.drive_idle(rst=1, n=3)

    def set(self, **kw):
        for k, v in kw.items():
            getattr(self.dut, k).value = v

    async def drive_idle(self, rst=0, n=1):
        for _ in range(n):
            await FallingEdge(self.dut.clk_i)
            self.set(rst_i=rst, pkt_tx_valid_i=0, pkt_tx_data_i=0, pkt_tx_sop_i=0,
                     pkt_tx_eop_i=0, pkt_tx_dllp_i=0, pkt_tx_nullify_i=0, tx_l0_i=0,
                     tx_stop_i=0, rx_l0_i=0, skp_req_i=0, rx_data_i=0, rx_datak_i=0,
                     rx_valid_i=0, rx_elec_idle_i=1, rx_status_i=0)
        await Timer(1, "ns")

    def out(self, name):
        return int(getattr(self.dut, name).value)

    # ------------------------------------------------------- receive side

    def check_rx(self, rx_word):
        """Feed this cycle's receive word to RxRef and compare the RTL's
        registered outputs (which reflect earlier words)."""
        c = self.cycle
        syms, valid, ei, status, rx_l0 = rx_word
        # Registered status outputs belong to the previous cycle's word.
        got_ferr, got_idle = self.out("rx_frame_err_o"), self.out("rx_idle_o")
        exp_ferr, exp_idle = self.ferr_pipe.pop(0), self.idle_pipe.pop(0)
        assert got_ferr == int(exp_ferr), (
            f"cycle {c}: rx_frame_err_o expected {int(exp_ferr)} got {got_ferr}")
        assert got_idle == exp_idle[0] | exp_idle[1] << 1, (
            f"cycle {c}: rx_idle_o expected {exp_idle} got {got_idle:02b}")
        # A beat on the upper stream.
        if self.out("pkt_rx_valid_o"):
            self.rx_beat()
        else:
            for f in ("pkt_rx_sop_o", "pkt_rx_eop_o", "pkt_rx_err_o", "pkt_rx_nullify_o"):
                assert self.out(f) == 0, f"cycle {c}: {f} high without valid"
        # This cycle's word into the reference.
        self.rx.events = []
        pkts, ferr, idle, ends = self.rx.word(syms, valid, ei, status, rx_l0)
        for ev in self.rx.events:
            COV_RX_EV.sample(event=ev)
        for p, lane, cause in ends:
            self.ref_pkts.append((p, c, cause, lane))
        assert len(pkts) == len(ends)
        self.ferr_pipe.append(ferr)
        self.idle_pipe.append(idle if valid and not ei else [0, 0])

    def rx_beat(self):
        c = self.cycle
        b = dict(data=self.out("pkt_rx_data_o"), sop=self.out("pkt_rx_sop_o"),
                 eop=self.out("pkt_rx_eop_o"), dllp=self.out("pkt_rx_dllp_o"),
                 err=self.out("pkt_rx_err_o"), nullify=self.out("pkt_rx_nullify_o"))
        assert b["sop"] == int(not self.rtl_beats), (
            f"cycle {c}: pkt_rx_sop_o {b['sop']} on beat {len(self.rtl_beats)} of a packet")
        if not b["eop"]:
            assert not b["err"] and not b["nullify"], f"cycle {c}: err/nullify without eop"
        self.rtl_beats.append(b)
        if not b["eop"]:
            return
        beats, self.rtl_beats = self.rtl_beats, []
        assert self.ref_pkts, f"cycle {c}: RTL delivered a packet the reference did not end"
        ref, t_ref, cause, lane = self.ref_pkts.pop(0)
        data = b"".join(bytes([x["data"] & 0xFF, x["data"] >> 8]) for x in beats)
        got = (bool(b["dllp"]), bool(b["err"]), bool(b["nullify"]))
        exp = (ref.dllp, ref.err, ref.nullify)
        assert got == exp and all(x["dllp"] == b["dllp"] for x in beats), (
            f"cycle {c}: packet {self.delivered} flags (dllp, err, nullify) expected {exp} "
            f"got {got}")
        n = len(ref.data)
        assert len(data) == n + n % 2 and data[:n] == ref.data, (
            f"cycle {c}: packet {self.delivered} data expected {ref.data.hex()} "
            f"got {data.hex()}")
        # Latency: the eop beat at most 3 cycles after the reference ended
        # the packet (its first word reached the sub-IP one cycle after it
        # left the line in loopback runs).
        assert c - t_ref <= 3, (
            f"cycle {c}: packet {self.delivered} eop {c - t_ref} cycles after its end")
        COV_RX.sample(outcome=cause, kind="dllp" if ref.dllp else "tlp", start_lane=lane)
        self.delivered += 1
        self.check_lossless(ref)

    def check_lossless(self, ref):
        """Loopback runs: each delivered packet is the next one the upper
        layer sent, aborted ones arriving with err."""
        if not self.expected:
            return
        exp = self.expected.pop(0)
        if isinstance(exp, tuple):
            assert ref.err and ref.dllp == exp[1], (
                f"cycle {self.cycle}: aborted packet expected err, got {ref}")
        else:
            assert ref == exp, f"cycle {self.cycle}: expected {exp} got {ref}"

    # ------------------------------------------------------ transmit side

    async def tx_cycle(self, drv, tx_l0, tx_stop, rst=0, loop=True, rx_word=None):
        """One cycle: drive the transmit inputs (and the receive word: the
        loopback of the previous line word, or ``rx_word``), compare with
        TxRef and RxRef."""
        await FallingEdge(self.dut.clk_i)
        self.cycle += 1
        valid, data, sop, eop, dllp, nullify = drv.drive()
        req = self.skp.req
        if loop:
            lw = self.line_prev
            rx_word = ((lw if lw else ((0, 0), (0, 0))), lw is not None, lw is None, 0, True)
        syms = rx_word[0]
        self.set(rst_i=rst, pkt_tx_valid_i=valid, pkt_tx_data_i=data, pkt_tx_sop_i=sop,
                 pkt_tx_eop_i=eop, pkt_tx_dllp_i=dllp, pkt_tx_nullify_i=nullify,
                 tx_l0_i=tx_l0, tx_stop_i=tx_stop, skp_req_i=req,
                 rx_data_i=syms[0][1] | syms[1][1] << 8, rx_datak_i=syms[0][0] | syms[1][0] << 1,
                 rx_valid_i=int(rx_word[1]), rx_elec_idle_i=int(rx_word[2]),
                 rx_status_i=rx_word[3], rx_l0_i=int(rx_word[4]))
        await Timer(1, "ns")
        c = self.cycle
        line_open_before, up_open_before = self.tx.line_open, self.tx.up_open
        o = self.tx.step(rst, valid, data, sop, eop, dllp, nullify, tx_l0, tx_stop, req)
        for name, exp in (("pkt_tx_ready_o", o.ready), ("skp_gnt_o", o.gnt),
                          ("tx_act_o", o.act), ("tx_pkt_o", o.pkt)):
            got = self.out(name)
            assert got == exp, f"cycle {c}: {name} expected {exp} got {got}"
        if o.act:
            got = (self.out("tx_data_o"), self.out("tx_datak_o"))
            assert got == (o.data, o.datak), (
                f"cycle {c}: line word expected {o.data:04x}/{o.datak:02b} "
                f"got {got[0]:04x}/{got[1]:02b}")
        if rst:
            for name in ("pkt_rx_valid_o", "pkt_rx_sop_o", "pkt_rx_eop_o", "pkt_rx_err_o",
                         "pkt_rx_nullify_o", "rx_frame_err_o", "rx_idle_o"):
                assert self.out(name) == 0, f"cycle {c}: {name} not 0 in reset"
            drv.abandon()
            self.line_prev = None
            return o
        # Back-pressure between packets, and its reason (HAS, Transmit).
        if not up_open_before and not o.ready:
            for reason, on in (("skp", req), ("stop", tx_stop), ("not_l0", not tx_l0),
                               ("tail", line_open_before)):
                if on:
                    COV_TX.sample(ready_low=reason)
        self.track_upper(drv, o, valid, eop, nullify, tx_l0)
        if valid and o.ready:
            drv.accepted()
        # SKP: granted at the first word with no packet open on the line.
        if o.gnt:
            if self.skp.words_left == 0:
                COV_SKP.sample(placement="deferred" if self.skp_wait else "immediate")
                COV_SKP.sample(burst="back_to_back" if self.skp_words else "single")
            self.skp_words += 1
        else:
            self.skp_words = 0
        self.skp_wait = bool(req and not o.gnt)
        skp_word = self.skp.step(o.gnt, bool(o.act or o.gnt))
        if o.act:
            line = ((o.datak & 1, o.data & 0xFF), (o.datak >> 1, o.data >> 8))
        else:
            line = skp_word
        self.check_rx(rx_word)
        self.line_prev = line
        return o

    def track_upper(self, drv, o, valid, eop, nullify, tx_l0):
        """The upper packets the receive side must deliver, from TxRef's
        events: whole packets, or ("abort", dllp) for an aborted one."""
        if o.event == "sop":
            self.line_pkt = drv.cur
            if self.line_pkt.data and len(self.line_pkt.data) <= 8192:
                COV_TX.sample(size=len(self.line_pkt.data))
        elif o.event in ("end", "edb_end") and self.line_pkt is not None:
            p = self.line_pkt
            exp = arch.Packet(p.dllp, p.data, nullify=p.nullify and not p.dllp)
            self.expected.append(exp)
            if p.dllp:
                COV_TX.sample(outcome="dllp_nullify_ignored" if p.nullify else "dllp_end")
            else:
                COV_TX.sample(outcome="tlp_nullify" if p.nullify else "tlp_end")
            self.line_pkt = None
        elif o.event == "abort" and self.line_pkt is not None:
            p = self.line_pkt
            self.expected.append(("abort", p.dllp))
            why = "l0" if not tx_l0 else "bubble"
            COV_TX.sample(outcome=f"{'dllp' if p.dllp else 'tlp'}_abort_{why}")
            self.line_pkt = None


class Markov:
    """A control that is ``on`` most of the time and drops for a while."""

    def __init__(self, rng, p_drop, max_low, on=1):
        self.rng, self.p_drop, self.max_low, self.on = rng, p_drop, max_low, on
        self.low = 0

    def step(self):
        if self.low:
            self.low -= 1
        elif self.rng.random() < self.p_drop:
            self.low = self.rng.randint(1, self.max_low)
        return self.on if not self.low else 1 - self.on


async def run_tx(env, cycles, max_tlp, p_l0_drop, p_stop, p_bubble, p_big=0.0, p_offer=0.6):
    rng = env.rng
    drv = Driver(rng, p_offer, p_bubble, max_tlp, p_big)
    l0 = Markov(rng, p_l0_drop, 40)
    stop = Markov(rng, p_stop, 30, on=0)
    for _ in range(cycles):
        await env.tx_cycle(drv, l0.step(), stop.step())
    # Drain: L0, no new packets, until the line and the receive side empty.
    drv.p_offer = 0.0
    drv.p_bubble = 0.0
    for _ in range(80 + 2 * max(len(drv.cur.data) if drv.cur else 0, 1)):
        await env.tx_cycle(drv, 1, 0)
    assert not env.expected and not env.ref_pkts, (
        f"undelivered: {len(env.expected)} expected, {len(env.ref_pkts)} reference packets")
    dut_log(env, f"transmit run: {env.delivered} packets delivered, "
            f"{len(env.rx.rx_packets)} parsed by the reference")
    assert env.delivered > 0, "vacuous run: no packet delivered"


def rx_words(rng, count, p_err, p_cut, p_status, max_tlp):
    """Receive words from random items: cut cycles, RxStatus errors and
    Rx_L0 drops are added per word."""
    syms = m.gen_rx_items(rng, count, p_err, max_tlp)
    l0 = Markov(rng, p_cut / 4, 12)
    i = 0
    while i < len(syms):
        rx_l0 = l0.step()
        r = rng.random()
        if r < p_cut:
            yield ((0, 0), (0, 0)), rng.random() < 0.5, rng.random() < 0.5, 0, rx_l0
            continue
        w = tuple(syms[i:i + 2]) + ((0, 0),) * (2 - len(syms[i:i + 2]))
        i += 2
        status = rng.randint(4, 7) if rng.random() < p_status else rng.choice((0, 0, 0, 1, 2))
        yield w, True, False, status, rx_l0


async def run_rx(env, count, p_err, p_cut, p_status, max_tlp):
    drv = Driver(env.rng, 0.0, 0.0, 2)
    n = 0
    for w in rx_words(env.rng, count, p_err, p_cut, p_status, max_tlp):
        await env.tx_cycle(drv, 0, 0, loop=False, rx_word=w)
        n += 1
    for _ in range(8):
        await env.tx_cycle(drv, 0, 0, loop=False,
                           rx_word=(((0, 0), (0, 0)), True, False, 0, True))
    assert not env.ref_pkts, f"{len(env.ref_pkts)} reference packets not delivered"
    dut_log(env, f"receive run: {n} words, {env.delivered} packets, "
            f"{env.rx.framing_errors} framing errors in the reference")
    assert env.delivered > 0 and env.rx.framing_errors > 0 or p_err == 0, "vacuous run"


def dut_log(env, msg):
    env.dut._log.info(msg)


def make_env(dut):
    seed = cocotb.RANDOM_SEED
    dut._log.info("random seed %d", seed)
    return Env(dut, random.Random(seed))


# ---------------------------------------------------------------- tests

@cocotb.test()
async def smoke(dut):
    """L0: reset values, directed framing, abort by L0 exit and by a bubble,
    reset inside a packet, then short random transmit and receive runs."""
    env = make_env(dut)
    await env.start()
    for name in ("pkt_tx_ready_o", "pkt_rx_valid_o", "skp_gnt_o", "tx_act_o", "tx_pkt_o",
                 "rx_frame_err_o", "rx_idle_o"):
        assert env.out(name) == 0, f"{name} not 0 in reset"
    drv = Driver(env.rng, 0.0, 0.0, 8)
    # Reset wins even with L0 and a packet offered.
    drv.script = [arch.Packet(False, bytes(range(1, 9)))]
    o = await env.tx_cycle(drv, 1, 0, rst=1)
    assert o.ready == 0
    await env.drive_idle(rst=0, n=1)
    env.new_refs()
    # A TLP, a DLLP and a nullified TLP, back to back.
    drv.script = [arch.Packet(False, bytes(range(1, 9))), arch.Packet(True, bytes(range(6))),
                  arch.Packet(False, bytes([7, 7]), nullify=True)]
    words = []
    for _ in range(16):
        o = await env.tx_cycle(drv, 1, 0)
        words.append((o.data, o.datak))
    assert words[:6] == [(0x01FB, 1), (0x0302, 0), (0x0504, 0), (0x0706, 0), (0xFD08, 2),
                         (0x005C, 1)], f"framed words {[(hex(a), b) for a, b in words[:6]]}"
    assert env.delivered == 3 and not env.expected, "three packets round trip"
    # L0 exit inside a packet: EDB, then the rest is taken and discarded.
    drv.script = [arch.Packet(False, bytes(range(16)))]
    await env.tx_cycle(drv, 1, 0)
    await env.tx_cycle(drv, 1, 0)
    o = await env.tx_cycle(drv, 0, 0)
    assert (o.event, o.data, o.datak, o.act) == ("abort", m.EDB, 1, 1)
    for _ in range(8):
        o = await env.tx_cycle(drv, 0, 0)
    assert drv.cur is None and o.ready == 0
    # A bubble inside a packet: EDB in L0.
    drv.script = [arch.Packet(True, bytes(range(6)))]
    await env.tx_cycle(drv, 1, 0)
    drv.p_bubble = 1.0
    o = await env.tx_cycle(drv, 1, 0)
    assert o.event == "abort"
    drv.p_bubble = 0.0
    for _ in range(10):
        await env.tx_cycle(drv, 1, 0)
    assert not env.expected, "aborted packets arrive with err"
    # Reset inside a packet: ready falls at once; afterwards a new packet
    # starts cleanly.
    drv.script = [arch.Packet(False, bytes(range(20)))]
    await env.tx_cycle(drv, 1, 0)
    o = await env.tx_cycle(drv, 1, 0, rst=1)
    assert o.ready == 0 and env.out("tx_act_o") == 0
    await env.drive_idle(rst=0, n=1)
    env.new_refs()
    drv.script = [arch.Packet(False, bytes(range(4)))]
    for _ in range(10):
        await env.tx_cycle(drv, 1, 0)
    assert env.delivered == 1 and not env.expected
    env.new_refs()
    await env.drive_idle(rst=1, n=2)
    await run_tx(env, 1500, max_tlp=32, p_l0_drop=0.005, p_stop=0.01, p_bubble=0.01)
    await env.drive_idle(rst=1, n=2)
    env.new_refs()
    await run_rx(env, 300, p_err=0.2, p_cut=0.02, p_status=0.01, max_tlp=32)


@cocotb.test()
async def tx_random(dut):
    """L1: random packet streams with back-pressure (SKP due, stop, L0
    exits), bubbles and aborts, looped back; SKP placement checked."""
    env = make_env(dut)
    await env.start()
    await run_tx(env, 20000, max_tlp=128, p_l0_drop=0.002, p_stop=0.005, p_bubble=0.002,
                 p_big=0.003)


@cocotb.test()
async def rx_random(dut):
    """L1: random receive streams: packets in both lanes, ordered sets,
    framing errors, cuts and RxStatus errors, against the arch parser."""
    env = make_env(dut)
    await env.start()
    await run_rx(env, 3000, p_err=0.15, p_cut=0.03, p_status=0.01, max_tlp=64)


@cocotb.test()
async def tx_long(dut):
    """L2: long transmit runs with large TLPs (SKP deferred past more than
    one interval: back-to-back SKP)."""
    env = make_env(dut)
    await env.start()
    await run_tx(env, 50000, max_tlp=512, p_l0_drop=0.001, p_stop=0.003, p_bubble=0.001,
                 p_big=0.005)


@cocotb.test()
async def rx_long(dut):
    """L2: long random receive runs."""
    env = make_env(dut)
    await env.start()
    await run_rx(env, 8000, p_err=0.1, p_cut=0.02, p_status=0.005, max_tlp=256)
