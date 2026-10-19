# FPGA tool: `./build fpga`

Compiles one IP for the FPGA board with Intel Quartus Prime Lite and writes
its resource and timing metrics. The [FPGA BKM](../../bkm/fpga.md) owns the
target, the inputs an IP provides and the acceptance ladder; this page owns
the command line, the generated project, the metrics schema and failures.

| File | Owns |
| --- | --- |
| `tools/fpga/flow.py` | Input validation, project generation, Quartus discovery, running the steps. |
| `tools/fpga/reports.py` | Parsing Quartus reports into metrics. |
| `cfg/fpga.yaml` | Board name, per-step timeout, Quartus processor count, known Quartus warning IDs (`known_warnings`, a list of distinct positive integers; optional, default empty). |
| `cfg/backends.yaml` | The `fpga` key: the suffix of the source backend files this flow selects ([layout](../../project/layout.md#source-backends)). |
| `.github/workflows/fpga.yml` | The self-hosted CI job. |

## Command line

```
./build fpga (--ip ID [--ip ID]... | --all) [--part DEVICE] [--timeout S] [--synth-only] [--dry-run]
```

| Option | Meaning |
| --- | --- |
| `--ip ID` | IP to compile; it must have a `src/fpga/<ip>/` folder. Repeatable. |
| `--all` | Every IP that has a `src/fpga/<ip>/` folder, sorted by id. |
| `--part DEVICE` | Override the device that the board settings file sets (letters and digits only). |
| `--timeout S` | Wall-clock bound in seconds on each step (default: `step_timeout_s` in `cfg/fpga.yaml`). |
| `--synth-only` | Lint, `project` and `map` (analysis and synthesis) only, in `workdir/<ip>/fpga-synth/`: no fit, timing or worst path, and `synthesis.json` instead of `metrics.json` ([synthesis only](#synthesis-only)). PR CI runs this ([CI](#ci)). |
| `--dry-run` | Validate inputs, write the project scripts, print the steps; run nothing and need no Quartus. A previous compile in the same directory is left in place. `./build check` runs the same input validation for every FPGA target ([flow-input check](../checks/SPEC.md#flow-input-check)). |

The shared `--src-root` and `--workdir` options work as in the
[build spec](../build/SPEC.md#command-line).

## Inputs

For IP `<ip>` (its id), and `<board>` from
`cfg/fpga.yaml` (`de2_115`):

| Input | Rule |
| --- | --- |
| `src/rtl/<ip>/<ip>.rtl.f` | Flattened by the [filelist resolver](../build/SPEC.md#filelists): sources, include directories, defines. The flow adds `SYNTHESIS`, so the [assertion macros](../../bkm/rtl.md#assertions) compile out; a filelist that defines `SYNTHESIS` itself is an input error. Each source with an `fpga` [backend file](../../project/layout.md#source-backends) beside it (`<stem>_<suffix>.sv`) is replaced by that file, in place, in the Quartus source list; a filelist that lists a backend file is an input error. |
| `src/fpga/<ip>/<board>.qsf.tcl` | Required. Quartus Tcl run inside the open project: device, pins, I/O standards. |
| `src/fpga/<ip>/*.sdc` | Exactly one. Added as the project's `SDC_FILE`. |
| `src/fpga/<ip>/*.sv` | Exactly one: the board wrapper. Its file stem is the top-level module, and it must declare `module <stem>`. It must not also be in `<ip>.rtl.f`. |

Sources must be `.sv` (`SYSTEMVERILOG_FILE`) or `.v` (`VERILOG_FILE`).
Every input is checked before anything runs; a violation is an error that
names the file.

## Steps

Everything runs in `workdir/<ip>/fpga/` (`fpga-synth/` with `--synth-only`), which a real run deletes and
recreates first (`--dry-run` only overwrites the two scripts). Each step runs in its own process group, with stdout and stderr in
`logs/<step>.log` (first line: the command). A step that exceeds the
timeout has its group killed and counts as `timeout`. SIGINT or SIGTERM
also kills the active step's whole process group and waits for its leader,
including when cancellation arrives through `ci vendor`. The flow stops
without running later steps or publishing metrics, the CLI exits 130, and
the previous signal handlers are restored. Signals arriving during process
creation are deferred until the group handle is available, then cleaned up;
repeated signals cannot interrupt kill/reap. The first step that
fails or times out stops the flow and prints its
[failure excerpt](../build/SPEC.md#failure-excerpt): every error line of its
log (Quartus `Error (<id>):` and `Critical Warning[ (<id>)]:` messages,
Verilator `%Error` lines), at most 50 with the total count, then the last 30
lines. The closing `Error:` summary block (`… was unsuccessful. N errors`)
is not an error line; it is in the tail.

| Step | Command | Does |
| --- | --- | --- |
| `lint` | `verilator --lint-only -Wall -Wno-fatal --top-module <top> ...` | Lints the flattened sources plus the wrapper (BKM acceptance stage 2), in the simulation view: the portable files (backends are not selected) and without `SYNTHESIS`, like `./build check`. It passes only when every `%Warning` and `%Error` in its log is covered by a reviewed waiver in `src/lint_waivers.yaml`, matched as the [lint check](../checks/SPEC.md#waivers) matches them, and a nonzero exit has an error message; each other message is printed as a finding, and an unreadable or invalid waiver file fails the step. Stale waivers are left to `./build check`. Skipped, and recorded as `skipped`, when Verilator is not found ([build spec](../build/SPEC.md#simulation-build-sim) search order). |
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
   `cfg/host.yaml` ([host spec](../host/SPEC.md#hosts)); a fallback kept
   from the now unsupported Omarchy host.

If none has it, the command fails with exit 2 and names the places it
looked. `--dry-run` never looks.

## Metrics

After the steps pass, the tool parses `output_files/<top>.fit.summary`,
`output_files/<top>.fit.rpt`, `output_files/<top>.sta.rpt` and
`worst_path.txt`, writes `metrics.json` (two-space indent) and prints a
summary: device, logic elements and registers, the memory block count, one
line per RAM (name, mode, clock mode, depth x width, blocks, mixed-port
read-during-write mode), each clock's Fmax and slack, the unconstrained
paths, the [warnings](#warnings) and the critical path. Before the steps, and in `--dry-run`, it
prints one `[backend] <portable> -> <backend>` line per selected backend
file.

| Key | Source and meaning |
| --- | --- |
| `ip`, `board`, `top` | IP id, board name, top-level module. |
| `schema` | `1`; bumped on any incompatible change. |
| `quartus_version`, `family`, `device` | Fitter summary. |
| `resources.logic_elements` | `{used, total}`, fitter summary "Total logic elements". |
| `resources.registers` | Fitter summary "Total registers". |
| `resources.memory_bits` | `{used, total}`, "Total memory bits". |
| `resources.memory_blocks` | `{type, used, total}` from the fitter resource usage line `M9Ks` (any `M<n>Ks`), or `null`. |
| `resources.rams` | One entry per row of the fitter's "Fitter RAM Summary" panel: `name` (hierarchical), `mode`, `clock_mode`, `port_a_depth`, `port_a_width`, `port_b_depth`, `port_b_width`, `bits` (`Size`), `mixed_rdw` ("Mixed Width RDW Mode") and `blocks` (the `M<n>Ks` column); a missing column, and a cell Quartus prints as `--` (a field that does not apply, such as port B of a ROM or a single-port RAM), is `null`. Empty when there is no such panel. |
| `resources.dsp_9bit` | `{used, total}`, "Embedded Multiplier 9-bit elements". |
| `resources.pins`, `resources.plls` | `{used, total}`. |
| `timing.clocks.<clock>` | `fmax_mhz`, `restricted_fmax_mhz`, `setup_slack_ns`, `hold_slack_ns`, `recovery_slack_ns`, `removal_slack_ns`: for each clock, the worst value over every timing model (corner) panel `* Model Fmax Summary` and `* Model <Setup\|Hold\|Recovery\|Removal> Summary`; `null` when a kind has no paths for that clock. |
| `timing.worst_<kind>_slack_ns` | Minimum over the clocks for `setup`, `hold`, `recovery`, `removal`; `null` when no clock has such paths. |
| `timing.unconstrained` | The "Unconstrained Paths Summary" panel as `{property: {setup, hold}}`, property in snake case (`unconstrained_input_port_paths`, ...). |
| `timing.unconstrained_total` | Sum of all those counts. |
| `timing.met` | True when setup and hold paths exist with worst slack `>= 0`, recovery and removal slack (when present) are `>= 0`, and `unconstrained_total` is 0. Pulse-width checks are not included. |
| `timing.critical_path` | `{from, to, from_clock, to_clock, slack_ns, data_delay_ns}` from `worst_path.txt` (slow model), or `null` when there is no path. |
| `flow.lint` | `pass`, `fail` or `skipped`. |
| `flow.steps.<step>` | `{status, runtime_s, warnings, warning_ids}`; `warnings` is the count from the step's closing "was successful. N errors, M warnings" line, or `null`; `warning_ids` maps each Quartus message ID (a string, in numeric order) of the step log's warnings to its count ([warnings](#warnings)). |

### Warnings

Each Quartus step's log is scanned for `Warning (<id>): <message>` and
`Critical Warning (<id>): <message>` lines, indented sub-messages included;
a warning without an ID is not counted by ID, so `warnings` can differ from
the sum of `warning_ids`. An ID is known when `known_warnings` in
`cfg/fpga.yaml` lists it (the [FPGA BKM](../../bkm/fpga.md#known-quartus-warnings)
table says why; a unit test keeps the two equal). For each step with a
warning, the summary prints:

```
  warnings <step>: known <k>, unknown <u> (closing line: <warnings>)
    unknown <id> (x<count>): <message>
```

with one `unknown` line per unknown ID, in ID order, holding the ID's first
message, sanitized: paths under the checkout made relative to it, any other
absolute path cut to `.../<file name>`, the user and host names (whole
words) replaced by `<name>`, e-mail and IPv4 addresses by `<email>` and
`<ip>`, whitespace runs collapsed, and the text cut to 160 characters
(ending in `...` when cut). Known IDs print only as a count. Nothing else
from the logs is printed for a passing run.

A ratio missing from the fitter summary is `null`. These are report errors
(exit 1): fitter status other than `Successful`, no logic-element, register
or device line, a missing Fmax, setup or hold summary panel or
Unconstrained Paths Summary, no clock with timing paths, an unreadable report, or a malformed `worst_path.txt` line.

## Synthesis only

`--synth-only` is the fast compatibility check required CI runs: it proves
that Quartus analyses and synthesizes the IP with its board wrapper, and
reports resources before fitting; it makes no fit, placement or timing
claim. It uses the same inputs, validation, generated `project.tcl`, lint
step, timeouts, cancellation and failure excerpt as the full flow, in
`workdir/<ip>/fpga-synth/` (deleted and recreated first; `worst_path.tcl`
is not written), and never touches `workdir/<ip>/fpga/`, so a full-flow
result is never replaced by a synthesis-only one, and the nightly metrics
(which read only `fpga/metrics.json`) never see it.

After `map` passes, the tool parses `output_files/<top>.map.summary` and
writes `synthesis.json` (two-space indent): `ip`, `board`, `schema` (`1`),
`stage` (`synthesis`), `quartus_version`, `family`, `device` (`null` when
the summary has no device line), `top`, and `resources` with the used
counts `logic_elements`, `registers`, `memory_bits`, `dsp_9bit`, `pins`
and `plls` (a `used / total` value keeps the used part; a missing optional
line is `null`), plus `flow` as in [metrics](#metrics). It has no `timing`
key. A status other than `Successful`, no logic-element or register line,
a malformed count or an unreadable summary is a report error (exit 1, no
`synthesis.json`). It prints the family, the resource counts and the
[warnings](#warnings) summary.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Every selected IP compiled, `metrics.json` written, timing met (`--synth-only`: synthesized and `synthesis.json` written). |
| 1 | A step failed or timed out, a report could not be parsed, or timing is not met (metrics are still written). |
| 2 | Usage or input error: unknown IP, IP without `src/fpga/<ip>/`, bad inputs or `cfg/fpga.yaml`, filelist error, Quartus not found. |

With several IPs, every IP runs and the worst code wins.

## Output

| Path | Content |
| --- | --- |
| `workdir/<ip>/fpga/project.tcl`, `worst_path.tcl` | Generated scripts. |
| `workdir/<ip>/fpga/<top>.qpf`, `<top>.qsf`, `db/`, `incremental_db/` | Quartus project. |
| `workdir/<ip>/fpga/output_files/` | Reports (`*.rpt`, `*.summary`) and the `.sof`. |
| `workdir/<ip>/fpga/logs/<step>.log` | Step logs. |
| `workdir/<ip>/fpga/worst_path.txt`, `metrics.json` | Parsed inputs and the metrics. |
| `workdir/<ip>/fpga-synth/` | The same layout for `--synth-only`, with `synthesis.json` and no `worst_path.*` or `metrics.json`. |

Because `fpga/` and `fpga-synth/` live in `workdir/<ip>/`, no test may be named `fpga` or `fpga-synth`
(`RESERVED_TEST_NAMES` in `tools/build/ip.py`).

## CI

`.github/workflows/fpga.yml` runs on `[self-hosted, linux, x64, wsl]`, the
WSL host's runners, which have Quartus on `PATH`, with `contents: read` and a 90-minute job timeout. It has
**manual dispatch only**; required CI separately runs `--synth-only` on
affected targets ([CI selection](../ci/SPEC.md#build-ci-vendor)), and the
nightly the full flow on every target.
Input `ip` runs `./build fpga --ip <ip>`,
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
committed; the failure excerpt and the sanitized
[unknown-warning lines](#warnings) printed to the job log are the only parts
of a log that leave the host.

## Unit tests

`tools/fpga/tests/` runs without Quartus:

- `test_fpga_reports.py` parses hand-made report fixtures in
  `tools/fpga/tests/fixtures/` (layout of Quartus Lite Cyclone IV E reports;
  not real tool output, see its README): resources, worst-of-corners timing,
  the worst path, failed fitter status, malformed values, missing panels,
  no clocks, negative slack, and the per-RAM rows (all columns, a missing
  panel, missing columns, `--` cells of a ROM row, a malformed block count), and the synthesis
  summary of `--synth-only` (resource counts, a `used / total` value, a
  missing optional line, a failed status, a missing register line, a
  malformed count, a missing summary).
- `test_fpga_flow.py` builds a synthetic IP tree and checks planning and
  input errors (a filelist defining `SYNTHESIS`, a listed backend file),
  backend selection (the backend file in place in the Quartus sources and
  `project.tcl`, the portable file in the lint command), the generated Tcl (order,
  defines with `SYNTHESIS`, include paths, `--part`),
  deterministic generation, the lint command, Quartus discovery order,
  lint waivers (a waived warning passes, an unwaived one or an invalid
  waiver file fails),
  `cfg/fpga.yaml` (including bad `known_warnings`) and `cfg/backends.yaml`
  loading, the known IDs in `cfg/fpga.yaml` equal to the FPGA BKM table,
  warning-ID parsing of the hand-made `map_warnings.log` (counts, critical
  and indented warnings, lines that are not warnings), message sanitizing
  (checkout-relative and cut absolute paths, a home path, user and host
  names as whole words, e-mail and IPv4 addresses, the length cut),
  and the run loop with fake `quartus_*` scripts
  that replay the fixtures: metrics written with `warning_ids`, the summary's
  known and unknown counts with and without the repository's known IDs, a failing step stops the flow
  and prints every error line of the hand-made `map_fail.log` (errors more
  than 30 lines before its end) before the tail,
  lint failure, timing not met, and a step timeout killing its group;
  `--synth-only` (only `project` and `map` run, in `fpga-synth/`, fit and
  timing never start, `synthesis.json` without timing, `fpga/` untouched; a
  failing `map` or a missing summary writes nothing); and the CLI
  (`--dry-run`, `--dry-run --synth-only`, `--all`, missing Quartus, IP
  without `src/fpga/<ip>/`).

The cancellation integration tests drive `ci vendor` through the FPGA CLI
into fake Quartus executables. A fake map step starts a writing grandchild;
SIGINT and SIGTERM must stop the group and its writes, return 130, and
leave later steps and success metrics absent. Handler restoration is
checked on ordinary completion and timeout. Process-creation tests inject
cancellation before and after spawning a real child, before its handle is
returned to the caller, and require the child to be killed and reaped.
