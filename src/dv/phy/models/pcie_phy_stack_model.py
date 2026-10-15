"""Back-to-back stack of the phy e2e (wiki/src/dv/phy/DV_PLAN.md).

- ``ModelPort``: one pcie_phy made of models, the mac architecture model
  (``pcie_mac_arch.Mac``) over the pcs stand-in (``PcsModel``), joined by
  PIPE.
- ``Bench``: two ports, a and b, and one ``Channel`` each way. ``edge(i)``
  is one rising edge of port i's clk_i, which is also the far port's
  ser_rx_clk_i: port i steps, its word crosses the channel, and the far
  port's receive front end takes it. The cocotb testbench calls ``edge``
  from each clock's coroutine; ``run`` here schedules the two edges in
  time order for the pure-Python tests (``ppm`` sets b's clock offset).

Block hook, transmit scrambler: ``ModelPort(tx_scrambler=block)`` takes
the scrambling out of the mac model and puts ``block`` between the mac and
the pcs, at the ``pcie_mac_scrambler`` interface
(wiki/src/rtl/mac_scrambler/MAS.md): each cycle the port reads the block's
registered outputs (``valid``, ``data``, ``datak``), then calls
``block.clock(rst, valid, data, datak, scr)`` with the mac's unscrambled
word and its per-symbol ``scr`` bits. The block's one-cycle latency
delays the whole PIPE transmit word by one cycle. ``WordScrambler``, the
sub-IP's cycle model, is such a block; an adapter that drives a
``pcie_mac_scrambler`` instance's pins at the falling edge is another
(wiki/src/dv/phy/DV_PLAN.md, Model-to-RTL switch).

A port is any object with ``rst``, ``core_edge(rxdet) -> (word, ei)``,
``rx_edge(word, ei)`` and ``present()``, plus the upper-layer view
(``send``, ``received``, ``code``, ``link_up``, ``rcv_errors``,
``retrain``); the testbench's RTL ports implement the same protocol, which
is how a layer swaps from model to RTL.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.append(str(_HERE))

from pcie_phy_channel_model import Channel  # noqa: E402
from pcie_phy_pcs_model import PcsModel, mac_arch  # noqa: E402

ROLES = mac_arch.ROLES
L0_CODE = mac_arch.CODES[mac_arch.L0]            # 0x20: L0 with both L0s sub-states in L0
RECOVERY_CODES = frozenset(mac_arch.CODES[s] for s in (mac_arch.RL, mac_arch.RC, mac_arch.RI))
CORE_PS = 8000.0                                 # clk_i period, 125 MHz
RESET_CYCLES = 8                                 # HAS reset contract minimum


def other(role: str) -> str:
    return ROLES[1 - ROLES.index(role)]


class _Unscrambled:
    """Stands in for the mac model's transmit ``Scrambler`` when a block
    scrambles: passes every symbol through and records its ``scr`` bit."""

    def __init__(self) -> None:
        self.scr: list[int] = []

    def feed(self, k: int, b: int, scramble: bool) -> int:
        self.scr.append(int(scramble and not k))
        return b


class ModelPort:
    """One pcie_phy as models: mac model over the pcs stand-in, with an
    optional transmit-scrambler block (see the module docstring)."""

    kind = "model"

    def __init__(self, role: str, timeout_div: int = 1024, tx_scrambler=None,
                 **mac_kw) -> None:
        self.role = role
        self.mac = mac_arch.Mac(role, timeout_div, 1, **mac_kw)
        self.pcs = PcsModel()
        self.tx_scrambler = tx_scrambler
        self._held = mac_arch.PipeTx(reset_n=False)
        self.rst = True
        self._seen = 0

    # lower side
    def present(self) -> bool:
        return not self.rst

    def core_edge(self, rxdet: bool) -> tuple[int, bool]:
        self.mac.rst = self.rst
        if self.tx_scrambler is None:
            tx = self.mac.step(self.pcs.out)
        else:
            tx = self._through_block(self.mac.step)
        return self.pcs.core_edge(self.rst, tx, rxdet)

    def _through_block(self, step) -> mac_arch.PipeTx:
        """One mac step with its transmit word scrambled by the block."""
        blk = self.tx_scrambler
        rec = _Unscrambled()
        self.mac.tx_scr = rec              # the mac re-creates its scrambler in reset
        tx = step(self.pcs.out)
        out = self._held
        if blk.valid:                      # last cycle's word, scrambled
            out.syms = ((blk.datak & 1, blk.data & 0xFF), (blk.datak >> 1 & 1, blk.data >> 8))
        active = not tx.elec_idle and tx.reset_n
        data = tx.syms[0][1] | tx.syms[1][1] << 8
        datak = tx.syms[0][0] | tx.syms[1][0] << 1
        scr = (rec.scr[0] | rec.scr[1] << 1) if active else 0
        blk.clock(int(self.rst), int(active), data, datak, scr)
        self._held = tx
        return out

    def rx_edge(self, word: int, ei: bool) -> None:
        self.pcs.rx_edge(self.rst, word, ei)

    # upper side
    def send(self, dllp: bool, data: bytes, nullify: bool = False) -> None:
        self.mac.tx_packets.append(mac_arch.Packet(dllp, data, nullify))

    def received(self) -> list:
        """Packets delivered since the last call, as (dllp, data, nullify, err)."""
        new = self.mac.rx_packets[self._seen:]
        self._seen = len(self.mac.rx_packets)
        return [(p.dllp, p.data, p.nullify, p.err) for p in new]

    def tx_idle(self) -> bool:
        return not self.mac.tx_packets and not any(it[3] for it in self.mac.txq)

    @property
    def code(self) -> int:
        return self.mac.code

    @property
    def link_up(self) -> bool:
        return self.mac.link_up

    @property
    def rcv_errors(self) -> int:
        return self.mac.rcv_errors

    def retrain(self) -> None:
        self.mac.retrain_req = True


class Bench:
    """Ports a (index 0) and b (1) back to back over two channels."""

    def __init__(self, ports, rng: random.Random, delay_words: int = 4,
                 release: tuple[int, int] = (RESET_CYCLES, RESET_CYCLES)) -> None:
        self.ports = tuple(ports)
        self.channels = (Channel(rng, delay_words), Channel(rng, delay_words))  # a->b, b->a
        self.release = release
        self.cycles = [0, 0]
        self.hooks: list = []          # called after every edge with (bench, i)

    def edge(self, i: int) -> None:
        p, q = self.ports[i], self.ports[1 - i]
        p.rst = self.cycles[i] < self.release[i]
        word, ei = p.core_edge(q.present())
        q.rx_edge(*self.channels[i].step(word, ei))
        self.cycles[i] += 1
        for h in self.hooks:
            h(self, i)

    def both_l0(self) -> bool:
        return all(p.code == L0_CODE and p.link_up for p in self.ports)

    # -------------------------------------------- pure-Python scheduling

    def run(self, cond, limit_cycles: int, ppm: float = 0.0) -> int:
        """Step both clocks in time order (b's period ``ppm`` off a's) until
        ``cond(self)``; return a's cycle count. Raise past ``limit_cycles``."""
        periods = (CORE_PS, CORE_PS * (1.0 + ppm * 1e-6))
        if not hasattr(self, "_t"):
            self._t = [0.0, CORE_PS * 0.37]
        start = self.cycles[0]
        while not cond(self):
            if self.cycles[0] - start > limit_cycles:
                a, b = self.ports
                raise RuntimeError(f"condition not met in {limit_cycles} cycles: "
                                   f"a code {a.code:#x}, b code {b.code:#x}")
            i = 0 if self._t[0] <= self._t[1] else 1
            self._t[i] += periods[i]
            self.edge(i)
        return self.cycles[0] - start
