"""Failure excerpt of a tool log: every error line, then the tail.

Used by the vendor flows (``./build fpga``, ``./build asic``) when a step
fails or times out, so the tool's own error messages reach the job log even
when they are far from the end of the log. Each flow passes its tool's error
pattern; this module knows no tool. Spec: wiki/tools/build/SPEC.md.
"""

from __future__ import annotations

import re

# At most this many error lines are printed; the header gives the full count.
MAX_ERROR_LINES = 50
TAIL_LINES = 30


def excerpt(text: str, pattern: re.Pattern, name: str,
            max_errors: int = MAX_ERROR_LINES, tail: int = TAIL_LINES) -> list[str]:
    """Lines to print for a failed step whose log is ``text`` (``name``
    labels it): a header with the error-line count, the first ``max_errors``
    lines matching ``pattern`` in log order, a note when more were cut, then
    the last ``tail`` lines."""
    lines = text.splitlines()
    errors = [line for line in lines if pattern.search(line)]
    shown = errors[:max_errors]
    if not errors:
        out = [f"--- error lines in {name}: none matched ---"]
    elif len(errors) > max_errors:
        out = [f"--- error lines in {name}: {len(errors)} (first {max_errors} shown) ---"]
    else:
        out = [f"--- error lines in {name}: {len(errors)} ---"]
    out += shown
    if len(errors) > max_errors:
        out.append(f"... {len(errors) - max_errors} more error line(s) in {name}")
    out.append(f"--- last {min(tail, len(lines))} lines of {name} ---")
    out += lines[-tail:] if tail > 0 else []
    return out
