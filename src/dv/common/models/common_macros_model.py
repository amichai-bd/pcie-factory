"""Cycle model of the shared vocabulary's register, memory and level-crossing
macros as wired in tb/common_macros_fixture.sv
(wiki/src/dv/common/common_macros.md).

The oracle is the macro contract in the RTL BKM, not the macro text: on each
rising clock edge every register form loads its next value by its own
reset/enable priority, the register arrays copy a whole next-state array,
the memory read ports return the data stored before this edge's write, and
each level synchronizer shows its input two edges later; the one on the
asynchronous reset (COMMON_METAFLOP_ARST) clears at once on assertion. The
reset synchronizer on the asynchronous reset is high at once on assertion
and falls at the second edge after release. Values are 8-bit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEPTH = 8          # memory words (fixture localparam DEPTH)
ARRAY = 4          # register-array elements
RST_VAL = 0xA5     # COMMON_DFF_RST_VAL value
RST_EN_VAL = 0x3C  # COMMON_DFF_RST_EN value
ARST_VAL = 0x5A    # COMMON_DFF_ARST_VAL value
ARST_EN_VAL = 0xC3  # COMMON_DFF_ARST_EN value
META_VAL = 0x96    # COMMON_METAFLOP_VAL reset value
MASK = 0xFF


def array_next(data: int) -> list[int]:
    """The fixture's next-state array: element i is data + i."""
    return [(data + i) & MASK for i in range(ARRAY)]


def lane_word(data: int) -> int:
    """The fixture's 16-bit lane write data: {data, ~data}."""
    return (data << 8) | (~data & MASK)


@dataclass
class Inputs:
    rst: int = 0
    arst: int = 0
    en: int = 0
    data: int = 0
    we: int = 0
    waddr: int = 0
    lanes: int = 0
    re: int = 0
    raddr: int = 0


@dataclass
class MacroModel:
    """Register state after the last rising edge. ``None`` is unknown (a
    form without reset before its first load, or an unwritten word)."""

    dff: int | None = None
    dff_en: int | None = None
    rst: int | None = None
    rst_val: int | None = None
    rst_en: int | None = None
    arst_val: int | None = None
    arst_en: int | None = None
    arr_en: list[int | None] = field(default_factory=lambda: [None] * ARRAY)
    arr_rst_en: list[int | None] = field(default_factory=lambda: [None] * ARRAY)
    mem: list[int | None] = field(default_factory=lambda: [None] * DEPTH)
    wmem: list[int | None] = field(default_factory=lambda: [None] * DEPTH)
    rd: int | None = None
    rd_en: int | None = None
    wrd: int | None = None
    meta: list[int | None] = field(default_factory=lambda: [None, None])
    meta_val: list[int | None] = field(default_factory=lambda: [None, None])
    meta_arst: list[int | None] = field(default_factory=lambda: [None, None])
    rst_sync: list[int | None] = field(default_factory=lambda: [None, None])

    def edge(self, i: Inputs) -> None:
        """Apply one rising clock edge with inputs ``i`` (held across it)."""
        d = i.data & MASK
        self.dff = d
        if i.en:
            self.dff_en = d
        self.rst = 0 if i.rst else d
        self.rst_val = RST_VAL if i.rst else d
        if i.rst:
            self.rst_en = RST_EN_VAL
        elif i.en:
            self.rst_en = d
        self.arst_val = ARST_VAL if i.arst else d
        if i.arst:
            self.arst_en = ARST_EN_VAL
        elif i.en:
            self.arst_en = d
        nxt = array_next(d)
        if i.en:
            self.arr_en = list(nxt)
        if i.rst:
            self.arr_rst_en = [0] * ARRAY
        elif i.en:
            self.arr_rst_en = list(nxt)
        # Reads see the words stored before this edge's write.
        self.rd = self.mem[i.raddr]
        if i.re:
            self.rd_en = self.mem[i.raddr]
        self.wrd = self.wmem[i.raddr]
        if i.we:
            self.mem[i.waddr] = d
            word = lane_word(d)
            old = self.wmem[i.waddr]
            new = 0
            for lane in range(2):
                sel = 0xFF << (8 * lane)
                if (i.lanes >> lane) & 1:
                    new |= word & sel
                elif old is None:
                    new = None
                    break
                else:
                    new |= old & sel
            self.wmem[i.waddr] = new
        self.meta = [0, 0] if i.rst else [d, self.meta[0]]
        self.meta_val = [META_VAL, META_VAL] if i.rst else [d, self.meta_val[0]]
        self.meta_arst = [0, 0] if i.arst else [d, self.meta_arst[0]]
        self.rst_sync = [1, 1] if i.arst else [0, self.rst_sync[0]]

    def async_reset(self) -> None:
        """An asynchronous reset assertion between edges."""
        self.arst_val = ARST_VAL
        self.arst_en = ARST_EN_VAL
        self.meta_arst = [0, 0]
        self.rst_sync = [1, 1]

    def outputs(self) -> dict[str, int | None]:
        """Expected fixture outputs (name without the ``_o`` suffix)."""
        out = {"dff": self.dff, "dff_en": self.dff_en, "rst": self.rst,
               "rst_val": self.rst_val, "rst_en": self.rst_en, "arst_val": self.arst_val,
               "arst_en": self.arst_en, "rd": self.rd, "rd_en": self.rd_en, "wrd": self.wrd,
               "meta": self.meta[1], "meta_val": self.meta_val[1],
               "meta_arst": self.meta_arst[1],
               "rst_sync": self.rst_sync[1]}
        for k in range(ARRAY):
            out[f"arr_en_{k}"] = self.arr_en[k]
            out[f"arr_rst_en_{k}"] = self.arr_rst_en[k]
        return out
