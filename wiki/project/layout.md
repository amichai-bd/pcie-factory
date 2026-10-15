# Repository layout

```
.
├── CLAUDE.md                    # agent contract (normative; no AGENTS.md)
├── README.md                    # human overview
├── LICENSE                      # Apache-2.0 license text
├── NOTICE                       # copyright and third-party notice
├── .gitignore                   # ignores workdir/, worktrees/ and caches
├── .gitattributes               # line endings; binary and vendored files
├── build                        # single entry point → tools/
├── .claude/                     # settings.json, hooks/, skills/<name>/SKILL.md
├── .github/                     # workflows/{ci,nightly,pages,fpga,asic}.yml, templates
├── cfg/                         # generic config: levels, ci, host, checks, labels
├── tools/                       # generic Python tools + unit tests
├── src/                         # kind-first: src/<kind>/<ip>/
│   ├── README.md                # declares the design target and prefix
│   ├── lint_waivers.yaml        # reviewed Verilator waivers (optional; checks SPEC)
│   ├── specs.yaml               # reference documents cached outside the repo (specs SPEC)
│   ├── rtl/
│   │   ├── common/              # shared blocks: common_<name>.sv, <block>.rtl.f, the
│   │   │                        #   vocabulary headers common_*.svh (never an IP)
│   │   ├── <ip>/                # <ip>.rtl.f + synthesizable sources
│   │   └── <ip>_<sub>/          # a sub-IP: a flat sibling with a prefixed id
│   ├── dv/
│   │   ├── common/              # the shared unit: common.{dv,sim}.f, tb/, tests/, regress/
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
│   │                            #   arch/<ip>/HAS.md, fpga/<ip>/FPGA.md, asic/<ip>/ASIC.md,
│   │                            #   {rtl,dv}/common/<block>.md;
│   │                            #   plus README.md, roadmap.md, references.md,
│   │                            #   decisions/NNNN-<slug>.md
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
folder under every kind. An IP is a **sub-IP** of a parent only when both
hold: its id is `<parent>_<name>`, and the parent's `.rtl.f` reaches its
`.rtl.f` through `-F` (directly or through another IP). Every other IP is
**top-level**. Sub-IPs are flat siblings, never nested
(`src/rtl/<ip>_<sub>/`). The name is a claim the filelists must confirm: an
IP whose id is `<other>_<name>`, for another IP `<other>`, must be reached by
`<other>`'s `.rtl.f`; otherwise the layout check fails until it is included
with `-F` or renamed. `common/` under `rtl/` and `dv/` holds shared blocks
and is never an IP.

A leaf block is a file in its IP's folder, not a sub-IP: a sub-IP is made
only for a substantial, independently verified block that the captain
approves, and testbenches exist per IP and for the shared unit only
([granularity](../bkm/rtl.md#granularity)).

A top-level IP may **integrate** other top-level IPs through `-F`
(`sys.rtl.f` including `a.rtl.f` and `b.rtl.f`). They stay top-level, with
their own HAS, `arch/`, `fpga/` and `asic/`, and `regress --affected` still
selects the integrating IP when one of them changes
([regress selection](../tools/regress/SPEC.md#selection);
Q19 of [decision record 0002](../src/decisions/0002-m1-grill.md#decisions)).

## Shared blocks

`src/rtl/common/` holds the shared blocks, IP-neutral cells that IPs reuse
(`common_<name>`), and the shared project package, a package-only shared
block for types and constants used by more than one IP
([packages](../bkm/rtl.md#types-and-constants-live-in-packages)). It is never an IP: no HAS, no `arch/`, `fpga/` or `asic/`
folder, and no IP reaches it as a sub-IP. A **shared block** is one
filelist `rtl/common/<block>.rtl.f`, directly in the folder and named after
the block's top module; it lists the block's sources and includes the
blocks it uses with `-F`. An IP includes a block the same way, from its own
`.rtl.f`. The `.svh` files directly in `rtl/common/` are the shared RTL
vocabulary headers ([RTL BKM](../bkm/rtl.md#the-rtl-vocabulary)); their
block `common_macros.rtl.f` is **include-only** (`+incdir+.`, no sources)
and every filelist whose sources use the vocabulary includes it with `-F`.

The shared blocks are verified together as the **shared unit**, id
`common`, laid out like an IP's DV folder:

| File | Rule |
| --- | --- |
| `dv/common/common.dv.f` | Simulation-only SystemVerilog for the blocks' tests. |
| `dv/common/common.sim.f` | `-F ../../rtl/common/<block>.rtl.f` for every block, include-only ones too (so every block is tested), then `-F common.dv.f`. |
| `dv/common/tb/`, `tests/`, `models/`, `regress/level{0,1,2}.yaml` | As for an IP; tests name the block as `top`, and `./build ci unit` runs the `models/` tests. |

`./build sim`, `regress` and `clean` take `--ip common`; output goes to
`workdir/common/`. IP tests cannot import from `dv/common/`: `sim` puts
only the tested unit's own DV folder on the Python path. Each block has a MAS `wiki/src/rtl/common/<block>.md` and
a DV plan `wiki/src/dv/common/<block>.md`
([wiki BKM](../bkm/wiki.md#page-set)).

## Per-IP files

| File | Rule |
| --- | --- |
| `rtl/<ip>/<ip>.rtl.f` | Synthesizable sources only. Single source of truth for simulation, FPGA and ASIC. Includes sub-IP and common lists with `-F`. The only filelist in `rtl/<ip>/`. |
| `dv/<ip>/<ip>.dv.f` | Simulation-only SystemVerilog (testbench tops, bind and assertion files, SV models). |
| `dv/<ip>/<ip>.sim.f` | `-F ../../rtl/<ip>/<ip>.rtl.f` plus `-F <ip>.dv.f`. |
| `dv/<ip>/regress/level{0,1,2}.yaml` | This IP's own tests per level. Levels are exclusive; sub-IP tests are not inherited. |
| `fpga/<ip>/`, `asic/<ip>/` | Top-level IPs only. Constraints and flow config only; generated output goes to `workdir/`. |
| `arch/<ip>/` | Top-level IPs only: `<prefix><ip>_arch.py` (Python architecture model, not verification), its self-tests and `perf/<scenario>.yaml`; required once the HAS defines targets ([arch BKM](../bkm/arch.md)). |

`./build check` enforces the source layout (its `layout` check), the
shared unit's files (its `filelist` check) and the wiki mirror.

## Wiki mirror

`wiki/src/` mirrors `src/` exactly: `rtl/<ip>/MAS.md` and `dv/<ip>/DV_PLAN.md`
for every IP, `arch/<ip>/HAS.md` for every top-level IP, and
`fpga/<ip>/FPGA.md` or `asic/<ip>/ASIC.md` when `src/fpga/<ip>/` or
`src/asic/<ip>/` exists, and `rtl/common/<block>.md` and
`dv/common/<block>.md` for every shared block. Beside them, a fixed set of design-level pages:
`README.md`, `roadmap.md` and `decisions/NNNN-<slug>.md`. The [wiki BKM](../bkm/wiki.md#page-set) owns the
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
