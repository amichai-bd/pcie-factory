# Build tool: `./build`, filelists, IP discovery and simulation

`./build` is the single entry point. It's a small Python launcher at the
repository root (`python3 -B`) that adds `tools/` to `sys.path` and calls
`tools/cli/main.py`. Everything else is in `tools/`:

| Module | Owns |
| --- | --- |
| `tools/cli/main.py` | Argument parsing and the subcommand dispatch table (`COMMANDS`). |
| `tools/build/filelist.py` | The `.f` filelist resolver. |
| `tools/build/ip.py` | Repository context (source root, workdir) and IP discovery. |
| `tools/build/sim.py` | Verilator + cocotb build and test driver. |
| `tools/regress/` | Regression lists, selection and summary; see the [regress spec](../regress/SPEC.md). |
| `tools/fpga/` | Quartus FPGA flow and report parsing; see the [fpga spec](../fpga/SPEC.md). |
| `tools/labels/` | GitHub label standard, sync and check; see the [labels spec](../labels/SPEC.md). |
| `tools/checks/` | `./build check`; see the [checks spec](../checks/SPEC.md). |
| `tools/metrics/` | Nightly metrics, the data guard and the `nightly-red` issue; see the [metrics spec](../metrics/SPEC.md). |

## Command line

```
./build <command> [--src-root DIR] [--workdir DIR] [options]
```

| Command | Status |
| --- | --- |
| `sim` | Run one test of one IP (below). |
| `regress` | Run regression levels; see the [regress spec](../regress/SPEC.md). |
| `clean [--ip ID]...` | Delete the whole workdir. With `--ip`, empty `workdir/<ip>/` for each given IP but keep the directories of its sub-IPs, which nest there. |
| `labels` | Sync and check GitHub labels; see the [labels spec](../labels/SPEC.md). |
| `metrics collect\|commits\|guard\|red` | Nightly metrics records, the per-commit series, the data guard and the `nightly-red` issue; see the [metrics spec](../metrics/SPEC.md). |
| `wiki build\|serve` | Build the wiki site into `<workdir>/wiki-site`, or serve an existing build; see the [wiki site spec](../wiki_site/SPEC.md). |
| `check` | Static checks; see the [checks spec](../checks/SPEC.md). |
| `fpga (--ip ID... \| --all) [--part P] [--timeout S] [--dry-run]` | Quartus compile and `metrics.json` for IPs with `fpga/`; see the [fpga spec](../fpga/SPEC.md). |
| `asic --ip ID [--platform P] [--timeout S] [--dry-run]` | ORFS flow and `metrics.json` for one IP with `asic/`; see the [asic spec](../asic/SPEC.md). |

Options shared by every command:

- `--src-root DIR` is the tree to discover IPs in. If it isn't given, the tools
  use `$BUILD_SRC_ROOT`, then `<repo>/src`. Tests and fixtures use it.
- `--workdir DIR` is where output goes. If it isn't given, the tools use
  `$BUILD_WORKDIR`, then `<repo>/workdir`.

Configuration (`cfg/`) always comes from the repository that holds the
launcher.

Exit codes: `0` means success, `1` means a test or build failed, and `2`
means a usage or configuration error (bad filelist, regression list or IP id,
missing Verilator or Quartus, git error, or a command that isn't implemented yet).
Errors are printed as `error: <message>` and name the file and line where
there is one.

## IP discovery

An IP is any directory `D` under the source root that contains
`rtl/<basename(D)>.rtl.f`. Its **id** is `D` relative to the source root,
using `/` as the separator, for example `a` or `a/b` for sub-IP `b` of `a`.
The tools don't descend into `rtl/`, `dv/`, `fpga/`, `asic/` or hidden
directories when they look for IPs. IPs are sorted by id. No IP name is
hard-coded anywhere in `tools/` or `cfg/`. The id `regress` is reserved
because `workdir/regress/` holds the regression summary.

Each IP has these files:

| Path | Meaning |
| --- | --- |
| `rtl/<name>.rtl.f` | Synthesizable filelist; this file is what marks the directory as an IP. |
| `dv/<name>.sim.f` | Simulation filelist, used by `sim` and `regress`. |
| `dv/` | Python root for cocotb test modules (`module: tests.test_x` is `dv/tests/test_x.py`). |
| `dv/regress/level{0,1,2}.yaml` | Regression lists. |

## Filelists

Filelists use plain Verilator `.f` syntax with one entry per line:

| Line | Meaning |
| --- | --- |
| *(blank)* or `// ...` | Ignored. A `//` comment can also follow an entry. |
| `<path>` | A source file. |
| `-F <path>` | Include another filelist. |
| `+incdir+<dir>` | An include directory (exactly one per line). |
| `+define+NAME[=VALUE]` | A preprocessor define (exactly one per line). |

All paths are relative to the filelist that contains them. Absolute paths
are allowed but discouraged. The resolver rejects every other option (such
as `-f`, `-v`, `-y` or `+libext+`), globs (`* ? [ ] { }`), `$VAR` and `~`,
paths with spaces, and lines with more than one entry. Each rejection is an
error that names `file:line`.

The resolver flattens the tree depth-first in file order and returns:

- `sources`: absolute paths, de-duplicated, keeping the first occurrence.
- `incdirs`: absolute paths, de-duplicated, keeping the first occurrence.
- `defines`: name to value (`None` for a bare `+define+NAME`). The same
  value repeated is fine; a different value is an error.
- `filelists`: every filelist visited, starting with the root.

These are also errors: a source, include directory or filelist that is
missing, and a `-F` cycle (the message shows the chain `a.f -> b.f -> a.f`).
A filelist reached twice through different branches (a diamond) is not a
cycle and is flattened once.

`Resolved.reaches(path)` answers the reverse question for `regress
--affected`. It is true when `path` is a source, a visited filelist, or a
file anywhere below one of the include directories. Subdirectories count
(``include "sub/x.svh"``), and so do new files.

## Simulation: `./build sim`

```
./build sim --ip <id> --test <name> [--seed N] [--waves]
```

The command looks `<name>` up across the IP's level lists (test names are
unique per IP; see the regress spec), then builds and runs that one test with
one seed. The default seed is the test's first regression seed:

- With a `seeds` list, it's the first entry in the list's own order, not
  the sorted order that regress prints.
- With a count, it's derived seed 0.

`--seed N` overrides the default.

The simulator is Verilator, driven by cocotb's Python runner
(`cocotb_tools.runner`). The cocotb version is pinned in
`tools/requirements.txt`; the tools don't check it themselves. The tools
look for `verilator` in this order:

1. `PATH`.
2. `$VERILATOR_ROOT/bin`.
3. `<prefix>/bin`, where `<prefix>` is `verilator.prefix` in
   `cfg/host.yaml`.

If none of these has it, the command fails with exit code 2.

Sources, include directories and defines come from the resolved
`dv/<name>.sim.f`. A bare define is passed as `-DNAME=`. The test entry's
`top` is the HDL toplevel, and its `params` become top-level parameter
overrides (`-G`).

Each build and each test runs in a child process
(`python3 -B tools/build/sim.py build|test <job.json>`). The parent never
imports cocotb, and tests can run in parallel. Every child runs in its own
process group:

- A test that runs past its `timeout_s` has its group killed.
- A build has a fixed wall-clock bound, `BUILD_TIMEOUT_S` (1800 s) in
  `sim.py`. Exceeding it is a build `error`.
- On Ctrl-C, every active group is killed and the command exits 130.

The filelist is resolved before the build child starts. A resolve failure
is a build `error`, with the `file:line` message as the detail and in
`build.log`. Output layout (`<ip>` is the IP id,
so a sub-IP nests under its parent):

| Path | Content |
| --- | --- |
| `workdir/<ip>/build/<top>[-<hash>]/` | Verilator build and `build.log`. `<hash>` (8 hex digits) is added when the test has `params` or waves are on, so different elaborations never share a directory. |
| `workdir/<ip>/<test>/<seed>/` | `results.xml`, `sim.log` (the full simulator output), and waves (`dump.vcd`) with `--waves`. This directory is cleared before each run. |

Because a test directory and a sub-IP directory share `workdir/<ip>/`, a
test may not be named `build`, `fpga` (the [fpga tool's](../fpga/SPEC.md#output) output),
`asic` (the [asic tool's](../asic/SPEC.md#output) output) or after a sub-IP directory.

A test's status comes from `results.xml`:

| Status | Meaning |
| --- | --- |
| `pass` | Every test case passed and the simulator exited 0. |
| `fail` | At least one test case has a failure or error. |
| `timeout` | The run exceeded `timeout_s` and was killed. |
| `error` | The build failed, `results.xml` is missing, empty or unreadable (for example truncated), or the simulator exited nonzero without a recorded failure. |

## Unit tests

```
python3 -m pytest tools
```

`tools/pytest.ini` puts `tools/` on the path, collects every tool's tests
and excludes `fixtures/`. It uses pytest's default (prepend) import mode, so
test file basenames must be unique across `tools/`. The
fixture source tree `tools/build/tests/fixtures/src/` is a counter IP with a
sub-IP, and it exercises `-F`, `+incdir+`, `+define+`, `params`, seeds and one
test per level. The end-to-end tests (real build, pass, fail, timeout, build
error, waves, a broken filelist in one IP, and the `regress` and `sim` CLI)
are skipped when Verilator or cocotb is missing. Process-control tests use a
sleeping shell script in place of the child, so they need no simulator.

Python dependencies (pinned in `tools/requirements.txt`): `PyYAML`,
`pytest`, `cocotb`.
