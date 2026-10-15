"""Unit tests of the pcs traffic kinds, and of the RTL package's elastic
buffer constants against the architecture model; no simulator needed.

Run: python3 -m pytest src/dv/pcs/models
"""

import random
import re
from pathlib import Path

import pytest

import pcie_pcs_traffic as m

PKG = Path(__file__).resolve().parents[3] / "rtl" / "pcs" / "pcie_pcs_pkg.sv"
COMMON = Path(__file__).resolve().parents[3] / "rtl" / "common" / "common_pkg.sv"
K_COM, K_SKP = m.code8b10b.K_COM, m.code8b10b.K_SKP


def _localparams(path: Path) -> dict[str, int]:
    out = {}
    text = path.read_text()
    for name, digits in re.findall(
            r"localparam\s+\w+(?:\s+unsigned)?\s+(\w+)\s*=\s*\d+'h([0-9A-Fa-f]+)", text):
        out[name] = int(digits, 16)
    for name, value in re.findall(r"localparam\s+int\s+unsigned\s+(\w+)\s*=\s*(\d+)\s*;", text):
        out[name] = int(value)
    return out


def test_rtl_codes_match_arch_code():
    """The package's raw COM and SKP codes are the arch model's K28.5 and
    K28.0 in both columns (RECEIVE.md: matching is polarity-blind)."""
    p = _localparams(PKG)
    assert {p["COM_RD_MINUS"], p["COM_RD_PLUS"]} == m.COM_CODES
    assert {p["SKP_RD_MINUS"], p["SKP_RD_PLUS"]} == m.SKP_CODES
    for c in m.COM_CODES | m.SKP_CODES:
        assert c ^ 0x3FF in m.COM_CODES | m.SKP_CODES   # inverted lane


def test_rtl_levels_match_arch():
    p = _localparams(PKG)
    assert 2 ** p["EB_ADDR_W"] == m.EB_WORDS
    assert (p["EB_NOMINAL"], p["EB_HIGH"], p["EB_LOW"], p["EB_HOLDOFF"]) == \
        (m.EB_NOMINAL, m.EB_HIGH, m.EB_LOW, m.EB_HOLDOFF)


def _enum(name: str) -> int:
    v = re.search(rf"\b{name}\s*=\s*\d+'[hd]([0-9A-Fa-f]+)", COMMON.read_text())
    assert v, name
    base = 16 if "'h" in v.group(0) else 10
    return int(v.group(1), base)


def test_shared_codes_match_the_pcs_pages():
    """The codes pcs takes from common_pkg: EDB (the arch model's), the
    RxStatus codes (RECEIVE.md, Decode and RxStatus) and PowerDown
    (PIPE_CONTROL.md, PowerDown)."""
    assert _enum("SYM_EDB") == m.code8b10b.K_EDB == 0xFE
    status = {"RX_OK": 0, "RX_SKP_ADDED": 1, "RX_SKP_REMOVED": 2, "RX_DETECTED": 3,
              "RX_CODE_ERR": 4, "RX_EB_OVERFLOW": 5, "RX_EB_UNDERFLOW": 6, "RX_DISP_ERR": 7}
    assert {k: _enum(k) for k in status} == status
    assert [_enum(k) for k in ("PD_P0", "PD_P0S", "PD_P1", "PD_P2")] == [0, 1, 2, 3]


def _sets(syms):
    """SKP counts of every SKP ordered set, in order."""
    out, i = [], 0
    while i < len(syms):
        if syms[i] == (1, K_COM) and i + 1 < len(syms) and syms[i + 1] == (1, K_SKP):
            n = 0
            while i + 1 + n < len(syms) and syms[i + 1 + n] == (1, K_SKP):
                n += 1
            out.append(n)
            i += 1 + n
        else:
            i += 1
    return out


@pytest.mark.parametrize("kind", m.KINDS)
def test_traffic_kinds(kind):
    syms = m.link_symbols(random.Random(3), kind, 4, 1180)
    assert syms[0] == (1, K_COM) and _sets(syms)


def test_random_and_dense_sets_hold_one_to_five_skp():
    for kind in ("random", "dense"):
        counts = set(_sets(m.link_symbols(random.Random(4), kind, 300, 60)))
        assert counts == {1, 2, 3, 4, 5}, (kind, counts)


def test_short_burst_sends_one_set_too_few():
    """After the short packet only one set goes out although the gap is
    almost two intervals long."""
    rng = random.Random(5)
    syms = m.packet_stream(rng, [2 * 1538 - 7], 3, 1538, lead_ts=0)
    runs, i = [], 0
    while i < len(syms):
        if syms[i] == (1, K_COM):
            n = 0
            while i < len(syms) and syms[i] == (1, K_COM):
                n += 1
                i += 4
            runs.append(n)
        i += 1
    assert 1 in runs


def test_plain_symbols_have_no_skp_after_the_training():
    syms = m.plain_symbols(random.Random(6), 500)
    assert (1, K_SKP) not in syms[16 * 8:] and len(syms) == 16 * 8 + 500
