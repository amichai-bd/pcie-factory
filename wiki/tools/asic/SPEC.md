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
| `--ip ID` | Required. An IP (or sub-IP) id with an `asic/` directory. |
| `--platform` | ORFS platform; only `sky130hd` is supported. |
| `--timeout S` | Wall-clock budget for all ORFS steps together (default 3600 s). |
| `--dry-run` | Write `config.mk`, print every command, run nothing. |

Exit codes: `0` the flow passed and every required metric was read; `1`
a step failed, timed out, or a required metric is missing (`metrics.json`
is still written); `2` a usage or configuration error.

## Inputs

| File | Use |
| --- | --- |
| `src/<ip>/rtl/<name>.rtl.f` | Flattened by the [filelist resolver](../build/SPEC.md#filelists) into `VERILOG_FILES`, `VERILOG_INCLUDE_DIRS` and `VERILOG_DEFINES` (`-DNAME` or `-DNAME=VALUE`), in filelist order. |
| `src/<ip>/asic/<platform>.mk` | Design knobs, included first by the generated config. `DESIGN_NAME` is required. |
| `src/<ip>/asic/*.sdc` | Exactly one SDC; becomes `SDC_FILE`. Its first `create_clock -period` is reported as `clock_period_ns`. |
| `cfg/host.yaml` `hosts.<name>.orfs_*` | How to reach ORFS (below). |

The tool owns `PLATFORM`, `DESIGN_NICKNAME`, `SDC_FILE`, `VERILOG_FILES`,
`VERILOG_INCLUDE_DIRS`, `VERILOG_DEFINES`, `WORK_HOME` and `FLOW_VARIANT`; a
`.mk` that assigns any of them is a configuration error. The check reads
plain `NAME = value` assignments (`=`, `:=`, `?=`, `+=`, with or without
`export`); it does not evaluate make.

**SystemVerilog frontend.** Yosys's built-in reader handles only part of
SystemVerilog (package imports in a module header, typed parameters). An IP
whose RTL needs more sets `SYNTH_HDL_FRONTEND = slang` in its `.mk`; ORFS
then reads all sources in one `read_slang` call (slang is built into the
pinned image's Yosys). The tool passes the knob through unchanged.

## ORFS runners

| Runner | Runs | Selected by |
| --- | --- | --- |
| `docker` | `docker run --rm --memory=3g --memory-swap=3g` of the image, as the calling user (`--user uid:gid`, `HOME=/tmp`), with ORFS at `/OpenROAD-flow-scripts` inside it. The repository and an outside source root are mounted read-only and the workdir read-write, each at its own path, so generated absolute paths work unchanged. | `ORFS_IMAGE`, else the host's `orfs_image` |
| `native` | `bash -c` on the host, after `source <root>/env.sh` when it exists. | `ORFS_ROOT`, else the host's `orfs_root` |

Selection, first match wins: `ORFS_RUNNER` (`docker`|`native`) if set;
`ORFS_ROOT` set means native; an image (environment or cfg) means docker; a
cfg root means native. With none of them the command exits 2. The host
values come from the one `hosts.<name>` entry in
[`cfg/host.yaml`](../../../cfg/host.yaml) that records `orfs_image` or
`orfs_root` (more than one is an error). Its `orfs_commit` is reported as
`toolchain.orfs_pin` only when the image or root that runs is the recorded
one. The Omarchy host has only the pinned image (also exported to its
runners as `ORFS_IMAGE`); native mode is kept because it costs one `bash -c`
and is what the unit tests drive with a fake ORFS.

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
   | `flow` | `make -C <root>/flow DESIGN_CONFIG=… WORK_HOME=… FLOW_VARIANT=base finish` | Synthesis to final report |
   | `extra` | the same `make … RUN_SCRIPT=tools/asic/extra_metrics.tcl run` | Max fanout and logic depth on `6_final.odb` |

4. Each step runs in its own process group. When the shared `--timeout`
   budget runs out, the group is killed (and, for docker, the named
   container `asic-<nick>-<pid>-<step>` is killed) and the status is
   `<step>: timeout after <S>s`. Ctrl-C kills the same way.
5. Parse the reports and write `metrics.json`, also on failure.

## Output

| Path | Content |
| --- | --- |
| `workdir/<ip>/asic/config.mk` | Generated ORFS `DESIGN_CONFIG`. |
| `workdir/<ip>/asic/flow.log` | Every step's command line and output. |
| `workdir/<ip>/asic/run/` | ORFS `WORK_HOME`: `logs/`, `reports/`, `results/`, `objects/` under `<platform>/<nick>/base/`. `<nick>` is the IP id with `/` replaced by `_`. |
| `workdir/<ip>/asic/metrics.json` | The summary below. |

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
| `logic_depth.value`, `.endpoint` | `extra_metrics.tcl`: most cells on any path from a register or input port to a register data pin or output port (endpoints excluded; buffers and inverters count), walked backwards over the odb netlist with memoization; a combinational loop is cut. Registers come from OpenSTA `all_registers`. Names are reported with odb's `\[ \]` escapes removed. |
| `worst_path.from`, `.to`, `.group`, `.slack_ns`, `.cells` | First `report_checks -path_delay max` section of `6_finish.rpt`; `cells` counts instances strictly between start and end point on the data path. |
| `power_w`, `route_drc_errors` | `*__power__total`, `detailedroute__route__drc_errors`. |
| `toolchain` | Runner, image, root, `orfs_pin` (the host's `orfs_commit`, see above) and the `versions` step output (`openroad`, `yosys`, `orfs`; the pinned image's OpenROAD and ORFS report `unknown`, so the image digest and `orfs_pin` identify them). |
| `source`, `elapsed_s`, `work_home` | Repository `HEAD`, run time, ORFS work home. |

"Latest stage" means the first present of `finish`, `detailedroute`,
`globalroute`, `cts`, `detailedplace`, `globalplace`, `placeopt`,
`floorplan`, `synth`, over the merged `logs/…/*.json` files. Required (else
`incomplete`): `timing.setup_ws_ns`, `timing.tns_ns`, `area.stdcell_um2`,
`cells`, `utilization`, `max_fanout.value`, `logic_depth.value`,
`worst_path.from`, `worst_path.to`. Units follow the platform: sky130hd
liberty time is ns, area µm².

## Workflow

`asic.yml` runs on `[self-hosted, linux, x64]` (the host with ORFS) on
`workflow_dispatch` (input `ip`, default the first synthesis target) and on
pull requests touching `tools/asic/**`, `src/**/asic/**`, `src/**/rtl/**`
or the workflow. It prints an ORFS discovery report (docker images, native
tools, candidate roots), runs `./build asic --ip <ip>` and uploads
`metrics.json`, `flow.log`, the ORFS reports and the step logs as the
`asic-metrics` artifact. Pull requests from forks are skipped (the job's
`if:` requires the head repository to be this one), so fork code never runs
on the self-hosted host. It is not part of `ci-gate`.

## Tests

`tools/asic/tests/` runs without ORFS:

| Test file | Proves |
| --- | --- |
| `test_asic_flow.py` | `config.mk` generation (filelist order, include dirs, defines, sub-IP nickname, determinism); `.mk` validation; SDC count; ORFS settings and overrides; docker mounts; step commands; `--dry-run`; and a full run against `fixtures/fake_orfs` (a fake `env.sh`, `make`, `openroad`, `yosys`): pass, step failure, timeout and missing metrics, each with the expected exit code and `status`. |
| `test_asic_metrics.py` | Each parser on the fixture reports in `fixtures/orfs/` and on malformed or empty input. |

`fixtures/orfs/` holds report files cut from a real run of the pinned image
on the first synthesis target (see its `README.md`); the fake `make` copies
them into `WORK_HOME` exactly where ORFS writes them.
