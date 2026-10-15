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
5. Check `src/rtl/common/` before writing a generic block; add a `common_`
   block only when a second consumer exists. A clock-domain crossing uses a
   crossing cell there ([crossings](../../../wiki/bkm/rtl.md#clock-domain-crossings)).

## 2. While editing

- One module per file, named after the module; types and shared constants in
  the package ([packages](../../../wiki/bkm/rtl.md#types-and-constants-live-in-packages)).
- Apply the [SystemVerilog rules](../../../wiki/bkm/rtl.md#systemverilog-rules)
  and [reset and clocks](../../../wiki/bkm/rtl.md#reset-and-clocks) as you
  write; do not leave them for the checker.
- Simulation-only checkers go in bind modules listed in `dv/<ip>/<ip>.dv.f`
  ([assertions](../../../wiki/bkm/rtl.md#assertions)); hand those to
  [dv-coder](../dv-coder/SKILL.md) if they need test changes.
- No `ifdef` for behavior, no lint pragmas in RTL, no flow-specific copies
  of a source list.

## 3. Filelists

Update `src/rtl/<ip>/<ip>.rtl.f` in the same edit as the source:

- [ ] New file listed once, in order: packages, leaves, then the IP top.
- [ ] Sub-IP and `common_` blocks included with `-F <their>.rtl.f`, never by
      listing their files.
- [ ] Removed or renamed file removed from the list; no orphan `.sv` left in
      `rtl/<ip>/`.
- [ ] A new sub-IP is a flat sibling with a prefixed id, `src/rtl/<ip>_<sub>/`
      with its own `<ip>_<sub>.rtl.f` (reached from the parent with `-F`),
      `src/dv/<ip>_<sub>/` lists and two wiki pages (see
      [wiki-spec-writer](../wiki-spec-writer/SKILL.md)); never a nested folder.
- [ ] Only `-F`, `+incdir+`, `+define+`, relative paths, no globs
      ([layout](../../../wiki/project/layout.md#per-ip-files)).

## 4. Check, cheapest first

```text
./build check                                  # layout, filelists, RTL style, wiki mirror, flow inputs, neutrality, citation, lint -Wall
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
