"""Architecture model of pcie_rc, the root-port stack: rc_tl and rc_seq
over link (wiki/src/arch/rc/HAS.md, its tl-seq page TL_SEQ.md, and the
sub-IP pages wiki/src/rtl/rc_tl/{MAS,REQUESTER,INBOUND}.md and
wiki/src/rtl/rc_seq/{MAS,PROGRAMS}.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). Written from those pages only; it shares no code with
the DV host model (src/dv/core/models/), which is the e2e oracle. It
models, one step per clk_i cycle:

- ``RcTl``, rc_tl at transaction level: the field and routing checks of a
  command, the Type 1 header, host memory, the error record, the MSI
  capture, the requester (one request, 5-bit rolling tag, completion
  matching and checks, the completion timeout), the receive checks and
  inbound routing, the completer (64-byte read completion boundary), the
  transmit arbiter and the credit release; link down (``lrst``);
- ``RcSeq``, the rc_seq interpreter on 128-bit ROM words (the MAS step
  format), with its result registers, waits, Hot Reset and jumps, and the
  M4 programs ``ENUM``, ``MMIO``, ``DMA_MSI`` and ``ERRORS`` built by an
  assembler from the PROGRAMS page;
- ``Bench``: rc over its end of the link model's ``LinkPair``, the
  endpoint ``pcie_core_arch.Core`` over the other, and ``EpApp``, the
  application the PROGRAMS boundary asks for (BAR0 as memory, the DMA
  doorbell, the no-answer DW).

The sibling arch models (dll, link, tl, cfg, core) are loaded by bare
name from src/arch, as the arch SPEC import rule allows: TLP formats,
credits and splitting come from them. Cycle costs inside rc_tl and rc_seq
are the MAS timing figures; where a MAS gives none (host memory
arbitration, the link's Hot Reset timer) the model uses the constants
below, which bound nothing. Python standard library only.
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

# The sibling models by bare name from src/arch: arch may use another IP's
# arch model; it never imports DV, RTL or tools.
for _ip in ("dll", "link", "tl", "cfg", "core"):
    _d = str(Path(__file__).resolve().parent.parent / _ip)
    if _d not in sys.path:
        sys.path.append(_d)

import pcie_cfg_arch as cfgm  # noqa: E402
import pcie_core_arch as corem  # noqa: E402
import pcie_dll_arch as dll  # noqa: E402
import pcie_link_arch as link  # noqa: E402
import pcie_tl_arch as tlm  # noqa: E402

# ---------------------------------------------------------------- constants

CLK_MHZ = 125                 # clk_i (HAS, Clocks and reset)
MSI_WINDOW_BYTES = 4096       # the MSI window (HAS, MSI window)
MAX_READ_DW = 32              # 128-byte read limit (M2 Q6)
ADDR_32 = 1 << 32             # memory requests from rc use 32-bit addresses
PAGE = 4096                   # a read never crosses a 4 KiB boundary
RCB = 64                      # read completion boundary (INBOUND, Completer)
CPL_TIMEOUT = 1 << 21         # N at CPL_TIMEOUT_DIV 1 (REQUESTER, Completion timeout)
TAG_MOD = 32                  # 5-bit rolling tag (REQUESTER, Requests)
MSI_DEPTH = 4                 # MSI capture entries (INBOUND, MSI capture; D10)
ROM_AW = 8                    # rc_seq ROM: 256 steps (rc_seq MAS, Programs)
ROM_STEPS = 1 << ROM_AW
REQ_TX_DELAY = 3              # take to first offer on tlp_tx (rc_tl MAS, Timing)
HOST_RD_DELAY = 2             # HOST_RD beat: second cycle after the take (INBOUND)
# Model constants, not MAS values: #376 sets the package's.
WAIT_TIMEOUT = 200_000        # WAIT_LINK, WAIT_MSI and HOT_RESET timeouts, cycles
HOT_RESET_HOLD = 16           # HOT_RESET hold after link_up falls, cycles
LOOP_LIMIT = 2000             # polling-loop limit (backward takes)
HOT_RESET_ENTRY = 8           # bench: hot_reset_req to link_up falling, cycles
HOT_RESET_EXIT = 2000         # bench: the link's Hot Reset timer, scaled down

# tl-seq ops and result statuses (TL_SEQ, Commands and Results).
CFG_RD, CFG_WR, MEM_RD, MEM_WR, HDR_RD, HDR_WR, HOST_RD, HOST_WR, ERR_RD, ERR_CLR = range(10)
OK, UR, CA, CRS, TIMEOUT, LOCAL_UR, LINK_DOWN, BAD = range(8)
STATUS_NAMES = ("OK", "UR", "CA", "CRS", "TIMEOUT", "LOCAL_UR", "LINK_DOWN", "BAD")
ENDPOINT_OPS = (CFG_RD, CFG_WR, MEM_RD, MEM_WR)

# Error record bits (TL_SEQ, Error record).
(E_UR, E_CA, E_CRS, E_TIMEOUT, E_UNEXPECTED, E_POISONED, E_MALFORMED, E_COR, E_NONFATAL,
 E_FATAL, E_MSG, E_INBOUND_UR, E_MSI_DROP) = range(13)
ERR_MASK = 0x1FFF
MSG_BITS = {0x30: E_COR, 0x31: E_NONFATAL, 0x33: E_FATAL}

# Defaults of the HAS parameter table (lower case).
DEFAULTS = {"host_mem_addr_w": 12, "host_mem_base": 0x0000_0000, "msi_addr": 0xFEE0_0000,
            "vendor_id": 0x0000, "device_id": 0x0000, "revision_id": 0x00,
            "fc_ph": 8, "fc_pd": 32, "fc_nph": 8, "fc_npd": 8, "cpl_timeout_div": 1}
_RANGES = {"host_mem_addr_w": (8, 16), "host_mem_base": (0, ADDR_32 - 1),
           "msi_addr": (0, ADDR_32 - 1), "vendor_id": (0, 0xFFFE), "device_id": (0, 0xFFFE),
           "revision_id": (0, 0xFF), "fc_ph": (1, 127), "fc_pd": (8, 2047),
           "fc_nph": (1, 127), "fc_npd": (2, 2047), "cpl_timeout_div": (1, 1024)}


def params_of(over: dict | None = None) -> dict:
    """The HAS parameters with ``over`` applied, range-checked (HAS,
    Parameters)."""
    p = dict(DEFAULTS, **(over or {}))
    for k, v in p.items():
        if k not in _RANGES or not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"parameter {k}={v!r}")
        lo, hi = _RANGES[k]
        if not lo <= v <= hi:
            raise ValueError(f"parameter {k}={v:#x} outside {lo:#x}..{hi:#x}")
    address_map(p)
    return p


# --------------------------------------------------- address map, routing

def memory_window(mem_base: int, mem_limit: int) -> tuple[int, int] | None:
    """The memory window from the Memory Base and Memory Limit registers
    (16 bits each, address bits 31:20 in [15:4]): first and last byte, or
    None while it is empty (base above limit)."""
    lo = (mem_base & 0xFFF0) << 16
    hi = ((mem_limit & 0xFFF0) << 16) | 0xFFFFF
    return None if lo > hi else (lo, hi)


def _inside(addr: int, nbytes: int, first: int, last: int) -> bool:
    return first <= addr and addr + nbytes - 1 <= last


def outbound(req: dict, hdr: dict, dl_up: bool) -> str:
    """The HAS outbound routing of one sequencer request to the endpoint:
    'send', 'LOCAL_UR' or 'LINK_DOWN'. ``req`` has ``kind`` ('cfg' or
    'mem'), and ``bus`` and ``dev`` (cfg) or ``addr``, ``len`` in DWs and
    ``write`` (mem; a write is 1 DW, a read 1 to 32). ``hdr`` holds the
    header fields (``secondary``, ``mem_space_en``, ``mem_base``,
    ``mem_limit``)."""
    if not dl_up:
        return "LINK_DOWN"
    if req["kind"] == "cfg":
        return "send" if req["bus"] == hdr["secondary"] and req["dev"] == 0 else "LOCAL_UR"
    if req["kind"] != "mem":
        raise ValueError(f"request kind {req['kind']!r}")
    addr, nbytes = req["addr"], 4 * req["len"]
    max_len = 1 if req.get("write", False) else MAX_READ_DW
    if not 1 <= req["len"] <= max_len or addr % 4 or addr // PAGE != (addr + nbytes - 1) // PAGE:
        return "LOCAL_UR"
    win = memory_window(hdr["mem_base"], hdr["mem_limit"])
    if not hdr["mem_space_en"] or win is None or not _inside(addr, nbytes, *win):
        return "LOCAL_UR"
    return "send"


def inbound(req: dict, hdr: dict, params: dict = DEFAULTS) -> str:
    """The HAS inbound routing of one endpoint memory request: 'host',
    'msi' or 'ur', the rows checked in order. ``req`` has ``write``,
    ``addr`` (bytes) and ``len`` (DWs); ``hdr`` the header fields
    (``bus_master_en``, ``mem_base``, ``mem_limit``)."""
    addr, nbytes = req["addr"], 4 * req["len"]
    win = memory_window(hdr["mem_base"], hdr["mem_limit"])
    # Whole DWs: byte enables are deliberately not applied to the window overlap.
    if win is not None and addr <= win[1] and addr + nbytes - 1 >= win[0]:
        return "ur"
    if not hdr["bus_master_en"]:
        return "ur"
    msi = params["msi_addr"]
    if req["write"] and req["len"] == 1 and _inside(addr, nbytes, msi, msi + MSI_WINDOW_BYTES - 1):
        return "msi"
    base, size = params["host_mem_base"], 1 << params["host_mem_addr_w"]
    if _inside(addr, nbytes, base, base + size - 1):
        return "host"
    return "ur"


def address_map(params: dict) -> dict[str, int]:
    """Host memory size, overlap with the MSI window and the highest byte
    of both, in bytes."""
    w = params["host_mem_addr_w"]
    if not 8 <= w <= 16:
        raise ValueError(f"host_mem_addr_w {w!r}: 8 to 16")
    size, base, msi = 1 << w, params["host_mem_base"], params["msi_addr"]
    if base % size or msi % MSI_WINDOW_BYTES:
        raise ValueError("host_mem_base must be size aligned, msi_addr 4 KiB aligned")
    overlap = max(0, min(base + size, msi + MSI_WINDOW_BYTES) - max(base, msi))
    return {
        "host_mem_bytes": size,
        "map_overlap_bytes": overlap,
        "map_top_byte": max(base + size, msi + MSI_WINDOW_BYTES) - 1,
    }


def be_legal(op: int, length: int, addr: int, fbe: int, lbe: int) -> bool:
    """The byte-enable rule of a command (REQUESTER, Byte enables)."""
    if length == 1 and lbe:
        return False
    if op in (CFG_RD, CFG_WR, MEM_WR) and not fbe:
        return False
    if op == MEM_RD and length >= 2:
        return tlm.be_ok(length, fbe, lbe, addr)
    return True


def _byte_mask(be: int) -> int:
    return sum(0xFF << (8 * i) for i in range(4) if be >> i & 1)


# ---------------------------------------------------------- Type 1 header

class Header:
    """The root port's Type 1 header (HAS, Type 1 header): the RW fields
    as registers, every other field a constant."""

    # DW index -> RW bits (HAS header table)
    RW = {1: 0x0000_0006, 3: 0x0000_00FF, 6: 0x00FF_FFFF, 8: 0xFFF0_FFF0}

    def __init__(self, p: dict):
        self.p = p
        self.regs = {i: 0 for i in self.RW}

    def read(self, idx: int) -> int:
        p = self.p
        const = {0: p["vendor_id"] | p["device_id"] << 16,
                 2: p["revision_id"] | 0x060400 << 8,
                 3: 0x01 << 16}
        return const.get(idx, 0) | self.regs.get(idx, 0)

    def write(self, idx: int, data: int, be: int) -> None:
        if idx in self.RW:
            m = _byte_mask(be) & self.RW[idx]
            self.regs[idx] = (self.regs[idx] & ~m) | (data & m)

    @property
    def state(self) -> dict:
        """t_hdr_st: what routing reads."""
        r = self.regs
        return {"mem_space_en": r[1] >> 1 & 1, "bus_master_en": r[1] >> 2 & 1,
                "primary": r[6] & 0xFF, "secondary": r[6] >> 8 & 0xFF,
                "mem_base": r[8] & 0xFFFF, "mem_limit": r[8] >> 16}


# --------------------------------------------------------------- rc_tl

def check_cmd(cmd: dict, st: dict, dl_up: bool, host_mem_addr_w: int) -> int | None:
    """The take checks 1 to 7 of a command (rc_tl MAS, Commands): the
    refusing status, or None when the command passes. ``st`` is the
    header state."""
    op, addr, n = cmd["op"], cmd["addr"], cmd["len"]
    if op in ENDPOINT_OPS and not dl_up:
        return LINK_DOWN
    if not 0 <= op <= ERR_CLR:
        return LOCAL_UR
    if not (1 <= n <= MAX_READ_DW if op == MEM_RD else n == 1):
        return LOCAL_UR
    extra = {CFG_RD: 0xF000_0003, CFG_WR: 0xF000_0003, MEM_RD: 3, MEM_WR: 3,
             HDR_RD: 0xFFFF_F003, HDR_WR: 0xFFFF_F003,
             HOST_RD: (~((1 << host_mem_addr_w) - 1) & 0xFFFF_FFFF) | 3,
             HOST_WR: (~((1 << host_mem_addr_w) - 1) & 0xFFFF_FFFF) | 3}.get(op, 0)
    if addr & extra:
        return LOCAL_UR
    if op == MEM_RD and addr // PAGE != (addr + 4 * n - 1) // PAGE:
        return LOCAL_UR
    if not be_legal(op, n, addr, cmd["fbe"], cmd["lbe"]):
        return LOCAL_UR
    if op in (CFG_RD, CFG_WR):
        req = {"kind": "cfg", "bus": addr >> 20 & 0xFF, "dev": addr >> 15 & 0x1F}
    elif op in (MEM_RD, MEM_WR):
        req = {"kind": "mem", "addr": addr, "len": n, "write": op == MEM_WR}
    else:
        return None
    return None if outbound(req, st, True) == "send" else LOCAL_UR


def rx_kind(h: dict) -> str:
    """Receive check step 1: the TLP kind, or 'malformed' (INBOUND, Receive
    checks)."""
    fmt, typ = h["fmt"], h["type"]
    if fmt > 3:
        return "malformed"                      # a prefix or a reserved Fmt
    data = fmt & 2
    if typ == tlm.T_MEM:
        return "mem"
    if typ == tlm.T_MEMLK:
        return "malformed" if data else "memlk"
    if typ in (tlm.T_IO, tlm.T_CFG0, tlm.T_CFG1):
        return "malformed" if fmt & 1 else ("io" if typ == tlm.T_IO else "cfg")
    if typ in (tlm.T_FETCHADD, tlm.T_SWAP, tlm.T_CAS):
        return "atomic" if data else "malformed"
    if typ & 0x18 == 0x10:
        return "msg" if fmt & 1 else "malformed"
    if typ in (tlm.T_CPL, tlm.T_CPLLK):
        return "malformed" if fmt & 1 else "cpl"
    return "malformed"


def rx_malformed(h: dict, kind: str) -> bool:
    """Receive check steps 3 to 6 on a request or message."""
    if kind in ("mem", "memlk"):
        n = h["length"]
        if n == 1 and h["lbe"]:
            return True
        if n > 1 and not tlm.be_ok(n, h["fbe"], h["lbe"], h["addr"]):
            return True
        return (h["addr"] & (PAGE - 1)) + 4 * n > PAGE
    if kind in ("io", "cfg"):
        return h["length"] != 1 or h["lbe"] != 0 or h["tc"] != 0 or h["attr"] != 0
    if kind == "msg":
        return h["tc"] != 0 and h["code"] in tlm.TC0_MSGS
    return False


class Request:
    """The one outstanding endpoint request (REQUESTER, Requests)."""

    def __init__(self, cmd: dict, tlp: bytes, tag: int, rid: int, ready: int):
        self.cmd, self.tlp, self.tag, self.rid, self.ready = cmd, tlp, tag, rid, ready
        self.op = cmd["op"]
        self.sent: int | None = None
        self.cpls = 0
        if self.op == MEM_RD:
            self.left = tlm.byte_count(cmd["len"], cmd["fbe"], cmd["lbe"])
            self.nxt = cmd["addr"] + tlm._lo(cmd["fbe"])


class RcTl:
    """pcie_rc_tl at transaction level over ``d``, a ``pcie_dll_arch.Dll``
    with the credit-release port (or any object with its upper-layer
    methods)."""

    def __init__(self, p: dict, d):
        self.p, self.dll = p, d
        self.hdr = Header(p)
        self.mem = bytearray(1 << p["host_mem_addr_w"])
        self.timeout = CPL_TIMEOUT // p["cpl_timeout_div"]
        self.err = 0
        self.msi: deque = deque()                # [offset, data, capture cycle]
        self.msi_log: list[tuple] = []           # (cycle, offset, data) of every capture
        self.tag = 0
        self.rsp: deque = deque()                # [ready cycle, status, data, last]
        self.rsp_t = -1
        self.busy = False
        self.free_at = 0
        self.req: Request | None = None
        self.log: list[dict] = []                # finished endpoint requests
        self.events = [0] * 13                   # count of each error-record event
        self.seen = 0
        self.was_up = False
        self.counts = {"tlps_rx": 0, "tlps_tx": 0, "host_bytes_written": 0,
                       "host_bytes_read": 0, "completions_tx": 0, "link_downs": 0}
        self._lreset()

    # --------------------------------------------------------- state
    def _lreset(self) -> None:
        """lrst: receive parser, non-posted queue and completer, transmit,
        pending releases (rc_tl MAS, Reset and link down)."""
        self.npq: deque = deque()                # [kind, h, data credits]
        self.cpl_out: deque = deque()            # [ready cycle, tlp] of the completer
        self.cpl_busy = False
        self.txp: deque = deque()                # rc's MEM_WR
        self.txnp: deque = deque()               # rc's non-posted request
        self.rel: list[tuple[int, int, int]] = []

    def _event(self, bit: int) -> None:
        self.err |= 1 << bit
        self.events[bit] += 1

    def _beat(self, t: int, status: int, data: int = 0, last: bool = True) -> None:
        t = max(t, self.rsp_t + 1)
        self.rsp_t = t
        self.rsp.append([t, status, data & 0xFFFF_FFFF, last])

    def _finish(self, now: int, status: int) -> None:
        r = self.req
        self.log.append({"op": r.op, "addr": r.cmd["addr"], "len": r.cmd["len"],
                         "taken": r.ready - REQ_TX_DELAY, "sent": r.sent, "end": now,
                         "cpls": r.cpls, "status": status})
        self.req = None

    @property
    def rid(self) -> int:
        return self.hdr.state["primary"] << 8

    # ------------------------------------------------------ commands
    def offer(self, cmd: dict, now: int) -> bool:
        """cmd_valid with ``cmd``: True when taken (cmd_ready). One command
        in progress, from its take to its last result beat."""
        if self.busy or now < self.free_at:
            return False
        self.busy = True
        dl_up = self.dll.dl_up
        st = self.hdr.state
        bad = check_cmd(cmd, st, dl_up, self.p["host_mem_addr_w"])
        op, addr = cmd["op"], cmd["addr"]
        if bad is not None:
            self._beat(now + 1, bad)
        elif op == HDR_RD:
            self._beat(now + 1, OK, self.hdr.read(addr >> 2 & 0x3FF))
        elif op == HDR_WR:
            self.hdr.write(addr >> 2 & 0x3FF, cmd["data"], cmd["fbe"])
            self._beat(now + 1, OK)
        elif op == ERR_RD:
            self._beat(now + 1, OK, self.err)
        elif op == ERR_CLR:
            self.err &= ~cmd["data"]
            self._beat(now + 1, OK)
        elif op == HOST_RD:
            self.counts["host_bytes_read"] += 4
            self._beat(now + HOST_RD_DELAY, OK, int.from_bytes(self.mem[addr:addr + 4], "little"))
        elif op == HOST_WR:
            self._mem_write(addr, cmd["data"], cmd["fbe"])
            self._beat(now + 1, OK)
        else:
            self._request(cmd, now, st)
        return True

    def _request(self, cmd: dict, now: int, st: dict) -> None:
        op, addr = cmd["op"], cmd["addr"]
        rid = st["primary"] << 8
        data = cmd["data"].to_bytes(4, "little")
        if op in (CFG_RD, CFG_WR):
            tlp = tlm.cfg_tlp(op == CFG_WR, addr >> 20 & 0xFF, addr >> 15 & 0x1F,
                              addr >> 12 & 7, addr >> 2 & 0x3FF, cmd["fbe"], rid, self.tag,
                              data if op == CFG_WR else b"")
        else:
            tlp = tlm.mem_tlp(op == MEM_WR, addr, cmd["len"], cmd["fbe"], cmd["lbe"], rid,
                              0 if op == MEM_WR else self.tag, data if op == MEM_WR else b"")
        self.req = Request(cmd, tlp, self.tag, rid, now + REQ_TX_DELAY)
        (self.txp if op == MEM_WR else self.txnp).append(self.req)

    def pop_rsp(self, now: int):
        """The result beat of this cycle, (status, data, last), or None."""
        if not self.rsp or self.rsp[0][0] > now:
            return None
        _, status, data, last = self.rsp.popleft()
        if last:
            self.busy = False
            self.free_at = now + 1
        return status, data, last

    # ---------------------------------------------------- host memory
    def _mem_write(self, off: int, data: int, be: int) -> None:
        for i in range(4):
            if be >> i & 1:
                self.mem[off + i] = data >> (8 * i) & 0xFF
                self.counts["host_bytes_written"] += 1

    def _mem_read(self, off: int, n: int) -> bytes:
        self.counts["host_bytes_read"] += 4 * n
        return bytes(self.mem[off:off + 4 * n])

    # ---------------------------------------------------------- cycle
    def step(self, now: int) -> None:
        """One clk_i cycle: link-down handling, credit releases, received
        TLPs, the completion timeout, the completer, the transmit arbiter."""
        d = self.dll
        if not d.dl_up:
            if self.was_up:
                self.counts["link_downs"] += 1
            self.was_up = False
            self._lreset()
            if self.req is not None:
                self._beat(now + 1, LINK_DOWN)
                self._finish(now, LINK_DOWN)
            self.seen = len(d.delivered)
            return
        self.was_up = True
        due = [r for r in self.rel if r[0] <= now]
        self.rel = [r for r in self.rel if r[0] > now]
        for _, cls, dc in due:
            d.release(cls, dc)
        while self.seen < len(d.delivered):
            self.receive(d.delivered[self.seen], now)
            self.seen += 1
        r = self.req
        if r is not None and r.sent is not None and now - r.sent >= self.timeout:
            self._event(E_TIMEOUT)
            self._beat(now + 1, TIMEOUT)
            self._finish(now, TIMEOUT)
        self._completer(now)
        self._transmit(now)

    # ------------------------------------------------------- receive
    def receive(self, tlp: bytes, now: int) -> None:
        """A TLP's eop taken from link; every TLP is taken at once."""
        self.counts["tlps_rx"] += 1
        h = tlm.parse(tlp)
        cls, _, dc = tlm.credits(tlp)
        kind = rx_kind(h)
        if kind == "cpl":
            self._completion(h, now)
            return
        if kind == "malformed" or rx_malformed(h, kind):
            self._event(E_MALFORMED)
            if cls == dll.NP:
                self.npq.append(["drop", h, dc])
            elif cls == dll.P:
                self.rel.append((now + 1, dll.P, dc))
            return
        st, ep = self.hdr.state, h["ep"]
        if kind == "msg":
            self._event(E_POISONED if ep else MSG_BITS.get(h["code"], E_MSG))
            self.rel.append((now + 1, dll.P, dc))
            return
        route = "ur"
        if kind == "mem":
            route = inbound({"write": bool(h["fmt"] & 2), "addr": h["addr"],
                             "len": h["length"]}, st, self.p)
        if cls == dll.P:
            if route == "ur":
                self._event(E_INBOUND_UR)
            elif ep:
                self._event(E_POISONED)
            elif route == "host":
                self._host_write(h)
            else:
                self._capture(h, now)
            self.rel.append((now + 1, dll.P, dc))
            return
        if route == "ur":
            self._event(E_INBOUND_UR)
            self.npq.append(["urlk" if kind == "memlk" else "ur", h, dc])
        elif ep:
            self._event(E_POISONED)
            self.npq.append(["ur", h, dc])
        else:
            self.npq.append(["read", h, dc])

    def _host_write(self, h: dict) -> None:
        off, n = h["addr"] - self.p["host_mem_base"], h["length"]
        for i in range(n):
            be = h["fbe"] if i == 0 else (h["lbe"] if i == n - 1 else 0xF)
            self._mem_write(off + 4 * i, int.from_bytes(h["data"][4 * i:4 * i + 4], "little"), be)

    def _capture(self, h: dict, now: int) -> None:
        if len(self.msi) >= MSI_DEPTH:
            self._event(E_MSI_DROP)
            return
        off = (h["addr"] - self.p["msi_addr"]) >> 2
        data = int.from_bytes(h["data"][:4], "little")
        self.msi.append([off, data, now])
        self.msi_log.append((now, off, data))

    def take_msi(self):
        """msi_ready: removes the oldest capture; (offset, data) or None."""
        if not self.msi:
            return None
        off, data, _ = self.msi.popleft()
        return off, data

    # ------------------------------------------- completion matching
    def _completion(self, h: dict, now: int) -> None:
        r = self.req
        if (r is None or r.sent is None or r.op == MEM_WR or h["type"] == tlm.T_CPLLK
                or h["rid"] != r.rid or h["tag"] != r.tag):
            self._event(E_UNEXPECTED)
            return
        r.cpls += 1
        cfg, st, data = r.op in (CFG_RD, CFG_WR), h["status"], h["data"]
        fail = None
        if st == tlm.CRS:
            fail = (CRS, E_CRS) if cfg else (BAD, E_MALFORMED)
        elif st == tlm.UR:
            fail = (UR, E_UR)
        elif st == tlm.CA:
            fail = (CA, E_CA)
        elif st != tlm.SC:
            fail = (BAD, E_MALFORMED)
        elif self._cpl_malformed(r, h):
            fail = (BAD, E_MALFORMED)
        elif h["ep"]:
            fail = (BAD, E_POISONED)
        if fail is not None:
            self._event(fail[1])
            self._beat(now + 1, fail[0])
            self._finish(now, fail[0])
            return
        if r.op == CFG_RD:
            self._beat(now + 1, OK, int.from_bytes(data, "little"))
            self._finish(now, OK)
        elif r.op == CFG_WR:
            self._beat(now + 1, OK)
            self._finish(now, OK)
        else:
            n = len(data) // 4
            span = (r.nxt & ~3) + 4 * n - r.nxt
            last = span >= r.left
            r.left, r.nxt = r.left - min(span, r.left), r.nxt + span
            for i in range(n):
                self._beat(now + 1 + i, OK, int.from_bytes(data[4 * i:4 * i + 4], "little"),
                           last and i == n - 1)
            if last:
                self._finish(now, OK)

    @staticmethod
    def _cpl_malformed(r: Request, h: dict) -> bool:
        """The Successful-completion malformed checks (REQUESTER,
        Completion matching)."""
        data = h["data"]
        if r.op == CFG_RD:
            return not data or h["length"] != 1
        if r.op == CFG_WR:
            return bool(data)
        n = len(data) // 4
        if not n or h["bc"] != r.left or h["la"] != r.nxt & 0x7F:
            return True
        span = (r.nxt & ~3) + 4 * n - r.nxt
        return span >= r.left + 4 or (span < r.left and (r.nxt + span) % RCB != 0)

    # ------------------------------------------------------ completer
    def _completer(self, now: int) -> None:
        if self.cpl_busy or not self.npq:
            return
        kind, h, dc = self.npq.popleft()
        self.rel.append((now + 1, dll.NP, dc))
        if kind == "drop":
            return
        cid = self.hdr.state["primary"] << 8
        mem = h["type"] in (tlm.T_MEM, tlm.T_MEMLK)
        if kind in ("ur", "urlk"):
            bc = tlm.byte_count(h["length"], h["fbe"], h["lbe"]) if mem else 4
            la = (h["addr"] + tlm._lo(h["fbe"])) & 0x7F if mem else 0
            self.cpl_out.append([now + 1, tlm.cpl_tlp(cid, tlm.UR, bc, h["rid"], h["tag"], la,
                                                      locked=kind == "urlk", tc=h["tc"],
                                                      attr=h["attr"])])
        else:
            n, fbe, lbe = h["length"], h["fbe"], h["lbe"]
            off = h["addr"] - self.p["host_mem_base"]
            data = tlm.enabled_data(self._mem_read(off, n), n, fbe, lbe)
            t = now + 1
            for dw, k, bc, la in tlm.split_read(h["addr"], n, fbe, lbe):
                t += k                                   # its DWs read, one per cycle
                self.cpl_out.append([t, tlm.cpl_tlp(cid, tlm.SC, bc, h["rid"], h["tag"], la,
                                                    data[4 * dw:4 * (dw + k)], tc=h["tc"],
                                                    attr=h["attr"])])
        self.cpl_busy = True

    # ------------------------------------------------------- transmit
    def _transmit(self, now: int) -> None:
        """Priority: rc's posted write, then a completion, then rc's
        non-posted request; a head needs its link credits."""
        d = self.dll
        heads = []
        if self.txp and self.txp[0].ready <= now:
            heads.append(("p", self.txp[0].tlp))
        if self.cpl_out and self.cpl_out[0][0] <= now:
            heads.append(("cpl", self.cpl_out[0][1]))
        if self.txnp and self.txnp[0].ready <= now:
            heads.append(("np", self.txnp[0].tlp))
        for which, tlp in heads:
            cls, hc, dc = tlm.credits(tlp)
            if d.avail(cls, 0) < hc or d.avail(cls, 1) < dc:
                continue
            if not d.offer(tlp, now):
                return
            self.counts["tlps_tx"] += 1
            if which == "cpl":
                self.cpl_out.popleft()
                self.counts["completions_tx"] += 1
                if not self.cpl_out:
                    self.cpl_busy = False
            elif which == "p":
                r = self.txp.popleft()
                r.sent = now
                self._beat(now + -(-len(tlp) // 4), OK)     # the cycle after eop taken
                self._finish(now, OK)
            else:
                r = self.txnp.popleft()
                r.sent = now
                self.tag = (self.tag + 1) % TAG_MOD
            return

    def quiet(self) -> bool:
        """Only the completion timer runs: a request sent, nothing else to
        do (lets a standalone run skip to the timeout)."""
        return (self.req is not None and self.req.sent is not None and not self.rsp
                and not self.npq and not self.cpl_out and not self.rel
                and not self.txp and not self.txnp)


# --------------------------------------------------------------- rc_seq

# rc_seq ops (rc_seq MAS, Commands); REQ is 0x10 + the tl-seq op.
S_FAIL, S_EXPECT, S_WAIT_MSI, S_WAIT_LINK, S_HOT_RESET, S_JUMP, S_DONE = range(7)
S_REQ = 0x10
ALWAYS, DATA_EQ, DATA_NE, STATUS_EQ, STATUS_NE = range(5)
FULL = 0xFFFF_FFFF
# (name, lsb, width) of a 128-bit step (rc_seq MAS, Step format)
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


def decode(w: int) -> dict:
    return {name: w >> lsb & ((1 << width) - 1) for name, lsb, width in FIELDS}


def cfg_addr(bus: int, dev: int, fn: int, reg: int) -> int:
    """The CFG_* address packer: bus [27:20], device [19:15], function
    [14:12], DW index [11:2] from the byte offset ``reg``."""
    return bus << 20 | dev << 15 | fn << 12 | (reg & 0xFFC)


def req(op: int, addr: int, n: int = 1, fbe: int = 0xF, lbe: int | None = None,
        data: int = 0, chk: bool = True, status: int = OK) -> int:
    if lbe is None:
        lbe = 0xF if n > 1 else 0
    return encode(op=S_REQ + op, addr=addr, len=n, fbe=fbe, lbe=lbe, data=data,
                  chk=int(chk), status=status)


def expect(value: int, mask: int = FULL, idx: int = 0) -> int:
    return encode(op=S_EXPECT, data=value & mask, mask=mask, idx=idx)


def wait_msi(timeout: int = WAIT_TIMEOUT) -> int:
    return encode(op=S_WAIT_MSI, data=timeout)


def wait_link(timeout: int = WAIT_TIMEOUT) -> int:
    return encode(op=S_WAIT_LINK, data=timeout)


def hot_reset(timeout: int = WAIT_TIMEOUT, hold: int = HOT_RESET_HOLD) -> int:
    return encode(op=S_HOT_RESET, data=timeout, addr=hold)


def jump(cond: int, target: int, limit: int = 0, value: int = 0, mask: int = FULL,
         idx: int = 0, status: int = 0) -> int:
    return encode(op=S_JUMP, cond=cond, addr=limit << 16 | target, data=value & mask,
                  mask=mask, idx=idx, status=status)


def done() -> int:
    return encode(op=S_DONE)


def fail() -> int:
    return 0


class RcSeq:
    """pcie_rc_seq: the interpreter of ``rom`` (a list of 128-bit words,
    padded with FAIL). ``step`` runs one clk_i cycle against ``tl`` (the
    rc_tl command, result and MSI interfaces) and the link levels."""

    def __init__(self, rom: list[int]):
        if len(rom) > ROM_STEPS:
            raise ValueError(f"program of {len(rom)} steps, ROM holds {ROM_STEPS}")
        self.rom = list(rom) + [0] * (ROM_STEPS - len(rom))
        self.R: list[int | None] = [None] * 32
        self.done = self.passed = False
        self.fail_step = 0
        self.hot_reset_req = False
        self.trace: list[tuple] = []             # (pc, op, first cycle, last cycle)
        self.state = "idle"
        self._gen = None
        self.now, self.tl, self.dl_up, self.link_up = 0, None, False, False

    def start(self) -> None:
        """A rising start_i with no run in progress."""
        if self._gen is None:
            self.done = self.passed = False
            self.fail_step = 0
            self._gen = self._run()

    def step(self, now: int, tl, dl_up: bool, link_up: bool) -> None:
        if self._gen is None:
            return
        self.now, self.tl, self.dl_up, self.link_up = now, tl, dl_up, link_up
        try:
            next(self._gen)
        except StopIteration:
            self._gen = None

    def _end(self, pc: int, ok: bool) -> None:
        self.done, self.passed = True, ok
        self.fail_step = 0 if ok else pc
        self.hot_reset_req = False
        self.state = "idle"

    def _reg(self, i: int) -> int:
        v = self.R[i]
        if v is None:
            raise RuntimeError(f"the program reads R[{i}], never written")
        return v

    def _run(self):
        pc, S, L = 0, OK, 0
        while True:
            f = decode(self.rom[pc] if pc < ROM_STEPS else 0)
            op, t0, nxt, bad = f["op"], self.now, pc + 1, False
            self.state = "step"
            yield                                    # FETCH
            yield                                    # EXEC
            if op == S_DONE:
                self.trace.append((pc, op, t0, self.now))
                self._end(pc, True)
                return
            if op == S_EXPECT:
                yield                                # READ
                bad = ((self._reg(f["idx"]) ^ f["data"]) & f["mask"]) != 0
            elif op == S_JUMP:
                cond, target, limit = f["cond"], f["addr"] & 0xFFFF, f["addr"] >> 16
                if cond > STATUS_NE:
                    bad = True
                else:
                    if cond in (DATA_EQ, DATA_NE):
                        yield                        # READ
                        eq = ((self._reg(f["idx"]) ^ f["data"]) & f["mask"]) == 0
                        taken = eq == (cond == DATA_EQ)
                    elif cond in (STATUS_EQ, STATUS_NE):
                        taken = (S == f["status"]) == (cond == STATUS_EQ)
                    else:
                        taken = True
                    if cond == ALWAYS:
                        nxt = target
                    elif taken and target <= pc:
                        if L == limit:
                            bad = True
                        else:
                            L, nxt = L + 1, target
                    else:
                        L = 0
                        if taken:
                            nxt = target
            elif op in (S_WAIT_MSI, S_WAIT_LINK):
                self.state = "wait"
                ok = False
                for _ in range(f["data"] + 1):
                    if (self.tl.msi if op == S_WAIT_MSI else self.dl_up):
                        ok = True
                        break
                    yield
                bad = not ok
                if ok and op == S_WAIT_MSI:
                    off, data = self.tl.take_msi()
                    self.R[0], self.R[1] = data, off
                    yield                            # R[1] written in the next cycle
            elif op == S_HOT_RESET:
                self.hot_reset_req = True
                self.state = "wait"
                ok = False
                for _ in range(f["data"] + 1):
                    if not self.link_up:
                        ok = True
                        break
                    yield
                bad = not ok
                if ok:
                    for _ in range(f["addr"] & 0xFFFF):
                        yield
                self.hot_reset_req = False
            elif op >= S_REQ:
                cmd = {"op": op - S_REQ, "addr": f["addr"], "len": f["len"], "fbe": f["fbe"],
                       "lbe": f["lbe"], "data": f["data"]}
                self.state = "offer"
                while not self.tl.offer(cmd, self.now):
                    yield
                self.state = "rsp"
                k = 0
                while True:
                    yield
                    beat = self.tl.pop_rsp(self.now)
                    if beat is None:
                        continue
                    if k < 32:
                        self.R[k] = beat[1]
                    k += 1
                    if beat[2]:
                        S = beat[0]
                        break
                bad = bool(f["chk"]) and S != f["status"]
            else:
                bad = True                           # FAIL and the reserved ops
            self.trace.append((pc, op, t0, self.now))
            if bad:
                self._end(pc, False)
                return
            pc = nxt


# ------------------------------------------------------------- programs

class Asm:
    """Builds a program: steps in order, labels, and jumps to labels
    resolved at the end (targets derived, never hand-counted)."""

    def __init__(self):
        self.steps: list = []
        self.labels: dict[str, int] = {}
        self.sections: dict[str, list[int]] = {}

    def __call__(self, *words) -> None:
        self.steps.extend(words)

    def label(self, name: str) -> None:
        self.labels[name] = len(self.steps)

    def section(self, name: str) -> None:
        """Mark the start of a section (its steps run to the next mark)."""
        self.sections.setdefault(name, []).append(len(self.steps))

    def jump(self, cond: int, to: str, **kw) -> None:
        self.steps.append(lambda labels: jump(cond, labels[to], **kw))

    def rom(self) -> list[int]:
        return [s(self.labels) if callable(s) else s for s in self.steps]


# Address plan (PROGRAMS, Address plan) at rc and core defaults.
BUS, EP = 1, (1, 0, 0)
WINDOW = 0x1000_1000                 # DW 8: Memory Base and Limit 0x100
BAR0, BAR2 = 0x1000_0000, 0x1000_1000
NOT_DECODED, OUTSIDE = 0x1000_8000, 0x2000_0000
MSI_DATA = 0x0001
DMA_SRC, DMA_DST, RUN_FLAG = 0x100, 0x200, 0xFFC
DOORBELL = (0xF00, 0xF04, 0xF08, 0xF0C)
NO_ANSWER = 0xF10


def _ep(reg: int, bus: int = BUS, dev: int = 0) -> int:
    return cfg_addr(bus, dev, 0, reg)


def mmio_pattern(k: int) -> int:
    return (0xA500_0000 | k * 0x0001_0203) & FULL


def dma_pattern(k: int) -> int:
    return (0xD000_0000 | k * 0x0101_0101) & FULL


def enum_section(a: Asm, rc: dict, ep: cfgm.Params) -> None:
    """ENUM, E1 to E10 (PROGRAMS, Enumeration)."""
    a.section("E1")
    a(req(HDR_WR, 6 * 4, data=0x0001_0100, fbe=0x7), req(HDR_WR, 8 * 4, data=WINDOW),
      req(HDR_WR, 1 * 4, data=0x0000_0006, fbe=0x1))
    a.section("E2")
    for dw, want in ((0, rc["vendor_id"] | rc["device_id"] << 16), (1, 0x0000_0006),
                     (2, 0x0604_0000 | rc["revision_id"]), (6, 0x0001_0100), (8, WINDOW)):
        a(req(HDR_RD, 4 * dw), expect(want))
    a.section("E3")
    a(wait_link())
    a.section("E4")
    a(req(CFG_RD, _ep(0x00)), expect(ep.vendor_id | ep.device_id << 16),
      req(CFG_RD, _ep(0x0C)), expect(0, 0x007F_0000),
      req(CFG_RD, _ep(0x04)), expect(1 << 20, 1 << 20))
    a.section("E5")
    a(req(CFG_RD, _ep(0x34)), expect(0x40, 0xFF))
    a.section("E6")
    for reg, want in ((0x40, 0x5001), (0x50, 0x6005), (0x60, 0x7011), (0x70, 0x0010)):
        a(req(CFG_RD, _ep(reg)), expect(want, 0xFFFF))
    a.section("E7")
    sizes = {0: (~((1 << ep.bar0_addr_w) - 1)) & FULL, 2: (~(cfgm.BAR2_BYTES - 1)) & FULL}
    for i in range(6):
        a(req(CFG_WR, _ep(0x10 + 4 * i), data=FULL), req(CFG_RD, _ep(0x10 + 4 * i)),
          expect(sizes.get(i, 0)))
    a.section("E8")
    for reg, bar in ((0x10, BAR0), (0x18, BAR2)):
        a(req(CFG_WR, _ep(reg), data=bar), req(CFG_RD, _ep(reg)), expect(bar))
    a.section("E9")
    a(req(CFG_WR, _ep(0x54), data=rc["msi_addr"]), req(CFG_WR, _ep(0x58), data=0),
      req(CFG_WR, _ep(0x5C), data=MSI_DATA), req(CFG_WR, _ep(0x50), data=1 << 16, fbe=0x4),
      req(CFG_RD, _ep(0x50)), expect(1 << 16, 1 << 16))
    a.section("E10")
    a(req(CFG_WR, _ep(0x04), data=0x06, fbe=0x1), req(CFG_RD, _ep(0x04)), expect(0x6, 0x6))


def mmio_section(a: Asm) -> None:
    a.section("M1")
    a(*(req(MEM_WR, BAR0 + 4 * k, data=mmio_pattern(k)) for k in range(32)))
    a.section("M2")
    a(req(MEM_RD, BAR0, 32), *(expect(mmio_pattern(k), idx=k) for k in range(32)))
    a.section("M3")
    a(req(MEM_RD, BAR0 + 0x38, 4), *(expect(mmio_pattern(14 + k), idx=k) for k in range(4)))
    a.section("M4")
    a(req(MEM_WR, BAR0 + 0x80, data=0x1234_5678),
      req(MEM_WR, BAR0 + 0x80, data=FULL, fbe=0x3), req(MEM_RD, BAR0 + 0x80),
      expect(0x1234_FFFF))
    a.section("M5")
    a(req(ERR_RD, 0), expect(0, ERR_MASK))


def doorbell(a: Asm, src: int, dst: int, nbytes: int, ring: int = 1) -> None:
    a(req(MEM_WR, BAR0 + DOORBELL[0], data=src), req(MEM_WR, BAR0 + DOORBELL[1], data=dst),
      req(MEM_WR, BAR0 + DOORBELL[2], data=nbytes))
    a(*(req(MEM_WR, BAR0 + DOORBELL[3], data=1) for _ in range(ring)))


def dma_section(a: Asm, nbytes: int = 64) -> None:
    dws = nbytes // 4
    a.section("D1")
    a(*(req(HOST_WR, DMA_SRC + 4 * k, data=dma_pattern(k)) for k in range(dws)),
      *(req(HOST_WR, DMA_DST + 4 * k, data=0) for k in range(dws)), req(ERR_CLR, 0, data=ERR_MASK))
    a.section("D2")
    doorbell(a, DMA_SRC, DMA_DST, nbytes)
    a.section("D3")
    a(wait_msi(), expect(MSI_DATA), expect(0, idx=1))
    a.section("D4")
    for k in range(dws):
        a(req(HOST_RD, DMA_DST + 4 * k), expect(dma_pattern(k)))
    a.section("D5")
    a(req(ERR_RD, 0), expect(0, ERR_MASK))


def poll_err(a: Asm, name: str, bits: int) -> None:
    """ERR_RD, then a backward DATA_NE jump to it until ``bits`` are set."""
    a.label(name)
    a(req(ERR_RD, 0))
    a.jump(DATA_NE, name, limit=LOOP_LIMIT, value=bits, mask=bits)


def errors_program(a: Asm, rc: dict, ep: cfgm.Params) -> None:
    """ERRORS, R1 to R14 (PROGRAMS, Errors and recovery)."""
    a.section("R1")
    a(req(HOST_WR, RUN_FLAG, data=0))
    a.label("ENUM")
    enum_section(a, rc, ep)
    a.section("R2")
    a(req(HOST_RD, RUN_FLAG))
    a.jump(DATA_EQ, "R11", value=1)
    a.jump(DATA_EQ, "R14", value=2)
    a.section("R3")
    a(req(CFG_WR, _ep(0x78), data=0x1F, fbe=0x1), req(ERR_CLR, 0, data=ERR_MASK))
    a.section("R4")
    a(req(MEM_WR, NOT_DECODED, data=0x5A5A_5A5A), req(MEM_RD, NOT_DECODED, status=UR))
    a.section("R5")
    poll_err(a, "R5", 0x181)
    a(expect(0x181, ERR_MASK), req(ERR_CLR, 0, data=ERR_MASK))
    a.section("R6")
    a(req(MEM_RD, OUTSIDE, status=LOCAL_UR), req(CFG_RD, _ep(0, dev=1), status=LOCAL_UR),
      req(CFG_RD, _ep(0, bus=2), status=LOCAL_UR))
    a.section("R7")
    doorbell(a, OUTSIDE, DMA_DST, 4)
    poll_err(a, "R7", 1 << E_INBOUND_UR)
    a.section("R8")
    doorbell(a, DMA_SRC, DMA_DST, 4, ring=MSI_DEPTH + 1)
    poll_err(a, "R8", 1 << E_MSI_DROP)
    a(*(wait_msi() for _ in range(MSI_DEPTH)))
    a.section("R9")
    a(req(MEM_RD, BAR0 + NO_ANSWER, status=TIMEOUT), req(ERR_RD, 0), expect(0x1808, ERR_MASK),
      req(ERR_CLR, 0, data=ERR_MASK), req(ERR_RD, 0), expect(0, ERR_MASK))
    a.section("R10")
    a(req(HOST_WR, RUN_FLAG, data=1), hot_reset(), wait_link())
    a.jump(ALWAYS, "ENUM")
    a.label("R11")
    a.section("R11")
    a(req(MEM_WR, BAR0 + 0x40, data=0x0BAD_F00D), req(MEM_RD, BAR0 + 0x40), expect(0x0BAD_F00D),
      req(HOST_WR, RUN_FLAG, data=2))
    a.section("R12")
    a.label("R12")
    a(req(MEM_RD, BAR0, chk=False))
    a.jump(STATUS_EQ, "R12", limit=LOOP_LIMIT, status=OK)
    a.jump(STATUS_EQ, "R13", status=LINK_DOWN)
    a(fail())
    a.label("R13")
    a.section("R13")
    a(wait_link())
    a.jump(ALWAYS, "ENUM")
    a.label("R14")
    a.section("R14")
    a(req(MEM_WR, BAR0 + 0x44, data=0x600D_CAFE), req(MEM_RD, BAR0 + 0x44), expect(0x600D_CAFE),
      done())


PROGRAMS = ("ENUM", "MMIO", "DMA_MSI", "ERRORS")


def program(name: str, rc: dict | None = None, ep: cfgm.Params | None = None,
            dma_bytes: int = 64) -> Asm:
    """The M4 program ``name`` (rc_seq MAS, Programs) for ``rc`` and
    ``core`` parameters (defaults: both at their defaults)."""
    rc, ep = rc or params_of(), ep or cfgm.Params()
    a = Asm()
    if name == "ERRORS":
        errors_program(a, rc, ep)
        return a
    if name not in PROGRAMS:
        raise ValueError(f"program {name!r}")
    enum_section(a, rc, ep)
    if name == "MMIO":
        mmio_section(a)
    elif name == "DMA_MSI":
        dma_section(a, dma_bytes)
    a.section("DONE")
    a(done())
    return a


# ------------------------------------------------------------ endpoint

class EpApp(corem.App):
    """The endpoint application the programs need (PROGRAMS, Boundary),
    on core's application interface: BAR0 offsets 0x000 to 0xEFF as
    memory; the DMA doorbell (0xF00 source, 0xF04 destination, 0xF08
    bytes, a write to 0xF0C starts a copy: a bus-master read, then a write,
    then MSI vector 0; doorbells queue, a failed read ends its copy); a
    read of 0xF10 never answered."""

    def __init__(self, rng: random.Random, latency: int = 16):
        super().__init__(rng, latency=latency)
        self.bar0 = bytearray(0xF00)
        self.regs = {o: 0 for o in DOORBELL[:3]}
        self.doorbells: deque = deque()
        self.copy: dict | None = None
        self.copies = {"done": 0, "failed": 0, "bytes": 0}
        self.copy_log: list[tuple] = []          # (doorbell cycle, msi raised cycle, bytes)

    def _wr(self, off: int, data: int, be: int, now: int) -> None:
        m = _byte_mask(be)
        if off < len(self.bar0):
            old = int.from_bytes(self.bar0[off:off + 4], "little")
            self.bar0[off:off + 4] = ((old & ~m) | (data & m)).to_bytes(4, "little")
        elif off in self.regs:
            self.regs[off] = (self.regs[off] & ~m) | (data & m)
        elif off == DOORBELL[3] and be:
            self.doorbells.append((self.regs[0xF00], self.regs[0xF04], self.regs[0xF08], now))

    def _rd(self, off: int) -> int:
        if off < len(self.bar0):
            return int.from_bytes(self.bar0[off:off + 4], "little")
        return self.regs.get(off, 0)

    def cq_take(self, pkt: dict, now: int) -> None:
        self.cq_got.append(pkt)
        off, n = pkt["addr"], pkt["len"]
        if pkt["write"]:
            for i in range(n):
                be = pkt["fbe"] if i == 0 else (pkt["lbe"] if i == n - 1 else 0xF)
                self._wr(off + 4 * i, int.from_bytes(pkt["data"][4 * i:4 * i + 4], "little"),
                         be, now)
        elif not off <= NO_ANSWER < off + 4 * n:
            data = [self._rd(off + 4 * i) for i in range(n)]
            self.reads.append([now + self.latency, {"handle": pkt["handle"], "data": data}])

    def rc_take(self, pkt: dict, now: int) -> None:
        super().rc_take(pkt, now)
        c = self.copy
        if c is None or c["state"] != "read":
            return
        if pkt["status"] != tlm.RC_OK:
            self.copies["failed"] += 1
            self.copy = None
            return
        c["data"] += pkt["data"]
        if pkt["done"]:
            dws = len(c["data"]) // 4
            self.rq.append({"write": True, "addr": c["dst"], "len": dws, "fbe": 0xF,
                            "lbe": 0xF if dws > 1 else 0,
                            "data": [int.from_bytes(c["data"][4 * i:4 * i + 4], "little")
                                     for i in range(dws)]})
            c["state"] = "write"

    def step(self, now: int) -> None:
        if self.copy is None and self.doorbells:
            src, dst, n, rung = self.doorbells.popleft()
            dws = max(1, min(n, tlm.MAX_PAYLOAD) // 4)
            self.copy = {"src": src, "dst": dst, "data": b"", "state": "read", "rung": rung}
            self.rq.append({"write": False, "addr": src, "len": dws, "fbe": 0xF,
                            "lbe": 0xF if dws > 1 else 0, "tag": 0})
        c = self.copy
        if c is not None and c["state"] == "write" and not self.rq:
            self.irqs.append(0)
            c["state"] = "irq"
        elif c is not None and c["state"] == "irq" and not self.irqs:
            self.copies["done"] += 1
            self.copies["bytes"] += len(c["data"])
            self.copy_log.append((c["rung"], now, len(c["data"])))
            self.copy = None


# --------------------------------------------------------------- benches

class NullLink:
    """A link that is up, has infinite credits, takes every TLP and never
    answers (standalone rc_tl runs: the completion timeout)."""

    def __init__(self):
        self.dl_up, self.delivered, self.sent = True, [], []
        self.dl_up_at = 0

    def avail(self, cls: int, f: int) -> int:
        return dll.MODS[f] - 1

    def offer(self, tlp: bytes, now: int) -> bool:
        self.sent.append((now, tlp))
        return True

    def release(self, cls: int, data: int) -> None:
        pass


class Bench:
    """rc (rc_tl, rc_seq) back to back with core over the link model, or
    alone over a ``NullLink`` when ``endpoint`` is False."""

    def __init__(self, rng: random.Random, rom: list[int], rc: dict | None = None,
                 endpoint: bool = True, hot_reset_exit: int = HOT_RESET_EXIT):
        self.p = rc or params_of()
        self.seq = RcSeq(rom)
        self.endpoint = endpoint
        self.hot_reset_exit = hot_reset_exit
        self.phy_up = True
        self.hot_resets = 0
        self.down_until = -1
        self.hr_since: int | None = None
        self.inject: list[int] = []              # cycles at which to take the link down
        self.inject_cycles = 200
        self.now = 0
        if endpoint:
            p = self.p
            fc = {"ph": p["fc_ph"], "pd": p["fc_pd"], "nph": p["fc_nph"], "npd": p["fc_npd"],
                  "cplh": 0, "cpld": 0}
            self.a = dll.Dll("rc", fc, release_port=True)
            self.b = dll.Dll("core", dict(dll.DEFAULT_FC, **corem.FC), release_port=True)
            self.pair = link.LinkPair(self.a, self.b, link.Line(rng))
            self.app = EpApp(rng)
            self.core = corem.Core(self.b, cfgm.Params(), corem.FC)
            self.tl = RcTl(self.p, self.a)
        else:
            self.a = NullLink()
            self.tl = RcTl(self.p, self.a)
        self.seq.start()

    def _phy(self) -> None:
        """link_up_o: Hot Reset (hot_reset_req held, then the link's timer)
        and injected link downs."""
        now, s = self.now, self.seq
        if s.hot_reset_req:
            if self.hr_since is None:
                self.hr_since = now
                self.hot_resets += 1
            if now - self.hr_since >= HOT_RESET_ENTRY:
                self.down_until = max(self.down_until, now + self.hot_reset_exit)
        else:
            self.hr_since = None
        if self.inject and now >= self.inject[0]:
            self.inject.pop(0)
            self.down_until = max(self.down_until, now + self.inject_cycles)
        self.phy_up = now >= self.down_until and not (s.hot_reset_req and self.hr_since is not None
                                                       and now - self.hr_since >= HOT_RESET_ENTRY)

    def step(self) -> None:
        self._phy()
        self.tl.step(self.now)
        self.seq.step(self.now, self.tl, self.a.dl_up, self.phy_up)
        if self.endpoint:
            self.app.step(self.now)
            self.core.step(self.now, self.app)
            self.pair.step(link=(self.phy_up, self.phy_up))
            self.now = self.pair.now
        else:
            self.now += 1

    def run(self, limit: int) -> int:
        while not self.seq.done and self.now < limit:
            if not self.endpoint and self.seq.state == "rsp" and self.tl.quiet():
                self.now = max(self.now, self.tl.req.sent + self.tl.timeout)
            self.step()
        if not self.seq.done:
            raise RuntimeError(f"program not done after {limit} cycles")
        return self.now


# ------------------------------------------------------------------ perf

def _int(w: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = w.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"workload {key}={v!r} not an integer in {lo}..{hi}")
    return v


def _check_keys(w: dict, keys: set) -> None:
    extra = set(w) - keys - {"kind"}
    if extra:
        raise ValueError(f"unknown workload keys {sorted(extra)}")


def _rc_params(w: dict) -> dict:
    return params_of({k: w[k] for k in DEFAULTS if k in w})


def _span(b: Bench, asm: Asm, first: str, last: str) -> tuple[int, int]:
    """First and last cycle of the steps from section ``first`` to the end
    of section ``last`` (the first pass of each)."""
    marks = sorted((v[0], k) for k, v in asm.sections.items())
    lo = asm.sections[first][0]
    after = [i for i, k in marks if i > asm.sections[last][0]]
    hi = after[0] if after else len(asm.steps)
    cyc = [(t0, t1) for pc, _, t0, t1 in b.seq.trace if lo <= pc < hi]
    return cyc[0][0], cyc[-1][1]


def _endpoint_log(b: Bench, op: int, addr: int) -> dict:
    return next(x for x in b.tl.log if x["op"] == op and x["addr"] == addr)


def run_program(w: dict, seed: int) -> dict[str, float]:
    """One program from start_i to done_o, rc and core back to back."""
    _check_keys(w, set(DEFAULTS) | {"program", "max_cycles", "dma_bytes", "hot_reset_cycles",
                                    "link_down_after"})
    name = w.get("program")
    if name not in PROGRAMS:
        raise ValueError(f"workload program {name!r}: one of {PROGRAMS}")
    rc = _rc_params(w)
    dma_bytes = _int(w, "dma_bytes", 64, 4, 128)
    if dma_bytes % 4:
        raise ValueError("dma_bytes: a DW multiple")
    asm = program(name, rc, dma_bytes=dma_bytes)
    rng = random.Random(seed)
    b = Bench(rng, asm.rom(), rc, hot_reset_exit=_int(w, "hot_reset_cycles", HOT_RESET_EXIT,
                                                       100, 1_000_000))
    after = _int(w, "link_down_after", 300, 1, 100_000)
    limit = _int(w, "max_cycles", 2_000_000, 1000, 50_000_000)
    flagged = False
    while not b.seq.done and b.now < limit:
        if name == "ERRORS" and not flagged and b.tl.mem[RUN_FLAG] == 2:
            flagged = True                               # PROGRAMS boundary: link down in R12
            b.inject.append(b.now + after)
        b.step()
    if not b.seq.done:
        raise RuntimeError(f"program {name} not done after {limit} cycles")
    s, tl = b.seq, b.tl
    ep_reqs = len(tl.log)
    met = {
        "run_passed": float(s.passed),
        "fail_step": float(s.fail_step),
        "run_cycles": float(b.now),
        "steps_executed": float(len(s.trace)),
        "endpoint_requests": float(ep_reqs),
        "unexpected_completions": float(tl.events[E_UNEXPECTED]),
        "malformed_received": float(tl.events[E_MALFORMED]),
    }
    if not s.passed:
        return met
    if name != "ERRORS":
        t_link = _span(b, asm, "E3", "E3")[1]
        met["enumeration_cycles"] = float(_span(b, asm, "E4", "E10")[1] - t_link)
    if name == "MMIO":
        t0, t1 = _span(b, asm, "M1", "M1")
        met["mmio_write_bytes_per_cycle"] = 128 / (t1 - t0)
        rd = _endpoint_log(b, MEM_RD, BAR0)
        t0, t1 = _span(b, asm, "M2", "M2")
        met["mmio_read_bytes_per_cycle"] = 128 / (rd["end"] - rd["taken"])
        met["mmio_read_latency_cycles"] = float(rd["end"] - rd["taken"])
        met["mmio_read_completions"] = float(rd["cpls"])
        met["mmio_read_step_cycles"] = float(t1 - t0)
    elif name == "DMA_MSI":
        rung, msi_at, nbytes = b.app.copy_log[0]
        captured = tl.msi_log[0][0]
        met["dma_bytes"] = float(nbytes)
        met["dma_copy_cycles"] = float(captured - rung)
        met["dma_bytes_per_cycle"] = nbytes / (captured - rung)
        met["msi_captured"] = float(len(tl.msi_log))
        met["host_bytes_written"] = float(tl.counts["host_bytes_written"])
    elif name == "ERRORS":
        tos = [x for x in tl.log if x["status"] == TIMEOUT]
        met["timeouts"] = float(len(tos))
        met["timeout_cycles"] = float(tos[0]["end"] - tos[0]["sent"]) if tos else 0.0
        met["hot_resets"] = float(b.hot_resets)
        met["link_downs"] = float(tl.counts["link_downs"])
        met["enumerations"] = float(sum(1 for pc, *_ in s.trace if pc == asm.sections["E10"][0]))
        met["link_down_results"] = float(sum(1 for x in tl.log if x["status"] == LINK_DOWN))
        met["msi_dropped"] = float(tl.events[E_MSI_DROP])
        met["inbound_ur"] = float(tl.events[E_INBOUND_UR])
    return met


def run_timeout(w: dict, seed: int) -> dict[str, float]:
    """A memory read that is never answered, rc_tl alone over a NullLink:
    the completion timeout in cycles and in microseconds at ``clk_mhz``."""
    del seed                                             # no random choice
    _check_keys(w, {"cpl_timeout_div", "clk_mhz", "reads"})
    rc = _rc_params({"cpl_timeout_div": w.get("cpl_timeout_div", 1)})
    mhz = _int(w, "clk_mhz", CLK_MHZ, 1, 1000)
    reads = _int(w, "reads", 1, 1, 64)
    a = Asm()
    a(req(HDR_WR, 6 * 4, data=0x0001_0100, fbe=0x7), req(HDR_WR, 8 * 4, data=WINDOW),
      req(HDR_WR, 1 * 4, data=0x6, fbe=0x1))
    for _ in range(reads):
        a(req(MEM_RD, BAR0 + NO_ANSWER, status=TIMEOUT))
    a(req(ERR_RD, 0), expect(1 << E_TIMEOUT, ERR_MASK), done())
    b = Bench(random.Random(0), a.rom(), rc, endpoint=False)
    b.run(reads * (CPL_TIMEOUT + 1000) + 1000)
    tos = [x for x in b.tl.log if x["status"] == TIMEOUT]
    # From the sop taken to the expiry: N (the TIMEOUT beat follows it).
    cyc = [x["end"] - x["sent"] for x in tos]
    return {
        "run_passed": float(b.seq.passed),
        "timeouts": float(len(tos)),
        "timeout_cycles_min": float(min(cyc, default=0)),
        "timeout_cycles_max": float(max(cyc, default=0)),
        "timeout_us_min": min(cyc, default=0) / mhz,
        "timeout_us_max": max(cyc, default=0) / mhz,
        "tags_used": float(len({tlm.parse(t)["tag"] for _, t in b.a.sent})),
    }


def run_address_map(w: dict, seed: int) -> dict[str, float]:
    del seed                                             # closed form: no random choice
    params = {k: DEFAULTS[k] for k in ("host_mem_addr_w", "host_mem_base", "msi_addr")}
    for key, value in w.items():
        if key == "kind":
            continue
        if key not in params or not isinstance(value, int):
            raise ValueError(f"workload {key}: {value!r}")
        params[key] = value
    return {k: float(v) for k, v in address_map(params).items()}


def perf(workload: dict, seed: int) -> dict[str, float]:
    kind = workload.get("kind")
    if kind == "address_map":
        return run_address_map(workload, seed)
    if kind == "program":
        return run_program(workload, seed)
    if kind == "completion_timeout":
        return run_timeout(workload, seed)
    raise ValueError(f"workload kind {kind!r}")
