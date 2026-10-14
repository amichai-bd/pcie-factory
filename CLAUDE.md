# Agent operating contract

## Mission

Build a repeatable, agent-driven factory that turns captain-defined hardware
intent into reviewed, verified RTL with FPGA and ASIC evidence. The factory
(contract, skills, build, regressions, CI, wiki, metrics) is the product; the
design under `src/` is its proving ground. Prefer small, understandable
implementations and the fastest test that can disprove a change.

This file is the normative contract for agent work; `README.md` is the human
overview. The wiki owns specifications and methods, skills own procedures,
issues own one bounded outcome, pull requests own delivery evidence. If this
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

## Non-negotiables

- **Public repository.** The repository and its wiki site are public:
  everything committed must be fit for public reading, so no credentials,
  personal data, private machine details or unredistributable content
  ([publishability](wiki/bkm/wiki.md#publishability)).
- **License and provenance.** The repository is licensed under Apache-2.0
  ([`LICENSE`](LICENSE)); every contribution is under it. Code or text adapted
  from elsewhere needs a compatible license or the copyright owner's
  permission, carries a provenance note (source, commit, license), and is
  adapted, not copied blindly.
- **Self-hosted runner safety,** in depth: the fork job guard, never
  approving a fork PR's workflow run, re-pushing reviewed fork commits here,
  never `pull_request_target` with a PR checkout ([DevOps BKM, Forks](wiki/bkm/devops.md#continuous-integration)).
- **Specs and code** change together ([wiki BKM](wiki/bkm/wiki.md#specs-and-code-stay-aligned)).
- Never weaken acceptance, skip a test or bypass a gate to finish.

## Flow

Every tracked change, however small, goes issue → claim → branch and worktree
→ draft PR → independent `REVIEW-VERDICT` → squash merge → cleanup. The
[agent-flow](.claude/skills/agent-flow/SKILL.md) skill owns the steps and the
merge gate.

**Exceptions:**
- Permanent: the nightly data PR may touch only `wiki/tracker/data/**`; a guard
  enforces it and it auto-merges after `ci-gate`, with no issue or reviewer.
- Temporary, captain-approved: until `ci-gate` is a required check on
  `main`, a PR may merge on recorded local evidence when `ci-gate` has not
  run. A red `ci-gate` run always blocks merge. Remove this bullet then.

## Pointers

- **Roles:** captain, firstmate, crewmates in [firstmate](.claude/skills/firstmate/SKILL.md).
- **Verification:** levels, evidence, seeds, warnings and `workdir/` output in
  the [DevOps](wiki/bkm/devops.md#local-evidence) and [DV](wiki/bkm/dv.md) BKMs.
- **Layout:** paths and per-IP file rules in the [layout page](wiki/project/layout.md).

## Skills

Read the matching `.claude/skills/<name>/SKILL.md` before working in its area.

| Skill | Applies to |
| --- | --- |
| `firstmate` | Roles, delegation, captain decisions, coordination. |
| `agent-flow` | Issues, claims, worktrees, PRs, review, merge, cleanup. |
| `issue-creation` | Drafting, labeling and creating issues and sub-issues. |
| `pr-creation` | Opening a draft PR with validation evidence. |
| `pr-reviewer` | Independent read-only review and the verdict comment. |
| `pr-babysit` | Driving a PR to green: CI, findings, conflicts. |
| `rtl-coder` | RTL and `.rtl.f` filelists. |
| `dv-coder` | cocotb tests, models, DV filelists, regression lists, DV plans. |
| `build-maintainer` | `build`, `tools/`, `cfg/`, `.github/`. |
| `wiki-spec-writer` | MAS, HAS, DV plans, tool specs, BKMs. |
