"""Exact-timing receive harness of the pcs top (wiki/src/dv/pcs/DV_PLAN.md,
Decoder): the decoder block's checks through the pcie_pcs ports.

Both clocks run at exactly 125 MHz with a fixed phase, so once the aligner
has locked and the elastic buffer has started, every received word leaves
on PIPE a fixed number of cycles ``D`` after it was driven: the buffer
neither adds nor removes symbols (no COM is followed by a SKP) and never
recentres (its fill stays at the start level). The line carries whole
10-bit codes at word boundaries, so the aligner locks at offset 0 on the
first comma and the decoder sees exactly the driven codes.

A ``Stim`` lists the words to drive, each with the RxPolarity and
pipe_reset_ni the MAC applies in the cycle the decoder decodes it, and
whether it is locked. As words are added, the decoder block's cycle model
(models.pcie_pcs_dec8b10b_model, which imports the architecture model's
decode()) computes what PIPE must show for it: RxData, RxDataK, RxValid
and RxStatus (4 for a code error, 7 for a disparity error, in either
symbol). ``Direct.run`` drives the words, measures ``D`` on a marker word,
then applies each word's controls in its decode cycle and compares every
checked word on PIPE with the model; a word that leaves at another cycle
fails the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cocotb
from cocotb.triggers import FallingEdge

from models import pcie_pcs_dec8b10b_model as dm
from models.pcie_pcs_model import P0, arch
from models.pcie_pcs_rx_check import Word, relock_requests
from tb.pcs_bench import Bench
from tb.pcs_env import start_clocks

PHASE_PS = 2600                   # ser_rx_clk_i edges 2.6 ns after clk_i's
D21_5 = arch.tx_encode([(0, 0xB5)])[0][0]   # one code at either RD, keeps the RD
PROBE = dm.PROBE                  # D0.0 RD-: a disparity error at RD+
MARK = (0x5A, 0xA5)               # marker data bytes
TS1 = ([(1, 0xBC), (1, 0xF7), (1, 0xF7), (0, 0x80), (0, 0x02), (0, 0x00)]
       + [(0, 0x4A)] * 10)


@dataclass
class Exp:
    """What PIPE must show for one word."""
    valid: int
    syms: tuple                   # (dm.Sym, dm.Sym)
    tag: str = ""

    @property
    def status(self) -> int:
        if not self.valid:
            return 0
        if any(s.code_err for s in self.syms):
            return 4
        if any(s.disp_err for s in self.syms):
            return 7
        return 0


@dataclass
class Stim:
    codes: list[tuple[int, int]] = field(default_factory=list)
    idle: list[int] = field(default_factory=list)
    pol: list[int] = field(default_factory=list)
    rst: list[int] = field(default_factory=list)
    exp: list[Exp | None] = field(default_factory=list)
    marker: int = -1
    model: dm.Decoder = field(default_factory=dm.Decoder)
    tx_rd: int = -1               # the partner's running disparity

    def add(self, c0: int, c1: int, *, lock: int = 1, idle: int = 0, pol: int = 0,
            rst: int = 0, check: bool = True, tag: str = "") -> Exp | None:
        """One word. With ``check`` its PIPE word is compared with the model
        (data and K only when unlocked, as RxValid is 0)."""
        syms = (c0 ^ (0x3FF if pol else 0), c1 ^ (0x3FF if pol else 0))
        lk = (lock, lock)
        if rst:
            exp = None
            self.model.clock(1, 1, syms, lk, (0, 0), pol)
        else:
            out = self.model.outputs(syms, lk, (0, 0), pol)
            exp = Exp(lock, out, tag) if check else None
            self.model.clock(0, 1, syms, lk, (0, 0), pol)
        self.codes.append((c0, c1))
        self.idle.append(idle)
        self.pol.append(pol)
        self.rst.append(rst)
        self.exp.append(exp)
        return exp

    def line(self, syms: list[tuple[int, int]], check: bool = False, **kw) -> None:
        """Encode ``syms`` (an even count) from the partner's RD and add them."""
        codes, self.tx_rd = arch.tx_encode(syms, None, self.tx_rd)
        for i in range(0, len(codes), 2):
            self.add(codes[i], codes[i + 1], check=check, **kw)

    def idle_words(self, n: int) -> None:
        for _ in range(n):
            self.add(0, 0, lock=0, idle=1, check=False)

    def resync(self) -> None:
        """Restart the model with the RD unknown (the next word must hold a
        code that fixes it, such as a COM in lane 0)."""
        self.model = dm.Decoder()

    def lock(self, sets: int = 16) -> None:
        """Training: TS1 sets, the model restarted on the last set's COM,
        then the marker word."""
        self.line(TS1 * (sets - 1))
        self.resync()
        self.line(TS1, check=True)
        self.marker = len(self.codes) if self.marker < 0 else self.marker
        self.line([(0, MARK[0]), (0, MARK[1])], check=True, tag="marker")

    def clean(self, n: int, **kw) -> None:
        for _ in range(n):
            self.add(D21_5, D21_5, **kw)

    @property
    def rd(self) -> int:
        """The decoder's RD after the last word (known in a locked run)."""
        assert self.model.known, "RD unknown"
        return self.model.rd

    def check_no_relock(self) -> None:
        """The stimulus never reaches the relock limit (RECEIVE.md)."""
        words = [Word(i, 1, ((0, 0), (0, 0)), e.status) if e is not None and e.valid
                 else Word(i, 0, ((0, 0), (0, 0)), 0)
                 for i, e in enumerate(self.exp)]
        reqs = relock_requests(words)
        assert not reqs, f"stimulus requests a relock at words {reqs[:4]}"


class Direct:
    """The bench: clocks at 0 ppm, the MAC (tb.pcs_bench.Bench, PIPE control
    checked every cycle), the line driver and the PIPE comparison."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.b: Bench | None = None
        self.stim: Stim | None = None
        self.go = False
        self.outs: list[dict] = []
        self.d: int | None = None

    async def start(self, stim: Stim) -> None:
        self.stim = stim
        start_clocks(self.dut, 0.0, PHASE_PS)
        self.dut.ser_rx_data_i.value = 0
        self.dut.ser_rx_elec_idle_i.value = 1
        self.b = Bench(self.dut, len(stim.codes) + 4000, receive=True)
        cocotb.start_soon(self._drive())
        await self.b.reset("rst_i", 8)
        await self.b.power(P0)

    async def _drive(self) -> None:
        i = 0
        dut = self.dut
        while True:
            await FallingEdge(dut.ser_rx_clk_i)
            if self.go and i < len(self.stim.codes):
                c0, c1 = self.stim.codes[i]
                pol = 0x3FF if self.stim.pol[i] else 0
                dut.ser_rx_data_i.value = (c0 ^ pol) | ((c1 ^ pol) << 10)
                dut.ser_rx_elec_idle_i.value = self.stim.idle[i]
                i += 1
            else:
                dut.ser_rx_data_i.value = 0
                dut.ser_rx_elec_idle_i.value = 1

    async def run(self) -> None:
        """Drive every word; apply each word's RxPolarity and pipe_reset_ni
        in its decode cycle (once D is known); record PIPE."""
        st, mac = self.stim, self.b.mac
        # Trailing words: the word before the line goes idle leaves unlocked.
        st.clean(4, check=False)
        n_words = len(st.codes)
        st.check_no_relock()
        self.go = True
        n = 0
        while True:
            if self.d is not None:
                w = n + 1 - self.d           # the word the decoder sees now
                pol = st.pol[w] if 0 <= w < n_words else 0
                rst = st.rst[w] if 0 <= w < n_words else 0
                if rst:
                    mac.held_in_reset()
                mac.set(rx_polarity=0 if rst else pol, reset_n=0 if rst else 1)
            out = await self.b.tick()
            self.outs.append(out)
            if self.d is None and out["pipe_rx_valid_o"] and \
                    out["pipe_rx_data_o"] == MARK[0] | (MARK[1] << 8):
                self.d = n - st.marker
                self.dut._log.info("marker on PIPE at cycle %d: line to PIPE %d cycles",
                                   n, self.d)
            n += 1
            if self.d is None:
                assert n < st.marker + 200, "marker word never on PIPE"
            elif n - self.d > n_words + 8:
                break

    def check(self) -> dict[str, int]:
        """Compare every checked word; returns counts by RxStatus."""
        st = self.stim
        counts: dict[str, int] = {}
        for w, e in enumerate(st.exp):
            if e is None:
                continue
            t = w + self.d
            out = self.outs[t]
            if w + 1 < len(st.rst) and st.rst[w + 1]:
                # Shown in the first reset cycle: the HAS reset values.
                assert (out["pipe_rx_valid_o"], out["pipe_rx_status_o"]) == (0, 0), (
                    f"word {w}: PIPE cycle {t} in reset: RxValid {out['pipe_rx_valid_o']}, "
                    f"RxStatus {out['pipe_rx_status_o']}")
                continue
            got_syms = tuple(((out["pipe_rx_datak_o"] >> i) & 1,
                              (out["pipe_rx_data_o"] >> (8 * i)) & 0xFF) for i in range(2))
            want_syms = tuple((s.k, s.data) for s in e.syms)
            got = (out["pipe_rx_valid_o"], got_syms, out["pipe_rx_status_o"] if e.valid else 0)
            want = (e.valid, want_syms, e.status)
            if got != want:
                self._context(t)
            assert got == want, (
                f"word {w} ({e.tag}) codes {st.codes[w][0]:#05x} {st.codes[w][1]:#05x} "
                f"pol {st.pol[w]}: PIPE cycle {t} (RxValid, symbols, RxStatus) expected "
                f"{want} got {got}; RD in {[s.rd_in for s in e.syms]}")
            counts[str(e.status)] = counts.get(str(e.status), 0) + 1
        return counts

    def _context(self, t: int) -> None:
        """Log the PIPE cycles around a mismatch with their expectations."""
        st = self.stim
        for tt in range(max(0, t - 12), min(len(self.outs), t + 4)):
            o, w = self.outs[tt], tt - self.d
            e = st.exp[w] if 0 <= w < len(st.exp) else None
            self.dut._log.info("cycle %d word %d: RxValid %d data %04x K %d RxStatus %d; "
                               "expected %s", tt, w, o["pipe_rx_valid_o"], o["pipe_rx_data_o"],
                               o["pipe_rx_datak_o"], o["pipe_rx_status_o"],
                               e and (e.valid, [(s.k, s.data) for s in e.syms], e.status))

    def first_valid_after(self, w: int) -> tuple[int, dict]:
        """The first PIPE cycle with RxValid at or after word w's cycle."""
        for t in range(w + self.d, len(self.outs)):
            if self.outs[t]["pipe_rx_valid_o"]:
                return t, self.outs[t]
        raise AssertionError(f"no RxValid after word {w}")
