"""Unit tests of the throughput line attribution and the injector's line
tally (wiki/src/dv/link/DV_PLAN.md, throughput). No simulator.

The reference transmitter of the injector unit tests builds a symbol
stream with a known content; the injector records the sent view and the
line runs, and ``attribute`` must account for every symbol exactly.

Run: python3 -m pytest src/dv/link/models
"""

import random
import sys
from pathlib import Path

import pytest

import pcie_link_injector_model as inj
import pcie_link_throughput_model as thr
from test_pcie_link_injector_model import Tx, dllp_body, tlp_body

sys.path.append(str(Path(__file__).resolve().parents[2] / "dll" / "models"))
from pcie_dll_partner_passive import ViewPacket  # noqa: E402

P = 10                                # ps per word in these tests


def mwr(seq, dw, tag=0):
    """A framed 3-DW memory write of ``dw`` payload DWs."""
    hdr = bytes([0x40, 0, 0, dw, tag >> 8, tag & 0xFF]) + bytes(6)
    return tlp_body(seq, 0, 0)[:2] + hdr + bytes(4 * dw) + bytes(4)


def capture(tx, ei_words=0):
    sent = []
    x = inj.Injector(random.Random(0), 4, on_sent=lambda p: sent.append(ViewPacket(*p)))
    x.line_runs = []
    words = tx.words()
    for i, w in enumerate(words):
        x.step(w, False, i * P)
    for i in range(len(words), len(words) + ei_words):
        x.step(0, True, i * P)
    return sent, x.line_runs, len(words) + ei_words


def stream():
    t = Tx().idle(4)
    for i in range(3):
        t.tlp(mwr(i, 8, tag=i)).idle(2).dllp(dllp_body(inj.ACK, i)).idle(2)
    t.skp().idle(2)
    t.tlp(mwr(3, 32, tag=3)).dllp(dllp_body(inj.NAK, 2)).dllp(dllp_body(0x80))
    t.tlp(mwr(1, 8, tag=1)).idle(2)                 # a replay of seq 1
    t.dllp(dllp_body(0x90)).dllp(dllp_body(0xA0)).dllp(dllp_body(0x40)).idle(4)
    return t


def test_every_symbol_attributed():
    sent, runs, n = capture(stream(), ei_words=3)
    att = thr.attribute(sent, runs, 0, (n - 1) * P, P)
    c = att["counts"]
    assert att["window"] == 2 * n and sum(c.values()) == att["window"]
    assert c["payload"] == 4 * (8 * 3 + 32) and c["tlp_header"] == 12 * 4
    assert c["tlp_framing"] == 8 * 4 and att["tlps"] == 4
    assert c["replay"] == len(mwr(1, 8)) + 2
    assert (c["ack"], c["nak"], c["updatefc_p"], c["updatefc_np"], c["updatefc_cpl"],
            c["initfc"]) == (24, 8, 8, 8, 8, 8)
    # The SKP ordered set, plus the COM the reference transmitter starts with.
    assert c["skp"] == 4 + 1 and c["ei"] == 6
    assert c["idle"] == 1 + 4 + 3 * 4 + 2 + 2 + 4


def test_window_and_straddle():
    sent, runs, n = capture(stream())
    first, last = sent[0], sent[2]                  # TLP 0 to TLP 1 (Ack 0 between)
    att = thr.attribute(sent, runs, first.start, last.end, P)
    assert sum(att["counts"].values()) == att["window"]
    assert att["tlps"] == 2 and att["counts"]["ack"] == 8 and att["counts"]["idle"] == 4
    # A window that cuts the first TLP: its words inside only.
    att = thr.attribute(sent, runs, first.start + 2 * P, last.end, P)
    assert sum(att["counts"].values()) == att["window"] and att["tlps"] == 1


def test_bad_and_empty():
    with pytest.raises(ValueError):
        thr.attribute([], [], 0, 10, P)
    bad = ViewPacket(0, 3 * P, False, bytes(6), True)
    assert thr.classify(bad, None)[0] == {"bad": 8}
    assert thr.ticks(0, 9, 0, P) == 1 and thr.ticks(5, 4, 0, P) == 0
    assert thr.summary({"counts": {"payload": 1, "idle": 0}, "window": 4}) == {"payload": 0.25}


def test_tally_off_by_default():
    x = inj.Injector(random.Random(0))
    for i, w in enumerate(stream().words()):
        x.step(w, False, i)
    assert x.line_runs is None
