# Factory wiki

The wiki owns specifications and methods. Code and wiki change together: a PR
that changes behavior updates its owning page, and reviewers reject drift.

| Section | Owns |
| --- | --- |
| [Project](project/architecture.md) | Factory architecture, [layout](project/layout.md), [roadmap](project/roadmap.md), [decisions](project/decisions/0001-m0-grill.md). |
| [`bkm/`](bkm/README.md) | Best known methods: RTL, DV, DevOps, wiki, FPGA, PD. |
| `tools/<tool>/SPEC.md` | One spec per tool under `tools/`. |
| `src/<ip>/` | Per-IP `MAS.md`, `HAS.md`, `DV_PLAN.md`; the only design-specific pages. |
| `tracker/` | Metrics history from nightly runs. |

The agent contract is [`CLAUDE.md`](../CLAUDE.md); it is normative where it
and a page disagree until they are reconciled in the same issue.
