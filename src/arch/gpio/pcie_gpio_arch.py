"""Architecture model of pcie_gpio, the GPIO data-link-boundary adapter
(wiki/src/arch/gpio/HAS.md, LINK_UP.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). It states the adapter's timing as the HAS and MAS fix
it and measures the HAS targets:

- the latencies: ``L_tx`` (a beat taken to its first transfer, at a slot
  boundary), ``L_rx`` (a slot's last transfer to its beat on the receive
  stream) and the one-way latency ``W`` that the link-up rules bound to 4
  slots of the slower end;
- the line use of back-to-back packets under the ready rule (between
  packets ready only with room for a maximum packet in a buffer of D = 75
  beats);
- the link-up time of two ends at clock ratios from 1/2 to 2, from a
  slot-level model of the link-up rules (alignment, the quiet run, the run
  of 8 good slots);
- the loaded replay round trip of the dll over the adapter, which sizes
  the GPIO build's replay timeout (link HAS, replay timeout at LOWER 1).

Clock periods are in arbitrary units; a ratio r means the far end's clock
period is r times the near end's. Python standard library only.
"""

from __future__ import annotations

import heapq
import math
import random

# ------------------------------------------------------------ constants

CLK_DIV = 2                 # the default (HAS, parameters)
TRANSFERS_PER_SLOT = 4      # HAS, slots
DEPTH = 75                  # transmit buffer D, beats (MAS)
MAX_PKT_BEATS = 75          # a maximum TLP (HAS, transmit)
DLLP_BEATS = 3              # a DLLP: 6 bytes
L_TX = 2                    # cycles from a beat taken to its first transfer, at a slot boundary (MAS)
RX_SYNC = 4                 # receiver cycles from a slot pushed to its beat on the stream (MAS)
ALIGN_SLOTS = 8             # LINK_UP.md, rules
UP_RUN = 8
QUIET_WORDS = 16
W_BOUND_SLOTS = 4           # LINK_UP.md, terms: W at most 4 slots of the slower end
DLL_ACK_CYCLES = 35         # far dll, eop to Ack offered (dll MAS timing)
DLL_ACT_CYCLES = 3          # near dll, Ack eop to acting (dll MAS timing)
REPLAY_TIMEOUT_MAX = 65535  # the dll parameter's range


def slot_cycles(clk_div: int = CLK_DIV) -> int:
    """S: one slot in the sender's clk_i cycles."""
    return TRANSFERS_PER_SLOT * clk_div


# ------------------------------------------------------------- latencies

def l_rx(clk_div: int = CLK_DIV, ratio: float = 1.0) -> float:
    """L_rx in receiver cycles, the sender's clock ``ratio`` times the
    receiver's period: the receiver captures the last transfer mid-transfer
    and pushes the slot at the next forwarded-clock edge, half a transfer
    after the slot ends; then the crossing and the output register."""
    return clk_div / 2 * ratio + RX_SYNC


def w_cycles(clk_div: int = CLK_DIV, ratio: float = 1.0) -> float:
    """W in receiver cycles, from a beat taken at a slot boundary to its
    handling at the receiver: L_tx, the slot's four transfers, the capture
    and push of the last one, and the crossing (handled one cycle before
    the output register)."""
    sender = L_TX + slot_cycles(clk_div) + clk_div / 2
    return sender * ratio + RX_SYNC - 1


def w_slots(clk_div: int = CLK_DIV, ratio: float = 1.0) -> float:
    """W in slots of the slower end; ``ratio`` is the sender's period over
    the receiver's."""
    cycles = w_cycles(clk_div, ratio)                    # receiver cycles
    slower = max(1.0, ratio)                             # slower period, in receiver periods
    return cycles / (slot_cycles(clk_div) * slower)


# -------------------------------------------------------------- line use

def line_use(packets: list[int], clk_div: int = CLK_DIV) -> dict[str, float]:
    """One direction, the dll offering ``packets`` (beats each) back to back
    and a beat every cycle inside a packet. Cycle by cycle: a sop is taken
    only on an empty buffer (room for a maximum packet in D = 75), a beat
    taken at edge e can leave at a slot start from edge e + 2, and each slot
    start sends one buffered beat or an idle word."""
    s = slot_cycles(clk_div)
    buf: list[int] = []          # edge from which each buffered beat can leave
    pending = list(packets)
    pkt_left = 0
    cycle = 0
    slots = busy = 0
    while pending or pkt_left or buf:
        if cycle % s == 0:
            slots += 1
            if buf and buf[0] <= cycle:
                buf.pop(0)
                busy += 1
        if pkt_left:
            buf.append(cycle + 2)
            pkt_left -= 1
        elif pending and not buf:
            pkt_left = pending.pop(0) - 1
            buf.append(cycle + 2)
        cycle += 1
    return {"slots": slots, "busy_slots": busy, "line_use": busy / slots}


# -------------------------------------------------------------- link-up

class _End:
    """One end of the slot-level link-up model: it sends a slot every own
    slot time and acts on each received slot by the LINK_UP.md rules."""

    def __init__(self, period: float, start: float) -> None:
        self.period, self.start = period, start
        self.aligned = False
        self.align = 0
        self.quiet = 0
        self.run = 0
        self.up_at: float | None = None

    def send(self) -> str:
        """The word of the next slot (TRAIN, TRAIN_OK or IDLE)."""
        if self.up_at is not None:
            return "IDLE"
        if self.aligned and self.quiet >= QUIET_WORDS:
            return "TRAIN_OK"
        self.quiet += 1
        return "TRAIN"

    def receive(self, word: str, t: float) -> None:
        if not self.aligned:
            self.align += 1
            self.aligned = self.align >= ALIGN_SLOTS
            return
        if self.up_at is not None:
            return
        good = word in ("TRAIN_OK", "IDLE") and self.quiet >= QUIET_WORDS
        self.run = self.run + 1 if good else 0
        if self.run >= UP_RUN:
            self.up_at = t


def link_up(ratio: float, skew: float, clk_div: int = CLK_DIV) -> float:
    """Both ends from their reset release to up; end B's clock period is
    ``ratio`` times A's and B starts ``skew`` A cycles after A. Returns the
    time both are up after the later start, in slots of the slower end."""
    s = slot_cycles(clk_div)
    ends = (_End(1.0, 0.0), _End(ratio, skew))
    q: list[tuple[float, int, int, str]] = []
    seq = 0
    for i, e in enumerate(ends):
        heapq.heappush(q, (e.start, seq, i, "tx"))
        seq += 1
    while q:
        t, _, i, kind = heapq.heappop(q)
        e, far = ends[i], ends[1 - i]
        if kind == "tx":
            word = e.send()
            # The slot reaches the far end W after its start.
            w = w_cycles(clk_div, e.period / far.period) * far.period
            heapq.heappush(q, (t + w, seq, 1 - i, word))
            heapq.heappush(q, (t + s * e.period, seq + 1, i, "tx"))
            seq += 2
        else:
            e.receive(kind, t)
        if all(x.up_at is not None for x in ends):
            break
        if t > 1e7:
            raise RuntimeError("link-up model did not converge")
    later = max(e.start for e in ends)
    up = max(e.up_at for e in ends)
    return (up - later) / (s * max(e.period for e in ends))


# ---------------------------------------------------------------- replay

def replay_round_trip(clk_div: int = CLK_DIV, ratio: float = 1.0,
                      l_cycles: float | None = None) -> float:
    """The loaded round trip, in near cycles, from a TLP's eop taken by the
    near adapter to its Ack acting at the near dll, the far clock period
    ``ratio`` times the near's. Near: the TLP's D slots, a slot-phase
    wait, L and the dll's 3 cycles. Far, in far cycles: the dll's 35 cycles
    to the Ack, the far buffer draining a maximum packet and then the packet
    the far dll had already started (the dll finishes a started packet, dll
    RETRY transmit priority), a slot-phase wait, the Ack's 3 slots and L.
    L = L_tx + L_rx, by default the MAS bound 3 S."""
    s = slot_cycles(clk_div)
    big_l = 3 * s if l_cycles is None else l_cycles
    near = DEPTH * s + (s - 1) + big_l + DLL_ACT_CYCLES
    far = DLL_ACK_CYCLES + 2 * DEPTH * s + (s - 1) + DLLP_BEATS * s + big_l
    return near + ratio * far


def replay_timeout(clk_div: int = CLK_DIV) -> int:
    """The GPIO build's replay timeout: the round trip at the clock
    contract's worst ratio, 2, within the dll's range."""
    return min(REPLAY_TIMEOUT_MAX, math.ceil(replay_round_trip(clk_div, 2.0)))


# ------------------------------------------------------------------ perf

RATIOS = (0.5, 1.0, 2.0)


def perf(workload: dict, seed: int) -> dict[str, float]:
    kind = workload.get("kind")
    clk_div = int(workload.get("clk_div", CLK_DIV))
    if clk_div < 2 or clk_div > 64 or clk_div % 2:
        raise ValueError(f"clk_div {clk_div}: even, 2 to 64")
    rng = random.Random(seed)
    if kind == "latency":
        ratios = workload.get("ratios", list(RATIOS))
        return {
            "l_tx_cycles": float(L_TX),
            "l_rx_cycles_max": max(l_rx(clk_div, r) for r in ratios),
            "w_slots_max": max(w_slots(clk_div, r) for r in ratios),
        }
    if kind == "link_up":
        trials = int(workload["trials"])
        skew = float(workload["skew_cycles"])
        worst = 0.0
        for _ in range(trials):
            r = rng.choice(workload.get("ratios", list(RATIOS)))
            worst = max(worst, link_up(r, rng.uniform(0, skew), clk_div))
        return {"up_slots_max": worst}
    if kind == "traffic":
        n = int(workload["packets"])
        mix = workload.get("mix", "max_tlp")
        if mix == "max_tlp":
            pkts = [MAX_PKT_BEATS] * n
        elif mix == "dll":
            pkts = [rng.choice((DLLP_BEATS, rng.randint(9, MAX_PKT_BEATS))) for _ in range(n)]
        else:
            raise ValueError(f"mix {mix}: max_tlp or dll")
        one_way = line_use(pkts, clk_div)
        # Both ways, each TLP of 128 bytes is acknowledged by an Ack and its
        # credits returned by an UpdateFC on the reverse line.
        payload_beats = 64
        both = (payload_beats * n) / one_way["slots"] / (1 + 2 * DLLP_BEATS / MAX_PKT_BEATS)
        return {"line_use": one_way["line_use"], "payload_beats_per_slot_bidir": both}
    if kind == "replay":
        rt = {r: replay_round_trip(clk_div, r) for r in RATIOS}
        to = replay_timeout(clk_div)
        return {"round_trip_cycles_equal": rt[1.0], "round_trip_cycles_max": max(rt.values()),
                "replay_timeout_cycles": float(to),
                "replay_margin_cycles": to - max(rt.values())}
    raise ValueError(f"workload kind {kind}: latency, link_up, traffic or replay")
