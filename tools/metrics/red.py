"""The single ``nightly-red`` issue: open or update it on failure, close it on
the next green run. A failure names the failed steps (a full-L2 failure or a
coverage-floor miss fails ``regress``) and the commits since the last green
nightly, so the change that broke it can be found without rerunning."""

from __future__ import annotations

import re

from .config import MetricsError

LABEL = "nightly-red"
TITLE = "Nightly run is red"
# Labels for a new issue; it must also satisfy cfg/labels.yaml (one type:, one area:).
NEW_LABELS = (LABEL, "type:bug", "area:ci")
OUTCOMES = ("success", "failure")
_RUN_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+(/attempts/\d+)?\Z")
_SHA = re.compile(r"^[0-9a-f]{40}\Z")
MAX_COMMITS = 50  # commits listed in full; the compare link covers the rest


def open_red(gh) -> list[dict]:
    items = gh.paginate(f"repos/{gh.repo}/issues?state=open&labels={LABEL}")
    return sorted((x for x in items if "pull_request" not in x), key=lambda x: x["number"])


def last_green(gh, workflow: str, branch: str) -> dict | None:
    """The newest successful run of ``workflow`` on ``branch``, or None."""
    data = gh.api("GET", f"repos/{gh.repo}/actions/workflows/{workflow}/runs"
                         f"?branch={branch}&status=success&per_page=1")
    runs = (data or {}).get("workflow_runs") or []
    return runs[0] if runs else None


def commits_since(gh, base: str, head: str) -> tuple[int, list[tuple[str, str]]]:
    """(total, [(sha, subject)...] oldest first) of ``base...head``."""
    data = gh.api("GET", f"repos/{gh.repo}/compare/{base}...{head}") or {}
    commits = [(c["sha"], (c.get("commit", {}).get("message") or "").split("\n", 1)[0])
               for c in data.get("commits", [])]
    return int(data.get("total_commits", len(commits))), commits


def details(gh, head: str | None, steps: dict[str, str] | None,
            workflow: str | None, branch: str) -> str:
    """The failed steps and the commits since the last green nightly, as
    Markdown. A failed lookup becomes a line saying so: the issue still opens."""
    out = []
    failed = [name for name, result in (steps or {}).items() if result == "fail"]
    if steps is not None:
        out.append("Failed steps: " + (", ".join(f"`{n}`" for n in failed) or "none recorded")
                   + ". `regress` covers the full L0+L1+L2 regression and the coverage floors.")
    if head is None or workflow is None:
        return "\n".join(out)
    try:
        green = last_green(gh, workflow, branch)
        if green is None:
            out.append("No green nightly on record, so the commits are not listed.")
            return "\n".join(out)
        base = green["head_sha"]
        if base == head:
            out.append(f"No commits since the last green nightly ({green.get('html_url', base)}): "
                       "the failure is not from a code change (flaky test, host or tool).")
            return "\n".join(out)
        total, commits = commits_since(gh, base, head)
    except MetricsError as exc:
        out.append(f"The commits since the last green nightly could not be listed: {exc}")
        return "\n".join(out)
    out.append(f"Commits since the last green nightly ({green.get('html_url', base[:12])}, "
               f"at `{base[:12]}`): {total}.")
    out += [f"- `{sha[:12]}` {subject}" for sha, subject in commits[-MAX_COMMITS:]]
    if total > min(len(commits), MAX_COMMITS):
        out.append(f"- ... {total - min(len(commits), MAX_COMMITS)} more")
    out.append(f"Compare: https://github.com/{gh.repo}/compare/{base[:12]}...{head[:12]}")
    return "\n".join(out)


def sync(gh, outcome: str, run_url: str, head: str | None = None,
         steps: dict[str, str] | None = None, workflow: str | None = None,
         branch: str = "main") -> tuple[str, list[int]]:
    """Apply the red-issue rule; return (action, issue numbers touched)."""
    if outcome not in OUTCOMES:
        raise MetricsError(f"--outcome must be one of {', '.join(OUTCOMES)}, got {outcome!r}")
    if not _RUN_URL.match(run_url):
        raise MetricsError("--run-url must be https://github.com/OWNER/NAME/actions/runs/ID")
    if not re.fullmatch(r"[\w./-]+", branch or ""):
        raise MetricsError(f"--branch must be a branch name, got {branch!r}")
    if head is not None and not _SHA.match(head):
        raise MetricsError(f"--head must be a full 40-character lower-case SHA, got {head!r}")
    r = gh.repo
    issues = open_red(gh)
    if outcome == "failure":
        extra = details(gh, head, steps, workflow, branch)
        extra = f"\n\n{extra}" if extra else ""
        if issues:
            n = issues[0]["number"]
            gh.api("POST", f"repos/{r}/issues/{n}/comments",
                   [("body", f"Nightly still red: {run_url}{extra}")])
            return "updated", [n]
        body = (f"The nightly run failed: {run_url}{extra}\n\n"
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
