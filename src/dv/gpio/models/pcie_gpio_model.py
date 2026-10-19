"""Reference model of the pcie_gpio slot format and receive rules, written
from the HAS (wiki/src/arch/gpio/HAS.md: slots, idle words, receive) and
the link-up page (wiki/src/arch/gpio/LINK_UP.md: terms, rules). Pure
Python, no simulator; the unit DV (tb/gpio_env.py) uses it as the oracle
for what a sender puts on its pins and what a receiver makes of the slots
it receives.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Slot codes (HAS, slots).
IDLE, MID, SOP_TLP, SOP_DLLP, EOP, ABORT, ONE_TLP, ONE_DLLP = range(8)
CODE_NAMES = ("IDLE", "MID", "SOP_TLP", "SOP_DLLP", "EOP", "ABORT", "ONE_TLP", "ONE_DLLP")

# Idle words (HAS, idle words).
TRAIN = 0x005A
TRAIN_OK = 0x015A
IDLE_WORD = 0x00C3
HOT_RESET = 0x0096
WORD_NAMES = {TRAIN: "TRAIN", TRAIN_OK: "TRAIN_OK", IDLE_WORD: "IDLE", HOT_RESET: "HOT_RESET"}

# ltssm_state_o codes (HAS, link status; LINK_UP.md, states).
LTSSM_RESET, LTSSM_TRAINING, LTSSM_HOT_RESET, LTSSM_UP = 0x00, 0x04, 0x19, 0x20
LTSSM_CODES = (LTSSM_RESET, LTSSM_TRAINING, LTSSM_HOT_RESET, LTSSM_UP)

# Runs (LINK_UP.md, rules).
ALIGN_SLOTS = 8
DAMAGE_WINDOW = 8
UP_RUN = 8
DOWN_CLEAR_RUN = 4
DOWN_ANY_RUN = 64
HR_RUN = 4
QUIET_WORDS = 16
HR_WORDS = 32
WATCHDOG_XFERS = 64

# Transmit (HAS, transmit): a maximum packet in 16-bit beats.
MAX_PKT_BEATS = 75


def sop_code(dllp: bool, one: bool) -> int:
    if one:
        return ONE_DLLP if dllp else ONE_TLP
    return SOP_DLLP if dllp else SOP_TLP


def encode(code: int, data: int) -> list[tuple[int, int]]:
    """The four transfers of a slot, (control pin, data pins) each: the
    marker then code bits 0 to 2 on the control pin, beat bits [4t+3:4t] on
    the data pins of transfer t (HAS, slots)."""
    ctrl = (1, code & 1, code >> 1 & 1, code >> 2 & 1)
    return [(ctrl[t], data >> (4 * t) & 0xF) for t in range(4)]


def decode(transfers) -> tuple[int, int, int]:
    """(marker, code, data) of four transfers, oldest first."""
    ctrl = [c for c, _ in transfers]
    data = 0
    for t, (_, d) in enumerate(transfers):
        data |= (d & 0xF) << (4 * t)
    return ctrl[0], ctrl[1] | ctrl[2] << 1 | ctrl[3] << 2, data


def slot_name(code: int, data: int) -> str:
    if code == IDLE:
        return WORD_NAMES.get(data, f"word {data:#06x}")
    return CODE_NAMES[code]


@dataclass
class Beat:
    """One beat on the receive stream."""
    sop: bool
    eop: bool
    dllp: bool
    err: bool
    data: int


@dataclass
class SlotResult:
    """What the receive rules make of one slot."""
    pulse: bool = False                      # rcv_err_o
    beat: Beat | None = None                 # delivered (when delivering)
    kind: str = "other"                      # for the runs: train, train_ok, idle, hot_reset,
                                             # packet, discarded, damaged, other


@dataclass
class RxTable:
    """The receive table of the HAS (receive) for one aligned receiver,
    slot by slot: an open packet, the discard state after a pulse, and the
    delivery of beats while delivering is on (the link is up)."""
    open: bool = False
    open_dllp: bool = False
    deliv: bool = False
    discard: bool = False
    log: list = field(default_factory=list)

    def close(self) -> Beat | None:
        beat = Beat(False, True, self.open_dllp, True, 0) if self.deliv else None
        self.open = self.deliv = False
        return beat

    def slot(self, marker: int, code: int, data: int, up: bool = True) -> SlotResult:
        r = SlotResult()
        if not marker:
            r.pulse, r.kind = True, "damaged"
            if self.open:
                r.beat = self.close()
            self.discard = True
            return r
        if code == IDLE:
            self.discard = False
            if self.open:
                r.pulse, r.kind = True, "damaged"
                r.beat = self.close()
                self.discard = True
                return r
            r.kind = {TRAIN: "train", TRAIN_OK: "train_ok", IDLE_WORD: "idle",
                      HOT_RESET: "hot_reset"}.get(data, "damaged")
            if r.kind == "damaged":
                r.pulse = True
                self.discard = True
            return r
        if code in (SOP_TLP, SOP_DLLP, ONE_TLP, ONE_DLLP):
            self.discard = False
            if self.open:
                r.pulse, r.kind = True, "damaged"
                r.beat = self.close()
                self.discard = True
                return r
            one = code in (ONE_TLP, ONE_DLLP)
            dllp = code in (SOP_DLLP, ONE_DLLP)
            r.kind = "packet"
            if up:
                r.beat = Beat(True, one, dllp, False, data)
                self.deliv = not one
            self.open, self.open_dllp = not one, dllp
            return r
        if self.discard:
            r.kind = "discarded"
            return r
        if not self.open:
            r.pulse, r.kind = True, "damaged"
            self.discard = True
            return r
        r.kind = "packet"
        if code == ABORT:
            r.beat = self.close()
            return r
        eop = code == EOP
        if self.deliv:
            r.beat = Beat(False, eop, self.open_dllp, False, data)
        if eop:
            self.open = self.deliv = False
        return r

    def link_down(self) -> Beat | None:
        """At link-down an open delivered packet is closed with an error eop."""
        beat = Beat(False, True, self.open_dllp, True, 0) if self.deliv else None
        self.deliv = False
        return beat

