# Best known methods

Repository-wide, design-neutral engineering methods. Each BKM is normative for
its discipline and links to the owning page instead of restating it.

| BKM | Owns |
| --- | --- |
| [RTL](rtl.md) | SystemVerilog style, packages, naming, filelists, lint waivers |
| [Arch](arch.md) | Python architecture models of top-level IPs: layout, perf scenarios, use by DV |
| [DV](dv.md) | cocotb structure, regression lists, levels, seeds, DV plan alignment |
| [DevOps](devops.md) | `./build`, levels and budgets, local evidence, CI selection, nightly, labels, metrics |
| [Wiki](wiki.md) | Page sets, spec↔code alignment, writing rules |
| [FPGA](fpga.md) | Quartus Lite flow, board target, `src/<ip>/fpga/` contents |
| [PD](pd.md) | ORFS flow, platform, `src/<ip>/asic/` contents, reported metrics |

Precedence: [`CLAUDE.md`](../../CLAUDE.md) is normative where it and a BKM
disagree until both are reconciled in the same issue. The
[layout page](../project/layout.md) owns paths and per-IP file rules; the
[M0 decision record](../project/decisions/0001-m0-grill.md) records why.
Tool specs (`wiki/tools/<tool>/SPEC.md`) own exact command syntax.

Methods here are adapted from the nand2anything BKMs (`wiki/bkm/`, commit
`085ef4b`, same owner, no license file); each page carries its own
provenance line.
