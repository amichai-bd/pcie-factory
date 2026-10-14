# Chip-design factory

An agent-driven factory that turns hardware intent into reviewed, verified RTL
with FPGA and ASIC evidence. The factory itself (contract, skills, build,
regressions, CI, wiki, metrics) is the product; the design in `src/` is what it
builds.

- **Agents:** start with [`CLAUDE.md`](CLAUDE.md), the operating contract.
- **Specs and methods:** the [wiki](wiki/index.md), also published as a public
  site on GitHub Pages.
- **Design target:** declared in [`src/README.md`](src/README.md).

## Quick start

`./build` is the single entry point for checks, simulation, regressions, FPGA,
ASIC and the wiki site. It is being built under the M0 epic; see the
[roadmap](wiki/project/roadmap.md) for status.

## How work flows

Captain (human) → firstmate (root agent) → crewmates (subagents). Every change
goes issue → branch → worktree → draft PR → independent review → squash merge
→ cleanup. Details in [`CLAUDE.md`](CLAUDE.md#flow).

## Status

<!-- Badges (ci-gate, nightly, pages) and the tracker link land with the CI,
nightly and wiki-site milestones. -->
Badges and a link to the metrics tracker arrive with the CI, nightly and wiki
site milestones.
