"""The single ``nightly-red`` issue: open or update it on failure, close it on
the next green run."""

from __future__ import annotations

import re

from .config import MetricsError

LABEL = "nightly-red"
TITLE = "Nightly run is red"
# Labels for a new issue; it must also satisfy cfg/labels.yaml (one type:, one area:).
NEW_LABELS = (LABEL, "type:bug", "area:ci")
OUTCOMES = ("success", "failure")
_RUN_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+(/attempts/\d+)?\Z")


def open_red(gh) -> list[dict]:
    items = gh.paginate(f"repos/{gh.repo}/issues?state=open&labels={LABEL}")
    return sorted((x for x in items if "pull_request" not in x), key=lambda x: x["number"])


def sync(gh, outcome: str, run_url: str) -> tuple[str, list[int]]:
    """Apply the red-issue rule; return (action, issue numbers touched)."""
    if outcome not in OUTCOMES:
        raise MetricsError(f"--outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
    if not _RUN_URL.match(run_url):
        raise MetricsError("--run-url must be https://github.com/OWNER/NAME/actions/runs/ID")
    r = gh.repo
    issues = open_red(gh)
    if outcome == "failure":
        if issues:
            n = issues[0]["number"]
            gh.api("POST", f"repos/{r}/issues/{n}/comments",
                   [("body", f"Nightly still red: {run_url}")])
            return "updated", [n]
        body = (f"The nightly run failed: {run_url}\n\n"
                "This issue is opened and updated by the nightly workflow and closed "
                "automatically by the next green run.")
        fields = [("title", TITLE), ("body", body)] + [("labels[]", x) for x in NEW_LABELS]
        created = gh.api("POST", f"repos/{r}/issues", fields)
        return "opened", [created["number"]]
    closed = []
    for issue in issues:
        n = issue["number"]
        gh.api("POST", f"repos/{r}/issues/{n}/comments",
               [("body", f"Nightly green again: {run_url}")])
        gh.api("PATCH", f"repos/{r}/issues/{n}",
               [("state", "closed"), ("state_reason", "completed")])
        closed.append(n)
    return ("closed" if closed else "none"), closed
