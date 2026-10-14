---
name: pr-babysit
description: Use when driving your own open pull request to mergeable green - red CI, REQUEST-CHANGES findings, review comments, or merge conflicts with main - and re-requesting review after each push. Never skips or disables tests.
---

# PR babysit

Owned by the implementer of the PR (or a crewmate the firstmate assigns in its
place). Part of the fix loop in [agent-flow](../agent-flow/SKILL.md#6-review-loop);
the gates are in [`CLAUDE.md`](../../../CLAUDE.md#flow).

Use the GitHub MCP tools (`pull_request_read` with `get`, `get_check_runs`,
`get_comments`, `get_review_comments`; `get_job_logs`;
`add_reply_to_pull_request_comment`; `add_issue_comment`) or `gh` with
`--repo <owner>/<repo>`. Work only in the issue worktree.

## Loop

Repeat until the merge gate holds for the current head:

1. Read the PR: head SHA, mergeable state, check runs, the latest
   `REVIEW-VERDICT` comment and any unresolved review threads.
2. Handle what is red, in this order: conflicts, CI, findings.
3. Rerun the affected checks locally, then the required levels on the new
   commit; update the PR body's validation section for the new head SHA.
4. Push, then ask the firstmate for re-review of the new SHA (a PR comment
   naming it is enough when the firstmate is watching).

When the firstmate is watching a PR and a session can receive events, it may
subscribe to PR activity instead of polling.

## Merge conflicts

```text
git fetch origin
git merge origin/main          # in the issue worktree
```

Resolve each conflict by understanding both sides; keep `main`'s behavior
unless the issue changes it. Rerun checks, commit the merge, push. Do not
rebase or force-push a branch that is shared or under review; a merge commit
is fine because the PR is squash-merged.

## CI red

1. Open the failing job log and find the first real error, not the last line.
2. Reproduce locally with the same command and seed.
3. Fix the root cause in code, test or spec, whichever is wrong per the
   owning wiki page.
4. If the failure is unrelated to the PR (broken `main`, flaky infra), prove
   it (same failure on `main`, or a rerun) and report it to the firstmate,
   who opens or links an issue. Do not paper over it.

Never: skip, disable, `xfail` or delete a test; loosen a check or threshold;
add a waiver without a reviewed reason; push empty commits to retrigger CI;
bypass a gate. If a test is genuinely wrong, fix it and say why in the PR.

## Review findings

For each Blocking item in a REQUEST-CHANGES verdict, and each review thread:

- **Accept:** fix it, and reply with the commit that fixes it.
- **Rebut:** reply with concrete reasoning (spec section, evidence). The
  reviewer decides on re-review; do not resolve the thread yourself if it is
  disputed.

Non-blocking items: fix if cheap and in scope; otherwise reply and, if
worthwhile, ask the firstmate for a follow-up issue. Never widen scope to
satisfy a suggestion.

## Stop and report

Report to the firstmate when the head has an APPROVE verdict and every check
is green (or local evidence is recorded under the temporary exception), or
when blocked on something only the captain can resolve (product decision,
credentials, physical action). Include the head SHA and what changed since
the last review.
