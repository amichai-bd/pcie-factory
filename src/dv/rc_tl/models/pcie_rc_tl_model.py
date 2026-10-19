"""Reference model of the pcie_rc_tl front end and Type 1 header.

Written from the specs, not the RTL: the command checks and result beats of
wiki/src/rtl/rc_tl/MAS.md#commands, the byte-enable rule of
wiki/src/rtl/rc_tl/REQUESTER.md#byte-enables, the header table of
wiki/src/arch/rc/HAS.md#type-1-header and the header access of
wiki/src/arch/rc/TL_SEQ.md#header-access. It covers what #374 builds:
checks 2 to 7, HDR_* and the interim rule (MAS, Leaf blocks) for every
other command that passes the checks.

`FrontEnd.step` is the cycle model the testbench compares with the DUT at
every clock edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Operation codes and result statuses (TL_SEQ.md, Commands and Results).
CFG_RD, CFG_WR, MEM_RD, MEM_WR, HDR_RD, HDR_WR, HOST_RD, HOST_WR, ERR_RD, ERR_CLR = range(10)
OP_NAMES = ("CFG_RD", "CFG_WR", "MEM_RD", "MEM_WR", "HDR_RD", "HDR_WR",
            "HOST_RD", "HOST_WR", "ERR_RD", "ERR_CLR")
OK, UR, CA, CRS, TIMEOUT, LOCAL_UR, LINK_DOWN, BAD = range(8)
ST_NAMES = ("OK", "UR", "CA", "CRS", "TIMEOUT", "LOCAL_UR", "LINK_DOWN", "BAD")

ENDPOINT_OPS = (CFG_RD, CFG_WR, MEM_RD, MEM_WR)

# Header constants (rc HAS, Type 1 header).
CLASS_CODE = 0x060400
HEADER_TYPE = 0x01


def op_name(op: int) -> str:
    return OP_NAMES[op] if op < len(OP_NAMES) else f"op{op}"


@dataclass(frozen=True)
class Params:
    """The rc_tl parameters the front end and header use."""
    vendor_id: int = 0
    device_id: int = 0
    revision_id: int = 0
    host_mem_addr_w: int = 12


@dataclass(frozen=True)
class Cmd:
    """A t_rc_cmd (common_pkg): op, addr, len, first_be, last_be, data."""
    op: int
    addr: int = 0
    len: int = 1
    first_be: int = 0xF
    last_be: int = 0
    data: int = 0

    def pack(self) -> int:
        """The 82-bit packed struct, op in the top bits."""
        v = self.op & 0xF
        v = (v << 32) | (self.addr & 0xFFFF_FFFF)
        v = (v << 6) | (self.len & 0x3F)
        v = (v << 4) | (self.first_be & 0xF)
        v = (v << 4) | (self.last_be & 0xF)
        return (v << 32) | (self.data & 0xFFFF_FFFF)

    @staticmethod
    def unpack(v: int) -> "Cmd":
        data = v & 0xFFFF_FFFF
        v >>= 32
        last_be = v & 0xF
        v >>= 4
        first_be = v & 0xF
        v >>= 4
        n = v & 0x3F
        v >>= 6
        addr = v & 0xFFFF_FFFF
        v >>= 32
        return Cmd(op=v & 0xF, addr=addr, len=n, first_be=first_be, last_be=last_be, data=data)

    def __str__(self) -> str:
        return (f"{op_name(self.op)} addr=0x{self.addr:08x} len={self.len} "
                f"fbe={self.first_be:04b} lbe={self.last_be:04b} data=0x{self.data:08x}")


@dataclass(frozen=True)
class Rsp:
    """A t_rc_rsp beat (common_pkg): status, data, last."""
    status: int
    data: int = 0
    last: int = 1

    def pack(self) -> int:
        return (self.status << 33) | ((self.data & 0xFFFF_FFFF) << 1) | (self.last & 1)

    @staticmethod
    def unpack(v: int) -> "Rsp":
        return Rsp(status=(v >> 33) & 0x7, data=(v >> 1) & 0xFFFF_FFFF, last=v & 1)

    def __str__(self) -> str:
        return f"{ST_NAMES[self.status]} data=0x{self.data:08x} last={self.last}"


@dataclass
class Header:
    """The root port's Type 1 header: the RW fields; the rest is constant."""
    p: Params
    mse: int = 0
    bme: int = 0
    cache_line: int = 0
    pri_bus: int = 0
    sec_bus: int = 0
    sub_bus: int = 0
    mem_base: int = 0    # Memory Base [15:4]
    mem_limit: int = 0   # Memory Limit [15:4]

    def reset(self) -> None:
        self.mse = self.bme = self.cache_line = 0
        self.pri_bus = self.sec_bus = self.sub_bus = 0
        self.mem_base = self.mem_limit = 0

    def read(self, idx: int) -> int:
        """The DW at index idx, every field included; 16 to 1023 read 0."""
        if idx == 0:
            return (self.p.device_id << 16) | self.p.vendor_id
        if idx == 1:
            return (self.bme << 2) | (self.mse << 1)          # Status 0
        if idx == 2:
            return (CLASS_CODE << 8) | self.p.revision_id
        if idx == 3:
            return (HEADER_TYPE << 16) | self.cache_line
        if idx == 6:
            return (self.sub_bus << 16) | (self.sec_bus << 8) | self.pri_bus
        if idx == 8:
            return (self.mem_limit << 20) | (self.mem_base << 4)
        return 0

    def write(self, idx: int, be: int, data: int) -> None:
        """The enabled bytes of data, field by field: RW fields take them."""
        byte = [(data >> (8 * i)) & 0xFF if (be >> i) & 1 else None for i in range(4)]
        if idx == 1 and byte[0] is not None:
            self.mse = (byte[0] >> 1) & 1
            self.bme = (byte[0] >> 2) & 1
        elif idx == 3 and byte[0] is not None:
            self.cache_line = byte[0]
        elif idx == 6:
            if byte[0] is not None:
                self.pri_bus = byte[0]
            if byte[1] is not None:
                self.sec_bus = byte[1]
            if byte[2] is not None:
                self.sub_bus = byte[2]
        elif idx == 8:
            # Memory Base [15:4] is DW bits 15:4; Memory Limit [15:4] is 31:20.
            if byte[0] is not None:
                self.mem_base = (self.mem_base & 0xFF0) | (byte[0] >> 4)
            if byte[1] is not None:
                self.mem_base = (self.mem_base & 0x00F) | (byte[1] << 4)
            if byte[2] is not None:
                self.mem_limit = (self.mem_limit & 0xFF0) | (byte[2] >> 4)
            if byte[3] is not None:
                self.mem_limit = (self.mem_limit & 0x00F) | (byte[3] << 4)

    def window(self) -> tuple[int, int]:
        """The memory window's first and last byte (empty when first > last)."""
        return self.mem_base << 20, (self.mem_limit << 20) | 0xFFFFF


# --------------------------------------------------------------- checks

CONTIG_FIRST = (0b1111, 0b1110, 0b1100, 0b1000)
CONTIG_LAST = (0b0001, 0b0011, 0b0111, 0b1111)


def be_legal(c: Cmd) -> bool:
    """REQUESTER.md, Byte enables, for a command with a legal op and len."""
    if c.len == 1 and c.last_be != 0:
        return False
    if c.op in (CFG_RD, CFG_WR, MEM_WR):
        return c.first_be != 0
    if c.op == MEM_RD and c.len >= 2:
        if c.first_be == 0 or c.last_be == 0:
            return False
        if c.len == 2 and (c.addr >> 2) & 1 == 0:
            return True
        return c.first_be in CONTIG_FIRST and c.last_be in CONTIG_LAST
    return True    # MEM_RD len 1, HDR_*, HOST_*, ERR_*


def failed_check(c: Cmd, hdr: Header, p: Params) -> int:
    """The first of checks 2 to 7 (MAS, Commands) that c fails, or 0."""
    if c.op > ERR_CLR:
        return 2
    if not (1 <= c.len <= 32 if c.op == MEM_RD else c.len == 1):
        return 3
    if c.op in (CFG_RD, CFG_WR):
        zero = (0xF << 28) | 0x3
    elif c.op in (MEM_RD, MEM_WR):
        zero = 0x3
    elif c.op in (HDR_RD, HDR_WR):
        zero = 0xFFFFF000 | 0x3
    elif c.op in (HOST_RD, HOST_WR):
        zero = (0xFFFF_FFFF & ~((1 << p.host_mem_addr_w) - 1)) | 0x3
    else:
        zero = 0
    if c.addr & zero:
        return 4
    if c.op == MEM_RD and (c.addr & 0xFFF) + 4 * c.len > 0x1000:
        return 5
    if not be_legal(c):
        return 6
    if c.op in (CFG_RD, CFG_WR):
        bus, dev = (c.addr >> 20) & 0xFF, (c.addr >> 15) & 0x1F
        if bus != hdr.sec_bus or dev != 0:
            return 7
    if c.op in (MEM_RD, MEM_WR):
        first, last = hdr.window()
        lo, hi = c.addr, c.addr + 4 * c.len - 1
        if not hdr.mse or lo < first or hi > last:
            return 7
    return 0


# --------------------------------------------------------------- front end

@dataclass
class FrontEnd:
    """The command front end with its header: what each command returns,
    and the cycle model of cmd_ready_o, rsp_valid_o and rsp_o."""
    p: Params = field(default_factory=Params)
    hdr: Header = field(init=False)
    busy: bool = False
    beat: Rsp | None = None

    def __post_init__(self) -> None:
        self.hdr = Header(self.p)

    def execute(self, c: Cmd, dl_up: int) -> Rsp:
        """The result of c taken now (one beat); applies an HDR_WR."""
        if failed_check(c, self.hdr, self.p):
            return Rsp(LOCAL_UR)
        if c.op in ENDPOINT_OPS:
            # Interim (MAS, Leaf blocks): no requester until #373.
            return Rsp(LOCAL_UR if dl_up else LINK_DOWN)
        if c.op == HDR_RD:
            return Rsp(OK, self.hdr.read((c.addr >> 2) & 0x3FF))
        if c.op == HDR_WR:
            self.hdr.write((c.addr >> 2) & 0x3FF, c.first_be, c.data)
            return Rsp(OK)
        # Interim: HOST_* until #375, ERR_* until #373.
        return Rsp(LOCAL_UR)

    def step(self, rst: int, valid: int, c: Cmd, dl_up: int) -> tuple[int, Rsp | None, bool]:
        """One clock edge with these inputs. Returns cmd_ready_o after the
        edge (rst still applied), the beat out after it, and whether the
        edge took a command."""
        if rst:
            self.hdr.reset()
            self.busy, self.beat = False, None
            return 0, None, False
        take = bool(valid) and not self.busy
        if self.beat is not None and self.beat.last:
            self.busy = False
        self.beat = None
        if take:
            self.beat = self.execute(c, dl_up)
            self.busy = True
        return int(not self.busy), self.beat, take
