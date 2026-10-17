"""Self-tests of the pcie_core placeholder architecture model; no
simulator needed.

Run: python3 -m pytest src/arch/core (./build ci unit runs them as arch:<ip>).
"""

import pytest

import pcie_core_arch as m


def test_stream_cycles_of_a_128_byte_write():
    """35 beats on the dll stream, 32 on the application's, 74 cycles on
    the line (HAS, Targets)."""
    c = m.stream_cycles(128)
    assert c["dll_stream_cycles_per_tlp"] == 35
    assert c["app_stream_cycles_per_tlp"] == 32
    assert c["line_cycles_per_tlp"] == 74


@pytest.mark.parametrize("both", [False, True])
def test_core_reaches_the_link_rate(both):
    """Neither 32-bit stream limits a 128-byte write: core keeps the whole
    link payload rate, which the link model derives."""
    d = m.derived(128, both)
    assert d["fraction_of_link"] == 1.0
    assert d["payload_line_fraction"] == pytest.approx(
        m.link.derived_fractions(140, 128, both)["payload_line_fraction"])


@pytest.mark.parametrize("both,floor", [(False, 0.833), (True, 0.745)])
def test_targets(both, floor):
    """The HAS targets: 0.98 of the link targets (0.85 and 0.76)."""
    d = m.perf({"kind": "derived", "payload": 128, "bidirectional": both}, 1)
    assert d["fraction_of_link"] >= 0.98
    assert d["payload_line_fraction"] >= floor


def test_a_slow_stream_would_limit_the_rate(monkeypatch):
    """The limit applies: with one-byte beats the dll stream needs 140
    cycles per write, so core gets 74/140 of the link rate."""
    monkeypatch.setattr(m, "DW_BYTES", 1)
    d = m.derived(128, False)
    assert d["fraction_of_link"] == pytest.approx(74 / 140)


@pytest.mark.parametrize("w", [
    {"kind": "traffic"},
    {"kind": "derived", "payload": 130},
    {"kind": "derived", "payload": 256},
    {"kind": "derived", "payload": 0},
    {"kind": "derived", "bidirectional": 1},
])
def test_bad_workloads_raise(w):
    with pytest.raises(ValueError):
        m.perf(w, 1)
