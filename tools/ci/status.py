"""``./build ci status``: post the ``ci-gate`` commit status for a nightly
data branch, from the dispatched ``ci-gate`` job's own result.

A data PR is opened with ``GITHUB_TOKEN``, so its ``ci-gate`` runs by
dispatch, and branch protection does not count a dispatched check run toward
the PR. A commit status with the same context, posted by GitHub Actions
(the app the required check is bound to), does. This module only maps a job
result to a status and refuses any ref outside the data-branch prefix; the
workflow decides when it runs (wiki/tools/ci/SPEC.md#build-ci-status).
"""

from __future__ import annotations

import re

from metrics.config import MetricsError, load as load_metrics
from metrics.gh import Gh, resolve_repo

# The required check's name; the job in ci.yml and branch protection use it.
CONTEXT = "ci-gate"
# GitHub job results (needs.<job>.result) and the commit state each becomes.
# Anything but success or failure (cancelled, skipped) is an error state,
# which blocks the merge like a failure.
STATES = {"success": "success", "failure": "failure", "cancelled": "error", "skipped": "error"}
# GitHub rejects a status description longer than this (HTTP 422); a long
# branch name is cut and marked with "...".
DESCRIPTION_MAX = 140
_SHA = re.compile(r"^[0-9a-f]{40}$")
_HEADS = "refs/heads/"


class StatusError(Exception):
    pass


def plan(result: str, sha: str, ref: str, branch_prefix: str, target_url: str = "") -> dict:
    """The status fields to post, or StatusError for an input that must not post."""
    if result not in STATES:
        raise StatusError(f"--result must be one of {', '.join(STATES)}, got {result!r}")
    if not _SHA.match(sha):
        raise StatusError(f"--sha must be a full 40-character lowercase commit SHA, got {sha!r}")
    branch = ref[len(_HEADS):] if ref.startswith(_HEADS) else ref
    if ref.startswith("refs/") and not ref.startswith(_HEADS):
        raise StatusError(f"--ref {ref!r} is not a branch")
    if not branch.startswith(branch_prefix) or branch == branch_prefix:
        raise StatusError(f"refusing to post {CONTEXT}: {branch!r} is not a "
                          f"{branch_prefix}<name> data branch")
    description = f"{CONTEXT} job on {branch}: {result}"
    if len(description) > DESCRIPTION_MAX:
        description = description[:DESCRIPTION_MAX - 3] + "..."
    fields = {"state": STATES[result], "context": CONTEXT, "description": description}
    if target_url:
        fields["target_url"] = target_url
    return fields


def run(ctx, args, gh_factory=Gh) -> int:
    try:
        prefix = load_metrics(ctx.cfg_dir).branch_prefix
        fields = plan(args.result, args.sha, args.ref, prefix, args.target_url or "")
        print(f"[status] {args.sha[:12]}: {fields['context']}={fields['state']} "
              f"({fields['description']})")
        if args.dry_run:
            print("[status] dry run: nothing posted")
            return 0
        gh = gh_factory(resolve_repo(args.repo, ctx.repo_root))
        gh.api("POST", f"repos/{gh.repo}/statuses/{args.sha}", sorted(fields.items()))
    except (StatusError, MetricsError) as exc:
        print(f"error: {exc}")
        return 2
    print(f"[status] posted to {gh.repo}")
    return 0
