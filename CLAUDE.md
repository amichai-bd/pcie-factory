# Agent operating contract

## Mission

Build a repeatable, agent-driven factory that turns captain-defined hardware
intent into reviewed, verified RTL with FPGA and ASIC evidence. The factory
(contract, skills, build, regressions, CI, wiki, metrics) is the product; the
design under `src/` is its proving ground. Prefer small, understandable
implementations and the fastest test that can disprove a change.

This file is normative for agent work. `README.md` is the human overview. The
wiki owns architecture and specifications. Skills own repeatable procedures.
Issues own one bounded outcome. Pull requests own delivery evidence. If this
file, a skill and a spec disagree, stop and reconcile them in the same issue;
never invent a third rule.

## Design neutrality

Everything outside `src/` and `wiki/src/` is design-neutral: this file, skills,
`tools/`, `cfg/`, `.github/`, BKMs and project pages never name the design
target, its protocol or its IPs. Tools discover IPs from `src/`; no IP name is
ever hard-coded in `tools/` or `cfg/`. `./build check` enforces this. Exempt:
the repository slug in URLs and nightly data under `wiki/tracker/data/`. The
target and its module prefix are declared once, in
[`src/README.md`](src/README.md).

## Layout

| Path | Owns |
| --- | --- |
| `CLAUDE.md` | This contract. There is no `AGENTS.md`. |
| `build` | The single entry point; implementation in `tools/`. |
| `.claude/` | Settings, session-start hook, `skills/<name>/SKILL.md`. |
| `.github/` | Workflows (`ci`, `nightly`, `pages`) and templates. |
| `cfg/` | Generic configuration only (levels, CI, host pins, checks, labels). |
| `tools/` | Generic Python tools, each with unit tests and a wiki SPEC. |
| `src/<ip>/` | `rtl/`, `dv/`, optional `fpga/`, `asic/`; sub-IPs in `<subip>/`. |
| `wiki/` | Specs and methods, published as the wiki site. |
| `worktrees/` | Issue worktrees; contents ignored. |
| `workdir/` | All generated output; ignored and disposable. |

[Layout](wiki/project/layout.md) owns the full tree and per-IP file rules
(filelists, regression lists, physical inputs).

## Roles

- **Captain:** the human. Sets direction, priorities and acceptance intent;
  answers decisions outside existing authorization.
- **Firstmate:** the root interactive agent and the captain's single point of
  contact. Plans issues, assigns worktrees, delegates to crewmates, reads their
  evidence, and owns review, merge and cleanup end to end.
- **Crewmate:** a delegated subagent with fresh context and one bounded job. It
  reports to the firstmate, never directly to the captain, and never broadens
  its assignment. An implementer crewmate owns one issue branch through
  approval. A reviewer crewmate reviews a change it did not author.

The firstmate may implement an issue itself, but a different crewmate reviews
it. Keep at most ten active crewmates. Crewmates run as Claude Code subagents
or sessions, each writing crewmate in its own git worktree.

## Flow

Every tracked change follows the same path; no change is too small for it:

1. One scoped GitHub issue with acceptance criteria and labels from
   `cfg/labels.yaml`.
2. **Claim:** add the `in-progress` label and a comment naming the session,
   branch and worktree. Skip issues claimed by someone else; only the claimant
   or the captain releases a claim.
3. Branch `<issue>-<slug>` from current `origin/main`, worktree
   `worktrees/<issue>-<slug>/`. Never implement on the root checkout.
4. Implement code and its owning wiki pages together, within issue scope.
5. Run focused checks while developing, then the required levels once on the
   commit intended for review. Record exact commands and results.
6. Push and open a **draft** PR that closes the issue.
7. An independent reviewer crewmate reviews the exact head, read-only and
   trusting CI, for code↔wiki alignment, BKM conformance and test adequacy
   (DV plan against regression lists), and posts one
   `REVIEW-VERDICT: APPROVE|REQUEST-CHANGES @<sha>` comment.
8. Fix every accepted finding and request review of the new head; repeat.
9. **Merge gate:** an APPROVE verdict for the current head, `ci-gate` green on
   that head, no unresolved review threads, mergeable. The firstmate marks the
   PR ready and squash-merges. No captain checkpoint on the normal path.
10. Verify the merge on `main` and the issue closed, remove the worktree and
    branch, and report the integration SHA.

The `agent-flow` skill owns the detailed procedure. Stop for the captain only
for a product decision outside the issue, missing credentials, or a physical
action. Failures, findings and conflicts are work, not reasons to stop. Never
weaken acceptance, skip a test, or bypass a gate to finish.

**Exceptions (one permanent, one temporary):**

- The nightly workflow's data PR may touch only `wiki/tracker/data/**`; a guard
  check enforces it and it auto-merges after `ci-gate`, with no issue or
  reviewer.
- Temporary, captain-approved: until the self-hosted CI runners are online,
  the merge gate omits `ci-gate` and the PR must record local validation
  evidence instead. Remove this bullet when `ci-gate` is required on `main`.

## Specs and code stay aligned

Specs live only in the wiki. Every change to `src/` or `tools/` updates its
owning wiki page in the same PR, and every wiki change that alters behavior
updates the code. Each IP and sub-IP has `MAS.md`, `HAS.md` and `DV_PLAN.md`
under `wiki/src/`; each tool has `wiki/tools/<tool>/SPEC.md`. Reviewers reject
a PR whose code and specs disagree.

## Verification

- Verilator is the only simulator; DV is Python with cocotb.
- Regressions are per IP in `src/<ip>/dv/regress/level{0,1,2}.yaml`. Levels
  are exclusive: combine them explicitly (`--level 0 --level 1`).
- **L0:** fast local confidence. **L1:** the `ci-gate` check (static checks
  plus L0+L1 of affected IPs, within its budget). **L2:** nightly, including
  FPGA and ASIC flows and metrics.
- Evidence demonstrates behavior, not command success. Treat warnings as
  failures unless a reviewed waiver explains them. Record seeds. Keep logs and
  waves under `workdir/`; summarize evidence in the PR, never commit outputs.
- Code or text adapted from another repository carries a provenance note
  (source, commit, license) and is adapted, not copied blindly.
- Never commit credentials, private machine details or unredistributable tool
  output.

The DevOps BKM in `wiki/bkm/` owns levels, budgets, CI selection, labels and
metrics.

## Skills

Read the matching skill before working in its area. Skills live in
`.claude/skills/<name>/SKILL.md`.

| Skill | Applies to |
| --- | --- |
| `agent-flow` | Issues, claims, worktrees, PRs, review, merge, cleanup. |
| `issue-creation` | Drafting, labeling and creating issues and sub-issues. |
| `pr-creation` | Opening a draft PR with validation evidence. |
| `pr-reviewer` | Independent read-only review and the verdict comment. |
| `pr-babysit` | Driving a PR to green: CI, findings, conflicts. |
| `rtl-coder` | RTL and `.rtl.f` filelists. |
| `dv-coder` | cocotb tests, models, DV filelists, regression lists, DV plans. |
| `build-maintainer` | `build`, `tools/`, `cfg/`, `.github/`. |
| `wiki-spec-writer` | MAS, HAS, DV plans, tool specs, BKMs. |

Until a listed skill exists, follow this contract and the owning BKM.
