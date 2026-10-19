"""TLP-level endpoint for the pcie_rc_tl testbench (wiki/src/dv/rc_tl/DV_PLAN.md).

It reads each request TLP that pcie_rc_tl sends and returns its
completions, built from the TLP formats and completion rules of the specs
(wiki/src/arch/tl/TLP.md, wiki/src/rtl/rc_tl/INBOUND.md#completer), not from
the RTL. By default every non-posted request is answered with Successful
completions: configuration reads from a register map, memory reads from a
memory image, one completion each. A test replaces the answer to the next
requests with `plan`: a function of the decoded request that returns the
TLPs to send (any completion, well formed or not, split or late, or none).
"""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.append(str(Path(__file__).resolve().parents[1] / "models"))
from pcie_rc_tl_model import (CS_SC, Cmd, MEM_RD, bytes_of, cpl_tlp,  # noqa: E402
                              read_completions)

CFG_RD_T, CFG_WR_T, MEM_RD_T, MEM_WR_T = 0x04, 0x44, 0x00, 0x40


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
