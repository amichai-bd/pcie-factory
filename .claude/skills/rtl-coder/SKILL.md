---
name: rtl-coder
description: Use before and while adding or changing synthesizable SystemVerilog under src/rtl/ or an <ip>.rtl.f filelist - new modules, packages, sub-IPs, interface or behavior changes, lint or style fixes - to keep RTL, filelists, checks and the owning HAS and MAS aligned in one PR.
---

# RTL coder

<!-- Provenance: adapted from nand2anything .agents/skills/rtl-coder/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

The [RTL BKM](../../../wiki/bkm/rtl.md) owns SystemVerilog style, naming,
reset, assertions and lint rules; the [layout page](../../../wiki/project/layout.md#per-ip-files)
owns file placement and filelist format. This skill is the procedure; it never
redefines those rules. Work inside an issue worktree per
[agent-flow](../agent-flow/SKILL.md).

## 1. Before editing

1. Read the owning specs in `wiki/src/`, which mirrors `src/`:
   `arch/<ip>/HAS.md` (top-level IPs: function, interfaces, parameters,
   behavior, targets), `rtl/<ip>/MAS.md` (pipeline, state, RTL structure,
   timing, files; for a sub-IP also its interfaces and behavior) and
   `dv/<ip>/DV_PLAN.md` (which tests cover what you will touch).
2. If the spec is missing or silent on the behavior or structure you are
   changing, write the minimal HAS or MAS change first with
   [wiki-spec-writer](../wiki-spec-writer/SKILL.md).
   Code follows the spec, never the reverse.
3. Read the prefix from [`src/README.md`](../../../src/README.md); names
   follow the [naming table](../../../wiki/bkm/rtl.md#naming).
4. Find every consumer of what you change: instantiations
   (`grep -rn '<module>' src/`), filelists that reach the file through `-F`,
   bind checkers in `dv/<ip>/<ip>.dv.f`, `fpga/<ip>/` and `asic/<ip>/` constraints, and the
   tests named in the DV plan.
5. Check `src/rtl/common/` and its group folders (`cdc/` crossing cells,
   `ram/` the RAM family) before writing a generic block; add a `common_`
   block only when a second consumer exists, in the group folder its name
   matches ([shared blocks](../../../wiki/project/layout.md#shared-blocks)). A clock-domain crossing uses a
   crossing macro (`COMMON_METAFLOP`, `COMMON_METAFLOP_VAL`,
   `COMMON_PULSE_SYNC`), which instantiates the crossing cell; outside
   `src/rtl/common/` never instantiate a wrapped cell directly
   ([crossings](../../../wiki/bkm/rtl.md#clock-domain-crossings)).

## 2. While editing

Write to the four [design rules](../../../wiki/bkm/rtl.md#design-rules)
from the first line; `./build check` fails on each:

- [ ] Every register, array and memory port is a `COMMON_DFF*` /
      `COMMON_MEM*` macro with named operands; no `always_ff`, no `<=`
      ([registers and memories](../../../wiki/bkm/rtl.md#registers-and-memories)).
- [ ] Every assertion and parameter check is a `COMMON_ASSERT*` macro; no
      `assert`, `$error`, `$fatal`, `initial`, and no system task at all
      (`$display`, `$finish`, `$stop`, `$readmemh`, ...; only pure functions
      such as `$clog2`, `$bits`, `$signed`)
      ([assertions](../../../wiki/bkm/rtl.md#assertions)).
- [ ] No `` `ifdef``/`` `ifndef``/`` `elsif``/`` `else`` and no
      `` `define``/`` `undef`` in a `.sv`
      ([no conditional compilation](../../../wiki/bkm/rtl.md#no-conditional-compilation)).
- [ ] Types (every `enum` and `struct`, FSM states included) and shared
      constants in the package, named `<pkg>::<item>`; no `import`
      ([packages](../../../wiki/bkm/rtl.md#types-and-constants-live-in-packages)).

Review enforces two more from the start:

- [ ] Related signals that cross a module boundary inside the IP, and
      multi-field state, are packed structs from the IP's package
      (`common_pkg` when two IPs use the type); flat `logic` only for
      single-meaning signals, top ports mirroring an external interface
      the HAS names, and parameter-width fields
      ([structs](../../../wiki/bkm/rtl.md#structs-at-interfaces)).
- [ ] A new leaf block is a file in `src/rtl/<ip>/`, in the IP's filelist,
      MAS and testbench; never a new sub-IP, package or testbench of its own
      without captain approval
      ([granularity](../../../wiki/bkm/rtl.md#granularity)).

Then:

- `` `include "common_macros.svh"`` after `` `default_nettype none`` in every
  file that calls a macro, and `-F ../common/common_macros.rtl.f` in its
  filelist ([includes](../../../wiki/bkm/rtl.md#includes)).
- One module per file, named after the module.
- Only `common_macros.svh` and `common_assertions.svh` are exempt from the
  design rules. A new vocabulary header, or a new form in one, is a
  reviewed change: name it in the checker's list and say so in the PR.
- Apply the [SystemVerilog rules](../../../wiki/bkm/rtl.md#systemverilog-rules)
  and [reset and clocks](../../../wiki/bkm/rtl.md#reset-and-clocks) as you
  write; do not leave them for the checker.
- Invariants of the module go in it as `COMMON_ASSERT*` macros; checkers that
  need testbench state go in bind modules listed in `dv/<ip>/<ip>.dv.f`
  ([assertions](../../../wiki/bkm/rtl.md#assertions)); hand those to
  [dv-coder](../dv-coder/SKILL.md) if they need test changes.
- Deliberately unused bits go through `COMMON_UNUSED_BITS`; no lint
  pragmas in RTL, no flow-specific copies of a source list.

## 3. Filelists

Update `src/rtl/<ip>/<ip>.rtl.f` in the same edit as the source:

- [ ] New file listed once, in order: the vocabulary block
      (`-F ../common/common_macros.rtl.f`) and any crossing cell's filelist
      (`-F ../common/cdc/<cell>.rtl.f`),
      packages, leaves, then the IP top.
- [ ] Sub-IP and `common_` blocks included with `-F <their>.rtl.f`, never by
      listing their files.
- [ ] Removed or renamed file removed from the list; no orphan `.sv` left in
      `rtl/<ip>/`.
- [ ] A captain-approved sub-IP ([granularity](../../../wiki/bkm/rtl.md#granularity))
      is a flat sibling with a prefixed id, `src/rtl/<ip>_<sub>/`
      with its own `<ip>_<sub>.rtl.f` (reached from the parent with `-F`),
      `src/dv/<ip>_<sub>/` lists and two wiki pages (see
      [wiki-spec-writer](../wiki-spec-writer/SKILL.md)); never a nested folder.
- [ ] Only `-F`, `+incdir+`, `+define+`, relative paths, no globs
      ([layout](../../../wiki/project/layout.md#per-ip-files)).

## 4. Check, cheapest first

```text
./build check                                  # layout, filelists, RTL style, wiki mirror, flow inputs, neutrality, citation, publishability, lint -Wall, cdc
./build sim --ip <id> --test <name> [--seed N] [--waves]   # focused test
./build regress --level 0 --level 1 --affected origin/main   # before review
```

The [build SPEC](../../../wiki/tools/build/SPEC.md#simulation-build-sim)
owns `sim` and the [regress SPEC](../../../wiki/tools/regress/SPEC.md)
owns `regress`; `<id>` is the IP id (`<ip>` or a sub-IP's `<ip>_<sub>`). The [checks SPEC](../../../wiki/tools/checks/SPEC.md)
owns `check`; narrow it with `--ip <id>` and `--only lint|style|filelist`
while iterating, then run it in full before review.

- Every warning is a failure. Fix the cause; a waiver goes only in the
  reviewed waiver file with a reason
  ([lint and style](../../../wiki/bkm/rtl.md#lint-and-style-checks)).
- Run the focused test that exercises the change, including reset and
  boundary cases, before any wider regression
  ([fast test ladder](../../../wiki/bkm/dv.md#fast-test-ladder)).
- A behavior change with no test is incomplete: add or extend one with
  [dv-coder](../dv-coder/SKILL.md) and the DV plan row in the same PR.

## 5. Before the PR

- [ ] `arch/<ip>/HAS.md` (sub-IP: `rtl/<ip>/MAS.md`) matches the RTL: ports, widths,
      parameters, reset values and behavior. `rtl/<ip>/MAS.md` matches the
      structure, pipeline, latency and the Files table.
- [ ] `dv/<ip>/DV_PLAN.md` and regression lists updated if coverage changed.
- [ ] `fpga/<ip>/` and `asic/<ip>/` constraints still name existing ports and
      clocks, and `fpga/<ip>/FPGA.md` / `asic/<ip>/ASIC.md` still describe them.
- [ ] The [RTL review checklist](../../../wiki/bkm/rtl.md#review-checklist)
      holds.
- [ ] Local evidence per the [DevOps BKM](../../../wiki/bkm/devops.md#local-evidence)
      recorded at the head SHA, then [pr-creation](../pr-creation/SKILL.md).
