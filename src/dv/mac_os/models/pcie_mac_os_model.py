"""Cycle model of pcie_mac_os (wiki/src/rtl/mac_os/MAS.md), the DV
reference for its tests.

Written from the MAS. It reuses the parent's architecture model
(src/arch/mac/pcie_mac_arch.py) as the arch BKM allows: its symbol
constants, and its own TS builder (``Mac._queue_ts``) as the source of TS
symbols. The unit tests also check the receive parser against the
architecture model's parser.

A symbol is ``(k, byte)``; a word is two symbols, ``[0]`` the earlier
(``[7:0]``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[3] / "arch" / "mac"))
import pcie_mac_arch as arch  # noqa: E402

COM, PAD, SKP, FTS, IDL = arch.COM, arch.PAD, arch.SKP, arch.FTS, arch.EIDL
TS1_ID, TS2_ID = arch.TS1_ID, arch.TS2_ID
IDS = {TS1_ID: (False, False), TS2_ID: (True, False),
       TS1_ID ^ 0xFF: (False, True), TS2_ID ^ 0xFF: (True, True)}  # (ts2, inverted)
SKP_INTERVAL = arch.SKP_INTERVAL_MIN
CREDIT_MAX = (1 << 14) - 1

KINDS = ("ts", "skp", "eios", "fts", "compliance")


def ts_symbols(n_fts: int, ts2: bool, link, lane, ctl: int) -> list:
    """The 16 symbols of a TS from the architecture model's own TS builder;
    ``link``/``lane`` None is PAD."""
    m = arch.Mac(n_fts=n_fts)
    m.txq.clear()
    m._queue_ts("TS2" if ts2 else "TS1", link, lane, ctl)
    return [(k, b) for k, b, _, _ in m.txq]


def set_symbols(kind: str, n_fts: int = 32, ts2=False, link=None, lane=None, ctl=0):
    """Symbols of one ordered set of ``kind`` (MAS, Transmit)."""
    if kind == "ts":
        return ts_symbols(n_fts, ts2, link, lane, ctl)
    if kind == "compliance":
        return [COM, (0, arch.D21_5), COM, (0, arch.D10_2)]
    body = {"skp": SKP, "eios": IDL, "fts": FTS}[kind]
    return [COM, body, body, body]


class Tx:
    """Transmit side: one ``step`` per clock with the cycle's inputs; returns
    the outputs and updates the state (MAS, Transmit)."""

    def __init__(self, n_fts: int = 32) -> None:
        self.n_fts = n_fts
        self.reset()

    def reset(self) -> None:
        self.words: list = []      # words of the set in progress still to send
        self.kind = None
        self.credit = 0

    def drives(self, req: str | None, skp_ok: bool) -> bool:
        """Whether ``tx_valid_o`` is 1 this cycle for these inputs."""
        return bool(self.words) or (self.credit >= SKP_INTERVAL and skp_ok) or req is not None

    def step(self, req: str | None, fields: dict, skp_ok: bool, count: bool) -> dict:
        """``req`` is the requested kind or None; ``fields`` holds ts2, link,
        lane (None for PAD) and ctl."""
        due = self.credit >= SKP_INTERVAL
        out = {"valid": False, "word": ((0, 0), (0, 0)), "ack": False, "last": False,
               "compliance": False, "skp_due": due, "kind": None, "start": False}
        sched = req_start = False
        if not self.words:
            if due and skp_ok:
                sched = True
                self.kind, syms = "skp", set_symbols("skp")
            elif req is not None:
                req_start = True
                self.kind = req
                syms = set_symbols(req, self.n_fts, **fields)
            if sched or req_start:
                self.words = [(syms[i], syms[i + 1]) for i in range(0, len(syms), 2)]
                out["start"] = True
                out["compliance"] = self.kind == "compliance"
        if self.words:
            out["valid"] = True
            out["kind"] = self.kind
            out["word"] = self.words.pop(0)
            out["last"] = not self.words
        out["ack"] = req_start
        if req_start and req == "skp":
            credit = 0
        elif sched:
            credit = self.credit - SKP_INTERVAL
        else:
            credit = self.credit
        if (out["valid"] and self.kind != "compliance") or count:
            credit += 2
        self.credit = min(credit, CREDIT_MAX)
        return out


class Rx:
    """Receive parser (MAS, Receive). ``step`` takes one word and returns
    the cycle's events and the fields of a TS that completed."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.phase = "out"
        self.ts: list = []

    def _sym(self, k: int, b: int, ev: dict) -> bool:
        sym = (k, b)
        if sym == COM:
            if self.phase == "ts":
                ev["ts_bad"], ev["bad_reason"] = True, "cut_by_com"
            self.phase = "com"
            return True
        p = self.phase
        if p == "com":
            if sym == SKP:
                ev["skp"], self.phase = True, "skp"
            elif sym == IDL:
                ev["eios"], self.phase = True, "body"
            elif sym == FTS:
                ev["fts"], self.phase = True, "body"
            else:
                self.phase, self.ts = "ts", [sym]
            return True
        if p == "ts":
            self.ts.append(sym)
            if len(self.ts) == 15:
                self.phase = "out"
                f = decode(self.ts)
                if f is None:
                    ev["ts_bad"], ev["bad_reason"] = True, bad_reason(self.ts)
                else:
                    ev["ts"] = True
                    ev["fields"] = f
            return True
        if p == "skp" and sym == SKP:
            return True
        if p == "body" and sym in (IDL, FTS):
            return True
        self.phase = "out"
        return False

    def step(self, word, valid: bool = True, elec_idle: bool = False) -> dict:
        ev = {"ts": False, "ts_bad": False, "skp": False, "eios": False, "fts": False,
              "os_sym": [False, False], "fields": None, "bad_reason": None, "slot": None}
        if valid and not elec_idle:
            for i, (k, b) in enumerate(word):
                before = (ev["ts"], ev["skp"], ev["eios"], ev["fts"])
                ev["os_sym"][i] = self._sym(k, b, ev)
                if (ev["ts"], ev["skp"], ev["eios"], ev["fts"]) != before:
                    ev["slot"] = i      # the symbol that completed the set
        return ev


def bad_reason(ts: list) -> str:
    """Why 15 TS symbols fail to decode (for coverage)."""
    first = ts[5]
    if any(x != first for x in ts[5:]):
        return "id_mismatch"
    return "id_k" if first[0] else "id_unknown"


def decode(ts: list) -> dict | None:
    """Fields of the 15 symbols after COM, or None when the TS fails to
    decode (MAS, Receive)."""
    ids = ts[5:]
    first = ids[0]
    if first[0] or any(x != first for x in ids) or first[1] not in IDS:
        return None
    ts2, inv = IDS[first[1]]

    def field(sym):
        k, b = sym
        return {"byte": b, "pad": sym == PAD, "num": not k}

    return {"ts2": ts2, "inv": inv, "link": field(ts[0]), "lane": field(ts[1]),
            "n_fts": ts[2][1], "ctl": ts[4][1] & 0x1F}
