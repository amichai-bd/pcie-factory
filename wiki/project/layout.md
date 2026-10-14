# Repository layout

```
.
├── CLAUDE.md                    # agent contract (normative)
├── README.md                    # human overview
├── build                        # single entry point → tools/
├── .claude/                     # settings.json, hooks/, skills/<name>/SKILL.md
├── .github/                     # workflows/{ci,nightly,pages,fpga}.yml, templates
├── cfg/                         # generic config: levels, ci, host, checks, labels
├── tools/                       # generic Python tools + unit tests
├── src/
│   ├── README.md                # declares the design target and prefix
│   ├── common/{rtl,dv}/         # shared blocks
│   └── <ip>/
│       ├── rtl/                 # <ip>.rtl.f + synthesizable sources
│       ├── dv/                  # <ip>.dv.f, <ip>.sim.f, tb/, tests/, models/, regress/
│       ├── fpga/                # synthesis targets only: SDC, QSF Tcl, board wrapper
│       ├── asic/                # synthesis targets only: SDC, flow config
│       └── <subip>/             # same shape, recursively
├── wiki/
│   ├── index.md
│   ├── project/                 # architecture, layout, roadmap, decisions/
│   ├── bkm/                     # rtl, dv, devops, wiki, fpga, pd
│   ├── tools/<tool>/SPEC.md
│   ├── src/<ip>/                # MAS.md, HAS.md, DV_PLAN.md, <subip>/...
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
