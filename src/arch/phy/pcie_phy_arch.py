"""Architecture model of pcie_phy, the Gen1 x1 physical-layer stack
(wiki/src/arch/phy/HAS.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). It states the stack-level behavior the HAS fixes and
measures it against the HAS targets:

- framing of the upper packet stream into a symbol stream with STP, SDP,
  END, EDB, logical idle and scheduled SKP ordered sets, and the reverse;
- an elastic buffer that adds or removes SKP symbols across the RX clock
  offset;
- stack latency from the per-block budgets;
- a link-up time estimate for two stacks back to back.

It is the shared stack reference for the phy e2e. The mac and pcs models
plug in later through ``MacBudget`` and ``PcsBudget``: any object with the
same attributes can be passed to ``Stack``. The training estimate is
behavioral (a reduced LTSSM); the exact states and arcs belong to the mac
HAS. Python standard library only.
"""

from __future__ import annotations

import math
import random

# ---------------------------------------------------------------- constants

# Spec facts are cited where they are stated; the rest are this design's
# choices from the HAS.
CORE_MHZ = 125.0            # clk_i and PCLK (HAS, Clocks and resets)
SYMBOLS_PER_CYCLE = 2       # 16-bit PIPE word, 2x10-bit lower interface
SYMBOL_NS = 4.0             # 2.5 GT/s, 10 bits per symbol [cite: PCIe Base Spec, rev 3.0, §4.2.1.1]
BYTES_PER_CYCLE_MAX = 2.0   # one 16-bit beat per clk_i cycle
TS_SYMBOLS = 16             # TS1 and TS2 length [cite: PCIe Base Spec, rev 3.0, §4.2.4.1]
SKP_OS_SYMBOLS = 4          # COM plus three SKP [cite: PCIe Base Spec, rev 3.0, §4.2.7]
SKP_INTERVAL_MIN = 1180     # symbol times between SKP ordered sets [cite: PCIe Base Spec, rev 3.0, §4.2.7]
SKP_INTERVAL_MAX = 1538
PPM_MAX = 600               # two ends at +-300 ppm each [cite: PCIe Base Spec, rev 3.0, §4.2.7]
DETECT_QUIET_CYCLES = 1_500_000   # 12 ms at 125 MHz [cite: PCIe Base Spec, rev 3.0, §4.2.6.1]
POLLING_ACTIVE_MIN_TS1 = 1024     # [cite: PCIe Base Spec, rev 3.0, §4.2.6.2]
DLLP_BYTES = 6              # [cite: PCIe Base Spec, rev 3.0, §4.2.1.2]
TIMEOUT_DIV_MAX = 1024      # HAS Parameters: divides Detect.Quiet (and Hot Reset, not modelled)

# Special symbols as (is_k, byte) [cite: PCIe Base Spec, rev 3.0, §4.2.1.1].
COM = (1, 0xBC)
STP = (1, 0xFB)
SDP = (1, 0x5C)
END = (1, 0xFD)
EDB = (1, 0xFE)
SKP = (1, 0x1C)
IDLE = (0, 0x00)            # logical idle, scrambled on the line [cite: PCIe Base Spec, rev 3.0, §4.2.1.2]


def cycles_to_us(cycles: float) -> float:
    return cycles / CORE_MHZ


# ------------------------------------------------------------------ budgets

# Plain classes, not dataclasses: ./build arch imports this file by path
# without registering it in sys.modules, which dataclasses need.

class MacBudget:
    """What the stack needs from the mac. Latencies in clk_i cycles."""

    def __init__(self, tx_latency_cycles: int = 3, rx_latency_cycles: int = 3,
                 skp_interval_symbols: int = SKP_INTERVAL_MIN) -> None:
        self.tx_latency_cycles = tx_latency_cycles  # beat accepted -> PIPE TxData
        self.rx_latency_cycles = rx_latency_cycles  # PIPE RxData -> beat out, low lane
        self.skp_interval_symbols = skp_interval_symbols


class PcsBudget:
    """What the stack needs from the pcs. Latencies in clk_i cycles."""

    def __init__(self, tx_latency_cycles: int = 2, rx_fixed_cycles: int = 6,
                 eb_depth_symbols: int = 16, eb_nominal_symbols: int = 8,
                 reset_cycles: int = 16, rx_detect_cycles: int = 64) -> None:
        self.tx_latency_cycles = tx_latency_cycles  # PIPE TxData -> ser_tx_data_o
        self.rx_fixed_cycles = rx_fixed_cycles      # ser_rx_data_i -> RxData, EB fill excluded
        self.eb_depth_symbols = eb_depth_symbols
        self.eb_nominal_symbols = eb_nominal_symbols
        self.reset_cycles = reset_cycles            # rst_i release -> PhyStatus low
        self.rx_detect_cycles = rx_detect_cycles    # TxDetectRx -> PhyStatus with the result


class Stack:
    """One pcie_phy: a mac and a pcs with their budgets. A mac or pcs model
    plugs in by passing any object with the budget attributes."""

    def __init__(self, mac: MacBudget | None = None, pcs: PcsBudget | None = None,
                 timeout_div: int = 1) -> None:
        self.mac = mac if mac is not None else MacBudget()
        self.pcs = pcs if pcs is not None else PcsBudget()
        if not 1 <= timeout_div <= TIMEOUT_DIV_MAX:
            raise ValueError(f"timeout_div: expected 1 to {TIMEOUT_DIV_MAX}")
        self.timeout_div = timeout_div

    def tx_latency_cycles(self) -> int:
        return self.mac.tx_latency_cycles + self.pcs.tx_latency_cycles

    def rx_latency_cycles(self, eb_fill_symbols: int, odd_lane: bool) -> int:
        return (self.pcs.rx_fixed_cycles + math.ceil(eb_fill_symbols / 2)
                + self.mac.rx_latency_cycles + int(odd_lane))


# ------------------------------------------------------------------ packets

class Packet:
    """One upper-stream packet: tag, bytes and the EDB (nullify) flag."""

    def __init__(self, dllp: bool, data: bytes, nullify: bool = False) -> None:
        self.dllp, self.data, self.nullify = dllp, bytes(data), nullify

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Packet) and (self.dllp, self.data, self.nullify)
                == (other.dllp, other.data, other.nullify))

    def __repr__(self) -> str:
        return f"Packet(dllp={self.dllp}, {len(self.data)} bytes, nullify={self.nullify})"

    def check(self) -> None:
        if len(self.data) < 2 or len(self.data) % 2:
            raise ValueError("a packet is a positive, even number of bytes")
        if self.dllp and (len(self.data) != DLLP_BYTES or self.nullify):
            raise ValueError("a DLLP is exactly 6 bytes and is never nullified")


def frame(packets: list[Packet], skp_interval: int = SKP_INTERVAL_MIN,
          lead_idle_symbols: int = 0) -> tuple[list[tuple[int, int]], list[int]]:
    """Transmit framing for back-to-back ``packets``.

    Returns the symbol stream and, per packet, the index of its first
    (framing) symbol. A SKP ordered set is due every ``skp_interval``
    symbols; one that falls due inside a packet waits for its end, and all
    that are due go out back to back. Packets and SKP ordered sets have even
    lengths, so every packet starts in the low symbol of a cycle.
    """
    out: list[tuple[int, int]] = []
    starts: list[int] = []
    credit = 0

    def emit(sym: tuple[int, int]) -> None:
        nonlocal credit
        out.append(sym)
        credit += 1

    def boundary() -> None:
        nonlocal credit
        while credit >= skp_interval:
            credit -= skp_interval
            out.extend([COM, SKP, SKP, SKP])
            credit += SKP_OS_SYMBOLS

    for _ in range(lead_idle_symbols):
        emit(IDLE)
    for p in packets:
        p.check()
        boundary()
        starts.append(len(out))
        emit(SDP if p.dllp else STP)
        for b in p.data:
            emit((0, b))
        emit(EDB if p.nullify else END)
    boundary()
    return out, starts


def deframe(symbols: list[tuple[int, int]]) -> tuple[list[Packet], list[int], int]:
    """Receive deframing. Returns the packets, the index of each packet's
    framing symbol and the count of framing errors. A framing error is a
    symbol outside a packet that is not idle or part of a SKP ordered set,
    or a K symbol inside a packet other than END or EDB, which drops the
    packet. After an error, symbols up to the next END, EDB, STP or SDP are
    discarded without further counts. A packet that ends in EDB is
    returned with ``nullify`` set."""
    pkts: list[Packet] = []
    starts: list[int] = []
    errors = 0
    cur: list[int] | None = None
    dllp = False
    resync = False
    for i, sym in enumerate(symbols):
        if sym in (STP, SDP):
            if cur is not None:
                errors += 1
                starts.pop()
            cur, dllp, resync = [], sym == SDP, False
            starts.append(i)
        elif cur is not None and not sym[0]:
            cur.append(sym[1])
        elif cur is not None and sym in (END, EDB):
            pkts.append(Packet(dllp, bytes(cur), sym == EDB))
            cur = None
        elif cur is not None:
            errors += 1
            starts.pop()
            cur, resync = None, True
        elif resync:
            resync = sym not in (END, EDB)
        elif sym not in (COM, SKP, IDLE):
            errors += 1
            resync = True
    return pkts, starts, errors


# ----------------------------------------------------------- elastic buffer

def elastic(symbols: list[tuple[int, int]], ppm: float, depth: int,
            nominal: int) -> tuple[list[tuple[int, int]], list[int], dict[str, int]]:
    """RX elastic buffer with the RX clock ``ppm`` off the core clock
    (positive: the RX clock is fast, so the buffer fills).

    The fill starts at ``nominal`` and drifts by ``ppm`` symbols per million
    received. On each received SKP ordered set the buffer removes one SKP
    when the fill is a symbol or more above nominal, and adds one when it
    is a symbol or more below. Returns the symbols as read out, the fill
    seen by each of them, and statistics.
    """
    out: list[tuple[int, int]] = []
    fills: list[int] = []
    drift = 0.0
    fill = nominal
    stats = {"added": 0, "removed": 0, "fill_min": nominal,
             "fill_max": nominal, "overflow": 0}
    i = 0
    while i < len(symbols):
        if symbols[i] == COM and symbols[i + 1:i + 2] == [SKP]:
            n = 0
            while i + 1 + n < len(symbols) and symbols[i + 1 + n] == SKP:
                n += 1
            consumed = 1 + n
            if fill >= nominal + 1 and n > 1:
                n, fill = n - 1, fill - 1
                stats["removed"] += 1
            elif fill <= nominal - 1:
                n, fill = n + 1, fill + 1
                stats["added"] += 1
            seg = [COM] + [SKP] * n
        else:
            consumed, seg = 1, [symbols[i]]
        for sym in seg:
            out.append(sym)
            fills.append(fill)
        i += consumed
        drift += ppm * 1e-6 * consumed
        step = math.trunc(drift)
        drift -= step
        fill += step
        stats["fill_min"] = min(stats["fill_min"], fill)
        stats["fill_max"] = max(stats["fill_max"], fill)
        if not 0 <= fill <= depth:
            stats["overflow"] += 1
    return out, fills, stats


# ------------------------------------------------------------------ traffic

def make_packets(rng: random.Random, count: int, mix: str, tlp_bytes: int,
                 nullify_ratio: float) -> list[Packet]:
    """Packets as the data-link layer would hand them down: a TLP is a
    sequence number, a 3DW or 4DW header, a payload of whole DWs and an
    LCRC (2 + 12|16 + 4n + 4 bytes); a DLLP is 6 bytes."""
    pkts = []
    for _ in range(count):
        dllp = mix == "dllp" or (mix == "random" and rng.random() < 0.3)
        if dllp:
            n = DLLP_BYTES
        elif mix == "random":
            n = 2 + rng.choice((12, 16)) + 4 * rng.randrange(0, 65) + 4
        else:
            n = tlp_bytes
        nullify = not dllp and rng.random() < nullify_ratio
        pkts.append(Packet(dllp, bytes(rng.randrange(256) for _ in range(n)), nullify))
    return pkts


def traffic(stack: Stack, packets: list[Packet], ppm: float) -> dict[str, float]:
    """Send ``packets`` back to back through ``stack`` and receive them at a
    partner whose RX clock is ``ppm`` off; measure throughput and latency."""
    syms, tx_starts = frame(packets, stack.mac.skp_interval_symbols)
    rx_syms, fills, eb = elastic(syms, ppm, stack.pcs.eb_depth_symbols,
                                 stack.pcs.eb_nominal_symbols)
    got, rx_starts, framing_errors = deframe(rx_syms)
    lost = sum(1 for a, b in zip(packets, got) if a != b) + abs(len(packets) - len(got))
    payload = sum(len(p.data) for p in packets)
    first = tx_starts[0]
    last = len(syms) - 1
    while syms[last] != END and syms[last] != EDB:
        last -= 1
    cycles = (last - first) // SYMBOLS_PER_CYCLE + 1
    thr = payload / cycles
    rx_lat = [stack.rx_latency_cycles(fills[s], s % 2 == 1) for s in rx_starts]
    return {
        "packets": len(packets),
        "payload_bytes": payload,
        "cycles": cycles,
        "throughput_bytes_per_cycle": round(thr, 6),
        "throughput_mbytes_per_s": round(thr * CORE_MHZ, 3),
        "efficiency_percent": round(100.0 * thr / BYTES_PER_CYCLE_MAX, 3),
        "skp_ordered_sets": sum(1 for i, s in enumerate(syms)
                                if s == COM and syms[i + 1:i + 2] == [SKP]),
        "tx_latency_cycles_max": stack.tx_latency_cycles(),
        "rx_latency_cycles_min": min(rx_lat),
        "rx_latency_cycles_max": max(rx_lat),
        "eb_fill_symbols_min": eb["fill_min"],
        "eb_fill_symbols_max": eb["fill_max"],
        "eb_overflows": eb["overflow"],
        "skp_added": eb["added"],
        "skp_removed": eb["removed"],
        "packets_lost": lost,
        "framing_errors": framing_errors,
        "nullified": sum(p.nullify for p in got),
    }


# ------------------------------------------------------------------ link-up

# Reduced LTSSM for the estimate: (state, sends, accepts, rx_consecutive,
# tx_after_first_rx, tx_min). A port leaves a state when it has sent at
# least tx_min slots, has received rx_consecutive matching slots in a row
# and has sent tx_after_first_rx slots since the first match. A slot is one
# ordered set (16 symbols); in Configuration.Idle it is 16 idle symbols.
# From Configuration on, a match also needs the partner in the same state
# or a later one. Polling counts follow
# [cite: PCIe Base Spec, rev 3.0, §4.2.6.2] and Configuration counts
# [cite: PCIe Base Spec, rev 3.0, §4.2.6.3]; TIMEOUT_DIV never scales them.
# The mac HAS owns the full state set and arcs.
TRAINING = (
    ("Polling.Active", "TS1", ("TS1", "TS2"), 8, 0, POLLING_ACTIVE_MIN_TS1),
    ("Polling.Configuration", "TS2", ("TS2",), 8, 16, 0),
    ("Configuration.Linkwidth.Start", "TS1", ("TS1",), 2, 0, 0),
    ("Configuration.Linkwidth.Accept", "TS1", ("TS1",), 2, 0, 0),
    ("Configuration.Lanenum.Wait", "TS1", ("TS1",), 2, 0, 0),
    ("Configuration.Lanenum.Accept", "TS1", ("TS1",), 2, 0, 0),
    ("Configuration.Complete", "TS2", ("TS2",), 8, 16, 0),
    ("Configuration.Idle", "IDLE", ("IDLE",), 1, 1, 0),
)
FIRST_CONFIG = 2
SLOT_CYCLES = TS_SYMBOLS // SYMBOLS_PER_CYCLE


class _Port:
    """Training state of one port in the link-up estimate."""

    def __init__(self, stack: Stack, reset_release: int) -> None:
        self.stack = stack
        self.reset_release = reset_release
        self.polling = 0
        self.state = 0
        self.sent = 0
        self.consec = 0
        self.first_rx: int | None = None
        self.next_slot = 0
        self.skp_credit = 0
        self.l0: int | None = None

    def receive(self, kind: str, phase: int) -> None:
        if self.l0 is not None:
            return
        _, _, accepts, _, _, _ = TRAINING[self.state]
        ok = kind in accepts and (self.state < FIRST_CONFIG or phase >= self.state)
        if ok:
            self.consec += 1
            if self.first_rx is None:
                self.first_rx = self.sent
        else:
            self.consec = 0

    def slot(self, t: int) -> tuple[str, int] | None:
        """At a slot boundary: maybe advance, then send one slot."""
        if self.l0 is not None:
            return None
        _, _, _, need, after, tx_min = TRAINING[self.state]
        if (self.sent >= tx_min and self.consec >= need
                and self.first_rx is not None and self.sent - self.first_rx >= after):
            self.state += 1
            self.sent, self.consec, self.first_rx = 0, 0, None
            if self.state == len(TRAINING):
                self.l0 = t
                return None
        self.sent += 1
        self.skp_credit += TS_SYMBOLS
        self.next_slot = t + SLOT_CYCLES
        if self.skp_credit >= self.stack.mac.skp_interval_symbols:
            self.skp_credit -= self.stack.mac.skp_interval_symbols
            self.next_slot += SKP_OS_SYMBOLS // SYMBOLS_PER_CYCLE
        return TRAINING[self.state][1], self.state


def link_up(a: Stack, b: Stack, skew_cycles: int = 0,
            channel_cycles: int = 0) -> dict[str, float]:
    """Link-up estimate for ``a`` and ``b`` back to back; ``b`` leaves reset
    ``skew_cycles`` after ``a``. Times are from each port's own reset
    release to its entry into L0 (link_up_o rising)."""
    ports = [_Port(a, 0), _Port(b, skew_cycles)]

    def one_way(src: _Port, dst: _Port) -> int:
        fill = math.ceil(dst.stack.pcs.eb_nominal_symbols / 2)
        return (src.stack.tx_latency_cycles() + channel_cycles
                + dst.stack.rx_latency_cycles(2 * fill, False))

    # Detect.Quiet (the only modelled wait that TIMEOUT_DIV scales) ends on
    # its timeout, or when the partner's first TS1
    # breaks electrical idle; then Detect.Active runs receiver detection.
    # A partner's termination is present once its pcs is out of reset; a
    # detection before that finds no receiver and returns to Detect.Quiet.
    awake = [p.reset_release + p.stack.pcs.reset_cycles for p in ports]
    quiet = [DETECT_QUIET_CYCLES // p.stack.timeout_div for p in ports]
    quiet_end = [awake[i] + quiet[i] for i in range(2)]
    for i in range(2):
        while quiet_end[i] + ports[i].stack.pcs.rx_detect_cycles < awake[1 - i]:
            quiet_end[i] += ports[i].stack.pcs.rx_detect_cycles + quiet[i]
    early, late = sorted(range(2), key=lambda i: quiet_end[i])
    ports[early].polling = quiet_end[early] + ports[early].stack.pcs.rx_detect_cycles
    seen = ports[early].polling + one_way(ports[early], ports[late])
    quiet_end[late] = min(quiet_end[late], max(seen, awake[late]))
    ports[late].polling = quiet_end[late] + ports[late].stack.pcs.rx_detect_cycles

    for p in ports:
        p.next_slot = p.polling
    flight: list[tuple[int, int, str, int]] = []   # (arrival, dst, kind, phase)
    t = min(p.polling for p in ports)
    limit = t + 4 * (POLLING_ACTIVE_MIN_TS1 + 1024) * SLOT_CYCLES
    while any(p.l0 is None for p in ports):
        if t > limit:
            raise RuntimeError("training did not reach L0")
        for arr in [f for f in flight if f[0] == t]:
            ports[arr[1]].receive(arr[2], arr[3])
        flight = [f for f in flight if f[0] != t]
        for i, p in enumerate(ports):
            if p.l0 is None and t == p.next_slot:
                sent = p.slot(t)
                if sent is not None:
                    dst = 1 - i
                    arrival = t + SLOT_CYCLES + one_way(p, ports[dst])
                    flight.append((arrival, dst, sent[0], sent[1]))
        t += 1
    up = [p.l0 - p.reset_release for p in ports]
    train = [p.l0 - p.polling for p in ports]
    detect = [p.polling - p.reset_release for p in ports]
    return {
        "link_up_cycles_max": max(up),
        "link_up_us_max": round(cycles_to_us(max(up)), 3),
        "link_up_us_min": round(cycles_to_us(min(up)), 3),
        "detect_us_max": round(cycles_to_us(max(detect)), 3),
        "training_us_max": round(cycles_to_us(max(train)), 3),
        "training_us_min": round(cycles_to_us(min(train)), 3),
    }


# --------------------------------------------------------------------- perf

_KINDS = {
    "traffic": {"kind", "packets", "mix", "tlp_bytes", "nullify_ratio", "ppm",
                "skp_interval"},
    "link_up": {"kind", "timeout_div", "skew_cycles", "channel_cycles"},
}


def _int(w: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = w.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"workload.{key}: expected an integer in [{lo}, {hi}]")
    return v


def _num(w: dict, key: str, default: float, lo: float, hi: float) -> float:
    v = w.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ValueError(f"workload.{key}: expected a number in [{lo}, {hi}]")
    return float(v)


def perf(workload: dict, seed: int) -> dict[str, float]:
    """Measure one workload against the HAS targets.

    ``kind: traffic``: ``packets`` packets back to back (``mix`` ``tlp``,
    all ``tlp_bytes`` long; ``dllp``; or ``random`` TLP sizes with 30%
    DLLPs), a ``nullify_ratio`` of TLPs ended with EDB, a SKP ordered set
    every ``skp_interval`` symbols, received with the RX clock ``ppm`` off.

    ``kind: link_up``: two default stacks back to back with ``timeout_div``,
    the second leaving reset ``skew_cycles`` later (drawn from [0, value]
    with the seed), and ``channel_cycles`` of channel delay.
    """
    kind = workload.get("kind")
    if kind not in _KINDS:
        raise ValueError(f"workload.kind: expected one of {sorted(_KINDS)}")
    unknown = set(workload) - _KINDS[kind]
    if unknown:
        raise ValueError(f"unknown workload key(s) {sorted(unknown)}; "
                         f"known: {sorted(_KINDS[kind])}")
    rng = random.Random(seed)
    if kind == "traffic":
        count = _int(workload, "packets", 0, 1, 1_000_000)
        mix = workload.get("mix", "tlp")
        if mix not in ("tlp", "dllp", "random"):
            raise ValueError("workload.mix: expected tlp, dllp or random")
        tlp_bytes = _int(workload, "tlp_bytes", 274, 2, 4122)
        if tlp_bytes % 2:
            raise ValueError("workload.tlp_bytes: expected an even number")
        nullify = _num(workload, "nullify_ratio", 0.0, 0.0, 1.0)
        ppm = _num(workload, "ppm", 0.0, -PPM_MAX, PPM_MAX)
        interval = _int(workload, "skp_interval", SKP_INTERVAL_MIN,
                        SKP_INTERVAL_MIN, SKP_INTERVAL_MAX)
        stack = Stack(mac=MacBudget(skp_interval_symbols=interval))
        return traffic(stack, make_packets(rng, count, mix, tlp_bytes, nullify), ppm)
    div = _int(workload, "timeout_div", 1, 1, TIMEOUT_DIV_MAX)
    skew = rng.randint(0, _int(workload, "skew_cycles", 0, 0, 10_000_000))
    channel = _int(workload, "channel_cycles", 0, 0, 1000)
    return link_up(Stack(timeout_div=div), Stack(timeout_div=div), skew, channel)
