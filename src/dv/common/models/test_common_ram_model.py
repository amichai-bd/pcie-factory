"""The RAM model follows the MAS contracts: read-first collisions, lane
merges, write-through, held reads and the true dual-port rules."""

import pytest

from common_ram_model import Port, RamModel


def test_read_first_collision():
    m = RamModel("sync_ram", 8, 2, 1)
    m.edge(Port(wr_en=1, lanes=1, addr=1, data=0x11))
    m.edge(Port(wr_en=1, lanes=1, addr=1, data=0x22))
    assert m.outputs() == [(0x11, 0xFF)]
    m.edge(Port(addr=1))
    assert m.outputs() == [(0x22, 0xFF)]


def test_unwritten_word_is_unknown():
    m = RamModel("sync_ram", 8, 2, 1)
    m.edge(Port(addr=3))
    assert m.outputs() == [(0, 0)]


def test_lanes_merge_and_mark_known():
    m = RamModel("sync_ram", 16, 1, 2)
    m.edge(Port(wr_en=1, lanes=0b01, addr=0, data=0xAABB))
    m.edge(Port(addr=0))
    assert m.outputs() == [(0x00BB, 0x00FF)]
    m.edge(Port(wr_en=1, lanes=0b10, addr=0, data=0xCCDD))
    m.edge(Port(wr_en=1, lanes=0b00, addr=0, data=0x1234))
    assert m.outputs() == [(0xCCBB, 0xFFFF)]


def test_write_through_shows_new_lanes():
    m = RamModel("sync_ram_wt", 16, 1, 2)
    m.edge(Port(wr_en=1, lanes=0b11, addr=1, data=0x1122))
    m.edge(Port(wr_en=1, lanes=0b10, addr=1, data=0x3344))
    assert m.outputs() == [(0x3322, 0xFFFF)]
    m.edge(Port(wr_en=0, lanes=0b11, addr=1, data=0x5566))
    assert m.outputs() == [(0x3322, 0xFFFF)]


def test_held_and_sdp_hold_without_read_enable():
    for kind in ("sync_ram_held", "sdp_ram"):
        m = RamModel(kind, 8, 2, 1)
        m.edge(Port(wr_en=1, lanes=1, addr=2, wr_addr=2, data=0x5A, rd_en=0))
        assert m.outputs() == [(0, 0)]
        m.edge(Port(addr=2, rd_en=1))
        m.edge(Port(wr_en=1, lanes=1, addr=2, wr_addr=2, data=0x77, rd_en=0))
        assert m.outputs() == [(0x5A, 0xFF)]


def test_sdp_separate_addresses():
    m = RamModel("sdp_ram", 8, 2, 1)
    m.edge(Port(wr_en=1, lanes=1, wr_addr=1, addr=1, data=0x99))
    assert m.outputs() == [(0, 0)]
    m.edge(Port(wr_en=1, lanes=1, wr_addr=2, addr=1, data=0x33))
    assert m.outputs() == [(0x99, 0xFF)]


def test_tdp_both_ports_read_first():
    m = RamModel("tdp_ram", 8, 2, 1)
    m.edge(Port(wr_en=1, lanes=1, addr=0, data=0x10), Port(wr_en=1, lanes=1, addr=1, data=0x20))
    m.edge(Port(wr_en=1, lanes=1, addr=1, data=0x30), Port(addr=1))
    assert m.outputs() == [(0x20, 0xFF), (0x20, 0xFF)]
    m.edge(Port(addr=1), Port(addr=0))
    assert m.outputs() == [(0x30, 0xFF), (0x10, 0xFF)]


def test_tdp_disjoint_lanes_may_share_a_word():
    m = RamModel("tdp_ram", 16, 1, 2)
    m.edge(Port(wr_en=1, lanes=0b01, addr=0, data=0x00AA), Port(wr_en=1, lanes=0b10, addr=0,
                                                                 data=0xBB00))
    m.edge(Port(addr=0), Port(addr=0))
    assert m.outputs() == [(0xBBAA, 0xFFFF)] * 2


def test_tdp_write_collision_is_rejected():
    m = RamModel("tdp_ram", 8, 2, 1)
    with pytest.raises(ValueError):
        m.edge(Port(wr_en=1, lanes=1, addr=3), Port(wr_en=1, lanes=1, addr=3))


@pytest.mark.parametrize("geometry", [(0, 2, 1), (8, 0, 1), (8, 2, 0), (8, 2, 3)])
def test_illegal_geometry_is_rejected(geometry):
    with pytest.raises(ValueError):
        RamModel("sync_ram", *geometry)
