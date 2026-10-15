"""Reference model and stream checker of pcie_pcs_elastic
(wiki/src/rtl/pcs_elastic/MAS.md, wiki/src/dv/pcs_elastic/DV_PLAN.md).

Written from the pcs receive-path rules (wiki/src/arch/pcs/RECEIVE.md,
Elastic buffer), not from the RTL. The pcs architecture model is imported
for the 8b/10b code, the traffic it sends and the buffer constants (the
arch BKM lets DV import arch; src/arch/pcs is appended to sys.path,
located from this file).

- ``Traffic`` builds the aligned symbol stream the write side sees: raw
  10-bit codes in words of two, each word with the aligner's lock tag and
  lock-generation bit.
- ``check`` compares the read side's words with that stream after the
  run. The symbols that leave must be the symbols that entered, in order,
  except for these changes, each of which the rules allow:

  * one SKP removed from a locked SKP ordered set (at most one per set),
    reported (RxStatus 2) on the word holding the set's COM, or on the word
    holding the symbol after the removed SKP when that was not yet visible
    to the read side (accepted only when the COM's word went out at a
    visible fill below 4, the only fill at which that can happen);
  * one SKP added to a SKP ordered set whose COM left locked (at most one
    per set), reported (RxStatus 1) on the word holding the COM;
  * an unlocked aligned word dropped by recentring;
  * with ``loss`` set, words lost to overflow, the first word after each
    loss carrying the overflow flag (and, when the lost words held a SKP
    ordered set with a removal, possibly the removed flag).

  A symbol may leave unlocked although it entered locked only at a change
  of lock or generation (a word packed from both sides of it is tagged
  unlocked).
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "pcs"))
import pcie_pcs_arch as arch  # noqa: E402
import pcie_pcs_8b10b as code8b10b  # noqa: E402

# Buffer constants (RECEIVE.md, Elastic buffer), taken from the arch model.
EB_WORDS = arch.EB_WORDS
EB_NOMINAL = arch.EB_NOMINAL
EB_HIGH = arch.EB_HIGH
EB_LOW = arch.EB_LOW
EB_HOLDOFF = arch.EB_HOLDOFF_WORDS
FILL_MIN, FILL_MAX = 4, 12          # HAS targets, visible fill in symbols
RESYNC_WINDOW = 4 * EB_WORDS * 2    # symbols searched after an overflow
RESYNC_MATCH = 8                    # symbols that must agree to resync
TAIL = 64                           # trailing symbols a run need not deliver
FALLBACK_FILL = 4                   # below it the symbol after a COM may be unseen

COM_CODES = frozenset(code8b10b.ENCODE[(code8b10b.K_COM, True, rd)][0] for rd in (-1, 1))
SKP_CODES = frozenset(code8b10b.ENCODE[(code8b10b.K_SKP, True, rd)][0] for rd in (-1, 1))


def is_com(code: int) -> bool:
    return code8b10b.raw_is(code, code8b10b.K_COM)


def is_skp(code: int) -> bool:
    return code8b10b.raw_is(code, code8b10b.K_SKP)


# ------------------------------------------------------------------ traffic

def packet_stream(rng: random.Random, sizes, packets: int, skp_interval: int,
                  skp_counts=(3,), lead_ts: int = 8) -> list[tuple[int, int]]:
    """(k, byte) symbols a partner sends: ``lead_ts`` TS1 ordered sets, then
    ``packets`` back-to-back packets (STP, data, END) whose data length is
    drawn from ``sizes`` (a list, or a callable of ``rng``), with a SKP
    ordered set due every ``skp_interval`` symbols, deferred to the end of
    an ordered set or packet and sent back to back when several are due
    (the rule of arch.make_stream; RECEIVE.md, Depth versus clock offset).
    Each set is a COM and a SKP count drawn from ``skp_counts``."""
    out: list[tuple[int, int]] = []
    credit = 0

    def skp_set() -> None:
        nonlocal credit
        n = rng.choice(skp_counts)
        out.extend([(1, code8b10b.K_COM)] + [(1, code8b10b.K_SKP)] * n)
        credit += 1 + n

    def boundary() -> None:
        nonlocal credit
        while credit >= skp_interval:
            credit -= skp_interval
            skp_set()

    ts1 = [(1, code8b10b.K_COM), (1, code8b10b.K_PAD), (1, code8b10b.K_PAD),
           (0, 0x80), (0, 0x02), (0, 0x00)] + [(0, 0x4A)] * 10
    for _ in range(lead_ts):
        boundary()
        out.extend(ts1)
        credit += len(ts1)
    for _ in range(packets):
        boundary()
        n = sizes(rng) if callable(sizes) else rng.choice(sizes)
        out.append((1, code8b10b.K_STP))
        out.extend((0, rng.randrange(256)) for _ in range(n))
        out.append((1, code8b10b.K_END))
        credit += n + 2
    boundary()
    skp_set()
    return out


@dataclass
class Sym:
    code: int
    lock: int
    gen: int


@dataclass
class Traffic:
    """The aligned stream into the write side: ``syms`` in words of two
    (``syms[2j]`` is word j's bits [9:0]); every symbol of a word shares its
    lock tag and generation."""

    syms: list[Sym] = field(default_factory=list)
    idle_words: int = 0
    relocks: int = 0

    @property
    def words(self) -> int:
        return len(self.syms) // 2

    @staticmethod
    def build(rng: random.Random, symbols: list[tuple[int, int]],
              idles: list[tuple[int, int]] = (), relocks: int = 0,
              tail: int = TAIL) -> "Traffic":
        """Encode ``symbols`` with running disparity and tag them as the
        aligner would: locked, generation 1 from the start; inside each
        ``idles`` symbol range the line carries noise and is unlocked, and
        lock returns (with the generation flipped) on the first word holding
        a COM after it. ``relocks`` same-cycle relocks (the generation flips,
        lock stays) are placed at random locked words. ``tail`` locked data
        symbols are appended so the run ends in data."""
        symbols = list(symbols) + [(0, rng.randrange(256)) for _ in range(tail)]
        if len(symbols) % 2:
            symbols.append((0, 0))
        codes, _ = arch.tx_encode(symbols)
        idle = bytearray(len(codes))
        for a, b in idles:
            for i in range(a, b):
                idle[i] = 1
                codes[i] = rng.randrange(1024)
        t = Traffic()
        nwords = len(codes) // 2
        flip_at = set(rng.sample(range(8, nwords - 8), relocks)) if relocks else set()
        lock, gen = 1, 1
        for j in range(nwords):
            a, b = 2 * j, 2 * j + 1
            if idle[a] or idle[b]:
                if lock:
                    t.idle_words += 1
                lock = 0
            elif not lock and (is_com(codes[a]) or is_com(codes[b])):
                lock, gen = 1, gen ^ 1
            elif lock and j in flip_at:
                gen ^= 1
                t.relocks += 1
            t.syms.append(Sym(codes[a], lock, gen))
            t.syms.append(Sym(codes[b], lock, gen))
        return t


def link_traffic(rng: random.Random, kind: str, packets: int, skp_interval: int,
                 idle_gaps: int = 0, idle_symbols: int = 0, relocks: int = 0) -> Traffic:
    """Traffic for one test: ``kind`` is ``tlp`` (maximum TLPs), ``random``
    (random TLP and DLLP sizes, as arch.make_stream), ``dllp``, ``stress``
    (sizes chosen to strand corrections: 4122, 3070, 1534 and 2 bytes) or
    ``dense`` (short packets with SKP ordered sets of 1 to 5 SKPs every
    ``skp_interval`` symbols, for the corner cases) or ``short_burst`` (a packet just under two SKP intervals, so one set too
    few goes out after it, then a maximum TLP; the reviewer's case)."""
    if idle_gaps:
        symbols, idles = arch.make_stream(rng, packets, kind, arch.TLP_BYTES_MAX,
                                          skp_interval, idle_gaps, idle_symbols, lead_ts=8)
        return Traffic.build(rng, symbols, idles, relocks)
    if kind == "tlp":
        sizes = [arch.TLP_BYTES_MAX]
    elif kind == "dllp":
        sizes = [6]
    elif kind == "random":
        sizes = lambda r: 2 + r.choice((12, 16)) + 4 * r.randrange(0, 65) + 4  # noqa: E731
    elif kind == "stress":
        sizes = [4122, 3070, 1534, 2]
    elif kind == "dense":
        sizes = lambda r: r.randrange(0, 48)  # noqa: E731
    elif kind == "short_burst":
        # 2 * interval - 2 (STP, END) - 4 (the set) - 1: one set is sent
        # after a gap that needed two.
        sizes = [2 * skp_interval - 7, arch.TLP_BYTES_MAX]
    else:
        raise ValueError(f"unknown traffic kind {kind!r}")
    counts = (1, 2, 3, 4, 5) if kind in ("random", "dense") else (3,)
    symbols = packet_stream(rng, sizes, packets, skp_interval, counts)
    return Traffic.build(rng, symbols, relocks=relocks)


def plain_traffic(rng: random.Random, symbols: int, lock: int = 1) -> Traffic:
    """Data only, no ordered set: nothing to add or remove (overflow and
    underflow tests)."""
    syms = [(0, rng.randrange(256)) for _ in range(symbols)]
    t = Traffic.build(rng, syms, tail=0)
    if not lock:
        for s in t.syms:
            s.lock = 0
    return t


# ------------------------------------------------------------------ checker

@dataclass
class Word:
    """One read-side output word, sampled while ``rx_valid_o`` is 1."""

    cycle: int
    sym0: int
    sym1: int
    lock: tuple[int, int]
    gen: tuple[int, int]
    add: int
    rem: int
    ovf: int
    udf: int
    fill: int = -1   # fill_o in that cycle; -1: not recorded


@dataclass
class Result:
    delivered: int = 0     # input symbols delivered
    added: int = 0
    removed: int = 0
    rem_on_com: int = 0    # removals reported on the COM's word
    rem_fallback: int = 0  # reported on the next symbol's word
    add_slot: list[int] = field(default_factory=lambda: [0, 0])  # COM slot of each add
    dropped_words: int = 0
    lost_symbols: int = 0
    overflows: int = 0
    underflows: int = 0
    downgraded: int = 0    # locked symbols that left unlocked (lock change)
    flushed: int = 0       # unlocked symbols dropped just before a lock (MAS, Lock start)
    late_lock_start: int = 0  # locked symbols that left unlocked at a lock start
    back_to_back_coms: int = 0
    bare_coms: int = 0     # one-SKP sets that left as a bare COM
    # Every SKP ordered set sent locked: input index of its COM, word of the
    # COM, SKPs in the input, change ("add", "rem" or None) and whether a
    # removal and an addition were allowed.
    sets: list[dict] = field(default_factory=list)


class CheckError(AssertionError):
    pass


def _boundaries(syms: list[Sym]) -> set[int]:
    """Indices whose lock tag or generation differs from the symbol before."""
    out = set()
    for i in range(1, len(syms)):
        if (syms[i].lock, syms[i].gen) != (syms[i - 1].lock, syms[i - 1].gen):
            out.add(i)
    return out


def check(traffic: Traffic, words: list[Word], loss: bool = False,
          udf_allowed_from: int | None = None) -> Result:
    """Check ``words`` (every word with rx_valid_o high, in order) against
    ``traffic``. Underflow words are allowed only from cycle
    ``udf_allowed_from`` on (None: anywhere). Raises CheckError with the
    word, cycle and symbol index of the first mismatch."""
    inp = traffic.syms
    near = set()
    for b in _boundaries(inp):
        near.update((b - 1, b))
    r = Result()
    out: list[tuple[int, int, int, int, int]] = []  # code, lock, gen, word, slot
    for wi, w in enumerate(words):
        if w.udf:
            r.underflows += 1
            if udf_allowed_from is not None and w.cycle < udf_allowed_from:
                raise CheckError(f"word {wi} (cycle {w.cycle}): underflow while data was "
                                 "still being written")
            if (w.sym0, w.sym1, w.lock, w.add, w.rem, w.ovf) != (0, 0, (0, 0), 0, 0, 0):
                raise CheckError(f"word {wi} (cycle {w.cycle}): underflow word carries data "
                                 "or other flags")
            continue
        out.append((w.sym0, w.lock[0], w.gen[0], wi, 0))
        out.append((w.sym1, w.lock[1], w.gen[1], wi, 1))

    add_words: set[int] = set()
    rem_events: list[tuple[int, int]] = []
    resync_words: set[int] = set()
    p = 0
    sset = None   # the SKP ordered set being sent: dict
    i = 0

    def where(k: int) -> str:
        code, lock, gen, wi, slot = out[k]
        exp = inp[p] if p < len(inp) else None
        e = f"{exp.code:#05x} lock {exp.lock} gen {exp.gen}" if exp else "end of input"
        return (f"word {wi} slot {slot} (cycle {words[wi].cycle}), input symbol {p}: "
                f"expected {e}, got {code:#05x} lock {lock} gen {gen}")

    while i < len(out):
        code, lock, gen, wi, slot = out[i]
        if sset is not None:
            if sset["next_word"] is None:
                sset["next_word"] = wi   # the word after the COM's symbol
            if is_skp(code):
                if sset["left"] > 0 and p < len(inp) and inp[p].code == code:
                    sset["left"] -= 1
                    p += 1
                    r.delivered += 1
                    i += 1
                    continue
                if sset["change"] is not None or not sset["add_ok"] or sset["n"] == 0:
                    raise CheckError("SKP added where no addition is allowed: " + where(i))
                if code != sset["skp"]:
                    raise CheckError("added SKP is not a copy of the set's SKP: " + where(i))
                sset["change"] = "add"
                add_words.add(sset["com_word"])
                r.added += 1
                r.add_slot[sset["com_slot"]] += 1
                i += 1
                continue
            # The set ends here.
            if sset["left"] == 1 and sset["change"] is None and sset["rem_ok"]:
                sset["change"] = "rem"
                rem_events.append((sset["com_word"], sset["next_word"]))
                r.removed += 1
                if sset["n"] == 1:
                    r.bare_coms += 1
                p += 1
            elif sset["left"] != 0:
                raise CheckError(f"{sset['left']} SKP(s) missing from a SKP ordered set: "
                                 + where(i))
            r.sets.append(sset)
            sset = None
            continue   # re-examine this symbol outside the set
        if p < len(inp):
            exp = inp[p]
            lock_ok = lock == exp.lock or (lock == 0 and exp.lock == 1 and p in near)
            gen_ok = lock == 0 or gen == exp.gen
            if code == exp.code and lock_ok and gen_ok:
                if lock != exp.lock:
                    r.downgraded += 1
                    # The first locked symbols after unlocked ones (a lock
                    # start) should leave locked (MAS, Lock start).
                    if any(not inp[k].lock for k in range(max(0, p - 2), p)):
                        r.late_lock_start += 1
                p += 1
                r.delivered += 1
                if exp.lock and is_com(code):
                    n = 0
                    while p + n < len(inp) and is_skp(inp[p + n].code):
                        n += 1
                    skps = inp[p:p + n]
                    if i > 0 and is_com(out[i - 1][0]) and out[i - 1][1]:
                        r.back_to_back_coms += 1
                    sset = {"com_in": p - 1, "com_word": wi, "com_slot": slot,
                            "left": n, "n": n,
                            "next_word": None,
                            "change": None, "add_ok": bool(lock),
                            "rem_ok": n > 0 and all(s.lock for s in skps),
                            "skp": skps[0].code if n else None}
                i += 1
                continue
            # Lock start: the unlocked symbol before the first symbol of a
            # lock may be dropped, so the lock starts a word of its own. On
            # the read side it is the leftover symbol, which whole unlocked
            # words dropped by recentring may separate from the lock.
            if not exp.lock:
                q = p + 1
                while q + 1 < len(inp) and q % 2 == 0 and not inp[q].lock \
                        and not inp[q + 1].lock and inp[q].code != code:
                    q += 2
                if q < len(inp) and inp[q].lock and inp[q].code == code:
                    r.flushed += 1
                    r.dropped_words += (q - p - 1) // 2
                    p = q
                    continue
            # A dropped word right after a symbol that also matched the
            # dropped word's first symbol: the earlier match was greedy, so
            # take it as the symbol after the dropped word instead.
            if (p % 2 == 1 and not inp[p - 1].lock and not exp.lock and p + 2 < len(inp)
                    and inp[p + 1].code == inp[p - 1].code and inp[p + 2].code == code):
                r.dropped_words += 1
                p += 2
                continue
            if exp.lock == 0 and p % 2 == 0 and p + 1 < len(inp) and inp[p + 1].lock == 0:
                r.dropped_words += 1
                p += 2
                continue
            if loss and words[wi].ovf and wi not in resync_words:
                # Match up to RESYNC_MATCH symbols, up to and including the
                # first COM (a SKP may be added or removed after it).
                want = []
                for o in out[i:i + RESYNC_MATCH]:
                    want.append(o[0])
                    if is_com(o[0]):
                        break
                for q in range(p + 1, min(len(inp), p + RESYNC_WINDOW)):
                    if [s.code for s in inp[q:q + len(want)]] == want:
                        r.lost_symbols += q - p
                        r.overflows += 1
                        resync_words.add(wi)
                        p = q
                        break
                else:
                    raise CheckError("overflow flag but no resynchronization point: " + where(i))
                continue
        raise CheckError("symbol mismatch: " + where(i))
    if sset is not None and sset["left"] > 1:
        raise CheckError("run ended inside a SKP ordered set with SKPs missing")

    # Status flags.
    for wi, w in enumerate(words):
        if w.udf:
            continue
        if bool(w.add) != (wi in add_words):
            raise CheckError(f"word {wi} (cycle {w.cycle}): add flag {w.add}, expected "
                             f"{int(wi in add_words)}")
        if w.ovf and wi not in resync_words:
            raise CheckError(f"word {wi} (cycle {w.cycle}): overflow flag with no lost word")
        # After a loss, the removal it reports may have been in a set whose
        # COM was lost.
        if w.rem and not any(wi in ev for ev in rem_events) and wi not in resync_words:
            raise CheckError(f"word {wi} (cycle {w.cycle}): removed flag with no removal")
    for com_w, next_w in rem_events:
        if words[com_w].rem:
            r.rem_on_com += 1
        elif words[next_w].rem:
            # Allowed only when the symbol after the COM was not yet visible
            # as the COM left, which needs a fill below FALLBACK_FILL.
            if not 0 <= words[com_w].fill < FALLBACK_FILL:
                raise CheckError(f"removal reported on word {next_w}, not on the COM's word "
                                 f"{com_w} (cycle {words[com_w].cycle}, fill "
                                 f"{words[com_w].fill}): the symbol after the COM was visible")
            r.rem_fallback += 1
        else:
            raise CheckError(f"removal in the set whose COM left in word {com_w} (cycle "
                             f"{words[com_w].cycle}) is not reported")
    if resync_words - {wi for wi, w in enumerate(words) if w.ovf}:
        raise CheckError("a loss without the overflow flag")
    undelivered = len(inp) - p
    if undelivered > TAIL:
        raise CheckError(f"{undelivered} input symbols never delivered (last {TAIL} may be "
                         "in flight)")
    return r


def dump(traffic: Traffic, words: list[Word], path: str) -> Path:
    """Write the run's input stream and output words as JSON, for triage."""
    out = Path(path).resolve()
    out.write_text(json.dumps({"input": [asdict(s) for s in traffic.syms],
                               "words": [asdict(w) for w in words]}))
    return out


def load(path: str) -> tuple[Traffic, list[Word]]:
    """Read a dump back: check(*load(path)) reproduces the failure."""
    d = json.loads(Path(path).read_text())
    t = Traffic([Sym(**s) for s in d["input"]])
    return t, [Word(**{**w, "lock": tuple(w["lock"]), "gen": tuple(w["gen"])}) for w in d["words"]]
