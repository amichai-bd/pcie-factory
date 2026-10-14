# Chip-design factory

An agent-driven factory that turns hardware intent into reviewed, verified RTL
with FPGA and ASIC evidence. The factory itself (contract, skills, build,
regressions, CI, wiki, metrics) is the product; the design in `src/` is what it
builds.

- **Agents:** start with [`CLAUDE.md`](CLAUDE.md), the operating contract.
- **Specs and methods:** the [wiki](wiki/index.md), also published as a public
  site on GitHub Pages. The repository itself is public.
- **Design target:** declared in [`src/README.md`](src/README.md).

## Quick start

`./build` is the single entry point for checks, simulation, regressions, FPGA,
ASIC and the wiki site. It is being built under the M0 epic; see the
[roadmap](wiki/project/roadmap.md) for status.

## How work flows

Captain (human) → firstmate (root agent) → crewmates (subagents). Every change
goes issue → branch → worktree → draft PR → independent review → squash merge
→ cleanup. Details in the
[agent-flow](.claude/skills/agent-flow/SKILL.md) skill.

## Status

[![ci-gate](https://github.com/amichai-bd/pcie-factory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/amichai-bd/pcie-factory/actions/workflows/ci.yml)
[![nightly](https://github.com/amichai-bd/pcie-factory/actions/workflows/nightly.yml/badge.svg?branch=main)](https://github.com/amichai-bd/pcie-factory/actions/workflows/nightly.yml)
[![pages](https://github.com/amichai-bd/pcie-factory/actions/workflows/pages.yml/badge.svg?branch=main)](https://github.com/amichai-bd/pcie-factory/actions/workflows/pages.yml)

Metrics history (code, CI, nightly, tests, issues and PRs, FPGA and ASIC per
IP) is charted on the live
[metrics tracker](https://amichai-bd.github.io/pcie-factory/tracker/)
([source](wiki/tracker/index.md)). The nightly job updates its data; it never
edits this README.

## License

Licensed under the [Apache License 2.0](LICENSE). Vendored third-party files
keep their own licenses next to them (see each `PROVENANCE.md`).
guard negative test
