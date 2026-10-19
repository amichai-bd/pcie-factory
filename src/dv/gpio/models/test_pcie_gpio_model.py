"""Unit tests of the pcie_gpio reference model (no simulator).

Run: python3 -m pytest src/dv/gpio/models
"""

import pytest

import pcie_gpio_model as gm


def test_encode_decode_round_trip():
    """A slot's four transfers: the marker and code bits 0 to 2 on the
    control pin, the beat's nibbles low first on the data pins."""
    xs = gm.encode(gm.SOP_DLLP, 0xA5C3)
    assert xs == [(1, 0x3), (1, 0xC), (1, 0x5), (0, 0xA)]
    assert gm.decode(xs) == (1, gm.SOP_DLLP, 0xA5C3)


def test_idle_marker_pattern():
    """An IDLE slot reads 1, 0, 0, 0 on the control pin: the alignment marker."""
    assert [c for c, _ in gm.encode(gm.IDLE, gm.TRAIN)] == [1, 0, 0, 0]


def feed(table, slots, up=True):
    out = [table.slot(1, c, d, up) for c, d in slots]
    return sum(r.pulse for r in out), [r.beat for r in out if r.beat is not None]


def test_packet_delivered():
    t = gm.RxTable()
    pulses, beats = feed(t, [(gm.SOP_TLP, 1), (gm.MID, 2), (gm.EOP, 3)])
    assert pulses == 0
    assert [(b.sop, b.eop, b.err, b.data) for b in beats] == [
        (True, False, False, 1), (False, False, False, 2), (False, True, False, 3)]


def test_one_beat_packet():
    pulses, beats = feed(gm.RxTable(), [(gm.ONE_DLLP, 7)])
    assert pulses == 0 and len(beats) == 1 and beats[0].sop and beats[0].eop and beats[0].dllp


@pytest.mark.parametrize("code", [gm.MID, gm.EOP, gm.ABORT])
def test_no_packet_open_pulses_then_discards(code):
    """MID, EOP or ABORT with no packet open pulse once; the MID, EOP and
    ABORT slots after it are dropped silently until an IDLE."""
    t = gm.RxTable()
    pulses, beats = feed(t, [(code, 0), (gm.MID, 0), (gm.EOP, 0), (gm.ABORT, 0),
                             (gm.IDLE, gm.IDLE_WORD), (gm.MID, 0)])
    assert pulses == 2 and beats == []


@pytest.mark.parametrize("slot", [(gm.IDLE, gm.IDLE_WORD), (gm.SOP_TLP, 0), (gm.ONE_TLP, 0)])
def test_open_packet_closed_with_error(slot):
    """IDLE, SOP or ONE inside a packet: an error eop and one pulse; the new
    packet and the rest of the old one are discarded."""
    t = gm.RxTable()
    pulses, beats = feed(t, [(gm.SOP_TLP, 1), slot, (gm.MID, 2), (gm.EOP, 3)])
    assert pulses == 1
    assert [(b.sop, b.eop, b.err) for b in beats] == [(True, False, False), (False, True, True)]


def test_abort_closes_without_pulse():
    t = gm.RxTable()
    pulses, beats = feed(t, [(gm.SOP_TLP, 1), (gm.ABORT, 0)])
    assert pulses == 0 and beats[-1].err and beats[-1].eop


def test_damaged_marker():
    t = gm.RxTable()
    feed(t, [(gm.SOP_TLP, 1)])
    r = t.slot(0, gm.MID, 2)
    assert r.pulse and r.beat.err and r.beat.eop


def test_unknown_word_pulses():
    r = gm.RxTable().slot(1, gm.IDLE, 0x1234)
    assert r.pulse and r.kind == "damaged"


@pytest.mark.parametrize("word,kind", [(gm.TRAIN, "train"), (gm.TRAIN_OK, "train_ok"),
                                       (gm.IDLE_WORD, "idle"), (gm.HOT_RESET, "hot_reset")])
def test_word_kinds(word, kind):
    assert gm.RxTable().slot(1, gm.IDLE, word).kind == kind


def test_not_up_not_delivered():
    """Packets framed while not up are not delivered, nor their rest after up."""
    t = gm.RxTable()
    t.slot(1, gm.SOP_TLP, 1, up=False)
    assert t.slot(1, gm.MID, 2).beat is None
    assert t.slot(1, gm.EOP, 3).beat is None


def test_link_down_closes_delivered_packet():
    t = gm.RxTable()
    t.slot(1, gm.SOP_TLP, 1)
    b = t.link_down()
    assert b is not None and b.err and b.eop
    assert t.link_down() is None

