"""Simulator-free tests of the real MAC harness's transition predicates.

Kept beside its model unit tests so the affected-IP CI unit suite runs it.
The history scan is an independent oracle for the indexed predicate.
"""

import sys
from pathlib import Path

import pytest

_MAC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_MAC.parents[2] / "tools"))
sys.path.insert(0, str(_MAC))
from tb.harness import Dut, Harness, arch  # noqa: E402


@pytest.fixture
def h():
    # Exercise actual observation/query methods without constructing simulator
    # clock triggers or pin handles. These are their ordinary Python inputs.
    h = Harness.__new__(Harness)
    h.a = Dut()
    h.now = h.t0 = 0
    h.role = "downstream"
    h.rst_sampled = False
    h.req = {"hot_reset_req_i": 0, "disable_req_i": 0, "loopback_req_i": 0}
    return h


def observe(h, cycle, code, reset=False):
    assert cycle >= h.now
    h.now, h.rst_sampled = cycle, reset
    h._observe({"ltssm_state_o": code, "link_up_o": int(code & 0x30 == 0x20)})


def scan(h, arc, cutoff):
    return any((a, b) == arc and cycle >= cutoff for cycle, a, b in h.a.arcs)


def test_never_seen_inclusive_boundary_and_repeated_arc(h):
    arc = (arch.DQ, arch.DA)
    assert not h.took(*arc, after=-100)(h)
    observe(h, 7, arch.CODES[arch.DA])
    assert h.took(*arc, after=7)(h)
    assert not h.took(*arc, after=8)(h)
    observe(h, 8, arch.CODES[arch.DQ], reset=True)
    observe(h, 11, arch.CODES[arch.DA])
    assert h.took(*arc, after=8)(h)
    assert h.took(*arc, after=11)(h)
    assert not h.took(*arc, after=12)(h)
    assert h.a.arcs == [(7, *arc), (11, *arc)]
    assert h.a.latest_arc[arc] == 11


def test_reset_does_not_record_an_arc_or_clear_history(h):
    observe(h, 3, arch.CODES[arch.DA])
    arcs, index = list(h.a.arcs), dict(h.a.latest_arc)
    observe(h, 4, arch.CODES[arch.DQ], reset=True)
    observe(h, 5, arch.CODES[arch.DQ], reset=True)
    observe(h, 6, arch.CODES[arch.DQ])  # No transition after reset either.
    assert h.a.arcs == arcs and h.a.latest_arc == index
    assert not h.took(arch.DA, arch.DQ, after=0)(h)


def test_default_cutoff_is_captured_when_predicate_is_created(h):
    arc = (arch.DQ, arch.DA)
    before_reset = h.took(*arc)
    observe(h, 3, arch.CODES[arch.DA])
    h.t0 = 4  # fresh() changes t0, not the monotonic cycle count or history.
    after_reset = h.took(*arc)
    assert before_reset(h) and not after_reset(h)
    observe(h, 4, arch.CODES[arch.DQ], reset=True)
    observe(h, 6, arch.CODES[arch.DA])
    assert before_reset(h) and after_reset(h)
    assert h.took(*arc, after=6)(h)


def test_index_matches_original_scan_after_every_mixed_observation(h):
    # Main-state arcs, simultaneous TX/RX substate arcs, repeated transitions,
    # stable states and resets all pass through the real _observe method.
    codes = [0x00, 0x01, 0x04, 0x06, 0x08, 0x09, 0x0a, 0x0b,
             0x0c, 0x0d, 0x20, 0x25, 0x2a, 0x2f, 0x20,
             0x21, 0x10, 0x11, 0x12, 0x20, 0x20]
    arcs = sorted(arch.ARC_SET) + [("never", "seen")]
    captured = [(h.took(*arc), arc, h.t0) for arc in arcs]
    for _ in range(3):
        h.t0 = h.now + 1
        for i, code in enumerate(codes):
            observe(h, h.now + 1, code, reset=i == 0)
            for arc in arcs:
                for cutoff in (-1, 0, h.t0, h.now - 1, h.now, h.now + 1):
                    assert h.took(*arc, after=cutoff)(h) == scan(h, arc, cutoff)
                assert h.took(*arc)(h) == scan(h, arc, h.t0)
            for predicate, arc, cutoff in captured:
                assert predicate(h) == scan(h, arc, cutoff)
        captured += [(h.took(*arc), arc, h.t0) for arc in arcs]
    assert h.a.latest_arc[arch.RE, arch.RXI] == h.a.latest_arc[arch.RXI, arch.ROFF]
    assert len(h.a.arcs) > len(h.a.latest_arc)  # Repeats remain in history.
