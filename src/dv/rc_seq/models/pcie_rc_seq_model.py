"""Reference model of pcie_rc_seq (wiki/src/rtl/rc_seq/MAS.md, PROGRAMS.md).

Written from those pages, not from the RTL:

- the 128-bit step format, its constructors and an assembler whose jump
  targets are labels resolved at the end (never hand-counted);
- the programs ENUM, MMIO, DMA_MSI, ERRORS and SELFTEST, built from the
  programs page step by step; the testbench runs the RTL's package images
  against the same stand-in as these, so any encoding difference shows as
  a cycle mismatch;
- ``RcSeqModel``, the interpreter one clk_i cycle at a time, with the
  cycle timing of the MAS (Timing): outputs of the current cycle from
  ``outputs`` and the clock edge from ``clock``.

Provenance: the step encoder and the program builders are adapted from the
rc architecture model, src/arch/rc/pcie_rc_arch.py (PR #392, commit
2bfb7fc, this repository, Apache-2.0); the cycle model is new.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------- format

ROM_AW = 8
ROM_STEPS = 1 << ROM_AW

# Step ops (MAS, Commands); REQ is 0x10 + the tl-seq op.
FAIL, EXPECT, WAIT_MSI, WAIT_LINK, HOT_RESET, JUMP, DONE = range(7)
REQ = 0x10
OP_NAMES = {FAIL: "FAIL", EXPECT: "EXPECT", WAIT_MSI: "WAIT_MSI", WAIT_LINK: "WAIT_LINK",
            HOT_RESET: "HOT_RESET", JUMP: "JUMP", DONE: "DONE"}
ALWAYS, DATA_EQ, DATA_NE, STATUS_EQ, STATUS_NE = range(5)
COND_NAMES = ("ALWAYS", "DATA_EQ", "DATA_NE", "STATUS_EQ", "STATUS_NE", "BAD5", "BAD6", "BAD7")

# tl-seq ops and statuses (TL_SEQ, Commands and Results).
CFG_RD, CFG_WR, MEM_RD, MEM_WR, HDR_RD, HDR_WR, HOST_RD, HOST_WR, ERR_RD, ERR_CLR = range(10)
TL_OP_NAMES = ("CFG_RD", "CFG_WR", "MEM_RD", "MEM_WR", "HDR_RD", "HDR_WR", "HOST_RD", "HOST_WR",
               "ERR_RD", "ERR_CLR")
OK, UR, CA, CRS, TIMEOUT, LOCAL_UR, LINK_DOWN, BAD = range(8)
STATUS_NAMES = ("OK", "UR", "CA", "CRS", "TIMEOUT", "LOCAL_UR", "LINK_DOWN", "BAD")

FULL = 0xFFFF_FFFF

# (name, lsb, width) of a step (MAS, Step format); bit 127 is reserved.
FIELDS = (("op", 122, 5), ("status", 119, 3), ("chk", 118, 1), ("cond", 115, 3),
          ("idx", 110, 5), ("len", 104, 6), ("fbe", 100, 4), ("lbe", 96, 4),
          ("addr", 64, 32), ("data", 32, 32), ("mask", 0, 32))


def encode(**f) -> int:
    """One ROM word from its fields; unused fields 0."""
    w = 0
    for name, lsb, width in FIELDS:
        v = f.pop(name, 0)
        if not 0 <= v < 1 << width:
            raise ValueError(f"step field {name}={v!r} wider than {width} bits")
        w |= v << lsb
    if f:
        raise ValueError(f"unknown step fields {sorted(f)}")
    return w


@dataclass(frozen=True)
class Step:
    op: int
    status: int
    chk: int
    cond: int
    idx: int
    len: int
    fbe: int
    lbe: int
    addr: int
    data: int
    mask: int


def decode(w: int) -> Step:
    return Step(**{name: w >> lsb & ((1 << width) - 1) for name, lsb, width in FIELDS})


def op_name(op: int) -> str:
    if op >= REQ:
        t = op - REQ
        return "REQ_" + (TL_OP_NAMES[t] if t < 10 else f"OP{t}")
    return OP_NAMES.get(op, f"RSV{op:02X}")


# ----------------------------------------------------------- constructors

def cfg_addr(bus: int, dev: int, fn: int, reg: int) -> int:
    """CFG_* address: bus [27:20], device [19:15], function [14:12], DW
    index [11:2] from the byte offset ``reg``."""
    return bus << 20 | dev << 15 | fn << 12 | (reg & 0xFFC)


def req(op: int, addr: int, n: int = 1, fbe: int = 0xF, lbe: int | None = None, data: int = 0,
        chk: bool = True, status: int = OK) -> int:
    """A REQ step; PROGRAMS defaults: len 1, first_be 0xF, last_be 0xF above
    one DW and 0 otherwise, checking OK."""
    if lbe is None:
        lbe = 0xF if n > 1 else 0
    return encode(op=REQ + op, addr=addr, len=n, fbe=fbe, lbe=lbe, data=data, chk=int(chk),
                  status=status)


def expect(value: int, mask: int = FULL, idx: int = 0) -> int:
    return encode(op=EXPECT, data=value & mask, mask=mask, idx=idx)


def wait_msi(timeout: int) -> int:
    return encode(op=WAIT_MSI, data=timeout)


def wait_link(timeout: int) -> int:
    return encode(op=WAIT_LINK, data=timeout)


def hot_reset(timeout: int, hold: int) -> int:
    return encode(op=HOT_RESET, data=timeout, addr=hold)


def jump(cond: int, target: int, limit: int = 0, value: int = 0, mask: int = FULL, idx: int = 0,
         status: int = OK) -> int:
    """A JUMP; only the data conditions carry value, mask and idx, only the
    status ones a status."""
    if cond not in (DATA_EQ, DATA_NE):
        value = mask = idx = 0
    if cond not in (STATUS_EQ, STATUS_NE):
        status = 0
    return encode(op=JUMP, cond=cond, addr=limit << 16 | target, data=value & mask, mask=mask,
                  idx=idx, status=status)


def done() -> int:
    return encode(op=DONE)


def fail() -> int:
    return 0


class Asm:
    """A program: steps in order, labels, and jumps to labels resolved at
    the end."""

    def __init__(self):
        self.steps: list = []
        self.labels: dict[str, int] = {}

    def __call__(self, *words) -> None:
        self.steps.extend(words)

    @property
    def here(self) -> int:
        return len(self.steps)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"label {name} twice")
        self.labels[name] = self.here

    def jump(self, cond: int, to: str, **kw) -> None:
        self.steps.append(lambda labels: jump(cond, labels[to], **kw))

    def rom(self) -> list[int]:
        words = [s(self.labels) if callable(s) else s for s in self.steps]
        if len(words) > ROM_STEPS:
            raise ValueError(f"program of {len(words)} steps, ROM holds {ROM_STEPS}")
        return words


# -------------------------------------------------------------- constants

# Wait timeouts, Hot Reset hold, loop limit (MAS, Program constants).
LINK_TIMEOUT = 25_000_000
MSI_TIMEOUT = 1_250_000
HR_TIMEOUT = 125_000
HR_HOLD = 64
POLL_LIMIT = 4096
MSI_DEPTH = 4

# rc and core at their defaults; the address plan (PROGRAMS).
RC_IDS, RC_REVISION, EP_IDS = 0x0000_0000, 0x00, 0x0000_0000
BUS = 1
BUSES = 0x0001_0100
WINDOW = 0x1000_1000
BAR0, BAR2 = 0x1000_0000, 0x1000_1000
BAR_4K_SIZE = 0xFFFF_F000
NOT_DECODED, OUTSIDE = 0x1000_8000, 0x2000_0000
MSI_ADDR, MSI_DATA = 0xFEE0_0000, 0x0001
DMA_SRC, DMA_DST, RUN_FLAG = 0x100, 0x200, 0xFFC
DB_SRC, DB_DST, DB_LEN, DB_RING = 0xF00, 0xF04, 0xF08, 0xF0C
NO_ANSWER = 0xF10
ERR_MASK = 0x1FFF

# SELFTEST (PROGRAMS, Self-test).
SELF_WAIT, SELF_HOLD, SELF_LIMIT = 8, 3, 3
SELF_SCRATCH = 0x040
SELF_POLL_A, SELF_POLL_B, SELF_POLL_C = 0x0D0, 0x0D4, 0x0D8
SELF_GUARD = 0x0A0
SELF_KICK_LINK, SELF_KICK_MSI = 0x0F0, 0x0F4
SELF_MSI_KICK = 0x0155_BEEF
SELF_FAR = 0x0123


def mmio_pattern(k: int) -> int:
    return 0xA500_0000 | k * 0x0001_0203


def dma_pattern(k: int) -> int:
    return 0xD000_0000 | k * 0x0101_0101


def st_pattern(k: int) -> int:
    return 0x5E00_0000 | k * 0x0002_0401


def ep(reg: int, bus: int = BUS, dev: int = 0, fn: int = 0) -> int:
    return cfg_addr(bus, dev, fn, reg)


# --------------------------------------------------------------- programs

def enum_section(a: Asm) -> None:
    """E1 to E10 (PROGRAMS, Enumeration)."""
    a(req(HDR_WR, 0x18, data=BUSES, fbe=0x7), req(HDR_WR, 0x20, data=WINDOW),
      req(HDR_WR, 0x04, data=0x6, fbe=0x1))
    for reg, want in ((0x00, RC_IDS), (0x04, 0x0000_0006), (0x08, 0x0604_0000 | RC_REVISION),
                      (0x18, BUSES), (0x20, WINDOW)):
        a(req(HDR_RD, reg), expect(want))
    a(wait_link(LINK_TIMEOUT))
    a(req(CFG_RD, ep(0x00)), expect(EP_IDS),
      req(CFG_RD, ep(0x0C)), expect(0, 0x007F_0000),
      req(CFG_RD, ep(0x04)), expect(1 << 20, 1 << 20))
    a(req(CFG_RD, ep(0x34)), expect(0x40, 0xFF))
    for reg, want in ((0x40, 0x5001), (0x50, 0x6005), (0x60, 0x7011), (0x70, 0x0010)):
        a(req(CFG_RD, ep(reg)), expect(want, 0xFFFF))
    for i in range(6):
        a(req(CFG_WR, ep(0x10 + 4 * i), data=FULL), req(CFG_RD, ep(0x10 + 4 * i)),
          expect(BAR_4K_SIZE if i in (0, 2) else 0))
    for reg, bar in ((0x10, BAR0), (0x18, BAR2)):
        a(req(CFG_WR, ep(reg), data=bar), req(CFG_RD, ep(reg)), expect(bar))
    a(req(CFG_WR, ep(0x54), data=MSI_ADDR), req(CFG_WR, ep(0x58), data=0),
      req(CFG_WR, ep(0x5C), data=MSI_DATA), req(CFG_WR, ep(0x50), data=1 << 16, fbe=0x4),
      req(CFG_RD, ep(0x50)), expect(1 << 16, 1 << 16))
    a(req(CFG_WR, ep(0x04), data=0x06, fbe=0x1), req(CFG_RD, ep(0x04)), expect(0x6, 0x6))


def mmio_section(a: Asm) -> None:
    """M1 to M5 (PROGRAMS, MMIO)."""
    a(*(req(MEM_WR, BAR0 + 4 * k, data=mmio_pattern(k)) for k in range(32)))
    a(req(MEM_RD, BAR0, 32), *(expect(mmio_pattern(k), idx=k) for k in range(32)))
    a(req(MEM_RD, BAR0 + 0x38, 4), *(expect(mmio_pattern(14 + k), idx=k) for k in range(4)))
    a(req(MEM_WR, BAR0 + 0x80, data=0x1234_5678), req(MEM_WR, BAR0 + 0x80, data=FULL, fbe=0x3),
      req(MEM_RD, BAR0 + 0x80), expect(0x1234_FFFF))
    a(req(ERR_RD, 0), expect(0, ERR_MASK))


def doorbell(a: Asm, src: int, dst: int, nbytes: int, rings: int = 1) -> None:
    a(req(MEM_WR, BAR0 + DB_SRC, data=src), req(MEM_WR, BAR0 + DB_DST, data=dst),
      req(MEM_WR, BAR0 + DB_LEN, data=nbytes))
    a(*(req(MEM_WR, BAR0 + DB_RING, data=1) for _ in range(rings)))


def dma_section(a: Asm) -> None:
    """D1 to D5 (PROGRAMS, DMA and MSI), 64 bytes."""
    a(*(req(HOST_WR, DMA_SRC + 4 * k, data=dma_pattern(k)) for k in range(16)),
      *(req(HOST_WR, DMA_DST + 4 * k, data=0) for k in range(16)), req(ERR_CLR, 0, data=ERR_MASK))
    doorbell(a, DMA_SRC, DMA_DST, 64)
    a(wait_msi(MSI_TIMEOUT), expect(MSI_DATA), expect(0, idx=1))
    for k in range(16):
        a(req(HOST_RD, DMA_DST + 4 * k), expect(dma_pattern(k)))
    a(req(ERR_RD, 0), expect(0, ERR_MASK))


def poll_err(a: Asm, name: str, bits: int) -> None:
    a.label(name)
    a(req(ERR_RD, 0))
    a.jump(DATA_NE, name, limit=POLL_LIMIT, value=bits, mask=bits)


def errors_program(a: Asm) -> None:
    """R1 to R14 (PROGRAMS, Errors and recovery)."""
    a(req(HOST_WR, RUN_FLAG, data=0))
    a.label("ENUM")
    enum_section(a)
    a(req(HOST_RD, RUN_FLAG))
    a.jump(DATA_EQ, "R11", value=1)
    a.jump(DATA_EQ, "R14", value=2)
    a(req(CFG_WR, ep(0x78), data=0x1F, fbe=0x1), req(ERR_CLR, 0, data=ERR_MASK))
    a(req(MEM_WR, NOT_DECODED, data=0x5A5A_5A5A), req(MEM_RD, NOT_DECODED, status=UR))
    poll_err(a, "R5", 0x181)
    a(expect(0x181, ERR_MASK), req(ERR_CLR, 0, data=ERR_MASK))
    a(req(MEM_RD, OUTSIDE, status=LOCAL_UR), req(CFG_RD, ep(0, dev=1), status=LOCAL_UR),
      req(CFG_RD, ep(0, bus=2), status=LOCAL_UR))
    doorbell(a, OUTSIDE, DMA_DST, 4)
    poll_err(a, "R7", 0x800)
    doorbell(a, DMA_SRC, DMA_DST, 4, rings=MSI_DEPTH + 1)
    poll_err(a, "R8", 0x1000)
    a(*(wait_msi(MSI_TIMEOUT) for _ in range(MSI_DEPTH)))
    a(req(MEM_RD, BAR0 + NO_ANSWER, status=TIMEOUT), req(ERR_RD, 0), expect(0x1808, ERR_MASK),
      req(ERR_CLR, 0, data=ERR_MASK), req(ERR_RD, 0), expect(0, ERR_MASK))
    a(req(HOST_WR, RUN_FLAG, data=1), hot_reset(HR_TIMEOUT, HR_HOLD), wait_link(LINK_TIMEOUT))
    a.jump(ALWAYS, "ENUM")
    a.label("R11")
    a(req(MEM_WR, BAR0 + 0x40, data=0x0BAD_F00D), req(MEM_RD, BAR0 + 0x40), expect(0x0BAD_F00D),
      req(HOST_WR, RUN_FLAG, data=2))
    a.label("R12")
    a(req(MEM_RD, BAR0, chk=False))
    a.jump(STATUS_EQ, "R12", limit=POLL_LIMIT, status=OK)
    a.jump(STATUS_EQ, "R13", status=LINK_DOWN)
    a(fail())
    a.label("R13")
    a(wait_link(LINK_TIMEOUT))
    a.jump(ALWAYS, "ENUM")
    a.label("R14")
    a(req(MEM_WR, BAR0 + 0x44, data=0x600D_CAFE), req(MEM_RD, BAR0 + 0x44), expect(0x600D_CAFE),
      done())


# Guarded traps of SELFTEST, in order: (name, trap word or label target).
TRAPS = ("FAIL", "RSV07", "RSV0F", "COND5", "COND7", "PAD", "FAR")


def selftest_program(a: Asm) -> None:
    """SELFTEST (PROGRAMS, Self-test); labels name the steps tests use."""
    a.label("S1")
    a(req(HOST_WR, SELF_KICK_LINK, data=0))
    a.label("wait_link")
    a(wait_link(SELF_WAIT))
    a(req(HDR_WR, 0x18, data=BUSES, fbe=0x7), req(HDR_WR, 0x20, data=WINDOW),
      req(HDR_WR, 0x04, data=0x6, fbe=0x1), req(HDR_RD, 0x18), expect(BUSES))
    a(req(CFG_WR, ep(0x10), data=FULL), req(CFG_RD, ep(0x10)), expect(BAR_4K_SIZE, 0xFFFF_FFF0),
      req(CFG_WR, ep(0x10), data=BAR0), req(CFG_WR, ep(0x04), data=0x6, fbe=0x1))
    a(*(req(MEM_WR, BAR0 + 4 * k, data=st_pattern(k)) for k in range(32)))
    a.label("mem_rd32")
    a(req(MEM_RD, BAR0, 32), *(expect(st_pattern(k), idx=k) for k in range(32)))
    a(req(HOST_WR, SELF_SCRATCH, data=0xCAFE_F00D), req(HOST_RD, SELF_SCRATCH), expect(0xCAFE_F00D),
      req(ERR_CLR, 0, data=ERR_MASK), req(ERR_RD, 0), expect(0, ERR_MASK))
    a(req(0xA, 0, status=LOCAL_UR), req(0xF, 0, status=LOCAL_UR),
      req(MEM_RD, OUTSIDE, status=LOCAL_UR))

    def skip(cond: int, **kw) -> None:          # a taken jump over a FAIL
        name = f"skip{a.here}"
        a.jump(cond, name, **kw)
        a(fail())
        a.label(name)

    skip(STATUS_EQ, status=LOCAL_UR)
    a.jump(STATUS_EQ, "PAD", status=OK)
    skip(STATUS_NE, status=OK)
    a.jump(STATUS_NE, "PAD", status=LOCAL_UR)
    a(req(HOST_RD, SELF_SCRATCH))
    skip(DATA_EQ, value=0xCAFE_F00D)
    a.jump(DATA_EQ, "PAD", value=0xCAFE_F00E)
    skip(DATA_NE, value=0)
    a.jump(DATA_NE, "PAD", value=0xCAFE_0000, mask=0xFFFF_0000)
    skip(DATA_EQ, value=st_pattern(5), idx=5)
    skip(ALWAYS)
    for i, name in enumerate(TRAPS):
        a(req(HOST_RD, SELF_GUARD + 4 * i))
        a.jump(DATA_EQ, f"after_{name}", value=0)
        a.label(f"trap_{name}")
        if name == "FAIL":
            a(fail())
        elif name.startswith("RSV"):
            a(encode(op=int(name[3:], 16)))
        elif name.startswith("COND"):
            a(encode(op=JUMP, cond=int(name[4:])))
        elif name == "PAD":
            a.jump(ALWAYS, "PAD")
        else:
            a(jump(ALWAYS, SELF_FAR))
        a.label(f"after_{name}")
    for loop, poll in (("A", SELF_POLL_A), ("B", SELF_POLL_B), ("C", SELF_POLL_C)):
        a.label(f"poll_{loop}")
        a(req(HOST_RD, poll))
        if loop == "B":
            a.jump(ALWAYS, f"poll_{loop}_j")
        elif loop == "C":
            a.jump(DATA_EQ, f"poll_{loop}_j", value=4, mask=4)
        a.label(f"poll_{loop}_j")
        a.jump(DATA_NE, f"poll_{loop}", limit=SELF_LIMIT, value=1, mask=1)
    a(req(HOST_WR, SELF_KICK_MSI, data=SELF_MSI_KICK))
    a.label("wait_msi")
    a(wait_msi(SELF_WAIT), expect(SELF_MSI_KICK), expect((SELF_MSI_KICK >> 16) & 0x3FF, idx=1))
    a.label("hot_reset")
    a(hot_reset(SELF_WAIT, SELF_HOLD), hot_reset(SELF_WAIT, 0))
    a(req(MEM_RD, BAR0, status=LINK_DOWN), req(CFG_RD, ep(0), chk=False))
    skip(STATUS_EQ, status=LINK_DOWN)
    a(req(HDR_RD, 0x20), expect(WINDOW))
    a.label("done")
    a(done())
    a.label("PAD")


PROGRAM_NAMES = ("ENUM", "MMIO", "DMA_MSI", "ERRORS", "SELFTEST")


def program(prog: int | str) -> Asm:
    """The assembled program with id or name ``prog`` (MAS, Programs)."""
    name = PROGRAM_NAMES[prog] if isinstance(prog, int) else prog
    a = Asm()
    if name == "ERRORS":
        errors_program(a)
    elif name == "SELFTEST":
        selftest_program(a)
    else:
        enum_section(a)
        if name == "MMIO":
            mmio_section(a)
        elif name == "DMA_MSI":
            dma_section(a)
        elif name != "ENUM":
            raise ValueError(f"program {prog!r}")
        a(done())
    return a


# ----------------------------------------------------------- interpreter

class ModelError(Exception):
    """The stimulus broke the tl-seq contract, or a program read a result
    register it never wrote."""


@dataclass
class Inputs:
    """rc_seq inputs of one cycle."""
    rst: int = 0
    start: int = 0
    dl_up: int = 0
    link_up: int = 0
    cmd_ready: int = 0
    rsp_valid: int = 0
    rsp_status: int = 0
    rsp_data: int = 0
    rsp_last: int = 0
    msi_valid: int = 0
    msi_offset: int = 0
    msi_data: int = 0


@dataclass
class Outputs:
    """rc_seq outputs of one cycle; ``cmd`` is (op, addr, len, first_be,
    last_be, data) and is compared only while ``cmd_valid``."""
    cmd_valid: int
    cmd: tuple
    msi_ready: int
    hot_reset_req: int
    done: int
    passed: int
    fail_step: int


class RcSeqModel:
    """pcie_rc_seq one clk_i cycle at a time (MAS, Behavior and Timing).

    States as the MAS names them. ``outputs(i)`` gives the outputs of the
    current cycle for inputs ``i`` (only ``msi_ready`` depends on them);
    ``clock(i)`` takes the clock edge that ends the cycle.
    ``events`` records each finished step as (pc, op, outcome, detail)."""

    def __init__(self, rom: list[int]):
        if len(rom) > ROM_STEPS:
            raise ValueError("program longer than the ROM")
        self.rom = list(rom) + [0] * (ROM_STEPS - len(rom))
        self.R: list[int | None] = [None] * 32
        self.events: list[tuple] = []
        self.reset()

    def reset(self) -> None:
        self.state = "IDLE"
        self.pc, self.S, self.L, self.cnt, self.beat = 0, OK, 0, 0, 0
        self.done = self.passed = 0
        self.fail_step = 0
        self.start_prev = 0
        self.msi = (0, 0)

    def word(self) -> Step:
        return decode(self.rom[self.pc] if self.pc < ROM_STEPS else 0)

    @property
    def running(self) -> bool:
        return self.state != "IDLE"

    def outputs(self, i: Inputs) -> Outputs:
        w = self.word()
        return Outputs(
            cmd_valid=int(self.state == "REQ_OFFER"),
            cmd=((w.op & 0xF), w.addr, w.len, w.fbe, w.lbe, w.data),
            msi_ready=int(self.state == "WAIT" and w.op == WAIT_MSI and bool(i.msi_valid)),
            hot_reset_req=int(self.state in ("HR_FALL", "HR_HOLD")),
            done=self.done, passed=self.passed, fail_step=self.fail_step)

    # ---- step endings
    def _end(self, ok: bool, detail: str = "") -> None:
        w = self.word()
        self.events.append((self.pc, w.op, "pass" if ok else "fail", detail))
        self.state = "IDLE"
        self.done, self.passed = 1, int(ok)
        self.fail_step = 0 if ok else self.pc

    def _next(self, detail: str = "") -> None:
        self.events.append((self.pc, self.word().op, "ok", detail))
        self.pc = (self.pc + 1) & 0xFFFF
        self.state = "FETCH"

    def _jump(self, taken: bool) -> None:
        w = self.word()
        target, limit = w.addr & 0xFFFF, w.addr >> 16
        if w.cond == ALWAYS:
            self.events.append((self.pc, JUMP, "ok", "ALWAYS taken"))
            self.pc, self.state = target, "FETCH"
        elif taken and target <= self.pc:
            if self.L == limit:
                self._end(False, f"{COND_NAMES[w.cond]} limit")
            else:
                self.events.append((self.pc, JUMP, "ok", f"{COND_NAMES[w.cond]} taken back"))
                self.L += 1
                self.pc, self.state = target, "FETCH"
        else:
            self.events.append((self.pc, JUMP, "ok",
                                f"{COND_NAMES[w.cond]} {'taken' if taken else 'not taken'}"))
            self.L = 0
            self.pc = target if taken else (self.pc + 1) & 0xFFFF
            self.state = "FETCH"

    def _reg(self, k: int) -> int:
        v = self.R[k]
        if v is None:
            raise ModelError(f"step {self.pc} reads R[{k}], never written")
        return v

    def clock(self, i: Inputs) -> None:
        if i.rst:
            self.reset()
            return
        w = self.word()
        st = self.state
        beat_ok = st == "REQ_WAIT" or (st == "REQ_OFFER" and i.cmd_ready)
        if i.rsp_valid and not beat_ok:
            raise ModelError(f"result beat with no command outstanding (state {st})")
        if st == "IDLE":
            if i.start and not self.start_prev:
                self.state, self.pc, self.S, self.L = "FETCH", 0, OK, 0
                self.done = self.passed = 0
                self.fail_step = 0
        elif st == "FETCH":
            self.state = "EXEC"
        elif st == "EXEC":
            self.cnt = self.beat = 0
            if w.op >= REQ:
                self.state = "REQ_OFFER"
            elif w.op == EXPECT:
                self.state = "READ"
            elif w.op in (WAIT_MSI, WAIT_LINK):
                self.state = "WAIT"
            elif w.op == HOT_RESET:
                self.state = "HR_FALL"
            elif w.op == DONE:
                self._end(True)
            elif w.op == JUMP:
                if w.cond > STATUS_NE:
                    self._end(False, "bad condition")
                elif w.cond in (DATA_EQ, DATA_NE):
                    self.state = "READ"
                else:
                    self._jump(w.cond == ALWAYS or ((self.S == w.status) == (w.cond == STATUS_EQ)))
            else:
                self._end(False, "FAIL" if w.op == FAIL else "reserved op")
        elif st == "READ":
            eq = ((self._reg(w.idx) ^ w.data) & w.mask) == 0
            if w.op == EXPECT:
                if eq:
                    self._next()
                else:
                    self._end(False, "mismatch")
            else:
                self._jump(eq == (w.cond == DATA_EQ))
        elif st == "WAIT":
            if (i.msi_valid if w.op == WAIT_MSI else i.dl_up):
                if w.op == WAIT_MSI:
                    self.msi = (i.msi_offset, i.msi_data)
                    self.state = "MSI_R0"
                else:
                    self._next(f"after {self.cnt}")
            elif self.cnt == w.data:
                self._end(False, "timeout")
            else:
                self.cnt += 1
        elif st == "MSI_R0":
            self.R[0] = self.msi[1]
            self.state = "MSI_R1"
        elif st == "MSI_R1":
            self.R[1] = self.msi[0]
            self._next()
        elif st == "HR_FALL":
            hold = w.addr & 0xFFFF
            if not i.link_up:
                if hold == 0:
                    self._next()
                else:
                    self.state, self.cnt = "HR_HOLD", 1
            elif self.cnt == w.data:
                self._end(False, "timeout")
            else:
                self.cnt += 1
        elif st == "HR_HOLD":
            if self.cnt == w.addr & 0xFFFF:
                self._next()
            else:
                self.cnt += 1
        elif st in ("REQ_OFFER", "REQ_WAIT"):
            if st == "REQ_OFFER" and i.cmd_ready:
                self.state = "REQ_WAIT"
            if i.rsp_valid:
                if self.beat < 32:
                    self.R[self.beat] = i.rsp_data
                self.beat += 1
                if i.rsp_last:
                    self.S = i.rsp_status
                    if w.chk and i.rsp_status != w.status:
                        self._end(False, f"status {STATUS_NAMES[i.rsp_status]}")
                    else:
                        self._next(STATUS_NAMES[i.rsp_status])
        self.start_prev = i.start


def run(rom: list[int], standin, *, max_cycles: int = 200_000, reset_cycles: int = 8,
        start_hold: bool = True) -> tuple[RcSeqModel, int]:
    """Run ``rom`` on the model against ``standin`` (a ``StandIn``) with no
    simulator: reset, ``start_i`` held 1 through its release, until
    ``done_o``. Returns the model and the cycle count."""
    seq = RcSeqModel(rom)
    for n in range(max_cycles):
        rst = int(n < reset_cycles)
        o = seq.outputs(Inputs())
        i = standin.drive(n, rst, o.cmd_valid, o.cmd, o.hot_reset_req)
        i.rst, i.start = rst, int(start_hold)
        o = seq.outputs(i)
        standin.commit(n, i, o.msi_ready)
        seq.clock(i)
        if seq.done and not seq.running:
            return seq, n + 1
    raise ModelError(f"no done_o in {max_cycles} cycles (pc {seq.pc}, state {seq.state})")
