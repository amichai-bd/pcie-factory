# Repository layout

```
.
├── CLAUDE.md                    # agent contract (normative; no AGENTS.md)
├── README.md                    # human overview
├── build                        # single entry point → tools/
├── .claude/                     # settings.json, hooks/, skills/<name>/SKILL.md
├── .github/                     # workflows/{ci,nightly,pages,fpga,asic}.yml, templates
├── cfg/                         # generic config: levels, ci, host, checks, labels
├── tools/                       # generic Python tools + unit tests
├── src/
│   ├── README.md                # declares the design target and prefix
│   ├── lint_waivers.yaml        # reviewed Verilator waivers (optional; checks SPEC)
│   ├── common/{rtl,dv}/         # shared blocks
│   └── <ip>/
│       ├── arch/                # top-level IPs: Python architecture models (planned)
│       ├── rtl/                 # <ip>.rtl.f + synthesizable sources
│       ├── dv/                  # <ip>.dv.f, <ip>.sim.f, tb/, tests/, models/, regress/
│       ├── fpga/                # synthesis targets only: SDC, QSF Tcl, board wrapper
│       ├── asic/                # synthesis targets only: SDC, flow config
│       └── <subip>/             # same shape without arch/, recursively
├── wiki/
│   ├── index.md
│   ├── project/                 # architecture, layout, roadmap, decisions/
│   ├── bkm/                     # rtl, dv, devops, wiki, fpga, pd
│   ├── tools/<tool>/SPEC.md
│   ├── src/<ip>/                # mirrors src/<ip>/: arch/HAS.md (top-level IPs),
│   │                            #   rtl/MAS.md, dv/DV_PLAN.md, fpga/FPGA.md,
│   │                            #   asic/ASIC.md, <subip>/rtl/MAS.md, ...
│   └── tracker/                 # index.md, data/
├── worktrees/                   # issue worktrees (ignored except README)
└── workdir/                     # generated output (ignored)
```

## Neutrality boundary

Only `src/` and `wiki/src/` name the design target. Everything else is
generic; tools discover IPs by walking `src/`. Exempt: the repository slug in
URLs and nightly data in `wiki/tracker/data/`. `src/README.md` declares the
target and the prefix `<prefix>` used below.

## Per-IP files

| File | Rule |
| --- | --- |
| `rtl/<ip>.rtl.f` | Synthesizable sources only. Single source of truth for simulation, FPGA and ASIC. Includes sub-IP and common lists with `-F`. |
| `dv/<ip>.dv.f` | Simulation-only SystemVerilog (testbench tops, bind and assertion files, SV models). |
| `dv/<ip>.sim.f` | `-F ../rtl/<ip>.rtl.f` plus `-F <ip>.dv.f`. |
| `dv/regress/level{0,1,2}.yaml` | This IP's own tests per level. Levels are exclusive; sub-IP tests are not inherited. |
| `fpga/`, `asic/` | Constraints and flow config only; generated output goes to `workdir/`. |
| `arch/` | Top-level IPs only: architecture models (Python, not verification); the tooling is a planned follow-up, so the folder is optional for now. |

## Wiki mirror

`wiki/src/` mirrors `src/`: each IP folder holds `arch/HAS.md` (top-level
IPs only), `rtl/MAS.md`, `dv/DV_PLAN.md`, and `fpga/FPGA.md` or
`asic/ASIC.md` when those source folders exist; sub-IPs nest the same way.
The [wiki BKM](../bkm/wiki.md#page-set) owns the page set, and
`./build check` enforces it.

Filelists use plain Verilator `.f` syntax: one path per line, `//` comments,
paths relative to the filelist, and only `-F`, `+incdir+` and `+define+`.
Order is explicit (packages, leaves, tops) and globs are not allowed.

## Naming

Directories use the bare IP name (`src/<ip>/<subip>/`). Files, modules and
packages use `<prefix><ip>[_<subip>]`; shared blocks use `common_`.

## Generated output

All generated output goes under `workdir/` in the worktree that produced it.
Never commit logs, waves, reports, netlists or bitstreams; summarize evidence
in the PR.
