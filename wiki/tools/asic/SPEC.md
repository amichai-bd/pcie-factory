# ASIC tool: `./build asic`

`./build asic` runs OpenROAD-flow-scripts (ORFS) from RTL to a routed design
for one IP and writes `metrics.json`. With `--synth-only`, it stops after
synthesis and writes separate compatibility evidence. The [PD BKM](../../bkm/pd.md) owns the
method (inputs, acceptance ladder, review). Code: `tools/asic/` (`cli.py`,
`flow.py`, `metrics.py`, `extra_metrics.tcl`, `clock_slacks.tcl`); workflow:
[`.github/workflows/asic.yml`](../../../.github/workflows/asic.yml).

```
./build asic --ip ID [--platform sky130hd] [--timeout S] [--synth-only] [--dry-run]
             [--study-clock-period NS] [--src-root DIR] [--workdir DIR]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | Required. A top-level IP id with a `src/asic/<ip>/` folder. |
| `--platform` | ORFS platform; only `sky130hd` is supported. |
| `--timeout S` | Wall-clock budget for all ORFS steps together (default 3600 s). |
| `--synth-only` | Run only ORFS `synth`, in `workdir/<ip>/asic-synth/`; no routed metrics. |
| `--study-clock-period NS` | Explicit full-flow clock study in a finite positive period (ns), for an opted-in SDC; isolated from default evidence. Cannot combine with `--synth-only`. |
| `--dry-run` | Write `config.mk`, print every command, run nothing. `./build check` runs the same input and ORFS-settings validation for every ASIC target, writing nothing ([flow-input check](../checks/SPEC.md#flow-input-check)). |

Exit codes: `0` the flow passed and every required output was validated; `1`
a step failed, timed out, logged an error in synthesis-only or study mode, or required
evidence is missing (the full flow still writes `metrics.json`; synthesis-only
writes `synthesis.json`); `2` a usage or configuration error.

## Inputs

| File | Use |
| --- | --- |
| `src/rtl/<ip>/<ip>.rtl.f` | Flattened by the [filelist resolver](../build/SPEC.md#filelists) into `VERILOG_FILES`, `VERILOG_INCLUDE_DIRS` and `VERILOG_DEFINES` (`-DNAME` or `-DNAME=VALUE`), in filelist order. `VERILOG_DEFINES` ends with `-DSYNTHESIS`, so the [assertion macros](../../bkm/rtl.md#assertions) compile out; a filelist that defines `SYNTHESIS` itself is an input error. |
| `src/asic/<ip>/<platform>.mk` | Design knobs, included first by the generated config (after `ASIC_INPUT_DIR`). `DESIGN_NAME` is required. It may name other files of `src/asic/<ip>/` through `$(ASIC_INPUT_DIR)`, such as a `PDN_TCL`, and macro views (`ADDITIONAL_LEFS`, `ADDITIONAL_LIBS`, `ADDITIONAL_GDS`) from the image. |
| `cfg/backends.yaml` `asic` | The ASIC [source backends](#backends) (suffix `sky130`). |
| `src/asic/<ip>/*.sdc` | Exactly one SDC; becomes `SDC_FILE`. Its first `create_clock -period` is reported as `clock_period_ns`. |
| `cfg/host.yaml` `hosts.<name>.orfs_*` | How to reach ORFS (below). |

The tool owns `PLATFORM`, `DESIGN_NICKNAME`, `DESIGN_CONFIG`, `SDC_FILE`,
`VERILOG_FILES`, `VERILOG_INCLUDE_DIRS`, `VERILOG_DEFINES`, `WORK_HOME`,
`FLOW_VARIANT`, `NUM_CORES`, `RESULTS_DIR`, `LOG_DIR`, `REPORTS_DIR`,
`OBJECTS_DIR` and `ASIC_INPUT_DIR` (the IP's `src/asic/<ip>/` folder,
exported before the `.mk` is included); a `.mk` that sets any of them is a
configuration error. The
check reads assignments (`=`, `:=`, `::=`, `:::=`, `?=`, `+=`, `!=`, with
`export` and/or `override`) and `define NAME` blocks, with line continuations
joined; it does not evaluate make.

**SystemVerilog frontend.** Yosys's built-in reader handles only part of
SystemVerilog (package imports in a module header, typed parameters). An IP
whose RTL needs more sets `SYNTH_HDL_FRONTEND = slang` in its `.mk`; ORFS
then reads all sources in one `read_slang` call (slang is built into the
pinned image's Yosys). The tool passes the knob through unchanged.

### Backends

`VERILOG_FILES` is the flattened filelist with the `asic` entry of
[`cfg/backends.yaml`](../../../cfg/backends.yaml) applied by
`build.backends.select`: each source with a backend file
`<stem>_<suffix>.sv` beside it (suffix `sky130`) is replaced by that file,
in place, so filelist order is kept
([source backends](../../project/layout.md#source-backends)). A filelist
that lists a backend file, or a bad `cfg/backends.yaml`, exits 2 before
anything runs (`./build check` runs the same validation). The command
prints `[asic] backend: <new> replaces <old>` for each. A backend's macro
views are design knobs in the IP's `.mk`.

## ORFS runners

| Runner | Runs | Selected by |
| --- | --- | --- |
| `docker` | `docker run --rm` of the image with ORFS at `/OpenROAD-flow-scripts`, as the calling user (`--user uid:gid`, `HOME=/tmp`), hardened for a shared host: `--network=none`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit=4096`, `--cpus=<cores>`, `--memory=6g --memory-swap=6g` (6 GB, swap included, under the captain's decision of 2026-10-17 in the [M2 record](../../src/decisions/0003-m2-grill.md#updates)). The repository and an outside source root are mounted read-only and the workdir read-write, each at its own path, so generated absolute paths work unchanged. | The host's `orfs_image` |
| `native` | `bash -c` on the host, after `source <root>/env.sh` when it exists. | `ORFS_ROOT`, else the host's `orfs_root` |

Host values come from the one `hosts.<name>` entry in
[`cfg/host.yaml`](../../../cfg/host.yaml) that records `orfs_image` or
`orfs_root` (more than one is an error):

| Key | Meaning |
| --- | --- |
| `orfs_image` | Docker image, required to carry `@sha256:<digest>`; a tag-only image exits 2. The image comes only from cfg: an `ORFS_IMAGE` environment variable is ignored. |
| `orfs_root` | Native ORFS checkout, if the host has one. |
| `orfs_commit` | ORFS revision of that image or root, reported as `toolchain.orfs_pin` (dropped when `ORFS_ROOT` points elsewhere). |
| `orfs_cores` | Positive integer, default 2: passed as `NUM_CORES` to ORFS and as `--cpus` to docker. |

Selection, first match wins: `ORFS_RUNNER` (`docker`|`native`) if set;
`ORFS_ROOT` set means native; a cfg image means docker; a cfg root means
native. With none of them the command exits 2. The WSL host records only the
pinned image; native mode is kept because it costs one `bash -c` and is what
the unit tests drive with a fake ORFS.

## Behavior

1. Discover the IP, validate its inputs, resolve its filelist and load the
   ORFS settings; any failure exits 2 before anything runs.
2. Delete `workdir/<ip>/asic/` (not on `--dry-run`) and write
   `config.mk` there.
3. Run four steps in order, each a separate runner invocation, appending to
   `flow.log`; the first step that fails stops the run:

   | Step | Command | Purpose |
   | --- | --- | --- |
   | `versions` | `openroad -version`, `yosys -V`, `git describe` of the ORFS root | Toolchain record |
   | `flow` | `make -C <root>/flow DESIGN_CONFIG=… WORK_HOME=… FLOW_VARIANT=base NUM_CORES=<cores> finish` | Synthesis to final report |
   | `extra` | the same `make … RUN_SCRIPT=tools/asic/extra_metrics.tcl run` | Max fanout and logic depth on `6_final.odb` |
   | `clocks` | the same `make … RUN_SCRIPT=tools/asic/clock_slacks.tcl RUN_LOG_NAME_STEM=asic_clocks run` | Setup slack per clock on `6_final.odb`, into `asic_clocks.json` |

   The `clocks` step runs last, in its own OpenROAD process, so a crash,
   OOM kill or timeout there never costs the other metrics: `metrics.json`
   still records them. Like every step, it shares the run budget, and its
   failure or timeout fails the run (`clocks: fail (exit N)`,
   `clocks: timeout after <S>s`), because `timing.clocks` is required
   ([#200](https://github.com/amichai-bd/pcie-factory/issues/200)).

4. Each step runs in its own process group. Docker containers are named
   `asic-<nick>-<12 random hex>-<step>`, unique per run because several
   runners share the host. When the shared `--timeout` budget runs out, the
   container is removed (`docker rm -f`; dockerd owns it, so killing the
   client is not enough), the group is killed and the status is
   `<step>: timeout after <S>s`. Ctrl-C or SIGTERM (a cancelled CI job)
   cleans up the same way and exits 130.
5. A step that fails or times out prints the
   [failure excerpt](../build/SPEC.md#failure-excerpt) of its part of
   `flow.log`, labelled `step <step> in <flow.log>`: every error
   line (OpenROAD `[ERROR <code>]`, Yosys `ERROR:`, OpenSTA `Error:`, make
   `make[<n>]: *** … Error <n>`), at most 50 with the total count, then the
   last 30 lines.
6. Parse the reports and write `metrics.json` and `worst_path.txt`, also on
   failure.

Everything the command prints (commands, paths, errors) shows host paths as
placeholders, because CI job logs are public: the repository as `<repo>`,
the default workdir and source root as `<repo>/workdir` and `<repo>/src`,
others as `<workdir>` and `<src>`. The commands that run, `config.mk` and
`flow.log` keep the real paths.

## Synthesis-only compatibility check

`--synth-only` validates the same inputs and uses the same pinned runner,
CPU/memory limits, read-only source mounts and timeout/cancellation cleanup.
It deletes only `workdir/<ip>/asic-synth/`, writes its config there, then
runs `versions` and `make … synth`. ORFS synthesizes the RTL and loads the
mapped netlist and constraints into an OpenROAD database; it does not run
floorplanning, placement, CTS or routing. It fails on a nonzero exit,
timeout, an error line matching the flow's error patterns even with exit
zero, or absent/empty/symlinked required outputs: `1_2_yosys.v`,
`1_synth.odb`, `1_synth.sdc` in `run/results/<platform>/<ip>/base/`.
Cleaning before execution prevents a previous run from satisfying this check.

`synthesis.json` records `schema: 1`, `stage: synthesis`, `ip`, `design`,
`platform`, `status`, the present output paths relative to the workdir,
`toolchain`, `source` and `elapsed_s`. It is written on pass or ordinary
failure. It carries no routed area, timing, worst path or closure claim.
The mode never writes `metrics.json` or `worst_path.txt`, and never changes
`workdir/<ip>/asic/`. [CI](../ci/SPEC.md#build-ci-vendor) requires this
check for affected targets; full implementation remains nightly/on demand.

## Clock studies

`--study-clock-period NS` leaves the default SDC and nightly target unchanged.
The SDC opts in with the exact comment `# build-asic: study-clock-period` and
consumes the Tcl variable `study_clock_period_ns` when present. It must apply
that period to every clock and scale its dependent I/O and crossing budgets;
uncertainty and other exceptions stay as specified by the design. Unsupported
SDCs, nonfinite/nonpositive periods and synthesis-only combinations exit 2
before deleting or writing outputs.

The tool snapshots the source SDC into generated `study.sdc`, prefixed with
the study variable. A trailing Tcl check rejects absent clocks or any clock
whose actual period differs from the requested period. ORFS reads this SDC
throughout implementation. A logged tool error fails the study even if the
child process returns zero. Each period has its own output directory,
`workdir/<ip>/asic-study/<period>ns/` (for example `10ns`); only that directory
is cleaned. Default `asic/` and `asic-synth/` evidence is never touched.

Study `metrics.json` reports the study period in `clock_period_ns` and adds
`study: {kind: "clock-period", clock_period_ns, target_clock_period_ns}`; the
last field is the source SDC's original target. The nightly collector reads
only `asic/metrics.json`, so a study cannot replace target measurements.
Like the default full flow, success means implementation completed, not timing
closure: inspect per-clock setup slack, hold slack and route DRCs before
claiming a checkpoint. Verify the final `6_final.sdc` clock and boundary
constraints as well as the requested period.

## Output

| Path | Content |
| --- | --- |
| `workdir/<ip>/asic/config.mk` | Generated ORFS `DESIGN_CONFIG`. |
| `workdir/<ip>/asic/flow.log` | Every step's command line and output. |
| `workdir/<ip>/asic/run/` | ORFS `WORK_HOME`: `logs/`, `reports/`, `results/`, `objects/` under `<platform>/<nick>/base/`. `<nick>` is the IP id. |
| `workdir/<ip>/asic/metrics.json` | The summary below. |
| `workdir/<ip>/asic/worst_path.txt` | The worst setup path in the tool's own format: group, slack, cell count, start and end point, then one line per data-path pin (arrival ns, rise/fall, pin, cell), from the startpoint to the endpoint pin. `worst setup path: not found` without a report. |

### `metrics.json` (schema 1)

| Key | Source |
| --- | --- |
| `ip`, `design`, `platform`, `clock_period_ns` | Inputs. |
| `status` | `pass`, `<step>: fail (exit N)`, `<step>: timeout after S s` or `incomplete: missing <keys>`. |
| `timing.setup_ws_ns`, `timing.tns_ns`, `timing.hold_ws_ns` | ORFS `*__timing__setup__ws`, `*__timing__setup__tns`, `*__timing__hold__ws`, latest stage. |
| `timing.wns_ns` | `min(setup_ws_ns, 0)`. |
| `timing.clocks.<clock>.setup_ws_ns`, `.tns_ns`, `.wns_ns` | `clock_slacks.tcl` (the `clocks` step) on `6_final.odb` with the final report's parasitics (`6_final.spef` when ORFS extracted it, else global-route estimates): one worst setup path per constrained endpoint (`find_timing_paths -path_delay max -endpoint_path_count 1`), grouped by the clock that captures it; the worst slack and the sum of the negative slacks of each SDC clock (`null` worst slack for a clock with no endpoint); `wns_ns` is `min(setup_ws_ns, 0)`. `.worst_paths` (optional): the slack, start and end point of the clock's five worst endpoint paths, worst first. ORFS reports only the totals over all clocks. A Tcl error writes an empty `clocks`. Required (below). |
| `area.stdcell_um2`, `area.core_um2`, `area.die_um2` | `*__design__instance__area__stdcell`, `*__design__core__area`, `*__design__die__area`; Yosys `synth_stat.txt` area as fallback. |
| `cells`, `synth_cells` | `*__design__instance__count__stdcell` after route (tap cells, clock and timing-repair buffers included, fill cells excluded); Yosys count after synthesis (`synth__…` JSON, else `synth_stat.txt`). |
| `utilization` | `*__design__instance__utilization`, a fraction of the core area. |
| `max_fanout.value`, `.net` | `extra_metrics.tcl` on `6_final.odb`: largest count of input pins plus output ports on one signal net; power, ground and clock nets (odb `CLOCK` type or reaching a register clock pin) excluded. |
| `logic_depth.value`, `.endpoint` | `extra_metrics.tcl`: most cells on any path from a register or input port to a register input pin other than its clock (data, enable, set or reset) or an output port (endpoints excluded; buffers and inverters count), walked backwards over the odb netlist with memoization; a combinational loop is cut. Registers come from OpenSTA `all_registers`. Names are reported with odb's `\[ \]` escapes removed. |
| `worst_path.from`, `.to`, `.group`, `.slack_ns`, `.cells` | The path with the least slack in the `report_checks -path_delay max` section of `6_finish.rpt` (one path per path group; the first on a tie); `cells` counts instances strictly between start and end point on the data path. |
| `warnings` | When `timing.clocks` is missing, one line with its cause (absent `asic_clocks.json`, no `clocks` entry, no clock written, malformed); `[]` when none. |
| `power_w`, `route_drc_errors` | `*__power__total`, `detailedroute__route__drc_errors`. |
| `peak_memory.mb`, `.step` | The largest `Peak memory: <N>KB` line over the step logs (`logs/…/*.log`, which ORFS times), in MB (1024 KB), and the log it came from; `null` without one. Not required: an OOM-killed step writes none, which the step's failure already reports. |
| `toolchain` | Runner, image, root, `orfs_pin` (the host's `orfs_commit`, see above) and the `versions` step output (`openroad`, `yosys`, `orfs`; the pinned image's OpenROAD and ORFS report `unknown`, so the image digest and `orfs_pin` identify them). |
| `source`, `elapsed_s`, `work_home` | Repository `HEAD`, run time, ORFS work home relative to the workdir (`<ip>/asic/run`; no host paths, since the file is published). |

"Latest stage" means the first present of `finish`, `detailedroute`,
`globalroute`, `cts`, `detailedplace`, `globalplace`, `placeopt`,
`floorplan`, `synth`, over the merged `logs/…/*.json` files. Required (else
`incomplete`): `timing.setup_ws_ns`, `timing.tns_ns`, `timing.clocks`,
`area.stdcell_um2`, `cells`, `utilization`, `max_fanout.value`,
`logic_depth.value`, `worst_path.from`, `worst_path.to`. `timing.clocks`
became required in [#200](https://github.com/amichai-bd/pcie-factory/issues/200),
once a nightly showed it populated on the real OpenSTA. Missing, empty or
malformed, it is `null`, the status is `incomplete: missing timing.clocks`
(exit 1) unless a step already failed, and `warnings` names the cause,
also printed as `[asic] warning: timing.clocks missing (required, run
incomplete): ...`. Units follow the platform: sky130hd
liberty time is ns, area µm².

## Workflow

`asic.yml` runs the full flow only on `workflow_dispatch` (nightly L2
also runs the full command directly), on `[self-hosted, linux, x64, wsl]`, the WSL host's runners only ([DevOps BKM](../../bkm/devops.md#continuous-integration)). Input
`ip`: one IP id; empty (the default) runs every IP that has a `src/asic/<ip>/`
folder, found with `find`, one `./build asic --ip <id>` each
(the job fails if any fails). Input `study_clock_period`: empty (the
default) runs the SDC target; a positive decimal number of ns (digits with
at most one point, not all zero) adds
`--study-clock-period <ns>` to each run ([clock studies](#clock-studies));
anything else fails the job before the flow. Input `timeout`: empty (the default)
keeps the tool's 3600 s budget per IP; a whole number of seconds adds
`--timeout <s>` (the job allows 150 minutes). A discovery step prints only `ORFS_IMAGE`, the
docker version, the local ORFS images (name, tag, digest) and where
`openroad`, `yosys` and `klayout` are on `PATH`. The `asic-metrics` artifact
(14-day retention) holds only `metrics.json` and `worst_path.txt` per IP
(from `asic/`, or `asic-study/<period>ns/` for a study),
never raw ORFS reports or logs; the redacted failure excerpt printed to the
job log is the only part of `flow.log` that leaves the host. The job keeps the fork guard (`if:` requires
a non-`pull_request` event or a head repository equal to this one), so fork
code never runs on the self-hosted host if a trigger is added later.

## Tests

`tools/asic/tests/` runs without ORFS. Synthesis-mode tests exercise the CLI,
isolation from routed evidence, stale-output cleanup, missing/empty outputs,
child failure, zero-exit error logs and timeout. Clock-study tests cover invalid
periods, unsupported SDCs, synthesis-only rejection, deterministic generated
constraints, separate study/default outputs and explicit study metadata. The
metrics collector tests prove studies cannot populate the nightly ASIC record.

| Test file | Proves |
| --- | --- |
| `test_asic_flow.py` | `config.mk` generation (filelist order, include dirs, defines with `SYNTHESIS`, nickname and output path from the IP id, determinism); `.mk` validation (including `override`, `define`, the ORFS directory variables and `ASIC_INPUT_DIR`); `ASIC_INPUT_DIR` exported before the `.mk`; source backends (a `sky130` backend file replacing its portable file in place, no swap without one, another flow's backend ignored, a listed backend file exiting 2); SDC count; ORFS settings (digest required, `ORFS_IMAGE` ignored, cores, overrides); docker hardening flags and mounts; unique container names; step commands; `--dry-run`; and a full run against `fixtures/fake_orfs` (a fake `env.sh`, `make`, `openroad`, `yosys`): pass, step failure, timeout, SIGTERM, missing metrics; a failing step that prints the hand-made `fixtures/flow_fail.log` (its error lines more than 30 lines before the end) shows every error line, redacted, before the tail, and one without error lines says `none matched`; and, for the `clocks` step, an empty `clocks` (`incomplete: missing timing.clocks`), a failing step (`clocks: fail (exit 3)`) and a hanging one cut by the run budget (`clocks: timeout after <S>s`), each exit 1 with fanout and depth kept and, where the clocks are missing, the cause in `warnings`. |
| `test_asic_metrics.py` | Each parser, the worst path as the least slack of a multi-group section (and not of a later section), each clock's worst paths, the peak memory (largest step, absent or malformed lines, in the summary), the per-clock slacks (sorted, `wns_ns`, non-numbers dropped; missing, empty or malformed `clocks` alone makes the run `incomplete`, with a warning naming the cause) and the worst-path summary on the fixture reports in `fixtures/orfs/` and on malformed or empty input. |

`fixtures/orfs/` holds report files cut from a real run of the pinned image
on the first synthesis target (see its `README.md`); the fake `make` copies
them into `WORK_HOME` exactly where ORFS writes them.
