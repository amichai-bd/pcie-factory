"""Self-tests of the pcie_phy architecture model; no simulator needed.

Run: python3 -m pytest src/arch/phy (./build ci unit runs them as arch:<ip>).
"""

import random

import pytest

import pcie_phy_arch as m


def pkt(n, dllp=False, nullify=False, fill=0x5A):
    return m.Packet(dllp, bytes([fill] * n), nullify)


def test_framing_wraps_each_packet_and_keeps_even_alignment():
    syms, starts = m.frame([pkt(18), pkt(6, dllp=True), pkt(20, nullify=True)])
    assert syms[starts[0]] == m.STP and syms[starts[0] + 19] == m.END
    assert syms[starts[1]] == m.SDP and syms[starts[1] + 7] == m.END
    assert syms[starts[2]] == m.STP and syms[starts[2] + 21] == m.EDB
    assert all(s % 2 == 0 for s in starts)
    assert starts == [0, 20, 28]


def test_deframe_inverts_frame_and_flags_nullified():
    pkts = m.make_packets(random.Random(1), 200, "random", 274, 0.2)
    syms, _ = m.frame(pkts)
    got, starts, errors = m.deframe(syms)
    assert got == pkts and errors == 0 and len(starts) == len(pkts)
    assert any(p.nullify for p in got)


def test_deframe_counts_framing_errors_and_drops_the_bad_packet():
    syms, _ = m.frame([pkt(8), pkt(8)])
    bad = list(syms)
    bad[3] = m.COM                 # K symbol inside the first packet
    bad.insert(10, (0, 0x77))      # data outside a packet
    got, _, errors = m.deframe(bad)
    assert errors == 2 and got == [pkt(8)]


def test_skp_ordered_sets_are_scheduled_and_deferred_to_packet_ends():
    syms, _ = m.frame([pkt(4122)] * 3, skp_interval=1180)
    coms = [i for i, s in enumerate(syms) if s == m.COM]
    # A 4124-symbol packet holds three intervals' worth, sent back to back.
    assert len(coms) >= 9
    assert all(syms[i + 1:i + 4] == [m.SKP] * 3 for i in coms)
    short, _ = m.frame([pkt(18)] * 400, skp_interval=1180)
    coms = [i for i, s in enumerate(short) if s == m.COM]
    assert max(b - a for a, b in zip(coms, coms[1:])) <= 1180 + 20 + 4


@pytest.mark.parametrize("ppm", [-600, -300, 0, 300, 600])
def test_elastic_buffer_tracks_ppm_without_loss(ppm):
    pkts = m.make_packets(random.Random(2), 500, "random", 274, 0.0)
    syms, _ = m.frame(pkts)
    out, fills, st = m.elastic(syms, ppm, depth=16, nominal=8)
    got, _, errors = m.deframe(out)
    assert got == pkts and errors == 0 and st["overflow"] == 0
    assert len(out) - len(syms) == st["added"] - st["removed"]
    assert 6 <= st["fill_min"] and st["fill_max"] <= 10
    assert len(fills) == len(out)
    if ppm > 0:
        assert st["removed"] > 0 and st["added"] == 0
    if ppm < 0:
        assert st["added"] > 0 and st["removed"] == 0


def test_throughput_is_the_framing_and_skp_limit():
    r = m.perf({"kind": "traffic", "packets": 2000, "tlp_bytes": 274}, 1)
    limit = 2.0 * 274 / 276 * 1180 / 1184
    assert abs(r["throughput_bytes_per_cycle"] - limit) < 0.002
    d = m.perf({"kind": "traffic", "packets": 4000, "mix": "dllp"}, 1)
    assert abs(d["throughput_bytes_per_cycle"] - 2.0 * 6 / 8 * 1180 / 1184) < 0.002


def test_latency_is_the_sum_of_the_budgets():
    s = m.Stack(m.MacBudget(tx_latency_cycles=4, rx_latency_cycles=2),
                m.PcsBudget(tx_latency_cycles=1, rx_fixed_cycles=5))
    assert s.tx_latency_cycles() == 5
    assert s.rx_latency_cycles(8, False) == 5 + 4 + 2
    assert s.rx_latency_cycles(9, True) == 5 + 5 + 2 + 1


def test_link_up_honours_detect_quiet_and_polling_minimum():
    r = m.link_up(m.Stack(), m.Stack())
    assert r["link_up_cycles_max"] >= m.DETECT_QUIET_CYCLES
    ts1_time = m.cycles_to_us(m.POLLING_ACTIVE_MIN_TS1 * m.SLOT_CYCLES)
    assert r["training_us_min"] >= ts1_time
    assert r["link_up_us_min"] == r["link_up_us_max"]   # symmetric


def test_timeout_div_shortens_only_the_timeouts():
    slow = m.link_up(m.Stack(timeout_div=1), m.Stack(timeout_div=1))
    fast = m.link_up(m.Stack(timeout_div=1024), m.Stack(timeout_div=1024))
    assert fast["link_up_us_max"] < slow["link_up_us_max"] / 100
    assert abs(fast["training_us_max"] - slow["training_us_max"]) < 2


def test_partner_ts1_ends_detect_quiet_early():
    r = m.link_up(m.Stack(), m.Stack(), skew_cycles=500_000)
    assert r["link_up_us_min"] < r["link_up_us_max"] - 500


def test_detection_before_the_partner_wakes_retries():
    base = m.link_up(m.Stack(timeout_div=1024), m.Stack(timeout_div=1024))
    late = m.link_up(m.Stack(timeout_div=1024), m.Stack(timeout_div=1024),
                     skew_cycles=5000)
    assert late["detect_us_max"] > base["detect_us_max"]


def test_perf_is_deterministic_per_seed():
    w = {"kind": "traffic", "packets": 300, "mix": "random", "ppm": 100}
    assert m.perf(w, 3) == m.perf(w, 3)
    assert m.perf(w, 3)["payload_bytes"] != m.perf(w, 4)["payload_bytes"]


@pytest.mark.parametrize("workload, msg", [
    ({}, "kind"),
    ({"kind": "traffic"}, "packets"),
    ({"kind": "traffic", "packets": 4, "mix": "x"}, "mix"),
    ({"kind": "traffic", "packets": 4, "tlp_bytes": 7}, "tlp_bytes"),
    ({"kind": "traffic", "packets": 4, "ppm": 700}, "ppm"),
    ({"kind": "traffic", "packets": 4, "skp_interval": 100}, "skp_interval"),
    ({"kind": "link_up", "timeout_div": 0}, "timeout_div"),
    ({"kind": "link_up", "packets": 4}, "unknown workload"),
])
def test_bad_workload_is_rejected(workload, msg):
    with pytest.raises(ValueError, match=msg):
        m.perf(workload, 1)


def test_bad_packets_are_rejected():
    with pytest.raises(ValueError, match="even"):
        m.frame([pkt(7)])
    with pytest.raises(ValueError, match="DLLP"):
        m.frame([pkt(8, dllp=True)])
