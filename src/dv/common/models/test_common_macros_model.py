"""The macro model follows the BKM contract: reset beats enable, reads return
old data, lane enables merge, synchronizers delay two edges."""

from common_macros_model import (ARST_EN_VAL, ARST_VAL, META_VAL, RST_EN_VAL, RST_VAL, Inputs,
                                 MacroModel, array_next, lane_word)


def test_reset_wins_over_enable():
    m = MacroModel()
    m.edge(Inputs(rst=1, arst=1, en=1, data=0x11))
    out = m.outputs()
    assert (out["dff"], out["dff_en"], out["rst"], out["rst_val"], out["rst_en"]) == (
        0x11, 0x11, 0, RST_VAL, RST_EN_VAL)
    assert (out["arst_val"], out["arst_en"]) == (ARST_VAL, ARST_EN_VAL)
    assert [out[f"arr_rst_en_{k}"] for k in range(4)] == [0] * 4
    assert [out[f"arr_en_{k}"] for k in range(4)] == array_next(0x11)


def test_enable_low_holds():
    m = MacroModel()
    m.edge(Inputs(en=1, data=0x12))
    m.edge(Inputs(en=0, data=0x34))
    out = m.outputs()
    assert out["dff"] == 0x34 and out["rst"] == 0x34 and out["arst_val"] == 0x34
    assert out["dff_en"] == out["rst_en"] == out["arst_en"] == 0x12


def test_read_during_write_returns_old_data():
    m = MacroModel()
    m.edge(Inputs(we=1, waddr=3, data=0x40, lanes=0b11))
    m.edge(Inputs(we=1, waddr=3, data=0x41, lanes=0b01, re=1, raddr=3))
    assert m.rd == m.rd_en == 0x40
    assert m.wrd == lane_word(0x40)
    m.edge(Inputs(raddr=3))
    assert m.rd == 0x41
    assert m.wrd == (lane_word(0x40) & 0xFF00) | (lane_word(0x41) & 0x00FF)


def test_partial_lane_write_of_unknown_word_stays_unknown():
    m = MacroModel()
    m.edge(Inputs(we=1, waddr=1, lanes=0b10, data=1))
    assert m.wmem[1] is None


def test_synchronizer_latency_and_reset_value():
    m = MacroModel()
    m.edge(Inputs(rst=1, data=7))
    assert (m.outputs()["meta"], m.outputs()["meta_val"]) == (0, META_VAL)
    m.edge(Inputs(data=7))
    assert m.outputs()["meta"] == 0
    m.edge(Inputs(data=7))
    assert m.outputs()["meta"] == m.outputs()["meta_val"] == 7


def test_reset_synchronizer_asserts_at_once_and_releases_after_two_edges():
    m = MacroModel()
    m.async_reset()
    assert m.outputs()["rst_sync"] == 1
    m.edge(Inputs(arst=1))
    m.edge(Inputs())
    assert m.outputs()["rst_sync"] == 1
    m.edge(Inputs())
    assert m.outputs()["rst_sync"] == 0
    m.edge(Inputs(arst=1))
    assert m.outputs()["rst_sync"] == 1
