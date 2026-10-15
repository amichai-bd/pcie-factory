# Design pages

The design-specific part of the wiki: the specs for the PCI Express
controller declared in [`src/README.md`](../../src/README.md), plus its
roadmap and decision records. Everything else in the wiki is design-neutral.

| Page | Holds |
| --- | --- |
| [Roadmap](roadmap.md) | Product milestones, their waves and issues. |
| [References](references.md) | The official specifications and sources of truth the design follows: links, revisions and access terms only. |
| `decisions/NNNN-<slug>.md` | Design decision records (index below). |
| `arch/<ip>/HAS.md`, `rtl/<ip>/MAS.md`, `dv/<ip>/DV_PLAN.md`, `fpga/<ip>/FPGA.md`, `asic/<ip>/ASIC.md` | Per-IP specs mirroring the kind-first `src/` ([page set](../bkm/wiki.md#page-set)). |

## Decisions

Numbers are shared with the factory records in
[`wiki/project/decisions/`](../project/decisions/0001-m0-grill.md).

| # | Record | Status |
| --- | --- | --- |
| 0002 | [M1 grill: Gen1 x1 physical layer, kind-first layout](decisions/0002-m1-grill.md) | accepted |
