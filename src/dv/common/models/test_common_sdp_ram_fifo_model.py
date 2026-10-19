"""The queue model follows the MAS: two-edge fall-through, pop and refill
at one edge, capacity of the RAM plus the head, the contract errors."""

import pytest

from common_sdp_ram_fifo_model import FifoModel


def test_push_shows_at_the_head_two_edges_later():
    m = FifoModel(2)
    m.edge(True, 0xA, False)
    assert not m.head_valid and not m.empty and m.ram_used == 1
    m.edge(False, 0, False)
    assert m.head_valid and m.head == 0xA and m.ram_used == 0


def test_pop_and_refill_share_an_edge():
    m = FifoModel(2)
    for d in (1, 2, 3):
        m.edge(True, d, False)
    m.edge(False, 0, False)
    got = []
    while m.head_valid:
        got.append(m.head)
        m.edge(False, 0, True)
    assert got == [1, 2, 3] and m.empty


def test_capacity_is_the_ram_plus_the_head():
    m = FifoModel(1)
    m.edge(True, 1, False)
    m.edge(True, 2, False)      # 1 loads into the head at this edge
    m.edge(True, 3, False)
    assert m.full and m.head == 1 and m.ram_used == 2
    with pytest.raises(ValueError):
        m.edge(True, 4, False)


def test_pop_with_no_head_is_outside_the_contract():
    m = FifoModel(1)
    m.edge(True, 1, False)
    with pytest.raises(ValueError):
        m.edge(False, 0, True)


def test_reset_empties():
    m = FifoModel(1)
    m.edge(True, 1, False)
    m.edge(False, 0, False)
    m.reset()
    assert m.empty and not m.full and m.ram_used == 0
