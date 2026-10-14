# Roadmap

## M0: factory infrastructure

Stand up a design-neutral, agent-driven factory and prove it end to end on a
seed IP. Tracked by the M0 epic (issue #1) and its sub-issues.

| Area | Outcome |
| --- | --- |
| Contract and layout | `CLAUDE.md`, layout, README, decision record |
| Methods | BKMs for RTL, DV, DevOps, wiki, FPGA, PD |
| Skills | Flow, coder and maintainer skills |
| Build | `./build` core, checks, labels sync |
| Seed design | One IP with one sub-IP, all levels, full specs |
| Hosts | Cloud session-start hook; self-hosted CI host |
| CI | `ci-gate` on PRs; nightly L2 with metrics and data PR |
| Physical | FPGA (Quartus Lite, DE2-115) and ASIC (ORFS, sky130hd) flows |
| Wiki | Site generator, public Pages deploy, metrics tracker |

M0 is done when the epic's goal checklist holds on `main`.

## Later

Product milestones for the design target are planned in `wiki/src/` once M0
lands.
