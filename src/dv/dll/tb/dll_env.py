"""Environment of the pcie_dll testbench (wiki/src/dv/dll/DV_PLAN.md,
Strategy). The DUT is driven only through its ports (tb/pcie_dll_tb.sv).

One cycle, at the falling edge of clk_i after rising edge n:

1. Read the DUT's outputs (all registered).
2. The **phy stand-in** decides ``pkt_tx_ready_i``: 1 from an accepted sop
   to its eop (phy HAS, packet stream), 0 in the framing cycle after each
   eop, otherwise 1 with probability ``ready_p``. An accepted beat goes
   into the line towards the far end and into the DUT packet checker.
3. The far end is either the **independent partner**
   (``models/pcie_dll_partner.py``) behind a fixed-delay ``Line`` each way,
   whose ``tx_ready`` drops with probability ``gap_p`` (gaps inside and
   between received packets), or **hand-driven** beats a test queues with
   ``inject``. The beat that reaches the DUT is driven on ``pkt_rx_*``.
4. The **shadow**, a ``pcie_dll_arch.Dll`` with the DUT's parameters, gets
   the same link state and the same received packets and steps once; its
   state sequence, DL_Active time and recorded credits are compared with
   the DUT's (``check_shadow``). It sends into nothing and is never
   steered.
5. Every DLLP the DUT sends is checked as it ends (``_tx_packet``): 6
   bytes, its CRC-16 equal to both the architecture model's and the
   partner's, its type allowed in the DUT's state, its values the DUT's
   parameters, and its bytes equal to what both models encode.
"""

from __future__ import annotations

import random
import sys
from collections import deque
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge

from regress.funcov import Covergroup

_DV = Path(__file__).resolve().parents[1]
sys.path.append(str(_DV / "models"))
sys.path.append(str(_DV.parents[1] / "arch" / "dll"))
import pcie_dll_arch as arch  # noqa: E402
import pcie_dll_crc as arch_crc  # noqa: E402
import pcie_dll_partner as ptn  # noqa: E402
import pcie_dll_partner_crc as ptn_crc  # noqa: E402

# dl_state_o codes (HAS, Status) and the partner's names for them.
INACTIVE, INIT1, INIT2, ACTIVE = 0, 1, 2, 3
STATE_NAMES = ("DL_Inactive", "FC_INIT1", "FC_INIT2", "DL_Active")
PARTNER_STATE = {ptn.INACTIVE: INACTIVE, ptn.FC_INIT1: INIT1, ptn.FC_INIT2: INIT2,
                 ptn.ACTIVE: ACTIVE}
CLASSES = ("P", "NP", "Cpl")        # the partner's names; arch index 0, 1, 2
ERR_BAD_DLLP = 1 << 1               # err_o bit 1 (HAS, errors)
L0_CODE = 0x20                      # an L0 code of ltssm_state_i (mac LTSSM encoding)

# DLLP types the DUT may send in each state (DLLP.md, types used): the
# InitFC of the state's group in DL_Init, UpdateFC in DL_Active. Ack and
# Nak (#213) are not sent yet.
TX_KINDS = {INIT1: "init1", INIT2: "init2", ACTIVE: "update"}
ARCH_KINDS = {"init1": arch.INITFC1, "init2": arch.INITFC2, "update": arch.UPDATEFC}

# Functional coverage (DV plan, coverage).
COV_STATES = Covergroup("dll_dl_states")
COV_STATES.coverpoint("arc", bins={
    "inactive__init1": (INACTIVE, INIT1), "init1__init2": (INIT1, INIT2),
    "init2__active": (INIT2, ACTIVE), "init1__inactive": (INIT1, INACTIVE),
    "init2__inactive": (INIT2, INACTIVE), "active__inactive": (ACTIVE, INACTIVE)})

COV_TX = Covergroup("dll_dllp_tx")
COV_TX.coverpoint("kind", bins=["init1", "init2", "update"])
COV_TX.coverpoint("cls", bins=list(CLASSES))
COV_TX.cross("kind_x_cls", "kind", "cls")
COV_TX.coverpoint("resend", bins=["first", "resend"])
COV_TX.coverpoint("stall", bins=["sop_waited", "sop_at_once"])

COV_RX = Covergroup("dll_dllp_rx")
COV_RX.coverpoint("outcome", bins=["good", "bad_crc", "bad_length", "flagged",
                                   "inactive_discard"])
COV_RX.coverpoint("kind", bins=["ack", "nak", "init1", "init2", "update", "other_type",
                                "other_vc"])
COV_RX.coverpoint("gap", bins=["no_gap", "gap_inside"])

COV_INIT = Covergroup("dll_fc_init")
COV_INIT.coverpoint("init2_end", bins=["initfc2", "updatefc"])
COV_INIT.coverpoint("adv_hdr", bins=["finite", "infinite"])
COV_INIT.coverpoint("adv_data", bins=["finite", "infinite"])
COV_INIT.coverpoint("partner_hdr", bins=["finite", "infinite"])
COV_INIT.coverpoint("partner_data", bins=["finite", "infinite"])
COV_INIT.coverpoint("first_up", bins=["dut", "partner", "same"])

COV_UPDFC = Covergroup("dll_updatefc_tx")
COV_UPDFC.coverpoint("cause", bins=["entry", "refresh"])
COV_UPDFC.coverpoint("cls", bins=list(CLASSES))
COV_UPDFC.cross("cause_x_cls", "cause", "cls")


def fc_field(v: int) -> str:
    return "infinite" if v == 0 else "finite"


def dllp_bytes(typ: int, hdr: int, data: int) -> bytes:
    """An FC DLLP with its CRC, as the architecture model encodes it."""
    kinds = next(k for k in (arch.INITFC1, arch.INITFC2, arch.UPDATEFC) if typ & 0xF8 in k)
    return arch.fc_dllp(kinds, kinds.index(typ & 0xF8), hdr, data, typ & 0x07)


def raw_dllp(body: bytes) -> bytes:
    """A DLLP: body and CRC-16, with both models agreeing on the CRC."""
    a, p = arch_crc.crc16(body), ptn_crc.crc16_bytes(body)
    assert a == p, f"CRC-16 of {body.hex()}: arch {a.hex()} partner {p.hex()}"
    return body + a


class Gate:
    """The receiving phy's view of a line: with its link down it delivers
    nothing, and a packet whose sop it missed is dropped up to its end, so
    every delivered beat belongs to a packet that started with a sop."""

    def __init__(self) -> None:
        self.drop = False

    def pass_beat(self, beat: ptn.Beat | None, up: bool) -> ptn.Beat | None:
        if beat is None:
            return None
        if beat.sop:
            self.drop = not up
        elif not up:
            self.drop = True
        return None if self.drop else beat


class Env:
    """The DUT, the phy stand-in, the far end and the shadow."""

    def __init__(self, dut, seed: int, *, partner: ptn.Partner | None = None,
                 ready_p: float = 1.0, gap_p: float = 0.0, delay: int = 22) -> None:
        self.dut = dut
        self.rng = random.Random(seed)
        self.partner = partner
        self.ready_p = ready_p
        self.gap_p = gap_p
        self.to_far = ptn.Line(delay)
        self.to_dut = ptn.Line(delay)
        self.raw: deque[ptn.Beat | None] = deque()
        self.gate_far = Gate()
        self.gate_dut = Gate()
        self.adv: dict[str, tuple[int, int]] = {}
        self.shadow: arch.Dll | None = None
        self.cycle = 0
        self.dut_link = False
        self.partner_link = False
        self.state = INACTIVE
        self.states: list[tuple[int, int]] = [(0, INACTIVE)]       # DUT (cycle, state)
        self.shadow_states: list[tuple[int, int]] = [(0, INACTIVE)]
        self.partner_states: list[tuple[int, int]] = [(0, INACTIVE)]
        self.in_pkt = False                    # DUT packet accepted, eop not yet
        self.framing = False                   # the cycle after an eop
        self.tx_beats: list[ptn.Beat] = []
        self.tx_state = INACTIVE               # DUT state at the packet's sop
        self.tx_sop_wait = 0
        self.sent: list[tuple[int, str, int, bytes]] = []   # (sop cycle, kind, class, bytes)
        self.group_starts: list[int] = []      # sop cycles of each InitFC group's P
        self.upd_last: dict[int, int] = {}     # class -> sop cycle of its last UpdateFC
        self.upd_entry: set[int] = set()       # classes still owed their entry UpdateFC
        self.rx_beats: list[ptn.Beat] = []
        self.rx_gap = False
        self.rx_good: list[tuple[int, bytes]] = []   # (eop cycle, DLLP) good at the DUT
        self.bad_expected = 0                  # bad DLLPs the DUT must report
        self.bad_seen = 0                      # Bad DLLP pulses on err_o
        self.shadow_rx: deque[tuple[int, arch.Packet]] = deque()
        self.errs: list[tuple[int, int]] = []  # (cycle, err_o) when nonzero
        self.violations: list[str] = []

    # ------------------------------------------------------------ set-up

    async def start(self) -> None:
        d = self.dut
        cocotb.start_soon(Clock(d.clk_i, 8, unit="ns").start())
        for name in ("tlp_tx_valid_i", "tlp_tx_data_i", "tlp_tx_sop_i", "tlp_tx_eop_i",
                     "pkt_tx_ready_i", "pkt_rx_valid_i", "pkt_rx_data_i", "pkt_rx_sop_i",
                     "pkt_rx_eop_i", "pkt_rx_dllp_i", "pkt_rx_err_i", "pkt_rx_nullify_i",
                     "link_up_i", "ltssm_state_i"):
            getattr(d, name).value = 0
        # The DUT's upper receive stream never stalls (HAS, receive).
        d.tlp_rx_ready_i.value = 1
        d.rst_i.value = 1
        for _ in range(3):
            await FallingEdge(d.clk_i)
        d.rst_i.value = 0
        # The DUT's credit parameters, from the testbench's cfg_fc_o.
        cfg = int(d.cfg_fc_o.value) >> 20
        fields = []
        for w in (12, 8, 12, 8, 12, 8):         # FC_CPLD up to FC_PH
            fields.append(cfg & ((1 << w) - 1))
            cfg >>= w
        cpld, cplh, npd, nph, pd, ph = fields
        self.adv = {"P": (ph, pd), "NP": (nph, npd), "Cpl": (cplh, cpld)}
        self.shadow = arch.Dll("shadow", {"ph": ph, "pd": pd, "nph": nph, "npd": npd,
                                          "cplh": cplh, "cpld": cpld})

    def inject(self, pkt: bytes, *, dllp: bool = True, err: bool = False,
               gaps: int = 0) -> None:
        """Queue a raw packet towards the DUT (hand-driven far end), with
        ``gaps`` idle cycles after each beat but the last."""
        beats = ptn.to_beats(pkt, dllp)
        if err:
            beats[-1] = beats[-1]._replace(err=True)
        for i, b in enumerate(beats):
            self.raw.append(b)
            if i < len(beats) - 1:
                self.raw.extend([None] * gaps)

    # ------------------------------------------------------------ one cycle

    async def step(self) -> None:
        d, n = self.dut, self.cycle
        valid = int(d.pkt_tx_valid_o.value)
        state = int(d.dl_state_o.value)
        err = int(d.err_o.value)
        if err:
            self.errs.append((n, err))
            if err & ERR_BAD_DLLP:
                self.bad_seen += 1
            if err & ~ERR_BAD_DLLP:
                self.violations.append(f"cycle {n}: err_o {err:#04x}: only Bad DLLP is "
                                       f"reported before the TLP paths exist")
        if state != self.state:
            COV_STATES.sample(arc=(self.state, state))
            self.states.append((n, state))
            if state == ACTIVE:
                self.upd_entry = {0, 1, 2}
            self.state = state
        if int(d.dl_up_o.value) != int(state in (INIT2, ACTIVE)):
            self.violations.append(f"cycle {n}: dl_up_o {int(d.dl_up_o.value)} in "
                                   f"{STATE_NAMES[state]}")
        if state == INACTIVE:
            self._check_quiet(n)
        if self.in_pkt and not valid:
            if self.dut_link and state != INACTIVE:
                self.violations.append(f"cycle {n}: pkt_tx_valid_o dropped inside a packet")
            self.in_pkt = False                # cut by the link going down
            self.tx_beats = []

        # phy stand-in: transmit ready, the line towards the far end.
        if self.in_pkt:
            ready = 1
        elif self.framing:
            ready = 0
        else:
            ready = int(self.rng.random() < self.ready_p)
        self.framing = False
        d.pkt_tx_ready_i.value = ready
        to_far = None
        if valid:
            beat = ptn.Beat(int(d.pkt_tx_data_o.value), bool(d.pkt_tx_sop_o.value),
                            bool(d.pkt_tx_eop_o.value), bool(d.pkt_tx_dllp_o.value))
            if beat.sop and not self.in_pkt and not self.tx_beats:
                self.tx_state = state
            if beat.sop and not ready:
                self.tx_sop_wait += 1
            if ready:
                to_far = beat
                self._tx_beat(n, beat)
        # far end
        if self.partner is not None:
            p = self.partner
            p.link_up = self.partner_link
            p.in_l0 = self.partner_link
            p.tx_ready = not (self.gap_p and self.rng.random() < self.gap_p)
            far_out = p.clock(self.gate_far.pass_beat(self.to_far.push(to_far),
                                                      self.partner_link))
            ps = PARTNER_STATE[p.state]
            if ps != self.partner_states[-1][1]:
                self.partner_states.append((n, ps))
            in_beat = self.gate_dut.pass_beat(self.to_dut.push(far_out), self.dut_link)
        else:
            self.to_far.push(to_far)
            in_beat = self.raw.popleft() if self.raw else None
        self._drive_rx(in_beat)
        d.link_up_i.value = int(self.dut_link)
        d.ltssm_state_i.value = L0_CODE if self.dut_link else 0

        # shadow: the DUT samples these inputs at the next edge; a received
        # packet reaches it one cycle later, as the DUT's DLLP receive
        # registers it.
        sh = self.shadow
        sh.set_link_up(self.dut_link, n)
        while self.shadow_rx and self.shadow_rx[0][0] <= n:
            sh.receive(self.shadow_rx.popleft()[1], n)
        if in_beat is not None:
            self._rx_collect(n, in_beat)
        elif self.rx_beats:
            self.rx_gap = True
        sh.tick(n, True, True, True)
        if sh.state != self.shadow_states[-1][1]:
            self.shadow_states.append((n, sh.state))

        await FallingEdge(d.clk_i)
        self.cycle += 1

    async def run(self, cycles: int) -> None:
        for _ in range(cycles):
            await self.step()

    async def run_until(self, cond, limit: int, what: str) -> int:
        """Step until ``cond()``; fail after ``limit`` cycles."""
        for i in range(limit):
            if cond():
                return i
            await self.step()
        raise AssertionError(f"cycle {self.cycle}: {what} not reached within {limit} cycles")

    # ------------------------------------------------------------ helpers

    def _drive_rx(self, b: ptn.Beat | None) -> None:
        d = self.dut
        d.pkt_rx_valid_i.value = int(b is not None)
        if b is None:
            d.pkt_rx_sop_i.value = 0
            d.pkt_rx_eop_i.value = 0
            d.pkt_rx_err_i.value = 0
            d.pkt_rx_nullify_i.value = 0
            return
        d.pkt_rx_data_i.value = b.data
        d.pkt_rx_sop_i.value = int(b.sop)
        d.pkt_rx_eop_i.value = int(b.eop)
        d.pkt_rx_dllp_i.value = int(b.dllp)
        d.pkt_rx_err_i.value = int(b.err)
        d.pkt_rx_nullify_i.value = int(b.nullify)

    def _rx_collect(self, n: int, b: ptn.Beat) -> None:
        """Assemble what the DUT receives: for the shadow, for coverage and
        for the expected Bad DLLP pulses, judged by the models' CRC."""
        if b.sop:
            self.rx_beats = []
            self.rx_gap = False
        self.rx_beats.append(b)
        if not b.eop:
            return
        data = bytes(v for x in self.rx_beats for v in (x.data & 0xFF, x.data >> 8))
        self.shadow_rx.append((n + 1, arch.Packet(b.dllp, data, b.err, b.nullify)))
        self.rx_beats = []
        if not b.dllp:
            return
        if len(data) == 6 and arch_crc.crc16(data[:4]) != ptn_crc.crc16_bytes(data[:4]):
            self.violations.append(f"cycle {n}: CRC-16 of {data[:4].hex()}: arch "
                                   f"{arch_crc.crc16(data[:4]).hex()}, partner "
                                   f"{ptn_crc.crc16_bytes(data[:4]).hex()}")
        live = self.state != INACTIVE and self.dut_link
        if not live:
            outcome = "inactive_discard"
        elif b.err:
            outcome = "flagged"
        elif len(data) != 6:
            outcome = "bad_length"
        elif data[4:] != ptn_crc.crc16_bytes(data[:4]):
            outcome = "bad_crc"
        else:
            outcome = "good"
        if live and outcome != "good":
            self.bad_expected += 1
        kinds = {}
        if outcome == "good":
            kind, _, vc, _, _ = arch.decode_dllp(data)
            kinds["kind"] = {"other": "other_type"}.get(kind, kind)
            if kind in ("init1", "init2", "update") and vc:
                kinds["kind"] = "other_vc"
            self.rx_good.append((n, data))
        COV_RX.sample(outcome=outcome, gap="gap_inside" if self.rx_gap else "no_gap", **kinds)

    def _check_quiet(self, n: int) -> None:
        """In DL_Inactive every output holds its reset value (HAS)."""
        d = self.dut
        for name in ("pkt_tx_valid_o", "tlp_tx_ready_o", "tlp_rx_valid_o", "tlp_rx_sop_o",
                     "tlp_rx_eop_o", "fc_ph_avail_o", "fc_pd_avail_o", "fc_nph_avail_o",
                     "fc_npd_avail_o", "fc_cplh_avail_o", "fc_cpld_avail_o", "dl_up_o",
                     "err_o", "retrain_req_o"):
            v = int(getattr(d, name).value)
            if v:
                self.violations.append(f"cycle {n}: {name} {v:#x} in DL_Inactive")

    def _tx_beat(self, n: int, b: ptn.Beat) -> None:
        if b.sop:
            if self.in_pkt:
                self.violations.append(f"cycle {n}: sop inside a packet")
            self.tx_beats = []
            self.tx_sop_cycle = n
            COV_TX.sample(stall="sop_waited" if self.tx_sop_wait else "sop_at_once")
            self.tx_sop_wait = 0
        elif not self.in_pkt:
            self.violations.append(f"cycle {n}: beat outside a packet")
            return
        self.tx_beats.append(b)
        self.in_pkt = not b.eop
        if b.eop:
            self.framing = True
            self._tx_packet(n, self.tx_beats)
            self.tx_beats = []

    def _tx_packet(self, n: int, beats: list[ptn.Beat]) -> None:
        """Check one DLLP the DUT sent against the HAS and both models."""
        sop = self.tx_sop_cycle
        data = bytes(v for x in beats for v in (x.data & 0xFF, x.data >> 8))
        st = self.tx_state
        where = f"cycle {sop}: DLLP {data.hex()} in {STATE_NAMES[st]}"
        if not all(x.dllp for x in beats) or len(data) != 6:
            self.violations.append(f"{where}: not a 6-byte DLLP")
            return
        body, crc = data[:4], data[4:]
        if crc != arch_crc.crc16(body) or crc != ptn_crc.crc16_bytes(body):
            self.violations.append(f"{where}: CRC-16 {crc.hex()}, arch "
                                   f"{arch_crc.crc16(body).hex()}, partner "
                                   f"{ptn_crc.crc16_bytes(body).hex()}")
        kind, cls, vc, hdr, dat = arch.decode_dllp(data)
        want = TX_KINDS.get(st)
        if kind != want or vc != 0:
            self.violations.append(f"{where}: kind {kind} VC {vc}, expected {want} VC 0")
            return
        h, dd = self.adv[CLASSES[cls]]
        expect = arch.fc_dllp(ARCH_KINDS[kind], cls, h, dd)
        typ = {"init1": ptn.INITFC1, "init2": ptn.INITFC2, "update": ptn.UPDATEFC}[kind]
        if data != expect or data != ptn.make_dllp(typ[CLASSES[cls]], h, dd):
            self.violations.append(f"{where}: expected {expect.hex()} for "
                                   f"{kind}-{CLASSES[cls]} {h}, {dd}")
        prev = self.sent[-1] if self.sent else None
        if kind in ("init1", "init2"):
            # Groups go P, NP, Cpl back to back.
            if cls == 0:
                resend = bool(self.group_starts) and prev is not None and prev[1] == kind
                self.group_starts.append(sop)
                COV_TX.sample(resend="resend" if resend else "first")
            elif prev is None or prev[1] != kind or prev[2] != cls - 1:
                self.violations.append(f"{where}: {kind} class {cls} out of group order")
        else:
            if cls in self.upd_entry:
                cause = "entry"
                self.upd_entry.discard(cls)
            else:
                cause = "refresh"
            COV_UPDFC.sample(cause=cause, cls=CLASSES[cls])
            self.upd_last[cls] = sop
        COV_TX.sample(kind=kind, cls=CLASSES[cls])
        self.sent.append((sop, kind, cls, data))

    # ------------------------------------------------------------ checks

    def dut_avail(self) -> dict[str, tuple[int, int]]:
        d = self.dut
        return {"P": (int(d.fc_ph_avail_o.value), int(d.fc_pd_avail_o.value)),
                "NP": (int(d.fc_nph_avail_o.value), int(d.fc_npd_avail_o.value)),
                "Cpl": (int(d.fc_cplh_avail_o.value), int(d.fc_cpld_avail_o.value))}

    def check_avail(self, expect: dict[str, tuple[int, int]]) -> None:
        """The credits-available outputs in DL_Active: ``expect`` gives the
        partner's advertisement (0 infinite, shown as all ones)."""
        got = self.dut_avail()
        for i, cls in enumerate(CLASSES):
            want = tuple((0xFF, 0xFFF)[f] if expect[cls][f] == 0 else expect[cls][f]
                         for f in (0, 1))
            shadow = (self.shadow.avail(i, 0), self.shadow.avail(i, 1))
            assert got[cls] == want, f"{cls} credits available {got[cls]}, expected {want}"
            assert shadow == want, f"{cls}: shadow (arch) shows {shadow}, expected {want}"

    def check_shadow(self, bound: int) -> int:
        """The DUT and the shadow visit the same states in the same order,
        each change within ``bound`` cycles of the other; returns the
        largest difference."""
        a = [s for _, s in self.states]
        b = [s for _, s in self.shadow_states]
        assert a == b, (f"state sequence: DUT {[STATE_NAMES[s] for s in a]}, shadow "
                        f"(arch) {[STATE_NAMES[s] for s in b]}")
        worst = 0
        for (ca, sa), (cb, _) in zip(self.states, self.shadow_states):
            diff = abs(ca - cb)
            assert diff <= bound, (f"{STATE_NAMES[sa]}: DUT at cycle {ca}, shadow (arch) at "
                                   f"cycle {cb}, more than {bound} apart")
            worst = max(worst, diff)
        return worst

    def check_clean(self) -> None:
        assert not self.violations, "DUT violations:\n" + "\n".join(self.violations[:20])
        assert self.bad_seen == self.bad_expected, (
            f"Bad DLLP pulses: {self.bad_seen}, expected {self.bad_expected} from the "
            f"received packets")
        if self.partner is not None:
            assert not self.partner.violations, ("partner violations:\n"
                                                 + "\n".join(self.partner.violations[:20]))


# ---------------------------------------------------------------------------
# The architecture model against the partner, without the RTL.


class ArchEnd:
    """A ``pcie_dll_arch.Dll`` on the partner's beat stream: packets it
    starts go out one beat per cycle (its ``tick`` keeps one framing cycle
    between packets), received beats are collected into packets."""

    def __init__(self, dll: arch.Dll) -> None:
        self.dll = dll
        self.link_up = False
        self.now = 0
        self.out: deque[ptn.Beat] = deque()
        self.rx: list[ptn.Beat] = []

    def clock(self, beat_in: ptn.Beat | None) -> ptn.Beat | None:
        d, n = self.dll, self.now
        self.now += 1
        d.set_link_up(self.link_up, n)
        if not self.link_up:
            self.out.clear()
            self.rx = []
            return None
        if beat_in is not None:
            if beat_in.sop:
                self.rx = []
            self.rx.append(beat_in)
            if beat_in.eop:
                data = bytes(v for x in self.rx for v in (x.data & 0xFF, x.data >> 8))
                d.receive(arch.Packet(beat_in.dllp, data, beat_in.err, beat_in.nullify), n)
                self.rx = []
        pkt = d.tick(n, True, not self.out, True)
        if pkt is not None:
            self.out.extend(ptn.to_beats(pkt.data, pkt.dllp))
        return self.out.popleft() if self.out else None


def arch_partner_init(adv: dict[str, tuple[int, int]], partner: ptn.Partner, *,
                      arch_up: int, partner_up: int, delay: int = 22,
                      limit: int = 20000) -> tuple[int, arch.Dll]:
    """Link the architecture model (credits ``adv``) and ``partner`` up at
    the given cycles; return the cycle both are in DL_Active, and the model."""
    fc = {"ph": adv["P"][0], "pd": adv["P"][1], "nph": adv["NP"][0], "npd": adv["NP"][1],
          "cplh": adv["Cpl"][0], "cpld": adv["Cpl"][1]}
    a = ArchEnd(arch.Dll("arch", fc))
    ab, ba = ptn.Line(delay), ptn.Line(delay)
    gate_a, gate_p = Gate(), Gate()
    to_a = to_p = None
    for n in range(limit):
        a.link_up = n >= arch_up
        partner.link_up = n >= partner_up
        out_a = a.clock(to_a)
        out_p = partner.clock(to_p)
        to_p = gate_p.pass_beat(ab.push(out_a), partner.link_up)
        to_a = gate_a.pass_beat(ba.push(out_p), a.link_up)
        if a.dll.state == arch.ACTIVE and partner.state == ptn.ACTIVE:
            return n, a.dll
    raise AssertionError(f"arch model and partner not both in DL_Active within {limit} cycles")
