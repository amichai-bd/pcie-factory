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
├── src/                         # kind-first: src/<kind>/<ip>/
│   ├── README.md                # declares the design target and prefix
│   ├── lint_waivers.yaml        # reviewed Verilator waivers (optional; checks SPEC)
│   ├── rtl/
│   │   ├── common/              # shared synthesizable blocks (never an IP)
│   │   ├── <ip>/                # <ip>.rtl.f + synthesizable sources
│   │   └── <ip>_<sub>/          # a sub-IP: a flat sibling with a prefixed id
│   ├── dv/
│   │   ├── common/              # shared DV Python and SV (never an IP)
│   │   └── <ip>/                # <ip>.dv.f, <ip>.sim.f, tb/, tests/, models/, regress/
│   ├── arch/<ip>/               # top-level IPs: Python architecture models, perf/
│   ├── fpga/<ip>/               # top-level synthesis targets: SDC, QSF Tcl, board wrapper
│   └── asic/<ip>/               # top-level synthesis targets: SDC, flow config
├── wiki/
│   ├── index.md
│   ├── project/                 # architecture, layout, roadmap, decisions/
│   ├── bkm/                     # rtl, dv, arch, devops, wiki, fpga, pd
│   ├── tools/<tool>/SPEC.md
│   ├── src/                     # mirrors src/: rtl/<ip>/MAS.md, dv/<ip>/DV_PLAN.md,
│   │                            #   arch/<ip>/HAS.md, fpga/<ip>/FPGA.md, asic/<ip>/ASIC.md
│   └── tracker/                 # index.md, data/
├── worktrees/                   # issue worktrees (ignored except README)
└── workdir/                     # generated output (ignored)
```

## Neutrality boundary

Only `src/` and `wiki/src/` name the design target. Everything else is
generic; tools discover IPs by walking `src/`. Exempt: the repository slug in
URLs and nightly data in `wiki/tracker/data/`. `src/README.md` declares the
target and the prefix `<prefix>` used below.

## IPs and sub-IPs

An IP is a folder `src/rtl/<id>/` holding `<id>.rtl.f`; its id names its
folder under every kind. An IP is a **sub-IP** of every IP whose `.rtl.f`
reaches its `.rtl.f` through `-F` (directly or through another sub-IP); a
**top-level IP** is one no other IP reaches. Sub-IPs are flat siblings, never
nested: a sub-IP's id is a reaching IP's id plus `_<sub>`
(`src/rtl/<ip>_<sub>/`). `common/` under `rtl/` and `dv/` holds shared blocks
and is never an IP.

## Per-IP files

| File | Rule |
| --- | --- |
| `rtl/<ip>/<ip>.rtl.f` | Synthesizable sources only. Single source of truth for simulation, FPGA and ASIC. Includes sub-IP and common lists with `-F`. The only filelist in `rtl/<ip>/`. |
| `dv/<ip>/<ip>.dv.f` | Simulation-only SystemVerilog (testbench tops, bind and assertion files, SV models). |
| `dv/<ip>/<ip>.sim.f` | `-F ../../rtl/<ip>/<ip>.rtl.f` plus `-F <ip>.dv.f`. |
| `dv/<ip>/regress/level{0,1,2}.yaml` | This IP's own tests per level. Levels are exclusive; sub-IP tests are not inherited. |
| `fpga/<ip>/`, `asic/<ip>/` | Top-level IPs only. Constraints and flow config only; generated output goes to `workdir/`. |
| `arch/<ip>/` | Top-level IPs only: `<prefix><ip>_arch.py` (Python architecture model, not verification), its self-tests and `perf/<scenario>.yaml`; required once the HAS defines targets ([arch BKM](../bkm/arch.md)). |

`./build check` enforces the source layout (its `layout` check) and the
wiki mirror.

## Wiki mirror

`wiki/src/` mirrors `src/` exactly: `rtl/<ip>/MAS.md` and `dv/<ip>/DV_PLAN.md`
for every IP, `arch/<ip>/HAS.md` for every top-level IP, and
`fpga/<ip>/FPGA.md` or `asic/<ip>/ASIC.md` when `src/fpga/<ip>/` or
`src/asic/<ip>/` exists. The [wiki BKM](../bkm/wiki.md#page-set) owns the
page set.

Filelists use plain Verilator `.f` syntax: one path per line, `//` comments,
paths relative to the filelist, and only `-F`, `+incdir+` and `+define+`.
Order is explicit (packages, leaves, tops) and globs are not allowed.

## Naming

Folders use the IP id (`src/<kind>/<ip>/`, a sub-IP `<ip>_<sub>`). Files,
modules and packages use `<prefix><id>`; shared blocks use `common_`.

## Generated output

All generated output goes under `workdir/` in the worktree that produced it.
Never commit logs, waves, reports, netlists or bitstreams; summarize evidence
in the PR.
