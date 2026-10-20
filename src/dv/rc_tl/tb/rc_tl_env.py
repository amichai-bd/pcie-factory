"""cocotb environment of pcie_rc_tl (wiki/src/dv/rc_tl/DV_PLAN.md).

One coroutine drives and checks: `tick()` applies the driven inputs for a
cycle, waits for the falling edge of clk_i (after the rising edge that used
them), steps the reference model with the same inputs and compares every
output with it. So every cycle of every test is checked against the model:
cmd_ready_o, rsp_valid_o, rsp_o, tlp_tx_*, msi_valid_o, msi_o, the credit
releases fc_rel_* exactly, and tlp_rx_ready_o.

The link side is driven here too: tlp_tx_ready_i (always, or at random),
the credits, and the receive stream, which carries the TLPs the endpoint
(tb/rc_tl_ep.py) answers each request with, or any TLP a test queues. The
stream keeps the link's credit contract: a non-posted TLP waits while
FC_NPH of them are unreleased. Completions the DUT sends (its completer's)
are collected in `cpls`, not answered.
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

from regress.funcov import Covergroup

sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
from pcie_rc_tl_model import (CFG_RD, CFG_WR, CLS_NP, ENDPOINT_OPS, ERR_CLR,  # noqa: E402
                              HOST_RD, HOST_WR, MEM_RD, MEM_WR, OK, READ, ST_NAMES, T_ATOMICS,
                              T_CPL, T_CPLLK, Beat, Cmd, Inputs, Params, RcTl, Rsp, RxHdr,
                              credit_class, failed_check, is_msg, kind_known, timeout_cycles,
                              tlp_str)
from tb.rc_tl_ep import Endpoint  # noqa: E402

FC_INPUTS = {"fc_ph_avail_i": 0xFF, "fc_pd_avail_i": 0xFFF, "fc_nph_avail_i": 0xFF,
             "fc_npd_avail_i": 0xFFF, "fc_cplh_avail_i": 0xFF, "fc_cpld_avail_i": 0xFFF}

MAX_WAIT = 16    # cycles a local command may wait to be taken or answered


def op_bin(op: int) -> str:
    return str(op) if op <= ERR_CLR else "undef"


ALL_ST = {name: i for i, name in enumerate(ST_NAMES)}
LOCAL_OPS = [str(op) for op in range(4, 10)] + ["undef"]

# Functional coverage of the front end (DV plan, Functional coverage).
COV = Covergroup("rc_tl_cmd")
COV.coverpoint("op", bins={str(i): str(i) for i in range(10)} | {"undef": "undef"})
COV.coverpoint("check", bins={"pass": 0, "link": 1, "op": 2, "len": 3, "addr": 4, "4k": 5,
                              "be": 6, "route": 7})
COV.coverpoint("status", bins=ALL_ST)
COV.coverpoint("dl_up", bins={"down": 0, "up": 1})
COV.cross("op_x_status", "op", "status", unreachable=(
    [(op, st) for op in LOCAL_OPS for st in ("UR", "CA", "CRS", "TIMEOUT", "LINK_DOWN", "BAD")]
    + [("undef", "OK")]
    + [("3", st) for st in ("UR", "CA", "CRS", "TIMEOUT", "BAD")]
    + [("2", "CRS")]))
COV.cross("op_x_check", "op", "check", unreachable=(
    [(str(op), "op") for op in range(10)]
    + [("undef", c) for c in ("pass", "link", "len", "addr", "4k", "be", "route")]
    + [(op, "link") for op in LOCAL_OPS]
    + [(str(op), "4k") for op in (0, 1, 3, 4, 5, 6, 7, 8, 9)]
    + [(str(op), "route") for op in range(4, 10)]
    + [(str(op), "addr") for op in (8, 9)]))
# An endpoint command: dl_up_i at the take and its status.
COV.cross("ep_dl_up_x_status", "dl_up", "status", unreachable=(
    [("down", st) for st in ST_NAMES if st != "LINK_DOWN"]))
# Header access: each DW with RW fields written, and the byte-enable count.
COV.coverpoint("hdr_wr_dw", bins={"cmd": 1, "cls": 3, "bus": 6, "mem": 8,
                                  "ro": lambda v: v not in (1, 3, 6, 8)})
COV.coverpoint("hdr_wr_be", bins={"none": 0, "one": [1, 2, 4, 8], "some": [3, 5, 6, 9, 10, 12],
                                  "three": [7, 11, 13, 14], "all": 15})
COV.cross("hdr_wr_dw_x_be", "hdr_wr_dw", "hdr_wr_be")

# Functional coverage of the requester: each endpoint request's outcome
# (REQUESTER.md), and each completion received with the decision it drew.
REQ_ST = {name: ALL_ST[name] for name in ("OK", "UR", "CA", "CRS", "TIMEOUT", "LINK_DOWN",
                                          "BAD")}
COV_REQ = Covergroup("rc_tl_req")
COV_REQ.coverpoint("op", bins={"CFG_RD": CFG_RD, "CFG_WR": CFG_WR, "MEM_RD": MEM_RD,
                               "MEM_WR": MEM_WR})
COV_REQ.coverpoint("status", bins=REQ_ST)
COV_REQ.coverpoint("beats", bins={"1": 1, "2..8": range(2, 9), "9..31": range(9, 32),
                                  "32": 32})
# Matched completions: none, one, or a split read's several (only a
# MEM_RD has more than one; CRS ends only a configuration request).
COV_REQ.coverpoint("cpls", bins={"0": 0, "1": 1, "2_up": lambda v: v >= 2})
COV_REQ.cross("op_x_status", "op", "status", unreachable=(
    [("MEM_WR", st) for st in ("UR", "CA", "CRS", "TIMEOUT", "BAD")] + [("MEM_RD", "CRS")]))
COV_REQ.cross("status_x_cpls", "status", "cpls", unreachable=(
    [(st, "0") for st in ("UR", "CA", "CRS", "BAD")] + [("CRS", "2_up")]))
COV_CPL = Covergroup("rc_tl_cpl")
COV_CPL.coverpoint("status", bins={"SC": 0, "UR": 1, "CRS": 2, "CA": 4, "rsvd": [3, 5, 6, 7]})
COV_CPL.coverpoint("outcome", bins={"unexpected": "unexp", "data": "data", "ok": "ok",
                                    "UR": "UR", "CA": "CA", "CRS": "CRS", "BAD": "BAD"})
COV_CPL.coverpoint("ep", bins={"0": 0, "1": 1})
COV_CPL.coverpoint("td", bins={"0": 0, "1": 1})
COV_CPL.cross("status_x_outcome", "status", "outcome", unreachable=(
    [(s, o) for s in ("UR", "CRS", "CA", "rsvd") for o in ("data", "ok")]
    + [(s, o) for s in ("UR", "CA", "rsvd") for o in ("CRS",)]
    + [(s, o) for s in ("SC", "CA", "CRS", "rsvd") for o in ("UR",)]
    + [(s, o) for s in ("SC", "UR", "CRS", "rsvd") for o in ("CA",)]
    + [("SC", "CRS"), ("UR", "BAD"), ("CA", "BAD")]))
COV_CPL.cross("td_x_outcome", "td", "outcome")

# Functional coverage of the inbound side (INBOUND.md): each received
# request or message by kind, the outcome the checks and the routing gave
# it and its error record bit; each completion the completer sent; each
# HOST_* by its wait for host memory.
INB_KINDS = ("mem_wr", "mem_rd", "memlk", "io", "cfg", "atomic", "msg", "unknown")
INB_OUTS = ("write", "capture", "read", "ur", "urlk", "drop", "np_drop")
COV_INB = Covergroup("rc_tl_inb")
COV_INB.coverpoint("kind", bins={k: k for k in INB_KINDS})
COV_INB.coverpoint("outcome", bins={o: o for o in INB_OUTS})
COV_INB.coverpoint("err", bins={"none": 0, "mal": 1 << 6, "pois": 1 << 5, "in_ur": 1 << 11,
                                "cor": 1 << 7, "nonfatal": 1 << 8, "fatal": 1 << 9,
                                "msg": 1 << 10})
COV_INB.coverpoint("hdr4", bins={"3dw": 0, "4dw": 1})
COV_INB.coverpoint("td", bins={"0": 0, "1": 1})
COV_INB.coverpoint("msi", bins={"captured": "captured", "dropped": "dropped"})
COV_INB.cross("kind_x_outcome", "kind", "outcome", unreachable=(
    [("mem_wr", o) for o in ("read", "ur", "urlk", "np_drop")]
    + [("mem_rd", o) for o in ("write", "capture", "urlk", "drop")]
    + [("memlk", o) for o in ("write", "capture", "read", "ur", "drop")]
    + [(k, o) for k in ("io", "cfg") for o in ("write", "capture", "read", "urlk", "drop")]
    + [("atomic", o) for o in ("write", "capture", "read", "urlk", "drop", "np_drop")]
    + [("msg", o) for o in ("write", "capture", "read", "ur", "urlk", "np_drop")]
    + [("unknown", o) for o in ("write", "capture", "read", "ur", "urlk")]))
COV_CPLR = Covergroup("rc_tl_cplr")
COV_CPLR.coverpoint("status", bins={"SC": 0, "UR": 1})
COV_CPLR.coverpoint("locked", bins={"0": 0, "1": 1})
COV_CPLR.coverpoint("dws", bins={"0": 0, "1": 1, "2..16": range(2, 17), "17..31": range(17, 32),
                                 "32": 32})
COV_CPLR.coverpoint("part", bins={"first": "first", "later": "later"})
COV_CPLR.coverpoint("bc", bins={"4096": 0, "other": range(1, 4096)})
COV_CPLR.cross("status_x_dws", "status", "dws", unreachable=(
    [("UR", d) for d in ("1", "2..16", "17..31", "32")] + [("SC", "0")]))
COV_HOST = Covergroup("rc_tl_host")
COV_HOST.coverpoint("op", bins={"HOST_RD": HOST_RD, "HOST_WR": HOST_WR})
COV_HOST.coverpoint("wait", bins={"0": 0, "1": 1, "2..15": range(2, 16), "16..33": range(16, 34)})
COV_HOST.cross("op_x_wait", "op", "wait")


def inb_kind(h: RxHdr) -> str:
    if not kind_known(h.fmt, h.typ) or h.typ in (T_CPL, T_CPLLK):
        return "unknown"
    if is_msg(h.typ):
        return "msg"
    if h.typ in T_ATOMICS:
        return "atomic"
    return {0x00: "mem_wr" if h.wdata else "mem_rd", 0x01: "memlk", 0x02: "io",
            0x04: "cfg", 0x05: "cfg"}[h.typ]


class Env:
    """Drives pcie_rc_tl_tb through its ports and checks it every cycle."""

    def __init__(self, dut, seed: int = 0) -> None:
        self.dut = dut
        self.rng = random.Random(seed)
        self.random_req_witness = None  # enabled only by the long requester campaign
        self.p = Params()
        self.m = RcTl(self.p)
        self.ep = Endpoint()
        self.cycle = 0
        self.toggle_idle = False      # drive msi_ready_i at random each cycle
        self.tx_ready_p = 1.0         # probability tlp_tx_ready_i is 1 in a cycle
        self.rx_gap_p = 0.0           # probability of an idle cycle inside a TLP
        self.answer = True            # the endpoint answers every request
        self.answer_delay = (2, 2)    # cycles from a request's eop to its answer
        self.drv = {"rst_i": 1, "cmd_valid_i": 0, "cmd_i": 0, "dl_up_i": 1,
                    "msi_ready_i": 0, "tlp_tx_ready_i": 1, "tlp_rx_valid_i": 0,
                    "tlp_rx_data_i": 0, "tlp_rx_sop_i": 0, "tlp_rx_eop_i": 0} | FC_INPUTS
        self.rx_q: deque[tuple[int, list[int]]] = deque()   # (idle cycles before, TLP)
        self.rx_cur: list[int] | None = None
        self.rx_idx = 0
        self.tx_prev = Beat()
        self.tx_tlp: list[int] = []
        self.sent: list[list[int]] = []                    # every TLP the DUT sent
        self.beats: list[Rsp] = []                         # every result beat
        self.beat_cycles: list[int] = []                   # the cycle of each
        self.tx_sop_cycles: list[int] = []                 # cycles the link took a sop
        self.tx_eop_cycles: list[int] = []                 # ... an eop
        self.rx_hdr_cycles: list[int] = []                 # a completion's third DW taken
        self.rx_eop_cycles: list[int] = []                 # cycles an eop was driven
        self.takes: list[tuple[int, Cmd, Rsp]] = []        # (cycle, command, first beat)
        self.results: list[tuple[Cmd, list[Rsp]]] = []     # each command with its beats
        self.pending: tuple[Cmd, int, int, int, int] | None = None
        self.cpl_count = 0
        self.hooks: list = []         # f(env) -> keep, called before each cycle's drive
        self.np_sops = 0              # non-posted sops the link took since rst
        self.cpls: list[list[int]] = []        # every completion the DUT sent
        self.cpl_cycles: list[int] = []        # the cycle the link took each one's eop
        self.np_out = 0               # non-posted TLPs sent and not released
        self.rel_p: list[tuple[int, int]] = []     # (cycle, data credits) of each release
        self.rel_np: list[tuple[int, int]] = []
        self.msi_taken: list[tuple[int, int]] = []  # (offset, data) taken by msi_ready_i
        self.prev_msi: tuple[int, int] | None = None   # the capture offered in this cycle

    @property
    def fe(self) -> RcTl:
        """The model (its header, error record and parameters)."""
        return self.m

    # ------------------------------------------------------------ set-up

    async def start(self, rst_cycles: int = 8) -> None:
        cocotb.start_soon(Clock(self.dut.clk_i, 8, unit="ns").start())
        await self.reset(rst_cycles)

    async def reset(self, cycles: int = 8) -> None:
        self.drv["rst_i"] = 1
        self.drv["cmd_valid_i"] = 0
        self.np_sops = 0
        for _ in range(cycles):
            await self.tick()
        prm = int(self.dut.params_o.value)
        p = Params(vendor_id=prm & 0xFFFF, device_id=(prm >> 16) & 0xFFFF,
                   revision_id=(prm >> 32) & 0xFF, host_mem_addr_w=(prm >> 40) & 0x1F,
                   cpl_timeout_div=(prm >> 45) & 0x7FF, host_mem_base=(prm >> 56) & 0xFFFF_FFFF,
                   msi_addr=(prm >> 88) & 0xFFFF_FFFF, fc_nph=(prm >> 120) & 0x7F)
        if p != self.p:
            # The model is in reset like the DUT: a fresh one with the
            # parameters is the same state (host memory unknown until written).
            self.p = p
            self.m = RcTl(p)
        self.drv["rst_i"] = 0

    @property
    def n_tmo(self) -> int:
        return timeout_cycles(self.p.cpl_timeout_div)

    # ------------------------------------------------------------ link side

    def queue_rx(self, tlp: list[int], idle: int = 0) -> None:
        """Send tlp on the receive stream after `idle` idle cycles."""
        self.rx_q.append((idle, list(tlp)))

    def drive_link(self) -> None:
        d = self.drv
        if d["rst_i"] or not d["dl_up_i"]:
            # The link abandons what it holds and restarts its credits (dll
            # HAS, link down).
            self.rx_cur, self.rx_q = None, deque()
            self.tx_tlp = []
            self.np_out = 0
        d["tlp_tx_ready_i"] = int(self.tx_ready_p >= 1 or self.rng.random() < self.tx_ready_p)
        d["tlp_rx_valid_i"] = d["tlp_rx_sop_i"] = d["tlp_rx_eop_i"] = 0
        if self.rx_cur is None and self.rx_q:
            idle, tlp = self.rx_q[0]
            if idle > 0:
                self.rx_q[0] = (idle - 1, tlp)
                return
            b0 = tlp[0] & 0xFF
            np = credit_class(b0 >> 5, b0 & 0x1F) == CLS_NP
            if np and self.np_out >= self.p.fc_nph:
                return                   # no non-posted header credit
            self.np_out += np
            self.rx_q.popleft()
            self.rx_cur, self.rx_idx = tlp, 0
        if self.rx_cur is None:
            return
        if self.rx_idx > 0 and self.rng.random() < self.rx_gap_p:
            return
        d["tlp_rx_valid_i"] = 1
        d["tlp_rx_data_i"] = self.rx_cur[self.rx_idx]
        d["tlp_rx_sop_i"] = int(self.rx_idx == 0)
        d["tlp_rx_eop_i"] = int(self.rx_idx == len(self.rx_cur) - 1)
        if d["tlp_rx_eop_i"]:
            self.rx_eop_cycles.append(self.cycle)
        self.rx_idx += 1
        if self.rx_idx == len(self.rx_cur):
            self.rx_cur = None

    def tx_taken(self, ready: int, up: int) -> None:
        """The beat offered before this edge, taken when ready (and up)."""
        b = self.tx_prev
        if not (b.valid and ready and up):
            return
        if b.sop:
            self.tx_tlp = []
            self.tx_sop_cycles.append(self.cycle)
            if b.data & 0xFF in (0x00, 0x04, 0x44):      # MEM_RD, CFG_RD, CFG_WR
                self.np_sops += 1
        self.tx_tlp.append(b.data)
        if b.eop:
            self.tx_eop_cycles.append(self.cycle)
            tlp = self.tx_tlp
            self.tx_tlp = []
            if tlp[0] & 0x1F in (T_CPL, T_CPLLK):
                # The completer's completion: collected, not answered.
                self.cpls.append(tlp)
                self.cpl_cycles.append(self.cycle)
                return
            self.sent.append(tlp)
            if self.answer:
                for cpl in self.ep.receive(tlp):
                    self.queue_rx(cpl, self.rng.randint(*self.answer_delay))

    # ------------------------------------------------------------ the cycle

    async def tick(self) -> tuple[bool, Rsp | None]:
        """One cycle: apply the inputs, wait for the falling edge, step the
        model and compare every output. Returns (taken, beat)."""
        d = self.dut
        if self.toggle_idle:
            self.drv["msi_ready_i"] = self.rng.getrandbits(1)
        self.hooks = [h for h in self.hooks if h(self)]
        self.drive_link()
        applied = dict(self.drv)
        for name, value in applied.items():
            getattr(d, name).value = value
        await FallingEdge(d.clk_i)
        cmd = Cmd.unpack(applied["cmd_i"])
        check = failed_check(cmd, self.m.hdr, self.p, applied["dl_up_i"])
        o = self.m.step(Inputs(
            rst=applied["rst_i"], cmd_valid=applied["cmd_valid_i"], cmd=cmd,
            dl_up=applied["dl_up_i"], tx_ready=applied["tlp_tx_ready_i"],
            rx_valid=applied["tlp_rx_valid_i"], rx_data=applied["tlp_rx_data_i"],
            rx_sop=applied["tlp_rx_sop_i"], rx_eop=applied["tlp_rx_eop_i"],
            fc_ph=applied["fc_ph_avail_i"], fc_pd=applied["fc_pd_avail_i"],
            fc_nph=applied["fc_nph_avail_i"], fc_npd=applied["fc_npd_avail_i"],
            fc_cplh=applied["fc_cplh_avail_i"], fc_cpld=applied["fc_cpld_avail_i"],
            msi_ready=applied["msi_ready_i"]))
        witness = self.random_req_witness
        if witness is not None:
            witness["reset_cycles"] += int(bool(applied["rst_i"]))
            witness["link_down_cycles"] += int(not applied["dl_up_i"])
            witness["credit_outage_cycles"] += int(any(applied[name] == 0 for name in (
                "fc_ph_avail_i", "fc_pd_avail_i", "fc_nph_avail_i", "fc_npd_avail_i")))
            witness["event_mask"] |= o["events"]
        beat, take = o["beat"], o["take"]
        where = f"cycle {self.cycle}"
        self.expect(where, "cmd_ready_o", o["cmd_ready"])
        self.expect(where, "rsp_valid_o", int(beat is not None))
        got = int(d.rsp_o.value)
        want = beat.pack() if beat is not None else 0
        # Data bits from host memory never written are unspecified.
        care = ~((~beat.known & 0xFFFF_FFFF) << 1) if beat is not None else -1
        assert (got ^ want) & care == 0, (f"{where}: rsp_o expected {beat} got {Rsp.unpack(got)}"
                                          + (f" for {cmd}" if take else ""))
        tx = o["tx"]
        got_tx = (int(d.tlp_tx_valid_o.value), int(d.tlp_tx_data_o.value),
                  int(d.tlp_tx_sop_o.value), int(d.tlp_tx_eop_o.value))
        got_tx = (got_tx[0], (got_tx[1] & tx.known) | (tx.data & ~tx.known & 0xFFFF_FFFF),
                  got_tx[2], got_tx[3])
        assert got_tx == (tx.valid, tx.data, tx.sop, tx.eop), (
            f"{where}: tlp_tx valid/data/sop/eop expected {tx.valid}/{tx.data:08x}/{tx.sop}/"
            f"{tx.eop} got {got_tx[0]}/{got_tx[1]:08x}/{got_tx[2]}/{got_tx[3]}")
        msi = o["msi"]
        self.expect(where, "msi_valid_o", int(msi is not None))
        self.expect(where, "msi_o", 0 if msi is None else (msi[0] << 32) | msi[1])
        for name, rel in (("p", o["rel_p"]), ("np", o["rel_np"])):
            self.expect(where, f"fc_rel_{name}_o", int(rel is not None))
            self.expect(where, f"fc_rel_{name}d_o", rel or 0)
        if o["rel_np"] is not None:
            self.np_out = max(0, self.np_out - 1)
            self.rel_np.append((self.cycle, o["rel_np"]))
        if o["rel_p"] is not None:
            self.rel_p.append((self.cycle, o["rel_p"]))
        if self.prev_msi is not None and applied["msi_ready_i"]:
            self.msi_taken.append(self.prev_msi)
        self.prev_msi = msi
        self.expect(where, "tlp_rx_ready_o", 1 - applied["rst_i"])
        self.tx_taken(applied["tlp_tx_ready_i"], applied["dl_up_i"] and not applied["rst_i"])
        self.tx_prev = tx
        if o.get("cpl") is not None:
            self.rx_hdr_cycles.append(self.cycle)
            self.sample_cpl(*o["cpl"])
        if o.get("rx") is not None:
            rx = o["rx"]
            COV_INB.sample(kind=inb_kind(rx.h), outcome=rx.out, err=rx.err, hdr4=rx.h.fmt & 1,
                           td=rx.h.td)
        if o.get("msi_push") is not None:
            COV_INB.sample(msi="dropped" if o["events"] & (1 << 12) else "captured")
        if o.get("cpl_sent") is not None:
            e, t = o["cpl_sent"]
            b = t[0] & 0xFF
            COV_CPLR.sample(status=(t[1] >> 21) & 7, locked=int(b & 0x1F == T_CPLLK),
                            dws=(t[0] >> 24) & 0xFF if b & 0x40 else 0,
                            part="first" if e.kind != READ or self.m.cp.i == 0 else "later",
                            bc=((t[1] >> 8) & 0xF00) | (t[1] >> 24))
        if applied["rst_i"]:
            self.pending = None
        if take:
            self.pending = (cmd, check, applied["dl_up_i"], len(self.beats), self.cpl_count,
                            self.cycle)
        if beat is not None:
            self.beats.append(beat)
            self.beat_cycles.append(self.cycle)
            if take:
                self.takes.append((self.cycle, cmd, beat))
            if beat.last and self.pending is not None:
                c, chk, up, first, cpls, at = self.pending
                self.pending = None
                beats = self.beats[first:]
                self.results.append((c, beats))
                self.sample(c, chk, beats, up, self.cpl_count - cpls)
                if c.op in (HOST_RD, HOST_WR) and chk == 0:
                    # Uncontended, the beat is 1 (HOST_WR) or 2 (HOST_RD)
                    # cycles after the take's (INBOUND.md, Host memory).
                    COV_HOST.sample(op=c.op, wait=self.cycle - at - (2 if c.op == HOST_RD else 1))
        self.cycle += 1
        return take, beat

    async def apply(self) -> None:
        """Apply the driven inputs now and settle, inside the current cycle,
        to read combinational outputs; the next tick() keeps them."""
        for name, value in self.drv.items():
            getattr(self.dut, name).value = value
        await Timer(1, unit="ns")

    def expect(self, where: str, name: str, want: int) -> None:
        got = int(getattr(self.dut, name).value)
        assert got == want, f"{where}: {name} expected {want} got {got}"

    def sample(self, c: Cmd, check: int, beats: list[Rsp], dl_up: int, cpls: int) -> None:
        status = beats[-1].status
        if self.random_req_witness is not None and c.op == MEM_RD and check == 0:
            if status == OK and cpls >= 2:
                self.random_req_witness["split_success"] += 1
            if status != OK and len(beats) > 1:
                self.random_req_witness["partial_failure"] += 1
        kw = {"op": op_bin(c.op), "check": check, "status": status}
        if c.op in ENDPOINT_OPS:
            kw["dl_up"] = dl_up
        if c.op == 5 and status == OK:
            kw |= {"hdr_wr_dw": (c.addr >> 2) & 0x3FF, "hdr_wr_be": c.first_be}
        COV.sample(**kw)
        if c.op in ENDPOINT_OPS and check == 0:
            COV_REQ.sample(op=c.op, status=status, beats=len(beats), cpls=cpls)

    def sample_cpl(self, f: dict, dec) -> None:
        if dec.match:
            self.cpl_count += 1
        if not dec.match:
            outcome = "unexp"
        elif dec.fin:
            outcome = "ok" if dec.status == OK else ST_NAMES[dec.status]
        else:
            outcome = "data"
        COV_CPL.sample(status=f["status"], outcome=outcome, ep=f["ep"], td=f["td"])

    # ------------------------------------------------------------ commands

    async def command(self, c: Cmd, dl_up: int | None = None,
                      wait: int | None = None) -> list[Rsp]:
        """Offer c until taken; return its result beats, the last with last 1.
        A local command or a refusal has one beat, in the cycle after the
        take."""
        if dl_up is not None:
            self.drv["dl_up_i"] = dl_up
        self.drv["cmd_valid_i"] = 1
        self.drv["cmd_i"] = c.pack()
        for _ in range(MAX_WAIT):
            take, beat = await self.tick()
            if take:
                break
        else:
            raise AssertionError(f"cycle {self.cycle}: {c} not taken in {MAX_WAIT} cycles")
        self.drv["cmd_valid_i"] = 0
        first = len(self.beats) - (beat is not None)
        if beat is None:
            # Only an endpoint command passing checks 1 to 7 has no beat at once.
            bound = wait if wait is not None else self.n_tmo + 1024
            for _ in range(bound):
                await self.tick()
                if self.beats[first:] and self.beats[-1].last:
                    break
            else:
                raise AssertionError(
                    f"cycle {self.cycle}: {c}: no last beat in {bound} cycles; model request "
                    f"{self.m.req}, link {dict((k, v) for k, v in self.drv.items() if k[:2] in ('fc', 'dl'))}, "
                    f"tx ready p {self.tx_ready_p}, receive queue {len(self.rx_q)}")
        beats = self.beats[first:]
        assert beats[-1].last == 1 and all(b.last == 0 for b in beats[:-1]), (
            f"{c}: beats {[str(b) for b in beats]}")
        return beats

    async def offer(self, c: Cmd) -> int:
        """Offer c until taken (within MAX_WAIT cycles); return the index
        its result will have in `results`."""
        k = len(self.results)
        self.drv["cmd_valid_i"], self.drv["cmd_i"] = 1, c.pack()
        for _ in range(MAX_WAIT):
            take, _ = await self.tick()
            if take:
                break
        else:
            raise AssertionError(f"cycle {self.cycle}: {c} not taken in {MAX_WAIT} cycles")
        self.drv["cmd_valid_i"] = 0
        return k

    async def command_beats(self, k: int, bound: int | None = None) -> list[Rsp]:
        """Wait for result k (from offer) and return its beats."""
        bound = bound if bound is not None else self.n_tmo + 1024
        for _ in range(bound):
            if len(self.results) > k:
                return self.results[k][1]
            await self.tick()
        raise AssertionError(f"cycle {self.cycle}: no result in {bound} cycles")

    def next_tag(self) -> int:
        """The tag of the next non-posted request: it steps at each
        non-posted sop the link takes, from 0 out of rst (REQUESTER.md,
        Tags)."""
        return self.np_sops % 32

    async def expect_cmd(self, c: Cmd, status: int, data: int | None = None,
                         dl_up: int | None = None) -> list[Rsp]:
        """c finishes with this status on its last beat (every other beat
        OK) and, when given, this data on it: spec values from the test,
        besides the model's per-cycle check."""
        beats = await self.command(c, dl_up)
        last = beats[-1]
        assert last.status == status and all(b.status == OK for b in beats[:-1]), (
            f"cycle {self.cycle}: {c}: expected {ST_NAMES[status]}, got "
            f"{[str(b) for b in beats]}")
        if data is not None:
            assert last.data == data, (f"cycle {self.cycle}: {c}: expected data "
                                       f"0x{data:08x}, got {last}")
        return beats

    async def hdr_rd(self, idx: int) -> int:
        beats = await self.command(Cmd(4, idx << 2))
        assert beats == [Rsp(OK, beats[0].data)], f"HDR_RD {idx}: {beats}"
        return beats[0].data

    async def hdr_wr(self, idx: int, data: int, be: int = 0xF) -> None:
        beats = await self.command(Cmd(5, idx << 2, first_be=be, data=data))
        assert beats == [Rsp(OK)], f"HDR_WR {idx}: {beats}"

    async def err_rd(self) -> int:
        beats = await self.command(Cmd(8))
        assert len(beats) == 1 and beats[0].status == OK, f"ERR_RD: {beats}"
        return beats[0].data

    async def err_clr(self, bits: int = 0xFFFF_FFFF) -> None:
        beats = await self.command(Cmd(9, data=bits))
        assert beats == [Rsp(OK)], f"ERR_CLR: {beats}"

    async def idle(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.tick()

    async def drain(self, cycles: int = 64) -> None:
        """Idle until the receive stream is empty, then `cycles` more."""
        for _ in range(4096):
            if self.rx_cur is None and not self.rx_q:
                break
            await self.tick()
        else:
            raise AssertionError(f"cycle {self.cycle}: receive stream not drained")
        await self.idle(cycles)

    def last_sent(self) -> str:
        return tlp_str(self.sent[-1]) if self.sent else "nothing"
