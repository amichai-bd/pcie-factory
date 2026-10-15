"""Architecture model of pcie_pcs, the 8b/10b physical coding sublayer
(wiki/src/arch/pcs/HAS.md).

For definition and performance analysis, not verification
(wiki/bkm/arch.md). It states the HAS behavior and measures it against the
HAS targets:

- the 8b/10b code, computed from the HAS rules (pcie_pcs_8b10b.py), and the
  transmit encoder with running disparity and TxCompliance;
- a channel model: RX clock offset in ppm, bit slips, single-bit symbol
  errors and electrical-idle gaps;
- the receive path as a two-clock simulation: comma alignment and symbol
  lock in the ser_rx_clk_i domain, the write side of the elastic buffer
  (SKP removal), a Gray-pointer asynchronous FIFO whose pointers cross
  through two-flop synchronizers, and the clk_i read side (SKP addition,
  polarity, decode, RxStatus, the error counter that requests a relock);
- the PIPE control timing (PhyStatus after reset, receiver detection,
  PowerDown acknowledgement);
- ``pcs_budget()``, the PcsBudget the phy model takes.

Python standard library only.
"""

from __future__ import annotations

import random
from array import array

import pcie_pcs_8b10b as code8b10b
from pcie_pcs_8b10b import (K_COM, K_END, K_FTS, K_IDL, K_PAD, K_SKP, K_STP, decode,
                            encode, has_comma, raw_is)

# ---------------------------------------------------------------- constants

CORE_NS = 8.0                 # clk_i, 125 MHz (HAS, Clocks and resets)
SYMBOLS_PER_WORD = 2
PPM_MAX = 600                 # two references at +-300 ppm each [cite: PCIe Base Spec, rev 3.0, §4.2.7]
SKP_INTERVAL_MIN = 1180       # symbol times between SKP ordered sets [cite: PCIe Base Spec, rev 3.0, §4.2.7]
SKP_INTERVAL_MAX = 1538
TLP_BYTES_MAX = 4122          # sequence number, 4DW header, 4096 bytes, ECRC, LCRC

# Elastic buffer (HAS, Elastic buffer).
EB_ADDR_W = 4                 # 16 words of two symbols
EB_WORDS = 1 << EB_ADDR_W
EB_NOMINAL = 8                # visible fill, symbols
EB_WINDOW = 16                # visible-fill window the budget states
EB_HIGH = EB_NOMINAL + 1      # fill-high level at or above this
EB_LOW = EB_NOMINAL - 1       # add a SKP at or below this
EB_HOLDOFF_WORDS = 8          # unlocked recentring: one word per 8 cycles

# Symbol lock (HAS, Comma alignment and symbol lock).
ERR_LIMIT = 3                 # errored words that request a relock (one bit error marks at most 2)
ERR_DECAY_WORDS = 8           # clean words that forgive one

# PIPE control timing (HAS, PIPE control), clk_i cycles.
PHY_STATUS_RESET_CYCLES = 16
RX_DETECT_CYCLES = 64
POWER_ACK_CYCLES = 4

# Pipeline (HAS, Targets), clk_i cycles.
TX_LATENCY = 2
RX_FIXED = 6

IN_FLIGHT_SYMBOLS = 2 * EB_WORDS + 2 * RX_FIXED   # buffer plus pipeline at the end of a run
SYNC_META_NS = 0.4            # a source change this close before an edge may
                              # resolve one edge late in a two-flop synchronizer


# ------------------------------------------------------------------ budget

class PcsBudget:
    """The pcs side of the phy model's budget (same attributes as
    pcie_phy_arch.PcsBudget). Latencies in clk_i cycles."""

    def __init__(self) -> None:
        self.tx_latency_cycles = TX_LATENCY
        self.rx_fixed_cycles = RX_FIXED
        self.eb_depth_symbols = EB_WINDOW
        self.eb_nominal_symbols = EB_NOMINAL
        self.reset_cycles = PHY_STATUS_RESET_CYCLES
        self.rx_detect_cycles = RX_DETECT_CYCLES


def pcs_budget() -> PcsBudget:
    return PcsBudget()


# --------------------------------------------------------------- transmit

def tx_encode(symbols: list[tuple[int, int]], compliance: set[int] | None = None,
              rd: int = -1) -> tuple[list[int], int]:
    """Encode (k, byte) symbols with running disparity, starting at ``rd``.
    ``compliance`` holds the indices of symbols sent with the running
    disparity forced negative first (TxCompliance on a word's low symbol).
    A K flag on a byte that is no valid K code sends EDB."""
    codes = []
    for i, (k, b) in enumerate(symbols):
        if compliance and i in compliance:
            rd = -1
        if k and b not in code8b10b.VALID_K:
            b = code8b10b.K_EDB      # an unsupported K byte goes out as EDB
        c, rd = encode(b, bool(k), rd)
        codes.append(c)
    return codes, rd


def line_stats(codes: list[int]) -> dict[str, int]:
    """Longest run of equal bits and the largest running digital sum on the
    line (ones minus zeros, from the start)."""
    run = best = 0
    last = None
    rds = rds_max = 0
    for c in codes:
        for i in range(10):
            bit = (c >> i) & 1
            run = run + 1 if bit == last else 1
            last = bit
            best = max(best, run)
            rds += 1 if bit else -1
            rds_max = max(rds_max, abs(rds))
    return {"run_length_max": best, "running_sum_max": rds_max}


# ------------------------------------------------------------------ traffic

def ordered_set(first: int, rest: int, n: int) -> list[tuple[int, int]]:
    return [(1, first)] + [(1, rest) if rest in (K_SKP, K_FTS) else (0, rest)] * (n - 1)


def make_stream(rng: random.Random, packets: int, mix: str, tlp_bytes: int,
                skp_interval: int, idle_gaps: int, idle_symbols: int,
                lead_ts: int = 32,
                eios: bool = False) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """The symbol stream a partner sends: ``lead_ts`` training ordered sets
    (COM, two PAD, N_FTS, rate, control, ten TS1 identifiers D10.2), then
    back-to-back packets (STP, random data standing in for scrambled bytes,
    END), with a SKP ordered set (COM and three SKP) every ``skp_interval``
    symbols throughout, deferred to the end of an ordered set or packet. ``idle_gaps`` electrical-idle gaps of ``idle_symbols`` are spread
    between packets; each is followed by 32 FTS ordered sets (COM and three
    FTS) to regain lock. With ``eios`` each gap follows an electrical idle
    ordered set (COM and three IDL) and starts at a transmitted word
    boundary (an even symbol index), as a PIPE transmitter enters idle; one
    more IDL pads an odd stream. Returns the symbols and the idle ranges."""
    out: list[tuple[int, int]] = []
    idles: list[tuple[int, int]] = []
    credit = 0
    every = max(1, packets // (idle_gaps + 1)) if idle_gaps else 0

    def boundary() -> None:
        nonlocal credit
        while credit >= skp_interval:
            credit -= skp_interval
            out.extend([(1, K_COM), (1, K_SKP), (1, K_SKP), (1, K_SKP)])
            credit += 4

    ts1 = [(1, K_COM), (1, K_PAD), (1, K_PAD), (0, 0x80), (0, 0x02), (0, 0x00)] + [(0, 0x4A)] * 10
    for _ in range(lead_ts):
        boundary()
        out.extend(ts1)
        credit += len(ts1)

    for p in range(packets):
        boundary()
        if every and p and p % every == 0 and len(idles) < idle_gaps:
            if eios:
                out.extend([(1, K_COM), (1, K_IDL), (1, K_IDL), (1, K_IDL)])
                if len(out) % 2:
                    out.append((1, K_IDL))
            start = len(out)
            out.extend([(0, 0)] * idle_symbols)
            idles.append((start, len(out)))
            for _ in range(32):
                out.extend([(1, K_COM), (1, K_FTS), (1, K_FTS), (1, K_FTS)])
            credit = 0
        if mix == "random":
            n = 2 + rng.choice((12, 16)) + 4 * rng.randrange(0, 65) + 4
        elif mix == "dllp":
            n = 6
        else:
            n = tlp_bytes
        out.append((1, K_STP))
        out.extend((0, rng.randrange(256)) for _ in range(n))
        out.append((1, K_END))
        credit += n + 2
    boundary()
    out.extend([(1, K_COM), (1, K_SKP), (1, K_SKP), (1, K_SKP)])
    return out, idles


# ------------------------------------------------------------------ channel

class Line:
    """The received bit stream: bits, the transmitted bit index of each (or
    -1 for noise), the electrical-idle flag per bit, and event positions."""

    def __init__(self) -> None:
        self.bits = bytearray()
        self.txpos = array("i")
        self.idle = bytearray()
        self.slips: list[int] = []     # received bit index of each slip
        self.errors: list[int] = []    # received bit index of each flipped bit


def channel(rng: random.Random, codes: list[int], idles: list[tuple[int, int]],
            slips: int, errors: int, polarity: bool = False) -> Line:
    """Serialize ``codes`` (bit 0 first), then apply the impairments: inside
    the idle symbol ranges the bits are noise with the idle flag set;
    ``slips`` bit slips (one bit dropped or repeated, alternately) and
    ``errors`` single-bit flips, each at a random place outside idle and
    the first 2000 symbols, at least 600 symbols apart; ``polarity``
    inverts every bit."""
    line = Line()
    idle_sym = bytearray(len(codes))
    for a, b in idles:
        for i in range(a, b):
            idle_sym[i] = 1
    n_sym = len(codes)
    places: list[int] = []
    tries = 0
    while len(places) < slips + errors and tries < 100000:
        tries += 1
        s = rng.randrange(2000, max(2001, n_sym - 200))
        if idle_sym[s] or any(abs(s - p) < 600 for p in places):
            continue
        if any(a - 200 <= s < b + 200 for a, b in idles):
            continue
        places.append(s)
    places.sort()
    kinds = ["slip"] * slips + ["error"] * errors
    rng.shuffle(kinds)
    events = dict(zip(places, kinds))
    slip_dir = 0
    for s, c in enumerate(codes):
        ev = events.get(s)
        flip = rng.randrange(10) if ev == "error" else -1
        for i in range(10):
            if idle_sym[s]:
                line.bits.append(rng.randrange(2))
                line.txpos.append(-1)
                line.idle.append(1)
                continue
            bit = ((c >> i) & 1) ^ int(polarity)
            if i == flip:
                line.errors.append(len(line.bits))
                bit ^= 1
            if ev == "slip" and i == 5:
                line.slips.append(len(line.bits))
                slip_dir ^= 1
                if slip_dir:          # drop this bit
                    continue
                line.bits.append(bit)  # repeat it
                line.txpos.append(-1)
                line.idle.append(0)
            line.bits.append(bit)
            line.txpos.append(10 * s + i)
            line.idle.append(0)
    return line


# --------------------------------------------------------------- receive

def _gray(n: int) -> int:
    return n ^ (n >> 1)


def _ungray(g: int) -> int:
    n = 0
    while g:
        n ^= g
        g >>= 1
    return n


class _Sync:
    """A two-flop synchronizer into one domain: ``meta`` and ``out``. A
    source that changed within SYNC_META_NS before the edge may be taken
    one edge late."""

    def __init__(self, rng: random.Random, value: int = 0) -> None:
        self.rng = rng
        self.meta = self.out = value

    def edge(self, src: int, src_prev: int, src_changed: float, t: float) -> None:
        self.out = self.meta
        late = t - src_changed < SYNC_META_NS and self.rng.random() < 0.5
        self.meta = src_prev if late else src


class _Reg:
    """A source flop seen by a synchronizer: value, previous value and the
    time of the last change."""

    def __init__(self, value: int = 0) -> None:
        self.value = self.prev = value
        self.changed = -1e9

    def set(self, value: int, t: float) -> None:
        if value != self.value:
            self.prev, self.value, self.changed = self.value, value, t


class _Sym:
    """One symbol in the receive path."""

    __slots__ = ("code", "lock", "gen", "t_in", "txpos", "removed_before", "ovf", "idle")

    def __init__(self, code: int, lock: int, gen: int, t_in: float, txpos: int,
                 idle: int = 0) -> None:
        self.code, self.lock, self.gen, self.t_in, self.txpos = code, lock, gen, t_in, txpos
        self.idle = idle             # starts in a word received in electrical idle
        self.removed_before = False
        self.ovf = False


def receive(line: Line, sent: list[tuple[int, int]], ppm: float, seed: int,
            polarity: bool = False) -> dict[str, float]:
    """Run the receive path over ``line`` with ser_rx_clk_i ``ppm`` off
    clk_i (positive: RX clock fast) and measure it. ``sent`` is the
    transmitted symbol list (to check delivered symbols); ``polarity`` sets
    RxPolarity from the start."""
    rng = random.Random(seed)
    t_rx = CORE_NS / (1.0 + ppm * 1e-6)
    words = len(line.bits) // 20

    # RX domain state.
    in_q = in_prev_q = 0
    in_txpos: list[int] = [-1] * 20
    prev_txpos: list[int] = [-1] * 20
    in_t = prev_t = 0.0
    idle_q = idle_prev_q = 1
    lock = 0
    gen = 0                      # lock generation: flips at every lock acquisition
    offset = 0
    aligned: list[_Sym] = []
    hold: list[_Sym] = []
    prev_com = False             # the last symbol taken in was a COM
    pending_removed = False
    pending_ovf = False
    unlock_holdoff = 0
    wbin = 0
    wgray = _Reg()
    rsync_rptr = _Sync(rng)
    rsync_full = _Sync(rng)
    rsync_relock = _Sync(rng)
    relock_seen = 0
    mem: list[list[_Sym]] = [[] for _ in range(EB_WORDS)]

    # clk_i state.
    rbin = 0
    rgray = _Reg()
    csync_wptr = _Sync(rng)
    too_full = _Reg(0)
    relock_toggle = _Reg(0)
    stage: list[tuple[_Sym, int, int, bool, bool]] = []
    rd: int | None = None
    started = False
    err_count = 0
    clean = 0
    relock_wait = False
    wait_gen = 0
    last_gen = -1
    read_holdoff = 0

    st = {"overflow": 0, "underflow": 0, "skp_added": 0, "skp_removed": 0,
          "fill_min": 10**9, "fill_max": -1, "lat_max": 0.0, "lat_min": 1e9,
          "delivered": 0, "wrong": 0, "relocks": 0,
          "code_errors": 0, "disp_errors": 0, "words_out": 0, "locks": 0,
          "write_view_max": 0, "skp_status_off_com": 0, "idle_tails": 0,
          "valid_in_idle": 0}
    status_counts = [0] * 8
    lock_events: list[float] = []        # RX lock acquired (time)
    first_valid_after: list[float] = []  # PIPE RxValid rising (time)
    relock_req_times: list[float] = []
    gen_starts: list[float] = []         # first PIPE word of each lock generation
    delivered_txpos: list[int] = []
    last_valid = 0

    n = 0
    t_r = rng.random() * t_rx
    t_c = 0.0
    while n < words:
        if t_r <= t_c:
            t = t_r
            # ---- ser_rx_clk_i edge: registered values first.
            rptr_seen = _ungray(rsync_rptr.out)
            full_level = rsync_full.out
            relock_level = rsync_relock.out
            # Write side: push the aligned word registered last edge.
            syms = aligned
            push_words: list[list[_Sym]] = []
            drop_word = False
            if syms and not syms[0].lock and full_level and unlock_holdoff == 0:
                drop_word = True
                unlock_holdoff = EB_HOLDOFF_WORDS
            if unlock_holdoff:
                unlock_holdoff -= 1
            if not drop_word:
                for s in syms:
                    is_skp = raw_is(s.code, K_SKP)
                    after_com, prev_com = prev_com, raw_is(s.code, K_COM)
                    if is_skp and after_com and s.lock and full_level:
                        # Remove the first SKP of the ordered set.
                        pending_removed = True
                        st["skp_removed"] += 1
                        continue
                    if pending_removed:
                        s.removed_before, pending_removed = True, False
                    hold.append(s)
                while len(hold) >= 2:
                    push_words.append(hold[:2])
                    hold = hold[2:]
            for w in push_words:
                if (wbin - rptr_seen) >= EB_WORDS:
                    st["overflow"] += 1
                    pending_ovf = True
                    continue
                if pending_ovf:
                    w[0].ovf, pending_ovf = True, False
                mem[wbin % EB_WORDS] = w
                wbin += 1
                st["write_view_max"] = max(st["write_view_max"], wbin - rptr_seen)
            wgray.set(_gray(wbin), t)
            # Aligner: register this edge's aligned word from the window.
            if relock_level != relock_seen:
                relock_seen = relock_level
                lock = 0
            # Lock drops for the word whose symbols start in an idle word:
            # the output word starts in in_prev_q (RECEIVE.md, Electrical idle).
            if idle_prev_q:
                lock = 0
            window = in_prev_q | (in_q << 20)
            if not lock and not idle_q and not idle_prev_q:
                for p in range(20):
                    if has_comma((window >> p) & 0x3FF):
                        lock, offset, gen = 1, p % 10, gen ^ 1
                        lock_events.append(t)
                        st["locks"] += 1
                        break
            tx_all = prev_txpos + in_txpos
            aligned = []
            for j in range(2):
                p = offset + 10 * j
                c = (window >> p) & 0x3FF
                tp = tx_all[p]
                if tp >= 0 and (tp % 10 or any(tx_all[p + i] != tp + i for i in range(10))):
                    tp = -1
                aligned.append(_Sym(c, lock, gen, in_t if p + 10 > 20 else prev_t,
                                    tp // 10 if tp >= 0 else -1, idle_prev_q))
            # Input register.
            in_prev_q, prev_txpos, prev_t, idle_prev_q = in_q, in_txpos, in_t, idle_q
            base = 20 * n
            in_q = _word(line.bits, base)
            in_txpos = list(line.txpos[base:base + 20])
            in_t = t
            idle_q = line.idle[base + 19]
            # Synchronizers into the RX domain.
            rsync_rptr.edge(rgray.value, rgray.prev, rgray.changed, t)
            rsync_full.edge(too_full.value, too_full.prev, too_full.changed, t)
            rsync_relock.edge(relock_toggle.value, relock_toggle.prev, relock_toggle.changed, t)
            n += 1
            t_r += t_rx
            continue

        # ---- clk_i edge.
        t = t_c
        visible = _ungray(csync_wptr.out) - rbin
        fill = 2 * visible + len(stage)
        if started:
            st["fill_min"] = min(st["fill_min"], fill)
            st["fill_max"] = max(st["fill_max"], fill)
        if not started and fill >= EB_NOMINAL:
            started = True
        out: list[tuple[_Sym, int, int, bool, bool]] = []
        status = 0
        if started:
            # Recentre while the next symbol to send is unlocked.
            nxt_sym = stage[0][0] if stage else (mem[rbin % EB_WORDS][0] if visible > 0 else None)
            head_unlocked = nxt_sym is not None and not nxt_sym.lock
            if read_holdoff:
                read_holdoff -= 1
            if head_unlocked and fill <= EB_LOW and read_holdoff == 0:
                read_holdoff = EB_HOLDOFF_WORDS     # repeat: no pop this cycle
            else:
                while len(out) < 2:
                    if not stage:
                        if visible <= 0:
                            st["underflow"] += 1
                            status = max_status(status, 6)
                            break
                        w = mem[rbin % EB_WORDS]
                        rbin += 1
                        visible -= 1
                        for s in w:
                            c = s.code ^ (0x3FF if polarity else 0)
                            b, k, rd_out, ce, de = decode(c, rd if s.lock else None)
                            rd = rd_out if s.lock else None
                            stage.append((s, b, int(k), ce, de))
                    item = stage.pop(0)
                    out.append(item)
                    s, b, k, ce, de = item
                    if (k, b) != (1, K_COM) or not s.lock:
                        continue
                    # A COM: look at the next symbol, in the leftover
                    # register or at the FIFO head, without reading it.
                    if stage:
                        nxt = stage[0][0]
                    elif visible > 0:
                        nxt = mem[rbin % EB_WORDS][0]
                    else:
                        continue
                    if nxt.removed_before:
                        nxt.removed_before = False
                        status = max_status(status, 2)
                    elif raw_is(nxt.code, K_SKP) and fill <= EB_LOW:
                        # Add a copy of that SKP right after the COM.
                        stage.insert(0, (nxt, K_SKP, 1, False, False))
                        st["skp_added"] += 1
                        status = max_status(status, 1)
        rgray.set(_gray(rbin), t)
        too_full.set(1 if fill >= EB_HIGH else 0, t)
        # PIPE outputs of this edge.
        if len(out) == 2:
            # An idle tail: the last locked symbol, then one that began in
            # electrical idle; RxValid 1, the second symbol EDB, no error.
            tail = bool(out[0][0].lock and not out[1][0].lock and out[1][0].idle)
            valid = int(out[0][0].lock and (out[1][0].lock or tail))
            if tail:
                st["idle_tails"] += 1
                out[1] = (out[1][0], 0xFE, 1, False, False)
            pipe_ei = out[0][0].idle
            if valid and pipe_ei:
                st["valid_in_idle"] += 1
            st["words_out"] += 1
            for s, b, k, ce, de in out:
                if s.removed_before:      # its COM had already left
                    s.removed_before = False
                    status = max_status(status, 2)
                if s.ovf:
                    status = max_status(status, 5)
                if valid and ce:
                    status = max_status(status, 4)
                    st["code_errors"] += 1
                if valid and de:
                    status = max_status(status, 7)
                    st["disp_errors"] += 1
            if status in (1, 2) and not any((k, b) == (1, K_COM) for _, b, k, _, _ in out):
                st["skp_status_off_com"] += 1
            if valid:
                status_counts[status] += 1
                if not last_valid:
                    first_valid_after.append(t)
                g = out[-1][0].gen
                if g != last_gen:
                    err_count = clean = 0        # a new locked run
                    last_gen = g
                    gen_starts.append(t)
                if relock_wait and g != wait_gen:
                    relock_wait = False          # the aligner has relocked
                errored = any(ce or de for _, _, _, ce, de in out)
                if relock_wait:
                    pass
                elif errored:
                    err_count += 1
                    clean = 0
                    if err_count >= ERR_LIMIT:
                        relock_toggle.set(relock_toggle.value ^ 1, t)
                        relock_req_times.append(t)
                        st["relocks"] += 1
                        err_count, relock_wait, wait_gen = 0, True, g
                else:
                    clean += 1
                    if clean >= ERR_DECAY_WORDS:
                        err_count, clean = max(0, err_count - 1), 0
                for s, b, k, ce, de in out:
                    if (k, b) == (1, K_SKP) or s.txpos < 0 or not s.lock:
                        continue
                    lat = (t - s.t_in) / CORE_NS
                    st["lat_max"] = max(st["lat_max"], lat)
                    st["lat_min"] = min(st["lat_min"], lat)
                    delivered_txpos.append(s.txpos)
                    if not ce and sent[s.txpos] != (k, b):
                        st["wrong"] += 1
            last_valid = valid
        else:
            last_valid = 0
        # Synchronizers into clk_i.
        csync_wptr.edge(wgray.value, wgray.prev, wgray.changed, t)
        t_c += CORE_NS

    st["status_counts"] = status_counts
    st["lock_events"] = lock_events
    st["valid_rise"] = first_valid_after
    st["gen_starts"] = gen_starts
    st["relock_req"] = relock_req_times
    st["delivered_txpos"] = delivered_txpos
    st["t_rx"] = t_rx
    return st


def _word(bits: bytearray, base: int) -> int:
    v = 0
    for i in range(19, -1, -1):
        v = (v << 1) | bits[base + i]
    return v


# RxStatus precedence (HAS, RxStatus): earlier wins.
STATUS_ORDER = (4, 5, 6, 7, 1, 2, 0)


def max_status(cur: int, new: int) -> int:
    return new if STATUS_ORDER.index(new) < STATUS_ORDER.index(cur) else cur


# ------------------------------------------------------------ PIPE control

class PipeControl:
    """Cycle model of the PIPE control of the HAS (PIPE control): reset and
    PhyStatus, receiver detection, PowerDown acknowledgement and the
    transmitter's electrical idle. ``step`` takes this cycle's inputs and
    returns the outputs registered at the end of the cycle."""

    def __init__(self) -> None:
        self.ready_count = PHY_STATUS_RESET_CYCLES
        self.power = 2            # P1 out of reset
        self.ack = 0
        self.detect = 0
        self.detect_done = False
        self.rxdet_seen = 0

    def step(self, rst: int, reset_n: int, power_down: int, detect_rx: int,
             tx_elec_idle: int, rxdet: int) -> dict[str, int]:
        out = {"phy_status": 0, "rx_status": 0, "tx_elec_idle": 1}
        if rst or not reset_n:
            self.__init__()
            out["phy_status"] = 1
            return out
        if self.ready_count:
            self.ready_count -= 1
            out["phy_status"] = 1
            self.power = power_down
            return out
        if power_down != self.power:
            self.power = power_down
            self.ack = POWER_ACK_CYCLES
        elif self.ack:
            self.ack -= 1
            if self.ack == 0:
                out["phy_status"] = 1
        if self.power == 2 and tx_elec_idle and detect_rx and not self.detect_done:
            self.detect += 1
            if self.detect == RX_DETECT_CYCLES - 1:
                self.rxdet_seen = rxdet
            if self.detect == RX_DETECT_CYCLES:
                out["phy_status"] = 1
                out["rx_status"] = 3 if self.rxdet_seen else 0
                self.detect_done = True
        elif not detect_rx:
            self.detect, self.detect_done = 0, False
        loopback = self.power == 0 and detect_rx and not tx_elec_idle
        out["tx_elec_idle"] = int(self.power != 0 or (tx_elec_idle and not loopback))
        return out


def pipe_script(rxdet: int) -> dict[str, int]:
    """A MAC-side script through reset, receiver detection in P1 and every
    PowerDown change; returns the measured handshake timings."""
    pc = PipeControl()
    t = 0
    res = {"pulses": 0, "changes": 0}

    def run(n, rst=0, reset_n=1, pd=2, det=0, idle=1):
        nonlocal t
        outs = []
        for _ in range(n):
            outs.append(pc.step(rst, reset_n, pd, det, idle, rxdet))
            t += 1
        return outs

    run(8, rst=1, reset_n=0)
    outs = run(40)
    res["phy_status_after_reset_cycles"] = next(i for i, o in enumerate(outs) if not o["phy_status"])
    outs = run(100, det=1)
    i = next(i for i, o in enumerate(outs) if o["phy_status"])
    res["rx_detect_cycles"] = i + 1
    res["rx_detect_status"] = outs[i]["rx_status"]
    res["pulses"] += sum(o["phy_status"] for o in outs)
    run(2)
    ack_max = 0
    for pd in (0, 1, 0, 2, 0, 3, 0):   # the legal transitions, from P1
        res["changes"] += 1
        outs = run(20, pd=pd, idle=int(pd != 0))
        hits = [i for i, o in enumerate(outs) if o["phy_status"]]
        res["pulses"] += len(hits)
        ack_max = max([ack_max] + hits)
    res["power_ack_cycles_max"] = ack_max
    return res


# ------------------------------------------------------------ code checks

def code_properties() -> dict[str, int]:
    """Exhaustive checks of the computed 8b/10b code (HAS, 8b/10b)."""
    import itertools
    syms = [(0, b) for b in range(256)] + [(1, b) for b in code8b10b.VALID_K]
    res = {"codes": len(code8b10b.DECODE), "roundtrip_errors": 0,
           "disparity_violations": 0, "run_length_max": 0,
           "misaligned_commas": 0, "aligned_comma_symbols": 0,
           "skp_com_not_complementary": 0, "skp_not_neutral": 0}
    for (k, b), rd in itertools.product(syms, (-1, 1)):
        c, rd2 = encode(b, k, rd)
        ones = bin(c).count("1")
        if ones not in (4, 5, 6) or (ones == 6 and rd > 0) or (ones == 4 and rd < 0):
            res["disparity_violations"] += 1
        db, dk, drd, ce, de = decode(c, rd)
        if (db, dk, drd, ce, de) != (b, bool(k), rd2, False, False):
            res["roundtrip_errors"] += 1
        if has_comma(c) and rd == -1:
            res["aligned_comma_symbols"] += 1
    for b in (K_SKP, K_COM):
        m, p = encode(b, True, -1)[0], encode(b, True, 1)[0]
        res["skp_com_not_complementary"] += int(m ^ 0x3FF != p)
    for rd in (-1, 1):
        res["skp_not_neutral"] += int(encode(K_SKP, True, rd)[1] != rd)
    # Run length and commas across every pair of symbols at either RD.
    k287 = {(1, 0xFC)}
    for rd in (-1, 1):
        for s1 in syms:
            c1, r1 = encode(s1[1], s1[0], rd)
            for s2 in syms:
                c2, _ = encode(s2[1], s2[0], r1)
                pair = c1 | (c2 << 10)
                res["run_length_max"] = max(res["run_length_max"], line_stats([c1, c2])["run_length_max"])
                if s1 in k287 or s2 in k287:
                    continue
                for p in range(1, 10):
                    if has_comma(pair >> p):
                        res["misaligned_commas"] += 1
    return res


# --------------------------------------------------------------------- perf

_KINDS = {
    "link": {"kind", "packets", "mix", "tlp_bytes", "skp_interval", "ppm",
             "slips", "errors", "idle_gaps", "idle_symbols", "polarity",
             "training_sets"},
    "pipe": {"kind", "rxdet"},
    "code": {"kind"},
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


def _after(times: list[float], t: float) -> float | None:
    for x in times:
        if x >= t:
            return x
    return None


def link(workload: dict, seed: int) -> dict[str, float]:
    """Partner transmitter, channel and this receive path, measured."""
    rng = random.Random(seed)
    packets = _int(workload, "packets", 100, 1, 100_000)
    mix = workload.get("mix", "tlp")
    if mix not in ("tlp", "dllp", "random"):
        raise ValueError("workload.mix: expected tlp, dllp or random")
    tlp_bytes = _int(workload, "tlp_bytes", 274, 2, TLP_BYTES_MAX)
    interval = _int(workload, "skp_interval", SKP_INTERVAL_MIN, SKP_INTERVAL_MIN, SKP_INTERVAL_MAX)
    ppm = _num(workload, "ppm", 0.0, -PPM_MAX, PPM_MAX)
    slips = _int(workload, "slips", 0, 0, 100)
    errors = _int(workload, "errors", 0, 0, 100)
    gaps = _int(workload, "idle_gaps", 0, 0, 100)
    idle_symbols = _int(workload, "idle_symbols", 0, 0, 1_000_000)
    polarity = workload.get("polarity", False)
    if not isinstance(polarity, bool):
        raise ValueError("workload.polarity: expected true or false")
    training = _int(workload, "training_sets", 32, 1, 100_000)
    syms, idles = make_stream(rng, packets, mix, tlp_bytes, interval, gaps, idle_symbols,
                              lead_ts=training)
    codes, _ = tx_encode(syms)
    tx = line_stats(codes)
    line = channel(rng, codes, idles, slips, errors, polarity)
    st = receive(line, syms, ppm, seed, polarity)
    t_rx = st["t_rx"]

    def bit_time(bit: int) -> float:
        return (bit // 20) * t_rx

    # Delivery: every transmitted symbol other than SKP, from the first one
    # delivered, comes out once and in order (impaired runs excluded).
    d = st["delivered_txpos"]
    expected = [i for i, (k, b) in enumerate(syms) if (k, b) != (1, K_SKP)]
    lost = 0
    if d:
        first = expected.index(d[0]) if d[0] in expected else 0
        exp = expected[first:first + len(d)]
        lost = sum(1 for a, b in zip(d, exp) if a != b)
        tail = len(expected) - first - len(d)
        lost += max(0, tail - IN_FLIGHT_SYMBOLS)   # the last symbols still in flight
    # Slips: time to the relock request, before the next slip, and to the
    # first word of the new lock on PIPE.
    # A slip while the aligner is already hunting (after a relock request,
    # before the lock that answers it) needs no detection and is skipped.
    detect, recover = [], []
    slip_times = [bit_time(b) for b in line.slips]
    in_lock = 0
    for ts, nxt in zip(slip_times, slip_times[1:] + [1e30]):
        last_req = max([x for x in st["relock_req"] if x <= ts], default=None)
        if last_req is not None and _after(st["lock_events"], last_req) is None:
            continue
        if last_req is not None and _after(st["lock_events"], last_req) > ts:
            continue
        in_lock += 1
        r = _after(st["relock_req"], ts)
        if r is None or r >= nxt:
            continue
        detect.append((r - ts) / CORE_NS)
        v = _after(st["gen_starts"], r)
        if v is not None:
            recover.append((v - ts) / CORE_NS)
    # Lock: from each lock acquisition to RxValid on PIPE.
    lock_lat = []
    for tl in st["lock_events"]:
        v = _after(st["gen_starts"], tl)
        if v is not None:
            lock_lat.append((v - tl) / CORE_NS)
    # Errors: every flipped bit is reported, as a code or disparity error.
    reports = st["code_errors"] + st["disp_errors"]
    metrics = {
        "symbols": len(syms),
        "tx_run_length_max": tx["run_length_max"],
        "tx_running_sum_max": tx["running_sum_max"],
        "eb_fill_symbols_min": st["fill_min"],
        "eb_fill_symbols_max": st["fill_max"],
        "eb_write_view_words_max": st["write_view_max"],
        "eb_overflows": st["overflow"],
        "eb_underflows": st["underflow"],
        "skp_added": st["skp_added"],
        "skp_removed": st["skp_removed"],
        "skp_status_words": st["status_counts"][1] + st["status_counts"][2],
        "skp_status_off_com": st["skp_status_off_com"],
        "rx_latency_cycles_min": round(st["lat_min"], 3),
        "rx_latency_cycles_max": round(st["lat_max"], 3),
        "symbols_wrong": st["wrong"],
        "relocks": st["relocks"],
        "locks": st["locks"],
        "lock_to_rx_valid_cycles_max": round(max(lock_lat), 3) if lock_lat else 0,
        "slips_injected": len(line.slips),
        "slips_detected": len(detect),
        "slips_missed": in_lock - len(detect),
        "slip_detect_cycles_max": round(max(detect), 3) if detect else 0,
        "slip_recover_cycles_max": round(max(recover), 3) if recover else 0,
        "errors_injected": len(line.errors),
        "error_reports": reports,
    }
    if not slips and not errors and not gaps:
        metrics["symbols_lost"] = lost
    if errors and not slips:
        metrics["false_relocks"] = st["relocks"]
    return metrics


def perf(workload: dict, seed: int) -> dict[str, float]:
    """Measure one workload against the HAS targets.

    ``kind: link``: a partner sends ``training_sets`` training ordered sets
    (default 32; a COM every 16 symbols, so comma-dense) then ``packets``
    packets (``mix`` ``tlp`` of ``tlp_bytes``, ``dllp`` or ``random``) with
    a SKP ordered set every ``skp_interval`` symbols; the channel applies
    ``ppm``, ``slips`` bit slips, ``errors`` single-bit flips, ``idle_gaps``
    electrical-idle gaps of ``idle_symbols`` and optional ``polarity``
    inversion; the receive path runs at the offset clock.

    ``kind: pipe``: the PIPE control script, with a far-end receiver present
    (``rxdet`` 1) or absent (0).

    ``kind: code``: exhaustive properties of the computed 8b/10b code.
    """
    kind = workload.get("kind")
    if kind not in _KINDS:
        raise ValueError(f"workload.kind: expected one of {sorted(_KINDS)}")
    unknown = set(workload) - _KINDS[kind]
    if unknown:
        raise ValueError(f"unknown workload key(s) {sorted(unknown)}; "
                         f"known: {sorted(_KINDS[kind])}")
    if kind == "link":
        return link(workload, seed)
    if kind == "pipe":
        return pipe_script(_int(workload, "rxdet", 1, 0, 1))
    return code_properties()
