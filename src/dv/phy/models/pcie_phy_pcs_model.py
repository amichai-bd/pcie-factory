"""Cycle stand-in for pcie_pcs in the phy e2e (wiki/src/dv/phy/DV_PLAN.md).

It plays the pcs between the mac model (PIPE, ``PipeTx``/``PipeRx`` of
pcie_mac_arch) and the channel (2x10-bit words) until the pcs RTL replaces
it. It follows the pcs HAS (wiki/src/arch/pcs/HAS.md, RECEIVE.md and
PIPE_CONTROL.md) at the level the e2e needs, reusing the pcs architecture
model's 8b/10b code and PIPE control; it is not cycle-exact to the RTL:

- **Transmit:** 8b/10b encode with running disparity (TxCompliance forces
  the low symbol negative), a ``TX_LATENCY``-cycle pipeline, electrical
  idle from the PIPE control.
- **Receive, ser_rx_clk_i side** (``rx_edge``): 40-bit window comma hunt,
  lock on the first comma, loss of lock on electrical idle or a relock
  request; SKP removal after a COM while the buffer is a symbol or more
  above nominal.
- **Receive, clk_i side** (inside ``core_edge``): the read side starts at
  the nominal fill, adds a SKP after a COM at or below nominal minus one,
  decodes with polarity, reports RxStatus by the HAS precedence, and counts
  errored words to request a relock (which here also flushes the buffer).
  RxElecIdle rises once the buffer has drained, so buffered symbols always
  reach the mac first.

Loopback (TxDetectRx in P0) is not modelled.

Block hooks:

- ``tx_encoder``: a block at the ``pcie_pcs_enc8b10b`` interface
  (wiki/src/rtl/pcs_enc8b10b/MAS.md) replaces the transmit encoder and its
  pipeline. Each cycle the stand-in calls ``block.clock(rst, valid, data,
  datak, compliance)`` with the PIPE transmit word (``valid`` is 0 in
  electrical idle), then sends ``block.data_o`` with electrical idle
  ``not block.valid_o``; the block's own latency is the transmit latency.
  ``Encoder``, the sub-IP's cycle model, is such a block; an adapter that
  drives a ``pcie_pcs_enc8b10b`` instance's pins at the falling edge is
  another (wiki/src/dv/phy/DV_PLAN.md, Model-to-RTL switch).
- ``encode`` and ``decode``: the symbol-level 8b/10b functions of the
  built-in transmit path and of the receive path.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

_ARCH = Path(__file__).resolve().parents[3] / "arch"
for _d in ("pcs", "mac"):
    if str(_ARCH / _d) not in sys.path:
        sys.path.append(str(_ARCH / _d))

import pcie_mac_arch as mac_arch  # noqa: E402
import pcie_pcs_8b10b as code8b10b  # noqa: E402
import pcie_pcs_arch as pcs_arch  # noqa: E402

K_COM, K_SKP, K_EDB = code8b10b.K_COM, code8b10b.K_SKP, code8b10b.K_EDB
TX_LATENCY = pcs_arch.TX_LATENCY
EB_NOMINAL = pcs_arch.EB_NOMINAL
EB_HIGH = pcs_arch.EB_HIGH
EB_LOW = pcs_arch.EB_LOW
EB_SYMBOLS = 2 * pcs_arch.EB_WORDS
ERR_LIMIT = pcs_arch.ERR_LIMIT
ERR_DECAY_WORDS = pcs_arch.ERR_DECAY_WORDS
MASK20 = (1 << 20) - 1


class PcsModel:
    """One pcs. ``core_edge`` runs at each clk_i rising edge; ``rx_edge`` at
    each ser_rx_clk_i rising edge (the far transmitter's clock)."""

    def __init__(self, encode=code8b10b.encode, decode=code8b10b.decode,
                 tx_encoder=None) -> None:
        self.encode, self.decode = encode, decode
        self.tx_encoder = tx_encoder
        self.stats = {"skp_added": 0, "skp_removed": 0, "overflow": 0, "underflow": 0,
                      "code_errors": 0, "disp_errors": 0, "relocks": 0, "locks": 0}
        self.reset()

    # ------------------------------------------------------------ reset

    def reset(self) -> None:
        self.ctl = pcs_arch.PipeControl()
        self.tx_rd = -1
        self.tx_pipe: deque = deque([(0, True)] * (TX_LATENCY - 1))
        # ser_rx_clk_i side
        self.prev_bits, self.prev_ei, self.cur_ei = 0, True, True
        self.lock, self.offset = False, 0
        self.prev_com = False
        self.pending_removed = False
        self.eb: deque = deque()          # [code, removed_before]
        # clk_i side
        self.ei_sync = [True, True]
        self.started = False
        self.rd: int | None = None
        self.stage: list = []             # decoded [byte, k, code_err, disp_err, removed]
        self.err_count = self.clean = 0
        self.out = mac_arch.PipeRx(phy_status=True)

    # ------------------------------------------------------- clk_i edge

    def core_edge(self, rst: bool, tx: mac_arch.PipeTx, rxdet: bool) -> tuple[int, bool]:
        """One clk_i cycle: take the mac's PIPE outputs ``tx``, update
        ``out`` (the PipeRx the mac sees next cycle) and return this cycle's
        (ser_tx_data_o, ser_tx_elec_idle_o)."""
        if rst:
            self.reset()
            if self.tx_encoder is not None:
                self.tx_encoder.clock(1, 0, 0, 0, 0)
            return 0, True
        ctl = self.ctl.step(0, int(tx.reset_n), tx.power_down, int(tx.detect_rx),
                            int(tx.elec_idle), int(rxdet))
        if self.tx_encoder is not None:
            out = self._encode_block(tx, ctl)
        elif ctl["tx_elec_idle"] or not tx.reset_n:
            word, ei = 0, True
        else:
            if tx.compliance:
                self.tx_rd = -1
            word = 0
            for i, (k, b) in enumerate(tx.syms):
                if k and b not in code8b10b.VALID_K:
                    b = K_EDB                     # an unsupported K goes out as EDB
                c, self.tx_rd = self.encode(b, bool(k), self.tx_rd)
                word |= c << (10 * i)
            ei = False
        if self.tx_encoder is None:
            self.tx_pipe.append((word, ei))
            out = self.tx_pipe.popleft()
        if not tx.reset_n or self.ctl.ready_count:
            self.out = mac_arch.PipeRx(phy_status=bool(ctl["phy_status"]))
            return out
        rx = self._read(bool(tx.polarity))
        if ctl["phy_status"]:
            rx.phy_status, rx.status = True, ctl["rx_status"]
        self.out = rx
        return out

    def _encode_block(self, tx: mac_arch.PipeTx, ctl: dict) -> tuple[int, bool]:
        blk = self.tx_encoder
        valid = int(not ctl["tx_elec_idle"] and tx.reset_n)
        data = tx.syms[0][1] | tx.syms[1][1] << 8
        datak = tx.syms[0][0] | tx.syms[1][0] << 1
        blk.clock(int(not tx.reset_n), valid, data, datak, int(bool(tx.compliance)))
        return (blk.data_o, False) if blk.valid_o else (0, True)

    def _read(self, polarity: bool) -> mac_arch.PipeRx:
        self.ei_sync = [self.cur_ei, self.ei_sync[0]]
        ei = self.ei_sync[1] and not self.eb and not self.stage
        if not self.started:
            if self.lock and len(self.eb) >= EB_NOMINAL:
                self.started = True
            else:
                return mac_arch.PipeRx(valid=False, elec_idle=ei, phy_status=False)
        out: list = []
        status = 0
        while len(out) < 2:
            if not self.stage:
                if not self.eb:
                    break
                code, removed = self.eb.popleft()
                if polarity:
                    code ^= 0x3FF
                b, k, self.rd, ce, de = self.decode(code, self.rd)
                self.stage.append([b, k, ce, de, removed])
            item = self.stage.pop(0)
            out.append(item)
            if item[4]:
                status = pcs_arch.max_status(status, 2)
            if not (item[1] and item[0] == K_COM):
                continue
            nxt = self.stage[0] if self.stage else (self.eb[0] if self.eb else None)
            if nxt is None:
                continue
            if self.stage and nxt[4]:
                nxt[4] = False
                status = pcs_arch.max_status(status, 2)
            elif not self.stage and nxt[1]:
                nxt[1] = False
                status = pcs_arch.max_status(status, 2)
            elif (self.stage and nxt[1] and nxt[0] == K_SKP) or \
                    (not self.stage and code8b10b.raw_is(nxt[0], K_SKP)):
                if len(self.eb) + len(self.stage) <= EB_LOW:
                    self.stage.insert(0, [K_SKP, True, False, False, False])
                    self.stats["skp_added"] += 1
                    status = pcs_arch.max_status(status, 1)
        if len(out) < 2:
            # Drained: a locked receiver underflowed; an unlocked one stopped.
            self.started = False
            self.stage.clear()
            if self.lock:
                self.stats["underflow"] += 1
                return mac_arch.PipeRx(valid=False, elec_idle=ei, status=6, phy_status=False)
            return mac_arch.PipeRx(valid=False, elec_idle=ei, phy_status=False)
        errored = False
        for b, k, ce, de, _ in out:
            if ce:
                status = pcs_arch.max_status(status, 4)
                self.stats["code_errors"] += 1
                errored = True
            if de:
                status = pcs_arch.max_status(status, 7)
                self.stats["disp_errors"] += 1
                errored = True
        if errored:
            self.err_count += 1
            self.clean = 0
            if self.err_count >= ERR_LIMIT:
                self._relock()
        else:
            self.clean += 1
            if self.clean >= ERR_DECAY_WORDS:
                self.err_count, self.clean = max(0, self.err_count - 1), 0
        syms = tuple((int(k), b) for b, k, _, _, _ in out)
        return mac_arch.PipeRx(syms=syms, valid=True, elec_idle=False, status=status,
                               phy_status=False)

    def _relock(self) -> None:
        """Relock request: the aligner hunts again; the misaligned symbols
        already buffered are dropped."""
        self.stats["relocks"] += 1
        self.err_count = self.clean = 0
        self.lock = False
        self.eb.clear()
        self.rd = None

    # ------------------------------------------------ ser_rx_clk_i edge

    def rx_edge(self, rst: bool, bits: int, ei: bool) -> None:
        """One ser_rx_clk_i cycle: ``bits`` is ser_rx_data_i (bit 0
        earliest), ``ei`` ser_rx_elec_idle_i."""
        if rst:
            self.prev_bits, self.prev_ei, self.cur_ei, self.lock = 0, True, True, False
            self.eb.clear()
            return
        window = self.prev_bits | (bits << 20)
        if self.cur_ei:                    # lock drops the cycle after idle is seen
            self.lock = False
        if not self.lock and not ei and not self.cur_ei:
            for p in range(20):
                if code8b10b.has_comma((window >> p) & 0x3FF):
                    self.lock, self.offset = True, p % 10
                    self.rd = None
                    self.stats["locks"] += 1
                    break
        if self.lock:
            for j in range(2):
                code = (window >> (self.offset + 10 * j)) & 0x3FF
                after_com, self.prev_com = self.prev_com, code8b10b.raw_is(code, K_COM)
                if (after_com and code8b10b.raw_is(code, K_SKP)
                        and len(self.eb) >= EB_HIGH):
                    self.pending_removed = True
                    self.stats["skp_removed"] += 1
                    continue
                if len(self.eb) >= EB_SYMBOLS:
                    self.stats["overflow"] += 1
                    continue
                self.eb.append([code, self.pending_removed])
                self.pending_removed = False
        self.prev_bits, self.prev_ei, self.cur_ei = bits & MASK20, self.cur_ei, ei
