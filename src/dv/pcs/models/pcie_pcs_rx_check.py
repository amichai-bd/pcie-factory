"""Receive-path scoreboard of the pcs top: the words seen on PIPE against
the symbols the partner sent (wiki/src/arch/pcs/RECEIVE.md). Pure Python,
so its unit tests run without a simulator.

A ``Word`` is one clk_i cycle of the PIPE receive outputs. A **run** is a
maximal stretch of cycles with RxValid 1; each run must be a contiguous
piece of the sent stream once SKP symbols are set aside (SKP is added and
removed, every other symbol arrives once and in order). Inside a run, a word
with RxStatus 4 or 7 marks an error: the symbols up to it may differ from
the sent ones (a flipped bit can decode to another valid byte and only show
as an error at a later symbol, 8B10B.md), and the run re-synchronizes on the
next clean stretch at the same place in the stream, since a bit error
neither adds nor drops a symbol. A run that loses the stream after an error
is accepted only when a bit slip may explain it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

K_COM, K_SKP = 0xBC, 0x1C
STATUS_SKP = {4: 1, 3: 0, 2: 2}   # SKPs left in a 3-SKP set -> RxStatus on its COM
ERROR_CODES = (4, 7)
TRIM_MAX = 16                     # symbols before a reported error that may differ
RESYNC_SLACK = 8                  # symbols the stream may be off after an error
LATENCY_MAX_PS = 24 * 8000        # anchor: a word at most this long after its arrival


@dataclass
class Word:
    t: int                        # rising clk_i edge that produced it, ps
    valid: int
    syms: tuple[tuple[int, int], tuple[int, int]]   # (k, byte), [7:0] first
    status: int


@dataclass
class Run:
    words: list[Word]
    first: int = -1               # sent index of its first non-SKP symbol
    last: int = -1                # sent index after its last matched symbol
    lost: bool = False            # stopped checking after an error (slip)
    lead_skipped: int = 0         # a pre-comma symbol at the start of the lock
    noise_lock: bool = False      # the run starts with an error word (a lock on noise)
    mismatches: int = 0
    placed: list[tuple[int, int]] = field(default_factory=list)  # (word time, sent index)


class CheckError(AssertionError):
    pass


def _key(s: tuple[int, int]) -> bytes:
    return bytes((s[0], s[1]))


def runs_of(words: list[Word]) -> list[Run]:
    runs, cur = [], []
    for w in words:
        if w.valid:
            cur.append(w)
        elif cur:
            runs.append(Run(cur))
            cur = []
    if cur:
        runs.append(Run(cur))
    return runs


class Stream:
    """The sent symbols with SKP set aside, as bytes for substring search."""

    def __init__(self, sent: list[tuple[int, int]]) -> None:
        self.index = [i for i, s in enumerate(sent) if s != (1, K_SKP)]
        self.syms = [sent[i] for i in self.index]
        self.blob = b"".join(_key(s) for s in self.syms)

    def find(self, needle: list[tuple[int, int]], lo: int, hi: int | None = None) -> int:
        """Position (in non-SKP symbols) of ``needle`` at or after ``lo``
        and ending by ``hi``, or -1."""
        if not needle:
            return lo
        b = b"".join(_key(s) for s in needle)
        start = 2 * max(lo, 0)
        while True:
            p = self.blob.find(b, start, None if hi is None else 2 * hi)
            if p < 0:
                return -1
            if p % 2 == 0:
                return p // 2
            start = p + 1


def check_runs(sent: list[tuple[int, int]], words: list[Word], slip_possible=lambda t0, t: False,
               where: str = "", flipped=frozenset(), arrival=None,
               lead_skip=lambda t: False, latency_max: int = LATENCY_MAX_PS) -> list[Run]:
    """Align every run with the sent stream; raise CheckError on loss,
    duplication, reordering or an unexplained wrong symbol.

    A wrong symbol is explained when a word reporting an error (RxStatus 4
    or 7) follows within TRIM_MAX symbols, or when the sent symbol is one
    the channel flipped a bit of (``flipped``, sent indices): two flips can
    cancel each other's disparity and go unreported, as 8b/10b allows. Near
    an explained error the stream may also shift by up to RESYNC_SLACK
    symbols (a flipped bit can turn a SKP into another symbol, or the
    reverse). A run that loses the stream otherwise is accepted only when
    ``slip_possible(run start, time)`` says a bit slip inside the run may
    explain it.

    A run is anchored on its first clean 24-symbol window. In periodic
    traffic (training or FTS sets) that window matches many places; with
    ``arrival`` (sent index -> ps when its last bit was sampled) a window
    anchors only when exactly one match lies at most ``latency_max`` before
    the word that holds it, so a run that starts in periodic traffic is
    anchored further on, where the stream is unique, and its start is then
    checked backwards from there. Symbol 0 of a run may be the symbol
    before the lock's comma, unchecked, only for the first run (after the
    initial electrical idle) or when ``lead_skip`` (the run's start time)
    says the run starts after electrical idle."""
    stream = Stream(sent)
    n_sent = len(stream.syms)
    runs = runs_of(words)
    pos = 0
    for n_run, r in enumerate(runs):
        flat = [(s, w) for w in r.words for s in w.syms if s != (1, K_SKP)]
        syms = [s for s, _ in flat]
        # Symbol j is followed by a reported error within TRIM_MAX symbols.
        flag_after = [False] * len(flat)
        nxt = None
        for j in range(len(flat) - 1, -1, -1):
            if flat[j][1].status in ERROR_CODES and flat[j][1].valid:
                nxt = j
            flag_after[j] = nxt is not None and nxt - j <= TRIM_MAX

        def explained(j: int, p: int) -> bool:
            return flag_after[j] or (0 <= p < n_sent and stream.index[p] in flipped)

        def plausible(k0: int, at: int) -> bool:
            if arrival is None:
                return True
            lat = flat[k0][1].t - arrival(stream.index[at])
            return 0 <= lat <= latency_max

        anchor = None
        windows = list(range(0, min(32, len(syms)))) + list(range(32, max(32, len(syms) - 24), 4))
        for k0 in windows:
            needle = syms[k0:k0 + 24]
            if len(needle) < min(24, len(syms)) or any(flag_after[k0:k0 + 24]):
                continue
            # Every match the window's time allows; the anchor must be the
            # only one, so a window inside periodic traffic never anchors.
            found, at = [], stream.find(needle, pos)
            while at >= 0 and len(found) < 2:
                if arrival is not None and flat[k0][1].t < arrival(stream.index[at]):
                    break
                if plausible(k0, at):
                    found.append(at)
                at = stream.find(needle, at + 1)
                if arrival is None and found:
                    break
            if len(found) == 1:
                anchor = (k0, found[0] - k0)
                break
        if anchor is None:
            if len(syms) < 24 and all(flag_after) or slip_possible(r.words[0].t, r.words[-1].t):
                r.lost = True
                continue
            raise CheckError(f"{where}: run at t={r.words[0].t} ps ({len(syms)} symbols) "
                             f"not found in the sent stream after {pos}: {syms[:8]}")
        k0, base = anchor
        # The symbols before the anchor, checked like the rest.
        may_skip = n_run == 0 or lead_skip(r.words[0].t)
        for j in range(k0):
            p = base + j
            if 0 <= p < n_sent and syms[j] == stream.syms[p]:
                continue
            if explained(j, p):
                r.mismatches += 1
                continue
            if j == 0 and may_skip:
                r.lead_skipped = 1
                continue
            raise CheckError(f"{where}: run at t={r.words[0].t} ps: symbol {j} got {syms[j]}, "
                             f"sent {stream.syms[p] if 0 <= p < n_sent else None}, before the "
                             f"anchor, no error reported")
        r.first = stream.index[min(n_sent - 1, max(0, base + r.lead_skipped))]
        r.noise_lock = any(w.status in ERROR_CODES for w in r.words[:1])
        j = k0
        while j < len(syms):
            p = base + j
            if p < n_sent and syms[j] == stream.syms[p]:
                r.placed.append((flat[j][1].t, stream.index[p]))
                j += 1
                continue
            if explained(j, p):
                # A wrong symbol, or a shift of the stream, near an error.
                for shift in [0] + [d for k in range(1, RESYNC_SLACK + 1) for d in (k, -k)]:
                    q = p + shift
                    win = syms[j + 1:j + 9]
                    if q + 1 >= 0 and stream.syms[q + 1:q + 1 + len(win)] == win:
                        base += shift
                        break
                r.mismatches += 1
                j += 1
                continue
            if slip_possible(r.words[0].t, flat[j][1].t):
                r.lost = True
                break
            ref = stream.syms[p:p + 4]
            raise CheckError(f"{where}: run at t={r.words[0].t} ps: symbol {j} (t={flat[j][1].t} "
                             f"ps) got {syms[j:j + 4]}, sent {ref} at {p}, no error reported")
        r.last = base + j
        if r.last > pos:
            pos = r.last
    return runs


def check_skp_status(runs: list[Run], where: str = "") -> int:
    """In a clean run, every SKP ordered set (COM and three SKP as sent)
    leaves with 2, 3 or 4 SKP, and its COM's word carries RxStatus 2, 0 or 1
    accordingly. Returns the number of sets checked."""
    checked = 0
    for r in runs:
        flat = [(s, w) for w in r.words for s in w.syms]
        i = 0
        while i < len(flat):
            s, w = flat[i]
            if s == (1, K_COM):
                n = 0
                while i + 1 + n < len(flat) and flat[i + 1 + n][0] == (1, K_SKP):
                    n += 1
                if n and i + 1 + n < len(flat):
                    if n not in STATUS_SKP:
                        raise CheckError(f"{where}SKP set at t={w.t} ps left with {n} SKP")
                    if w.status != STATUS_SKP[n]:
                        raise CheckError(f"{where}SKP set at t={w.t} ps with {n} SKP: "
                                         f"RxStatus {w.status}, expected {STATUS_SKP[n]}")
                    checked += 1
                i += 1 + n
            else:
                i += 1
    return checked


ERR_LIMIT = 3                     # RECEIVE.md, Relock request
ERR_DECAY_WORDS = 8


def relock_requests(words: list[Word]) -> list[int]:
    """The relock rule of RECEIVE.md applied to the words seen on PIPE:
    indices of the words whose error makes the PCS send a relock request.
    An errored word is one with RxValid and RxStatus 4 or 7; a new RxValid
    run is a new lock generation (true whenever lock is not retaken in the
    same cycle, as in packet traffic), which clears the count and ends the
    wait for the relock."""
    out = []
    count = clean = 0
    wait = False
    prev_valid = 0
    for i, w in enumerate(words):
        if w.valid and not prev_valid:
            count = clean = 0
            wait = False
        prev_valid = w.valid
        if not w.valid or wait:
            continue
        if w.status in ERROR_CODES:
            count += 1
            clean = 0
            if count >= ERR_LIMIT:
                out.append(i)
                count, wait = 0, True
        else:
            clean += 1
            if clean >= ERR_DECAY_WORDS:
                count, clean = max(0, count - 1), 0
    return out


def check_relocks(words: list[Word], max_delay: int, where: str = "",
                  explained=lambda i: False) -> int:
    """Every predicted request ends its RxValid run within ``max_delay``
    words, and every run that ends early (``explained`` aside: idle, slips,
    the end of the line) follows a predicted request. Returns the number of
    requests."""
    reqs = relock_requests(words)
    ends = [i for i in range(1, len(words)) if words[i - 1].valid and not words[i].valid]
    for r in reqs:
        nxt = [e for e in ends if e > r]
        if not nxt or nxt[0] - r > max_delay:
            raise CheckError(f"{where}relock request at word {r} (t={words[r].t} ps) did not "
                             f"drop RxValid within {max_delay} cycles")
    for e in ends:
        if explained(e):
            continue
        prev = [r for r in reqs if r < e]
        if not prev or e - prev[-1] > max_delay:
            raise CheckError(f"{where}RxValid fell at word {e} (t={words[e].t} ps) "
                             f"with no relock request")
    return len(reqs)
