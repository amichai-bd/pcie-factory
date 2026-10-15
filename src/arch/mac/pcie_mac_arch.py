"""Architecture model of pcie_mac: the Gen1 LTSSM, ordered sets, scrambling
and framing at the PIPE MAC side (wiki/src/arch/mac/HAS.md and its sub-pages).

For definition and performance analysis (wiki/bkm/arch.md), and the DV link
partner (decision record 0002, Q4):

- ``Mac`` is one MAC at the PIPE level, stepped once per clk_i cycle:
  ``step(PipeRx) -> PipeTx``. It holds the main LTSSM and the transmit and
  receive L0s sub-machines, the TS1/TS2, EIOS, FTS, SKP and compliance
  ordered sets, the scrambler, and packet framing and deframing.
- ``Link`` is an ideal PCS pair and channel between two PIPE MAC sides: the
  PIPE handshakes, receiver detection, loopback, polarity and symbol errors.
- ``Pair`` runs two ``Mac`` models back to back through a ``Link``.

DV uses a ``Mac`` as the link partner of the RTL: it samples the RTL's PIPE
outputs into a ``PipeTx``, steps ``Link`` with both sides and drives the
RTL's PIPE inputs from the returned ``PipeRx``. Error, timeout and
corner-case injection is through ``Mac.inject`` (``Inject``) and ``Pcs``.
``MacBudget`` is what the phy stack model needs from the mac.

Python standard library only. Plain classes: ./build arch imports this file
by path without registering it in sys.modules.
"""

from __future__ import annotations

import random
from collections import deque
from types import SimpleNamespace

import pcie_mac_sweep

# ---------------------------------------------------------------- constants

CORE_MHZ = 125.0            # clk_i and PCLK; two symbols per cycle
SKP_INTERVAL_MIN = 1180     # symbol times between SKP ordered sets [cite: PCIe Base Spec, rev 3.0, §4.2.7]
SKP_INTERVAL_MAX = 1538

# Timeouts in clk_i cycles (8 ns) [cite: PCIe Base Spec, rev 3.0, §4.2.6].
DETECT_QUIET_CYCLES = 1_500_000     # 12 ms, divided by TIMEOUT_DIV
HOT_RESET_CYCLES = 250_000          # 2 ms dwell, divided by TIMEOUT_DIV
T2MS = 250_000
T24MS = 3_000_000
T48MS = 6_000_000
ERR_TIMEOUT_FLOOR = 16_384          # floor of an ERR_TIMEOUT_DIV-divided timeout (131 us)
TX_IDLE_MIN_CYCLES = 3              # 20 ns of electrical idle, rounded up
POLLING_ACTIVE_MIN_TS1 = 1024       # [cite: PCIe Base Spec, rev 3.0, §4.2.6.2]
DIV_MAX = 1024
N_FTS_DEFAULT = 32
L0S_IDLE_DEFAULT = 256
L0S_IDLE_MAX = 875                  # 7 us
IDLE_TO_RLOCK_MAX = 0xFF

# PIPE PowerDown codes (wiki/src/arch/phy/PIPE.md).
P0, P0S, P1, P2 = 0, 1, 2, 3

# Symbols as (is_k, byte) [cite: PCIe Base Spec, rev 3.0, §4.2.1.1].
COM = (1, 0xBC)     # K28.5
STP = (1, 0xFB)     # K27.7
SDP = (1, 0x5C)     # K28.2
END = (1, 0xFD)     # K29.7
EDB = (1, 0xFE)     # K30.7
PAD = (1, 0xF7)     # K23.7
SKP = (1, 0x1C)     # K28.0
FTS = (1, 0x3C)     # K28.1
EIDL = (1, 0x7C)    # K28.3, the IDL symbol of an EIOS
TS1_ID, TS2_ID = 0x4A, 0x45         # D10.2, D5.2 [cite: PCIe Base Spec, rev 3.0, §4.2.4.1]
RATE_GEN1 = 0x02                    # 2.5 GT/s supported
CTL_HOT_RESET, CTL_DISABLE, CTL_LOOPBACK = 0x01, 0x02, 0x04
CTL_NO_SCRAMBLE = 0x08              # Disable Scrambling (SYMBOLS.md, Scrambling)
D21_5, D10_2 = 0xB5, 0x4A           # compliance pattern data symbols [cite: PCIe Base Spec, rev 3.0, §4.2.8]

ROLES = ("downstream", "upstream")


def cycles_to_us(cycles: float) -> float:
    return cycles / CORE_MHZ


# ---------------------------------------------------------------- scrambler

SEED = 0xFFFF
_KEY: dict[int, int] = {}
_NEXT: dict[int, int] = {}


def _fill(state: int) -> None:
    """Key byte and next state for ``state``: G(X) = X^16 + X^5 + X^4 + X^3
    + 1, eight shifts per symbol, key bit 0 first, taken from state bit 15
    before each shift [cite: PCIe Base Spec, rev 3.0, §4.2.1.3]."""
    s, key = state, 0
    for i in range(8):
        fb = (s >> 15) & 1
        key |= fb << i
        s = (s << 1) & 0xFFFF
        if fb:
            s ^= 0x0039
    _KEY[state], _NEXT[state] = key, s


def keystream(n: int) -> list[int]:
    """The first ``n`` key bytes after a reset (scrambling 00h)."""
    out, s = [], SEED
    for _ in range(n):
        if s not in _KEY:
            _fill(s)
        out.append(_KEY[s])
        s = _NEXT[s]
    return out


class Scrambler:
    """One direction's LFSR. ``feed`` takes one symbol in order and returns it
    scrambled (or descrambled; the operation is its own inverse). COM resets
    the LFSR; SKP does not advance it; every other symbol advances it; only
    data symbols with ``scramble`` set are changed."""

    def __init__(self) -> None:
        self.state = SEED

    def feed(self, k: int, b: int, scramble: bool) -> int:
        if k and b == 0xBC:
            self.state = SEED
            return b
        if k and b == 0x1C:
            return b
        s = self.state
        if s not in _KEY:
            _fill(s)
        if scramble and not k:
            b ^= _KEY[s]
        self.state = _NEXT[s]
        return b


# ------------------------------------------------------------------- states

# (name, short id, ltssm_state_o code); L0 is 0x20 | tx << 2 | rx.
MAIN_STATES = (
    ("Detect.Quiet", "detect_quiet", 0x00),
    ("Detect.Active", "detect_active", 0x01),
    ("Polling.Active", "polling_active", 0x04),
    ("Polling.Compliance", "polling_compliance", 0x05),
    ("Polling.Configuration", "polling_configuration", 0x06),
    ("Configuration.Linkwidth.Start", "cfg_linkwidth_start", 0x08),
    ("Configuration.Linkwidth.Accept", "cfg_linkwidth_accept", 0x09),
    ("Configuration.Lanenum.Wait", "cfg_lanenum_wait", 0x0A),
    ("Configuration.Lanenum.Accept", "cfg_lanenum_accept", 0x0B),
    ("Configuration.Complete", "cfg_complete", 0x0C),
    ("Configuration.Idle", "cfg_idle", 0x0D),
    ("Recovery.RcvrLock", "recovery_rcvrlock", 0x10),
    ("Recovery.RcvrCfg", "recovery_rcvrcfg", 0x11),
    ("Recovery.Idle", "recovery_idle", 0x12),
    ("Disabled", "disabled", 0x18),
    ("Hot Reset", "hot_reset", 0x19),
    ("Loopback.Entry", "loopback_entry", 0x1C),
    ("Loopback.Active", "loopback_active", 0x1D),
    ("Loopback.Exit", "loopback_exit", 0x1E),
    ("L0", "l0", 0x20),
    ("L1.Entry", "l1_entry", 0x30),
    ("L1.Idle", "l1_idle", 0x31),
    ("L2.Idle", "l2_idle", 0x34),
)
TX_STATES = (("Tx_Off", "tx_off", None), ("Tx_L0", "tx_l0", 0),
             ("Tx_L0s.Entry", "tx_l0s_entry", 1), ("Tx_L0s.Idle", "tx_l0s_idle", 2),
             ("Tx_L0s.FTS", "tx_l0s_fts", 3))
RX_STATES = (("Rx_Off", "rx_off", None), ("Rx_L0", "rx_l0", 0),
             ("Rx_L0s.Entry", "rx_l0s_entry", 1), ("Rx_L0s.Idle", "rx_l0s_idle", 2),
             ("Rx_L0s.FTS", "rx_l0s_fts", 3))
SHORT = {n: s for n, s, _ in MAIN_STATES + TX_STATES + RX_STATES}
CODES = {n: c for n, _, c in MAIN_STATES}
SUB_CODES = {n: c for n, _, c in TX_STATES + RX_STATES}

DQ, DA = "Detect.Quiet", "Detect.Active"
PA, PC, PCFG = "Polling.Active", "Polling.Compliance", "Polling.Configuration"
LWS, LWA = "Configuration.Linkwidth.Start", "Configuration.Linkwidth.Accept"
LNW, LNA = "Configuration.Lanenum.Wait", "Configuration.Lanenum.Accept"
CC, CI = "Configuration.Complete", "Configuration.Idle"
RL, RC, RI = "Recovery.RcvrLock", "Recovery.RcvrCfg", "Recovery.Idle"
DIS, HR = "Disabled", "Hot Reset"
LBE, LBA, LBX = "Loopback.Entry", "Loopback.Active", "Loopback.Exit"
L0, L1E, L1I, L2I = "L0", "L1.Entry", "L1.Idle", "L2.Idle"
TOFF, TL0, TE, TI, TF = "Tx_Off", "Tx_L0", "Tx_L0s.Entry", "Tx_L0s.Idle", "Tx_L0s.FTS"
ROFF, RL0, RE, RXI, RF = "Rx_Off", "Rx_L0", "Rx_L0s.Entry", "Rx_L0s.Idle", "Rx_L0s.FTS"

# Every arc of the HAS arc list (wiki/src/arch/mac/ARCS.md): the 100%
# arc-coverage target. Bin name -> (from, to).
_ARC_LIST = (
    (DQ, DA), (DA, PA), (DA, DQ),
    (PA, PCFG), (PA, PC), (PA, DQ), (PC, PA), (PCFG, LWS), (PCFG, DQ),
    (LWS, LWA), (LWS, LBE), (LWS, DIS), (LWS, DQ),
    (LWA, LNW), (LWA, DQ),
    (LNW, LNA), (LNW, DQ),
    (LNA, CC), (LNA, LNW), (LNA, DQ),
    (CC, CI), (CC, DQ),
    (CI, L0), (CI, RL), (CI, DQ),
    (L0, RL), (L0, L1E), (L0, L2I),
    (RL, RC), (RL, LWS), (RL, DQ),
    (RC, RI), (RC, LWS), (RC, DQ),
    (RI, L0), (RI, LWS), (RI, HR), (RI, DIS), (RI, LBE), (RI, RL), (RI, DQ),
    (L1E, L1I), (L1I, RL),
    (L2I, DQ),
    (DIS, DQ), (HR, DQ),
    (LBE, LBA), (LBE, LBX), (LBA, LBX), (LBX, DQ),
    (TOFF, TL0), (TL0, TOFF), (TL0, TE), (TE, TI), (TI, TF), (TF, TL0),
    (ROFF, RL0), (RL0, ROFF), (RL0, RE), (RE, RXI), (RXI, RF), (RF, RL0),
    (RF, ROFF), (RXI, ROFF),
)
ARCS = {f"{SHORT[a]}__{SHORT[b]}": (a, b) for a, b in _ARC_LIST}
ARC_SET = frozenset(ARCS.values())


def state_code(main: str, tx: str, rx: str) -> int:
    """``ltssm_state_o`` for a main state and the two L0s sub-states."""
    if main == L0:
        return 0x20 | SUB_CODES[tx] << 2 | SUB_CODES[rx]
    return CODES[main]


# --------------------------------------------------------------------- PIPE

class PipeTx:
    """MAC-to-PCS signals for one clk_i cycle. ``syms`` is two (is_k, byte)
    symbols, [0] in TxData[7:0]."""

    __slots__ = ("syms", "elec_idle", "detect_rx", "compliance", "polarity",
                 "power_down", "reset_n")

    def __init__(self, syms=((0, 0), (0, 0)), elec_idle=True, detect_rx=False,
                 compliance=False, polarity=False, power_down=P1, reset_n=True):
        self.syms, self.elec_idle, self.detect_rx = syms, elec_idle, detect_rx
        self.compliance, self.polarity = compliance, polarity
        self.power_down, self.reset_n = power_down, reset_n


class PipeRx:
    """PCS-to-MAC signals for one clk_i cycle."""

    __slots__ = ("syms", "valid", "elec_idle", "status", "phy_status")

    def __init__(self, syms=((0, 0), (0, 0)), valid=False, elec_idle=True,
                 status=0, phy_status=True):
        self.syms, self.valid, self.elec_idle = syms, valid, elec_idle
        self.status, self.phy_status = status, phy_status


# ------------------------------------------------------------------ packets

class Packet:
    """One upper-stream packet. On receive, ``err`` and ``nullify`` are the
    flags of its eop beat."""

    __slots__ = ("dllp", "data", "nullify", "err", "t")

    def __init__(self, dllp: bool, data: bytes, nullify: bool = False,
                 err: bool = False) -> None:
        self.dllp, self.data, self.nullify, self.err = dllp, bytes(data), nullify, err
        self.t = 0

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Packet) and (self.dllp, self.data, self.nullify, self.err)
                == (other.dllp, other.data, other.nullify, other.err))

    def __repr__(self) -> str:
        return (f"Packet(dllp={self.dllp}, {len(self.data)} bytes, "
                f"nullify={self.nullify}, err={self.err})")


class Ts:
    """A received TS1 or TS2. ``link`` and ``lane`` are None for PAD."""

    __slots__ = ("kind", "link", "lane", "n_fts", "ctl")

    def __init__(self, kind, link, lane, n_fts, ctl):
        self.kind, self.link, self.lane, self.n_fts, self.ctl = kind, link, lane, n_fts, ctl


class Inject:
    """Error, timeout and corner-case injection hooks of one ``Mac``.

    - ``mute``: the transmitter stays in electrical idle in every state.
    - ``freeze``: the LTSSM takes no arc; it keeps transmitting what its
      state transmits, and its counters keep counting.
    - ``ts``: fields forced into every TS sent: ``kind`` ("TS1"/"TS2"),
      ``link``, ``lane`` (None is PAD), ``n_fts``, ``ctl``.
    - ``ts_script``: a deque of such field dicts, one used per TS sent
      before ``ts`` applies again; with ``freeze`` it plays an exact TS
      sequence to the partner.
    - ``fts_extra``: FTS ordered sets sent beyond the partner's N_FTS on an
      L0s exit (negative: fewer).
    - ``skp_adjust``: +1 or -1 SKP symbol in the next SKP ordered set, so
      later symbols move by one byte lane.
    - ``no_eios``: enter electrical idle without sending an EIOS.
    - ``idle_to_rlock``: when not None, preset the idle_to_rlock_transitioned
      counter to this value once.
    """

    def __init__(self) -> None:
        self.mute = False
        self.freeze = False
        self.ts: dict = {}
        self.ts_script: deque = deque()
        self.fts_extra = 0
        self.skp_adjust = 0
        self.no_eios = False
        self.idle_to_rlock: int | None = None


# ------------------------------------------------------------------ the mac

class MacBudget:
    """What the phy stack model needs from the mac (pcie_phy_arch.Stack):
    latencies in clk_i cycles, from the mac HAS targets."""

    def __init__(self, tx_latency_cycles: int = 3, rx_latency_cycles: int = 3,
                 skp_interval_symbols: int = SKP_INTERVAL_MIN) -> None:
        self.tx_latency_cycles = tx_latency_cycles
        self.rx_latency_cycles = rx_latency_cycles
        self.skp_interval_symbols = skp_interval_symbols


def _check_int(name: str, v, lo: int, hi: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"{name}: expected an integer in [{lo}, {hi}]")
    return v


class Mac:
    """One pcie_mac at the PIPE MAC side.

    Upper-layer inputs are attributes the caller sets between steps:
    ``retrain_req`` (a pulse: set True, consumed in L0, cleared otherwise),
    ``hot_reset_req``, ``disable_req``, ``loopback_req`` (levels),
    ``pm_state_req`` (0 L0, 1 L0s allowed, 2 L1, 3 L2), ``tx_packets`` (a
    deque of ``Packet``). Outputs: ``state``, ``tx_sub``, ``rx_sub``,
    ``code`` (ltssm_state_o), ``link_up``, ``rx_packets``, ``rcv_errors``,
    ``framing_errors`` and ``arcs`` (cycle, from, to) for every arc taken.
    ``rst`` holds the model in reset while True.
    """

    def __init__(self, port_role: str = "downstream", timeout_div: int = 1,
                 err_timeout_div: int = 1, n_fts: int = N_FTS_DEFAULT,
                 l0s_idle_cycles: int = L0S_IDLE_DEFAULT,
                 skp_interval: int = SKP_INTERVAL_MIN, link_number: int = 0) -> None:
        if port_role not in ROLES:
            raise ValueError(f"port_role: expected one of {ROLES}")
        _check_int("timeout_div", timeout_div, 1, DIV_MAX)
        _check_int("err_timeout_div", err_timeout_div, 1, DIV_MAX)
        _check_int("n_fts", n_fts, 1, 255)
        _check_int("l0s_idle_cycles", l0s_idle_cycles, 16, L0S_IDLE_MAX)
        _check_int("skp_interval", skp_interval, SKP_INTERVAL_MIN, SKP_INTERVAL_MAX)
        _check_int("link_number", link_number, 0, 255)
        self.role = port_role
        self.dsp = port_role == "downstream"
        self.quiet_cycles = DETECT_QUIET_CYCLES // timeout_div
        self.dwell_cycles = HOT_RESET_CYCLES // timeout_div
        self.err_div = err_timeout_div
        self.n_fts = n_fts
        self.l0s_idle_cycles = l0s_idle_cycles
        self.skp_interval = skp_interval
        self.link_number = link_number
        self.retrain_req = False
        self.hot_reset_req = False
        self.disable_req = False
        self.loopback_req = False
        self.pm_state_req = 0
        self.tx_packets: deque[Packet] = deque()
        self.rx_packets: list[Packet] = []
        self.inject = Inject()
        self.arcs: list[tuple[int, str, str]] = []
        self.now = 0
        self.rst = True
        self._reset()

    def err(self, t: int) -> int:
        """An error-path timeout: ``t``, or divided by ERR_TIMEOUT_DIV and
        floored at ERR_TIMEOUT_FLOOR."""
        return t if self.err_div == 1 else max(t // self.err_div, ERR_TIMEOUT_FLOOR)

    # -------------------------------------------------------------- reset

    def _reset(self) -> None:
        self.state, self.tx_sub, self.rx_sub = DQ, TOFF, ROFF
        self.link_up = False
        self.rcv_errors = 0
        self.framing_errors = 0
        self.tx_aborted = 0
        self.lb_errors = 0
        self.idle_to_rlock = 0
        self.partner_n_fts = 255
        self.rx_link: int | None = None      # link number in use (upstream: learned)
        self.polarity = False
        self.scr_off = False                 # scrambling disabled (SYMBOLS.md, Scrambling)
        self.came_from = DQ
        # PIPE
        self.pd = P1
        self.pd_pending = False
        self.pcs_ready = False
        self.detect_rx = False
        self.detect_result: bool | None = None
        self.tx_ei = True
        self.ei_cycles = 0
        self.rx_ei = True
        # transmit
        self.txq: deque = deque()
        self.tx_scr = Scrambler()
        self.skp_credit = 0
        self.compl_words = 0                 # compliance-pattern words still queued
        self.tx_idle_cycles = 0
        self.lb_count = 0
        # receive
        self.rx_scr = Scrambler()
        self._p = 0
        self._ts: list = []
        self._pkt: bytearray | None = None
        self._pkt_dllp = False
        self._pkt_bad = False
        self._resync = False
        self._lb_expect: int | None = None
        self._enter_state(DQ)

    # ------------------------------------------------------------- arcs

    def _arc(self, a: str, b: str) -> None:
        self.arcs.append((self.now, a, b))

    def _go(self, new: str) -> None:
        old = self.state
        if old == L0:
            self._exit_l0()
        self._arc(old, new)
        # Disable Scrambling takes effect on entry to Configuration.Idle and
        # ends in Detect.Quiet (SYMBOLS.md, Scrambling).
        if old == CC and new == CI and (self.dscr_rx or self.dscr_sent):
            self.scr_off = True
        if new == DQ:
            self.scr_off = False
        self.came_from = old
        self.state = new
        self._enter_state(new)

    def _set_tx(self, new: str) -> None:
        self._arc(self.tx_sub, new)
        self.tx_sub = new
        self.tx_entered = self.now
        if new == TE:
            self.send_eios = True
        elif new == TF:
            self.fts_left = max(0, self.partner_n_fts + self.inject.fts_extra)
            self.fts_skp_done = False
        elif new == TL0:
            self.tx_idle_cycles = 0

    def _set_rx(self, new: str) -> None:
        if self.rx_sub == RL0 and new != RL0:
            # Leaving Rx_L0 (receive L0s or an L0 exit) cuts an open packet
            # and ends a resync (SYMBOLS.md, Receive).
            if self._pkt is not None:
                self._pkt_end(err=True, silent=True)
            self._resync = False
        self._arc(self.rx_sub, new)
        self.rx_sub = new
        self.rx_entered = self.now
        if new != RXI:                         # electrical idle seen in Rx_L0s.Entry counts
            self.sub_ei_seen = False
            self.sub_ei_exit = False
        if new == RF:
            self.rx_fts_deadline = self.now + min(2 * self.n_fts + 32, 4 * self.n_fts + 12)
        elif new == RL0:
            self.eios_rx = False
        self.rx_fts_timeout = False

    def _exit_l0(self) -> None:
        # Leaving L0 inside a packet ends it on the line with EDB.
        if any(it[3] for it in self.txq):
            kept = [it for it in self.txq if not it[3]]
            first = next(i for i, it in enumerate(self.txq) if it[3])
            kept.insert(first, (1, 0xFE, False, False))
            if len(kept) % 2:
                kept.insert(first + 1, (0, 0, True, False))
            self.txq = deque(kept)
            self.tx_aborted += 1
        self._set_tx(TOFF)
        self._set_rx(ROFF)

    def _enter_state(self, s: str) -> None:
        self.entered = self.now
        self.deadline: int | None = None
        self.preds: tuple = ()
        self.runs: dict[str, int] = {}
        self.ts_sent = 0
        self.sent_after: int | None = None     # TS sent since the first matching TS
        self.ts_rx_any = 0
        self.idle_run = 0
        self.idle_sent_after: int | None = None
        self.got8 = False                      # 8 consecutive matching TS seen (latched)
        self.mism8 = False                     # Recovery.RcvrCfg: 8 mismatching TS1 (latched)
        self.sent_after_mism: int | None = None
        self.idle8 = False                     # 8 consecutive idle symbols seen (latched)
        self.eios_rx = False
        self.ei_after_eios = False             # Disabled: electrical idle after an EIOS
        self.dscr_rx = False                   # Configuration.Complete: 2 TS2 with Disable Scrambling
        self.dscr_sent = False                 # ... or a TS2 sent with it (injection only)
        self.main_ei_seen = False
        self.main_ei_exit = False
        self.left_ei = False
        self.send_eios = False
        self.directed = False
        self.disable_ts_left = 0
        self.hr_last = self.now
        self.ev_ts = False
        self.ev_eios_pm = False
        self.pm_wait = False                   # upstream: EIOS sent in L0, waiting for the partner's
        self.retrain = False
        self.l0_exit = False
        n = self.link_number if self.dsp else self.rx_link

        def ts_(kinds, link=..., lane=...):
            def f(t):
                if t.kind not in kinds:
                    return False
                if link is not ... and t.link != link:
                    return False
                return lane is ... or t.lane == lane
            return f

        if s == DQ:
            self.deadline = self.now + self.quiet_cycles
            self.link_up = False
            self.rx_link = None
            self.polarity = False
        elif s == DA:
            self.detect_result = None
        elif s == PA:
            self.link_up = False
            self.deadline = self.now + self.err(T24MS)
            self.preds = (("go", ts_(("TS1", "TS2"), None, None)),)
        elif s == PCFG:
            self.deadline = self.now + self.err(T48MS)
            self.preds = (("go", ts_(("TS2",), None, None)),)
        elif s == LWS:
            self.deadline = self.now + self.err(T24MS)
            if self.dsp:
                go = ts_(("TS1",), self.link_number, None)
            else:
                go = lambda t: (t.kind == "TS1" and t.link is not None  # noqa: E731
                                and t.link >= 0 and t.lane is None)
            self.preds = (("go", go),
                          ("lb", lambda t: t.kind == "TS1" and bool(t.ctl & CTL_LOOPBACK)),
                          ("dis", lambda t: t.kind == "TS1" and bool(t.ctl & CTL_DISABLE)))
        elif s == LWA:
            self.deadline = self.now + self.err(T2MS)
            go = ts_(("TS1",), n, None) if self.dsp else ts_(("TS1",), n, 0)
            self.preds = (("go", go), ("pad", ts_(("TS1",), None, None)))
        elif s in (LNW, LNA):
            self.deadline = self.now + self.err(T2MS)
            self.preds = (("go", ts_(("TS1", "TS2"), n, 0)),
                          ("pad", ts_(("TS1",), None)))
            if s == LNA:
                self.preds += (("renum", lambda t: t.kind == "TS1" and t.link == n
                                and t.lane is not None and t.lane != 0),)
        elif s == CC:
            self.deadline = self.now + self.err(T2MS)
            self.preds = (("go", ts_(("TS2",), n, 0)),
                          ("dscr", lambda t: t.kind == "TS2" and t.link == n and t.lane == 0
                           and bool(t.ctl & CTL_NO_SCRAMBLE)))
        elif s in (CI, RI):
            self.deadline = self.now + self.err(T2MS)
            if s == RI:
                self.preds = (
                    ("hr", lambda t: t.kind == "TS1" and bool(t.ctl & CTL_HOT_RESET)),
                    ("dis", lambda t: t.kind == "TS1" and bool(t.ctl & CTL_DISABLE)),
                    ("lb", lambda t: t.kind == "TS1" and bool(t.ctl & CTL_LOOPBACK)),
                    ("lanepad", lambda t: t.kind == "TS1" and t.lane is None))
        elif s == L0:
            self.link_up = True
            self.idle_to_rlock = 0
            self._resync = False
            self._set_tx(TL0)
            self._set_rx(RL0)
        elif s == RL:
            self.deadline = self.now + self.err(T24MS)
            self.preds = (("go", ts_(("TS1", "TS2"), n, 0)),)
        elif s == RC:
            self.deadline = self.now + self.err(T48MS)
            self.preds = (("go", ts_(("TS2",), n, 0)),
                          ("mism", lambda t: t.kind == "TS1" and (t.link != n or t.lane != 0)))
        elif s in (L1E, L2I):
            self.send_eios = True
        elif s == DIS:
            self.link_up = False
            self.directed = self.dsp and self.disable_req
            self.disable_ts_left = 16
            self.deadline = self.now + self.err(T2MS)
        elif s == HR:
            self.link_up = False
            self.directed = self.dsp and self.hot_reset_req
            self.preds = (("hr", lambda t: t.kind == "TS1" and bool(t.ctl & CTL_HOT_RESET)),)
        elif s == LBE:
            self.link_up = False
            self.directed = self.loopback_req
            self.deadline = self.now + self.err(T2MS)
            self.preds = (("lb", lambda t: t.kind == "TS1" and bool(t.ctl & CTL_LOOPBACK)),)
        elif s == LBA:
            self.lb_master = self.directed_lb
            self._lb_expect = None
        elif s == LBX:
            self.send_eios = True
        if s == LBE:
            self.directed_lb = self.directed
        if s in (DIS, LBE):
            # TS link and lane as in the state the port came from: Linkwidth.Start's,
            # else link N and lane 0 (LTSSM.md, What each state transmits).
            if self.came_from == LWS:
                self.ctl_fields = (self.link_number if self.dsp else None, None)
            else:
                self.ctl_fields = (n, 0)
        self.runs = {name: 0 for name, _ in self.preds}

    @property
    def code(self) -> int:
        return state_code(self.state, self.tx_sub, self.rx_sub)

    # --------------------------------------------------------------- step

    def step(self, rx: PipeRx) -> PipeTx:
        self.now += 1
        if self.rst:
            arcs, now = self.arcs, self.now
            self._reset()
            self.arcs, self.now = arcs, now
            return PipeTx(reset_n=False)
        if self.inject.idle_to_rlock is not None:
            self.idle_to_rlock, self.inject.idle_to_rlock = self.inject.idle_to_rlock, None
        # PIPE status: PhyStatus is high until the PCS is ready, then pulses.
        if not self.pcs_ready:
            if not rx.phy_status:
                self.pcs_ready = True
        elif rx.phy_status:
            if self.detect_rx:
                self.detect_result = rx.status == 3
            else:
                self.pd_pending = False
        self._rx_word(rx)
        if self.state == L0 and self.retrain_req:
            self.retrain = True
        self.retrain_req = False
        if not self.inject.freeze:
            self._eval()
        self.ev_ts = False
        return self._tx_word()

    # ----------------------------------------------------------- receive

    def _rx_word(self, rx: PipeRx) -> None:
        ei = rx.elec_idle
        self.rx_ei = ei
        if ei and self.eios_rx:
            self.ei_after_eios = True
        if ei:
            self.main_ei_seen = True
            self.sub_ei_seen = True
        else:
            self.left_ei = True
            if self.main_ei_seen:
                self.main_ei_exit = True
            if self.sub_ei_seen:
                self.sub_ei_exit = True
        if rx.status >= 4:
            self.rcv_errors += 1
            if self._pkt is not None:
                self._pkt_bad = True
        if rx.valid and not ei:
            for sym in rx.syms:
                self._rx_sym(sym[0], sym[1])
        elif self._pkt is not None:
            self._pkt_end(err=True)

    def _rx_sym(self, k: int, b: int) -> None:
        scr = self.rx_scr
        if k and b == 0xBC:                     # COM: an ordered set starts
            if self._p == 2:
                self._on_ts(None)
            if self._pkt is not None:
                self._pkt_end(err=True)
                self._frame_err(self.state == L0 and self.rx_sub == RL0)
            scr.feed(k, b, False)
            self._p = 1
            return
        p = self._p
        if p == 1:
            scr.feed(k, b, False)
            if k and b == 0x1C:
                self._p = 3
                self._on_skp()
            elif k and b == 0x7C:
                self._p = 4
                self._on_eios()
            elif k and b == 0x3C:
                self._p = 4
            else:
                self._p = 2
                self._ts = [(k, b)]
            return
        if p == 2:
            scr.feed(k, b, False)
            self._ts.append((k, b))
            if len(self._ts) == 15:
                self._p = 0
                self._on_ts(_decode_ts(self._ts))
            return
        if p == 3:
            if k and b == 0x1C:
                scr.feed(k, b, False)
                return
            self._p = 0
        elif p == 4:
            if k and b in (0x7C, 0x3C):
                scr.feed(k, b, False)
                return
            self._p = 0
        if self.state == LBA and self.lb_master and not k:
            scr.feed(k, b, False)              # the loopback pattern is not scrambled
            if self._lb_expect is not None and b != self._lb_expect:
                self.lb_errors += 1
            self._lb_expect = (b + 1) & 0xFF
            return
        d = scr.feed(k, b, not self.scr_off)
        in_l0 = self.state == L0 and self.rx_sub == RL0
        if k:
            self.idle_run = 0
            if b in (0xFB, 0x5C):
                if self._pkt is not None:
                    self._pkt_end(err=True)
                    self._frame_err(in_l0)
                if in_l0:
                    self._pkt, self._pkt_dllp, self._pkt_bad = bytearray(), b == 0x5C, False
                self._resync = False
            elif b in (0xFD, 0xFE) and self._pkt is not None:
                self._pkt_end(err=False, edb=b == 0xFE)
            elif b in (0xFD, 0xFE) and self._resync:
                self._resync = False
            elif self._pkt is not None:
                self._pkt_end(err=True)
                self._frame_err(in_l0)
            elif not self._resync:             # discarded without errors while resyncing
                self._frame_err(in_l0)
        elif self._pkt is not None:
            self._pkt.append(d)
        elif d == 0 and not self._resync:
            self.idle_run += 1
            if self.idle_sent_after is None:
                self.idle_sent_after = 0
            if self.idle_run >= 8:
                self.idle8 = True
        else:
            self.idle_run = 0
            if not self._resync:
                self._frame_err(in_l0)

    def _frame_err(self, in_l0: bool) -> None:
        if in_l0:
            self.framing_errors += 1
            self.rcv_errors += 1
            self._resync = True

    def _pkt_end(self, err: bool, edb: bool = False, silent: bool = False) -> None:
        n = len(self._pkt)
        if n == 0:
            # A packet with no data byte is dropped; in L0 it is a framing
            # error that sets no resync, unless leaving Rx_L0 cut it
            # (HAS, Receive).
            self._pkt = None
            self._pkt_bad = False
            if not silent and self.state == L0 and self.rx_sub == RL0:
                self.framing_errors += 1
                self.rcv_errors += 1
            return
        bad = (err or self._pkt_bad or (edb and self._pkt_dllp) or n % 2 == 1 or n == 0
               or (self._pkt_dllp and n != 6))
        p = Packet(self._pkt_dllp, bytes(self._pkt), nullify=edb and not bad, err=bad)
        p.t = self.now
        self.rx_packets.append(p)
        self._pkt = None
        self._pkt_bad = False

    def _on_ts(self, ts: Ts | None) -> None:
        self.ts_rx_any += 1
        self.idle_run = 0
        self.ev_ts = True
        if ts is None or ts.kind.endswith("~"):
            if ts is not None and self.state in (PA, PCFG):
                self.polarity = True           # set once; cleared in Detect.Quiet
            for k in self.runs:
                self.runs[k] = 0
            return
        # Upstream: N is the link number of a TS1 that carries one (a data
        # symbol) in Configuration.Linkwidth.Start (ARCS.md).
        if (self.state == LWS and not self.dsp and ts.kind == "TS1"
                and ts.link is not None and ts.link >= 0):
            self.rx_link = ts.link
        runs = self.runs
        for name, pred in self.preds:
            runs[name] = runs[name] + 1 if pred(ts) else 0
        # The partner's N_FTS from the TS2 that advance Configuration.Complete
        # and Recovery.RcvrCfg (ARCS.md, "from these TS2").
        if self.state in (CC, RC) and ts.kind == "TS2" and runs["go"]:
            self.partner_n_fts = ts.n_fts
        if runs.get("go") and self.sent_after is None:
            self.sent_after = 0
        if runs.get("go", 0) >= 8:
            self.got8 = True
        if runs.get("mism") and self.sent_after_mism is None:
            self.sent_after_mism = 0
        if runs.get("mism", 0) >= 8:
            self.mism8 = True
        if runs.get("hr", 0) >= 2:
            self.hr_last = self.now
        if runs.get("dscr", 0) >= 2:
            self.dscr_rx = True

    def _on_skp(self) -> None:
        if self.state == L0 and self.rx_sub == RF and not self.inject.freeze:
            self._set_rx(RL0)

    def _on_eios(self) -> None:
        self.eios_rx = True
        if self.state == L0 and self.rx_sub == RL0 and not self.inject.freeze:
            if self.pm_state_req in (2, 3):
                self.ev_eios_pm = True
            else:
                self._set_rx(RE)

    # -------------------------------------------------------------- LTSSM

    def _eval(self) -> None:
        s, now, runs = self.state, self.now, self.runs
        timeout = self.deadline is not None and now >= self.deadline
        if s == DQ:
            if (self.pcs_ready and not self.pd_pending and self.pd == P1
                    and (timeout or not self.rx_ei)):
                self._go(DA)
        elif s == DA:
            if self.detect_result is not None and not self.detect_rx:
                self._go(PA if self.detect_result else DQ)
        elif s == PA:
            if self.ts_sent >= POLLING_ACTIVE_MIN_TS1 and runs["go"] >= 8:
                self._go(PCFG)
            elif timeout:
                self._go(PC if not self.left_ei else DQ)
        elif s == PC:
            if not self.rx_ei:
                self._go(PA)
        elif s == PCFG:
            if self.got8 and (self.sent_after or 0) >= 16:
                self._go(LWS)
            elif timeout:
                self._go(DQ)
        elif s == LWS:
            if self.loopback_req or runs["lb"] >= 2:
                self._go(LBE)
            elif (self.dsp and self.disable_req) or runs["dis"] >= 2:
                self._go(DIS)
            elif runs["go"] >= 2:
                self._go(LWA)
            elif timeout:
                self._go(DQ)
        elif s == LWA:
            if runs["go"] >= 2:
                self._go(LNW)
            elif runs["pad"] >= 2 or timeout:
                self._go(DQ)
        elif s == LNW:
            if runs["go"] >= 2:
                self._go(LNA)
            elif runs["pad"] >= 2 or timeout:
                self._go(DQ)
        elif s == LNA:
            if runs["go"] >= 2:
                self._go(CC)
            elif runs["renum"] >= 2:
                self._go(LNW)
            elif runs["pad"] >= 2 or timeout:
                self._go(DQ)
        elif s == CC:
            if self.got8 and (self.sent_after or 0) >= 16:
                self._go(CI)
            elif timeout:
                self._go(DQ)
        elif s in (CI, RI):
            if s == RI and self._recovery_idle_exit():
                return
            if self.idle8 and (self.idle_sent_after or 0) >= 16:
                self._go(L0)
            elif timeout:
                if self.idle_to_rlock < IDLE_TO_RLOCK_MAX:
                    self.idle_to_rlock += 1
                    self._go(RL)
                else:
                    self._go(DQ)
        elif s == L0:
            self._eval_l0()
        elif s == RL:
            if runs["go"] >= 8:
                self._go(RC)
            elif timeout:
                self._go(LWS if self.ts_rx_any else DQ)
        elif s == RC:
            if self.got8 and (self.sent_after or 0) >= 16:
                self._go(RI)
            elif self.mism8 and (self.sent_after_mism or 0) >= 16:
                self._go(LWS)
            elif timeout:
                self._go(DQ)
        elif s == L1E:
            if (not self.txq and self.tx_ei and self.ei_cycles >= TX_IDLE_MIN_CYCLES
                    and self.pd == P1 and not self.pd_pending):
                self._go(L1I)
        elif s == L1I:
            if self.pm_state_req in (0, 1) or self.main_ei_exit:
                self._go(RL)
        elif s == L2I:
            if (self.pd == P2 and not self.pd_pending
                    and (self.pm_state_req != 3 or self.main_ei_exit)):
                self._go(DQ)
        elif s == DIS:
            if self.directed:
                if not self.disable_req and self.disable_ts_left == 0 and not self.txq \
                        and self.tx_ei:
                    self._go(DQ)
            elif (self.eios_rx and self.ei_after_eios and not self.rx_ei) or (
                    timeout and not self.eios_rx):
                self._go(DQ)
        elif s == HR:
            if self.directed:
                if now >= self.entered + self.dwell_cycles and not self.hot_reset_req:
                    self._go(DQ)
            elif now >= self.hr_last + self.dwell_cycles:
                self._go(DQ)
        elif s == LBE:
            if not self.directed or runs["lb"] >= 2:
                self._go(LBA)
            elif timeout:
                self._go(LBX)
        elif s == LBA:
            if (self.lb_master and not self.loopback_req) or (not self.lb_master and self.eios_rx):
                self._go(LBX)
        elif s == LBX:
            if not self.txq and self.tx_ei and self.ei_cycles >= TX_IDLE_MIN_CYCLES:
                self._go(DQ)

    def _recovery_idle_exit(self) -> bool:
        r = self.runs
        if self.dsp and self.hot_reset_req:
            self._go(HR)
        elif self.dsp and self.disable_req:
            self._go(DIS)
        elif self.loopback_req:
            self._go(LBE)
        elif r["hr"] >= 2:
            self._go(HR)
        elif r["dis"] >= 2:
            self._go(DIS)
        elif r["lb"] >= 2:
            self._go(LBE)
        elif r["lanepad"] >= 2:
            self._go(LWS)
        else:
            return False
        return True

    def _eval_l0(self) -> None:
        now = self.now
        # Receive L0s sub-machine.
        if self.rx_sub == RE and now >= self.rx_entered + TX_IDLE_MIN_CYCLES:
            self._set_rx(RXI)
        elif self.rx_sub == RXI and self.sub_ei_exit:
            self._set_rx(RF)
        elif self.rx_sub == RF and now >= self.rx_fts_deadline:
            self.rx_fts_timeout = True
        # Main exits.
        pm = self.pm_state_req in (2, 3)
        if not pm:
            self.ev_eios_pm = False
        eios_pm = pm and (self.ev_eios_pm or self.rx_sub in (RE, RXI))
        target = None
        if self.rx_fts_timeout:
            target = RL
        elif self.rx_sub == RL0 and (self.ev_ts or (self.rx_ei and not self.eios_rx)):
            target = RL
        elif self.dsp and (self.hot_reset_req or self.disable_req):
            target = RL
        elif self.loopback_req or self.retrain or (
                self.pm_wait and not pm and self.tx_ei and self.ei_cycles >= TX_IDLE_MIN_CYCLES):
            target = RL
        elif eios_pm and (self.dsp or self.pm_wait):
            target = L1E if self.pm_state_req == 2 else L2I
        elif (pm and not self.dsp and not self.pm_wait and self.tx_sub == TL0
              and not self.l0_exit and not any(it[3] for it in self.txq)):
            self.pm_wait = True                # send one EIOS, then hold electrical idle
            self.send_eios = True
        if target is not None and target == RL:
            self.l0_exit = True
        pending = target is not None or self.l0_exit
        if pending and target is None:
            target = RL
        # Transmit L0s sub-machine.
        tx = self.tx_sub
        if tx == TL0:
            if (not pending and self.pm_state_req == 1
                    and self.tx_idle_cycles >= self.l0s_idle_cycles):
                self._set_tx(TE)
        elif tx == TE:
            if (not self.txq and self.tx_ei and self.ei_cycles >= TX_IDLE_MIN_CYCLES
                    and self.pd == P0S and not self.pd_pending):
                self._set_tx(TI)
        elif tx == TI:
            if pending or self.tx_packets or self.pm_state_req != 1:
                self._set_tx(TF)
        elif tx == TF:
            if self.fts_left == 0 and self.fts_skp_done and not self.txq:
                self._set_tx(TL0)
        if target is None or self.tx_sub != TL0:
            return
        if target in (L1E, L2I) and any(it[3] for it in self.txq):
            return
        if self.rx_sub == RE or (self.rx_sub == RF and not self.rx_fts_timeout):
            return
        self.l0_exit = False
        self._go(target)

    # ---------------------------------------------------------- transmit

    def _tx_mode(self) -> str:
        """'ei' (electrical idle, after any EIOS), or the active unit kind."""
        s = self.state
        if self.inject.mute:
            return "ei"
        if s in (DQ, DA, L1E, L1I, L2I, LBX):
            return "ei"
        if s == DIS:
            return "dis" if self.disable_ts_left else "ei"
        if s == L0:
            return "ei" if self.tx_sub in (TE, TI) or self.pm_wait else "l0"
        if (s == LBA and not self.lb_master) or (s == LBE and not self.directed):
            return "loop"
        return "act"

    def _want_pd(self, mode: str) -> int:
        s = self.state
        if s in (DQ, DA, DIS, L1E, L1I):
            return P1 if (mode == "ei" and not self.txq) or s in (DQ, DA) else P0
        if s == L2I:
            return P2 if not self.txq else P0
        if s == L0 and self.tx_sub in (TE, TI):
            return P0S if not self.txq else P0
        return P0

    def _tx_word(self) -> PipeTx:
        mode = self._tx_mode()
        want = self._want_pd(mode)
        detect = False
        if mode == "ei" and self.send_eios and not self.tx_ei:
            self.send_eios = False
            if not self.inject.no_eios and not self.inject.mute:
                self.txq.extend(((1, 0xBC, False, False), (1, 0x7C, False, False),
                                 (1, 0x7C, False, False), (1, 0x7C, False, False)))
        if mode == "ei" and not self.txq:
            self.send_eios = False
        active = mode != "ei" or bool(self.txq)
        if active and (self.pd != P0 or self.pd_pending):
            active = False
        if not active:
            self.txq.clear()
            self.compl_words = 0
            if self.pd != want and not self.pd_pending and self.tx_ei:
                self.pd = want
                self.pd_pending = True
            if not self.tx_ei:
                self.tx_ei = True
                self.ei_cycles = 0
            self.ei_cycles += 1
            if self.state == DA and self.pd == P1 and not self.pd_pending:
                if self.detect_result is None:
                    self.detect_rx = True
                else:
                    self.detect_rx = False
            elif self.detect_rx and self.state != DA:
                self.detect_rx = False
            return PipeTx(elec_idle=True, detect_rx=self.detect_rx,
                          polarity=self.polarity, power_down=self.pd)
        self.tx_ei = False
        self.ei_cycles = 0
        detect = mode == "loop"
        while len(self.txq) < 2:
            self._refill(mode)
        a, b = self.txq.popleft(), self.txq.popleft()
        scr = self.tx_scr
        s0 = (a[0], scr.feed(a[0], a[1], a[2] and not self.scr_off))
        s1 = (b[0], scr.feed(b[0], b[1], b[2] and not self.scr_off))
        # TxCompliance on the first word of each pattern unit; pattern words
        # are not counted for the SKP schedule (SYMBOLS.md, SKP schedule).
        compliance = self.compl_words == 2
        if self.compl_words:
            self.compl_words -= 1
        elif mode != "loop":
            self.skp_credit += 2
        if self.state == L0 and self.tx_sub == TL0:
            idle = not a[3] and not b[3]       # no TLP or DLLP symbol; SKP is idle
            self.tx_idle_cycles = self.tx_idle_cycles + 1 if idle and not self.tx_packets else 0
        return PipeTx(syms=(s0, s1), elec_idle=False, detect_rx=detect,
                      compliance=compliance, polarity=self.polarity, power_down=self.pd)

    def _queue_ts(self, kind: str, link, lane, ctl: int = 0) -> None:
        f = self.inject.ts_script.popleft() if self.inject.ts_script else self.inject.ts
        kind = f.get("kind", kind)
        link = f.get("link", link)
        lane = f.get("lane", lane)
        n_fts = f.get("n_fts", self.n_fts)
        ctl = f.get("ctl", ctl)
        ident = TS1_ID if kind == "TS1" else TS2_ID
        if self.state == CC and kind == "TS2" and ctl & CTL_NO_SCRAMBLE:
            self.dscr_sent = True
        q = self.txq
        q.append((1, 0xBC, False, False))
        q.append((1, 0xF7, False, False) if link is None else (0, link, False, False))
        q.append((1, 0xF7, False, False) if lane is None else (0, lane, False, False))
        q.append((0, n_fts, False, False))
        q.append((0, RATE_GEN1, False, False))
        q.append((0, ctl, False, False))
        q.extend([(0, ident, False, False)] * 10)
        self.ts_sent += 1
        if self.sent_after is not None:
            self.sent_after += 1
        if self.sent_after_mism is not None:
            self.sent_after_mism += 1

    def _queue_skp(self) -> None:
        n = 3 + self.inject.skp_adjust
        self.inject.skp_adjust = 0
        self.txq.append((1, 0xBC, False, False))
        self.txq.extend([(1, 0x1C, False, False)] * n)
        self.skp_credit -= self.skp_interval

    def _queue_idle(self) -> None:
        self.txq.extend(((0, 0, True, False), (0, 0, True, False)))
        if self.idle_sent_after is not None:
            self.idle_sent_after += 2

    def _refill(self, mode: str) -> None:
        s = self.state
        if mode == "loop":
            self._queue_idle()
            return
        if s == PC:
            # Compliance pattern: K28.5 D21.5 K28.5 D10.2; TxCompliance on the
            # first K28.5 forces its negative disparity.
            self.compl_words = 2
            self.txq.extend(((1, 0xBC, False, False), (0, D21_5, False, False)))
            self.txq.extend(((1, 0xBC, False, False), (0, D10_2, False, False)))
            return
        if len(self.txq) == 0 and self.skp_credit >= self.skp_interval:
            if s != L0 or self.tx_sub == TL0:
                self._queue_skp()
                return
        n = self.link_number if self.dsp else self.rx_link
        if s == PA:
            self._queue_ts("TS1", None, None)
        elif s == PCFG:
            self._queue_ts("TS2", None, None)
        elif s == LWS:
            self._queue_ts("TS1", self.link_number if self.dsp else None, None)
        elif s == LWA:
            self._queue_ts("TS1", n, None)
        elif s in (LNW, LNA, RL):
            self._queue_ts("TS1", n, 0)
        elif s in (CC, RC):
            self._queue_ts("TS2", n, 0)
        elif s in (CI, RI):
            self._queue_idle()
        elif s == HR:
            self._queue_ts("TS1", n, 0, CTL_HOT_RESET)
        elif s == DIS:
            self._queue_ts("TS1", *self.ctl_fields, CTL_DISABLE)
            self.disable_ts_left -= 1
            if self.disable_ts_left == 0:
                self.send_eios = True
        elif s == LBE:                         # the master only; a slave loops
            self._queue_ts("TS1", *self.ctl_fields, CTL_LOOPBACK)
        elif s == LBA:
            self.txq.extend(((0, self.lb_count, False, False),
                             (0, (self.lb_count + 1) & 0xFF, False, False)))
            self.lb_count = (self.lb_count + 2) & 0xFF
        elif s == L0:
            self._refill_l0()
        else:
            self._queue_idle()

    def _refill_l0(self) -> None:
        if self.tx_sub == TF:
            if self.fts_left:
                self.txq.extend(((1, 0xBC, False, False), (1, 0x3C, False, False),
                                 (1, 0x3C, False, False), (1, 0x3C, False, False)))
                self.fts_left -= 1
            elif not self.fts_skp_done:
                self.fts_skp_done = True
                self._queue_skp()
                self.skp_credit = 0
            else:
                self._queue_idle()
            return
        if self.tx_packets and not self.l0_exit:
            p = self.tx_packets.popleft()
            q = self.txq
            q.append((1, 0x5C if p.dllp else 0xFB, False, True))
            q.extend((0, x, True, True) for x in p.data)
            q.append((1, 0xFE if p.nullify and not p.dllp else 0xFD, False, True))  # DLLP: ignored
            p.t = self.now
            return
        self._queue_idle()


def _decode_ts(ts: list) -> Ts | None:
    """Decode the 15 symbols after COM; a TS whose identifier symbols are
    the bit-inverted TS1/TS2 identifiers is returned as kind 'TS1~'/'TS2~'
    (received with inverted polarity)."""
    ids = ts[5:]
    first = ids[0]
    if first[0] or any(x != first for x in ids):
        return None
    kinds = {TS1_ID: "TS1", TS2_ID: "TS2", TS1_ID ^ 0xFF: "TS1~", TS2_ID ^ 0xFF: "TS2~"}
    kind = kinds.get(first[1])
    if kind is None:
        return None

    def num(sym):
        return None if sym == PAD else (-1 if sym[0] else sym[1])

    return Ts(kind, num(ts[0]), num(ts[1]), ts[2][1], ts[4][1])


# --------------------------------------------------------------------- link

_EI = (True, None)


class Pcs:
    """An ideal PCS for one PIPE MAC side. Hooks: ``present`` (the far end
    senses this side's receiver termination), ``invert`` (the wire into this
    receiver is inverted), ``corrupt`` (replace the next n received data
    symbols with EDB and report RxStatus 4) and ``status`` (RxStatus codes
    to report on the next received words)."""

    def __init__(self, reset_cycles: int = 16, phy_status_cycles: int = 4,
                 rx_detect_cycles: int = 64) -> None:
        self.reset_cycles = reset_cycles
        self.phy_status_cycles = phy_status_cycles
        self.rx_detect_cycles = rx_detect_cycles
        self.present = True
        self.invert = False
        self.corrupt = 0
        self.status: deque[int] = deque()
        self.ready_at: int | None = None
        self.pd = P1
        self.pulse_at: int | None = None
        self.pulse_status = 0
        self.detecting = False

    def ready(self, now: int) -> bool:
        return self.ready_at is not None and now >= self.ready_at

    def termination(self, now: int) -> bool:
        return self.present and self.ready(now)

    def busy(self) -> bool:
        return self.pulse_at is not None or self.detecting

    def control(self, tx: PipeTx, now: int, far_present: bool) -> None:
        if not tx.reset_n:
            self.ready_at, self.pd, self.pulse_at, self.detecting = None, P1, None, False
            return
        if self.ready_at is None:
            self.ready_at = now + self.reset_cycles
        if not self.ready(now):
            return
        if tx.power_down != self.pd:
            self.pd = tx.power_down
            self.pulse_at, self.pulse_status = now + self.phy_status_cycles, 0
        elif tx.detect_rx and self.pd == P1 and tx.elec_idle and not self.detecting \
                and self.pulse_at is None:
            self.detecting = True
            self.pulse_at = now + self.rx_detect_cycles
            self.pulse_status = 3 if far_present else 0
        if self.detecting and not tx.detect_rx and self.pulse_at is None:
            self.detecting = False

    def transmit(self, tx: PipeTx, line_in, now: int):
        if not self.ready(now) or tx.elec_idle or self.pd != P0 or not tx.reset_n:
            return _EI
        if tx.detect_rx:                       # loopback: retransmit what arrives
            return line_in
        return (False, tx.syms)

    def receive(self, tx: PipeTx, line_in, now: int) -> PipeRx:
        if not tx.reset_n or not self.ready(now):
            return PipeRx(phy_status=True)
        phy_status, status = False, 0
        if self.pulse_at is not None and now >= self.pulse_at:
            phy_status, status = True, self.pulse_status
            self.pulse_at = None
        ei, syms = line_in
        if ei:
            return PipeRx(elec_idle=True, phy_status=phy_status, status=status)
        if self.invert != bool(tx.polarity):
            syms = tuple(s if s[0] else (0, s[1] ^ 0xFF) for s in syms)
        if self.corrupt and not phy_status:
            for i, s in enumerate(syms):
                if not s[0]:
                    syms = syms[:i] + (EDB,) + syms[i + 1:]
                    self.corrupt -= 1
                    status = 4
                    break
        if self.status and not phy_status:
            status = self.status.popleft()
        return PipeRx(syms=syms, valid=True, elec_idle=False, status=status,
                      phy_status=phy_status)


class Link:
    """Two ideal PCS and a channel between two PIPE MAC sides, with
    ``delay`` clk_i cycles from one side's TxData to the other's RxData."""

    def __init__(self, delay: int = 12, **pcs_kw) -> None:
        if delay < 1:
            raise ValueError("delay: expected at least 1 cycle")
        self.pcs = (Pcs(**pcs_kw), Pcs(**pcs_kw))
        self.lines = (deque([_EI] * delay), deque([_EI] * delay))
        self.now = 0

    def quiet(self) -> bool:
        return (all(w is _EI or w[0] for line in self.lines for w in line)
                and not any(p.busy() for p in self.pcs))

    def step(self, tx_a: PipeTx, tx_b: PipeTx) -> tuple[PipeRx, PipeRx]:
        txs, now, pcs = (tx_a, tx_b), self.now, self.pcs
        arriving = (self.lines[1].popleft(), self.lines[0].popleft())  # into a, into b
        for i in (0, 1):
            pcs[i].control(txs[i], now, pcs[1 - i].termination(now))
        for i in (0, 1):
            self.lines[i].append(pcs[i].transmit(txs[i], arriving[i], now))
        rx = (pcs[0].receive(txs[0], arriving[0], now), pcs[1].receive(txs[1], arriving[1], now))
        self.now += 1
        return rx


class Pair:
    """Two ``Mac`` models back to back; ``b`` leaves reset ``skew_cycles``
    after ``a``. ``now`` counts cycles from the start; both leave reset at
    ``reset_cycles`` (b later by the skew)."""

    def __init__(self, a: Mac, b: Mac, skew_cycles: int = 0, delay: int = 12,
                 reset_cycles: int = 8, **pcs_kw) -> None:
        self.macs = (a, b)
        self.link = Link(delay, **pcs_kw)
        self.release = (reset_cycles, reset_cycles + skew_cycles)
        self.now = 0
        self.rx = [PipeRx(), PipeRx()]
        self.hooks: list = []

    def step(self) -> None:
        txs = []
        for i, m in enumerate(self.macs):
            m.rst = self.now < self.release[i]
            txs.append(m.step(self.rx[i]))
        self.rx = list(self.link.step(*txs))
        self.now += 1
        for h in list(self.hooks):
            if h(self):
                self.hooks.remove(h)

    def _skip(self) -> None:
        """Jump over cycles in which nothing can happen: both models in
        Detect.Quiet (or in reset) waiting for a timer with a quiet line."""
        if self.hooks or any(m.state != DQ for m in self.macs):
            return
        if not self.link.quiet():
            return
        ends = []
        for i, m in enumerate(self.macs):
            if self.now < self.release[i]:
                ends.append(self.release[i])
            elif (m.state == DQ and m.pcs_ready and not m.pd_pending and m.pd == P1
                  and m.tx_ei and not m.txq and m.rx_ei and not m.inject.freeze):
                ends.append(m.deadline)
            else:
                return
        n = min(ends) - self.now - 2
        if n > 0:
            for m in self.macs:
                m.now += n
            self.now += n
            self.link.now += n

    def run(self, cycles: int) -> None:
        end = self.now + cycles
        while self.now < end:
            self.step()

    def run_until(self, cond, limit: int, skip: bool = True) -> int:
        """Step until ``cond(self)``; return the cycle count. Raise if it
        takes more than ``limit`` cycles."""
        start = self.now
        while not cond(self):
            if self.now - start > limit:
                a, b = self.macs
                raise RuntimeError(f"condition not met in {limit} cycles: "
                                   f"a in {a.state}, b in {b.state}")
            if skip:
                self._skip()
            self.step()
        return self.now - start


# ------------------------------------------------------------------ helpers

def both_l0(p: Pair) -> bool:
    return all(m.state == L0 and m.tx_sub == TL0 and m.rx_sub == RL0 for m in p.macs)


def make_pair(a_role: str = "downstream", timeout_div: int = 1024,
              err_timeout_div: int = 1024, skew_cycles: int = 0, delay: int = 12,
              **mac_kw) -> Pair:
    b_role = "upstream" if a_role == "downstream" else "downstream"
    a = Mac(a_role, timeout_div, err_timeout_div, **mac_kw)
    b = Mac(b_role, timeout_div, err_timeout_div, **mac_kw)
    return Pair(a, b, skew_cycles=skew_cycles, delay=delay)


def first_arc(m: Mac, to: str, after: int = 0) -> int:
    return next(t for t, _, b in m.arcs if b == to and t >= after)


def make_packets(rng: random.Random, count: int, mix: str, tlp_bytes: int,
                 nullify_ratio: float) -> list[Packet]:
    """Packets as the data-link layer hands them down: a TLP is 2 + 12|16 +
    4n + 4 bytes, a DLLP 6 bytes."""
    out = []
    for _ in range(count):
        dllp = mix == "dllp" or (mix == "random" and rng.random() < 0.3)
        if dllp:
            n = 6
        elif mix == "random":
            n = 2 + rng.choice((12, 16)) + 4 * rng.randrange(0, 65) + 4
        else:
            n = tlp_bytes
        nullify = not dllp and rng.random() < nullify_ratio
        out.append(Packet(dllp, bytes(rng.randrange(256) for _ in range(n)), nullify))
    return out


# --------------------------------------------------------------------- perf

def train(timeout_div: int, err_timeout_div: int, skew_cycles: int,
          channel_cycles: int) -> dict[str, float]:
    p = make_pair("downstream", timeout_div, err_timeout_div, skew_cycles,
                  delay=12 + channel_cycles)
    p.run_until(both_l0, 2 * DETECT_QUIET_CYCLES + 4 * T24MS)
    up, trn = [], []
    for i, m in enumerate(p.macs):
        l0 = first_arc(m, L0)
        up.append(l0 - p.release[i])
        trn.append(l0 - first_arc(m, PA))
    return {
        "link_up_us_max": round(cycles_to_us(max(up)), 3),
        "link_up_us_min": round(cycles_to_us(min(up)), 3),
        "training_us_max": round(cycles_to_us(max(trn)), 3),
        "training_us_min": round(cycles_to_us(min(trn)), 3),
    }


def _trained(**kw) -> Pair:
    p = make_pair(**kw)
    p.run_until(both_l0, 200_000)
    return p


def retrain() -> dict[str, float]:
    p = _trained()
    a, b = p.macs
    p.run(100)
    t0 = p.now
    a.retrain_req = True
    p.run_until(lambda q: q.now > t0 + 2 and both_l0(q), 100_000)
    return {"recovery_us_max": round(cycles_to_us(p.now - t0), 3),
            "recovery_cycles": p.now - t0}


def l0s(n_fts: int) -> dict[str, float]:
    p = _trained(n_fts=n_fts)
    a, b = p.macs
    a.pm_state_req = b.pm_state_req = 1
    t0 = p.now
    p.run_until(lambda q: a.tx_sub == TI and b.rx_sub == RXI, 10_000)
    entry = p.now - t0
    p.run(50)
    t1 = p.now
    a.tx_packets.append(Packet(False, bytes(range(20))))
    p.run_until(lambda q: a.tx_sub == TL0, 10_000)
    exit_tx = p.now - t1
    p.run_until(lambda q: b.rx_packets, 10_000)
    got = b.rx_packets[-1]
    return {"l0s_entry_cycles": entry, "l0s_exit_cycles": exit_tx,
            "l0s_packet_cycles": p.now - t1,
            "packets_lost": 0 if got == Packet(False, bytes(range(20))) else 1}


def l1(state_req: int) -> dict[str, float]:
    p = _trained()
    a, b = p.macs                         # a downstream, b upstream
    a.pm_state_req = state_req
    b.pm_state_req = state_req
    t0 = p.now
    idle = L1I if state_req == 2 else L2I
    p.run_until(lambda q: a.state == idle and b.state == idle, 10_000)
    entry = p.now - t0
    p.run(200)
    t1 = p.now
    a.pm_state_req = b.pm_state_req = 0
    p.run_until(lambda q: q.now > t1 + 2 and both_l0(q), 400_000)
    return {"entry_cycles": entry, "exit_us": round(cycles_to_us(p.now - t1), 3)}


def traffic(rng: random.Random, count: int, mix: str, tlp_bytes: int,
            nullify_ratio: float, skp_interval: int) -> dict[str, float]:
    p = _trained(skp_interval=skp_interval)
    a, b = p.macs
    pkts = make_packets(rng, count, mix, tlp_bytes, nullify_ratio)
    for q in pkts:
        a.tx_packets.append(Packet(q.dllp, q.data, q.nullify))
    t0 = p.now
    p.run_until(lambda q: len(b.rx_packets) >= count, 40 * count * (tlp_bytes + 8))
    got = b.rx_packets
    lost = sum(1 for x, y in zip(pkts, got) if x != y) + abs(len(pkts) - len(got))
    span = got[-1].t - got[0].t
    payload = sum(len(q.data) for q in pkts[1:])
    thr = payload / span if span else 0.0
    return {"packets": count, "throughput_bytes_per_cycle": round(thr, 6),
            "packets_lost": lost, "framing_errors": b.framing_errors,
            "rcv_errors": b.rcv_errors}


_KINDS = {
    "train": {"kind", "timeout_div", "err_timeout_div", "skew_cycles", "channel_cycles"},
    "retrain": {"kind"},
    "l0s": {"kind", "n_fts"},
    "l1": {"kind", "state"},
    "traffic": {"kind", "packets", "mix", "tlp_bytes", "nullify_ratio", "skp_interval"},
    "arc_sweep": {"kind", "roles", "full"},
}


def _int(w: dict, key: str, default: int, lo: int, hi: int) -> int:
    return _check_int(f"workload.{key}", w.get(key, default), lo, hi)


def _num(w: dict, key: str, default: float, lo: float, hi: float) -> float:
    v = w.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"workload.{key}: expected a number in [{lo}, {hi}]")
    return float(v)


def perf(workload: dict, seed: int) -> dict[str, float]:
    """Measure one workload against the HAS targets.

    ``train``: a downstream and an upstream model back to back from reset to
    L0, with ``timeout_div``, ``err_timeout_div``, the upstream leaving reset
    up to ``skew_cycles`` later (drawn with the seed) and ``channel_cycles``
    of extra channel delay. ``retrain``: a retrain request in L0 back to L0.
    ``l0s``: entry into and exit from transmit L0s with ``n_fts``. ``l1``:
    entry into and exit from L1 (``state`` 2) or L2 (3). ``traffic``:
    ``packets`` packets (``mix`` tlp, dllp or random) downstream to
    upstream in L0. ``arc_sweep``: every arc of the HAS arc list, driven on
    one model by the other through the partner's hooks, for each of ``roles``
(default both); ``full`` false runs only the smoke episode.
    """
    kind = workload.get("kind")
    if kind not in _KINDS:
        raise ValueError(f"workload.kind: expected one of {sorted(_KINDS)}")
    unknown = set(workload) - _KINDS[kind]
    if unknown:
        raise ValueError(f"unknown workload key(s) {sorted(unknown)}; "
                         f"known: {sorted(_KINDS[kind])}")
    rng = random.Random(seed)
    if kind == "train":
        div = _int(workload, "timeout_div", 1, 1, DIV_MAX)
        ediv = _int(workload, "err_timeout_div", 1, 1, DIV_MAX)
        skew = rng.randint(0, _int(workload, "skew_cycles", 0, 0, 10_000_000))
        channel = _int(workload, "channel_cycles", 0, 0, 1000)
        return train(div, ediv, skew, channel)
    if kind == "retrain":
        return retrain()
    if kind == "l0s":
        return l0s(_int(workload, "n_fts", N_FTS_DEFAULT, 1, 255))
    if kind == "l1":
        return l1(_int(workload, "state", 2, 2, 3))
    if kind == "traffic":
        count = _int(workload, "packets", 0, 2, 100_000)
        mix = workload.get("mix", "tlp")
        if mix not in ("tlp", "dllp", "random"):
            raise ValueError("workload.mix: expected tlp, dllp or random")
        tlp_bytes = _int(workload, "tlp_bytes", 274, 2, 4122)
        if tlp_bytes % 2:
            raise ValueError("workload.tlp_bytes: expected an even number")
        nullify = _num(workload, "nullify_ratio", 0.0, 0.0, 1.0)
        interval = _int(workload, "skp_interval", SKP_INTERVAL_MIN,
                        SKP_INTERVAL_MIN, SKP_INTERVAL_MAX)
        return traffic(rng, count, mix, tlp_bytes, nullify, interval)
    roles = workload.get("roles", list(ROLES))
    if not isinstance(roles, list) or not roles or any(r not in ROLES for r in roles):
        raise ValueError(f"workload.roles: expected a non-empty list of {ROLES}")
    full = workload.get("full", True)
    if not isinstance(full, bool):
        raise ValueError("workload.full: expected true or false")
    pcie_mac_sweep.use(SimpleNamespace(**globals()))
    return pcie_mac_sweep.arc_sweep(roles, full)
