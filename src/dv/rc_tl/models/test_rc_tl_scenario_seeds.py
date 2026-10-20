"""Golden original testcase identities and deterministic nightly rotation.

Loads the pure seed helpers without a simulator, cocotb or global path edits.
The goldens use the original entry seeds and cocotb's original testcase names.
"""

import importlib.util
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[1] / "tb" / "rc_tl_scenarios.py"
_spec = importlib.util.spec_from_file_location("rc_tl_scenario_seed_helpers", _path)
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)

GOLDENS = [
    ("header_contract", 226822507, "hdr_reset", "tests.test_rc_tl_hdr.hdr_reset",
     200213919, 128893070956907071560461056348422140967920129724),
    ("request_completion_contract", 532766566, "req_status", "tests.test_rc_tl_req.req_status",
     2079550537, 1454714764346931070979451304330019999835640810053),
    ("host_aw8_contract", 1404276179, "host_cmds_aw8", "tests.test_rc_tl_inb.host_cmds",
     899574590, 403404397702930747566895572588634520333858179550),
]


@pytest.mark.parametrize("suite,pinned,entry,fullname,outer,actual", GOLDENS)
def test_pinned_original_identity(monkeypatch, suite, pinned, entry, fullname, outer, actual):
    monkeypatch.setenv("COCOTB_RANDOM_SEED", str(pinned))
    assert _helpers.scenario_seed(suite, entry, fullname) == (outer, actual)


@pytest.mark.parametrize("suite,pinned,entry,fullname,outer,actual", GOLDENS)
def test_rotation_changes_stimulus_reproducibly(monkeypatch, suite, pinned, entry, fullname,
                                              outer, actual):
    monkeypatch.setenv("COCOTB_RANDOM_SEED", str(pinned ^ 123))
    expected = (outer ^ 123, (outer ^ 123) + actual - outer)
    assert expected != (outer, actual)
    assert _helpers.scenario_seed(suite, entry, fullname) == expected
    assert _helpers.scenario_seed(suite, entry, fullname) == expected


def test_parameter_entry_seed_is_distinct_from_testcase_identity(monkeypatch):
    monkeypatch.setenv("COCOTB_RANDOM_SEED", "1404276179")
    aw8 = _helpers.scenario_seed("host_aw8_contract", "host_cmds_aw8",
                                 "tests.test_rc_tl_inb.host_cmds")
    default = _helpers.scenario_seed("host_aw8_contract", "host_cmds",
                                     "tests.test_rc_tl_inb.host_cmds")
    wrong_name = _helpers.scenario_seed("host_aw8_contract", "host_cmds_aw8",
                                       "tests.test_rc_tl_inb.host_cmds_aw8")
    assert aw8 != default and aw8 != wrong_name
