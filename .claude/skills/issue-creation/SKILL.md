---
name: issue-creation
description: Use when drafting, labeling or creating a GitHub issue or sub-issue, including epics and their children. Covers duplicate checks, one bounded outcome, acceptance checklists, labels from cfg/labels.yaml, and read-back verification.
---

# Issue creation

<!-- Provenance: adapted from nand2anything .agents/skills/issue-creation/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

An issue is the work contract for exactly one bounded outcome
([`CLAUDE.md`](../../../CLAUDE.md#flow)). This skill turns an authorized goal
into one verified issue. Claiming is done later by
[agent-flow](../agent-flow/SKILL.md#1-issue-and-claim-firstmate).

Use the GitHub MCP tools (`search_issues`, `issue_read`, `issue_write`,
`sub_issue_write`) or `gh` with `--repo <owner>/<repo>`; never infer the repo.

## 1. Check for duplicates

Search open and closed issues for distinctive terms from the goal and read
likely matches, including comments and linked PRs.

```text
search_issues: repo:<owner>/<repo> <terms> in:title,body
gh issue list --repo <owner>/<repo> --state all --search '<terms> in:title,body'
```

- Same outcome already tracked: use that issue; do not create another.
- Overlap only: state the boundary and the dependency in both directions.
- Conflicts with a spec or roadmap ([roadmap](../../../wiki/project/roadmap.md)):
  reconcile first, or ask the captain if it is a product decision.

## 2. Scope one outcome

Split the goal until each issue has one observable outcome that can be
reviewed in one PR. Track separable outcomes as separate issues, not as an
open-ended checklist. Larger goals become an epic with sub-issues.

Title: concise and action-oriented, prefixed by area where useful
(`tools: ...`, `skills: ...`, `dv: ...`).

## 3. Write the body

```markdown
Part of #<epic>. Depends on #<n> (or: None).

## Outcome
What is true when this is done, independent of how.

## Scope
In: files and wiki pages this issue owns.
Out: adjacent work left to other issues (linked).

## Acceptance
- [ ] Verifiable criterion (exact check, behavior, or review point).
- [ ] Owning wiki page updated (MAS/HAS/DV_PLAN, tool SPEC, or BKM).
- [ ] Tests or regression-list entries at the right level.

## Risks
Dependencies, ordering, required access or physical action, or None.
```

Rules: name the owning spec path (or the spec gap this issue closes); avoid
vague criteria such as "works"; keep design-specific names only in issues
about `src/` or `wiki/src/`.

## 4. Choose labels

Labels are defined in `cfg/labels.yaml` and synced by `./build labels sync`.
Use only these values, never invent one:

| Dimension | Values | Rule |
| --- | --- | --- |
| `type:` | `feat`, `bug`, `docs`, `infra`, `test`, `refactor`, `epic`, `data` | Exactly one. |
| `area:` | `rtl`, `dv`, `build`, `ci`, `wiki`, `skills`, `fpga`, `pd` | At least one, by the part touched. |
| `ip:` | One per IP, derived from `src/` | One per affected IP. |
| `prio:` | `p0`, `p1`, `p2` | Optional. |
| State | `in-progress`, `blocked`, `needs-captain`, `nightly-red` | Set by the flow, not at creation. |

`cfg/labels.yaml` is the source of truth: if it ever differs from this
table, follow it and fix this table in the same issue. Until the labels exist
in the repository, create the issue unlabeled and note it in the issue.

## 5. Create and link

For a drafting request, return the title, body, labels and duplicate findings
without touching GitHub. When creation is authorized:

1. Create the issue with title, full body and labels
   (`issue_write` method `create`; `gh issue create --body-file`).
2. For a sub-issue, attach it to its epic (`sub_issue_write` method `add`
   with the child's numeric issue id, not its number; or the web UI) and
   keep the epic's checklist in sync.
3. Read it back (`issue_read` method `get`) and verify title, body, labels,
   `open` state, parent link and URL. Fix any mismatch and read back again.

Report the issue number and URL to the caller. Do not claim, assign a
worktree or start implementation here.
