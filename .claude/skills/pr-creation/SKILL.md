---
name: pr-creation
description: Use when an issue branch is pushed and ready for review, to open and verify one draft pull request that closes the issue and records validation evidence at the exact head SHA.
---

# Draft PR creation

<!-- Provenance: adapted from nand2anything .agents/skills/pr-creation/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

This skill owns only the draft-PR step of
[agent-flow](../agent-flow/SKILL.md#5-commit-push-draft-pr-implementer).
Review, ready, merge and cleanup belong to later steps; never do them here.

Use the GitHub MCP tools (`create_pull_request`, `pull_request_read`,
`update_pull_request`, `issue_write`) or `gh` with `--repo <owner>/<repo>`.

## 1. Check the branch

The PR must describe exactly what the reviewer will see:

```text
git status --porcelain                      # empty
git diff --check origin/main...HEAD         # no whitespace errors
git diff --stat origin/main...HEAD          # only issue-owned files
git log --format='%h %s' origin/main..HEAD  # attribution footers present
git fetch origin && git rev-parse HEAD origin/<n>-<slug>   # equal
```

Fix and push first if anything fails. Record the head SHA. Validation in the
body must have been run at this SHA; if you commit again, rerun it.

## 2. Compose the body

Write it to `workdir/pr-<n>.md` in the worktree (never committed):

```markdown
Closes #<n>. Part of epic #<epic>.

## What
Files and purpose, grouped by area. Owning wiki pages changed.

## Notes for review
Design choices, deviations from the issue, shared files touched
outside issue scope (with reason), known limitations and follow-up issues.

## Validation
At head <sha>:
| Command | Result |
| --- | --- |
| `<exact command>` | <pass/fail counts, seeds, versions> |

## Review
Independent reviewer: assigned by the firstmate. Verdict: pending.

<attribution footer used in this repository>
```

Rules:

- `Closes #<n>` names exactly one issue.
- One row per command actually run at the head SHA; state seeds and tool
  versions; treat warnings as failures unless a reviewed waiver covers them.
- While the temporary exception in [`CLAUDE.md`](../../../CLAUDE.md#flow)
  holds (`ci-gate` not yet a required check), this section is the merge evidence: include the
  required levels for affected IPs and tool unit tests.
- Summarize evidence; never paste or commit logs, waves or reports.
- End with the PR attribution footer shown in the session instructions or in
  recently merged PRs.

## 3. Open the draft

Title: the issue's outcome as an imperative summary. Base `main`, head the
issue branch, `draft: true`.

```text
create_pull_request: base=main head=<n>-<slug> draft=true title=... body=...
gh pr create --repo <owner>/<repo> --base main --head <n>-<slug> \
  --draft --title '<title>' --body-file workdir/pr-<n>.md
```

Do not use `gh pr create --fill`. Apply the issue's `type:`, `area:` and
`ip:` labels to the PR (`issue_write` on the PR number, or the `gh api` form
in agent-flow's **Labels** note); with MCP, write the full list as that note
says. If creation fails, find the cause (existing PR for the branch, unpushed head)
before retrying.

## 4. Read back

Read the PR (`pull_request_read` method `get`; `gh pr view --json ...`) and
verify:

- state open, draft true, base `main`, head the issue branch;
- head SHA equals the recorded SHA;
- body contains `Closes #<n>` and the validation table;
- labels match the issue and `./build labels check <pr>` reports `labels OK`;
- the URL is in the expected repository.

Fix mismatches with `update_pull_request` (or `gh pr edit`) and read back
again. If the head moved, rerun section 1 and update the body.

## 5. Report

Return to the firstmate: PR URL and number, head SHA, delivered items,
validation commands and results, deviations and open questions. Then stop.
