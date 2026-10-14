# FPGA tool: `./build fpga`

Compiles one IP for the FPGA board with Intel Quartus Prime Lite and writes
its resource and timing metrics. The [FPGA BKM](../../bkm/fpga.md) owns the
target, the inputs an IP provides and the acceptance ladder; this page owns
the command line, the generated project, the metrics schema and failures.

| File | Owns |
| --- | --- |
| `tools/fpga/flow.py` | Input validation, project generation, Quartus discovery, running the steps. |
| `tools/fpga/reports.py` | Parsing Quartus reports into metrics. |
| `cfg/fpga.yaml` | Board name, per-step timeout, Quartus processor count. |
| `.github/workflows/fpga.yml` | The self-hosted CI job. |

## Command line

```
./build fpga (--ip ID [--ip ID]... | --all) [--part DEVICE] [--timeout S] [--dry-run]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | IP to compile; it must have an `fpga/` directory. Repeatable. |
| `--all` | Every IP that has an `fpga/` directory, sorted by id. |
| `--part DEVICE` | Override the device that the board settings file sets (letters and digits only). |
| `--timeout S` | Wall-clock bound in seconds on each step (default: `step_timeout_s` in `cfg/fpga.yaml`). |
| `--dry-run` | Validate inputs, write the project scripts, print the steps; run nothing and need no Quartus. A previous compile in the same directory is left in place. |

The shared `--src-root` and `--workdir` options work as in the
[build spec](../build/SPEC.md#command-line).

## Inputs

For IP `<ip>` with directory name `<name>`, and `<board>` from
`cfg/fpga.yaml` (`de2_115`):

| Input | Rule |
| --- | --- |
| `rtl/<name>.rtl.f` | Flattened by the [filelist resolver](../build/SPEC.md#filelists): sources, include directories, defines. |
| `fpga/<board>.qsf.tcl` | Required. Quartus Tcl run inside the open project: device, pins, I/O standards. |
| `fpga/*.sdc` | Exactly one. Added as the project's `SDC_FILE`. |
| `fpga/*.sv` | Exactly one: the board wrapper. Its file stem is the top-level module, and it must declare `module <stem>`. It must not also be in `<name>.rtl.f`. |

Sources must be `.sv` (`SYSTEMVERILOG_FILE`) or `.v` (`VERILOG_FILE`).
Every input is checked before anything runs; a violation is an error that
names the file.

## Steps

Everything runs in `workdir/<ip>/fpga/`, which a real run deletes and
recreates first (`--dry-run` only overwrites the two scripts). Each step runs in its own process group, with stdout and stderr in
`logs/<step>.log` (first line: the command). A step that exceeds the
timeout has its group killed and counts as `timeout`. The first step that
fails or times out stops the flow and prints the last 30 lines of its log.

| Step | Command | Does |
| --- | --- | --- |
| `lint` | `verilator --lint-only -Wall --top-module <top> ...` | Lints the flattened sources plus the wrapper (BKM acceptance stage 2). Skipped, and recorded as `skipped`, when Verilator is not found ([build spec](../build/SPEC.md#simulation-build-sim) search order). |
| `project` | `quartus_sh -t project.tcl` | Creates `<top>.qpf` and `<top>.qsf` (revision = top). |
| `map` | `quartus_map <top> -c <top>` | Analysis and synthesis. |
| `fit` | `quartus_fit <top> -c <top>` | Place and route. |
| `sta` | `quartus_sta <top> -c <top>` | Timing analysis for every corner; writes `<top>.sta.rpt`. |
| `worst_path` | `quartus_sta -t worst_path.tcl` | Writes the worst setup path to `worst_path.txt`. |

`project.tcl` sets, in this order: `TOP_LEVEL_ENTITY`,
`PROJECT_OUTPUT_DIRECTORY output_files`, `VERILOG_INPUT_VERSION
SYSTEMVERILOG_2005`, `NUM_PARALLEL_PROCESSORS` (`processors` in
`cfg/fpga.yaml`), one `SEARCH_PATH` per include directory, one
`VERILOG_MACRO` per define (`NAME` or `NAME=VALUE`), the sources in filelist
order with the wrapper last, `SDC_FILE`, then `source <board>.qsf.tcl`, then
`DEVICE <part>` when `--part` is given. Paths are absolute and Tcl-braced.
Generation is deterministic: the same inputs give the same bytes.

`worst_path.tcl` opens the project, builds the timing netlist with the SDC,
and writes `key=value` lines for the single worst setup path
(`get_timing_paths -setup -npaths 1`): `slack`, `from`, `to`, `from_clock`,
`to_clock`, `data_delay`. The file is empty when there is no path.

### Finding Quartus

The tool looks for `quartus_sh` in this order and runs every `quartus_*`
from the same directory:

1. `PATH`.
2. `$QUARTUS_ROOTDIR/bin`.
3. `<quartus_root>/bin`, with `hosts.omarchy.quartus_root` from
   `cfg/host.yaml` ([host spec](../host/SPEC.md#hosts)).

If none has it, the command fails with exit 2 and names the places it
looked. `--dry-run` never looks.

## Metrics

After the steps pass, the tool parses `output_files/<top>.fit.summary`,
`output_files/<top>.fit.rpt`, `output_files/<top>.sta.rpt` and
`worst_path.txt`, writes `metrics.json` (two-space indent) and prints a
summary.

| Key | Source and meaning |
| --- | --- |
| `ip`, `board`, `top` | IP id, board name, top-level module. |
| `schema` | `1`; bumped on any incompatible change. |
| `quartus_version`, `family`, `device` | Fitter summary. |
| `resources.logic_elements` | `{used, total}`, fitter summary "Total logic elements". |
| `resources.registers` | Fitter summary "Total registers". |
| `resources.memory_bits` | `{used, total}`, "Total memory bits". |
| `resources.memory_blocks` | `{type, used, total}` from the fitter resource usage line `M9Ks` (any `M<n>Ks`), or `null`. |
| `resources.dsp_9bit` | `{used, total}`, "Embedded Multiplier 9-bit elements". |
| `resources.pins`, `resources.plls` | `{used, total}`. |
| `timing.clocks.<clock>` | `fmax_mhz`, `restricted_fmax_mhz`, `setup_slack_ns`, `hold_slack_ns`, `recovery_slack_ns`, `removal_slack_ns`: for each clock, the worst value over every timing model (corner) panel `* Model Fmax Summary` and `* Model <Setup\|Hold\|Recovery\|Removal> Summary`; `null` when a kind has no paths for that clock. |
| `timing.worst_<kind>_slack_ns` | Minimum over the clocks for `setup`, `hold`, `recovery`, `removal`; `null` when no clock has such paths. |
| `timing.unconstrained` | The "Unconstrained Paths Summary" panel as `{property: {setup, hold}}`, property in snake case (`unconstrained_input_port_paths`, ...). |
| `timing.unconstrained_total` | Sum of all those counts. |
| `timing.met` | True when setup and hold paths exist with worst slack `>= 0`, recovery and removal slack (when present) are `>= 0`, and `unconstrained_total` is 0. Pulse-width checks are not included. |
| `timing.critical_path` | `{from, to, from_clock, to_clock, slack_ns, data_delay_ns}` from `worst_path.txt` (slow model), or `null` when there is no path. |
| `flow.lint` | `pass`, `fail` or `skipped`. |
| `flow.steps.<step>` | `{status, runtime_s, warnings}`; `warnings` is the count from the step's closing "was successful. N errors, M warnings" line, or `null`. |

A ratio missing from the fitter summary is `null`. These are report errors
(exit 1): fitter status other than `Successful`, no logic-element, register
or device line, a missing Fmax, setup or hold summary panel or
Unconstrained Paths Summary, no clock with timing paths, an unreadable report, or a malformed `worst_path.txt` line.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Every selected IP compiled, `metrics.json` written, timing met. |
| 1 | A step failed or timed out, a report could not be parsed, or timing is not met (metrics are still written). |
| 2 | Usage or input error: unknown IP, IP without `fpga/`, bad inputs or `cfg/fpga.yaml`, filelist error, Quartus not found. |

With several IPs, every IP runs and the worst code wins.

## Output

| Path | Content |
| --- | --- |
| `workdir/<ip>/fpga/project.tcl`, `worst_path.tcl` | Generated scripts. |
| `workdir/<ip>/fpga/<top>.qpf`, `<top>.qsf`, `db/`, `incremental_db/` | Quartus project. |
| `workdir/<ip>/fpga/output_files/` | Reports (`*.rpt`, `*.summary`) and the `.sof`. |
| `workdir/<ip>/fpga/logs/<step>.log` | Step logs. |
| `workdir/<ip>/fpga/worst_path.txt`, `metrics.json` | Parsed inputs and the metrics. |

Because `fpga/` lives in `workdir/<ip>/`, no test may be named `fpga`
(`RESERVED_TEST_NAMES` in `tools/build/ip.py`), and `fpga` is never a sub-IP
directory.

## CI

`.github/workflows/fpga.yml` runs on `[self-hosted, linux, x64]` (the host
with Quartus) with `contents: read` and a 90-minute job timeout. It has
**manual dispatch only**: per decision Q26, Quartus runs in nightly L2 or on
demand, never on pull requests. Input `ip` runs `./build fpga --ip <ip>`,
or `--all` when empty; nightly (DevOps BKM) calls `./build fpga --all`
itself. The job still carries the fork guard
(`head.repo.full_name == github.repository`) from the
[DevOps BKM](../../bkm/devops.md#continuous-integration) in case a
`pull_request` trigger is ever added. The workflow uses no secrets and checks
out with `persist-credentials: false`.

A discovery step prints only the `quartus_sh`, Verilator and Python paths
and versions. The artifact `fpga-metrics` (kept 14 days, uploaded even when a
step fails) holds only this tool's own output, `metrics.json` and
`worst_path.txt`, for each IP. Quartus reports and logs carry the vendor's
notice, so they stay in `workdir/` on the host and are never uploaded or
committed.

## Unit tests

`tools/fpga/tests/` runs without Quartus:

- `test_fpga_reports.py` parses hand-made report fixtures in
  `tools/fpga/tests/fixtures/` (layout of Quartus Lite Cyclone IV E reports;
  not real tool output, see its README): resources, worst-of-corners timing,
  the worst path, failed fitter status, malformed values, missing panels,
  no clocks, negative slack.
- `test_fpga_flow.py` builds a synthetic IP tree and checks planning and
  input errors, the generated Tcl (order, defines, include paths, `--part`),
  deterministic generation, the lint command, Quartus discovery order,
  `cfg/fpga.yaml` validation, and the run loop with fake `quartus_*` scripts
  that replay the fixtures: metrics written, a failing step stops the flow,
  lint failure, timing not met, and a step timeout killing its group; and
  the CLI (`--dry-run`, `--all`, missing Quartus, IP without `fpga/`).
