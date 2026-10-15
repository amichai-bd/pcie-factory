"""Self-tests of the pcie_mac architecture model; no simulator needed.

Run: python3 -m pytest src/arch/mac (./build ci unit runs them as arch:mac).
"""

import importlib.util
import re
from pathlib import Path

import pytest

import pcie_mac_arch as m
import pcie_mac_sweep as sweep

HERE = Path(__file__).resolve().parent
WIKI = HERE.parents[2] / "wiki" / "src" / "arch" / "mac"
sweep.use(m)


def trained(role="downstream", **kw):
    p = m.make_pair(role, **kw)
    p.run_until(m.both_l0, 100_000)
    return p


# ------------------------------------------------------------ the HAS lists

def test_arc_list_matches_the_has_tables():
    rows = re.findall(r"^\| `([a-z0-9_]+__[a-z0-9_]+)` \| ([^|]+?) \| ([^|]+?) \|",
                      (WIKI / "LTSSM.md").read_text(), re.M)
    table = {name: (a, b) for name, a, b in rows}
    assert len(rows) == len(table), "duplicate arc names in LTSSM.md"
    assert table == m.ARCS


def test_state_codes_match_the_has_encoding_table():
    text = (WIKI / "LTSSM.md").read_text()
    rows = dict(re.findall(r"^\| ([A-Za-z][A-Za-z0-9. ]+?) \| `0x([0-9A-F]{2})` \|", text, re.M))
    assert {k: int(v, 16) for k, v in rows.items()} == m.CODES


def test_rtl_package_encoding_matches_the_model():
    pkg = (HERE.parents[1] / "rtl" / "mac" / "pcie_mac_pkg.sv").read_text()
    got = {k: int(v, 16) for k, v in re.findall(r"LTSSM_([A-Z0-9_]+)\s*=\s*6'h([0-9A-F]{2})", pkg)}
    want = {short.upper(): code for _, short, code in m.MAIN_STATES}
    assert got == want


def test_state_codes_fit_six_bits_and_l0_encodes_both_sub_states():
    assert m.CODES["Detect.Quiet"] == 0
    codes = list(m.CODES.values())
    assert len(set(codes)) == len(codes)
    l0 = {m.state_code(m.L0, t, r) for t in (m.TL0, m.TE, m.TI, m.TF)
          for r in (m.RL0, m.RE, m.RXI, m.RF)}
    assert l0 == set(range(0x20, 0x30))
    assert not l0 & (set(codes) - {0x20})
    assert all(0 <= c < 64 for c in codes)


def test_scrambler_known_answer():
    # Key bytes after a reset (scrambling 00h), from the published Gen1
    # scrambler sequence; independent of this model's code.
    assert m.keystream(16) == [0xFF, 0x17, 0xC0, 0x14, 0xB2, 0xE7, 0x02, 0x82,
                               0x72, 0x6E, 0x28, 0xA6, 0xBE, 0x6D, 0xBF, 0x8D]


def test_scrambler_rules():
    s = m.Scrambler()
    assert s.feed(1, 0xBC, True) == 0xBC and s.state == m.SEED   # COM resets
    assert s.feed(1, 0x1C, True) == 0x1C and s.state == m.SEED   # SKP holds
    assert s.feed(0, 0x00, False) == 0x00 and s.state != m.SEED  # TS data: advance only
    s2 = m.Scrambler()
    s2.feed(1, 0xBC, False)
    assert s2.feed(0, 0x00, True) == 0xFF                        # first key byte
    assert s2.feed(1, 0xFB, True) == 0xFB                        # K not scrambled


def test_timeouts_and_their_divisors():
    a = m.Mac(timeout_div=1024, err_timeout_div=1024)
    assert a.quiet_cycles == 1464 and a.dwell_cycles == 244
    assert a.err(m.T24MS) == m.ERR_TIMEOUT_FLOOR and a.err(m.T2MS) == m.ERR_TIMEOUT_FLOOR
    b = m.Mac(err_timeout_div=8)
    assert b.err(m.T24MS) == m.T24MS // 8 and b.err(m.T2MS) == 31_250
    assert m.Mac().err(m.T48MS) == m.T48MS
    assert m.ERR_TIMEOUT_FLOOR > 2 * m.POLLING_ACTIVE_MIN_TS1 * 8 // 2


@pytest.mark.parametrize("kw, msg", [
    ({"port_role": "x"}, "port_role"),
    ({"timeout_div": 0}, "timeout_div"),
    ({"err_timeout_div": 2048}, "err_timeout_div"),
    ({"n_fts": 0}, "n_fts"),
    ({"l0s_idle_cycles": 900}, "l0s_idle_cycles"),
    ({"skp_interval": 100}, "skp_interval"),
])
def test_bad_parameters_are_rejected(kw, msg):
    with pytest.raises(ValueError, match=msg):
        m.Mac(**kw)


# ----------------------------------------------------------------- training

@pytest.mark.parametrize("role", m.ROLES)
def test_trains_against_itself_to_l0_in_both_roles(role):
    p = trained(role)
    a, b = p.macs
    path = [t for _, f, t in a.arcs if t not in (m.TL0, m.RL0)]
    assert path == [m.DA, m.PA, m.PCFG, m.LWS, m.LWA, m.LNW, m.LNA, m.CC, m.CI, m.L0]
    assert a.link_up and b.link_up
    assert a.code == b.code == 0x20
    assert b.rx_link == 0 if b.role == "upstream" else a.rx_link == 0
    assert a.partner_n_fts == b.n_fts


def test_polling_active_sends_at_least_1024_ts1():
    p = m.make_pair()
    p.run_until(lambda q: q.macs[0].state == m.PCFG, 100_000)
    assert p.macs[0].runs == {"go": 0}       # fresh state
    t_pa = m.first_arc(p.macs[0], m.PA)
    assert p.now - t_pa >= m.POLLING_ACTIVE_MIN_TS1 * 8


def test_err_timeout_div_leaves_training_unchanged():
    slow = m.perf({"kind": "train", "timeout_div": 1024, "err_timeout_div": 1}, 1)
    fast = m.perf({"kind": "train", "timeout_div": 1024, "err_timeout_div": 1024}, 1)
    assert slow == fast


def test_inverted_lane_is_corrected_in_polling():
    p = m.make_pair()
    p.link.pcs[0].invert = True              # the wire into a is inverted
    p.run_until(m.both_l0, 100_000)
    assert p.macs[0].polarity and not p.macs[1].polarity


def test_loopback_master_sees_its_pattern_returned():
    p = trained()
    a, b = p.macs
    a.loopback_req = True
    p.run_until(lambda q: a.state == m.LBA and b.state == m.LBA, 20_000)
    p.run(3000)
    assert a.lb_errors == 0 and a.lb_master and not b.lb_master
    a.loopback_req = False
    p.run_until(lambda q: b.state == m.DQ, 20_000)


def test_link_up_holds_through_recovery_and_falls_in_detect():
    p = trained()
    a, b = p.macs
    a.retrain_req = True
    p.run_until(lambda q: a.state == m.RL, 1000)
    assert a.link_up
    p.run_until(m.both_l0, 10_000)
    b.inject.freeze = True
    a.retrain_req = True
    p.run_until(lambda q: a.state == m.DQ, 100_000)
    assert not a.link_up and a.code == 0


# ------------------------------------------------------------------- framing

def test_packets_flags_and_odd_alignment():
    p = trained()
    a, b = p.macs
    sent = [m.Packet(False, bytes(range(20))), m.Packet(True, bytes(6)),
            m.Packet(False, bytes(range(12)), nullify=True)]
    a.inject.skp_adjust = 1                  # later packets start in the high byte
    a.skp_credit = a.skp_interval
    for q in sent:
        a.tx_packets.append(m.Packet(q.dllp, q.data, q.nullify))
    p.run(400)
    assert b.rx_packets == sent and b.framing_errors == 0


def test_dllp_ended_by_edb_is_an_error_not_a_nullify():
    p = trained()
    a, b = p.macs
    a.tx_packets.append(m.Packet(True, bytes(6), nullify=True))
    p.run(100)
    assert b.rx_packets == [m.Packet(True, bytes(6), nullify=False, err=True)]


def test_malformed_packets_end_bad():
    p = trained()
    a, b = p.macs
    a.tx_packets.append(m.Packet(True, bytes(8)))          # DLLP of 8 bytes
    a.tx_packets.append(m.Packet(False, bytes(7)))         # odd length
    a.tx_packets.append(m.Packet(False, bytes(8)))
    p.run(100)
    assert [q.err for q in b.rx_packets] == [True, True, False]


def test_symbol_error_marks_the_packet_bad_and_counts():
    p = trained()
    a, b = p.macs
    a.tx_packets.append(m.Packet(False, bytes(range(40))))
    p.run(16)
    p.link.pcs[1].corrupt = 1
    p.run(200)
    assert b.rx_packets[0].err and b.rcv_errors >= 1


def test_leaving_l0_inside_a_packet_ends_it_with_edb():
    p = trained()
    a, b = p.macs
    a.tx_packets.append(m.Packet(False, bytes(400)))
    p.run(20)
    a.retrain_req = True
    p.step()
    p.run_until(m.both_l0, 10_000)
    assert a.tx_aborted == 1
    assert b.rx_packets[0].nullify or b.rx_packets[0].err


# ------------------------------------------------------------ power states

def test_l0s_both_directions_and_codes():
    p = trained()
    a, b = p.macs
    a.pm_state_req = b.pm_state_req = 1
    p.run_until(lambda q: a.tx_sub == m.TI and a.rx_sub == m.RXI, 5000)
    assert a.code == 0x20 | 2 << 2 | 2
    assert a.state == m.L0 and a.link_up
    b.tx_packets.append(m.Packet(False, bytes(8)))
    p.run_until(lambda q: a.rx_packets, 5000)
    assert a.rx_sub == m.RL0 and a.tx_sub == m.TI
    assert a.code == 0x20 | 2 << 2


def test_l1_waits_for_the_upstream_eios_in_the_downstream_role():
    p = trained()
    a, b = p.macs                            # a downstream
    a.pm_state_req = 2
    p.run(200)
    assert a.state == m.L0                   # directed, but no EIOS yet
    b.pm_state_req = 2
    p.run_until(lambda q: a.state == m.L1I and b.state == m.L1I, 1000)
    assert m.first_arc(b, m.L1E) < m.first_arc(a, m.L1E)


def test_hot_reset_follower_leaves_after_the_dwell():
    p = trained()
    a, b = p.macs
    a.hot_reset_req = True
    p.run_until(lambda q: b.state == m.HR, 10_000)
    assert not b.link_up
    a.hot_reset_req = False
    p.run_until(lambda q: b.state == m.DQ, 10_000)
    assert m.first_arc(b, m.DQ, 1) - m.first_arc(b, m.HR) >= b.dwell_cycles


# ------------------------------------------------------------- the partner

def test_budget_plugs_into_the_phy_model():
    spec = importlib.util.spec_from_file_location(
        "phy_model_for_test", HERE.parent / "phy" / "pcie_phy_arch.py")
    phy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(phy)
    stack = phy.Stack(mac=m.MacBudget())
    assert stack.tx_latency_cycles() <= 6                      # phy HAS, TX latency
    assert stack.rx_latency_cycles(12, True) <= 16             # phy HAS, RX latency
    up = phy.link_up(stack, phy.Stack(mac=m.MacBudget(), timeout_div=1))
    assert up["training_us_max"] <= 80


def test_perf_is_deterministic():
    w = {"kind": "traffic", "packets": 200, "mix": "random", "nullify_ratio": 0.1}
    assert m.perf(w, 3) == m.perf(w, 3)


@pytest.mark.parametrize("workload, msg", [
    ({}, "kind"),
    ({"kind": "train", "timeout_div": 0}, "timeout_div"),
    ({"kind": "traffic"}, "packets"),
    ({"kind": "traffic", "packets": 4, "mix": "x"}, "mix"),
    ({"kind": "traffic", "packets": 4, "tlp_bytes": 7}, "tlp_bytes"),
    ({"kind": "l1", "state": 1}, "state"),
    ({"kind": "l0s", "n_fts": 0}, "n_fts"),
    ({"kind": "retrain", "x": 1}, "unknown workload"),
])
def test_bad_workload_is_rejected(workload, msg):
    with pytest.raises(ValueError, match=msg):
        m.perf(workload, 1)
