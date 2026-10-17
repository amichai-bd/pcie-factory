# 0001: M0 factory decisions

- **Status:** accepted
- **Source:** captain grill session, recorded in the M0 epic (issue #1) and
  updated by its comments. This copy is faithful except that design-target
  and IP names are replaced by neutral wording (see Q2); later decisions from
  the epic's comments are listed under Updates.

## Mission

A high-quality, agent-driven chip-design factory. The design target (declared
in `src/README.md`) is the primary target, but the factory is the product.
`nand2anything` is the reference for the factory's ideas; nothing here depends
on it at runtime, and anything adapted from it carries a provenance note.

## Decisions

| # | Decision |
|---|---|
| Q1 | The factory is the product. Infra, tools, skills, CI and BKMs are design-neutral and never mention the design target. |
| Q2 | Only `src/` and `wiki/src/` name the design target. One target, laid out as IPs under `src/` (kind-first since the layout update below). Tools discover IPs from `src/` and never hard-code IP names. A neutrality check enforces this. |
| Q3 | `CLAUDE.md` is the only agent contract. There is no `AGENTS.md`. Skills live in `.claude/skills/`. |
| Q4 | Filelists (paths per the kind-first layout update): `src/rtl/<ip>/<ip>.rtl.f` (synthesizable only; the source of truth for sim, FPGA and ASIC), `src/dv/<ip>/<ip>.dv.f` (sim-only SV), and `src/dv/<ip>/<ip>.sim.f` (= `-F` rtl + `-F` dv). Plain Verilator `.f` format, paths relative to the filelist, only `-F`, `+incdir+` and `+define+`, explicit order, no globs. A resolver in `tools/` flattens them for Quartus and ORFS. |
| Q5 | Regressions live in `src/dv/<ip>/regress/level{0,1,2}.yaml` (path per the kind-first layout update). Each IP is flat: it covers its own tests only and does not inherit from sub-IPs. A global run is the union of all IPs. |
| Q6 | Levels are **exclusive**: `--level 1` runs only `level1.yaml`, and callers combine them with `--level 0 --level 1`. |
| Q7 | `ci-gate` runs the checks plus L0+L1 for affected IPs. An IP is affected if its files changed or its filelists reach a changed file through `-F`. A change to `tools/` or `cfg/`, and every push to main, runs all IPs. |
| Q8 | The single entry point is `./build`, a thin launcher at the root. The implementation lives in `tools/`. |
| Q9 | `./build check`: Verilator lint (`-Wall`, waivers only in a reviewed file with reasons), the RTL style checker (BKM rules), filelist checks (files resolve, no duplicates, no orphan RTL), the wiki mirror check (added by the wiki mirror update below), and the neutrality check. |
| Q10 | `tools/` unit tests run in `ci-gate` when `tools/` or `cfg/` change. Doc line caps are a review guideline, not a check. |
| Q11/12 | Every IP **and** sub-IP has its own wiki specs (originally HAS, MAS and DV_PLAN in one folder per IP). Specs live only in the wiki. Every src/tools change updates the wiki and vice versa, and **review** checks that they match (no automation). Superseded in part: see the wiki mirror and kind-first layout updates. |
| Q13 | The wiki site generator is a minimal fresh port. Nav follows the `wiki/` tree, and the n2a theme is reused with provenance. Published to GitHub Pages. |
| Q14 | An issue is claimed with the `in-progress` label plus a claim comment naming the session, branch and worktree. |
| Q15 | The reviewer crewmate is fresh, independent and read-only. It trusts CI and reviews the exact head for code↔wiki alignment, BKM conformance and test adequacy (DV_PLAN against the regression lists). It posts one `REVIEW-VERDICT: APPROVE\|REQUEST-CHANGES @<sha>` comment. The firstmate then auto-merges once the verdict and `ci-gate` are green. |
| Q16 | Local hosts and Claude Code cloud sessions are both supported. `.claude/hooks/session-start.sh` installs the pinned toolchain from `cfg/host.yaml` in cloud sessions. CI runs on self-hosted runners on the **Omarchy** host. Herdr may be added later. |
| Q17 | Folders use the IP id; files, modules and packages are named `<prefix><ip>[_<sub>]` with the prefix declared in `src/README.md`, and shared blocks use `common_`. Superseded in part by the kind-first layout update (a sub-IP's id is `<ip>_<sub>`). |
| Q18 | Seed design: one IP with one sub-IP (named in `wiki/src/`), one test per level each, full docs. |
| Q19 | Labels use prefixed dimensions: `type:` (exactly one), `area:` (at least one), `ip:` (derived from `src/`), `prio:`, and state labels (`in-progress`, `blocked`, `needs-captain`, `nightly-red`). Defined in `cfg/labels.yaml` and synced by `./build labels sync`. |
| Q20/21 | Metrics history lives in `wiki/tracker/data/`. The nightly bot PR may touch **only** that path; a guard check enforces it, and it auto-merges after `ci-gate`. This is the single permanent flow exception in CLAUDE.md. |
| Q22 | Tracker metrics: code stats (LOC and files by kind and IP), tests (counts, pass/fail, runtime per level, slowest tests), GitHub flow (issues, PRs, lead time, review rounds), FPGA, and ASIC. |
| Q23 | FPGA: Quartus Lite targeting the DE2-115 (Cyclone IV E EP4CE115F29C7). Runs on the Omarchy host only. |
| Q24 | ASIC: OpenROAD-flow-scripts with sky130hd. Reports WNS/TNS, area, cells, max fanout and logic depth. Runs on the Omarchy host. |
| Q25 | Per-IP physical inputs live in `src/fpga/<ip>/` and `src/asic/<ip>/` (constraints and config only; paths per the kind-first layout update), and both consume `src/rtl/<ip>/<ip>.rtl.f`. |
| Q26 | Originally nightly/on-demand only; superseded by the required vendor CI update below. |
| Q27 | The README carries badges (`ci-gate`, nightly, pages) and a link to the tracker. The bot never edits it. |
| Q28 | `/tracker/` shows charts over time plus "latest vs 7/30 days ago" tables and a page per IP. The chart library is bundled locally. |

Also carried over: Verilator + cocotb (Python DV). Levels: L0 ≤ ~2 min locally,
L1 ≤ 10 min gate, L2 nightly (full regression, seeds, FPGA, ASIC), with a
dashboard and one `nightly-red` issue that's opened or updated on failure.
Merges are squash merges. BKMs: rtl, dv, devops, wiki, fpga (Quartus/DE2-115),
pd (ORFS/sky130hd).

## Updates

- **License (captain decision, recorded on #40):** the repository is licensed
  under Apache-2.0. Vendored third-party files keep their own licenses next to
  them.
- **Public repository and site (captain decisions, recorded on #1):** the
  repository is public and the wiki is published to GitHub Pages as a public
  site. Everything committed must be publishable; reviewers check it on every
  PR.
- **Self-hosted runner safety (firstmate rule, recorded on #1):** every job a
  `pull_request` can trigger is skipped for fork PRs but still runs on push and
  dispatch, using the guard in the DevOps BKM's Continuous integration
  section; `pull_request_target` never checks out PR code; workflows never
  echo secrets. The guard is defense in depth only, since a fork's own copy
  of the workflow can drop it. Maintainers never approve a fork PR's
  workflow run; fork changes reach CI only by review and re-push to a branch
  here. The repository keeps GitHub's default fork-run approval setting and
  the captain accepts the residual risk (recorded on #1).
- **CI host:** the self-hosted runners, Quartus Lite and ORFS/sky130hd run on
  the Omarchy host (supersedes earlier "WSL host" wording). Later, #273
  added the WSL host as a second CI host and pinned the nightly to it
  ([DevOps BKM](../../bkm/devops.md#nightly)).
- **Branch protection: `ci-gate` required (captain, recorded on #1):**
  branch protection on `main` requires `ci-gate`, with `enforce_admins`. The
  merge gate is an APPROVE verdict at the head plus `ci-gate` green on that
  head. This ends the earlier temporary exception that allowed merging on
  local validation evidence before the runners were online.
- **Runner selection (firstmate decision, recorded on #1):** workflows
  target `[self-hosted, linux, x64]`; runners registered to this repository
  serve only it, so no workflow names the design target.
- **Root layout addition (firstmate decision, recorded on #1):**
  `src/README.md` declares the design target and prefix so that everything
  outside `src/` can stay neutral.
- **Wiki mirrors src; HAS and MAS defined (captain decisions, recorded on
  [#1](https://github.com/amichai-bd/pcie-factory/issues/1#issuecomment-6176791147)
  and [#1](https://github.com/amichai-bd/pcie-factory/issues/1#issuecomment-6176828568)),
  updating Q11/12:** `wiki/src/` mirrors `src/` folder by folder and
  `wiki/tools/<tool>/` mirrors `tools/<tool>/`. The HAS (High-level
  Architecture Specification: what and why) is the IP's `HAS.md` in its
  `arch` folder, for top-level IPs only; the MAS (Micro-Architecture
  Specification: how) is `MAS.md` in its `rtl` folder and the DV plan
  `DV_PLAN.md` in its `dv` folder for every IP and sub-IP; `FPGA.md` and
  `ASIC.md` exist exactly when the IP's `fpga` or `asic` source folder does.
  The `arch` source folder holds Python behavioral and performance models
  for architects, not verification; DV may import them,
  never the reverse. Delivered by
  [#55](https://github.com/amichai-bd/pcie-factory/issues/55) (`./build arch`,
  [arch BKM](../../bkm/arch.md)): a top-level IP whose HAS defines targets
  needs its `arch` source folder, and the model reports against them.
  Paths superseded by the kind-first layout update below.
  `./build check` enforces the page set and the mirror; content alignment
  stays a review item.
- **Kind-first layout (captain decision, recorded as Q18 of the M1 decision
  record in [#65](https://github.com/amichai-bd/pcie-factory/issues/65);
  delivered by [#92](https://github.com/amichai-bd/pcie-factory/issues/92),
  2026-10-14), superseding the IP-first layout in Q2, Q4, Q5, Q11/12, Q17,
  Q25 and the wiki mirror update:** sources live at `src/<kind>/<ip>/` with
  `<kind>` one of `rtl`, `dv`, `arch`, `fpga` and `asic`. Sub-IPs are flat
  siblings with prefixed ids (`src/rtl/<ip>_<sub>/`); an IP is a sub-IP of
  every IP whose `.rtl.f` reaches its `.rtl.f` through `-F`, and a top-level
  IP is one no other IP reaches. `arch`, `fpga` and `asic` exist only for
  top-level IPs; `rtl/common/` and `dv/common/` hold shared blocks.
  `wiki/src/` mirrors the tree exactly: `wiki/src/rtl/<ip>/MAS.md` and
  `wiki/src/dv/<ip>/DV_PLAN.md` for every IP, `wiki/src/arch/<ip>/HAS.md`
  for every top-level IP, and `wiki/src/fpga/<ip>/FPGA.md` and
  `wiki/src/asic/<ip>/ASIC.md` where those source folders exist.
  `./build check` enforces the layout and the mirror
  ([layout page](../layout.md)). IP ids are unchanged except that a nested
  sub-IP's id becomes `<ip>_<sub>`; tracker records keep the id that was
  current when they were written.

- **Required vendor compatibility CI (captain, 2026-10-16,
  [#243](https://github.com/amichai-bd/pcie-factory/issues/243)), superseding Q26:**
  affected synthesis targets run full Quartus compile/fitting/timing and
  pinned ORFS synthesis-only checks in parallel with simulation. The single
  required `ci-gate` requires all jobs to succeed. Full ASIC implementation
  and L2 remain nightly. Synthesis-only evidence is isolated from routed
  metrics and makes no physical timing closure claim. The
  [CI spec](../../tools/ci/SPEC.md#build-ci-vendor) owns selection.
