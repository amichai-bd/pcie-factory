---
name: pr-reviewer
description: Use when acting as, or spawning, the independent reviewer crewmate for a pull request. Read-only review of the exact head SHA for code and wiki alignment, BKM conformance, test adequacy, neutrality and evidence, ending in one REVIEW-VERDICT comment.
---

# PR reviewer

<!-- Provenance: adapted from nand2anything .agents/skills/pr-reviewer/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

Review gate of [agent-flow](../agent-flow/SKILL.md#6-review-loop); policy in
[`CLAUDE.md`](../../../CLAUDE.md#flow). The verdict comment, not a native
GitHub approval, is the gate (crewmates share one GitHub identity and cannot
approve their own PRs).

## Part 1: firstmate spawns the reviewer

- Use a **fresh** crewmate (Claude Code subagent or session) that did not
  author the change. The firstmate never reviews a PR it implemented.
- Read the PR head SHA (`pull_request_read` method `get`). Optionally create a
  detached worktree for reading: `git worktree add --detach
  worktrees/review-<pr> <sha>`.
- Hand off: repo, PR number, head SHA, issue number, worktree path, this
  skill, and the expected report (verdict comment URL, reviewed SHA).
- After the implementer pushes fixes, re-engage a reviewer (the same one may
  continue) with the new head SHA. Remove the review worktree when done.

## Part 2: reviewer procedure

Rules: **read-only.** Never edit, commit, push, label, approve natively, mark
ready or merge. Write scratch notes only under the review worktree's
`workdir/` or the session scratchpad.

### 1. Pin the head

Read the PR and confirm its head SHA equals the one you were given. If it
moved, review the new head and say so. Every statement in the verdict is
about that SHA only.

### 2. Gather context

Read: the issue (acceptance, scope, comments), `CLAUDE.md`, the diff
(`pull_request_read` methods `get_diff`, `get_files`, or
`git diff origin/main...<sha>`), the owning wiki pages, and the BKMs under
`wiki/bkm/` for each area touched.

### 3. Check

| Area | Finding if |
| --- | --- |
| Acceptance | Any issue criterion unmet or unverifiable; scope creep beyond the issue. |
| Code and wiki | `src/` or `tools/` changed without its MAS/HAS/DV_PLAN or tool SPEC, or they disagree. |
| BKM | Code breaks a rule in the owning BKM or area skill. |
| Tests | DV_PLAN items lack tests in `src/<ip>/dv/regress/level{0,1,2}.yaml`, tests sit at the wrong level, or tools lack unit tests. Important negative and boundary cases missing. |
| Evidence | See below. |
| Neutrality | Outside `src/` and `wiki/src/`, any design-target, protocol or IP name (repo slug in URLs exempt). Check with `grep -rniI` on the changed paths. |
| Hygiene | Generated output, logs or credentials committed; provenance note missing on adapted content; unexplained warnings or waivers. |

**Evidence.** Trust CI: when `ci-gate` exists, read its result on the head
SHA (`pull_request_read` method `get_check_runs`); red or missing is a
finding. Under the temporary exception in `CLAUDE.md`, audit instead the
local validation recorded in the PR body: it must name the head SHA, exact
commands, results, seeds and versions, and cover the required levels for the
affected IPs. Missing, stale or implausible evidence is a finding. Do not
rerun regressions yourself; you may run cheap read-only checks (grep, YAML
parse, link check) to confirm a claim.

### 4. Post the verdict

Post **exactly one** PR comment (`add_issue_comment` on the PR number, or
`gh pr comment --body-file`). Format:

```markdown
REVIEW-VERDICT: APPROVE @<full-head-sha>

Reviewed: PR #<pr> for issue #<n> at <sha>.

### Blocking
- `path:line`: what is wrong, why, and what would fix it.
(or: None)

### Non-blocking
- Suggestions that do not gate merge.
(or: None)

### Evidence
What was audited (CI run or recorded local evidence) and its verdict.
```

- First line is exactly `REVIEW-VERDICT: APPROVE @<sha>` or
  `REVIEW-VERDICT: REQUEST-CHANGES @<sha>`.
- APPROVE requires an empty Blocking list. Any Blocking item means
  REQUEST-CHANGES.
- Order findings by severity; cite paths and concrete reasoning.

### 5. Re-review

On a new head, review the delta and whether each earlier Blocking item is
fixed or soundly rebutted, then post one new verdict for the new SHA. Earlier
verdicts are stale.

### 6. Report

Return to the firstmate: verdict, reviewed SHA, comment URL, and a one-line
summary of blocking items. Merge stays with the firstmate.
