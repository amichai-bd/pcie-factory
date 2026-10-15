"""The failure excerpt shared by the vendor flows: error lines, cap, tail."""

import re

from build.logexcerpt import MAX_ERROR_LINES, TAIL_LINES, excerpt

ERR = re.compile(r"^ERR\b")


def test_errors_before_tail_in_log_order():
    lines = ["ERR one", "info"] + ["ERR two"] + [f"info {i}" for i in range(40)]
    out = excerpt("\n".join(lines), ERR, "x.log")
    assert out[:3] == ["--- error lines in x.log: 2 ---", "ERR one", "ERR two"]
    assert out[3] == f"--- last {TAIL_LINES} lines of x.log ---"
    assert out[4:] == lines[-TAIL_LINES:]


def test_cap_states_the_count_and_the_cut():
    lines = [f"ERR {i}" for i in range(MAX_ERROR_LINES + 7)]
    out = excerpt("\n".join(lines), ERR, "x.log", tail=0)
    assert out[0] == f"--- error lines in x.log: {MAX_ERROR_LINES + 7} (first {MAX_ERROR_LINES} shown) ---"
    assert out[1:MAX_ERROR_LINES + 1] == lines[:MAX_ERROR_LINES]
    assert out[MAX_ERROR_LINES + 1] == "... 7 more error line(s) in x.log"
    assert out[-1] == "--- last 0 lines of x.log ---"


def test_exactly_at_the_cap_is_not_cut():
    lines = [f"ERR {i}" for i in range(MAX_ERROR_LINES)]
    out = excerpt("\n".join(lines), ERR, "x.log", tail=0)
    assert out[0] == f"--- error lines in x.log: {MAX_ERROR_LINES} ---"
    assert not any(line.startswith("...") for line in out)


def test_no_errors_and_short_log():
    out = excerpt("a\nb", ERR, "x.log")
    assert out == ["--- error lines in x.log: none matched ---",
                   "--- last 2 lines of x.log ---", "a", "b"]
