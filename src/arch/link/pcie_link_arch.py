"""Architecture model of pcie_link, the dll-over-phy stack
(wiki/src/arch/link/HAS.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). The data link rules are the dll architecture model's,
used unchanged: ``Dll`` from ``src/arch/dll/pcie_dll_arch.py``, loaded
from its folder so the two never diverge. This file adds what the stack
adds below it, the Gen1 x1 line of two ``phy`` stacks:

- the line rate, 2 bytes (symbols) per ``clk_i`` cycle each way;
- framing, one cycle (STP or SDP, and END) per packet, already counted by
  the dll model's transmit busy time;
- SKP ordered sets, 4 symbols (2 cycles) each, falling due at a fixed
  interval and sent between packets, as the phy HAS schedules them;
- the stack latency, 22 cycles from a packet's last beat on one ``dll``'s
  transmit stream to its ``eop`` at the other (the phy TX and RX budgets);
- the dll model's ``Channel`` impairments: dropped, corrupted and
  phy-flagged packets, and the retrain hold.

``perf`` measures the link HAS targets: the line fractions taken by TLPs,
DLLPs and SKP ordered sets, the TLP payload rate as a fraction of the line
rate, and the end-to-end no-loss, no-duplicate and no-reorder counts.
Python standard library only (plus the sibling dll model).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

# The dll model (and its CRC helper) by bare name from src/arch/dll: arch
# may use another IP's arch model; it never imports DV, RTL or tools.
_DLL_DIR = str(Path(__file__).resolve().parent.parent / "dll")
if _DLL_DIR not in sys.path:
    sys.path.append(_DLL_DIR)

import pcie_dll_arch as dll  # noqa: E402

# ---------------------------------------------------------------- constants

CLK_MHZ = 125                 # clk_i, the design rate (link HAS, Clocks)
LINE_BYTES_PER_CYCLE = 2      # 2.5 GT/s, 8b/10b: 250 MB/s, 2 symbols per cycle
SKP_SYMBOLS = 4               # COM and three SKP [cite: PCIe Base Spec, rev 3.0, §4.2.7]
SKP_INTERVAL_MIN = 1180       # symbol times [cite: PCIe Base Spec, rev 3.0, §4.2.7]
SKP_INTERVAL_MAX = 1538
STACK_LATENCY = 22            # cycles: phy TX 6 plus phy RX 16 (phy HAS, Targets)
FRAMING_SYMBOLS = 2           # STP or SDP, and END [cite: PCIe Base Spec, rev 3.0, §4.2.1.2]
DLLP_LINE_SYMBOLS = dll.DLLP_BYTES + FRAMING_SYMBOLS


def tlp_line_symbols(tlp_bytes: int) -> int:
    """Symbols one TLP of ``tlp_bytes`` (header and payload) takes on the
    line: framing, sequence number, the TLP and its LCRC."""
    return FRAMING_SYMBOLS + dll.SEQ_BYTES + tlp_bytes + dll.LCRC_BYTES


def derived_fractions(tlp_bytes: int, payload: int, bidirectional: bool,
                      skp_interval: int = SKP_INTERVAL_MIN) -> dict[str, float]:
    """The HAS throughput derivation for back-to-back TLPs of one size and
    class (posted), as closed-form line fractions per direction.

    One way, the forward line carries the TLPs, the SKP ordered sets and
    the sender's periodic UpdateFC refresh (three classes every 2048
    cycles); the Acks and UpdateFC-P that return credits use the reverse
    line. Both ways, each line also carries one Ack and one UpdateFC-P per
    TLP received from the other direction (the Ack timer, 32 cycles, is
    shorter than one TLP time, so Acks do not coalesce), and the refresh
    of the two other classes only (UpdateFC-P never waits 2048 cycles).
    """
    t = tlp_line_symbols(tlp_bytes)
    skp = SKP_SYMBOLS / skp_interval
    period = 2 * dll.UPDATEFC_PERIOD                      # refresh period, symbol times
    if bidirectional:
        refresh = 2 * DLLP_LINE_SYMBOLS / period
        per_tlp = t + 2 * DLLP_LINE_SYMBOLS              # TLP, Ack, UpdateFC-P
    else:
        refresh = 3 * DLLP_LINE_SYMBOLS / period
        per_tlp = t
    room = 1.0 - skp - refresh
    tlp = room * t / per_tlp
    return {
        "tlp_line_fraction": tlp,
        "payload_line_fraction": tlp * payload / t,
        "dllp_line_fraction": room - tlp + refresh,
        "skp_line_fraction": skp,
    }


# ------------------------------------------------------------ line and pair

class Line(dll.Channel):
    """The dll model's channel (latency, framing cycle, impairments, retrain
    hold) with the phy's SKP schedule added per direction."""

    def __init__(self, rng: random.Random, skp_interval: int = SKP_INTERVAL_MIN, **kw):
        if not SKP_INTERVAL_MIN <= skp_interval <= SKP_INTERVAL_MAX:
            raise ValueError(f"skp_interval {skp_interval} outside "
                             f"{SKP_INTERVAL_MIN}..{SKP_INTERVAL_MAX}")
        super().__init__(rng, **kw)
        self.skp_cycles = skp_interval / LINE_BYTES_PER_CYCLE
        self.skp_next = [self.skp_cycles, self.skp_cycles]
        self.skp_pending = [0, 0]
        self.skp_left = [0, 0]


class LinkPair:
    """Two links back to back: ``a`` (way 0 sends a to b) and ``b``. Each
    cycle a ``Dll`` sends on its line only when no SKP ordered set holds
    the line; a SKP that falls due inside a packet goes out after it."""

    def __init__(self, a: dll.Dll, b: dll.Dll, line: Line):
        self.a, self.b, self.line = a, b, line
        self.now = 0
        self.busy = {k: [0, 0] for k in ("tlp", "dllp", "skp")}
        self.trace: list | None = None     # per cycle, way 0: ("skp" | packet | None)

    def step(self, sinks=(True, True), link=(True, True)) -> None:
        now, ln = self.now, self.line
        for i, d in enumerate((self.a, self.b)):
            if link[i] != d.link_up:
                d.set_link_up(link[i], now)
        for way, d in ((0, self.b), (1, self.a)):
            for pkt in ln.arrivals(way, now):
                d.receive(pkt, now)
        rec = ln.recovery(now)
        for way, d in ((0, self.a), (1, self.b)):
            while now >= ln.skp_next[way]:
                ln.skp_pending[way] += 1
                ln.skp_next[way] += ln.skp_cycles
            # A packet ends this cycle (or none is in progress): a due SKP
            # takes the line before the dll may start the next packet.
            if not ln.skp_left[way] and ln.skp_pending[way] and d.tx_busy <= 1:
                ln.skp_pending[way] -= 1
                ln.skp_left[way] = SKP_SYMBOLS // LINE_BYTES_PER_CYCLE
            pkt = d.tick(now, not rec, not rec and not ln.skp_left[way], sinks[way])
            if pkt is not None:
                ln.send(way, pkt, now)
            if d.retrain_req:
                ln.retrain(now)
            owner = None
            if ln.skp_left[way]:
                ln.skp_left[way] -= 1
                self.busy["skp"][way] += 1
                owner = "skp"
            elif d.tx_busy and d.tx_cur is not None:
                self.busy["dllp" if d.tx_cur.dllp else "tlp"][way] += 1
                owner = d.tx_cur
            if way == 0 and self.trace is not None:
                self.trace.append(owner)
        self.now += 1


# ---------------------------------------------------------------- perf

def _int(w: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = w.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise ValueError(f"workload {key}={v!r} not an integer in {lo}..{hi}")
    return v


def _payload(tlp: bytes) -> int:
    return dll.tlp_info(tlp)[3] - (16 if tlp[0] & 0x20 else 12)


def run_traffic(w: dict, seed: int) -> dict[str, float]:
    rng = random.Random(seed)
    count = _int(w, "tlps", 500, 1, 100000)
    payload = _int(w, "payload", dll.MAX_PAYLOAD, -1, dll.MAX_PAYLOAD)
    mix = w.get("mix", "mwr")
    both = bool(w.get("bidirectional", False))
    fc = w.get("fc", {})
    a, b = dll.Dll("a", fc), dll.Dll("b", fc)
    line = Line(rng, skp_interval=_int(w, "skp_interval", SKP_INTERVAL_MIN,
                                       SKP_INTERVAL_MIN, SKP_INTERVAL_MAX),
                latency=_int(w, "latency", STACK_LATENCY, 0, 1000),
                retrain_cycles=_int(w, "retrain_cycles", 500, 1, 100000),
                errors=w.get("errors", {}))
    pair = LinkPair(a, b, line)
    sent = {0: dll.make_traffic(rng, count, mix, payload), 1: []}
    if both:
        sent[1] = dll.make_traffic(rng, count, mix, payload)
    idx, first, last = [0, 0], None, 0
    limit = _int(w, "max_cycles", 2_000_000, 1000, 50_000_000)
    while pair.now < limit:
        now = pair.now
        for i, d in enumerate((a, b)):
            if idx[i] < len(sent[i]) and d.offer(sent[i][idx[i]], now):
                if first is None:
                    first = now
                    for k in pair.busy:              # measure from the first TLP
                        pair.busy[k] = [0, 0]
                idx[i] += 1
        pair.step()
        if len(b.delivered) >= len(sent[0]) and len(a.delivered) >= len(sent[1]):
            last = pair.now
            break
    else:
        last = pair.now
    span = max(1, last - (first or 0))
    ways = (0, 1) if both else (0,)
    got = {0: b.delivered, 1: a.delivered}
    m = [dll._score(sent[i], got[i]) for i in (0, 1)]
    pay = [sum(_payload(t) for t in got[i]) for i in (0, 1)]
    met = {
        "tlps_lost": m[0][0] + m[1][0],
        "tlps_duplicated": m[0][1] + m[1][1],
        "tlps_out_of_order": m[0][2] + m[1][2],
        "tlps_delivered": float(len(a.delivered) + len(b.delivered)),
        "tlp_line_fraction": min(pair.busy["tlp"][i] / span for i in ways),
        "dllp_line_fraction": max(pair.busy["dllp"][i] / span for i in ways),
        "skp_line_fraction": max(pair.busy["skp"][i] / span for i in ways),
        "payload_line_fraction": min(pay[i] / (LINE_BYTES_PER_CYCLE * span) for i in ways),
        "payload_mbytes_per_s": min(pay[i] / span for i in ways) * CLK_MHZ,
        "acks_per_tlp": (a.counts["acks"] + b.counts["acks"]) / max(1, len(a.delivered)
                                                                    + len(b.delivered)),
        "updatefc_per_tlp": (a.counts["updatefc"] + b.counts["updatefc"])
        / max(1, len(a.delivered) + len(b.delivered)),
        "cycles": float(span),
        # At the dll-to-phy boundary (the dll and link HAS Ack target): from
        # a TLP's eop into the dll to the start of its Ack or Nak on the
        # phy stream, SKP waits included. The line bound adds the phy RX
        # and TX latencies (link HAS, Targets).
        "ack_latency_dll_cycles_max": float(max(a.ack_lat + b.ack_lat, default=0)),
        "replays": float(a.counts["replays"] + b.counts["replays"]),
        "retrains": float(a.counts["retrains"] + b.counts["retrains"]),
    }
    for i, n in enumerate(dll.ERR_NAMES):
        met["err_" + n] = float(a.errors[i] + b.errors[i])
    return met


def run_init(w: dict, seed: int) -> dict[str, float]:
    """Both ends from link-up to DL_Active over the line, with skew."""
    rng = random.Random(seed)
    trials = _int(w, "trials", 20, 1, 10000)
    skew = _int(w, "skew", 0, 0, 100000)
    worst = 0
    for _ in range(trials):
        a, b = dll.Dll("a", w.get("fc", {})), dll.Dll("b", w.get("fc", {}))
        line = Line(rng, latency=_int(w, "latency", STACK_LATENCY, 0, 1000),
                    errors=w.get("errors", {}))
        pair = LinkPair(a, b, line)
        up_b = rng.randint(0, skew)
        while pair.now < 100000 and (a.dl_up_at is None or b.dl_up_at is None):
            pair.step(link=(True, pair.now >= up_b))
        done = [d.dl_up_at for d in (a, b)]
        worst = max(worst, (max(done) - up_b) if None not in done else 10**9)
    return {"dl_active_cycles_max": float(worst)}


def perf(workload: dict, seed: int) -> dict[str, float]:
    kind = workload.get("kind")
    if kind == "traffic":
        return run_traffic(workload, seed)
    if kind == "init":
        return run_init(workload, seed)
    raise ValueError(f"workload kind {kind!r}")
