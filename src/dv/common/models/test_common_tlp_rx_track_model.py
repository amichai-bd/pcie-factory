"""The receive beat tracker model follows the MAS: beat numbers, header DW
enables, the last header DW by header size, saturation at 63, and the
whole-TLP contract."""

from common_tlp_rx_track_model import BEAT_MAX, TlpRxTrackModel


def feed(m, n, hdr4):
    """One TLP of n beats; returns the positions seen."""
    seen = []
    for i in range(n):
        sop, eop = i == 0, i == n - 1
        assert m.framing_error(True, sop, eop) is None
        seen.append(m.pos(True, sop))
        m.edge(True, sop, eop, hdr4)
    return seen


def test_three_dw_header():
    m = TlpRxTrackModel()
    p = feed(m, 5, False)
    assert [x.en_dw for x in p] == [1, 2, 4, 0, 0]
    assert [x.hdr_last for x in p] == [False, False, True, False, False]
    assert [x.beat for x in p[1:]] == [1, 2, 3, 4] and not m.busy


def test_four_dw_header():
    m = TlpRxTrackModel()
    p = feed(m, 4, True)
    assert [x.hdr_last for x in p] == [False, False, False, True]
    assert p[1].hdr_end == 3 and p[0].hdr_end == 2   # the sop sees the state before it


def test_saturates_at_63():
    m = TlpRxTrackModel()
    p = feed(m, 70, False)
    assert [x.beat for x in p[62:66]] == [62, 63, 63, 63] and m.beat == BEAT_MAX


def test_framing_errors():
    m = TlpRxTrackModel()
    assert m.framing_error(True, False, False) == "beat outside a TLP"
    assert m.framing_error(True, True, True) == "eop before the last header DW"
    m.edge(True, True, False, True)
    assert m.framing_error(True, True, False) == "sop inside a TLP"
    m.edge(True, False, False, False)      # beat 2 of a 4-DW header: one more is due
    assert m.framing_error(True, False, True) == "eop before the last header DW"
    m.edge(True, False, False, False)
    assert m.framing_error(True, False, True) is None
    assert m.framing_error(False, True, True) is None


def test_reset_clears():
    m = TlpRxTrackModel()
    m.edge(True, True, False, True)
    m.reset()
    assert not m.busy and m.beat == 0 and not m.hdr4
