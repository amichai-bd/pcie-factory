"""TLP-level endpoint for the pcie_rc_tl testbench (wiki/src/dv/rc_tl/DV_PLAN.md).

It reads each request TLP that pcie_rc_tl sends and returns its
completions, built from the TLP formats and completion rules of the specs
(wiki/src/arch/tl/TLP.md, wiki/src/rtl/rc_tl/INBOUND.md#completer), not from
the RTL. By default every non-posted request is answered with Successful
completions: configuration reads from a register map, memory reads from a
memory image, one completion each. A test replaces the answer to the next
requests with `plan`: a function of the decoded request that returns the
TLPs to send (any completion, well formed or not, split or late, or none).

`tlp` builds the endpoint's own requests and messages for the inbound side
(any Fmt, Type and field, a 3- or 4-DW header, data and a digest), and
`Cpl.decode` reads a completion the DUT's completer sends.
"""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
from pcie_rc_tl_model import (CS_SC, Cmd, MEM_RD, bytes_of, cpl_tlp,  # noqa: E402
                              dws_of, read_completions)

CFG_RD_T, CFG_WR_T, MEM_RD_T, MEM_WR_T = 0x04, 0x44, 0x00, 0x40
EP_RID = 0x0100          # the endpoint's Requester ID: bus 1, device 0, function 0


def tlp(fmt: int, typ: int, *, addr: int = 0, length: int = 1, fbe: int = 0xF, lbe: int = 0,
        data: list[int] | None = None, rid: int = EP_RID, tag: int = 0, tc: int = 0,
        attr: int = 0, ep: int = 0, td: int = 0, code: int | None = None,
        digest: int = 0xD16E_57ED) -> list[int]:
    """A request or message as the endpoint sends it (TLP.md): Fmt bit 0
    a 4-DW header (address bits 63:0 in bytes 8 to 15), bit 1 with data
    (`data`, Length DWs as given); byte 7 the byte enables, or `code` for a
    message; then a digest DW when TD is 1."""
    b = [0] * (16 if fmt & 1 else 12)
    b[0] = (fmt << 5) | typ
    b[1] = (tc & 7) << 4
    b[2] = (td << 7) | (ep << 6) | ((attr & 3) << 4) | ((length >> 8) & 3)
    b[3] = length & 0xFF
    b[4], b[5], b[6] = (rid >> 8) & 0xFF, rid & 0xFF, tag & 0xFF
    b[7] = code if code is not None else ((lbe << 4) | fbe)
    a = addr.to_bytes(8 if fmt & 1 else 4, "big")
    b[8:8 + len(a)] = list(a)
    dws = dws_of(b) + list(data or [])
    if td:
        dws.append(digest)
    return dws


def mem_wr(addr: int, data: list[int], fbe: int = 0xF, lbe: int | None = None,
           hdr4: bool = False, **kw) -> list[int]:
    """A memory write of len(data) DWs."""
    if lbe is None:
        lbe = 0xF if len(data) > 1 else 0
    return tlp(0b011 if hdr4 else 0b010, 0x00, addr=addr, length=len(data), fbe=fbe, lbe=lbe,
               data=data, **kw)


def mem_rd(addr: int, n: int = 1, fbe: int = 0xF, lbe: int | None = None, hdr4: bool = False,
           locked: bool = False, **kw) -> list[int]:
    """A memory read (or locked read) of n DWs (1024 sent as Length 0)."""
    if lbe is None:
        lbe = 0xF if n > 1 else 0
    return tlp(0b001 if hdr4 else 0b000, 0x01 if locked else 0x00, addr=addr, length=n & 0x3FF,
               fbe=fbe, lbe=lbe, **kw)


def msg(code: int, routing: int = 0b000, data: list[int] | None = None, **kw) -> list[int]:
    """A message (4-DW header), with data when `data` is given."""
    return tlp(0b011 if data else 0b001, 0x10 | routing, code=code,
               length=len(data) if data else 0, data=data, **kw)


@dataclass
class Cpl:
    """A completion as the endpoint decodes it (TLP.md, Completions)."""
    fmt: int
    typ: int
    tc: int
    attr: int
    td: int
    ep: int
    length: int
    cid: int
    status: int
    bcm: int
    bc: int
    rid: int
    tag: int
    la: int
    data: list[int]

    @staticmethod
    def decode(dws: list[int]) -> "Cpl":
        b = bytes_of(dws[:3])
        return Cpl(fmt=b[0] >> 5, typ=b[0] & 0x1F, tc=(b[1] >> 4) & 7, attr=(b[2] >> 4) & 3,
                   td=b[2] >> 7, ep=(b[2] >> 6) & 1, length=((b[2] & 3) << 8) | b[3],
                   cid=(b[4] << 8) | b[5], status=b[6] >> 5, bcm=(b[6] >> 4) & 1,
                   bc=((b[6] & 0xF) << 8) | b[7], rid=(b[8] << 8) | b[9], tag=b[10],
                   la=b[11] & 0x7F, data=list(dws[3:]))


@dataclass
class Req:
    """A request TLP as the endpoint decodes it (TLP.md)."""
    dws: list[int]
    kind: int          # header byte 0: Fmt and Type
    rid: int
    tag: int
    fbe: int
    lbe: int
    length: int
    addr: int          # memory: byte address; configuration: the CFG_* addr
    data: int | None

    @staticmethod
    def decode(dws: list[int]) -> "Req":
        b = bytes_of(dws[:3])
        kind = b[0]
        length = ((b[2] & 3) << 8) | b[3]
        if kind in (CFG_RD_T, CFG_WR_T):
            addr = (b[8] << 20) | (b[9] << 12) | ((b[10] & 0xF) << 8) | (b[11] & 0xFC)
        else:
            addr = (b[8] << 24) | (b[9] << 16) | (b[10] << 8) | (b[11] & 0xFC)
        return Req(dws, kind, (b[4] << 8) | b[5], b[6], b[7] & 0xF, b[7] >> 4,
                   length or 1024, addr, dws[3] if len(dws) > 3 else None)

    @property
    def posted(self) -> bool:
        return self.kind == MEM_WR_T

    def as_cmd(self) -> Cmd:
        """The MEM_RD command it reads, for the completion helpers."""
        return Cmd(MEM_RD, self.addr, self.length, self.fbe, self.lbe)


Plan = Callable[[Req], list[list[int]]]


class Endpoint:
    """Answers requests; queues completions for the link's receive side."""

    def __init__(self) -> None:
        self.cfg: dict[int, int] = {}      # configuration DW by addr[27:2]
        self.mem: dict[int, int] = {}      # memory DW by address >> 2
        self.plans: deque[Plan] = deque()  # answers for the next requests
        self.reqs: list[Req] = []          # every request received, in order

    def plan(self, *plans: Plan) -> None:
        self.plans.extend(plans)

    def cfg_dw(self, addr: int) -> int:
        return self.cfg.get(addr >> 2, (0xC0DE_0000 | (addr & 0xFFFF)) ^ 0x1234_5678)

    def mem_dw(self, dw_addr: int) -> int:
        return self.mem.get(dw_addr, (dw_addr * 0x9E37_79B9) & 0xFFFF_FFFF)

    def receive(self, dws: list[int]) -> list[list[int]]:
        """A request TLP sent by the DUT: the TLPs it answers with."""
        r = Req.decode(dws)
        self.reqs.append(r)
        if r.posted:
            self.mem[r.addr >> 2] = r.data
            return []
        if self.plans:
            return self.plans.popleft()(r)
        return self.default(r)

    def default(self, r: Req) -> list[list[int]]:
        """Successful completions (INBOUND.md, Completer, as an endpoint does)."""
        if r.kind == CFG_RD_T:
            return [cpl_tlp(CS_SC, r.rid, r.tag, [self.cfg_dw(r.addr)], bc=4)]
        if r.kind == CFG_WR_T:
            self.cfg[r.addr >> 2] = r.data
            return [cpl_tlp(CS_SC, r.rid, r.tag, bc=4)]
        return read_completions(r.as_cmd(), r.rid, r.tag, self.read_data(r))

    def read_data(self, r: Req) -> list[int]:
        return [self.mem_dw((r.addr >> 2) + i) for i in range(r.length)]
