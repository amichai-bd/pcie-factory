# 0001: M0 factory decisions

- **Status:** accepted
- **Source:** captain grill session; M0 epic (issue #1), updated by its comments.

The factory is the product and must stay design-neutral. Each row is a
decision; later decisions supersede earlier ones only through a new record.

| # | Decision |
|---|---|
| Q1 | The factory is the product. Infra, tools, skills, CI and BKMs never name the design target. |
| Q2 | Only `src/` and `wiki/src/` name the design target. One target, laid out flat as `src/<ip>/`. Tools discover IPs from `src/`. A neutrality check enforces this. |
| Q3 | `CLAUDE.md` is the only agent contract; no `AGENTS.md`. Skills live in `.claude/skills/`. |
| Q4 | Filelists: `rtl/<ip>.rtl.f` (synthesizable; source of truth for sim, FPGA, ASIC), `dv/<ip>.dv.f` (sim-only SV), `dv/<ip>.sim.f` (both). Plain `.f`, relative `-F`, only `-F`/`+incdir+`/`+define+`, explicit order, no globs. A resolver flattens them for vendor flows. |
| Q5 | Regressions in `src/<ip>/dv/regress/level{0,1,2}.yaml`. Each IP is flat: its own tests only, no sub-IP inheritance. |
| Q6 | Levels are exclusive; callers combine them (`--level 0 --level 1`). |
| Q7 | `ci-gate`: checks plus L0+L1 for affected IPs (own files changed, or filelists reach a changed file). `tools/`/`cfg/` changes and pushes to `main` run all IPs. |
| Q8 | Single entry point `./build`; implementation in `tools/`. |
| Q9 | `./build check`: Verilator lint (`-Wall`, reviewed waivers), RTL style checker, filelist checks, neutrality check. |
| Q10 | `tools/` unit tests run in `ci-gate` when `tools/` or `cfg/` change. Doc line caps are a review guideline. |
| Q11/12 | Every IP and sub-IP has `wiki/src/<ip>/{MAS,HAS,DV_PLAN}.md`. Specs live only in the wiki; review enforces code/spec alignment. |
| Q13 | Wiki site: minimal fresh generator, nav from the `wiki/` tree, GitHub Pages. |
| Q14 | Claim an issue with the `in-progress` label and a claim comment. |
| Q15 | A fresh, read-only reviewer crewmate posts one `REVIEW-VERDICT: APPROVE\|REQUEST-CHANGES @<sha>`; the firstmate squash-merges when the verdict and `ci-gate` are green. |
| Q16 | Local hosts and Claude Code cloud sessions are both supported; a session-start hook provisions cloud sessions from pinned versions. CI runs on self-hosted runners on the Omarchy host. |
| Q17 | Directories use bare IP names; files, modules and packages use the prefix declared in `src/README.md`; shared blocks use `common_`. |
| Q18 | Seed design: one IP with one sub-IP, one test per level each, full specs. |
| Q19 | Labels: `type:` (one), `area:` (one or more), `ip:` (from `src/`), `prio:`, state labels; defined in `cfg/labels.yaml`, synced by `./build labels sync`. |
| Q20/21 | Metrics history in `wiki/tracker/data/`. The nightly data PR may touch only that path, is guarded, and auto-merges after `ci-gate`. |
| Q22 | Tracker metrics: code stats, tests and runtimes, GitHub flow, FPGA, ASIC. |
| Q23 | FPGA: Quartus Lite, Terasic DE2-115 (Cyclone IV E EP4CE115F29C7), on the Omarchy host. |
| Q24 | ASIC: OpenROAD-flow-scripts with sky130hd; WNS/TNS, area, cells, max fanout, logic depth. |
| Q25 | Physical inputs in `src/<ip>/fpga/` and `src/<ip>/asic/`, consuming `rtl/<ip>.rtl.f`. |
| Q26 | FPGA and ASIC flows run in nightly L2 or on demand. |
| Q27 | README: badges and a tracker link; never edited by the bot. |
| Q28 | Tracker: charts over time, latest vs 7/30-day tables, per-IP pages; chart library vendored. |

Also: Verilator with Python cocotb DV; L0 about 2 minutes locally, L1 within
10 minutes, L2 nightly with a `nightly-red` issue on failure; squash merges.
