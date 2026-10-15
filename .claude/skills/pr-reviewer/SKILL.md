---
name: pr-reviewer
description: Use when acting as, or spawning, the independent reviewer crewmate for a pull request. Read-only review of the exact head SHA for code and wiki alignment, BKM conformance, test adequacy, neutrality and evidence, ending in one REVIEW-VERDICT comment.
---

# PR reviewer

<!-- Provenance: adapted from nand2anything .agents/skills/pr-reviewer/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

Review gate of [agent-flow](../agent-flow/SKILL.md#6-review-loop); reviewer
independence in [firstmate](../firstmate/SKILL.md#reviewer-independence). The
verdict comment, not a native GitHub approval, is the gate (crewmates share
one GitHub identity and cannot approve their own PRs).

## Part 1: firstmate spawns the reviewer

- Use a **fresh** crewmate (Claude Code subagent or session) that did not
  author the change. The firstmate does not review; a crewmate does.
- Read the PR head SHA (`pull_request_read` method `get`). Do not create a
  worktree for the reviewer: it reads with `git show`/`git diff` on the
  fetched head or through the GitHub API, and runs nothing.
- Hand off: repo, PR number, head SHA, issue number, this skill, and the
  expected report (verdict comment URL, reviewed SHA).
- After the implementer pushes fixes, re-engage a reviewer (the same one may
  continue) with the new head SHA. A re-review checks only the fixes and the
  merge delta.

## Part 2: reviewer procedure

Rules: **read-only, and review only.** Never edit, commit, push, label,
approve natively, mark ready or merge. **Never build or run anything:** no
`./build check`, `regress`, `sim`, `arch` or flows, no `pytest`, no
mutants, no worktree to run in. Allowed commands are read-only: `git show`,
`git diff`, `git log`, `grep`, reading files and the GitHub API. Write scratch
notes only in the session scratchpad. Report every blocking finding in one
round.

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
| Code and wiki | `src/` or `tools/` changed without its HAS, MAS, DV_PLAN, FPGA/ASIC page or tool SPEC, or they disagree. |
| BKM | Code breaks a rule in the owning BKM or area skill. |
| Tests | DV_PLAN items lack tests in `src/dv/<ip>/regress/level{0,1,2}.yaml`, tests sit at the wrong level, or tools lack unit tests. Important negative and boundary cases missing. |
| Evidence | See below. |
| Publishability | The change adds content that may not be public ([wiki BKM](../../../wiki/bkm/wiki.md#publishability)); the repository and site are public. A workflow lets a fork PR run on the self-hosted runners. |
| Citations | The PR touches material that relies on an external document such as a standard, manual or datasheet (a `[cite: ...]`, or text derived from one) and does not paraphrase it: copied sentences, tables or figures; or a citation outside the [citation form](../../../wiki/bkm/wiki.md#citations). A "behaves per §x" pointer that stands in for a rule the page should state (specs are self-contained; unsure points belong in Open items). A field layout that reproduces the standard's table, its column structure or its prose instead of stating this design's use of each field (positions and code values are fine). A cited standard missing from `wiki/src/references.md`. Confirm paraphrase-only in the verdict by reading the cited passages: when `./build specs list` shows the document present, spot-check citations and claims against `./build specs path --absolute <id>`, and never paste its text into a comment, verdict or PR description. `./build check` only flags long quotes, form errors and cached-text copies. |
| Neutrality | Outside `src/` and `wiki/src/`, any design-target, protocol or IP name (repo slug in URLs exempt). Check with `grep -rniI` on the changed paths. |
| Hygiene | Generated output, logs or credentials committed; provenance note missing on adapted content; unexplained warnings or waivers. |

**Evidence.** Trust CI: read the `ci-gate` result on the head SHA
(`pull_request_read` method `get_check_runs`); red or missing is a finding.
Also audit the local validation recorded in the PR body: it must name the head SHA, exact
commands, results, seeds and versions, and cover the required levels for the
affected IPs. Missing, stale or implausible evidence is a finding. Never
rerun anything to confirm a claim. When a claim lacks recorded proof (a test
that can fail, a caught mutant, a timing bound), raise a blocking finding
that asks the author to run it and record the result in the PR.

### 4. Post the verdict

Post **exactly one** PR comment (`add_issue_comment` on the PR number, or
`gh pr comment --body-file`). Format:

```markdown
REVIEW-VERDICT: APPROVE @<full-head-sha>

Reviewed PR #<pr> for issue #<n> at <short-sha>: what was checked and how.

Blocking:
1. `path:line`: what is wrong, why, and what would fix it.
(or: None)

Non-blocking:
- Suggestions that do not gate merge.
(or: None)

---
_Generated by [Claude Code](https://claude.ai/code)_
```

- First line is exactly `REVIEW-VERDICT: APPROVE @<sha>` or
  `REVIEW-VERDICT: REQUEST-CHANGES @<sha>`.
- APPROVE requires an empty Blocking list. Any Blocking item means
  REQUEST-CHANGES.
- Order findings by severity; cite paths and concrete reasoning. Say what
  evidence was audited (the `ci-gate` run and recorded local evidence).
- Only the first line is parsed; the layout below it is free-form, but keep
  the footer.

### 5. Re-review

On a new head, review the delta and whether each earlier Blocking item is
fixed or soundly rebutted, then post one new verdict for the new SHA. Earlier
verdicts are stale.

### 6. Report

Return to the firstmate: verdict, reviewed SHA, comment URL, and a one-line
summary of blocking items. Merge stays with the firstmate.
