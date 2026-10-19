"""Stand-in for pcie_rc_tl, and for the link and endpoint behind it, at the
tl-seq interface (wiki/src/arch/rc/TL_SEQ.md), for the rc_seq testbench
(wiki/src/dv/rc_seq/DV_PLAN.md).

It conforms to TL_SEQ: commands taken with a random ready delay, results in
command order as result beats (a ``MEM_RD`` of ``len`` DWs as ``len`` beats
with random gaps, a local command's first beat possibly in the take cycle),
the field checks and outbound routing answering ``LOCAL_UR``, endpoint
commands answering ``LINK_DOWN`` while ``dl_up`` is 0, the error record and
the MSI capture of depth 4 whose overflow sets bit 12; nothing is offered in
reset.

Behind it, a functional endpoint at ``core``'s defaults as the programs
page needs it (PROGRAMS, Boundary): its configuration registers (IDs,
capabilities, BAR sizing, MSI, command), BAR0 as memory with the DMA
doorbell and the no-answer DW, Unsupported Requests outside its BARs with
their error messages, and a link that comes up, goes down on a Hot Reset
request and, in ``ERRORS``, once under traffic. Two modes:

- ``m4``: the link trains by itself after reset and after a Hot Reset;
- ``self`` (``SELFTEST``): the link is up but DL_Down until the program's
  ``HOST_WR`` to ``SELF_KICK_LINK``; a ``HOST_WR`` to ``SELF_KICK_MSI``
  queues one MSI; Hot Reset takes the link down for good; host reads of the
  poll offsets follow ``host_sched``.

Test hooks: ``corrupt`` (command number -> (beat, xor)), ``status_over``
(command number -> status of its last beat), ``msi_corrupt`` (take number
-> xor of the offered data), and the wait offsets ``c_link``, ``c_msi``,
``c_hr`` in cycles from the waiting step's cycle 0 (MAS, Timing: the cycle
0 of a step after a REQ is its last beat plus 3).
"""

from __future__ import annotations

import heapq
import random
from collections import deque
from dataclasses import dataclass, field

import pcie_rc_seq_model as m

# SELFTEST's passing poll answers: loops A and B exit after exactly
# SELF_LIMIT takes; loop C takes 5 times, its forward jump setting L 0 on
# every pass (PROGRAMS, Self-test).
SELF_SCHED = {m.SELF_POLL_A: [0, 0, 0, 1], m.SELF_POLL_B: [0, 0, 0, 1],
              m.SELF_POLL_C: [4, 4, 4, 4, 4, 5]}

HOST_BYTES = 4096
CAPTURE_DEPTH = 4
CPL_TIMEOUT = 300           # stand-in completion timeout, cycles
STEP0 = 3                   # last beat to the next step's cycle 0 (MAS, Timing)

# Endpoint configuration registers at core defaults: offset -> (reset, RW mask).
EP_REGS = {
    0x00: (0x0000_0000, 0),
    0x04: (0x0010_0000, 0x0000_0006),     # Status Capabilities List; Command MSE, BME
    0x0C: (0x0000_0000, 0),
    0x10: (0x0000_0000, 0xFFFF_F000),     # BAR0, 4 KiB
    0x18: (0x0000_0000, 0xFFFF_F000),     # BAR2, 4 KiB
    0x34: (0x0000_0040, 0),
    0x40: (0x0000_5001, 0),
    0x50: (0x0000_6005, 0x0001_0000),     # MSI Enable
    0x54: (0x0000_0000, 0xFFFF_FFFC),
    0x58: (0x0000_0000, 0xFFFF_FFFF),
    0x5C: (0x0000_0000, 0x0000_FFFF),
    0x60: (0x0000_7011, 0),
    0x70: (0x0000_0010, 0),
    0x78: (0x0000_0000, 0x0000_00FF),     # Device Control: error reporting enables
}

# Root-port Type 1 header: offset -> (reset, RW mask) (rc HAS, Type 1 header).
HDR_REGS = {
    0x00: (m.RC_IDS, 0),
    0x04: (0, 0x0000_0006),
    0x08: (0x0604_0000 | m.RC_REVISION, 0),
    0x0C: (0x0001_0000, 0x0000_00FF),
    0x18: (0, 0x00FF_FFFF),
    0x20: (0, 0xFFF0_FFF0),
}

E_UR_CPL, E_CA, E_CRS, E_TIMEOUT = 0, 1, 2, 3
E_COR, E_NONFATAL, E_INBOUND_UR, E_MSI_DROP = 7, 8, 11, 12


def be_mask(be: int) -> int:
    return sum(0xFF << 8 * i for i in range(4) if be >> i & 1)


def write_reg(regs: dict, defaults: dict, off: int, data: int, be: int) -> None:
    if off in defaults:
        rw = defaults[off][1] & be_mask(be)
        regs[off] = (regs[off] & ~rw) | (data & rw)


@dataclass
class Command:
    op: int
    addr: int
    len: int
    fbe: int
    lbe: int
    data: int


@dataclass
class Result:
    beats: list                    # [(status, data)]
    delay: int                     # cycles from the take to the first beat
    on_done: list = field(default_factory=list)   # callables(last_beat_cycle)


class StandIn:
    def __init__(self, rng: random.Random, mode: str = "m4", *, corrupt=None, status_over=None,
                 msi_corrupt=None, host_sched=None, c_link=0, c_msi=0, c_hr=0,
                 same_cycle=0.25, ready_max=2, gap_max=2, linkdown_after=None):
        if mode not in ("m4", "self"):
            raise ValueError(mode)
        self.rng, self.mode = rng, mode
        self.corrupt = dict(corrupt or {})
        self.status_over = dict(status_over or {})
        self.msi_corrupt = dict(msi_corrupt or {})
        if host_sched is None:
            host_sched = SELF_SCHED if mode == "self" else {}
        self.host_sched = {k: list(v) for k, v in host_sched.items()}
        self.c_link, self.c_msi, self.c_hr = c_link, c_msi, c_hr
        self.same_cycle, self.ready_max, self.gap_max = same_cycle, ready_max, gap_max
        self.linkdown_after = (rng.randint(1, 4) if linkdown_after is None else linkdown_after)
        self.host = bytearray(HOST_BYTES)
        self.bar0 = {}
        self.ncmd = 0                   # commands taken
        self.ntake = 0                  # MSI captures taken
        self.log: list[tuple] = []      # (cycle, command number, Command, statuses)
        self.now = 0
        self._reset(0)

    # ------------------------------------------------------------ state
    def _reset(self, now: int) -> None:
        self.events: list = []
        self.eseq = 0
        self.beats: deque = deque()     # (cycle, status, data, last)
        self.cmd_at = None              # the cycle the offered command is taken
        self.capture: deque = deque()
        self.err = 0
        self.hdr = {k: v[0] for k, v in HDR_REGS.items()}
        self._ep_reset()
        self.dma: deque = deque()
        self.dma_busy = False
        self.hr_active = False
        self.flag2_reads = None
        self.link_up = self.dl_up = False
        self.in_reset = True

    def _ep_reset(self) -> None:
        self.ep = {k: v[0] for k, v in EP_REGS.items()}

    def _at(self, cycle: int, fn) -> None:
        heapq.heappush(self.events, (cycle, self.eseq, fn))
        self.eseq += 1

    def _link(self, up: bool, now: int, dl_lag: int = 0) -> None:
        self.link_up = up
        if not up:
            self.dl_up = False
            self._ep_reset()
        elif dl_lag:
            self._at(now + dl_lag, lambda n: self._dl(True))
        else:
            self.dl_up = True

    def _dl(self, up: bool) -> None:
        if self.link_up:
            self.dl_up = up

    def set_err(self, bit: int) -> None:
        self.err |= 1 << bit

    # ------------------------------------------------------------ cycle
    def drive(self, now: int, rst: int, cmd_valid: int, cmd: tuple, hot_reset_req: int) -> m.Inputs:
        """The rc_seq inputs of cycle ``now`` from the stand-in's state and
        rc_seq's registered outputs of that cycle."""
        self.now = now
        if rst:
            self._reset(now)
            return m.Inputs()
        if self.in_reset:
            self.in_reset = False
            if self.mode == "self":
                self.link_up = True
            else:
                self._at(now + self.rng.randint(10, 40),
                         lambda n: self._link(True, n, self.rng.randint(2, 8)))
        while self.events and self.events[0][0] <= now:
            _, _, fn = heapq.heappop(self.events)
            fn(now)
        # Hot Reset: link_up falls c_hr cycles after the request rises.
        if hot_reset_req and not self.hr_active:
            self.hr_active = True
            delay = self.c_hr if self.mode == "self" else self.rng.randint(1, 10)
            if delay == 0:
                self._link(False, now)
            else:
                self._at(now + delay, lambda n: self._link(False, n))
        elif not hot_reset_req and self.hr_active:
            self.hr_active = False
            if self.mode == "m4":
                self._at(now + self.rng.randint(30, 80),
                         lambda n: self._link(True, n, self.rng.randint(2, 8)))
        i = m.Inputs(dl_up=int(self.dl_up), link_up=int(self.link_up))
        if cmd_valid:
            if self.cmd_at is None:
                self.cmd_at = now + self.rng.randint(0, self.ready_max)
            if now >= self.cmd_at:
                i.cmd_ready = 1
                self.cmd_at = None
                self._take(now, Command(*cmd))
        if self.beats and self.beats[0][0] == now:
            _, i.rsp_status, i.rsp_data, i.rsp_last = self.beats.popleft()
            i.rsp_valid = 1
        if self.capture:
            i.msi_valid = 1
            i.msi_offset, i.msi_data = self.capture[0]
            i.msi_data ^= self.msi_corrupt.get(self.ntake, 0)
        return i

    def commit(self, now: int, i: m.Inputs, msi_ready: int) -> None:
        """The clock edge ending cycle ``now``: a capture taken."""
        if msi_ready and i.msi_valid:
            self.capture.popleft()
            self.ntake += 1

    # ------------------------------------------------------- commands
    def _take(self, now: int, c: Command) -> None:
        k = self.ncmd
        self.ncmd += 1
        r = self._answer(now, c)
        beats = list(r.beats)
        if k in self.corrupt:
            b, x = self.corrupt[k]
            st, d = beats[b]
            beats[b] = (st, d ^ x)
        if k in self.status_over:
            st, d = beats[-1]
            beats[-1] = (self.status_over[k], d)
        t = now + r.delay
        if self.beats:
            t = max(t, self.beats[-1][0] + 1)
        for j, (st, d) in enumerate(beats):
            last = int(j == len(beats) - 1)
            self.beats.append((t, st, d, last))
            if not last:
                t += 1 + self.rng.randint(0, self.gap_max)
        for fn in r.on_done:
            self._at(t, fn) if t > now else fn(t)
        self.log.append((now, k, c, tuple(st for st, _ in beats)))

    def _local_delay(self) -> int:
        return 0 if self.rng.random() < self.same_cycle else self.rng.randint(1, 3)

    def _ep_delay(self) -> int:
        return self.rng.randint(4, 16)

    def _answer(self, now: int, c: Command) -> Result:
        endpoint = c.op in (m.CFG_RD, m.CFG_WR, m.MEM_RD, m.MEM_WR)
        if c.op > m.ERR_CLR:
            return Result([(m.LOCAL_UR, 0)], self._local_delay())
        if endpoint and not self.dl_up:
            return Result([(m.LINK_DOWN, 0)], self._local_delay())
        if not self._fields_ok(c):
            return Result([(m.LOCAL_UR, 0)], self._local_delay())
        if c.op == m.HDR_RD:
            return Result([(m.OK, self.hdr.get(c.addr & 0xFFC, 0))], self._local_delay())
        if c.op == m.HDR_WR:
            write_reg(self.hdr, HDR_REGS, c.addr & 0xFFC, c.data, c.fbe)
            return Result([(m.OK, 0)], self._local_delay())
        if c.op == m.HOST_RD:
            sched = self.host_sched.get(c.addr)
            if sched:
                v = sched.pop(0) if len(sched) > 1 else sched[0]
            else:
                v = int.from_bytes(self.host[c.addr:c.addr + 4], "little")
            return Result([(m.OK, v)], self._local_delay())
        if c.op == m.HOST_WR:
            self._host_write(c.addr, c.data, c.fbe)
            r = Result([(m.OK, 0)], self._local_delay())
            if self.mode == "self" and c.addr == m.SELF_KICK_LINK:
                r.on_done.append(lambda t: self._at(t + STEP0 + self.c_link,
                                                    lambda n: setattr(self, "dl_up", True)))
            if self.mode == "self" and c.addr == m.SELF_KICK_MSI:
                d = c.data
                r.on_done.append(lambda t: self._at(t + STEP0 + self.c_msi,
                                                    lambda n: self._msi((d >> 16) & 0x3FF, d)))
            if c.addr == m.RUN_FLAG and c.data == 2:
                self.flag2_reads = self.linkdown_after
            return r
        if c.op == m.ERR_RD:
            return Result([(m.OK, self.err)], self._local_delay())
        if c.op == m.ERR_CLR:
            self.err &= ~c.data & m.ERR_MASK
            return Result([(m.OK, 0)], self._local_delay())
        if c.op in (m.CFG_RD, m.CFG_WR):
            return self._cfg(c)
        return self._mem(now, c)

    def _fields_ok(self, c: Command) -> bool:
        if c.op == m.MEM_RD:
            if not 1 <= c.len <= 32 or c.addr & 3:
                return False
            return (c.addr & 0xFFF) + 4 * c.len <= 0x1000
        if c.len != 1:
            return False
        if c.op in (m.CFG_RD, m.CFG_WR):
            return c.addr & 0xF000_0003 == 0
        if c.op == m.MEM_WR:
            return c.addr & 3 == 0
        if c.op in (m.HDR_RD, m.HDR_WR):
            return c.addr & ~0xFFC == 0
        if c.op in (m.HOST_RD, m.HOST_WR):
            return c.addr & ~(HOST_BYTES - 4) == 0
        return True

    def _host_write(self, off: int, data: int, be: int) -> None:
        for b in range(4):
            if be >> b & 1:
                self.host[off + b] = data >> 8 * b & 0xFF

    # ------------------------------------------------------- endpoint
    def _window(self) -> tuple[int, int]:
        w = self.hdr[0x20]
        return (w & 0xFFF0) << 16, ((w >> 16) & 0xFFF0) << 16 | 0xFFFFF

    def _cfg(self, c: Command) -> Result:
        bus, dev, fn = c.addr >> 20 & 0xFF, c.addr >> 15 & 0x1F, c.addr >> 12 & 7
        if bus != (self.hdr[0x18] >> 8 & 0xFF) or dev != 0:
            return Result([(m.LOCAL_UR, 0)], self._local_delay())
        if fn != 0:
            return self._ur_cpl()
        off = c.addr & 0xFFC
        if c.op == m.CFG_WR:
            write_reg(self.ep, EP_REGS, off, c.data, c.fbe)
            return Result([(m.OK, 0)], self._ep_delay())
        return Result([(m.OK, self.ep.get(off, 0))], self._ep_delay())

    def _ur_cpl(self) -> Result:
        r = Result([(m.UR, 0)], self._ep_delay())
        r.on_done.append(lambda t: self.set_err(E_UR_CPL))
        if self.ep[0x78] & 1:
            r.on_done.append(lambda t: self._at(t + self.rng.randint(1, 30),
                                                lambda n: self.set_err(E_COR)))
        return r

    def _bar(self, addr: int, reg: int) -> int | None:
        base = self.ep[reg] & 0xFFFF_F000
        if self.ep[0x04] & 2 and base and base <= addr < base + 0x1000:
            return addr - base
        return None

    def _mem(self, now: int, c: Command) -> Result:
        lo, hi = self._window()
        end = c.addr + 4 * (c.len if c.op == m.MEM_RD else 1) - 1
        if not (self.hdr[0x04] & 2 and lo <= c.addr and end <= hi):
            return Result([(m.LOCAL_UR, 0)], self._local_delay())
        if c.op == m.MEM_RD and self.flag2_reads is not None:
            self.flag2_reads -= 1
            if self.flag2_reads == 0:           # the e2e's link down under traffic
                self.flag2_reads = None
                self._link(False, now)
                self._at(now + self.rng.randint(50, 150),
                         lambda n: self._link(True, n, self.rng.randint(2, 8)))
                return Result([(m.LINK_DOWN, 0)], self._ep_delay())
        off = self._bar(c.addr, 0x10)
        off2 = self._bar(c.addr, 0x18)
        if off is None and off2 is None:
            if c.op == m.MEM_RD:
                return self._ur_cpl()
            if self.ep[0x78] & 2:
                self._at(now + self.rng.randint(1, 30), lambda n: self.set_err(E_NONFATAL))
            return Result([(m.OK, 0)], self._local_delay())
        if off2 is not None:                     # BAR2: reads 0, writes ignored
            n = c.len if c.op == m.MEM_RD else 1
            return Result([(m.OK, 0)] * n, self._ep_delay())
        if c.op == m.MEM_WR:
            self._bar0_write(off, c.data, c.fbe)
            return Result([(m.OK, 0)], self._local_delay())
        if off == m.NO_ANSWER:
            r = Result([(m.TIMEOUT, 0)], CPL_TIMEOUT)
            r.on_done.append(lambda t: self.set_err(E_TIMEOUT))
            return r
        return Result([(m.OK, self.bar0.get(off + 4 * k, 0)) for k in range(c.len)],
                      self._ep_delay())

    def _bar0_write(self, off: int, data: int, be: int) -> None:
        old = self.bar0.get(off, 0)
        mk = be_mask(be)
        self.bar0[off] = (old & ~mk) | (data & mk)
        if off == m.DB_RING:
            self.dma.append((self.bar0.get(m.DB_SRC, 0), self.bar0.get(m.DB_DST, 0),
                             self.bar0.get(m.DB_LEN, 0)))
            if not self.dma_busy:
                self._dma_next()

    def _dma_next(self) -> None:
        if not self.dma:
            self.dma_busy = False
            return
        self.dma_busy = True
        src, dst, nbytes = self.dma.popleft()
        self._at(self.now + self.rng.randint(10, 40), lambda n: self._dma_copy(src, dst, nbytes))

    def _dma_copy(self, src: int, dst: int, nbytes: int) -> None:
        """One doorbell's copy by bus-master reads and writes, then MSI
        vector 0; a read outside host memory ends it (bit 11)."""
        bme = self.hdr[0x04] & 4 and self.ep[0x04] & 4
        if not bme or src + nbytes > HOST_BYTES or dst + nbytes > HOST_BYTES:
            self.set_err(E_INBOUND_UR)
        else:
            self.host[dst:dst + nbytes] = self.host[src:src + nbytes]
            if self.ep[0x50] & 1 << 16:
                addr = self.ep[0x54]
                if m.MSI_ADDR <= addr < m.MSI_ADDR + 0x1000:
                    self._msi((addr - m.MSI_ADDR) >> 2, self.ep[0x5C] & 0xFFFF)
                else:
                    self.set_err(E_INBOUND_UR)
        self._dma_next()

    def _msi(self, offset: int, data: int) -> None:
        if len(self.capture) >= CAPTURE_DEPTH:
            self.set_err(E_MSI_DROP)
        else:
            self.capture.append((offset, data))


# ---------------------------------------------------- reference choices

def standin(seed: int, mode: str, **kw) -> StandIn:
    return StandIn(random.Random(seed), mode, **kw)


def mode_of(prog: int) -> str:
    return "self" if m.PROGRAM_NAMES[prog] == "SELFTEST" else "m4"


def reference_run(prog: int, seed: int, **kw) -> tuple[m.RcSeqModel, StandIn]:
    """The run the testbench will see, on the model alone."""
    si = standin(seed, mode_of(prog), **kw)
    seq, _ = m.run(m.program(prog).rom(), si)
    return seq, si


def expect_corruptions(prog: int, seed: int) -> list[tuple[int, dict]]:
    """Every EXPECT of the passing run that a corrupted read can make the
    first failing step: (EXPECT pc, stand-in keyword arguments). The
    source of R[idx] is the last REQ or WAIT_MSI before it, with only
    EXPECTs between them; one bit of the EXPECT's mask is flipped in the
    source's beat (or MSI capture), and no EXPECT in between checks that
    bit of that register."""
    seq, si = reference_run(prog, seed)
    if not seq.passed:
        raise m.ModelError(f"reference run of {m.PROGRAM_NAMES[prog]} seed {seed} fails")
    out = []
    ncmd = nmsi = 0
    src = None                  # (kind, number, steps since)
    between: list[m.Step] = []
    for pc, op, outcome, _ in seq.events:
        w = m.decode(seq.rom[pc])
        if op >= m.REQ:
            src, between = ("cmd", ncmd, w), []
            ncmd += 1
        elif op == m.WAIT_MSI:
            src, between = ("msi", nmsi, w), []
            nmsi += 1
        elif op == m.EXPECT and src is not None and w.mask:
            kind, num, sw = src
            bit = w.mask & -w.mask
            shadowed = any(b.idx == w.idx and b.mask & bit for b in between)
            fits = (kind == "msi" and w.idx in (0, 1)) or (
                kind == "cmd" and w.idx < max(1, sw.len if sw.op - m.REQ == m.MEM_RD else 1))
            if fits and not shadowed:
                if kind == "cmd":
                    out.append((pc, {"corrupt": {num: (w.idx, bit)}}))
                elif w.idx == 0:
                    out.append((pc, {"msi_corrupt": {num: bit}}))
            between.append(w)
        else:
            src, between = None, []
    return out
