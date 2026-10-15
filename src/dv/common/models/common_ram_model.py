"""Cycle model of the shared RAM family (wiki/src/rtl/common/ram/common_*ram*.md).

The oracle is each block's MAS contract, not its RTL: on each rising clock
edge every read port captures the word stored before that edge (read-first),
then every enabled write replaces the lanes its lane enables select. The
variants differ only in what the read port shows:

- ``sync_ram``: the word at the address, every edge;
- ``sync_ram_wt``: the same, with this edge's written lanes replaced by the
  new data (write-through);
- ``sync_ram_held`` and ``sdp_ram``: the word at the read address on an edge
  with the read enable, otherwise the previous output (held);
- ``tdp_ram``: each of ports a and b as ``sync_ram``, both reading before
  either write lands.

Every value is a pair (bits, known): ``known`` marks the bits that are
defined. A word is unknown until written, lane by lane, and a held output is
unknown until the first enabled read; tests compare only known bits.
"""

from __future__ import annotations

from dataclasses import dataclass

KINDS = ("sync_ram", "sync_ram_held", "sync_ram_wt", "sdp_ram", "tdp_ram")


@dataclass
class Port:
    """One port's inputs at one edge. ``addr`` is the read address (and the
    write address of a single-port or true dual-port RAM); ``wr_addr`` is
    the simple dual-port write address."""

    wr_en: int = 0
    lanes: int = 0
    addr: int = 0
    wr_addr: int = 0
    data: int = 0
    rd_en: int = 1


class RamModel:
    def __init__(self, kind: str, width: int, addr_w: int, lanes: int):
        if kind not in KINDS:
            raise ValueError(f"unknown RAM kind {kind!r}")
        if width < 1 or addr_w < 1 or lanes < 1 or width % lanes:
            raise ValueError(f"illegal geometry width={width} addr_w={addr_w} lanes={lanes}")
        self.kind = kind
        self.width = width
        self.depth = 2 ** addr_w
        self.lanes = lanes
        self.lane_w = width // lanes
        self.mask = (1 << width) - 1
        self.mem = [(0, 0)] * self.depth
        ports = 2 if kind == "tdp_ram" else 1
        self.out = [(0, 0)] * ports

    def lane_mask(self, lanes: int) -> int:
        """The bit mask of the lanes set in ``lanes``."""
        lane = (1 << self.lane_w) - 1
        return sum(lane << (i * self.lane_w) for i in range(self.lanes) if lanes >> i & 1)

    def _write(self, p: Port, addr: int) -> int:
        """Apply one port's write; return the mask of bits it wrote."""
        if not p.wr_en:
            return 0
        m = self.lane_mask(p.lanes)
        bits, known = self.mem[addr]
        self.mem[addr] = ((bits & ~m) | (p.data & m), known | m)
        return m

    def edge(self, a: Port, b: Port | None = None) -> None:
        """One rising edge with port inputs ``a`` (and ``b`` for tdp_ram)."""
        k = self.kind
        if k == "tdp_ram":
            if b is None:
                raise ValueError("tdp_ram needs both ports")
            clash = self.lane_mask(a.lanes) & self.lane_mask(b.lanes)
            if a.wr_en and b.wr_en and a.addr == b.addr and clash:
                raise ValueError("both ports write one lane of one word (outside the contract)")
            self.out = [self.mem[a.addr], self.mem[b.addr]]
            self._write(a, a.addr)
            self._write(b, b.addr)
            return
        if k == "sdp_ram":
            if a.rd_en:
                self.out = [self.mem[a.addr]]
            self._write(a, a.wr_addr)
            return
        if k == "sync_ram_held":
            if a.rd_en:
                self.out = [self.mem[a.addr]]
            self._write(a, a.addr)
            return
        old = self.mem[a.addr]
        m = self._write(a, a.addr)
        if k == "sync_ram_wt":
            bits, known = old
            old = ((bits & ~m) | (a.data & m), known | m)
        self.out = [old]

    def outputs(self) -> list[tuple[int, int]]:
        """(bits, known) of each read port after the last edge."""
        return list(self.out)
