"""Self-tests of the pcie_link architecture model; no simulator needed.

Run: python3 -m pytest src/arch/link (./build ci unit runs them as arch:<ip>).

The model composes the dll architecture model with the line of two phy
stacks; these tests check what the composition adds (the SKP schedule, the
line accounting) and that the HAS throughput derivation agrees with it.
"""

import random

import pytest

import pcie_link_arch as m

dll = m.dll


def test_line_symbols():
    """A 128-byte memory write (3-DW header) is 148 symbols on the line, a
    DLLP 8: framing, sequence number and LCRC as the HAS counts them."""
    assert m.tlp_line_symbols(12 + 128) == 148
    assert m.DLLP_LINE_SYMBOLS == 8


@pytest.mark.parametrize("both", [False, True])
def test_derivation_accounts_for_the_whole_line(both):
    f = m.derived_fractions(140, 128, both)
    total = f["tlp_line_fraction"] + f["dllp_line_fraction"] + f["skp_line_fraction"]
    assert total == pytest.approx(1.0)


@pytest.mark.parametrize("both,tlp,pay", [(False, 0.9908, 0.8569), (True, 0.8959, 0.7748)])
def test_derivation_values(both, tlp, pay):
    """The numbers the HAS states (Throughput)."""
    f = m.derived_fractions(140, 128, both)
    assert f["tlp_line_fraction"] == pytest.approx(tlp, abs=5e-5)
    assert f["payload_line_fraction"] == pytest.approx(pay, abs=5e-5)


@pytest.mark.parametrize("both,payload", [(False, 128), (True, 128), (False, 64)])
def test_model_matches_derivation(both, payload):
    """The cycle model and the closed form agree within 0.2% of the line:
    the derivation leaves out only the start and the drain."""
    met = m.perf({"kind": "traffic", "tlps": 1500, "mix": "mwr", "payload": payload,
                  "bidirectional": both}, 7)
    f = m.derived_fractions(12 + payload, payload, both)
    for k in ("tlp_line_fraction", "payload_line_fraction", "skp_line_fraction"):
        assert met[k] == pytest.approx(f[k], abs=0.002), k
    assert met["acks_per_tlp"] == pytest.approx(1.0, abs=0.01)


def run_line(cycles=4000, traffic=True):
    rng = random.Random(1)
    a, b = dll.Dll("a"), dll.Dll("b")
    pair = m.LinkPair(a, b, m.Line(rng))
    pair.trace = []
    tlps = dll.make_traffic(rng, 200, "mixed", -1) if traffic else []
    i = 0
    while pair.now < cycles:
        if i < len(tlps) and a.offer(tlps[i], pair.now):
            i += 1
        pair.step()
    return pair


def test_skp_interval_on_an_idle_line():
    """With no TLPs, way 0 still sends a 2-cycle SKP ordered set every 590
    cycles (1180 symbol times)."""
    pair = run_line(cycles=5902, traffic=False)       # SKPs fall due at 590, 1180, ... 5900
    starts = [t for t, o in enumerate(pair.trace)
              if o == "skp" and (t == 0 or pair.trace[t - 1] != "skp")]
    assert len(starts) == 10
    assert all(pair.trace[t + 1] == "skp" for t in starts)


def test_skp_never_splits_a_packet():
    """Each packet's cycles on the line are contiguous: a SKP that falls due
    inside a packet waits for its end, and is sent before the next one."""
    pair = run_line()
    seen, prev = set(), None
    for o in pair.trace:
        if o is not None and o != "skp" and o is not prev:
            assert id(o) not in seen, "a packet resumed after another use of the line"
            seen.add(id(o))
        prev = o
    assert pair.busy["skp"][0] >= 2 * (4000 // 590)


def test_skp_interval_range():
    with pytest.raises(ValueError):
        m.Line(random.Random(0), skp_interval=1179)
    with pytest.raises(ValueError):
        m.Line(random.Random(0), skp_interval=1539)


def test_skp_interval_changes_the_overhead():
    """The least frequent SKP (1538 symbol times) leaves more of the line to
    TLPs than the most frequent (1180)."""
    w = {"kind": "traffic", "tlps": 800, "mix": "mwr", "payload": 128}
    lo = m.perf(dict(w, skp_interval=1180), 1)
    hi = m.perf(dict(w, skp_interval=1538), 1)
    assert hi["skp_line_fraction"] < lo["skp_line_fraction"]
    assert hi["tlp_line_fraction"] > lo["tlp_line_fraction"]


def test_errors_recover_without_loss():
    e = {"tlp_corrupt": 0.03, "tlp_drop": 0.02, "dllp_corrupt": 0.03, "dllp_drop": 0.02}
    met = m.perf({"kind": "traffic", "tlps": 400, "mix": "mixed", "payload": -1,
                  "bidirectional": True, "errors": e}, 11)
    assert met["replays"] >= 1
    assert met["tlps_lost"] == met["tlps_duplicated"] == met["tlps_out_of_order"] == 0
    assert met["err_dl_protocol"] == met["err_rx_overflow"] == 0


def test_deterministic():
    w = {"kind": "traffic", "tlps": 200, "mix": "mixed", "payload": -1, "bidirectional": True,
         "errors": {"tlp_corrupt": 0.05}}
    assert m.perf(dict(w), 3) == m.perf(dict(w), 3)


def test_bad_workload():
    with pytest.raises(ValueError):
        m.perf({"kind": "nope"}, 0)
    with pytest.raises(ValueError):
        m.perf({"kind": "traffic", "tlps": 0}, 0)
