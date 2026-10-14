---
name: agent-flow
description: Use for any tracked repository change, from choosing or claiming an issue through worktree, implementation, draft PR, independent review, squash merge and post-merge cleanup. Covers firstmate and crewmate duties, including spawning crewmates as Claude Code subagents or sessions.
---

# Agent flow

<!-- Provenance: adapted from nand2anything .agents/skills/agent-flow/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

[`CLAUDE.md`](../../../CLAUDE.md#flow) owns roles, gates and exceptions; this
skill owns the step-by-step procedure. No change is too small for it.

```text
issue -> claim -> branch + worktree -> code + wiki -> validate -> draft PR
-> reviewer crewmate -> fix loop -> squash merge -> verify on main -> cleanup
```

## GitHub access

Name operations generically; use whichever interface the session has. In
cloud sessions that is the GitHub MCP tools (`mcp__github__*`, loaded with
ToolSearch); `gh` is the local equivalent and always takes
`--repo <owner>/<repo>`. Read the slug from `git remote get-url origin`; never
let a tool infer it. GitHub being unavailable is not a reason to bypass a step.

| Operation | MCP tool | `gh` |
| --- | --- | --- |
| Read issue, comments | `issue_read` | `gh issue view` |
| Label, comment | `issue_write`, `add_issue_comment` | `gh api` (see **Labels**), `gh issue comment` |
| Open / read PR | `create_pull_request`, `pull_request_read` | `gh pr create`, `gh pr view` |
| Ready, merge | `update_pull_request`, `merge_pull_request` | `gh pr ready`, `gh pr merge` |

**Labels:** `issue_write` `labels` replaces the whole list. To add or remove
one label, read the current list (`issue_read` method `get_labels`), write
back the full list with or without it (`issue_write` method `update`), and
read it back. With `gh`, use the incremental REST forms (`gh issue edit`
uses GraphQL, which cloud sessions block):

```bash
gh api -X POST repos/<owner>/<repo>/issues/<n>/labels -f 'labels[]=<label>'
gh api -X DELETE repos/<owner>/<repo>/issues/<n>/labels/<url-encoded label>
```

Issues and PRs share the number space, so both work on a PR number. Check
the rules with `./build labels check <n>`.

## 1. Issue and claim (firstmate)

1. Find or create the issue with the [issue-creation](../issue-creation/SKILL.md)
   skill. One issue, one bounded outcome.
2. Read its labels and comments. If it carries `in-progress` from someone
   else, skip it; only the claimant or the captain releases a claim.
3. **Claim:** add `in-progress` (see **Labels** above), then comment:

   ```markdown
   Claimed by the firstmate (session <session-url>)[, delegated to <crewmate>].

   - Branch: `<n>-<slug>`
   - Worktree: `worktrees/<n>-<slug>/`
   ```

   Read the issue back and confirm the label and comment are present.

## 2. Branch and worktree

From the root checkout (kept clean on `main`; never implement there), run
`git fetch origin` then
`git worktree add -b <n>-<slug> worktrees/<n>-<slug> origin/main`. Before
editing, confirm the worktree is clean and belongs to this issue.
Preserve any dirty worktree of unclear ownership; never reset or delete it.

## 3. Delegate (firstmate)

Spawn one implementer crewmate per issue as a Claude Code subagent (Agent
tool) or a cloud session, working only in its worktree. Keep at most ten
active crewmates. The handoff names: issue number, branch, absolute worktree
path, files the issue owns, relevant wiki pages, this skill, and the expected
final report (PR URL, head SHA, validation commands and results, deviations).
The firstmate may implement itself, but then a different crewmate reviews.

## 4. Implement and validate (implementer)

1. Turn the issue's acceptance into a checklist. Identify owning wiki pages
   (`wiki/src/<ip>/`, `wiki/tools/<tool>/SPEC.md`, `wiki/bkm/`) and consumers.
2. Change code and its wiki pages together, inside issue scope. Read the
   matching area skill from the [`CLAUDE.md` table](../../../CLAUDE.md#skills).
3. Loop on the cheapest focused check that can reject the edit. Fix causes;
   never mute a warning, weaken a check or skip a test.
4. On the commit intended for review, run the required levels once
   ([verification](../../../CLAUDE.md#verification)). Record exact commands,
   seeds, versions and results. Output stays under the worktree's `workdir/`.

## 5. Commit, push, draft PR (implementer)

Commit only issue-owned files. Commit messages end with the session's
attribution footer (`Co-Authored-By:` and session lines, as in recent
`git log`). Push with `git push -u origin <n>-<slug>`, then follow
[pr-creation](../pr-creation/SKILL.md). Report to the firstmate and stop;
do not review or merge your own PR.

## 6. Review loop

The firstmate spawns a **fresh** reviewer crewmate that did not author the
change and hands it the PR number, head SHA, issue and the
[pr-reviewer](../pr-reviewer/SKILL.md) skill. The reviewer posts exactly one
`REVIEW-VERDICT: APPROVE|REQUEST-CHANGES @<sha>` comment and never edits.

On REQUEST-CHANGES the implementer follows [pr-babysit](../pr-babysit/SKILL.md):
fix or concretely rebut each finding, rerun affected checks, push. The
firstmate re-engages a reviewer (the same one may re-review) for the new
head. Every push makes earlier verdicts stale.

## 7. Merge (firstmate)

Read the PR fresh and check every gate:

- latest `REVIEW-VERDICT` is APPROVE and names the current head SHA;
- `ci-gate` is green on that SHA, or, under the temporary exception in
  [`CLAUDE.md`](../../../CLAUDE.md#flow), the PR records local evidence at
  that SHA and the reviewer audited it;
- no unresolved review threads; mergeable, no conflicts;
- the PR closes exactly the issue and its acceptance is met.

Then mark the PR ready (`update_pull_request` with `draft: false`;
`gh pr ready`) and squash-merge, pinning the approved head
(`merge_pull_request` with `merge_method: squash` and
`expectedHeadSha: <approved-sha>`; `gh pr merge --squash --match-head-commit
<sha>`). Set the squash message explicitly: `commit_title` is
`<PR title> (#<pr>)`; `commit_message` is a short summary, `Closes #<n>` and
the commit attribution trailers (the default concatenates branch commits and
duplicates trailers). A failed gate is work for the implementer, never a
waiver.

## 8. Verify and clean up (firstmate)

1. Read back the PR: merged, with its merge commit SHA.
2. `git fetch --prune origin`; confirm the merge commit is on `origin/main`
   and its content equals the approved head:
   `git diff <approved-sha> <merge-sha> --stat` is empty when the branch was
   current with `main`; otherwise compare `git diff <merge-sha>^ <merge-sha>`
   with `git diff <merge-base> <approved-sha>`.
3. Confirm the issue closed; if not, comment with the PR link and close it
   (`issue_write` `state: closed`, `state_reason: completed`).
4. Remove `in-progress` (see **Labels**); closed or abandoned issues release
   it.
5. Remove the worktree and local branch:
   `git worktree remove worktrees/<n>-<slug>` then `git branch -D <n>-<slug>`
   (`-D` because a squash merge does not make the branch an ancestor). Never
   force-remove a dirty worktree; preserve unclear work and report it.
6. Delete the remote branch: `git push origin --delete <n>-<slug>`. If the
   environment's git proxy refuses it, rely on the repository setting "Automatically delete head
   branches" (a captain action if it is off) and note it in the report.
7. Report to the captain: integration SHA, PR and issue state, removed paths,
   anything retained and why.

## When to stop

Only for a product decision outside the issue, missing credentials or
access, or a physical action ([`CLAUDE.md`](../../../CLAUDE.md#flow)).
Failures, findings and conflicts are work.
