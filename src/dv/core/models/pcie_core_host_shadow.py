"""Passive shadow mode of the independent host model.

In the root-port e2e the host side is the root-port RTL, not this model:
the shadow sends nothing. The testbench shows it what happens on the
root port's ``link`` upper TLP stream, in both directions, and what the
root port reports to its sequencer, and the shadow checks both sides:

* the **root port**: each request it sends is well formed, uses a free tag,
  is the one the next sequencer command asks for and is legal for its
  Type 1 header (routing); each completion it sends answers an endpoint
  read as its host memory and routing rules predict; each result, MSI
  capture and error record it reports equals the shadow's prediction;
* the **endpoint**: each completion and request it sends is checked by
  the host model's own rules (completer IDs, Byte Count, Lower Address,
  splits, tags, Bus Master Enable, interrupt writes), plus the shadow's
  predictions: BAR read-backs, identity registers, and memory read data
  where it saw the bytes written.

The calls are ``observe_command``, ``observe_tx``, ``observe_rx``,
``observe_result``, ``observe_msi``, ``observe_dl_up``, ``tick`` and
``finish``. Every finding goes through the host model's ``_fail``: it
raises ``HostCheckError`` when ``strict`` (the default), or is recorded
in ``errors``; each message names the side it blames (``rc``,
``endpoint``).

Written from the PCIe Base Spec rev 3.0 (the author's reading, cited by
section, never reproduced), the root-port HAS and its tl-seq interface
page (``wiki/src/arch/rc/``) and the host model; it shares no code with
the root-port RTL, its sub-IPs or any architecture model. Contract:
``wiki/src/dv/core/HOST.md``, shadow mode.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace

from pcie_core_host_model import (
    CFG_BAR0,
    CFG_CAP_PTR,
    CFG_COMMAND,
    CAP_MSI,
    Bar,
    DeviceInfo,
    HostModel,
    MsiCap,
    Request,
)
from pcie_core_host_tlp import (
    CPL_SC,
    CPL_UR,
    MSG_ERR_COR,
    MSG_ERR_FATAL,
    MSG_ERR_NONFATAL,
    NON_POSTED,
    STATUS_NAMES,
    Tlp,
    TlpError,
    bdf_id,
    from_beats,
)

# Sequencer command operations (rc tl-seq page, commands).
CFG_RD, CFG_WR, MEM_RD, MEM_WR, HDR_RD, HDR_WR, HOST_RD, HOST_WR, ERR_RD, ERR_CLR = range(10)
OP_NAMES = ["CFG_RD", "CFG_WR", "MEM_RD", "MEM_WR", "HDR_RD", "HDR_WR", "HOST_RD",
            "HOST_WR", "ERR_RD", "ERR_CLR"]
_ENDPOINT_OPS = (CFG_RD, CFG_WR, MEM_RD, MEM_WR)

# Result status (rc tl-seq page, results).
RESULT_NAMES = ["OK", "UR", "CA", "CRS", "TIMEOUT", "LOCAL_UR", "LINK_DOWN", "BAD"]
OK, UR, CA, CRS, TIMEOUT, LOCAL_UR, LINK_DOWN, BAD = range(8)
_FROM_HOST = {"SC": {OK}, "UR": {UR}, "CA": {CA}, "poisoned": {BAD}, "error": {BAD},
              "timeout": {TIMEOUT}, "link_down": {LINK_DOWN}}

# Error record bits (rc tl-seq page, error record).
ERR_UR, ERR_CA, ERR_CRS, ERR_TIMEOUT, ERR_UNEXPECTED, ERR_POISONED, ERR_MALFORMED = range(7)
ERR_MSG_COR, ERR_MSG_NONFATAL, ERR_MSG_FATAL, ERR_MSG_OTHER, ERR_INBOUND_UR = range(7, 12)
ERR_MSI_DROP = 12
_ERR_BITS = 13
_ERR_MSG = {MSG_ERR_COR: ERR_MSG_COR, MSG_ERR_NONFATAL: ERR_MSG_NONFATAL,
            MSG_ERR_FATAL: ERR_MSG_FATAL}

# The root port's Type 1 header: writable bits per DW index (rc HAS).
_HDR_RW = {1: 0x0000_0006, 3: 0x0000_00FF, 6: 0x00FF_FFFF, 8: 0xFFF0_FFF0}
_CLASS_BRIDGE = 0x06_04_00
_BAR_REGS = range(CFG_BAR0, CFG_BAR0 + 24, 4)
_ID_REGS = (0x00, 0x08, 0x2C)  # IDs, class and revision, subsystem IDs: read-only


_FIRST_CONTIGUOUS = (0b1111, 0b1110, 0b1100, 0b1000)  # enabled up to byte 3
_LAST_CONTIGUOUS = (0b0001, 0b0011, 0b0111, 0b1111)  # enabled from byte 0


def be_problem(length: int, address: int, first_be: int, last_be: int) -> str | None:
    """Why a request's byte enables are illegal, or None
    [cite: PCIe Base Spec, rev 3.0, §2.2.5]: Last DW BE 0 for 1 DW; both
    fields non-zero for more; and, for 3 DW or more and for a 2-DW request
    that is not QW aligned, only bytes contiguous across the request
    enabled (First DW BE enabled up to byte 3, Last DW BE from byte 0)."""
    if length == 1:
        return f"1 DW with Last DW BE {last_be:04b}" if last_be else None
    if not first_be or not last_be:
        return f"{length} DW with a zero byte-enable field {last_be:04b}/{first_be:04b}"
    if (length >= 3 or address & 4) and (first_be not in _FIRST_CONTIGUOUS
                                         or last_be not in _LAST_CONTIGUOUS):
        return f"{length} DW with non-contiguous byte enables {last_be:04b}/{first_be:04b}"
    return None


@dataclass
class _Event:
    """An error-record event the root port records some time in [lo, hi]."""

    bit: int
    lo: int
    hi: int
    sure: bool = True  # False: the root port may or may not record it


@dataclass
class _Cmd:
    """One sequencer command and what the shadow predicts for it."""

    op: int
    addr: int
    length: int
    first_be: int
    last_be: int
    data: int
    at: int
    idx: int = 0  # command order
    expect: list | None = None  # fixed beats [(status set, data, mask)]
    local_ur_ok: bool = False  # byte enables the rc_tl MAS may refuse
    tlp: Tlp | None = None  # the request it should send
    req: Request | None = None  # once sent (non-posted)
    sent: bool = False
    down: bool = False  # abandoned by a link down before it was sent
    beats: int = 0
    host_bytes: dict = field(default_factory=dict)  # HOST_RD: address -> entry


@dataclass
class _EpRead:
    """An endpoint non-posted request the root port must answer."""

    tlp: Tlp
    status: int  # CPL_SC (served) or CPL_UR
    req: Request  # tracks Byte Count and Lower Address of the answer
    seq: int
    at: int
    snap: dict = field(default_factory=dict)  # address -> host memory entry


@dataclass
class _Mem:
    """One host memory byte as the shadow last saw it written."""

    value: int
    src: str  # "host" (sequencer command) or "dma" (endpoint write)
    seq: int  # receive order of a DMA write
    at: int


class HostShadow(HostModel):
    """The host model as a passive shadow of the root port.

    Parameters (the root port's, from its HAS): ``vendor_id``,
    ``device_id``, ``revision_id`` (Type 1 header), ``host_mem_base`` and
    ``host_mem_addr_w`` (host memory), ``msi_addr`` (MSI window). Then:

      num_tags: the root port's tags are 0 to num_tags - 1.
      latency: cycles the root port may take to act on a TLP its link
        delivered (results, the error record, MSI captures, host memory);
        timing-dependent checks allow this much either way.
      timeout_min, timeout_max: the root port's completion timeout window
        in cycles from the request's transmission; a TIMEOUT result before
        ``timeout_min`` is an error, and no result by ``timeout_max`` (plus
        ``latency``) is one when ``timeout_max`` is set.
      msi_depth: captures the root port holds at least (4 by its HAS); an
        MSI arriving with that many untaken may be dropped.
      msi_depth_exact: the root port holds exactly ``msi_depth`` captures
        (a bench that knows the depth): an MSI arriving with that many
        untaken, after any take of the same cycle, is surely dropped and
        sets bit 12; one arriving with fewer is surely captured.
      zero_be_refused: the command ops whose First DW BE 0 the root port
        surely refuses with ``LOCAL_UR`` (a bench that knows its rule);
        without it, either outcome is accepted.
      bar_data: predict an endpoint memory read from the bytes the root
        port wrote there (the endpoint's BAR is memory-like).
      strict: as the host model.
    """

    def __init__(self, vendor_id: int = 0, device_id: int = 0, revision_id: int = 0,
                 host_mem_base: int = 0, host_mem_addr_w: int = 12,
                 msi_addr: int = 0xFEE0_0000, num_tags: int = 32, latency: int = 32,
                 timeout_min: int = 0, timeout_max: int | None = None, msi_depth: int = 4,
                 bar_data: bool = True, strict: bool = True, max_payload: int = 128,
                 msi_depth_exact: bool = False, zero_be_refused: tuple = ()):
        super().__init__(requester_id=0, num_tags=num_tags, max_payload=max_payload,
                         rcb=64, timeout=None, strict=strict)
        self.vendor_id, self.device_id, self.revision_id = vendor_id, device_id, revision_id
        self.host_mem_base = host_mem_base
        self.host_mem_size = 1 << host_mem_addr_w
        self.msi_addr = msi_addr
        self.latency = latency
        self.timeout_min, self.timeout_max = timeout_min, timeout_max
        self.msi_depth = msi_depth
        self.msi_depth_exact = msi_depth_exact
        self.zero_be_refused = tuple(zero_be_refused)
        self._msi_new: list[list] = []  # MSIs that arrived this cycle (exact depth)
        self.bar_data = bar_data
        self.dl_up = False
        self.side = "rc"
        self.stats = {k: 0 for k in ("commands", "tx_requests", "tx_completions", "rx_completions",
                                     "rx_requests", "results", "msi", "err_reads")}
        self._hdr = {i: 0 for i in _HDR_RW}
        self._cmds: deque[_Cmd] = deque()  # awaiting their results, in command order
        self._unsent: list[_Cmd] = []  # endpoint commands not yet seen on the link
        self._ep_np: dict[tuple[int, int], _EpRead] = {}
        self._host_mem: dict[int, _Mem] = {}
        self._bar_mem: dict[int, int] = {}  # endpoint memory bytes the root port wrote
        self._events: list[_Event] = []
        self._clears: dict[int, list] = {}  # bit -> [[command index, time, result time]]
        self._ncmd = 0
        self._msi_q: deque[list] = deque()  # [offset, data, may drop]
        self._msi_fence = -1  # receive order of the last MSI the sequencer took
        self._rx_seq = 0
        self._late: set[int] = set()  # tags the root port timed out
        self._deferred: list[tuple] = []  # (command, request, beat, data): data still arriving
        self._down_at: int | None = None
        self._reset_endpoint()

    # ------------------------------------------------------------------
    # Errors and small helpers
    def _fail(self, msg: str, req: Request | None = None) -> None:
        super()._fail(f"{self.side}: {msg}", req)

    def _blame(self, side: str, msg: str, req: Request | None = None) -> None:
        old, self.side = self.side, side
        try:
            self._fail(msg, req)
        finally:
            self.side = old

    def _reset_endpoint(self) -> None:
        """What the shadow learnt about the endpoint, which a link down
        resets."""
        self._ident: dict[tuple[int, int], int] = {}
        self._bars: dict[tuple[int, int], dict] = {}
        self._caps: dict[int, set] = {}
        self._msi_regs: dict[int, dict[int, int]] = {}
        self._captured: set[int] = set()  # functions that took a configuration write

    def _event(self, bit: int, lo: int | None = None, hi: int | None = None,
               sure: bool = True) -> None:
        lo = self.now if lo is None else lo
        self._events.append(_Event(bit, lo, self.now + self.latency if hi is None else hi, sure))

    # ------------------------------------------------------------------
    # The root port's Type 1 header, as the sequencer wrote it
    def header(self, index: int) -> int:
        """The DW the root port's ``HDR_RD`` of ``index`` must return."""
        ro = {0: self.vendor_id | self.device_id << 16,
              2: self.revision_id | _CLASS_BRIDGE << 8, 3: 0x01 << 16}.get(index, 0)
        return ro | (self._hdr.get(index, 0) & _HDR_RW.get(index, 0))

    @property
    def mem_space(self) -> bool:
        return bool(self._hdr[1] & 2)

    @property
    def bus_master(self) -> bool:
        return bool(self._hdr[1] & 4)

    @property
    def rc_id(self) -> int:
        return bdf_id(self._hdr[6] & 0xFF, 0, 0)

    @property
    def secondary(self) -> int:
        return self._hdr[6] >> 8 & 0xFF

    def window(self) -> tuple[int, int] | None:
        """The memory window as [first, last] bytes, or None while empty
        [cite: PCIe Base Spec, rev 3.0, §7.5.3]."""
        base = (self._hdr[8] >> 4 & 0xFFF) << 20
        limit = (self._hdr[8] >> 20 & 0xFFF) << 20 | 0xFFFFF
        return None if base > limit else (base, limit)

    def _in_window(self, lo: int, hi: int) -> str:
        """'all', 'some' or 'none' of the bytes [lo, hi) in the window."""
        w = self.window()
        if w is None or hi <= w[0] or lo > w[1]:
            return "none"
        return "all" if lo >= w[0] and hi - 1 <= w[1] else "some"

    # ------------------------------------------------------------------
    # Sequencer commands
    def observe_command(self, op: int, addr: int = 0, length: int = 1, first_be: int = 0xF,
                        last_be: int = 0, data: int = 0) -> None:
        """A command ``rc_tl`` took from ``rc_seq`` (``cmd_valid`` and
        ``cmd_ready`` both 1), with its fields."""
        self.stats["commands"] += 1
        self._ncmd += 1
        c = _Cmd(op, addr & 0xFFFFFFFF, length, first_be & 0xF, last_be & 0xF,
                 data & 0xFFFFFFFF, self.now, self._ncmd)
        self._cmds.append(c)
        if op in _ENDPOINT_OPS and not self.dl_up:
            c.expect = [({LINK_DOWN}, 0, ~0)]
            return
        if not self._fields_ok(c):
            c.expect = [({LOCAL_UR}, 0, ~0)]
            return
        if op in _ENDPOINT_OPS:
            self._endpoint_command(c)
        elif op == HDR_RD:
            c.expect = [({OK}, self.header(addr >> 2 & 0x3FF), ~0)]
        elif op == HDR_WR:
            idx = addr >> 2 & 0x3FF
            if idx in _HDR_RW:
                mask = sum(0xFF << 8 * i for i in range(4) if first_be >> i & 1)
                self._hdr[idx] = (self._hdr[idx] & ~mask) | (data & mask)
            c.expect = [({OK}, 0, ~0)]
        elif op == HOST_RD:
            c.host_bytes = {a: self._host_mem.get(a) for a in self._host_span(addr)}
        elif op == HOST_WR:
            for i, a in enumerate(self._host_span(addr)):
                if first_be >> i & 1:
                    self._host_mem[a] = _Mem(data >> 8 * i & 0xFF, "host", self._rx_seq, self.now)
            c.expect = [({OK}, 0, ~0)]
        elif op == ERR_CLR:
            for b in range(_ERR_BITS):
                if data >> b & 1:
                    self._clears.setdefault(b, []).append([c.idx, self.now, None])
            c.expect = [({OK}, 0, ~0)]

    def _host_span(self, offset: int) -> range:
        a = self.host_mem_base + offset
        return range(a, a + 4)

    def _fields_ok(self, c: _Cmd) -> bool:
        """The field checks of the tl-seq page (commands)."""
        if c.op > ERR_CLR or not (1 <= c.length <= 32 if c.op == MEM_RD else c.length == 1):
            return False
        a = c.addr
        if c.op in (CFG_RD, CFG_WR):
            return not a & 0xF000_0003
        if c.op in (MEM_RD, MEM_WR):
            return not a & 3 and (c.op == MEM_WR or a >> 12 == (a + 4 * c.length - 1) >> 12)
        if c.op in (HDR_RD, HDR_WR):
            return not a & 0xFFFF_F003
        if c.op in (HOST_RD, HOST_WR):
            return not a & 3 and a < self.host_mem_size
        return True

    def _endpoint_command(self, c: _Cmd) -> None:
        """Route an endpoint command as the root port must (rc HAS,
        outbound routing) and build the request it should send."""
        n, fbe, lbe = c.length, c.first_be, c.last_be
        if not fbe and c.op in self.zero_be_refused:
            c.expect = [({LOCAL_UR}, 0, ~0)]
            return
        c.local_ur_ok = not fbe or be_problem(n, c.addr, fbe, lbe) is not None
        rid = self.rc_id
        if c.op in (CFG_RD, CFG_WR):
            bus, dev, fn, reg = c.addr >> 20 & 0xFF, c.addr >> 15 & 0x1F, c.addr >> 12 & 7, c.addr & 0xFFC
            if bus != self.secondary or dev != 0:
                c.expect = [({LOCAL_UR}, 0, ~0)]
                return
            data = c.data.to_bytes(4, "little") if c.op == CFG_WR else b""
            c.tlp = Tlp(fmt=0b010 if data else 0b000, type=0b00100, length=1,
                        requester_id=rid, first_be=fbe, last_be=lbe, cfg_bus=bus, cfg_dev=dev,
                        cfg_fn=fn, cfg_reg=reg, data=data)
        else:
            if not self.mem_space or self._in_window(c.addr, c.addr + 4 * n) != "all":
                c.expect = [({LOCAL_UR}, 0, ~0)]
                return
            data = c.data.to_bytes(4, "little") if c.op == MEM_WR else b""
            c.tlp = Tlp(fmt=0b010 if data else 0b000, type=0, length=n, requester_id=rid,
                        first_be=fbe, last_be=lbe, address=c.addr, data=data)
        self._unsent.append(c)

    # ------------------------------------------------------------------
    # Transmit: what the root port sent
    def observe_tx_beats(self, beats: list[int]) -> None:
        self.observe_tx(from_beats(beats))

    def observe_tx(self, raw: bytes) -> None:
        """A TLP the root port's ``link`` took on its transmit stream."""
        self.side = "rc"
        try:
            t = Tlp.decode(raw)
        except TlpError as exc:
            self._fail(f"malformed TLP sent: {exc}")
            return
        if t.td or t.ep:
            self._fail(f"{t.kind} sent with TD {t.td}, EP {t.ep}: the root port has no ECRC "
                       "and never poisons")
        if t.is_completion:
            self._tx_completion(t)
        elif t.kind in ("CfgRd0", "CfgWr0", "MRd", "MWr"):
            self._tx_request(t)
        else:
            self._fail(f"{t.kind} sent: the root port sends Type 0 configuration and memory "
                       "requests and completions only")

    def _well_formed(self, t: Tlp) -> None:
        """Header rules of a request [cite: PCIe Base Spec, rev 3.0, §2.2.5]
        [cite: PCIe Base Spec, rev 3.0, §2.2.7]."""
        n = t.length_dw
        problem = be_problem(n, t.address, t.first_be, t.last_be)
        if problem is not None:
            self._fail(f"{t.kind} at {t.address:#x}: {problem}")
        if t.kind.startswith("Cfg") and (t.tc or t.attr or n != 1):
            self._fail(f"{t.kind} with TC {t.tc}, Attr {t.attr}, Length {n}")
        if t.kind.startswith("M") and t.four_dw:
            self._fail(f"{t.kind} at {t.address:#x} with a 4-DW header: the root port uses "
                       "32-bit addresses")
        if t.kind == "MWr" and 4 * n > self.max_payload:
            self._fail(f"MWr of {4 * n} bytes exceeds the {self.max_payload}-byte payload")

    def _tx_request(self, t: Tlp) -> None:
        self.stats["tx_requests"] += 1
        self._well_formed(t)
        posted = t.kind == "MWr"
        cmd = next((c for c in self._unsent if self._matches(c.tlp, t)), None)
        if cmd is None:
            exp = self._unsent[0].tlp if self._unsent else None
            self._fail(f"{t.kind} sent (tag {t.tag}) that no pending command asks for; next "
                       f"expected: {self._describe(exp)}, sent: {self._describe(t)}")
            return
        # Commands still unsent ahead of it: one the rc_tl MAS may refuse
        # is passed freely, and a posted write may pass a non-posted
        # request, never the reverse [cite: PCIe Base Spec, rev 3.0, §2.4.1].
        for c in self._unsent[:self._unsent.index(cmd)]:
            if c.local_ur_ok or (posted and c.tlp.kind != "MWr"):
                continue
            if c.tlp.kind == "MWr":
                self._fail(f"{t.kind} tag {t.tag} passed an earlier posted write "
                           "[cite: PCIe Base Spec, rev 3.0, §2.4.1]")
            else:
                self._fail(f"{t.kind} sent ahead of an earlier command's {c.tlp.kind}: "
                           "requests go out in command order")
            break
        self._unsent.remove(cmd)
        cmd.sent = True
        if posted:
            cmd.expect = [({OK}, 0, ~0)]
            self._note_bar_write(t)
            return
        if not 0 <= t.tag < self.num_tags:
            self._fail(f"{t.kind} with tag {t.tag}: the root port's tags are 0 to "
                       f"{self.num_tags - 1}")
        if t.tag in self._outstanding:
            self._fail(f"{t.kind} with tag {t.tag}, still outstanding for "
                       f"{self._outstanding[t.tag].kind}")
        self._late.discard(t.tag)
        req = self._track(t)
        cmd.req = req
        self._outstanding[t.tag] = req

    @staticmethod
    def _matches(want: Tlp, t: Tlp) -> bool:
        if want.kind != t.kind or want.requester_id != t.requester_id:
            return False
        if (want.length_dw, want.first_be, want.last_be) != (t.length_dw, t.first_be, t.last_be):
            return False
        if t.kind.startswith("Cfg"):
            where = (want.cfg_bus, want.cfg_dev, want.cfg_fn, want.cfg_reg)
            if where != (t.cfg_bus, t.cfg_dev, t.cfg_fn, t.cfg_reg):
                return False
        elif want.address != t.address:
            return False
        if t.has_data:
            mask = [bool(t.first_be >> i & 1) for i in range(4)]
            return all(not m or a == b for m, a, b in zip(mask, want.data, t.data))
        return True

    @staticmethod
    def _describe(t: Tlp | None) -> str:
        if t is None:
            return "none"
        where = (f"{t.cfg_bus}:{t.cfg_dev}.{t.cfg_fn} reg {t.cfg_reg:#x}"
                 if t.kind.startswith("Cfg") else f"{t.address:#x}")
        return (f"{t.kind} {where} RID {t.requester_id:#06x} Length {t.length_dw} "
                f"BE {t.last_be:04b}/{t.first_be:04b} data {t.data.hex() or '-'}")

    def _track(self, t: Tlp) -> Request:
        """A host-model request for a TLP someone else sent, so the host
        model's completion checks judge its completions."""
        bdf = (t.cfg_bus, t.cfg_dev, t.cfg_fn)
        if t.kind.startswith("Cfg"):
            # A function captures its bus and device numbers from each
            # Type 0 configuration write, before it completes it
            # [cite: PCIe Base Spec, rev 3.0, §2.2.6.2]; before the first,
            # its Completer ID carries bus and device 0 (core TL_CFG,
            # register state), which a read-first enumeration sees.
            ident = bdf_id(*bdf)
            captured = t.has_data or ident in self._captured
            req = Request("cfg_wr" if t.has_data else "cfg_rd", t,
                          expected_completer=ident if captured else None)
            if not captured:
                req.pre_capture = {ident, bdf_id(0, 0, t.cfg_fn)}
            if t.has_data:
                self._captured.add(ident)
            if t.has_data and t.cfg_reg == CFG_COMMAND:
                self._note_command(bdf_id(*bdf), int.from_bytes(t.data, "little"), t.first_be)
            self._learn_write(t)
            req.predict = self._predict_cfg(t)
        else:
            req = Request("mem_rd", t, expected_completer=self._owner(t.address))
            req.first_byte, req.total = self._read_span(t)
            req.remaining, req.next_byte = req.total, req.first_byte
            req.end_dw = t.address + 4 * t.length_dw
            req.predict = {a: self._bar_mem[a] for a in range(req.first_byte, req.first_byte
                                                               + req.total) if a in self._bar_mem}
        req.tag, req.sent_at = t.tag, self.now
        req.bad = req.unsure = False
        return req

    def _note_bar_write(self, t: Tlp) -> None:
        if not self.bar_data:
            return
        for i in range(4):
            if t.first_be >> i & 1:
                self._bar_mem[t.address + i] = t.data[i]

    # -- passive enumeration ---------------------------------------------------
    def _learn_write(self, t: Tlp) -> None:
        """Follow the configuration writes the root port sends: BARs and
        MSI registers [cite: PCIe Base Spec, rev 3.0, §7.5.2.1]
        [cite: PCIe Base Spec, rev 3.0, §6.1.4]."""
        if not t.has_data:
            return
        ident, reg = bdf_id(t.cfg_bus, t.cfg_dev, t.cfg_fn), t.cfg_reg
        value = int.from_bytes(t.data, "little")
        mask = sum(0xFF << 8 * i for i in range(4) if t.first_be >> i & 1)
        if reg in _BAR_REGS:
            bar = self._bars.setdefault((ident, reg), {"mask": None, "flags": 0, "value": None})
            old = bar["value"] if bar["value"] is not None else 0
            bar["value"] = (old & ~mask) | (value & mask)
            bar["sizing"] = bar["value"] == 0xFFFFFFFF
            self._place_bars(ident)
        dev = self.devices.get(ident)
        if dev is not None and dev.msi is not None and dev.msi.offset <= reg <= dev.msi.data_offset:
            regs = self._msi_regs.setdefault(ident, {})
            regs[reg] = (regs.get(reg, 0) & ~mask) | (value & mask)
            self._msi_program(dev)

    def _msi_program(self, dev: DeviceInfo) -> None:
        regs, msi = self._msi_regs[dev.id], dev.msi
        self._forget_irqs(dev.id)
        if regs.get(msi.offset, 0) >> 16 & 1:  # MSI Enable
            address = regs.get(msi.offset + 4, 0) & ~3
            if msi.is_64:
                address |= regs.get(msi.offset + 8, 0) << 32
            self._add_irq(address, regs.get(msi.data_offset, 0) & 0xFFFF, "msi", 0, dev.id)

    def _place_bars(self, ident: int) -> None:
        """Assigned memory BARs of a function, for the expected completer of
        a memory read."""
        dev = self.devices.get(ident)
        if dev is None:
            return
        dev.bars.clear()
        for i, reg in enumerate(_BAR_REGS):
            b = self._bars.get((ident, reg))
            if not b or b["mask"] is None or b["value"] is None or b.get("sizing") or b.get("upper"):
                continue
            if b["flags"] & 1:
                continue
            base, mask = b["value"] & b["mask"], b["mask"]
            if b["flags"] >> 1 & 3 == 0b10:
                hi = self._bars.get((ident, reg + 4))
                if not hi or hi["mask"] is None or hi["value"] is None or hi.get("sizing"):
                    continue
                base |= (hi["value"] & hi["mask"]) << 32
                mask |= hi["mask"] << 32
                size = (~mask & 0xFFFF_FFFF_FFFF_FFFF) + 1
            else:
                size = (~mask & 0xFFFFFFFF) + 1
            dev.bars[i] = Bar(i, size, b["flags"] >> 1 & 3 == 0b10, bool(b["flags"] & 8), base=base)

    def _predict_cfg(self, t: Tlp) -> int | None:
        """The value a configuration read must return, when the shadow
        knows it: a BAR after sizing, or an identity register read before."""
        if t.has_data:
            return None
        ident, reg = bdf_id(t.cfg_bus, t.cfg_dev, t.cfg_fn), t.cfg_reg
        b = self._bars.get((ident, reg))
        if b and b["mask"] is not None and b["value"] is not None and not b.get("sizing"):
            return (b["value"] & b["mask"]) | b["flags"]
        return self._ident.get((ident, reg))

    def _learn_read(self, req: Request) -> None:
        """Learn from a successful configuration read: the function, its
        BAR sizes, its capability list and MSI capability
        [cite: PCIe Base Spec, rev 3.0, §7.5.1]."""
        t, value = req.tlp, req.value
        bdf = (t.cfg_bus, t.cfg_dev, t.cfg_fn)
        ident, reg = bdf_id(*bdf), t.cfg_reg
        dev = self.devices.setdefault(ident, DeviceInfo(bdf=bdf))
        if reg in _ID_REGS:
            self._ident[(ident, reg)] = value
        b = self._bars.get((ident, reg))
        if b is not None and b.get("sizing"):
            lower = self._bars.get((ident, reg - 4))
            if reg != CFG_BAR0 and lower and lower["flags"] & 7 == 0b100 and not lower.get("upper"):
                b["mask"], b["flags"], b["upper"] = value, 0, True
            elif value & 1:
                b["mask"], b["flags"] = value & ~3, value & 3
            else:
                b["mask"], b["flags"] = value & ~0xF, value & 0xF
        caps = self._caps.setdefault(ident, set())
        if reg == CFG_CAP_PTR and value & 0xFC:
            caps.add(value & 0xFC)
        elif reg in caps:
            cid, nxt = value & 0xFF, value >> 8 & 0xFC
            if nxt:
                caps.add(nxt)
            if cid == CAP_MSI:
                dev.msi = MsiCap(reg, bool(value >> 23 & 1), value >> 17 & 7, bool(value >> 24 & 1))

    # -- completions the root port sends ----------------------------------------
    def _tx_completion(self, c: Tlp) -> None:
        self.stats["tx_completions"] += 1
        key = (c.requester_id, c.tag)
        ep = self._ep_np.get(key)
        if ep is None:
            self._fail(f"{c.kind} sent to {c.requester_id:#06x} tag {c.tag}: no endpoint "
                       "request waits for it")
            return
        req, t = ep.req, ep.tlp
        if c.completer_id != self.rc_id:
            self._fail(f"{c.kind} tag {c.tag}: completer ID {c.completer_id:#06x}, the root "
                       f"port is {self.rc_id:#06x}")
        if c.tc != t.tc or (c.attr & 3) != (t.attr & 3) or c.type != 0b01010:
            self._fail(f"{c.kind} tag {c.tag}: TC/Attr {c.tc}/{c.attr & 3}, type {c.type:05b}; "
                       f"the request's {t.tc}/{t.attr & 3}, an unlocked completion")
        if c.status != ep.status:
            self._fail(f"{c.kind} tag {c.tag} to {t.kind} at {t.address:#x}: status "
                       f"{c.status:03b}, predicted {ep.status:03b} (rc HAS, inbound routing)")
            del self._ep_np[key]
            return
        if t.kind != "MRd":  # an Unsupported Request to an I/O, configuration or locked request
            if c.has_data:
                self._fail(f"{c.kind} with data for an Unsupported Request")
            del self._ep_np[key]
            return
        before = len(req.data)
        self._mem_completion(req, c, STATUS_NAMES[c.status])
        for i, b in enumerate(req.data[before:], start=req.first_byte + before):
            want = ep.snap.get(i)
            now = self._host_mem.get(i)
            if want is not None and now is want and b != want.value:
                self._fail(f"CplD tag {c.tag}: host memory byte {i:#x} sent as {b:#04x}, "
                           f"holds {want.value:#04x}")
        if req.done:
            del self._ep_np[key]

    # ------------------------------------------------------------------
    # Receive: what the root port's link delivered
    def observe_rx_beats(self, beats: list[int]) -> None:
        self.observe_rx(from_beats(beats))

    def observe_rx(self, raw: bytes) -> None:
        """A TLP the root port's ``link`` delivered on its receive stream."""
        try:
            self._observe_rx(raw)
        finally:
            self.side = "rc"

    def _observe_rx(self, raw: bytes) -> None:
        self._rx_seq += 1
        try:
            t = Tlp.decode(raw)
        except TlpError as exc:
            self._event(ERR_MALFORMED)
            self._blame("endpoint", f"malformed TLP: {exc}")
            return
        self.received.append(t)
        if t.is_completion:
            self._rx_completion(t)
            return
        self.stats["rx_requests"] += 1
        # A request or message the routing would serve, capture or record,
        # poisoned, sets bit 5 only and has no other effect, but a poisoned
        # non-posted request is answered with an Unsupported Request
        # completion; the routing's Unsupported Request outranks poisoned
        # (bit 11 only) [cite: PCIe Base Spec, rev 3.0, §2.7.2.2] (rc HAS,
        # inbound routing).
        if t.ep:
            self._blame("endpoint", f"poisoned {t.kind} [cite: PCIe Base Spec, rev 3.0, §2.7.2.2]")
        if t.kind in ("Msg", "MsgD"):
            if t.ep:
                self._event(ERR_POISONED)
                return
            self._event(_ERR_MSG.get(t.msg_code, ERR_MSG_OTHER))
            self.side = "endpoint"
            self._on_message(t)
            return
        if t.kind in ("MRd", "MWr"):
            self._rx_memory(t)
            return
        self._event(ERR_INBOUND_UR)
        self._blame("endpoint", f"unexpected {t.kind} from the endpoint")
        if t.credit_class == NON_POSTED:
            self._expect_answer(t, CPL_UR)

    def _rx_memory(self, t: Tlp) -> None:
        """Endpoint memory request: the endpoint's rules (host model) and
        the root port's inbound routing (rc HAS)."""
        self.side = "endpoint"
        if t.ep:
            pass                         # reported; not an interrupt nor a read to track
        elif t.kind == "MWr":
            HostModel._on_mem_write(self, t)
        else:
            self._check_ep_read(t)
            if (t.requester_id, t.tag) in self._ep_np:
                self._fail(f"MRd tag {t.tag}: the tag is still waiting for its completion")
        lo, hi = t.address, t.address + 4 * t.length_dw
        msi_end = self.msi_addr + 4096
        mem_end = self.host_mem_base + self.host_mem_size
        if self._in_window(lo, hi) != "none" or not self.bus_master:
            route = "ur"
        elif lo >= self.msi_addr and hi <= msi_end:
            route = "msi" if t.kind == "MWr" and t.length_dw == 1 else "ur"
        elif lo >= self.host_mem_base and hi <= mem_end:
            route = "mem"
        else:
            route = "ur"
        if route == "ur":
            self._event(ERR_INBOUND_UR)
            if t.kind == "MRd":
                self._expect_answer(t, CPL_UR)
        elif t.ep:
            self._event(ERR_POISONED)
            if t.kind == "MRd":
                self._expect_answer(t, CPL_UR)
        elif route == "msi":
            entry = [(lo - self.msi_addr) >> 2, int.from_bytes(t.data, "little"), False]
            if self.msi_depth_exact:
                self._msi_new.append(entry)  # decided at the end of the cycle (tick)
            elif len(self._msi_q) >= self.msi_depth:
                entry[2] = True
                self._event(ERR_MSI_DROP, sure=False)
            self._msi_q.append(entry)
        elif t.kind == "MWr":
            mask = self._be_mask(t)
            for i, b in enumerate(t.data):
                if mask[i]:
                    self._host_mem[lo + i] = _Mem(b, "dma", self._rx_seq, self.now)
        else:
            self._expect_answer(t, CPL_SC)

    def _expect_answer(self, t: Tlp, status: int) -> None:
        req = Request("mem_rd", t)
        if t.kind == "MRd":
            req.first_byte, req.total = self._read_span(t)
            req.remaining, req.next_byte = req.total, req.first_byte
            req.end_dw = t.address + 4 * t.length_dw
        ep = _EpRead(t, status, req, self._rx_seq, self.now)
        if status == CPL_SC:
            for a in range(req.first_byte, req.first_byte + req.total):
                m = self._host_mem.get(a)
                sure = m is not None and (m.seq < ep.seq if m.src == "dma"
                                          else m.at <= self.now - self.latency)
                if sure:
                    ep.snap[a] = m
        self._ep_np[(t.requester_id, t.tag)] = ep

    def _rx_completion(self, c: Tlp) -> None:
        """A completion for one of the root port's requests: the host
        model's checks, then the result it predicts."""
        self.stats["rx_completions"] += 1
        req = self._outstanding.get(c.tag)
        if req is None or c.requester_id != req.tlp.requester_id:
            self._event(ERR_UNEXPECTED)
            if c.tag in self._late:
                return  # a late completion after a root-port timeout
            self._blame("endpoint", f"unexpected completion: requester {c.requester_id:#06x}, "
                        f"tag {c.tag}, status {c.status:03b}")
            return
        # The root port's outcome, first row deciding (rc_tl MAS, completion
        # matching): CRS, UR, CA, a reserved status, then a successful
        # completion that is malformed (bit 6) or poisoned (bit 5).
        status = STATUS_NAMES.get(c.status)
        failed = None  # why the root port ends the request BAD at once
        if status is None:
            failed = f"reserved Completion Status {c.status:03b}"
        elif status == "CRS":
            if req.kind == "mem_rd":  # only a configuration request may be retried
                failed = "CRS completion to a memory read"
            else:
                self._event(ERR_CRS)
        elif status in ("UR", "CA"):
            self._event(ERR_UR if status == "UR" else ERR_CA)
        else:
            failed = self._malformed_cpl(req, c)
            if failed is None and c.ep:
                self._event(ERR_POISONED)
        if failed is not None:
            req.bad = True
            self._event(ERR_MALFORMED)
        # The status row decides first: on a non-Successful completion EP
        # has no effect, so the host checks judge it without EP (a copy;
        # the observed TLP is left as it came).
        judged = replace(c, ep=0) if c.ep and status != "SC" else c
        errs = len(req.errors)
        self.side = "endpoint"
        pre = getattr(req, "pre_capture", None)
        if pre is not None and c.completer_id not in pre:
            self._fail(f"{req.kind} tag {c.tag}: completer ID {c.completer_id:#06x} before the "
                       f"first configuration write, expected one of "
                       f"{sorted(hex(x) for x in pre)}", req)
        self.requester_id = req.tlp.requester_id
        try:
            self._on_completion(judged)
        finally:
            self.side = "rc"
        if failed is not None:
            # The root port ends the request at once: BAD, its data not
            # delivered, later completions for it unexpected (bit 4) and,
            # as the endpoint's, still reported.
            if not req.done:
                self._finish(req, "error")
            elif self._outstanding.get(req.tag) is req:
                del self._outstanding[req.tag]
            req.unsure = False
        elif len(req.errors) > errs:
            req.unsure = True  # an endpoint rule the root port does not judge (Completer ID, TC)
        self._check_deferred(req)
        if req.done:
            req.done_at = self.now
            if req.kind == "cfg_rd" and req.status == "SC":
                self._check_prediction(req)
                self._learn_read(req)
            elif req.kind == "mem_rd" and req.status == "SC" and req.tlp.first_be:
                for a, want in req.predict.items():
                    got = req.data[a - req.first_byte]
                    if got != want:
                        self._blame("endpoint", f"memory read at {a:#x}: byte {got:#04x}, the "
                                    f"root port wrote {want:#04x}", req)

    @staticmethod
    def _malformed_cpl(req: Request, c: Tlp) -> str | None:
        """Why a successful completion is malformed for the root port, or
        None (rc_tl MAS, completion matching)
        [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]."""
        if req.kind == "cfg_rd":
            return None if c.has_data and c.length_dw == 1 else "configuration read completion " \
                "without one DW of data"
        if req.kind == "cfg_wr":
            return "configuration write completion with data" if c.has_data else None
        if req.kind != "mem_rd":
            return None
        if not c.has_data:
            return "successful memory read completion without data"
        if c.byte_count_value != req.remaining:
            return f"Byte Count {c.byte_count_value}, {req.remaining} remaining"
        if c.lower_addr != req.next_byte & 0x7F:
            return f"Lower Address {c.lower_addr:#x}, next byte {req.next_byte & 0x7F:#x}"
        stop = (req.next_byte & ~3) + 4 * c.length_dw
        if stop > req.end_dw:
            return f"data ending at {stop:#x}, past the request's {req.end_dw:#x}"
        if stop < req.end_dw and stop % 64:
            return f"partial completion ending at {stop:#x}, not on 64 bytes"
        return None

    def _check_prediction(self, req: Request) -> None:
        if req.predict is not None and req.value != req.predict:
            t = req.tlp
            self._blame("endpoint", f"configuration read of {t.cfg_bus}:{t.cfg_dev}.{t.cfg_fn} "
                        f"reg {t.cfg_reg:#x}: {req.value:#010x}, predicted {req.predict:#010x}",
                        req)

    # ------------------------------------------------------------------
    # Results, MSI captures and the error record
    def observe_result(self, status, data: int = 0, last: bool = True) -> None:
        """One result beat ``rc_tl`` gave ``rc_seq`` (``rsp_valid``):
        ``status`` as a number or a name of the tl-seq page."""
        self.side = "rc"
        self.stats["results"] += 1
        st = RESULT_NAMES.index(status) if isinstance(status, str) else status
        if not self._cmds:
            self._fail(f"result {RESULT_NAMES[st]} with no command waiting for one")
            return
        c = self._cmds[0]
        what = f"{OP_NAMES[c.op] if c.op < len(OP_NAMES) else c.op} {c.addr:#x}"
        if c.op == ERR_RD and c.expect is None:
            self._err_result(c, st, data, last, what)
        elif c.op == HOST_RD and c.expect is None:
            self._host_result(c, st, data, last, what)
        elif c.expect is not None and not (c.local_ur_ok and st == LOCAL_UR and not c.sent):
            self._fixed_result(c, st, data, last, what)
        else:
            self._endpoint_result(c, st, data, last, what)

    def _done(self, c: _Cmd) -> None:
        self._cmds.popleft()
        for d in [d for d in self._deferred if d[0] is c]:
            self._deferred.remove(d)
            if not d[1].unsure and d[1].status == "SC":
                self._fail(f"{d[4]}: beat {d[2]} before its data arrived")
        if c.op == ERR_CLR:  # the clear has taken effect by its result
            for clears in self._clears.values():
                for clr in clears:
                    if clr[0] == c.idx:
                        clr[2] = self.now
        if c in self._unsent:
            self._unsent.remove(c)

    def _fixed_result(self, c, st, data, last, what) -> None:
        statuses, value, mask = c.expect[c.beats]
        c.beats += 1
        end = c.beats == len(c.expect)
        if st not in statuses or (data ^ value) & mask & 0xFFFFFFFF or last != end:
            exp = "/".join(RESULT_NAMES[s] for s in sorted(statuses))
            self._fail(f"{what}: result {RESULT_NAMES[st]} data {data:#010x} last {int(last)}; "
                       f"predicted {exp} data {value & mask & 0xFFFFFFFF:#010x} last {int(end)}")
        if last or end:
            self._done(c)

    def _host_result(self, c, st, data, last, what) -> None:
        if st != OK or not last:
            self._fail(f"{what}: result {RESULT_NAMES[st]} last {int(last)}; predicted OK last 1")
        for i, a in enumerate(self._host_span(c.addr)):
            m = c.host_bytes.get(a)
            sure = m is not None and self._host_mem.get(a) is m and (
                m.src == "host" or m.at <= c.at - self.latency or m.seq <= self._msi_fence)
            if sure and data >> 8 * i & 0xFF != m.value:
                self._fail(f"{what}: byte {i} is {data >> 8 * i & 0xFF:#04x}, host memory holds "
                           f"{m.value:#04x}")
        self._done(c)

    def _err_result(self, c, st, data, last, what) -> None:
        """The error record must hold every event surely recorded before
        the read and after its bit's last clear, and nothing that cannot
        have been recorded by the result."""
        self.stats["err_reads"] += 1
        must = may = 0
        for e in self._events:
            earlier = [clr for clr in self._clears.get(e.bit, []) if clr[0] < c.idx]
            _, c0, c1 = earlier[-1] if earlier else (None, None, None)
            if e.lo > self.now:
                continue
            if c0 is None or e.hi >= c0:
                may |= 1 << e.bit
            if e.sure and e.hi <= c.at and (c0 is None or (c1 is not None and e.lo >= c1)):
                must |= 1 << e.bit
        if st != OK or not last or data & must != must or data & ~may & 0xFFFFFFFF:
            self._fail(f"{what}: result {RESULT_NAMES[st]} record {data:#06x}; predicted OK "
                       f"with bits {must:#06x} set and none outside {may:#06x}")
        self._done(c)

    def _endpoint_result(self, c: _Cmd, st, data, last, what) -> None:
        req = c.req
        if req is None:
            if c.down:
                self._fixed_once(c, st, data, last, {LINK_DOWN}, what)
            elif c.local_ur_ok:
                self._fixed_once(c, st, data, last, {LOCAL_UR}, what)
            else:
                self._fail(f"{what}: result {RESULT_NAMES[st]} before its request was sent")
                self._done(c)
            return
        if req.unsure:  # the endpoint broke a rule the shadow already reported
            if last:
                self._done(c)
            return
        i = c.beats
        c.beats += 1
        n = c.length
        if st == OK and c.op == MEM_RD:
            if req.done and (req.status != "SC" or req.bad):
                self._final(c, req, st, data, True, what)
                return
            if i >= n:
                self._fail(f"{what}: more than {n} result beats")
            else:
                self._read_beat(c, req, i, data, what)
            if last != (i == n - 1):
                self._fail(f"{what}: beat {i} last {int(last)} of {n}")
            if i == n - 1 and req.done and req.status != "SC":
                self._fail(f"{what}: OK, but the read ended {req.status}")
            if i == n - 1 and not req.done:
                self._fail(f"{what}: OK before the read's last completion arrived")
            if i == n - 1 and req.bad:
                self._fail(f"{what}: OK for a read the shadow predicts BAD")
            if last:
                self._done(c)
            return
        self._final(c, req, st, data, last, what)

    def _fixed_once(self, c, st, data, last, statuses, what) -> None:
        c.expect = [(statuses, 0, ~0)]
        c.beats = 0
        self._fixed_result(c, st, data, last, what)

    def _read_beat(self, c: _Cmd, req: Request, i: int, data: int, what: str) -> None:
        """Beat ``i`` of a memory read: the DW at ``addr + 4i``, its enabled
        bytes as the completions carried them. The root port may give a
        DW as its completion arrives, before the shadow sees the whole
        completion: such a beat is checked when it does."""
        be = c.first_be if i == 0 else (c.last_be if i == c.length - 1 else 0xF)
        for j in range(4):
            if not be >> j & 1:
                continue
            a = c.addr + 4 * i + j
            k = a - req.first_byte
            if k >= len(req.data):
                if req.done:
                    self._fail(f"{what}: beat {i} beyond the data the completions carried")
                else:
                    self._deferred.append((c, req, i, data, what))
                return
            if data >> 8 * j & 0xFF != req.data[k]:
                self._fail(f"{what}: beat {i} byte {j} is {data >> 8 * j & 0xFF:#04x}, the "
                           f"completion carried {req.data[k]:#04x}")

    def _check_deferred(self, req: Request) -> None:
        """Beats given before the completion carrying their data arrived."""
        waiting = [d for d in self._deferred if d[1] is req]
        self._deferred = [d for d in self._deferred if d[1] is not req]
        for c, r, i, data, what in waiting:
            if r.unsure:
                continue
            self._read_beat(c, r, i, data, what)

    def _final(self, c: _Cmd, req: Request, st, data, last, what) -> None:
        """The last beat of an endpoint command."""
        if not last:
            self._fail(f"{what}: {RESULT_NAMES[st]} on a beat that is not the last")
        if st == TIMEOUT and not req.done:
            age = self.now - req.sent_at
            if age < self.timeout_min:
                self._fail(f"{what}: TIMEOUT {age} cycles after the request, before "
                           f"{self.timeout_min}")
            self._timed_out(req)
        elif st == TIMEOUT and req.done and req.status == "SC" and self.timeout_min and \
                req.done_at - req.sent_at >= self.timeout_min:
            self._timed_out(req)  # its completion raced the root port's timer
        elif not req.done:
            self._fail(f"{what}: result {RESULT_NAMES[st]} while the request is outstanding")
        else:
            want = set(_FROM_HOST.get(req.status, {BAD}))
            if req.status == "CRS":
                want = {CRS}  # a configuration request's (memory reads set bad)
            if req.bad:
                want = {BAD}
            if req.status == "SC" and self._down_at is not None and \
                    req.done_at >= self._down_at - self.latency:
                want.add(LINK_DOWN)
            if st not in want:
                exp = "/".join(RESULT_NAMES[s] for s in sorted(want))
                self._fail(f"{what}: result {RESULT_NAMES[st]}, predicted {exp}")
            if st == OK and req.kind == "cfg_rd":
                be = c.first_be
                m = sum(0xFF << 8 * j for j in range(4) if be >> j & 1)
                if (data ^ (req.value or 0)) & m:
                    self._fail(f"{what}: data {data:#010x}, the completion carried "
                               f"{req.value:#010x}")
            elif data and st != OK or (st == OK and req.kind == "cfg_wr" and data):
                self._fail(f"{what}: data {data:#010x} on a {RESULT_NAMES[st]} result; "
                           "predicted 0")
        self._done(c)

    def _timed_out(self, req: Request) -> None:
        self._event(ERR_TIMEOUT, lo=req.sent_at + self.timeout_min, hi=self.now)
        self._late.add(req.tag)
        if not req.done:
            self._finish(req, "timeout")
        elif self._outstanding.get(req.tag) is req:
            del self._outstanding[req.tag]

    def observe_msi(self, offset: int, data: int) -> None:
        """An MSI capture ``rc_seq`` took (``msi_valid`` and ``msi_ready``)."""
        self.side = "rc"
        self.stats["msi"] += 1
        while self._msi_q:
            off, val, may_drop = self._msi_q.popleft()
            if may_drop is None:
                continue                  # surely dropped (exact depth)
            if (off, val) == (offset, data):
                self._msi_fence = self._rx_seq
                return
            if not may_drop:
                self._fail(f"MSI capture offset {offset:#x} data {data:#010x}; predicted "
                           f"offset {off:#x} data {val:#010x}")
                return
        self._fail(f"MSI capture offset {offset:#x} data {data:#010x} with none predicted")

    # ------------------------------------------------------------------
    # Link state, time and the end of a test
    def observe_dl_up(self, up: bool) -> None:
        """The root port's ``dl_up_o``."""
        if up or not self.dl_up:
            self.dl_up = bool(up)
            return
        self.dl_up = False
        self._down_at = self.now
        for c in self._unsent:
            c.down = True
        self._unsent.clear()
        self._ep_np.clear()
        self.link_down()  # the host model's: outstanding requests end link_down
        self._reset_endpoint()

    def tick(self, cycles: int = 1) -> None:
        """Advance the shadow's clock, one call per ``clk_i`` cycle."""
        if self._msi_new:
            self._decide_msi()
        self.now += cycles
        if self.timeout_max is None or not self._cmds:
            return
        req = self._cmds[0].req
        if req is not None and not req.done and req.sent_at is not None and \
                self.now - req.sent_at > self.timeout_max + self.latency:
            self.side = "rc"
            self._fail(f"{req.tlp.kind} tag {req.tag}: no completion and no TIMEOUT result "
                       f"{self.now - req.sent_at} cycles after it was sent")
            self._finish(req, "timeout")
            self._cmds.popleft()

    def _decide_msi(self) -> None:
        """Exact depth: an MSI that arrived this cycle is dropped when
        ``msi_depth`` captures are held after this cycle's take."""
        for entry in self._msi_new:
            held = 0
            for e in self._msi_q:
                if e is entry:
                    break
                held += e[2] is not None
            if held >= self.msi_depth:
                entry[2] = None
                self._event(ERR_MSI_DROP)
        self._msi_new.clear()

    @property
    def pending_msi(self) -> int:
        """MSI captures predicted and not yet taken."""
        return sum(e[2] is not None for e in self._msi_q)

    def finish(self) -> None:
        """End of a test: every command has its result and every endpoint
        request its answer."""
        self.side = "rc"
        if self._cmds:
            c = self._cmds[0]
            self._fail(f"{len(self._cmds)} command(s) without a result, first "
                       f"{OP_NAMES[c.op] if c.op < len(OP_NAMES) else c.op} {c.addr:#x}")
        for (rid, tag), ep in self._ep_np.items():
            self._fail(f"{ep.tlp.kind} from {rid:#06x} tag {tag} at {ep.tlp.address:#x} never "
                       "answered")
