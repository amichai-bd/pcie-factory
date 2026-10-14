# Factory wiki

The wiki owns specifications and methods. Code and wiki change together: a PR
that changes behavior updates its owning page, and reviewers reject drift.

| Section | Owns |
| --- | --- |
| [Project](project/architecture.md) | Factory architecture, [layout](project/layout.md), [roadmap](project/roadmap.md), [decisions](project/decisions/0001-m0-grill.md). |
| [`bkm/`](bkm/README.md) | Best known methods: RTL, DV, architecture models, DevOps, wiki, FPGA, PD. |
| `tools/<tool>/SPEC.md` | One spec per tool under `tools/`. |
| [`src/`](src/README.md) | Per-IP specs mirroring the kind-first `src/`: `arch/<ip>/HAS.md`, `rtl/<ip>/MAS.md`, `dv/<ip>/DV_PLAN.md`, `fpga/<ip>/FPGA.md`, `asic/<ip>/ASIC.md`; plus the design [roadmap](src/roadmap.md) and decision records. The only design-specific pages. |
| `tracker/` | Metrics history from nightly runs. |

The agent contract is [`CLAUDE.md`](../CLAUDE.md); it is normative where it
and a page disagree until they are reconciled in the same issue.
