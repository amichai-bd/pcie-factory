"""Reference model of pcie_rc_tl: the front end, the Type 1 header, the
error record, the requester, the receive parser, host memory, the
completer, the MSI capture, the credit releases and the transmit stage.

Written from the specs, not the RTL: the command checks and result beats of
wiki/src/rtl/rc_tl/MAS.md#commands, the error record of MAS.md and
wiki/src/arch/rc/TL_SEQ.md#error-record, the byte-enable rule, requests,
completion matching, timeout and transmit of
wiki/src/rtl/rc_tl/REQUESTER.md, the receive checks, completer, host
memory, MSI capture and credits of wiki/src/rtl/rc_tl/INBOUND.md, the
inbound routing and header table of wiki/src/arch/rc/HAS.md, the header and
host memory access of wiki/src/arch/rc/TL_SEQ.md, link down (MAS, Reset and
link down) and the MAS timing.

`RcTl.step` is the cycle model the testbench compares with the DUT at every
clock edge. The completion checks here are stated on byte addresses (the
read's enabled bytes, the completion's data window), not on the RTL's
remaining-bytes counters; the receive checks on the TLP's bytes and its
byte address range; the completer's splits on byte addresses.
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


# Error record bits (TL_SEQ.md, Error record).
(E_UR, E_CA, E_CRS, E_TMO, E_UNEXP, E_POIS, E_MAL, E_COR, E_NONFATAL, E_FATAL, E_MSG,
 E_IN_UR, E_MSI_DROP) = range(13)
ERR_MASK = (1 << 13) - 1     # 31:13 reserved, read 0

# Completion Status [cite: PCIe Base Spec, rev 3.0, §2.2.9].
CS_SC, CS_UR, CS_CRS, CS_CA = 0, 1, 2, 4
MSI_DEPTH = 4     # MSI capture entries (INBOUND.md, MSI capture)

# Fmt and Type of each request (REQUESTER.md, Requests) and completions.
REQ_FMT_TYPE = {CFG_RD: 0x04, CFG_WR: 0x44, MEM_RD: 0x00, MEM_WR: 0x40}
T_CPL, T_CPLLK = 0x0A, 0x0B


def timeout_cycles(div: int) -> int:
    """The completion timeout N (REQUESTER.md, Completion timeout)."""
    return (1 << 21) // div


@dataclass(frozen=True)
class Params:
    """The rc_tl parameters the model uses."""
    vendor_id: int = 0
    device_id: int = 0
    revision_id: int = 0
    host_mem_addr_w: int = 12
    cpl_timeout_div: int = 1
    host_mem_base: int = 0x0000_0000
    msi_addr: int = 0xFEE0_0000
    fc_nph: int = 8


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
    """A t_rc_rsp beat (common_pkg): status, data, last. `known` marks the
    data bits the model knows: a HOST_RD of bytes never written reads
    unspecified data (INBOUND.md, Host memory), which no check compares."""
    status: int
    data: int = 0
    last: int = 1
    known: int = field(default=0xFFFF_FFFF, compare=False)

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


def failed_check(c: Cmd, hdr: Header, p: Params, dl_up: int = 1) -> int:
    """The first of checks 1 to 7 (MAS, Commands) that c fails, or 0."""
    if c.op in ENDPOINT_OPS and not dl_up:
        return 1
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


# --------------------------------------------------------------- TLPs

def dws_of(b: list[int]) -> list[int]:
    """Header bytes as DWs, byte 0 in [7:0] of the first (the dll stream)."""
    return [b[i] | (b[i + 1] << 8) | (b[i + 2] << 16) | (b[i + 3] << 24)
            for i in range(0, len(b), 4)]


def bytes_of(dws: list[int]) -> list[int]:
    return [(dw >> (8 * k)) & 0xFF for dw in dws for k in range(4)]


def request_tlp(c: Cmd, pri_bus: int, tag: int) -> list[int]:
    """The TLP an endpoint command sends (REQUESTER.md, Requests): a 3-DW
    header, TC, Attr, TD, EP and AT 0, Requester ID {pri_bus, 0, 0}."""
    length = c.len if c.op == MEM_RD else 1
    lbe = c.last_be if c.op == MEM_RD else 0
    b = [0] * 12
    b[0] = REQ_FMT_TYPE[c.op]
    b[2], b[3] = (length >> 8) & 0x3, length & 0xFF
    b[4], b[5] = pri_bus, 0
    b[6] = 0 if c.op == MEM_WR else tag
    b[7] = (lbe << 4) | c.first_be
    if c.op in (CFG_RD, CFG_WR):
        b[8] = (c.addr >> 20) & 0xFF                 # bus
        b[9] = (c.addr >> 12) & 0xFF                 # device, function
        b[10] = (c.addr >> 8) & 0xF                  # extended register
        b[11] = c.addr & 0xFC                        # register
    else:
        b[8:12] = [(c.addr >> 24) & 0xFF, (c.addr >> 16) & 0xFF, (c.addr >> 8) & 0xFF,
                   c.addr & 0xFC]
    dws = dws_of(b)
    if c.op in (CFG_WR, MEM_WR):
        dws.append(c.data)
    return dws


def read_bytes(c: Cmd) -> tuple[int, int]:
    """A read's enabled byte range [first, end) (INBOUND.md, Completer:
    total bytes T and first byte B, the endpoint's own rule)."""
    a = c.addr & ~3
    if c.len == 1:
        if c.first_be == 0:
            return a, a + 1
        lo = (c.first_be & -c.first_be).bit_length() - 1
        hi = c.first_be.bit_length() - 1
        return a + lo, a + hi + 1
    lo = (c.first_be & -c.first_be).bit_length() - 1
    hi = c.last_be.bit_length() - 1
    return a + lo, a + 4 * (c.len - 1) + hi + 1


def cpl_tlp(status: int, rid: int, tag: int, data: tuple[int, ...] | list[int] = (), *,
            bc: int = 4, la: int = 0, ep: int = 0, td: int = 0, locked: bool = False,
            length: int | None = None, cid: int = 0x0100, tc: int = 0, attr: int = 0,
            digest: int = 0x5EED_D16E) -> list[int]:
    """A completion TLP as the endpoint sends it (TLP.md, Completions): with
    data when `data` is not empty; Byte Count 4096 sent as 0."""
    n = len(data) if length is None else length
    b = [0] * 12
    b[0] = ((0b010 if data else 0b000) << 5) | (T_CPLLK if locked else T_CPL)
    b[1] = (tc & 7) << 4
    b[2] = (td << 7) | (ep << 6) | ((attr & 3) << 4) | ((n >> 8) & 3)
    b[3] = n & 0xFF
    b[4], b[5] = (cid >> 8) & 0xFF, cid & 0xFF
    b[6] = (status << 5) | ((bc >> 8) & 0xF)
    b[7] = bc & 0xFF
    b[8], b[9] = (rid >> 8) & 0xFF, rid & 0xFF
    b[10] = tag & 0xFF
    b[11] = la & 0x7F
    dws = dws_of(b) + list(data)
    if td:
        dws.append(digest)
    return dws


def read_completions(c: Cmd, rid: int, tag: int, data: list[int],
                     cuts: list[int] | None = None, **kw) -> list[list[int]]:
    """The Successful completions of a MEM_RD, as an endpoint returns them:
    by default one; with `cuts`, the DW count of each completion but the
    last (which takes the rest). Byte Count is the bytes still due from each
    completion's first byte, Lower Address that byte's [6:0]
    [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]."""
    first, end = read_bytes(c)
    out, dw, nxt = [], 0, first
    sizes = list(cuts or []) + [c.len - sum(cuts or [])]
    for n in sizes:
        out.append(cpl_tlp(CS_SC, rid, tag, data[dw:dw + n], bc=(end - nxt) & 0xFFF,
                           la=nxt & 0x7F, **kw))
        dw += n
        nxt = (c.addr & ~3) + 4 * dw
    return out


def tlp_str(dws: list[int]) -> str:
    return " ".join(f"{dw:08x}" for dw in dws)


# --------------------------------------------------------------- front end

HOST = "host"     # a HOST_* past the checks: it waits for host memory


@dataclass
class FrontEnd:
    """The local commands with the header and the error record: what each
    local command or refusal returns (one beat)."""
    p: Params = field(default_factory=Params)
    hdr: Header = field(init=False)
    err: int = 0

    def __post_init__(self) -> None:
        self.hdr = Header(self.p)

    def execute(self, c: Cmd, dl_up: int) -> Rsp | None | str:
        """The beat of c taken now; None when it goes to the requester, HOST
        when it waits for host memory; applies an HDR_WR and an ERR_CLR."""
        chk = failed_check(c, self.hdr, self.p, dl_up)
        if chk == 1:
            return Rsp(LINK_DOWN)
        if chk:
            return Rsp(LOCAL_UR)
        if c.op in ENDPOINT_OPS:
            return None
        if c.op == HDR_RD:
            return Rsp(OK, self.hdr.read((c.addr >> 2) & 0x3FF))
        if c.op == HDR_WR:
            self.hdr.write((c.addr >> 2) & 0x3FF, c.first_be, c.data)
            return Rsp(OK)
        if c.op == ERR_RD:
            return Rsp(OK, self.err)
        if c.op == ERR_CLR:
            self.err &= ~c.data & ERR_MASK
            return Rsp(OK)
        return HOST


# --------------------------------------------------------------- inbound

# TLP kinds by Fmt and Type (wiki/src/arch/tl/TLP.md#kinds): the Fmt values
# each Type allows; messages are Type 10rrr with Fmt 001 or 011.
T_MEM, T_MEMLK, T_IO, T_CFG0, T_CFG1 = 0x00, 0x01, 0x02, 0x04, 0x05
T_ATOMICS = (0x0C, 0x0D, 0x0E)
KIND_FMTS = {T_MEM: (0, 1, 2, 3), T_MEMLK: (0, 1), T_IO: (0, 2), T_CFG0: (0, 2),
             T_CFG1: (0, 2), T_CPL: (0, 2), T_CPLLK: (0, 2),
             0x0C: (2, 3), 0x0D: (2, 3), 0x0E: (2, 3)}
# Message codes that require TC 0 (wiki/src/arch/tl/ROUTING.md#checks,
# step 6): Unlock, power management, error signalling, Set_Slot_Power_Limit
# and INTx.
TC0_CODES = frozenset((0x00, 0x14, 0x18, 0x19, 0x1B, 0x30, 0x31, 0x33, 0x50)
                      + tuple(range(0x20, 0x28)))
MSG_BITS = {0x30: E_COR, 0x31: E_NONFATAL, 0x33: E_FATAL}
CLS_P, CLS_NP, CLS_CPL = "P", "NP", "CPL"


def is_msg(typ: int) -> bool:
    return typ >> 3 == 0b10


def kind_known(fmt: int, typ: int) -> bool:
    """Receive check step 1: Fmt and Type name a kind, Fmt no prefix."""
    if is_msg(typ):
        return fmt in (1, 3)
    return fmt in KIND_FMTS.get(typ, ())


def credit_class(fmt: int, typ: int) -> str:
    """The class the dll counted (wiki/src/arch/dll/FLOW_CONTROL.md):
    memory writes and messages posted, completions, the rest non-posted."""
    if (typ == T_MEM and fmt & 2) or is_msg(typ):
        return CLS_P
    if typ in (T_CPL, T_CPLLK):
        return CLS_CPL
    return CLS_NP


@dataclass(frozen=True)
class RxHdr:
    """A received request's or message's header fields (TLP.md)."""
    fmt: int
    typ: int
    tc: int
    td: int
    ep: int
    attr: int
    lfield: int        # the Length field
    rid: int
    tag: int
    fbe: int
    lbe: int
    code: int          # a message's code (byte 7)
    addr: int          # the byte address, bits [1:0] 0; 64 bits for a 4-DW header

    @staticmethod
    def parse(h: list[int]) -> "RxHdr":
        b = bytes_of(h)
        a = int.from_bytes(bytes(b[8:16] if len(h) == 4 else b[8:12]), "big") & ~3
        return RxHdr(fmt=b[0] >> 5, typ=b[0] & 0x1F, tc=(b[1] >> 4) & 7, td=b[2] >> 7,
                     ep=(b[2] >> 6) & 1, attr=(b[2] >> 4) & 3,
                     lfield=((b[2] & 3) << 8) | b[3], rid=(b[4] << 8) | b[5], tag=b[6],
                     fbe=b[7] & 0xF, lbe=b[7] >> 4, code=b[7], addr=a)

    @property
    def n(self) -> int:
        """Length in DWs, 0 read as 1024."""
        return self.lfield or 1024

    @property
    def wdata(self) -> bool:
        return bool(self.fmt & 2)

    @property
    def data_credits(self) -> int:
        """The data credits the dll counted: Length / 4 rounded up."""
        return (self.lfield + 3) // 4 if self.wdata else 0


def malformed(h: RxHdr) -> bool:
    """INBOUND.md, Receive checks, steps 1 and 3 to 6."""
    if not kind_known(h.fmt, h.typ):
        return True
    if h.typ in (T_MEM, T_MEMLK):
        # Step 3: Length 1 needs only Last BE 0; more needs both non-zero
        # and contiguous unless Length 2 in one aligned QW.
        if h.n == 1:
            if h.lbe:
                return True
        else:
            if not h.fbe or not h.lbe:
                return True
            if not (h.n == 2 and not h.addr & 4) and not (
                    h.fbe in CONTIG_FIRST and h.lbe in CONTIG_LAST):
                return True
        # Step 4: one aligned 4 KiB block.
        if (h.addr & 0xFFF) + 4 * h.n > 0x1000:
            return True
    if h.typ in (T_IO, T_CFG0, T_CFG1):
        if h.lfield != 1 or h.lbe or h.tc or h.attr:
            return True
    if is_msg(h.typ) and h.tc and h.code in TC0_CODES:
        return True
    return False


# The outcome of a received request or message.
DROP, WRITE, CAPTURE, READ, UR_CPL, URLK_CPL, NP_DROP = (
    "drop", "write", "capture", "read", "ur", "urlk", "np_drop")


def judge(h: RxHdr, hdr: Header, p: Params) -> tuple[str, int]:
    """The receive checks, the inbound routing rows (rc HAS, first match)
    and poisoned (INBOUND.md): (outcome, error record bits)."""
    cls = credit_class(h.fmt, h.typ)
    if malformed(h):
        return (NP_DROP if cls == CLS_NP else DROP), 1 << E_MAL
    if is_msg(h.typ):
        if h.ep:
            return DROP, 1 << E_POIS
        return DROP, 1 << MSG_BITS.get(h.code, E_MSG)
    lo, hi = h.addr, h.addr + 4 * h.n - 1
    win_lo, win_hi = hdr.window()
    host_lo = p.host_mem_base
    host_hi = host_lo + (1 << p.host_mem_addr_w) - 1
    if h.typ != T_MEM:
        route = "ur"                              # locked, I/O, configuration, atomic
    elif lo <= win_hi and hi >= win_lo:
        route = "ur"                              # any byte inside the memory window
    elif not hdr.bme:
        route = "ur"
    elif h.wdata and h.n == 1 and p.msi_addr <= lo and hi < p.msi_addr + 0x1000:
        route = "msi"
    elif host_lo <= lo and hi <= host_hi:
        route = "host"
    else:
        route = "ur"
    if route == "ur":
        if cls == CLS_P:
            return DROP, 1 << E_IN_UR
        return (URLK_CPL if h.typ == T_MEMLK else UR_CPL), 1 << E_IN_UR
    if h.ep:
        return (DROP if cls == CLS_P else UR_CPL), 1 << E_POIS
    if route == "msi":
        return CAPTURE, 0
    return (WRITE if h.wdata else READ), 0


@dataclass
class NpEntry:
    """A non-posted queue entry (INBOUND.md, Completer)."""
    kind: str          # READ, UR_CPL, URLK_CPL or NP_DROP
    h: RxHdr


def completions_of(h: RxHdr) -> list[tuple[int, int, int, int]]:
    """A read's completions (INBOUND.md, Completer): (first DW address,
    DWs, Byte Count, Lower Address) each, on byte addresses: total bytes T
    from byte B; each starts where the previous ended and takes the rest
    when it is at most 32 DWs, else ends on the highest 64-byte boundary
    within 128 bytes; Byte Count the bytes from its first byte to the end
    (4096 sent as 0), Lower Address that byte [6:0]."""
    first, end = read_bytes(Cmd(MEM_RD, h.addr & 0xFFFF_FFFF, h.n, h.fbe, h.lbe))
    out, a, left = [], h.addr & 0xFFFF_FFFF, h.n
    s = first
    while left:
        if left <= 32:
            k = left
        else:
            k = ((a + 128) // 64 * 64 - a) // 4
        out.append((a, k, (end - s) & 0xFFF, s & 0x7F))
        a += 4 * k
        left -= k
        s = a
    return out


def be_bytes(be: int) -> int:
    return sum(0xFF << (8 * i) for i in range(4) if (be >> i) & 1)


# --------------------------------------------------------------- cycle model

@dataclass
class Inputs:
    """One cycle of the DUT inputs."""
    rst: int = 0
    cmd_valid: int = 0
    cmd: Cmd = Cmd(0)
    dl_up: int = 1
    tx_ready: int = 1
    rx_valid: int = 0
    rx_data: int = 0
    rx_sop: int = 0
    rx_eop: int = 0
    fc_ph: int = 0xFF
    fc_pd: int = 0xFFF
    fc_nph: int = 0xFF
    fc_npd: int = 0xFFF
    fc_cplh: int = 0xFF
    fc_cpld: int = 0xFFF
    msi_ready: int = 0


@dataclass
class Beat:
    """A beat of tlp_tx_*; all 0 when none is offered."""
    valid: int = 0
    data: int = 0
    sop: int = 0
    eop: int = 0
    np: bool = False
    cpl: bool = False
    known: int = 0xFFFF_FFFF  # data bits the model knows (host memory never written)


@dataclass
class Request:
    """The one outstanding endpoint request (REQUESTER.md)."""
    cmd: Cmd
    rid: int
    tag: int
    tlp: list[int]
    txq: bool = True          # not yet granted by the transmit arbiter
    sent_edge: int | None = None   # the edge the link took its sop (non-posted)
    nxt: int = 0              # a read's next byte due (address)
    end: int = 0              # a read's end byte (exclusive)


@dataclass
class Completion:
    """The completion being received, decided at its third header DW."""
    match: bool
    status: int = OK          # the result when it ends the request
    err: int = 0              # its error record bits, at its eop
    fin: bool = False         # it ends the request at its eop
    dlv: int = 0              # data DWs still to deliver
    lastc: bool = False       # its data ends the request


@dataclass
class Completer:
    """The entry the completer serves and its current completion."""
    e: NpEntry
    cpls: list[tuple[int, int, int, int]]   # (DW address, DWs, Byte Count, Lower Address)
    i: int = 0                # the current completion
    iss: int = 0              # its reads still to issue
    got: int = 0              # its DWs still to arrive
    rd: int = 0               # the read's next DW to read (index)
    data: list[int] = field(default_factory=list)   # its DWs read, masked
    known: list[int] = field(default_factory=list)  # their bits known (written, or masked)
    gnt: bool = False         # granted, waiting for the link to take its eop


@dataclass
class Rx:
    """The received request or message after its last header DW."""
    h: RxHdr
    out: str
    err: int
    cnt: int = 0              # data DWs still to come
    k: int = 0                # data DWs taken
    first_dw: int = 0         # its first data DW (an MSI's data)


class RcTl:
    """The cycle model of pcie_rc_tl's ports (MAS, Timing)."""

    def __init__(self, p: Params = Params()) -> None:
        self.p = p
        self.fe = FrontEnd(p)
        self.n_tmo = timeout_cycles(p.cpl_timeout_div)
        self.edge = 0
        # Host memory is never reset and has no initial contents (MAS,
        # Reset and link down): the model tracks the bytes written, and a
        # byte never written is unknown, compared by no check.
        self.mem = [0] * (1 << (p.host_mem_addr_w - 2))
        self.known = [0] * (1 << (p.host_mem_addr_w - 2))
        self.wr: tuple[int, int, int] | None = None     # a write DW, written at the next edge
        self.reset()

    @property
    def hdr(self) -> Header:
        return self.fe.hdr

    @property
    def err(self) -> int:
        return self.fe.err

    def reset(self) -> None:
        """rst: everything but host memory (MAS, Reset and link down)."""
        self.fe.hdr.reset()
        self.fe.err = 0
        self.busy = False
        self.beat: Rsp | None = None
        self.tag = 0
        self.req: Request | None = None
        self.host: tuple[bool, int, int, int] | None = None   # (write, DW, lanes, data)
        self.host_rd: tuple[int, int] | None = None   # a HOST_RD's (data, known), its beat next
        self.host_last = False             # the front end had the latest port grant
        self.msi: list[tuple[int, int]] = []
        self.link_reset()

    def link_reset(self) -> None:
        """lrst: the receive parser, the non-posted queue and the completer,
        the transmit stage and the pending releases."""
        self.rx_busy = False
        self.rx_cpl = False
        self.rx_idx = 0
        self.rx_hdr: list[int] = []
        self.rx: Rx | None = None
        self.cpl: Completion | None = None
        self.arb: tuple[list[int], bool, bool, list[int]] | None = None   # (TLP, non-posted, completion, known)
        self.arb_beat = 0
        self.out = Beat()
        self.skid = Beat()
        self.npq: list[NpEntry] = []       # entries in the queue's RAM
        self.np_head: NpEntry | None = None
        self.cp: Completer | None = None
        self.cp_rd: tuple[int, int] | None = None     # the completer's (data, known), pushed next
        self.rel_p: int | None = None      # data credits released in this cycle
        self.rel_np: int | None = None

    # ------------------------------------------------------------ outputs

    def msi_out(self) -> tuple[int, int] | None:
        return self.msi[0] if self.msi else None

    def cpl_offer(self) -> bool:
        """The completer offers its completion: all its DWs are in."""
        cp = self.cp
        return cp is not None and not cp.gnt and cp.got == 0

    # ------------------------------------------------------------ edges

    def step(self, i: Inputs) -> dict:
        """One clock edge with these inputs. Returns the outputs after the
        edge (rst_i still applied) and what happened at it."""
        ev: dict = {"take": False, "tx_taken": None, "events": 0, "cpl_sent": None}
        self.edge += 1
        # A received write DW taken at the last edge is written at this one,
        # whatever else happens: it holds the host memory port.
        wr_now, self.wr = self.wr, None
        if wr_now is not None:
            self.write_mem(*wr_now)
        pri_bus = self.hdr.pri_bus        # the header's flops before this edge
        if i.rst:
            self.reset()
            ev |= {"cmd_ready": 0, "beat": None, "tx": Beat(), "msi": None, "rel_p": None,
                   "rel_np": None}
            return ev
        up = bool(i.dl_up)
        req = self.req
        # ---- transmit: the link takes a beat; the arbiter grants
        np_sop = p_eop = cpl_eop = False
        out_take = up and self.out.valid and i.tx_ready
        if out_take:
            ev["tx_taken"] = self.out
            np_sop = bool(self.out.sop and self.out.np)
            p_eop = bool(self.out.eop and not self.out.np and not self.out.cpl)
            cpl_eop = bool(self.out.eop and self.out.cpl)
        req_valid = req is not None and req.txq
        req_p = req_valid and req.cmd.op == MEM_WR
        cpl_valid = self.cpl_offer()
        cpl_el = cpl_valid and self.cpl_eligible(i)
        idle = up and self.arb is None
        cpl_grant = idle and cpl_el and not req_p
        grant = (idle and req_valid and self.eligible(req, i)
                 and (req_p or not cpl_el))
        # ---- host memory: a write DW wins; else the waiting accesses take turns
        host_req = self.host is not None
        cp_req = up and self.cp is not None and self.cp.iss > 0
        host_gnt = cp_gnt = False
        if wr_now is None:
            if host_req and cp_req:
                host_gnt, cp_gnt = not self.host_last, self.host_last
            else:
                host_gnt, cp_gnt = host_req, cp_req
        # ---- receive: the beats of this edge
        hdr_ev = dat_ev = eop_ev = False
        cfields = None
        rx_events = 0
        rx_last = None            # a request's or message's eop: (Rx)
        wr_next = None
        if up and i.rx_valid:
            if i.rx_sop:
                b0 = i.rx_data & 0xFF
                fmt, typ = b0 >> 5, b0 & 0x1F
                # Completion kinds with a 3-DW header (TLP.md, Kinds).
                self.rx_cpl = typ in (T_CPL, T_CPLLK) and fmt in (0b000, 0b010)
                self.rx_hdr = [i.rx_data]
                self.rx = None
                idx = 0
            else:
                idx = self.rx_idx
                if self.rx_cpl:
                    if idx < 3:
                        self.rx_hdr.append(i.rx_data)
                    hdr_ev = idx == 2
                    dat_ev = idx > 2
                    eop_ev = bool(i.rx_eop)
                else:
                    hdr_end = 3 if (self.rx_hdr[0] >> 5) & 1 else 2
                    if idx <= hdr_end:
                        self.rx_hdr.append(i.rx_data)
                    if idx == hdr_end:
                        h = RxHdr.parse(self.rx_hdr)
                        out, err = judge(h, self.hdr, self.p)
                        self.rx = Rx(h, out, err, cnt=h.n if h.wdata else 0)
                        ev["rx"] = self.rx
                    elif self.rx is not None and self.rx.cnt:
                        # A data DW: a host write is written at the next edge.
                        rx = self.rx
                        if rx.k == 0:
                            rx.first_dw = i.rx_data
                        if rx.out == WRITE:
                            n = rx.h.n
                            be = rx.h.fbe if rx.k == 0 else (rx.h.lbe if rx.k == n - 1 else 0xF)
                            wr_next = (self.host_dw(rx.h.addr) + rx.k, be, i.rx_data)
                        rx.k += 1
                        rx.cnt -= 1
                    if i.rx_eop and self.rx is not None:
                        rx_last = self.rx
                        rx_events = self.rx.err
                        self.rx = None
            if hdr_ev:
                cfields = self.cpl_fields(self.rx_hdr)
        # ---- the requester's decisions, on the state before the edge
        res: Rsp | None = None
        events = 0
        down = req is not None and not up
        dec = None
        if hdr_ev:
            dec = self.decide(req, cfields)
            ev["cpl"] = (cfields, dec)
        cur = dec if hdr_ev else self.cpl
        if down:
            res = Rsp(LINK_DOWN)
        else:
            if dat_ev and self.cpl is not None and self.cpl.dlv:
                last = self.cpl.lastc and self.cpl.dlv == 1
                res = Rsp(OK, i.rx_data, int(last))
                self.cpl.dlv -= 1
            elif eop_ev and cur is not None and cur.fin:
                res = Rsp(cur.status)
            if eop_ev and cur is not None:
                events |= cur.err
            # The timeout: N cycles after the sop's edge, but never from a
            # matched completion's third header DW to its eop, both edges
            # included.
            holding = ((hdr_ev and dec.match)
                       or (self.cpl is not None and self.cpl.match))
            if (res is None and not holding and req is not None
                    and req.sent_edge is not None and self.edge - req.sent_edge >= self.n_tmo):
                res = Rsp(TIMEOUT)
                events |= 1 << E_TMO
            if res is None and req is not None and req.cmd.op == MEM_WR and p_eop:
                res = Rsp(OK)
        # ---- the inbound side at its eop: capture, queue entry, release
        events |= rx_events
        np_push = None
        rel_p = None
        if rx_last is not None:
            h = rx_last.h
            cls = credit_class(h.fmt, h.typ)
            if rx_last.out == CAPTURE:
                ev["msi_push"] = ((h.addr >> 2) & 0x3FF, rx_last.first_dw)
            if cls == CLS_P:
                rel_p = h.data_credits
            elif cls == CLS_NP:
                np_push = NpEntry(rx_last.out, h)
        # ---- MSI capture: a take at this edge makes room
        if self.msi and i.msi_ready:
            self.msi.pop(0)
        if "msi_push" in ev:
            if len(self.msi) == MSI_DEPTH:
                events |= 1 << E_MSI_DROP
            else:
                self.msi.append(ev["msi_push"])
        # ---- the front end: the take, and the beat out after this edge
        take = bool(i.cmd_valid) and not self.busy
        if self.beat is not None and self.beat.last:
            self.busy = False
        self.beat = None
        start = None
        host_new = None
        if take:
            ev["take"] = True
            self.busy = True
            local = self.fe.execute(i.cmd, i.dl_up)
            if local is None:
                start = i.cmd
            elif local == HOST:
                c = i.cmd
                host_new = (c.op == HOST_WR, (c.addr >> 2) & (len(self.mem) - 1),
                            c.first_be, c.data)
            else:
                self.beat = local
        if res is not None:
            assert self.beat is None, "a requester beat with a local beat"
            self.beat = res
        # Host memory: a HOST_WR's beat after its grant, a HOST_RD's after its data.
        if self.host_rd is not None:
            assert self.beat is None, "a host memory beat with another beat"
            self.beat = Rsp(OK, self.host_rd[0], 1, self.host_rd[1])
            self.host_rd = None
        if host_gnt:
            wr, dw, be, data = self.host
            if wr:
                self.write_mem(dw, be, data)
                assert self.beat is None, "a host memory beat with another beat"
                self.beat = Rsp(OK)
            else:
                self.host_rd = (self.mem[dw] & self.known[dw], self.known[dw])
            self.host = None
            self.host_last = True
        if host_new is not None:
            self.host = host_new
        self.fe.err |= events
        ev["events"] = events
        # ---- requester state
        if req is not None:
            if grant:
                req.txq = False
            if np_sop:
                req.sent_edge = self.edge
                self.tag = (self.tag + 1) & 0x1F
            if res is not None and res.last:
                self.req = None
        if start is not None:
            c = start
            first, end = read_bytes(c) if c.op == MEM_RD else (0, 0)
            self.req = Request(c, rid=self.hdr.pri_bus << 8, tag=self.tag,
                               tlp=request_tlp(c, self.hdr.pri_bus, self.tag), nxt=first, end=end)
        # ---- completion and parser state
        if hdr_ev:
            self.cpl = dec
        if eop_ev or not up:
            self.cpl = None
        if up and i.rx_valid:
            if i.rx_sop:
                self.rx_busy, self.rx_idx = True, 1
            else:
                self.rx_idx = min(self.rx_idx + 1, 63)
            if i.rx_eop:
                self.rx_busy = False
        self.wr = wr_next
        # ---- the completer: its read data, its grants, then a new entry
        cp = self.cp
        cpl_tlp_new = None
        rel_np = None
        if cp is not None:
            if self.cp_rd is not None:
                cp.data.append(self.cp_rd[0])
                cp.known.append(self.cp_rd[1])
                cp.got -= 1
            self.cp_rd = None
            if cp_gnt:
                e = cp.e
                j = cp.rd
                be = e.h.fbe if j == 0 else (e.h.lbe if j == e.h.n - 1 else 0xF)
                dw, m = self.host_dw(e.h.addr) + j, be_bytes(be)
                # Bytes F and L do not enable are 0, known; the others are
                # known once written.
                self.cp_rd = (self.mem[dw] & m & self.known[dw], (self.known[dw] & m) | ~m & 0xFFFF_FFFF)
                cp.rd += 1
                cp.iss -= 1
                self.host_last = False
            if cpl_grant:
                cp.gnt = True
                cpl_tlp_new = self.cpl_tlp(cp, pri_bus)
                ev["cpl_sent"] = (cp.e, cpl_tlp_new)
            if cpl_eop:
                cp.gnt = False
                cp.i += 1
                if cp.i >= len(cp.cpls):         # an Unsupported Request has one, with no DWs
                    self.cp = None
                else:
                    cp.iss = cp.got = cp.cpls[cp.i][1]
                    cp.data, cp.known = [], []
        elif self.np_head is not None:
            # The completer takes the head when idle; its release follows.
            e = self.np_head
            rel_np = e.h.data_credits
            ev["np_pop"] = e
            if e.kind == READ:
                cpls = completions_of(e.h)
                self.cp = Completer(e, cpls, iss=cpls[0][1], got=cpls[0][1])
            elif e.kind in (UR_CPL, URLK_CPL):
                self.cp = Completer(e, [])
        # The queue: a pop and a refill share an edge; an entry pushed at
        # this edge reaches the head two edges later at the earliest.
        popped = rel_np is not None
        if self.npq and (self.np_head is None or popped):
            self.np_head = self.npq.pop(0)
        elif popped:
            self.np_head = None
        if np_push is not None:
            self.npq.append(np_push)
        self.rel_p, self.rel_np = rel_p, rel_np
        # ---- transmit state: the output stage, then the arbiter
        a_take = self.arb is not None and not self.skid.valid
        a_beat = Beat()
        if a_take:
            tlp, np, cplf, kn = self.arb
            a_beat = Beat(1, tlp[self.arb_beat], int(self.arb_beat == 0),
                          int(self.arb_beat == len(tlp) - 1), np, cplf, kn[self.arb_beat])
        if not self.out.valid or out_take:
            if self.skid.valid:
                self.out, self.skid = self.skid, Beat()
            else:
                self.out = a_beat
        elif a_take:
            self.skid = a_beat
        if a_take:
            self.arb_beat += 1
            if self.arb_beat == len(self.arb[0]):
                self.arb = None
        if grant:
            self.arb = (list(req.tlp), req.cmd.op != MEM_WR, False, [0xFFFF_FFFF] * len(req.tlp))
            self.arb_beat = 0
        if cpl_grant:
            kn = [0xFFFF_FFFF] * 3 + (list(cp.known) if cp.e.kind == READ else [])
            self.arb = (cpl_tlp_new, False, True, kn)
            self.arb_beat = 0
        if not up:
            self.link_reset()
            self.cp_rd = None
        ev |= {"cmd_ready": int(not self.busy), "beat": self.beat, "tx": self.out,
               "msi": self.msi_out(), "rel_p": self.rel_p, "rel_np": self.rel_np}
        return ev

    # ------------------------------------------------------------ helpers

    def write_mem(self, dw: int, be: int, data: int) -> None:
        m = be_bytes(be)
        self.mem[dw] = (self.mem[dw] & ~m) | (data & m)
        self.known[dw] |= m

    def host_dw(self, addr: int) -> int:
        """Host memory DW index of a byte address inside it."""
        return ((addr & 0xFFFF_FFFF) - self.p.host_mem_base) >> 2

    def cpl_eligible(self, i: Inputs) -> bool:
        """REQUESTER.md, Transmit: 1 completion header credit and the data
        credits of its DWs; all ones passes."""
        cp = self.cp
        k = cp.cpls[cp.i][1] if cp.cpls else 0
        return i.fc_cplh != 0 and i.fc_cpld >= (k + 3) // 4

    @staticmethod
    def cpl_tlp(cp: Completer, pri_bus: int) -> list[int]:
        """The current completion (INBOUND.md, Completer): Completer ID
        {Primary Bus Number, 0, 0} at the grant, TC and Attr of the request."""
        h = cp.e.h
        cid = pri_bus << 8
        if cp.e.kind == READ:
            _, k, bc, la = cp.cpls[cp.i]
            assert len(cp.data) == k
            return cpl_tlp(CS_SC, h.rid, h.tag, cp.data, bc=bc, la=la, cid=cid, tc=h.tc,
                           attr=h.attr)
        if h.typ in (T_MEM, T_MEMLK):
            first, end = read_bytes(Cmd(MEM_RD, h.addr & 0xFFFF_FFFF, h.n, h.fbe, h.lbe))
            bc, la = (end - first) & 0xFFF, first & 0x7F
        else:
            bc, la = 4, 0
        return cpl_tlp(CS_UR, h.rid, h.tag, bc=bc, la=la, cid=cid, tc=h.tc, attr=h.attr,
                       locked=cp.e.kind == URLK_CPL)

    @staticmethod
    def eligible(req: Request, i: Inputs) -> bool:
        """REQUESTER.md, Transmit: 1 header credit and the data credits of
        its class; all ones passes."""
        if req.cmd.op == MEM_WR:
            return i.fc_ph != 0 and i.fc_pd >= 1
        dc = 1 if req.cmd.op == CFG_WR else 0
        return i.fc_nph != 0 and i.fc_npd >= dc

    @staticmethod
    def cpl_fields(h: list[int]) -> dict:
        b = bytes_of(h)
        fmt, typ = b[0] >> 5, b[0] & 0x1F
        lf = ((b[2] & 3) << 8) | b[3]
        bc = ((b[6] & 0xF) << 8) | b[7]
        wdata = bool(fmt & 0b010)
        return {"locked": typ == T_CPLLK, "wdata": wdata, "ep": (b[2] >> 6) & 1,
                "td": b[2] >> 7,
                "status": b[6] >> 5, "n": (lf or 1024) if wdata else 0,
                "bc": bc or 4096, "rid": (b[8] << 8) | b[9], "tag": b[10], "la": b[11] & 0x7F}

    def decide(self, req: Request | None, f: dict) -> Completion:
        """REQUESTER.md, Completion matching: match, then the first row of
        the checks that applies."""
        sent = req is not None and req.sent_edge is not None
        if not (sent and not f["locked"] and f["rid"] == req.rid and f["tag"] == req.tag):
            return Completion(False, err=1 << E_UNEXP)
        op, st = req.cmd.op, f["status"]

        def fail(status: int, bit: int) -> Completion:
            return Completion(True, status=status, err=1 << bit, fin=True)

        if st == CS_CRS:
            return fail(CRS, E_CRS) if op in (CFG_RD, CFG_WR) else fail(BAD, E_MAL)
        if st == CS_UR:
            return fail(UR, E_UR)
        if st == CS_CA:
            return fail(CA, E_CA)
        if st != CS_SC:
            return fail(BAD, E_MAL)
        n = f["n"]
        if op == CFG_RD:
            mal = not f["wdata"] or n != 1
        elif op == CFG_WR:
            mal = f["wdata"]
        else:
            # The data window: whole DWs from the DW of the next byte due.
            start = req.nxt & ~3
            stop = start + 4 * n
            last_dw_end = (req.end + 3) & ~3
            mal = (not f["wdata"] or f["bc"] != req.end - req.nxt or f["la"] != req.nxt & 0x7F
                   or stop > last_dw_end or (stop < last_dw_end and stop % 64 != 0))
        if mal:
            return fail(BAD, E_MAL)
        if f["ep"]:
            return fail(BAD, E_POIS)
        if op == CFG_WR:
            return Completion(True, fin=True)
        if op == CFG_RD:
            return Completion(True, dlv=1, lastc=True)
        start = req.nxt & ~3
        stop = start + 4 * n
        lastc = stop >= req.end
        req.nxt = stop
        return Completion(True, dlv=n, lastc=lastc)
