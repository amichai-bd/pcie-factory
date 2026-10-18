---
name: dv-coder
description: Use before and while writing or changing verification under src/dv/ - cocotb tests, Python reference models, testbench tops and bind checkers, <ip>.dv.f and <ip>.sim.f, regress/level{0,1,2}.yaml entries and seeds - and when aligning an IP's DV_PLAN with its regression lists.
---

# DV coder

<!-- Provenance: adapted from nand2anything .agents/skills/dv-python/SKILL.md
     (commit 085ef4b, same owner, no license file); rewritten design-neutral. -->

The [DV BKM](../../../wiki/bkm/dv.md) owns DV method, directory roles,
regression-list rules, determinism and evidence; the
[layout page](../../../wiki/project/layout.md#per-ip-files) owns file names.
This skill is the procedure; it never redefines those rules. Work inside an
issue worktree per [agent-flow](../agent-flow/SKILL.md).

## 1. Before writing a test

1. Read the behavior under test in `wiki/src/arch/<ip>/HAS.md` (for a
   sub-IP, `wiki/src/rtl/<ip>/MAS.md`) and the row you are covering
   in `wiki/src/dv/<ip>/DV_PLAN.md`. The oracle comes from that spec, never from the RTL ([scope and oracle](../../../wiki/bkm/dv.md#scope-and-oracle)).
2. No plan row for the feature? Add it first with
   [wiki-spec-writer](../wiki-spec-writer/SKILL.md). A spec that is too vague
   to write an independent model is a spec finding, not a test guess.
3. Name the regression this test catches that existing tests miss
   ([test value](../../../wiki/bkm/dv.md#test-value)). If none, extend an
   existing test instead.
4. Check `src/dv/<ip>/tb/`, `models/` and `src/dv/common/` for helpers before
   adding infrastructure.

## 2. Writing

Place files under `src/dv/<ip>/` per the [directory structure](../../../wiki/bkm/dv.md#directory-structure):

| Change | Where |
| --- | --- |
| Pure-Python reference model | `models/`, with pytest unit tests that need no simulator |
| Drivers, monitors, scoreboards, clock/reset helpers | `tb/` (Python) |
| SV testbench top, bind or assertion module | `tb/`, listed in `<ip>.dv.f` |
| cocotb testcases | `tests/test_<feature>.py` |

- SV under `dv/` may use the RTL vocabulary (`` `include "common_macros.svh"``,
  reached through the RTL filelist) and may also write raw assertions; the
  [design rules](../../../wiki/bkm/rtl.md#design-rules) bind RTL only.
  Simulation builds pass `--assert`, so an RTL `COMMON_ASSERT*` that fails
  stops the test: a test never drives a module outside the contract its
  assertions state, unless it is a negative test outside the regression.
- Drive and observe documented ports only; bind checkers use internal
  signals the MAS names. Never read Verilator internals.
- Bound every test in cycles and wall time; mismatch messages give operation,
  expected, observed and cycle or transaction index.
- Random tests take their seed from the regression entry, print it, and draw
  all randomness from it ([determinism](../../../wiki/bkm/dv.md#determinism-and-failure-quality)).
- Include a check the test could fail on: a negative case or an after-drain
  check where a pass could otherwise be vacuous.
- Cover the plan's features with `regress.funcov` covergroups (named bins
  and crosses from the spec, sampled where stimulus is driven or results
  observed); see [coverage](../../../wiki/bkm/dv.md#coverage) and the
  [helper API](../../../wiki/tools/regress/SPEC.md#functional-coverage).

## 3. Filelists

- [ ] `dv/<ip>/<ip>.dv.f` lists simulation-only SV in order; same `.f` rules as
      RTL ([filelists](../../../wiki/bkm/rtl.md#filelists)). Never list
      synthesizable RTL here.
- [ ] `dv/<ip>/<ip>.sim.f` stays exactly `-F ../../rtl/<ip>/<ip>.rtl.f` then
      `-F <ip>.dv.f`.
- [ ] A new sub-IP (captain-approved only) gets its own flat
      `src/dv/<ip>_<sub>/` with both lists and all three regression files. A
      leaf block gets no testbench: it is tested through its IP's ports
      ([granularity](../../../wiki/bkm/rtl.md#granularity)).
- [ ] A new shared block is added to `dv/common/common.sim.f` and tested in
      the shared unit (`--ip common`), with its own DV plan
      `wiki/src/dv/common/[<group>/]<block>.md` (the block's group folder)
      ([layout](../../../wiki/project/layout.md#shared-blocks)).

## 4. Regression lists

Edit `src/dv/<ip>/regress/level{0,1,2}.yaml` per
[regression lists](../../../wiki/bkm/dv.md#regression-lists). The
[regress SPEC](../../../wiki/tools/regress/SPEC.md#regression-lists)
owns the exact schema, validation and defaults.

- [ ] Entry in exactly one level, chosen by the
      [budgets](../../../wiki/bkm/devops.md#levels-and-budgets); never
      duplicated to "also run" it.
- [ ] Entry in this IP's own lists only; a parent does not inherit sub-IP
      entries.
- [ ] Required `name`, `module`, `top` and `timeout_s` set; `name` unique
      across all three levels of the IP and not a reserved name (`build`,
      `fpga`, `asic`, `arch`, `coverage`).
- [ ] `seeds` is a count (default 1, derived seeds) or an explicit list,
      per the [regress SPEC](../../../wiki/tools/regress/SPEC.md#seeds);
      record the printed seeds as evidence.
- [ ] A level with no tests keeps its file with `tests: []`
      ([regression lists](../../../wiki/bkm/dv.md#regression-lists)).
- [ ] Optional `coverage_floors` (percent of `line`, `toggle`,
      `functional`) only once the DV plan has no gaps, at or just below the
      merged value ([coverage](../../../wiki/bkm/dv.md#coverage); schema in the
      [regress SPEC](../../../wiki/tools/regress/SPEC.md#coverage-floors)).

## 5. DV plan alignment

In the same PR, make `wiki/src/dv/<ip>/DV_PLAN.md` and the regression lists
agree ([DV plan alignment](../../../wiki/bkm/dv.md#dv-plan-alignment)):

- [ ] Every plan row names an entry and level that exist in exactly that file.
- [ ] Every regression entry maps to at least one plan row.
- [ ] Features without a test are listed with their owning issue.

A quick cross-check: list every `name:` in the three YAML files and every
entry name in the plan table, and compare both sets by level.

## 6. Run, cheapest first

Follow the [fast test ladder](../../../wiki/bkm/dv.md#fast-test-ladder):

```text
python3 -m pytest src/dv/<ip>/models               # model unit tests
./build sim --ip <id> --test <name> [--seed N] [--waves]   # focused test
./build regress --level 0 --level 1 --affected origin/main --dry-run   # what runs
./build regress --level 0 --level 1 --affected origin/main --jobs 4 --slots <dir>  # before review
./build sim --ip <id> --test <l2-entry> --seed N          # targeted L2, only when needed
```

PR evidence is the affected L0+L1 (`ci-gate` runs it and may be cited),
targeted L2 entries only when the change targets L2 behaviour or a reviewer
asks, and mutants only of the code you changed. Never run all of L2 or
all-IP coverage for a PR: the nightly does, and opens an issue on a floor
miss or L2 failure ([local evidence](../../../wiki/bkm/devops.md#local-evidence)).
On a shared host pass the host's CPU slot directory (`--slots <dir>` or
`$BUILD_SLOTS`) to every `sim` and `regress`; run mutants serially
(`--jobs 1`). Measure coverage (`--coverage` on the IP's level holding the
floor) only to set or raise a floor.

The [build SPEC](../../../wiki/tools/build/SPEC.md#simulation-build-sim)
and [regress SPEC](../../../wiki/tools/regress/SPEC.md)
own the syntax. `sim` defaults to the test's first regression seed (the
first list entry, or derived seed 0 for a count), and `--seed <s>`
reproduces any regression run. Rerun a failing seed alone to confirm it
reproduces before and after the fix. Outputs stay under `workdir/`.

## 7. Before the PR

- [ ] Every new test passes on all its seeds, and fails when the checked
      behavior is broken (try a one-line local break, then revert).
- [ ] Warnings fail unless a reviewed waiver explains them.
- [ ] PR evidence: the affected L0+L1 (or the cited `ci-gate` run), any
      targeted L2 entries, and mutants of the changed code: commands, seeds,
      pass/fail counts, runtime and every non-pass entry with diagnosis, at
      the head SHA
      ([output and evidence](../../../wiki/bkm/dv.md#output-and-evidence)),
      then [pr-creation](../pr-creation/SKILL.md).
