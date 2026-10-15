"""Self-tests of the pcie_pcs architecture model; no simulator needed.

Run: python3 -m pytest src/arch/pcs (./build ci unit runs them as arch:<ip>).
"""

import importlib.util
import itertools
import pathlib
import random

import pytest

import pcie_pcs_8b10b as c
import pcie_pcs_arch as m


def line(code):
    """A 10-bit code in line order (bit a first) as a string."""
    return "".join(str((code >> i) & 1) for i in range(10))


def from_line(s):
    return sum(int(b) << i for i, b in enumerate(s))


ALL = [(False, b) for b in range(256)] + [(True, b) for b in c.VALID_K]


# ------------------------------------------------------------------ 8b/10b

@pytest.mark.parametrize("name,byte,k,minus,plus", [
    # Widely published anchor values; the rest follows from the rules.
    ("K28.5", 0xBC, True, "0011111010", "1100000101"),
    ("K28.0", 0x1C, True, "0011110100", "1100001011"),
    ("D21.5", 0xB5, False, "1010101010", "1010101010"),
    ("D10.2", 0x4A, False, "0101010101", "0101010101"),
    ("D0.0", 0x00, False, "1001110100", "0110001011"),
])
def test_anchor_codes(name, byte, k, minus, plus):
    assert line(c.encode(byte, k, -1)[0]) == minus, name
    assert line(c.encode(byte, k, 1)[0]) == plus, name


def test_every_symbol_round_trips_at_both_disparities():
    for (k, b), rd in itertools.product(ALL, (-1, 1)):
        code, rd_out = c.encode(b, k, rd)
        assert c.decode(code, rd) == (b, k, rd_out, False, False)
        assert c.decode(code, None)[:2] == (b, k)


def test_codes_are_distinct_per_disparity_and_balanced():
    for rd in (-1, 1):
        codes = [c.encode(b, k, rd)[0] for k, b in ALL]
        assert len(set(codes)) == len(codes)
    for (k, b), rd in itertools.product(ALL, (-1, 1)):
        code, rd_out = c.encode(b, k, rd)
        ones = bin(code).count("1")
        assert ones in (4, 5, 6)
        assert ones == 5 or (ones == 6) == (rd < 0)
        assert ones != 5 or rd_out == rd   # a balanced code keeps RD
        assert ones == 5 or rd_out == -rd  # an unbalanced code flips it


def test_alternate_seven_only_where_p7_would_run_to_five():
    a7 = {(x, rd) for x in range(32) for rd in (-1, 1)
          if line(c.encode((7 << 5) | x, False, rd)[0])[6:] in ("0111", "1000")}
    assert a7 == {(17, -1), (18, -1), (20, -1), (11, 1), (13, 1), (14, 1)}


def test_decode_error_classes():
    rd = -1
    d5, _ = c.encode(0x05, False, rd)            # D5.0 RD-
    plus_only, _ = c.encode(0x00, False, 1)      # D0.0 is valid only in RD+
    assert c.decode(plus_only, rd)[3:] == (False, True)        # disparity error
    bad = from_line("1111100000")                             # in no column
    b, k, _, ce, de = c.decode(bad, rd)
    assert (b, k, ce, de) == (c.K_EDB, True, True, False)      # code error -> EDB
    assert c.decode(d5, rd)[3:] == (False, False)


def test_commas_only_in_k28_1_5_7_and_polarity_blind_matches():
    with_comma = {(k, b) for k, b in ALL for rd in (-1, 1) if c.has_comma(c.encode(b, k, rd)[0])}
    assert with_comma == {(True, 0x3C), (True, 0xBC), (True, 0xFC)}
    for byte in (c.K_COM, c.K_SKP):
        for rd in (-1, 1):
            code = c.encode(byte, True, rd)[0]
            assert c.raw_is(code, byte) and c.raw_is(code ^ 0x3FF, byte)
    assert not c.raw_is(c.encode(c.K_COM, True, -1)[0], c.K_SKP)


def test_skp_is_neutral_and_its_columns_are_complements():
    for rd in (-1, 1):
        assert c.encode(c.K_SKP, True, rd)[1] == rd
    m_, p_ = (c.encode(c.K_SKP, True, rd)[0] for rd in (-1, 1))
    assert m_ ^ 0x3FF == p_


def test_single_bit_error_is_always_detected_within_the_stream():
    rng = random.Random(7)
    syms = [(0, rng.randrange(256)) for _ in range(400)]
    codes, _ = m.tx_encode(syms)
    for _ in range(200):
        i, bit = rng.randrange(1, 300), rng.randrange(10)
        rx = list(codes)
        rx[i] ^= 1 << bit
        rd, seen = -1, False
        for code in rx:
            *_, ce, de = c.decode(code, rd)
            rd = c.rd_after(code, rd)
            seen |= ce or de
        assert seen, (i, bit)


def test_compliance_forces_negative_disparity():
    syms = [(1, c.K_COM)] * 3
    codes, _ = m.tx_encode(syms, compliance={0, 1, 2}, rd=1)
    assert all(code == c.encode(c.K_COM, True, -1)[0] for code in codes)
    assert m.tx_encode([(1, 0x00)])[0] == [c.encode(c.K_EDB, True, -1)[0]]


# ---------------------------------------------------------- receive path

def run(ppm, seed=1, **kw):
    rng = random.Random(seed)
    syms, idles = m.make_stream(rng, kw.get("packets", 150), kw.get("mix", "random"),
                                kw.get("tlp_bytes", 274), kw.get("skp", 1180),
                                kw.get("gaps", 0), kw.get("idle", 0))
    codes, _ = m.tx_encode(syms)
    ln = m.channel(rng, codes, idles, kw.get("slips", 0), kw.get("errors", 0))
    return syms, ln, m.receive(ln, syms, ppm, seed)


@pytest.mark.parametrize("ppm", [-600, 0, 600])
def test_elastic_buffer_is_lossless_and_corrects_toward_the_offset(ppm):
    syms, _, st = run(ppm)
    assert st["overflow"] == st["underflow"] == st["wrong"] == 0
    assert 4 <= st["fill_min"] and st["fill_max"] <= 12     # HAS Targets
    if ppm > 0:
        assert st["skp_removed"] > 0 and st["skp_added"] <= 1
    if ppm < 0:
        assert st["skp_added"] > 0 and st["skp_removed"] == 0
    d = st["delivered_txpos"]
    assert d == sorted(d) and len(set(d)) == len(d)


def test_bit_slip_requests_relock_and_lock_returns():
    _, ln, st = run(300, slips=2, packets=300)
    assert len(ln.slips) == 2 and st["relocks"] == 2
    assert st["locks"] == 3


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_relock_rearms_in_comma_dense_traffic(seed):
    """A relock in training sets can retake lock in the same cycle, so
    RxValid need not fall; the lock generation must still re-arm it."""
    rng = random.Random(seed)
    syms, idles = m.make_stream(rng, 2, "dllp", 6, 1180, 0, 0, lead_ts=3000)
    ln = m.channel(rng, m.tx_encode(syms)[0], idles, 12, 0)
    st = m.receive(ln, syms, 300, seed)
    slips = [(b // 20) * st["t_rx"] for b in ln.slips] + [1e30]
    for a, b in zip(slips, slips[1:]):
        assert any(a <= r < b for r in st["relock_req"]), "slip without a relock"
    assert len(st["gen_starts"]) == len(ln.slips) + 1


def test_isolated_errors_never_cost_lock():
    _, ln, st = run(-300, errors=5, packets=300)
    assert len(ln.errors) == 5 and st["relocks"] == 0
    assert st["code_errors"] + st["disp_errors"] >= 5


def test_status_precedence():
    assert m.max_status(1, 4) == 4 and m.max_status(7, 5) == 5
    assert m.max_status(2, 1) == 1 and m.max_status(0, 2) == 2


# ----------------------------------------------------------- PIPE control

def test_pipe_handshake_timing():
    for rxdet, status in ((1, 3), (0, 0)):
        r = m.pipe_script(rxdet)
        assert r["phy_status_after_reset_cycles"] == m.PHY_STATUS_RESET_CYCLES
        assert r["rx_detect_cycles"] == m.RX_DETECT_CYCLES
        assert r["rx_detect_status"] == status
        assert r["power_ack_cycles_max"] == m.POWER_ACK_CYCLES
        assert r["pulses"] == r["changes"] + 1


def test_transmitter_idle_outside_p0_and_loopback_in_p0():
    pc = m.PipeControl()
    for _ in range(m.PHY_STATUS_RESET_CYCLES + 1):
        pc.step(0, 1, 0, 0, 1, 0)
    assert pc.step(0, 1, 0, 1, 0, 0)["tx_elec_idle"] == 0      # loopback in P0
    assert pc.step(0, 1, 1, 0, 0, 0)["tx_elec_idle"] == 1      # P0s
    assert pc.step(0, 0, 0, 0, 0, 0)["phy_status"] == 1        # Reset# low


# ---------------------------------------------------- phy budget and perf

def test_budget_plugs_into_the_phy_model_and_meets_its_targets():
    path = pathlib.Path(__file__).resolve().parents[1] / "phy" / "pcie_phy_arch.py"
    spec = importlib.util.spec_from_file_location("phy_arch_for_test", path)
    phy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(phy)
    stack = phy.Stack(pcs=m.pcs_budget())
    pkts = phy.make_packets(random.Random(3), 300, "tlp", 274, 0.0)
    r = phy.traffic(stack, pkts, 600)
    assert r["packets_lost"] == 0 and r["rx_latency_cycles_max"] <= 16
    assert r["tx_latency_cycles_max"] <= 6
    for name in vars(phy.PcsBudget()):
        assert hasattr(m.pcs_budget(), name)


def test_perf_rejects_bad_workloads():
    with pytest.raises(ValueError):
        m.perf({"kind": "nope"}, 0)
    with pytest.raises(ValueError):
        m.perf({"kind": "link", "ppm": 700}, 0)
    with pytest.raises(ValueError):
        m.perf({"kind": "link", "bogus": 1}, 0)
    with pytest.raises(ValueError):
        m.perf({"kind": "link", "polarity": 1}, 0)
