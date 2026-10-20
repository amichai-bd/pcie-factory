---
name: pr-reviewer
description: Use when acting as, or spawning, the independent reviewer crewmate for a pull request. Read-only review of the exact head SHA for code and wiki alignment, BKM conformance, test adequacy, neutrality and evidence, ending in one REVIEW-VERDICT that the firstmate posts verbatim.
---

# PR reviewer

<!-- Provenance: adapted from nand2anything .agents/skills/pr-reviewer/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

Review gate of [agent-flow](../agent-flow/SKILL.md#6-review-loop); reviewer
independence in [firstmate](../firstmate/SKILL.md#reviewer-independence). The
verdict comment, not a native GitHub approval, is the gate (crewmates share
one GitHub identity and cannot approve their own PRs).

## Part 1: firstmate spawns the reviewer

- Use a **fresh** crewmate that did not author the change. As a Claude Code
  subagent, spawn the `pr-reviewer` agent type
  ([`.claude/agents/pr-reviewer.md`](../../agents/pr-reviewer.md)). It loads
  this skill and has only read and GitHub tools, with no shell, so it cannot
  run anything. The firstmate does not review; a crewmate does.
- Read the PR head SHA (`pull_request_read` method `get`). Do not create a
  worktree for the reviewer: it reads with `git show`/`git diff` on the
  fetched head or through the GitHub API, and runs nothing.
- Before spawning, run `./build pr check <pr>` (read-only,
  [pr tool spec](../../../wiki/tools/pr/SPEC.md)) on the current head. On
  `evidence FAIL`, return the PR to its implementer instead of spawning a
  reviewer ([pr-creation](../pr-creation/SKILL.md#4-read-back)).
- Hand off: repo, PR number, head SHA, issue number, this skill, the
  `./build pr check` output for that head, and the expected report (the
  verdict text and the reviewed SHA).
- After the implementer pushes fixes, re-engage a reviewer (the same one may
  continue) with the new head SHA. A re-review checks only the fixes and the
  merge delta.

## Part 2: reviewer procedure

Rules: **read-only, and review only.** Never edit, commit, push, label,
approve natively, mark ready or merge. **Never build or run anything:** no
`./build check`, `regress`, `sim`, `arch` or flows, no `pytest`, no
mutants, no worktree to run in. Allowed commands are read-only: `git show`,
`git diff`, `git log`, `grep`, reading files and the GitHub API. The
`pr-reviewer` agent type has no shell at all. For a citation spot-check the
firstmate passes the cached document's path in the hand-off, from
`./build specs path --absolute <id>`, and the reviewer reads it. Write scratch
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
| Citations | The PR touches material that relies on an external document such as a standard, manual or datasheet (a `[cite: ...]`, or text derived from one) and does not paraphrase it: copied sentences, tables or figures; or a citation outside the [citation form](../../../wiki/bkm/wiki.md#citations). A "behaves per §x" pointer that stands in for a rule the page should state (specs are self-contained; unsure points belong in Open items). A field layout that reproduces the standard's table, its column structure or its prose instead of stating this design's use of each field (positions and code values are fine). A cited standard missing from `wiki/src/references.md`. Confirm paraphrase-only in the verdict by reading the cited passages: when the firstmate's hand-off gives a cached document's path, spot-check citations and claims against that text, and never paste its text into a comment, verdict or PR description. CI's `./build check` only flags long quotes, form errors and cached-text copies. |
| Neutrality | Outside `src/` and `wiki/src/`, any design-target, protocol or IP name (repo slug in URLs exempt). Check with `grep -rniI` on the changed paths. |
| Hygiene | Generated output, logs or credentials committed; provenance note missing on adapted content; unexplained warnings or waivers. |

**Evidence.** Trust CI: read the `ci-gate` result on the head SHA
(`pull_request_read` method `get_check_runs`); red or missing is a finding.
The firstmate's hand-off carries the `./build pr check` output for the
head: treat it as given and do not re-derive what it checks (head SHA and
stale SHAs, command table, versions, seeds, per-level results and CPU
against budgets, mutation table, `### Not run`, keywords and rescoped
`Closes`, neutrality of the diff, labels). Cite it in the verdict
(`pr check: evidence OK @<sha>`); a missing or stale output, or any `error:`
line in it, is a blocking finding. A passing check is not a waiver: a format
discrepancy against the [pr-creation](../pr-creation/SKILL.md#2-compose-the-body)
rules that the checker missed is still a finding (name the rule, and it is
also a finding against the tool). Then audit what it cannot judge: that the
recorded evidence is plausible, covers the required levels for the affected
IPs, and that each mutant and result actually supports the claim.

The required levels are the affected L0+L1
([local evidence](../../../wiki/bkm/devops.md#local-evidence)); a cited green
`ci-gate` run on the head satisfies them. Mutants cover the changed code
only. Do not ask for full L2, all-IP coverage or a broad mutant campaign:
the nightly runs those. Ask for targeted L2 entries (named tests or seeds)
only when the change targets L2 behaviour or a specific doubt needs them, and
say which. Missing, stale or implausible evidence is a finding. Never
rerun anything to confirm a claim. When a claim lacks recorded proof (a test
that can fail, a caught mutant, a timing bound), raise a blocking finding
that asks the author to run it and record the result in the PR.

### 4. Write the verdict

Write **exactly one** verdict and return it to the firstmate. The reviewer
never writes to GitHub. The firstmate posts it **verbatim** as one PR comment,
with a one-line note that it was posted on the reviewer's behalf (captain
ruling). Format:

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

On a new head, review only the delta (the fixes and any merge from the base)
and whether each earlier Blocking item is fixed or soundly rebutted, then
write one new verdict for the new SHA. Earlier verdicts are stale.

### 6. Report

Return to the firstmate: the full verdict text, the reviewed SHA and a
one-line summary of blocking items. The firstmate posts the verdict
verbatim, without editing it, and keeps the merge.
