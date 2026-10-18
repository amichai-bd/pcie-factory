"""Independent host (root complex) model for the endpoint testbench.

The host sits on the upper TLP stream of its own ``link`` (the downstream
end of a back-to-back pair) and talks to the endpoint in TLP bytes:

* ``pop_tx(avail)`` hands the testbench the next TLP to send, gated by the
  credits the host's ``link`` shows available and by the host's ordering;
* ``receive(raw)`` takes every TLP the link delivers, at once (the host
  never stalls its receive stream), checks it and answers it.

On top of that it enumerates the function (configuration reads and writes,
the capability walk, BAR sizing and assignment), sets up MSI and MSI-X,
issues memory reads and writes with full completion checking (split
completions included), tracks its tags, answers the endpoint's memory reads
from a sparse host memory, and records interrupt writes and messages.
Multi-step flows are generator procedures run with ``start``; they resume
as their requests complete, so the same code runs under a cocotb
testbench and a pure-Python loopback.

Written from the PCIe Base Spec rev 3.0 (the author's reading, cited by
section, never reproduced) and the HAS pages under ``wiki/src/arch/``;
it shares no code with the architecture models or the other testbench
models. Contract: ``wiki/src/dv/core/HOST.md``.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Generator, Iterable

from pcie_core_host_tlp import (
    COMPLETION,
    CPL_CA,
    CPL_CRS,
    CPL_SC,
    CPL_UR,
    NON_POSTED,
    POSTED,
    ROUTE_LOCAL,
    STATUS_NAMES,
    Tlp,
    TlpError,
    bdf_id,
    byte_enables,
    cfg_read,
    cfg_write,
    completion,
    from_beats,
    mem_read,
    mem_write,
    message,
    to_beats,
)

# Configuration space offsets [cite: PCIe Base Spec, rev 3.0, §7.5.1]
# [cite: PCIe Base Spec, rev 3.0, §7.5.2].
CFG_ID = 0x00
CFG_COMMAND = 0x04
CFG_CLASS = 0x08
CFG_HEADER = 0x0C
CFG_BAR0 = 0x10
CFG_SUBSYS = 0x2C
CFG_ROM = 0x30
CFG_CAP_PTR = 0x34
CMD_MEM = 1 << 1
CMD_BUS_MASTER = 1 << 2
STATUS_CAP_LIST = 1 << 20  # Status bit 4, in the Command/Status DW

# Capability IDs [cite: PCIe Base Spec, rev 3.0, §7.5.1].
CAP_PM = 0x01
CAP_MSI = 0x05
CAP_PCIE = 0x10
CAP_MSIX = 0x11

# Completion status by name, for ``HostModel.read_status``.
CPL_STATUS = {"SC": CPL_SC, "UR": CPL_UR, "CRS": CPL_CRS, "CA": CPL_CA}

# Credit field widths on the link (all ones: infinite).
_INFINITE = {"ph": 0xFF, "pd": 0xFFF, "nph": 0xFF, "npd": 0xFFF, "cplh": 0xFF, "cpld": 0xFFF}
_CLASS_KEYS = {POSTED: ("ph", "pd"), NON_POSTED: ("nph", "npd"), COMPLETION: ("cplh", "cpld")}


class HostCheckError(AssertionError):
    """A rule the endpoint broke, as seen from the host."""


@dataclass
class Request:
    """One host request and its outcome.

    ``status`` is None while outstanding, then ``"SC"``, ``"UR"``,
    ``"CA"``, ``"CRS"``, ``"poisoned"``, ``"timeout"`` or ``"error"``.
    """

    kind: str  # cfg_rd, cfg_wr, mem_rd, mem_wr, msg, raw
    tlp: Tlp
    tag: int | None = None
    status: str | None = None
    done: bool = False
    value: int | None = None  # configuration read data
    data: bytearray = field(default_factory=bytearray)  # memory read data, enabled bytes
    completions: int = 0
    completion_sizes: list = field(default_factory=list)  # data bytes per completion
    errors: list = field(default_factory=list)
    expected_completer: int | None = None
    sent_at: int | None = None
    # Memory read tracking [cite: PCIe Base Spec, rev 3.0, §2.3.1.1].
    first_byte: int = 0
    total: int = 0
    remaining: int = 0
    next_byte: int = 0
    end_dw: int = 0  # exclusive byte address of the end of the last DW
    poisoned: bool = False

    @property
    def ok(self) -> bool:
        return self.done and self.status == "SC" and not self.errors


@dataclass
class Bar:
    index: int
    size: int
    is_64: bool = False
    prefetchable: bool = False
    io: bool = False
    base: int = 0


@dataclass
class MsiCap:
    offset: int
    is_64: bool
    multiple_capable: int
    per_vector_mask: bool

    @property
    def data_offset(self) -> int:
        return self.offset + (0xC if self.is_64 else 0x8)


@dataclass
class MsixCap:
    offset: int
    table_size: int
    table_bir: int
    table_offset: int
    pba_bir: int
    pba_offset: int


@dataclass
class DeviceInfo:
    """What enumeration found and assigned."""

    bdf: tuple
    vendor_id: int = 0
    device_id: int = 0
    revision: int = 0
    class_code: int = 0
    header_type: int = 0
    subsys_vendor_id: int = 0
    subsys_id: int = 0
    caps: list = field(default_factory=list)  # [(id, offset)] in list order
    ext_caps: list = field(default_factory=list)  # [(id, version, offset)]
    bars: dict = field(default_factory=dict)  # index -> Bar
    rom_size: int = 0
    msi: MsiCap | None = None
    msix: MsixCap | None = None
    pm_offset: int | None = None
    pcie_offset: int | None = None
    pcie_port_type: int | None = None
    mps_supported: int | None = None  # bytes

    @property
    def id(self) -> int:
        return bdf_id(*self.bdf)

    def cap(self, cap_id: int) -> int | None:
        for cid, off in self.caps:
            if cid == cap_id:
                return off
        return None

    def bar_addr(self, index: int, offset: int = 0) -> int:
        bar = self.bars[index]
        if offset >= bar.size:
            raise ValueError(f"offset {offset:#x} outside BAR{index} ({bar.size:#x} bytes)")
        return bar.base + offset


@dataclass
class Interrupt:
    kind: str  # "msi" or "msix"
    vector: int
    address: int
    data: int
    bdf: int


@dataclass
class Message:
    code: int
    routing: int
    requester_id: int
    data: bytes


class HostMemory:
    """Sparse byte-addressed host memory; unwritten bytes read as ``fill``."""

    def __init__(self, fill: int = 0):
        self._mem: dict[int, int] = {}
        self.fill = fill & 0xFF

    def write(self, addr: int, data: bytes, mask: Iterable[bool] | None = None) -> None:
        mask = list(mask) if mask is not None else [True] * len(data)
        for i, b in enumerate(data):
            if mask[i]:
                self._mem[addr + i] = b

    def read(self, addr: int, n: int) -> bytes:
        return bytes(self._mem.get(addr + i, self.fill) for i in range(n))


class Procedure:
    """A running generator procedure: it yields a Request (or a list of
    them) and is resumed with it once every one is done."""

    def __init__(self, gen: Generator):
        self.gen = gen
        self.waiting: list[Request] = []
        self.yielded = None  # what the generator last yielded, sent back on resume
        self.done = False
        self.result = None
        self.error: BaseException | None = None


def _encode_mps(nbytes: int) -> int:
    return {128: 0, 256: 1, 512: 2, 1024: 3, 2048: 4, 4096: 5}[nbytes]


class HostModel:
    """Root complex model at the ``link`` upper TLP stream.

    Parameters:
      requester_id: the host's own ID (bus 0, device 0, function 0 by default).
      num_tags: tags 0 to num_tags - 1 (32 without extended tags
        [cite: PCIe Base Spec, rev 3.0, §2.2.6.2]).
      max_payload: the largest data payload the host sends or accepts.
      rcb: the completer's read completion boundary the host checks
        against (64 bytes for the endpoint).
      host_rcb: the boundary the host's own completions split on.
      cpl_split: ``"fewest"`` (largest completions the payload and RCB allow)
        or ``"rcb"`` (a completion per RCB block) for the endpoint's reads.
      timeout: cycles of ``tick`` after which an unanswered request fails;
        None never.
      strict: raise HostCheckError at the first violation; otherwise record
        it in ``errors`` and carry on.
    """

    def __init__(self, requester_id: int = 0, num_tags: int = 32, max_payload: int = 128,
                 rcb: int = 64, host_rcb: int = 64, cpl_split: str = "fewest",
                 timeout: int | None = None, strict: bool = True,
                 mem32_base: int = 0x8000_0000, mem32_limit: int = 0xFFFF_FFFF):
        if not 1 <= num_tags <= 256:
            raise ValueError("num_tags must be 1 to 256")
        if cpl_split not in ("fewest", "rcb"):
            raise ValueError("cpl_split is 'fewest' or 'rcb'")
        self.requester_id = requester_id
        self.num_tags = num_tags
        self.max_payload = max_payload
        self.rcb = rcb
        self.host_rcb = host_rcb
        self.cpl_split = cpl_split
        self.timeout = timeout
        self.strict = strict
        self.memory = HostMemory()
        self.now = 0
        self.errors: list[str] = []
        self.interrupts: list[Interrupt] = []
        self.messages: list[Message] = []
        self.received: list[Tlp] = []
        self.devices: dict[int, DeviceInfo] = {}
        self.on_interrupt: Callable[[Interrupt], None] | None = None
        # Status the host answers an endpoint read with: f(address, nbytes) -> CPL_*.
        self.read_status: Callable[[int, int], int] | None = None
        self._mem32_base = self._mem32_next = mem32_base
        # Limits of the endpoint's own read requests (core HAS, F2).
        self.ep_tags = 8
        self.ep_max_read_request = 128
        self._mem32_limit = mem32_limit
        self._free_tags = list(range(num_tags))
        self._outstanding: dict[int, Request] = {}
        self._tag_wait: deque[Request] = deque()
        self._tx: list[tuple[tuple[str, int, int], bytes, Request | None]] = []
        self._procs: list[Procedure] = []
        self._command: dict[int, int] = {}  # Command register as written, per device ID
        self._irq_targets: dict[tuple[int, int], tuple[str, int, int]] = {}
        self._irq_addrs: dict[int, int] = {}  # address -> device ID

    # ------------------------------------------------------------------
    # Errors
    def _fail(self, msg: str, req: Request | None = None) -> None:
        text = f"[host @{self.now}] {msg}"
        self.errors.append(text)
        if req is not None:
            req.errors.append(text)
        if self.strict:
            raise HostCheckError(text)

    def link_down(self) -> None:
        """The host's link went down: its data link layer has abandoned
        every TLP it held, and the endpoint resets its function, so drop
        the queue, the outstanding requests (status ``"link_down"``), the
        running procedures and what enumeration learnt. Host memory and the
        interrupt and message logs stay
        [cite: PCIe Base Spec, rev 3.0, §2.9.1]."""
        for req in self.outstanding():
            req.status, req.done = "link_down", True
        self._tx.clear()
        self._outstanding.clear()
        self._tag_wait.clear()
        self._free_tags = list(range(self.num_tags))
        self._procs.clear()
        self.devices.clear()
        self._mem32_next = self._mem32_base
        self._command.clear()
        self._irq_targets.clear()
        self._irq_addrs.clear()

    # ------------------------------------------------------------------
    # Issuing requests
    def cfg_read(self, bdf: tuple, reg: int, first_be: int = 0xF, type1: bool = False) -> Request:
        """Configuration read of the DW at byte offset ``reg``."""
        self._check_reg(reg)
        t = cfg_read(self.requester_id, 0, *bdf, reg, first_be, type1)
        return self._issue_np(Request("cfg_rd", t, expected_completer=bdf_id(*bdf)))

    def cfg_write(self, bdf: tuple, reg: int, value: int, first_be: int = 0xF,
                  type1: bool = False) -> Request:
        """Configuration write of the DW at ``reg``, only the enabled bytes."""
        self._check_reg(reg)
        t = cfg_write(self.requester_id, 0, *bdf, reg, value, first_be, type1)
        if reg == CFG_COMMAND and not type1:
            ident = bdf_id(*bdf)
            old = self._command.get(ident, 0)
            for i in range(2):  # Command is bytes 0 and 1
                if first_be >> i & 1:
                    old = (old & ~(0xFF << 8 * i)) | (value & (0xFF << 8 * i))
            self._command[ident] = old & 0xFFFF
        return self._issue_np(Request("cfg_wr", t, expected_completer=bdf_id(*bdf)))

    def mem_read(self, addr: int, nbytes: int, tc: int = 0, attr: int = 0) -> Request:
        """One memory read request for the bytes [addr, addr + nbytes),
        0 to 4096 bytes inside one 4 KiB block
        [cite: PCIe Base Spec, rev 3.0, §2.2.7]; 0 is a zero-length read."""
        if not 0 <= nbytes <= 4096:
            raise ValueError("a read is 0 to 4096 bytes")
        if nbytes and (addr >> 12) != ((addr + nbytes - 1) >> 12):
            raise ValueError(f"read {addr:#x}+{nbytes} crosses a 4 KiB boundary")
        dw, ndw, fbe, lbe = byte_enables(addr, nbytes)
        t = mem_read(self.requester_id, 0, dw, ndw, fbe, lbe, tc, attr)
        req = Request("mem_rd", t, expected_completer=self._owner(dw))
        req.first_byte = addr if nbytes else dw
        req.total = nbytes if nbytes else 1
        req.remaining = req.total
        req.next_byte = req.first_byte
        req.end_dw = dw + 4 * ndw
        return self._issue_np(req)

    def mem_write(self, addr: int, data: bytes, tc: int = 0, attr: int = 0) -> list[Request]:
        """Posted writes of ``data`` at ``addr``: split at the host's maximum
        payload and at 4 KiB boundaries. Each request is done once queued."""
        out = []
        pos = 0
        data = bytes(data)
        if not data:
            raise ValueError("a write carries at least one byte")
        while pos < len(data):
            a = addr + pos
            dw = a & ~3
            limit = min(dw + self.max_payload, (a | 0xFFF) + 1)
            n = min(len(data) - pos, limit - a)
            dwa, ndw, fbe, lbe = byte_enables(a, n)
            buf = bytearray(4 * ndw)
            off = a - dwa
            buf[off:off + n] = data[pos:pos + n]
            t = mem_write(self.requester_id, dwa, bytes(buf), fbe, lbe, tc, attr)
            req = Request("mem_wr", t, status="SC", done=True)
            self._enqueue(t, req)
            out.append(req)
            pos += n
        return out

    def mem_write32(self, addr: int, value: int) -> Request:
        """One aligned DW write, as software writes MSI-X table entries."""
        if addr & 3:
            raise ValueError("DW write needs a DW-aligned address")
        return self.mem_write(addr, (value & 0xFFFFFFFF).to_bytes(4, "little"))[0]

    def send_message(self, code: int, routing: int = ROUTE_LOCAL, data: bytes = b"") -> Request:
        """A message to the endpoint, e.g. Set_Slot_Power_Limit or
        PME_Turn_Off (posted)."""
        t = message(self.requester_id, code, routing, data)
        req = Request("msg", t, status="SC", done=True)
        self._enqueue(t, req)
        return req

    def send_request(self, tlp: Tlp) -> Request:
        """Any TLP the testbench builds (negative cases). A non-posted one
        gets a tag and waits for one completion, checked only for its
        Requester ID and tag; a posted one is done once queued."""
        cls = tlp.credit_class
        if cls == NON_POSTED:
            return self._issue_np(Request("raw", tlp))
        req = Request("raw", tlp, status="SC", done=True)
        self._enqueue(tlp, req)
        return req

    def _check_reg(self, reg: int) -> None:
        if reg & 3 or not 0 <= reg < 0x1000:
            raise ValueError(f"configuration offset {reg:#x} is not a DW of the 4 KiB space")

    def _owner(self, addr: int) -> int | None:
        for dev in self.devices.values():
            for bar in dev.bars.values():
                if bar.size and bar.base <= addr < bar.base + bar.size:
                    return dev.id
        return None

    def _issue_np(self, req: Request) -> Request:
        self._tag_wait.append(req)
        self._assign_tags()
        return req

    def _assign_tags(self) -> None:
        while self._tag_wait and self._free_tags:
            req = self._tag_wait.popleft()
            req.tag = self._free_tags.pop(0)
            req.tlp.tag = req.tag
            self._outstanding[req.tag] = req
            self._enqueue(req.tlp, req)

    def _finish(self, req: Request, status: str) -> None:
        req.status = status
        req.done = True
        if req.tag is not None and self._outstanding.get(req.tag) is req:
            del self._outstanding[req.tag]
            self._free_tags.append(req.tag)
            self._free_tags.sort()
        self._assign_tags()

    # ------------------------------------------------------------------
    # Transmit side
    def _enqueue(self, tlp: Tlp, req: Request | None) -> None:
        self._tx.append((tlp.credit_cost(), tlp.encode(), req))

    @property
    def tx_pending(self) -> int:
        return len(self._tx)

    @staticmethod
    def _fits(cost: tuple[str, int, int], avail: dict | None) -> bool:
        if avail is None:
            return True
        cls, hdr, dat = cost
        hk, dk = _CLASS_KEYS[cls]
        h, d = avail.get(hk, _INFINITE[hk]), avail.get(dk, _INFINITE[dk])
        return (h == _INFINITE[hk] or h >= hdr) and (d == _INFINITE[dk] or d >= dat)

    def pop_tx(self, avail: dict | None = None) -> bytes | None:
        """The next TLP to send, or None.

        ``avail`` maps ``ph``, ``pd``, ``nph``, ``npd``, ``cplh`` and
        ``cpld`` to the credits the host's link shows available (all ones
        infinite; a missing key is infinite; None sends without gating).
        Each class keeps its order; a non-posted request or a completion
        never passes an earlier posted request, and a posted request may
        pass a blocked non-posted request or completion
        [cite: PCIe Base Spec, rev 3.0, §2.4.1].
        """
        blocked: set[str] = set()
        for i, (cost, raw, req) in enumerate(self._tx):
            cls = cost[0]
            if cls in blocked or (cls != POSTED and POSTED in blocked):
                blocked.add(cls)
                continue
            if self._fits(cost, avail):
                del self._tx[i]
                if req is not None and req.sent_at is None:
                    req.sent_at = self.now
                return raw
            blocked.add(cls)
        return None

    def pop_tx_beats(self, avail: dict | None = None) -> list[int] | None:
        """``pop_tx`` as 32-bit upper-stream beats."""
        raw = self.pop_tx(avail)
        return None if raw is None else to_beats(raw)

    # ------------------------------------------------------------------
    # Time
    def tick(self, cycles: int = 1) -> None:
        """Advance the host's clock; unanswered requests older than
        ``timeout`` cycles since sent fail with status ``timeout``."""
        self.now += cycles
        if self.timeout is None:
            return
        for req in list(self._outstanding.values()):
            if req.sent_at is not None and self.now - req.sent_at > self.timeout:
                self._finish(req, "timeout")
                self._fail(f"completion timeout: {req.kind} tag {req.tag}", req)
        self._advance()

    # ------------------------------------------------------------------
    # Receive side
    def receive_beats(self, beats: list[int]) -> tuple[str, int, int] | None:
        return self.receive(from_beats(beats))

    def receive(self, raw: bytes) -> tuple[str, int, int] | None:
        """Take one TLP from the link. Returns its credit cost (class,
        header, data) so a testbench running the host's link with credit
        release can release it at once; None for a malformed TLP."""
        try:
            t = Tlp.decode(raw)
        except TlpError as exc:
            self._fail(f"malformed TLP from the endpoint: {exc}")
            return None
        self.received.append(t)
        try:
            if t.is_completion:
                self._on_completion(t)
            elif t.kind == "MWr":
                self._on_mem_write(t)
            elif t.kind == "MRd":
                self._on_mem_read(t)
            elif t.kind in ("Msg", "MsgD"):
                self._on_message(t)
            else:
                self._fail(f"unexpected {t.kind} from the endpoint")
        finally:
            self._advance()
        return t.credit_cost()

    def _check_requester(self, t: Tlp, what: str) -> None:
        """An endpoint request must come from an enumerated function with
        Bus Master Enable set [cite: PCIe Base Spec, rev 3.0, §7.5.1.1]."""
        if not self.devices:
            return
        if t.requester_id not in self.devices:
            self._fail(f"{what} from unknown requester {t.requester_id:#06x}")
        elif not self._command.get(t.requester_id, 0) & CMD_BUS_MASTER:
            self._fail(f"{what} from {t.requester_id:#06x} with Bus Master Enable 0")

    def _on_mem_write(self, t: Tlp) -> None:
        if t.address in self._irq_addrs:
            self._check_requester(t, "interrupt write")
            value = int.from_bytes(t.data[:4], "little")
            if t.length_dw != 1 or t.first_be != 0xF or t.last_be != 0:
                self._fail(f"interrupt write to {t.address:#x}: Length {t.length_dw}, "
                           f"BE {t.last_be:04b}/{t.first_be:04b}; expected one whole DW")
            target = self._irq_targets.get((t.address, value))
            if target is None:
                self._fail(f"interrupt write to {t.address:#x} with data {value:#010x}: "
                           "matches no programmed vector")
                return
            kind, vector, dev = target
            if dev != t.requester_id:
                self._fail(f"interrupt from {t.requester_id:#06x}, programmed on {dev:#06x}")
            irq = Interrupt(kind, vector, t.address, value, t.requester_id)
            self.interrupts.append(irq)
            if self.on_interrupt is not None:
                self.on_interrupt(irq)
            return
        self._check_requester(t, "memory write")
        if t.length_dw * 4 > self.max_payload:
            self._fail(f"memory write of {t.length_dw * 4} bytes exceeds {self.max_payload}")
        self._check_4k(t)
        mask = self._be_mask(t)
        self.memory.write(t.address, t.data, mask)

    @staticmethod
    def _be_mask(t: Tlp) -> list[bool]:
        n = t.length_dw
        mask = [True] * (4 * n)
        for i in range(4):
            mask[i] = bool(t.first_be >> i & 1)
        if n > 1:
            for i in range(4):
                mask[4 * (n - 1) + i] = bool(t.last_be >> i & 1)
        return mask

    def _check_4k(self, t: Tlp) -> None:
        last = t.address + 4 * t.length_dw - 1
        if t.address >> 12 != last >> 12:
            self._fail(f"{t.kind} at {t.address:#x}, {t.length_dw} DW crosses 4 KiB "
                       "[cite: PCIe Base Spec, rev 3.0, §2.2.7]")

    def _on_mem_read(self, t: Tlp) -> None:
        """Answer an endpoint read from host memory, split on the host's
        RCB [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]."""
        self._check_requester(t, "memory read")
        self._check_4k(t)
        n = t.length_dw
        # The endpoint's own limits (core HAS, F2): tags 0 to 7 with
        # extended tags off, read requests of at most 128 bytes.
        if t.tag >= self.ep_tags:
            self._fail(f"memory read with tag {t.tag}: the endpoint uses tags 0 to "
                       f"{self.ep_tags - 1}")
        if 4 * n > self.ep_max_read_request:
            self._fail(f"memory read of {4 * n} bytes exceeds the endpoint's "
                       f"{self.ep_max_read_request}-byte maximum read request")
        fbe, lbe = t.first_be, t.last_be
        if n == 1 and lbe:
            self._fail(f"memory read of 1 DW with Last DW BE {lbe:04b}")
        if n > 1 and (fbe == 0 or lbe == 0):
            self._fail(f"memory read of {n} DW with a zero byte-enable field")
        # Byte count and lower address [cite: PCIe Base Spec, rev 3.0, §2.3.1.1].
        if n == 1:
            if fbe == 0:
                total, low = 1, 0
            else:
                low = (fbe & -fbe).bit_length() - 1
                high = fbe.bit_length() - 1
                total = high - low + 1
        else:
            low = (fbe & -fbe).bit_length() - 1
            high_last = lbe.bit_length() - 1
            total = 4 * n - low - (3 - high_last)
        first = t.address + low
        status = self.read_status(t.address, 4 * n) if self.read_status else CPL_SC
        ckw = dict(tc=t.tc, attr=t.attr & 0x3)
        if status != CPL_SC:
            c = completion(self.requester_id, t.requester_id, t.tag, status,
                           total & 0xFFF, first & 0x7F, **ckw)
            self._enqueue(c, None)
            return
        start = t.address
        end = t.address + 4 * n
        nxt = first
        while start < end:
            if self.cpl_split == "rcb":
                stop = min(end, (start // self.host_rcb + 1) * self.host_rcb)
            elif end - start <= self.max_payload:
                stop = end
            else:
                stop = (start + self.max_payload) // self.host_rcb * self.host_rcb
            data = self.memory.read(start, stop - start)
            remaining = first + total - nxt
            c = completion(self.requester_id, t.requester_id, t.tag, CPL_SC,
                           remaining & 0xFFF, nxt & 0x7F, data, **ckw)
            self._enqueue(c, None)
            start = stop
            nxt = stop

    def _on_message(self, t: Tlp) -> None:
        self.messages.append(Message(t.msg_code, t.routing, t.requester_id, t.data))

    # -- completions -------------------------------------------------------
    def _on_completion(self, c: Tlp) -> None:
        """Match and check a completion [cite: PCIe Base Spec, rev 3.0, §2.3.2]
        [cite: PCIe Base Spec, rev 3.0, §2.2.9]."""
        req = self._outstanding.get(c.tag)
        if c.requester_id != self.requester_id or req is None:
            self._fail(f"unexpected completion: requester {c.requester_id:#06x}, tag {c.tag}, "
                       f"status {c.status:03b}")
            return
        if req.expected_completer is not None and c.completer_id != req.expected_completer:
            self._fail(f"{req.kind} tag {c.tag}: completer ID {c.completer_id:#06x}, "
                       f"expected {req.expected_completer:#06x}", req)
        locked = c.type == 0b01011
        if locked != (req.tlp.kind == "MRdLk"):
            self._fail(f"{req.kind} tag {c.tag}: {c.kind} completion kind", req)
        if c.tc != req.tlp.tc or (c.attr & 3) != (req.tlp.attr & 3):
            self._fail(f"{req.kind} tag {c.tag}: TC/Attr {c.tc}/{c.attr & 3} not the "
                       f"request's {req.tlp.tc}/{req.tlp.attr & 3}", req)
        req.completions += 1
        status = STATUS_NAMES.get(c.status)
        if status is None:
            # A reserved status is handled as UR by the requester
            # [cite: PCIe Base Spec, rev 3.0, §2.3.2]; the endpoint sends only
            # SC and UR, so the host also flags it.
            self._fail(f"{req.kind} tag {c.tag}: reserved Completion Status {c.status:03b}", req)
            status = "UR"
        if req.kind == "mem_rd":
            self._mem_completion(req, c, status)
        else:
            self._single_completion(req, c, status)

    def _single_completion(self, req: Request, c: Tlp, status: str) -> None:
        if req.kind in ("cfg_rd", "cfg_wr"):
            # Byte Count 4 and Lower Address 0 for a configuration
            # completion [cite: PCIe Base Spec, rev 3.0, §2.2.9].
            if c.byte_count_value != 4 or c.lower_addr != 0:
                self._fail(f"{req.kind} tag {c.tag}: Byte Count {c.byte_count_value}, "
                           f"Lower Address {c.lower_addr}; expected 4 and 0", req)
            want_data = req.kind == "cfg_rd" and status == "SC"
            if c.has_data != want_data or (want_data and c.length_dw != 1):
                self._fail(f"{req.kind} tag {c.tag}: {c.kind} of {c.length_dw if c.has_data else 0}"
                           f" DW for status {status}", req)
        if c.ep:
            self._fail(f"{req.kind} tag {c.tag}: poisoned completion", req)
            self._finish(req, "poisoned")
            return
        if c.has_data:
            req.data = bytearray(c.data)
            req.completion_sizes.append(len(c.data))
            if req.kind == "cfg_rd":
                req.value = int.from_bytes(c.data[:4], "little")
        self._finish(req, status)

    def _mem_completion(self, req: Request, c: Tlp, status: str) -> None:
        tag = c.tag
        if status == "CRS":
            # Configuration Request Retry Status answers configuration
            # requests only [cite: PCIe Base Spec, rev 3.0, §2.3.1].
            self._fail(f"mem_rd tag {tag}: CRS completion to a memory read", req)
            self._finish(req, "CRS")
            return
        if status != "SC":
            # A UR or CA completion ends the read with no data; its Byte
            # Count is the bytes still owed [cite: PCIe Base Spec, rev 3.0, §2.3.1.1].
            if c.has_data:
                self._fail(f"mem_rd tag {tag}: {status} completion with data", req)
            if c.byte_count_value != req.remaining or c.lower_addr != req.next_byte & 0x7F:
                self._fail(f"mem_rd tag {tag}: {status} Byte Count {c.byte_count_value}, Lower "
                           f"Address {c.lower_addr:#x}; expected {req.remaining}, "
                           f"{req.next_byte & 0x7F:#x}", req)
            self._finish(req, status)
            return
        if not c.has_data:
            self._fail(f"mem_rd tag {tag}: successful completion without data", req)
            self._finish(req, "error")
            return
        if c.byte_count_value != req.remaining:
            self._fail(f"mem_rd tag {tag}: Byte Count {c.byte_count_value}, "
                       f"expected {req.remaining} remaining", req)
        if c.lower_addr != req.next_byte & 0x7F:
            self._fail(f"mem_rd tag {tag}: Lower Address {c.lower_addr:#x}, "
                       f"expected {req.next_byte & 0x7F:#x}", req)
        nbytes = 4 * c.length_dw
        if nbytes > self.max_payload:
            self._fail(f"mem_rd tag {tag}: completion of {nbytes} bytes exceeds the "
                       f"{self.max_payload}-byte maximum payload", req)
        start = req.next_byte & ~3
        stop = start + nbytes
        if stop > req.end_dw:
            self._fail(f"mem_rd tag {tag}: completion data ends at {stop:#x}, past the "
                       f"request's last DW ending {req.end_dw:#x}", req)
        covers = min(req.remaining, stop - req.next_byte)
        if covers < req.remaining and stop % self.rcb:
            self._fail(f"mem_rd tag {tag}: partial completion ends at {stop:#x}, not on a "
                       f"{self.rcb}-byte boundary", req)
        if c.ep:
            req.poisoned = True
            self._fail(f"mem_rd tag {tag}: poisoned completion", req)
        elif req.tlp.first_be:  # a zero-length read returns no bytes
            off = req.next_byte - start
            req.data += c.data[off:off + covers]
        req.completion_sizes.append(nbytes)
        req.remaining -= covers
        req.next_byte += covers
        if req.remaining <= 0:
            self._finish(req, "poisoned" if req.poisoned else "SC")

    # ------------------------------------------------------------------
    # Procedures
    def start(self, gen: Generator) -> Procedure:
        """Run a generator procedure; it advances as its requests finish."""
        proc = Procedure(gen)
        self._procs.append(proc)
        self._step(proc)
        return proc

    def _step(self, proc: Procedure) -> None:
        while not proc.done:
            if proc.waiting and not all(r.done for r in proc.waiting):
                return
            try:
                y = proc.gen.send(proc.yielded)
            except StopIteration as stop:
                proc.done, proc.result = True, stop.value
                return
            except BaseException as exc:
                proc.done, proc.error = True, exc
                if self.strict:
                    raise
                self.errors.append(f"[host @{self.now}] procedure failed: {exc}")
                return
            proc.waiting = [y] if isinstance(y, Request) else list(y)
            proc.yielded = y

    def _advance(self) -> None:
        for proc in list(self._procs):
            self._step(proc)
            if proc.done:
                self._procs.remove(proc)

    @property
    def idle(self) -> bool:
        """Nothing queued, outstanding or waiting for a tag, and no
        procedure running."""
        return not (self._tx or self._outstanding or self._tag_wait or self._procs)

    def outstanding(self) -> list[Request]:
        return list(self._outstanding.values()) + list(self._tag_wait)

    # -- procedure helpers -------------------------------------------------
    def _sc(self, req: Request, what: str) -> Request:
        if req.status != "SC":
            raise HostCheckError(f"{what}: status {req.status}")
        return req

    def _cfg_rd(self, bdf, reg):
        req = yield self.cfg_read(bdf, reg)
        return self._sc(req, f"config read {reg:#x}").value

    def _cfg_wr(self, bdf, reg, value, be=0xF):
        req = yield self.cfg_write(bdf, reg, value, be)
        self._sc(req, f"config write {reg:#x}")

    def _alloc(self, size: int) -> int:
        base = (self._mem32_next + size - 1) // size * size
        if base + size - 1 > self._mem32_limit:
            raise HostCheckError(f"no room for a {size:#x}-byte BAR in the memory window")
        self._mem32_next = base + size
        return base

    # -- enumeration -------------------------------------------------------
    def enumerate_device(self, bdf: tuple = (1, 0, 0), bus_master: bool = True,
                         memory: bool = True) -> Generator:
        """Procedure: enumerate one Type 0 function and return its
        DeviceInfo [cite: PCIe Base Spec, rev 3.0, §7.5.1]
        [cite: PCIe Base Spec, rev 3.0, §7.5.2].

        The first request is a configuration write (Command 0, decodes off),
        so the function captures its bus and device numbers before any
        completion it sends [cite: PCIe Base Spec, rev 3.0, §2.2.6.2].
        """
        dev = DeviceInfo(bdf=tuple(bdf))
        yield from self._cfg_wr(bdf, CFG_COMMAND, 0, 0b0011)
        ident = yield from self._cfg_rd(bdf, CFG_ID)
        dev.vendor_id, dev.device_id = ident & 0xFFFF, ident >> 16
        if dev.vendor_id == 0xFFFF:
            raise HostCheckError(f"no function at {bdf}: Vendor ID FFFFh")
        cls = yield from self._cfg_rd(bdf, CFG_CLASS)
        dev.revision, dev.class_code = cls & 0xFF, cls >> 8
        hdr = yield from self._cfg_rd(bdf, CFG_HEADER)
        dev.header_type = (hdr >> 16) & 0xFF
        if dev.header_type & 0x7F != 0:
            raise HostCheckError(f"header type {dev.header_type:#x}: not a Type 0 function")
        sub = yield from self._cfg_rd(bdf, CFG_SUBSYS)
        dev.subsys_vendor_id, dev.subsys_id = sub & 0xFFFF, sub >> 16
        yield from self._walk_caps(bdf, dev)
        yield from self._size_bars(bdf, dev)
        self.devices[dev.id] = dev
        cmd = (CMD_MEM if memory else 0) | (CMD_BUS_MASTER if bus_master else 0)
        yield from self._cfg_wr(bdf, CFG_COMMAND, cmd, 0b0011)
        return dev

    def _walk_caps(self, bdf, dev: DeviceInfo) -> Generator:
        """The capability list from the Capabilities Pointer, then the
        extended list from 0x100 [cite: PCIe Base Spec, rev 3.0, §7.5.1]
        [cite: PCIe Base Spec, rev 3.0, §7.9.1]."""
        status = (yield from self._cfg_rd(bdf, CFG_COMMAND)) >> 16
        if status & (STATUS_CAP_LIST >> 16):
            ptr = (yield from self._cfg_rd(bdf, CFG_CAP_PTR)) & 0xFC  # low 2 bits reserved
            seen: set[int] = set()
            while ptr:
                if ptr < 0x40 or ptr in seen or len(seen) >= 48:
                    raise HostCheckError(f"capability list: bad or looping pointer {ptr:#x}")
                seen.add(ptr)
                head = yield from self._cfg_rd(bdf, ptr)
                cid, nxt = head & 0xFF, (head >> 8) & 0xFC
                dev.caps.append((cid, ptr))
                if cid == CAP_PM:
                    dev.pm_offset = ptr
                elif cid == CAP_MSI:
                    dev.msi = MsiCap(ptr, bool(head >> 23 & 1), (head >> 17) & 0x7,
                                     bool(head >> 24 & 1))
                elif cid == CAP_MSIX:
                    tbl = yield from self._cfg_rd(bdf, ptr + 4)
                    pba = yield from self._cfg_rd(bdf, ptr + 8)
                    dev.msix = MsixCap(ptr, ((head >> 16) & 0x7FF) + 1, tbl & 7, tbl & ~7,
                                       pba & 7, pba & ~7)
                elif cid == CAP_PCIE:
                    dev.pcie_offset = ptr
                    dev.pcie_port_type = (head >> 20) & 0xF
                    devcap = yield from self._cfg_rd(bdf, ptr + 4)
                    dev.mps_supported = 128 << (devcap & 0x7)
                    # Max_Payload_Size: the smaller of the host's and the
                    # function's [cite: PCIe Base Spec, rev 3.0, §7.8.4].
                    mps = min(self.max_payload, dev.mps_supported)
                    ctl = yield from self._cfg_rd(bdf, ptr + 8)
                    ctl = (ctl & 0xFFFF & ~(0x7 << 5)) | (_encode_mps(mps) << 5)
                    yield from self._cfg_wr(bdf, ptr + 8, ctl, 0b0011)
                ptr = nxt
        off = 0x100
        seen = set()
        while True:
            if off in seen or off < 0x100:
                raise HostCheckError(f"extended capability list: bad or looping pointer {off:#x}")
            head = yield from self._cfg_rd(bdf, off)
            if head in (0, 0xFFFFFFFF):
                break
            seen.add(off)
            dev.ext_caps.append((head & 0xFFFF, (head >> 16) & 0xF, off))
            off = (head >> 20) & 0xFFC
            if not off:
                break

    def _size_bars(self, bdf, dev: DeviceInfo) -> Generator:
        """Size each BAR by writing all ones and reading it back, then assign
        a naturally aligned base [cite: PCIe Base Spec, rev 3.0, §7.5.2.1]."""
        i = 0
        while i < 6:
            reg = CFG_BAR0 + 4 * i
            yield from self._cfg_wr(bdf, reg, 0xFFFFFFFF)
            low = yield from self._cfg_rd(bdf, reg)
            if low == 0:
                i += 1
                continue
            if low & 1:  # I/O BAR: recorded, not assigned (no I/O window)
                dev.bars[i] = Bar(i, (~(low & ~0x3) & 0xFFFF) + 1, io=True)
                yield from self._cfg_wr(bdf, reg, 0)
                i += 1
                continue
            mtype = (low >> 1) & 0x3
            pref = bool(low >> 3 & 1)
            if mtype == 0b10:
                if i == 5:
                    raise HostCheckError("BAR5 claims a 64-bit decoder")
                yield from self._cfg_wr(bdf, reg + 4, 0xFFFFFFFF)
                high = yield from self._cfg_rd(bdf, reg + 4)
                mask = (high << 32) | (low & ~0xF)
                size = ((~mask) & 0xFFFF_FFFF_FFFF_FFFF) + 1
            elif mtype == 0b00:
                size = ((~(low & ~0xF)) & 0xFFFFFFFF) + 1
            else:
                raise HostCheckError(f"BAR{i}: reserved memory type {mtype:02b}")
            if size & (size - 1) or size < 16:
                raise HostCheckError(f"BAR{i}: size {size:#x} is not a power of two >= 16")
            base = self._alloc(size)
            bar = Bar(i, size, is_64=mtype == 0b10, prefetchable=pref, base=base)
            dev.bars[i] = bar
            yield from self._cfg_wr(bdf, reg, base & 0xFFFFFFFF)
            got = yield from self._cfg_rd(bdf, reg)
            if got & ~0xF != base & 0xFFFFFFF0:
                raise HostCheckError(f"BAR{i}: wrote base {base:#x}, read back {got:#x}")
            if bar.is_64:
                yield from self._cfg_wr(bdf, reg + 4, base >> 32)
                i += 2
            else:
                i += 1
        # Expansion ROM: bit 0 is the enable, the address bits 31:11
        # [cite: PCIe Base Spec, rev 3.0, §7.5.2.4].
        yield from self._cfg_wr(bdf, CFG_ROM, 0xFFFFF800)
        rom = yield from self._cfg_rd(bdf, CFG_ROM)
        dev.rom_size = ((~(rom & 0xFFFFF800)) & 0xFFFFFFFF) + 1 if rom & 0xFFFFF800 else 0
        yield from self._cfg_wr(bdf, CFG_ROM, 0)

    # -- interrupts ----------------------------------------------------------
    def setup_msi(self, dev: DeviceInfo, address: int, data: int) -> Generator:
        """Procedure: program MSI (address, data) and enable it, MSI-X
        disabled first [cite: PCIe Base Spec, rev 3.0, §6.1.4]
        [cite: PCIe Base Spec, rev 3.0, §7.7]."""
        msi = dev.msi
        if msi is None:
            raise HostCheckError("the function has no MSI capability")
        if address & 3 or (not msi.is_64 and address >> 32):
            raise ValueError(f"MSI address {address:#x} not usable")
        if dev.msix is not None:
            yield from self._cfg_wr(dev.bdf, dev.msix.offset, 0, 0b1000)
        yield from self._cfg_wr(dev.bdf, msi.offset + 4, address & 0xFFFFFFFC)
        if msi.is_64:
            yield from self._cfg_wr(dev.bdf, msi.offset + 8, address >> 32)
        yield from self._cfg_wr(dev.bdf, msi.data_offset, data & 0xFFFF, 0b0011)
        # MSI Enable is bit 16 of the first DW; Multiple Message Enable 0.
        yield from self._cfg_wr(dev.bdf, msi.offset, 1 << 16, 0b0100)
        self._forget_irqs(dev.id)
        self._add_irq(address, data & 0xFFFF, "msi", 0, dev.id)

    def setup_msix(self, dev: DeviceInfo, vectors: dict, unmask: bool = True) -> Generator:
        """Procedure: enable MSI-X under Function Mask, write the table
        entries ``{vector: (address, data)}`` through the table's BAR,
        unmask them (if ``unmask``), then clear Function Mask
        [cite: PCIe Base Spec, rev 3.0, §6.1.4] [cite: PCIe Base Spec, rev 3.0, §7.7]."""
        mx = dev.msix
        if mx is None:
            raise HostCheckError("the function has no MSI-X capability")
        if dev.msi is not None:  # software never enables both
            yield from self._cfg_wr(dev.bdf, dev.msi.offset, 0, 0b0100)
        yield from self._cfg_wr(dev.bdf, mx.offset, 0xC000_0000, 0b1000)  # Enable + Function Mask
        self._forget_irqs(dev.id)
        for vec, (address, data) in sorted(vectors.items()):
            if not 0 <= vec < mx.table_size:
                raise ValueError(f"vector {vec} outside the table ({mx.table_size})")
            if address & 3:
                raise ValueError(f"MSI-X address {address:#x} not DW aligned")
            entry = dev.bar_addr(mx.table_bir, mx.table_offset + 16 * vec)
            yield self.mem_write32(entry, address & 0xFFFFFFFF)
            yield self.mem_write32(entry + 4, address >> 32)
            yield self.mem_write32(entry + 8, data)
            yield self.mem_write32(entry + 12, 0 if unmask else 1)
            self._add_irq(address, data & 0xFFFFFFFF, "msix", vec, dev.id)
        yield from self._cfg_wr(dev.bdf, mx.offset, 0x8000_0000, 0b1000)  # Function Mask off

    def msix_mask(self, dev: DeviceInfo, vector: int, masked: bool) -> Request:
        """Set or clear one vector's Mask bit (a posted write)."""
        mx = dev.msix
        entry = dev.bar_addr(mx.table_bir, mx.table_offset + 16 * vector)
        return self.mem_write32(entry + 12, 1 if masked else 0)

    def msix_function_mask(self, dev: DeviceInfo, masked: bool) -> Request:
        """Set or clear Function Mask, MSI-X left enabled."""
        return self.cfg_write(dev.bdf, dev.msix.offset, 0xC000_0000 if masked else 0x8000_0000,
                              0b1000)

    def read_msix_pba(self, dev: DeviceInfo) -> Generator:
        """Procedure: read the pending-bit array; returns an int, bit n
        vector n's pending bit."""
        mx = dev.msix
        nbytes = ((mx.table_size + 63) // 64) * 8
        req = yield self.mem_read(dev.bar_addr(mx.pba_bir, mx.pba_offset), nbytes)
        self._sc(req, "PBA read")
        return int.from_bytes(bytes(req.data), "little") & ((1 << mx.table_size) - 1)

    def _add_irq(self, address: int, data: int, kind: str, vector: int, dev: int) -> None:
        self._irq_targets[(address, data)] = (kind, vector, dev)
        self._irq_addrs[address] = dev

    def _forget_irqs(self, dev: int) -> None:
        for key, (_, _, d) in list(self._irq_targets.items()):
            if d == dev:
                del self._irq_targets[key]
        for addr, d in list(self._irq_addrs.items()):
            if d == dev:
                del self._irq_addrs[addr]

    # -- convenience ---------------------------------------------------------
    def read(self, addr: int, nbytes: int, max_read_request: int = 512) -> Generator:
        """Procedure: read ``nbytes`` from ``addr`` in requests of at most
        ``max_read_request`` bytes that never cross 4 KiB, all outstanding
        at once (tags permitting); returns the bytes."""
        if nbytes == 0:
            req = yield self.mem_read(addr, 0)
            self._sc(req, f"memory read {addr:#x}")
            return b""
        reqs = []
        pos = 0
        while pos < nbytes:
            a = addr + pos
            n = min(nbytes - pos, max_read_request, ((a | 0xFFF) + 1) - a)
            reqs.append(self.mem_read(a, n))
            pos += n
        done = yield reqs
        out = bytearray()
        for r in done:
            self._sc(r, f"memory read {r.first_byte:#x}")
            out += r.data
        return bytes(out)
