"""The reset-synchronizer model follows the MAS: assertion with no edge,
release at the STAGES-th edge after the last fall, restart on re-assertion."""

import pytest

from common_cdc_rst_model import expected_output, release_edge

EDGES = [10, 20, 30, 40, 50, 60, 70, 80]


def test_short_pulse_between_edges():
    # A pulse inside one period still asserts; release two edges later.
    assert expected_output([(11, 1), (13, 0)], EDGES, 2) == [(11, 1), (30, 0)]


def test_assert_needs_no_edge_and_release_counts_stages():
    assert expected_output([(5, 1), (41, 0)], EDGES, 3) == [(5, 1), (70, 0)]


def test_reassert_during_release_restarts_the_count():
    # Fall at 15, rise at 25 (before the release at 30), fall at 35.
    assert expected_output([(15, 1), (17, 0), (25, 1), (35, 0)], EDGES, 2) == [
        (15, 1), (50, 0)]


def test_reassert_after_release_asserts_again():
    assert expected_output([(1, 1), (3, 0), (31, 1), (33, 0)], EDGES, 2) == [
        (1, 1), (20, 0), (31, 1), (50, 0)]


def test_no_release_without_enough_edges():
    assert expected_output([(1, 1), (75, 0)], EDGES, 2) == [(1, 1)]
    assert release_edge(75, EDGES, 2) is None
    assert release_edge(75, EDGES, 1) == 80


@pytest.mark.parametrize("changes", [[(10, 1), (12, 0)], [(1, 0)], []])
def test_rejects_changes_on_an_edge_or_without_a_first_rise(changes):
    with pytest.raises(ValueError):
        expected_output(changes, EDGES, 2)
