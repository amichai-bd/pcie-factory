"""Unit tests of the pcie_mac_os model; no simulator needed.

Run: python3 -m pytest src/dv/mac_os/models
"""

import random

import pcie_mac_os_model as m
from pcie_mac_os_model import COM, IDL, FTS, PAD, SKP, arch


def test_ts_layout_matches_mas():
    syms = m.ts_symbols(17, False, None, 0, 5)
    assert syms[:6] == [COM, PAD, (0, 0), (0, 17), (0, 0x02), (0, 5)]
    assert syms[6:] == [(0, 0x4A)] * 10
    syms = m.ts_symbols(255, True, 200, None, 0)
    assert syms[1:3] == [(0, 200), PAD] and syms[6:] == [(0, 0x45)] * 10


def test_other_sets():
    assert m.set_symbols("skp") == [COM, SKP, SKP, SKP]
    assert m.set_symbols("eios") == [COM, IDL, IDL, IDL]
    assert m.set_symbols("fts") == [COM, FTS, FTS, FTS]
    assert m.set_symbols("compliance") == [COM, (0, 0xB5), COM, (0, 0x4A)]


def words(syms):
    return [(syms[i], syms[i + 1]) for i in range(0, len(syms), 2)]


def run_rx(stream):
    rx, evs = m.Rx(), []
    for w in words(stream):
        evs.append(rx.step(w))
    return evs


def test_round_trip_both_phases():
    for kind in ("ts", "skp", "eios", "fts"):
        for phase in (0, 1):
            fields = dict(ts2=True, link=3, lane=None, ctl=4)
            stream = [(0, 0)] * phase + m.set_symbols(kind, 9, **fields) + [(0, 0)] * (2 - phase)
            evs = run_rx(stream)
            hits = [e for e in evs if e[kind if kind != "ts" else "ts"]]
            assert len(hits) == 1, (kind, phase)
            if kind == "ts":
                f = hits[0]["fields"]
                assert f["ts2"] and not f["inv"] and f["link"]["byte"] == 3
                assert f["lane"]["pad"] and f["n_fts"] == 9 and f["ctl"] == 4


def test_malformed_rejected():
    good = m.ts_symbols(32, False, 0, 0, 0)
    for i in range(6, 16):
        bad = list(good)
        bad[i] = (0, 0x4B)
        evs = run_rx(bad)
        assert not any(e["ts"] for e in evs) and sum(e["ts_bad"] for e in evs) == 1
    cut = good[:9] + good
    evs = run_rx(cut + [(0, 0)])
    assert sum(e["ts_bad"] for e in evs) == 1 and sum(e["ts"] for e in evs) == 1


class Recorder(arch.Mac):
    """The architecture model's receive parser with its ordered-set
    callbacks recorded instead of acted on."""

    def __init__(self):
        super().__init__()
        self.log = []

    def _on_ts(self, ts):
        if ts is None:
            self.log.append(("ts_bad",))
        else:
            self.log.append(("ts", ts.kind, ts.link, ts.lane, ts.n_fts, ts.ctl & 0x1F))

    def _on_skp(self):
        self.log.append(("skp",))

    def _on_eios(self):
        self.log.append(("eios",))


def random_stream(rng, n_sets):
    pool = [COM, SKP, IDL, FTS, PAD, (1, 0xFB), (0, 0x4A), (0, 0x45), (0, 0xB5), (0, 0)]
    out = []
    for _ in range(n_sets):
        r = rng.random()
        if r < 0.5:
            link = rng.choice([None, rng.randrange(256)])
            lane = rng.choice([None, rng.randrange(256)])
            s = m.ts_symbols(rng.randrange(1, 256), rng.random() < 0.5, link, lane,
                             rng.randrange(8))
            if rng.random() < 0.2:
                s[rng.randrange(6, 16)] = rng.choice(pool)
            if rng.random() < 0.1:
                s = s[:rng.randrange(1, 16)]
            if rng.random() < 0.1:
                s[6:] = [(0, s[6][1] ^ 0xFF)] * 10 if len(s) == 16 else s[6:]
            out += s
        elif r < 0.7:
            out += m.set_symbols(rng.choice(["skp", "eios", "fts"]))
            out += [SKP] * rng.randrange(3)
        else:
            out += [rng.choice(pool) for _ in range(rng.randrange(1, 6))]
    if len(out) % 2:
        out.append((0, 0))
    return out


def test_parser_matches_architecture_model():
    rng = random.Random(1)
    for _ in range(50):
        stream = random_stream(rng, 40)
        ref = Recorder()
        for k, b in stream:
            ref._rx_sym(k, b)
        mine = []
        for e in run_rx(stream):
            if e["ts_bad"]:
                mine.append(("ts_bad",))
            if e["ts"]:
                f = e["fields"]
                kind = ("TS2" if f["ts2"] else "TS1") + ("~" if f["inv"] else "")

                def num(x):
                    return None if x["pad"] else (x["byte"] if x["num"] else -1)

                mine.append(("ts", kind, num(f["link"]), num(f["lane"]), f["n_fts"], f["ctl"]))
            if e["skp"]:
                mine.append(("skp",))
            if e["eios"]:
                mine.append(("eios",))
        assert mine == ref.log


def test_skp_schedule():
    tx = m.Tx()
    fields = dict(ts2=False, link=None, lane=None, ctl=0)
    starts = []
    for cycle in range(30000):
        out = tx.step("ts", fields, skp_ok=True, count=False)
        if out["start"] and out["kind"] == "skp":
            starts.append(cycle)
    assert starts and starts[0] == 592  # 74 TS (1184 symbols), then the SKP
    # Each SKP waits for the next TS boundary (at most 8 words late), and the
    # remainder carries over, so the mean interval is exactly 1180 symbols.
    assert all(586 <= b - a <= 598 for a, b in zip(starts, starts[1:]))
    assert (starts[-1] - starts[0]) * 2 < 1180 * (len(starts) - 1) + 16
