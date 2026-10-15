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
                      (WIKI / "ARCS.md").read_text(), re.M)
    table = {name: (a, b) for name, a, b in rows}
    assert len(rows) == len(table), "duplicate arc names in ARCS.md"
    assert table == m.ARCS


def test_state_codes_match_the_has_encoding_table():
    text = (WIKI / "LTSSM.md").read_text()
    rows = dict(re.findall(r"^\| ([A-Za-z][A-Za-z0-9. ]+?) \| `0x([0-9A-F]{2})` \|", text, re.M))
    assert {k: int(v, 16) for k, v in rows.items()} == m.CODES


def test_rtl_package_encoding_matches_the_model():
    pkg = (HERE.parents[1] / "rtl" / "common" / "common_pkg.sv").read_text()
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


def test_rx_l0s_fts_timeout_is_clamped_for_small_n_fts():
    for n, want in ((1, 16), (9, 48), (10, 52), (32, 96)):
        mac = m.Mac(n_fts=n)
        mac.now = 0
        mac._set_rx(m.RF)
        assert mac.rx_fts_deadline == want


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
    assert sum(1 for t, *_ in p.macs[0].arcs) == sum(1 for t, *_ in p.macs[1].arcs)


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
    a.tx_packets.append(m.Packet(True, bytes(6), nullify=True))   # ignored for a DLLP
    p.run(100)
    assert b.rx_packets == [m.Packet(True, bytes(6))]
    d = (0, 0x11)
    for w in ((m.SDP, d), (d, d), (d, d), (d, m.EDB)):
        b.step(m.PipeRx(syms=w, valid=True, elec_idle=False, phy_status=False))
    got = b.rx_packets[-1]
    assert got.dllp and got.err and not got.nullify


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


def test_l1_needs_the_request_and_a_received_eios_in_both_roles():
    p = trained()
    a, b = p.macs                            # a downstream, b upstream
    a.pm_state_req = 2
    p.run(200)
    assert a.state == m.L0                   # directed, but no EIOS received
    b.pm_state_req = 2
    p.run_until(lambda q: b.pm_wait, 100)    # upstream sends its EIOS in L0
    assert b.state == m.L0 and b.code == 0x20
    p.run_until(lambda q: a.state == m.L1I and b.state == m.L1I, 1000)
    assert m.first_arc(a, m.L1E) < m.first_arc(b, m.L1E)


def test_upstream_does_not_enter_l1_when_the_downstream_retrains_instead():
    p = trained()
    a, b = p.macs                            # a downstream, b upstream
    b.pm_state_req = 2
    p.run_until(lambda q: b.pm_wait, 100)
    a.retrain_req = True                     # downstream answers with TS1, no EIOS
    p.step()
    p.run_until(lambda q: b.state == m.RL, 1000)
    b.pm_state_req = 0
    p.run_until(m.both_l0, 10_000)
    assert all(t not in (m.L1E, m.L1I) for _, _, t in b.arcs)


def test_packet_offered_while_waiting_for_l1_is_not_taken():
    p = trained()
    a, b = p.macs                            # a downstream, b upstream
    b.pm_state_req = 2
    p.run_until(lambda q: b.pm_wait, 100)
    b.tx_packets.append(m.Packet(False, bytes(range(16))))
    p.run(200)
    assert b.state == m.L0 and b.pm_wait and len(b.tx_packets) == 1
    assert not a.rx_packets
    a.pm_state_req = 2
    p.run_until(lambda q: a.state == m.L1I and b.state == m.L1I, 1000)
    assert len(b.tx_packets) == 1            # still queued through L1
    a.pm_state_req = b.pm_state_req = 0
    p.run_until(lambda q: a.rx_packets, 10_000)
    assert a.rx_packets == [m.Packet(False, bytes(range(16)))]


def test_rcvrcfg_leaves_for_configuration_only_after_16_ts2_sent():
    p = trained()
    a, b = p.macs
    a.retrain_req = True
    p.step()
    p.run_until(lambda q: b.state == m.RL, 1000)
    b.inject.freeze = True                   # TS1 with matching numbers
    p.run_until(lambda q: a.state == m.RC, 1000)
    b.inject.ts = {"kind": "TS1", "link": 3}
    p.run_until(lambda q: a.mism8, 1000)
    assert a.state == m.RC                   # 8 mismatching TS1 alone are not enough
    assert a.sent_after_mism < 16
    p.run_until(lambda q: a.state == m.LWS, 1000)
    assert a.arcs[-1][1:] == (m.RC, m.LWS)


def test_withdrawn_l1_request_returns_through_recovery():
    p = trained()
    a, b = p.macs
    b.pm_state_req = 2
    p.run_until(lambda q: b.pm_wait, 100)
    b.pm_state_req = 0
    p.run_until(lambda q: b.state == m.RL, 100)
    p.run_until(m.both_l0, 10_000)


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


# ------------------------------------------- rules aligned with the RTL (#146)

def _rx(*syms):
    return m.PipeRx(syms=syms, valid=True, elec_idle=False, phy_status=False)


def test_compliance_flag_on_the_first_word_of_each_pattern_only():
    p = m.make_pair()
    a, b = p.macs
    b.inject.mute = True
    p.run_until(lambda q: a.state == m.PC, 400_000)
    words = []
    step = a.step
    a.step = lambda rx: words.append(step(rx)) or words[-1]
    p.run(40)
    pattern = [w for w in words if not w.elec_idle]
    assert pattern, "no compliance words"
    flags = [w.compliance for w in pattern]
    firsts = [w.syms[1] == (0, m.D21_5) for w in pattern]
    assert flags == firsts and any(flags) and not all(flags)


def test_compliance_words_do_not_count_for_the_skp_schedule():
    p = m.make_pair()
    a, b = p.macs
    b.inject.mute = True
    p.run_until(lambda q: a.state == m.PC, 400_000)
    p.run(4)
    credit = a.skp_credit
    p.run(200)
    assert a.skp_credit == credit


def test_empty_packet_is_dropped_and_counted_without_resync():
    p = trained()
    a, b = p.macs
    errs, frames = b.rcv_errors, b.framing_errors
    b.step(_rx(m.STP, m.END))
    assert b.rx_packets == [] and b.framing_errors == frames + 1
    assert b.rcv_errors == errs + 1 and not b._resync


def test_leaving_rx_l0_ends_a_resync_and_cuts_silently():
    p = trained()
    a, b = p.macs
    b.step(_rx(m.END, m.PAD))                    # a framing error, then discarded
    assert b._resync
    b.step(_rx(m.COM, m.EIDL))                   # EIOS: Rx_L0s.Entry
    assert b.rx_sub == m.RE and not b._resync
    frames = b.framing_errors
    q = m.make_pair()
    q.run_until(m.both_l0, 100_000)
    c = q.macs[1]
    c.step(_rx(m.STP, m.END))                    # open, then end it empty in L0
    c._pkt = bytearray()                         # an empty packet open again
    c._set_rx(m.RE)                              # leaving Rx_L0 cuts it
    assert c._pkt is None and c.rx_packets == [] and frames == b.framing_errors


def test_upstream_learns_n_only_from_a_ts1_link_number():
    for kind, link, want in (("TS1", 7, 7), ("TS2", 7, None), ("TS1", -1, None)):
        u = m.Mac("upstream")
        u.rst = False
        u.state = m.LWS
        u._enter_state(m.LWS)
        u._on_ts(m.Ts(kind, link, None, 32, 0))
        assert u.rx_link == want, (kind, link)


def test_partner_n_fts_only_from_the_advancing_ts2():
    d = m.Mac("downstream")
    d.rst = False
    d.state = m.CC
    d._enter_state(m.CC)
    d._on_ts(m.Ts("TS2", 5, 0, 77, 0))              # link 5 is not N
    assert d.partner_n_fts == 255
    d._on_ts(m.Ts("TS2", 0, 0, 77, 0))
    assert d.partner_n_fts == 77


def test_rtl_shared_package_symbol_codes_match_the_model():
    pkg = (HERE.parents[1] / "rtl" / "common" / "common_pkg.sv").read_text()
    got = {k: int(v, 16) for k, v in re.findall(r"\b([A-Z][A-Z0-9_]+)\s*=\s*8'h([0-9A-F]{2})", pkg)}
    want = {"SYM_COM": m.COM[1], "SYM_STP": m.STP[1], "SYM_SDP": m.SDP[1], "SYM_END": m.END[1],
            "SYM_EDB": m.EDB[1], "SYM_PAD": m.PAD[1], "SYM_SKP": m.SKP[1], "SYM_FTS": m.FTS[1],
            "SYM_IDL": m.EIDL[1], "IDLE_DATA": 0, "RATE_GEN1": m.RATE_GEN1,
            "TS1_ID": m.TS1_ID, "TS2_ID": m.TS2_ID, "TS1_ID_INV": m.TS1_ID ^ 0xFF,
            "TS2_ID_INV": m.TS2_ID ^ 0xFF, "COMPL_D21_5": m.D21_5, "COMPL_D10_2": m.D10_2}
    assert got == want
