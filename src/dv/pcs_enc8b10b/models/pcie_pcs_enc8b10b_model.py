"""Cycle model of pcie_pcs_enc8b10b (wiki/src/rtl/pcs_enc8b10b/MAS.md).

The code itself is not re-implemented: each word is encoded by the pcs
architecture model's ``tx_encode`` (src/arch/pcs/pcie_pcs_arch.py, built
from wiki/src/arch/pcs/8B10B.md), which already applies TxCompliance and
the unsupported-K-to-EDB rule. This model adds only the MAS's cycle
behavior: a two-stage pipeline, the running disparity that holds while no
word is valid, and the synchronous reset. Running disparity is -1 or +1.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "pcs"))
import pcie_pcs_8b10b as code8b10b  # noqa: E402
import pcie_pcs_arch as arch  # noqa: E402

LATENCY = 2
VALID_K = code8b10b.VALID_K
K_EDB = code8b10b.K_EDB


def encode_word(rd: int, data: int, datak: int, compliance: int) -> tuple[int, int, int]:
    """Encode one 16-bit word from running disparity ``rd``.

    Returns (data_o, rd_mid, rd_out): the two codes packed with lane 0 in
    bits 9:0, the RD between the symbols and the RD after the word."""
    syms = [((datak >> i) & 1, (data >> (8 * i)) & 0xFF) for i in range(2)]
    c0, rd_mid = arch.tx_encode(syms[:1], {0} if compliance else None, rd)
    (c1,), rd_out = arch.tx_encode(syms[1:], None, rd_mid)
    return c0[0] | (c1 << 10), rd_mid, rd_out


class Encoder:
    """Registers of pcie_pcs_enc8b10b; ``clock`` applies one rising edge and
    ``valid_o`` / ``data_o`` are the outputs after it."""

    def __init__(self) -> None:
        self.s1 = (0, 0, 0, 0)     # valid, data, datak, compliance
        self.rd = -1
        self.valid_o = 0
        self.data_o = 0

    def clock(self, rst: int, valid: int, data: int, datak: int, compliance: int) -> None:
        if rst:
            self.rd, self.valid_o = -1, 0
        else:
            v, d, k, c = self.s1
            self.valid_o = v
            if v:
                self.data_o, _, self.rd = encode_word(self.rd, d, k, c)
        self.s1 = (0 if rst else valid, data, datak, compliance)
