"""Self-tests of the pcie_top architecture model; no simulator needed.

Run: python3 -m pytest src/arch/top (./build ci unit runs them as arch:<ip>).
"""

import pytest

import pcie_top_arch as m


def test_zero_stream_is_the_published_keystream():
    assert m.scramble([0x00] * 8) == [0xFF, 0x17, 0xC0, 0x14, 0xB2, 0xE7, 0x02, 0x82]


def test_scrambling_is_its_own_inverse():
    data = [0x00, 0x5A, 0xA5, 0xFF, 0x12, 0x34, 0x80, 0x01]
    assert m.scramble(m.scramble(data)) == data


def test_bubble_holds_key_and_reseed_applies_from_the_next_byte():
    s = m.Scrambler()
    assert s.cycle(valid=1, data=0x00) == (1, 0xFF)
    assert s.cycle() == (0, 0)                                # bubble: key held
    assert s.cycle(valid=1, data=0x00, reseed=1) == (1, 0x17)  # current key
    assert s.cycle(valid=1, data=0x00) == (1, 0xFF)            # seed key


def test_reset_clears_valid_and_reseeds():
    s = m.Scrambler()
    s.cycle(valid=1, data=0x00)
    assert s.cycle(valid=1, data=0x00, rst=1) == (0, 0)
    assert s.cycle(valid=1, data=0x00) == (1, 0xFF)


@pytest.mark.parametrize("latency", [1, 3])
def test_latency_is_the_pipeline_depth(latency):
    s = m.Scrambler(latency)
    outs = [s.cycle(valid=1, data=0x00) for _ in range(4)]
    assert [v for v, _ in outs] == [0] * (latency - 1) + [1] * (5 - latency)


def test_back_to_back_meets_the_has_targets():
    r = m.perf({"bytes": 256}, seed=1)
    assert r["throughput_bytes_per_cycle"] == 1.0
    assert r["latency_cycles_min"] == r["latency_cycles_max"] == m.LATENCY_CYCLES


def test_bubbles_and_reseeds_never_stall():
    r = m.perf({"bytes": 512, "valid_ratio": 0.3, "reseed_every": 7}, seed=5)
    assert r["throughput_ratio"] == 1.0 and r["latency_cycles_max"] == 1
    assert r["reseeds"] == 512 // 7
    assert 0.2 < r["offered_bytes_per_cycle"] < 0.4


def test_perf_is_deterministic_per_seed():
    w = {"bytes": 300, "valid_ratio": 0.5}
    assert m.perf(w, 3) == m.perf(w, 3)
    assert m.perf(w, 3)["cycles"] != m.perf(w, 4)["cycles"]


@pytest.mark.parametrize("workload, msg", [
    ({}, "bytes"),
    ({"bytes": 0}, "bytes"),
    ({"bytes": 8, "valid_ratio": 0}, "valid_ratio"),
    ({"bytes": 8, "valid_ratio": 1.5}, "valid_ratio"),
    ({"bytes": 8, "reseed_every": -1}, "reseed_every"),
    ({"bytes": 8, "burst": 2}, "unknown workload"),
])
def test_bad_workload_is_rejected(workload, msg):
    with pytest.raises(ValueError, match=msg):
        m.perf(workload, 1)
