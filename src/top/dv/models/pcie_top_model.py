"""Reference model of pcie_top, the byte-stream scrambler
(wiki/src/top/HAS.md).

The key stream comes from the lfsr sub-IP's model, reused rather than
re-implemented. Model modules carry their IP's full prefix so names never
collide, and ``models/`` directories have no ``__init__.py``. A parent IP
reaches a sub-IP model by putting ``<subip>/dv/models`` on ``sys.path``,
located relative to this file, and importing the module by its name. That
works the same under ``./build sim``/``regress`` (which put only this IP's
``dv/`` on the path) and under plain pytest.
"""

import sys
from pathlib import Path

_LFSR_MODELS = Path(__file__).resolve().parents[2] / "lfsr" / "dv" / "models"
if str(_LFSR_MODELS) not in sys.path:
    sys.path.append(str(_LFSR_MODELS))

from pcie_top_lfsr_model import Lfsr  # noqa: E402


class Scrambler:
    """Cycle model: ``valid_o``/``data_o`` are the outputs after the last
    ``clock``; ``data_o`` is meaningful only while ``valid_o`` is 1."""

    def __init__(self) -> None:
        self.lfsr = Lfsr()
        self.valid_o = 0
        self.data_o = 0

    def clock(self, rst: int, valid: int, data: int, reseed: int) -> None:
        if valid:
            self.data_o = (data ^ self.lfsr.key) & 0xFF
        self.valid_o = 0 if rst else valid
        self.lfsr.clock(rst=rst, reseed=reseed, advance=valid)


def scramble(data: list[int]) -> list[int]:
    """Scramble a back-to-back byte stream that starts right after a re-seed."""
    model = Scrambler()
    out = []
    for byte in data:
        model.clock(rst=0, valid=1, data=byte, reseed=0)
        out.append(model.data_o)
    return out
