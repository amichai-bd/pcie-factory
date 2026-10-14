---
name: Task
about: One bounded outcome, reviewable in one PR (see the issue-creation skill)
title: "<area>: <action-oriented summary>"
labels: []
---

<!-- Format owned by .claude/skills/issue-creation/SKILL.md. Labels from
     cfg/labels.yaml: exactly one type:, at least one area:, one ip: per IP. -->

Part of #<epic>. Depends on #<n> (or: None).

## Outcome
What is true when this is done, independent of how.

## Scope
In: files and wiki pages this issue owns.
Out: adjacent work left to other issues (linked).

## Acceptance
- [ ] Verifiable criterion (exact check, behavior, or review point).
- [ ] Owning wiki page updated (HAS/MAS/DV_PLAN/FPGA/ASIC, tool SPEC, or BKM).
- [ ] Tests or regression-list entries at the right level.

## Risks
Dependencies, ordering, required access or physical action, or None.
