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

````markdown
Closes #<n>. Part of epic #<epic>.

## What
Files and purpose, grouped by area. Owning wiki pages changed.

## Notes for review
Design choices, deviations from the issue, shared files touched
outside issue scope (with reason), known limitations and follow-up issues.

## Validation
At head <full 40-character sha>. Python 3.x.y, pytest x.y.z[, Verilator x.y, cocotb x.y.z].
Seeds: <the seeds, or "per the crc32 seed rule">.

| Command | Result |
| --- | --- |
| `<exact command>` | <pass/fail counts, warnings> |
| `./build regress --level 0 --level 1 ...` | L0 24/24 passed, CPU 59.1 s, wall 27 s; L1 110/110 passed, CPU 504.1 s, wall 542.0 s |

| Mutant | Entry | Seed | Result |
| --- | --- | --- | --- |
| M1: <what was changed> | <test entry> | <seed> | FAIL at cycle <c>: <message> |

### Not run
What was cut or not rerun at this head, and why; `None.` if nothing.

## Review
Independent reviewer: assigned by the firstmate. Verdict: pending.

<attribution footer used in this repository>
````

Rules. `./build pr check <pr>` ([pr tool spec](../../../wiki/tools/pr/SPEC.md))
checks each item marked **(checked)** mechanically; the rest is the
reviewer's. All checked items sit in the `## Validation` section.

- **(checked)** Keywords: `Closes #<n>` names exactly one issue. A partial PR
  writes only `Part of #<n>`. No closing keyword (close, closes, closed, fix,
  fixes, fixed, resolve, resolves, resolved) stands next to any issue the
  body calls `Part of`, anywhere in the body, not even in a sentence like
  "part 2 closes #<n>". Keywords inside code spans and code blocks are not
  counted.
- **(checked)** Head: an `At head <sha>` line with the full 40-character
  head SHA. Every result in the section ran at that SHA; if you commit
  again, rerun and update it. Never label a result with a SHA it did not run
  at.
- **(checked)** Commands: a `| Command | Result |` table (with its
  separator row), one row per command actually run at the head, the exact
  command in backticks in the first cell and a non-empty result. You may
  elide a host-specific path (such as a lock file) as `<lock>`; say so.
- **(checked)** Versions: `<Tool> <x.y>` for each tool the commands used:
  Python and pytest for `pytest`; Verilator, cocotb and Python for
  `./build regress` and `./build sim` (`cfg/pr.yaml`).
- **(checked)** Seeds: when a `./build regress` or `./build sim` row ran, a
  line with the word "seed" or "seeds" and the seed numbers, `crc32` (the
  seed rule) or a link to the [seed rule](../../../wiki/tools/regress/SPEC.md#seeds).
- **(checked)** Levels: each `./build regress` row states, for every
  `--level N` it ran, `L<N> <passed>/<total>` followed by `CPU <s> s` for
  that level (wall time too). A level's CPU above its `budget_s` in
  `cfg/levels.yaml` needs a line `L<N> budget: <why>` in the body.
- **(checked)** Mutants: a PR that adds or changes files under `src/rtl/` or
  `src/dv/` (`cfg/pr.yaml`) has a table whose header names a `Mutant`
  column, with its separator row and one row per mutant: what was changed,
  the entry and seed, and where and how it failed (cycle and message).
- **(checked)** Not run: a `### Not run` subsection listing anything cut or
  not rerun with the reason, or `None.`.
- **(checked)** Labels: `./build labels check <pr>` rules.
- Treat warnings as failures unless a reviewed waiver covers them.
- Local evidence complements `ci-gate`, which is the required merge check;
  it never replaces it.
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
- labels match the issue and `./build labels check <pr>` reports `labels OK`;
- `./build pr check <pr>` reports `evidence OK` (read-only; it reads the
  body, head and files from GitHub);
- the URL is in the expected repository.

`./build pr check` must pass on the current head before the firstmate
spawns a reviewer and before the PR is marked ready. Rerun it after every
push and body edit. Fix the body or the evidence, never the check; a rule
that is wrong for a PR is a finding against this skill and the tool, fixed
in its own issue.

Fix mismatches with `update_pull_request` (or `gh pr edit`) and read back
again. If the head moved, rerun section 1 and update the body.

## 5. Report

Return to the firstmate: PR URL and number, head SHA, delivered items,
validation commands and results, the `./build pr check` output, deviations
and open questions. Then stop.
