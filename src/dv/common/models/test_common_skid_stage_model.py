"""The output stage model follows the MAS: two beats in order, ready while
fewer than two are held, no gap while downstream takes every beat, a held
beat stays offered and is new only in its first cycle."""

from common_skid_stage_model import SkidStageModel


def test_streams_with_no_gap_while_downstream_takes_every_beat():
    m = SkidStageModel()
    out = []
    for i in range(5):
        assert m.in_ready
        t = m.edge(True, i, True)
        if t is not None:
            out.append(t)
    assert out == [0, 1, 2, 3] and m.out_data == 4 and m.out_new


def test_a_held_beat_fills_the_skid_and_drops_ready():
    m = SkidStageModel()
    m.edge(True, 1, True)
    assert m.out_new and m.out_data == 1
    m.edge(True, 2, False)          # 1 held, 2 into the skid
    assert not m.in_ready and m.out_data == 1 and not m.out_new
    m.edge(True, 3, False)          # not ready: 3 refused
    assert m.edge(False, 0, True) == 1
    assert m.out_data == 2 and m.out_new and m.in_ready
    assert m.edge(False, 0, True) == 2
    assert not m.out_valid


def test_reset_empties():
    m = SkidStageModel()
    m.edge(True, 1, False)
    m.edge(True, 2, False)
    m.reset()
    assert not m.out_valid and m.in_ready and not m.out_new
