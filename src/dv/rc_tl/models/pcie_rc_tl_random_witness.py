"""Non-vacuity obligations of the shortened long requester campaign."""

from pcie_rc_tl_model import BAD, CA, CRS, LINK_DOWN, LOCAL_UR, OK, TIMEOUT, UR

PLAN_CATEGORIES = ("good", "status", "poison", "malformed", "stray", "duplicate", "d7", "noanswer")
PHASE_WITNESSES = ("reset_cycles", "link_down_cycles", "credit_outage_cycles",
                   "split_success", "d7_failure")
EXPECTED_STATUSES = {OK, UR, CA, CRS, BAD, LINK_DOWN, LOCAL_UR, TIMEOUT}


def check_witness(counts, statuses):
    missing = [f"plan_{name}" for name in PLAN_CATEGORIES if not counts.get(f"plan_{name}")]
    missing += [name for name in PHASE_WITNESSES if not counts.get(name)]
    assert not missing, f"long random requester did not execute: {missing}; counts={dict(counts)}"
    assert EXPECTED_STATUSES <= statuses, (
        f"long random requester missing statuses {EXPECTED_STATUSES - statuses}; counts={dict(counts)}")
    assert counts.get("event_mask", 0) & 0x7F == 0x7F, (
        f"long random requester missed requester error events: {counts.get('event_mask', 0):#x}")
