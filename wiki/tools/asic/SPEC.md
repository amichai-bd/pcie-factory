# ASIC tool: `./build asic`

`./build asic` runs OpenROAD-flow-scripts (ORFS) from RTL to a routed design
for one IP and writes `metrics.json`. The [PD BKM](../../bkm/pd.md) owns the
method (inputs, acceptance ladder, review). Code: `tools/asic/` (`cli.py`,
`flow.py`, `metrics.py`, `extra_metrics.tcl`); workflow:
[`.github/workflows/asic.yml`](../../../.github/workflows/asic.yml).

```
./build asic --ip ID [--platform sky130hd] [--timeout S] [--dry-run]
             [--src-root DIR] [--workdir DIR]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | Required. A top-level IP id with a `src/asic/<ip>/` folder. |
| `--platform` | ORFS platform; only `sky130hd` is supported. |
| `--timeout S` | Wall-clock budget for all ORFS steps together (default 3600 s). |
| `--dry-run` | Write `config.mk`, print every command, run nothing. |

Exit codes: `0` the flow passed and every required metric was read; `1`
a step failed, timed out, or a required metric is missing (`metrics.json`
is still written); `2` a usage or configuration error.

## Inputs

| File | Use |
| --- | --- |
| `src/rtl/<ip>/<ip>.rtl.f` | Flattened by the [filelist resolver](../build/SPEC.md#filelists) into `VERILOG_FILES`, `VERILOG_INCLUDE_DIRS` and `VERILOG_DEFINES` (`-DNAME` or `-DNAME=VALUE`), in filelist order. |
| `src/asic/<ip>/<platform>.mk` | Design knobs, included first by the generated config. `DESIGN_NAME` is required. |
| `src/asic/<ip>/*.sdc` | Exactly one SDC; becomes `SDC_FILE`. Its first `create_clock -period` is reported as `clock_period_ns`. |
| `cfg/host.yaml` `hosts.<name>.orfs_*` | How to reach ORFS (below). |

The tool owns `PLATFORM`, `DESIGN_NICKNAME`, `DESIGN_CONFIG`, `SDC_FILE`,
`VERILOG_FILES`, `VERILOG_INCLUDE_DIRS`, `VERILOG_DEFINES`, `WORK_HOME`,
`FLOW_VARIANT`, `NUM_CORES`, `RESULTS_DIR`, `LOG_DIR`, `REPORTS_DIR` and
`OBJECTS_DIR`; a `.mk` that sets any of them is a configuration error. The
check reads assignments (`=`, `:=`, `::=`, `:::=`, `?=`, `+=`, `!=`, with
`export` and/or `override`) and `define NAME` blocks, with line continuations
joined; it does not evaluate make.

**SystemVerilog frontend.** Yosys's built-in reader handles only part of
SystemVerilog (package imports in a module header, typed parameters). An IP
whose RTL needs more sets `SYNTH_HDL_FRONTEND = slang` in its `.mk`; ORFS
then reads all sources in one `read_slang` call (slang is built into the
pinned image's Yosys). The tool passes the knob through unchanged.

## ORFS runners

| Runner | Runs | Selected by |
| --- | --- | --- |
| `docker` | `docker run --rm` of the image with ORFS at `/OpenROAD-flow-scripts`, as the calling user (`--user uid:gid`, `HOME=/tmp`), hardened for a shared host: `--network=none`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit=4096`, `--cpus=<cores>`, `--memory=3g --memory-swap=3g`. The repository and an outside source root are mounted read-only and the workdir read-write, each at its own path, so generated absolute paths work unchanged. | The host's `orfs_image` |
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
native. With none of them the command exits 2. The Omarchy host has only the
pinned image; native mode is kept because it costs one `bash -c` and is what
the unit tests drive with a fake ORFS.

## Behavior

1. Discover the IP, validate its inputs, resolve its filelist and load the
   ORFS settings; any failure exits 2 before anything runs.
2. Delete `workdir/<ip>/asic/` (not on `--dry-run`) and write
   `config.mk` there.
3. Run three steps in order, each a separate runner invocation, appending to
   `flow.log`; the first failure stops the run:

   | Step | Command | Purpose |
   | --- | --- | --- |
   | `versions` | `openroad -version`, `yosys -V`, `git describe` of the ORFS root | Toolchain record |
   | `flow` | `make -C <root>/flow DESIGN_CONFIG=… WORK_HOME=… FLOW_VARIANT=base NUM_CORES=<cores> finish` | Synthesis to final report |
   | `extra` | the same `make … RUN_SCRIPT=tools/asic/extra_metrics.tcl run` | Max fanout and logic depth on `6_final.odb` |

4. Each step runs in its own process group. Docker containers are named
   `asic-<nick>-<12 random hex>-<step>`, unique per run because several
   runners share the host. When the shared `--timeout` budget runs out, the
   container is removed (`docker rm -f`; dockerd owns it, so killing the
   client is not enough), the group is killed and the status is
   `<step>: timeout after <S>s`. Ctrl-C or SIGTERM (a cancelled CI job)
   cleans up the same way and exits 130.
5. Parse the reports and write `metrics.json` and `worst_path.txt`, also on
   failure.

Everything the command prints (commands, paths, errors) shows host paths as
placeholders, because CI job logs are public: the repository as `<repo>`,
the default workdir and source root as `<repo>/workdir` and `<repo>/src`,
others as `<workdir>` and `<src>`. The commands that run, `config.mk` and
`flow.log` keep the real paths.

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
| `area.stdcell_um2`, `area.core_um2`, `area.die_um2` | `*__design__instance__area__stdcell`, `*__design__core__area`, `*__design__die__area`; Yosys `synth_stat.txt` area as fallback. |
| `cells`, `synth_cells` | `*__design__instance__count__stdcell` after route (tap cells, clock and timing-repair buffers included, fill cells excluded); Yosys count after synthesis (`synth__…` JSON, else `synth_stat.txt`). |
| `utilization` | `*__design__instance__utilization`, a fraction of the core area. |
| `max_fanout.value`, `.net` | `extra_metrics.tcl` on `6_final.odb`: largest count of input pins plus output ports on one signal net; power, ground and clock nets (odb `CLOCK` type or reaching a register clock pin) excluded. |
| `logic_depth.value`, `.endpoint` | `extra_metrics.tcl`: most cells on any path from a register or input port to a register input pin other than its clock (data, enable, set or reset) or an output port (endpoints excluded; buffers and inverters count), walked backwards over the odb netlist with memoization; a combinational loop is cut. Registers come from OpenSTA `all_registers`. Names are reported with odb's `\[ \]` escapes removed. |
| `worst_path.from`, `.to`, `.group`, `.slack_ns`, `.cells` | First `report_checks -path_delay max` section of `6_finish.rpt`; `cells` counts instances strictly between start and end point on the data path. |
| `power_w`, `route_drc_errors` | `*__power__total`, `detailedroute__route__drc_errors`. |
| `toolchain` | Runner, image, root, `orfs_pin` (the host's `orfs_commit`, see above) and the `versions` step output (`openroad`, `yosys`, `orfs`; the pinned image's OpenROAD and ORFS report `unknown`, so the image digest and `orfs_pin` identify them). |
| `source`, `elapsed_s`, `work_home` | Repository `HEAD`, run time, ORFS work home relative to the workdir (`<ip>/asic/run`; no host paths, since the file is published). |

"Latest stage" means the first present of `finish`, `detailedroute`,
`globalroute`, `cts`, `detailedplace`, `globalplace`, `placeopt`,
`floorplan`, `synth`, over the merged `logs/…/*.json` files. Required (else
`incomplete`): `timing.setup_ws_ns`, `timing.tns_ns`, `area.stdcell_um2`,
`cells`, `utilization`, `max_fanout.value`, `logic_depth.value`,
`worst_path.from`, `worst_path.to`. Units follow the platform: sky130hd
liberty time is ns, area µm².

## Workflow

`asic.yml` runs only on `workflow_dispatch` (nightly L2 dispatches it; never
in `ci-gate`), on `[self-hosted, linux, x64]`, the host with ORFS. Input
`ip`: one IP id; empty (the default) runs every IP that has a `src/asic/<ip>/`
folder, found with `find`, one `./build asic --ip <id>` each
(the job fails if any fails). A discovery step prints only `ORFS_IMAGE`, the
docker version, the local ORFS images (name, tag, digest) and where
`openroad`, `yosys` and `klayout` are on `PATH`. The `asic-metrics` artifact
(14-day retention) holds only `metrics.json` and `worst_path.txt` per IP,
never raw ORFS reports or logs. The job keeps the fork guard (`if:` requires
a non-`pull_request` event or a head repository equal to this one), so fork
code never runs on the self-hosted host if a trigger is added later.

## Tests

`tools/asic/tests/` runs without ORFS:

| Test file | Proves |
| --- | --- |
| `test_asic_flow.py` | `config.mk` generation (filelist order, include dirs, defines, nickname and output path from the IP id, determinism); `.mk` validation (including `override`, `define` and the ORFS directory variables); SDC count; ORFS settings (digest required, `ORFS_IMAGE` ignored, cores, overrides); docker hardening flags and mounts; unique container names; step commands; `--dry-run`; and a full run against `fixtures/fake_orfs` (a fake `env.sh`, `make`, `openroad`, `yosys`): pass, step failure, timeout, SIGTERM and missing metrics, each with the expected exit code and `status`. |
| `test_asic_metrics.py` | Each parser and the worst-path summary on the fixture reports in `fixtures/orfs/` and on malformed or empty input. |

`fixtures/orfs/` holds report files cut from a real run of the pinned image
on the first synthesis target (see its `README.md`); the fake `make` copies
them into `WORK_HOME` exactly where ORFS writes them.
