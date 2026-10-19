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
| `tools/build/slots.py` | Host [CPU slots](#cpu-slots) shared by every `sim` and `regress` run. |
| `tools/build/cache.py` | The host [build cache](#build-cache): content-keyed builds and pass records. |
| `tools/build/logexcerpt.py` | The [failure excerpt](#failure-excerpt) the vendor flows print for a failed step. |
| `tools/regress/` | Regression lists, selection and summary; see the [regress spec](../regress/SPEC.md). |
| `tools/fpga/` | Quartus FPGA flow and report parsing; see the [fpga spec](../fpga/SPEC.md). |
| `tools/labels/` | GitHub label standard, sync and check; see the [labels spec](../labels/SPEC.md). |
| `tools/pr/` | Read-only pre-check of a PR's recorded evidence; see the [pr spec](../pr/SPEC.md). |
| `tools/checks/` | `./build check`; see the [checks spec](../checks/SPEC.md). |
| `tools/ci/` | `cfg/ci.yaml` rules and `./build ci`; see the [CI spec](../ci/SPEC.md). |
| `tools/arch/` | Architecture-model perf scenarios; see the [arch spec](../arch/SPEC.md). |
| `tools/metrics/` | Nightly metrics, the data guard and the `nightly-red` issue; see the [metrics spec](../metrics/SPEC.md). |

## Command line

```
./build <command> [--src-root DIR] [--workdir DIR] [options]
```

| Command | Status |
| --- | --- |
| `sim` | Run one test of one IP (below). |
| `regress` | Run regression levels; see the [regress spec](../regress/SPEC.md). |
| `clean [--ip ID]...` | Delete the whole workdir. With `--ip`, delete `workdir/<ip>/` for each given IP or the [shared unit](#shared-unit) (sub-IP output is a sibling, `workdir/<ip>_<sub>/`). |
| `labels` | Sync and check GitHub labels; see the [labels spec](../labels/SPEC.md). |
| `pr check NUMBER` | Read-only pre-check of a PR body's recorded evidence; see the [pr spec](../pr/SPEC.md). |
| `ci unit\|summary\|status` | `ci-gate` helpers: unit-test suites, the job summary and the data-branch `ci-gate` status; see the [CI spec](../ci/SPEC.md). |
| `metrics collect\|commits\|guard\|red` | Nightly metrics records, the per-commit series, the data guard and the `nightly-red` issue; see the [metrics spec](../metrics/SPEC.md). |
| `wiki build\|serve` | Build the wiki site into `<workdir>/wiki-site`, or serve an existing build; see the [wiki site spec](../wiki_site/SPEC.md). |
| `check` | Static checks; see the [checks spec](../checks/SPEC.md). |
| `fpga (--ip ID... \| --all) [--part P] [--timeout S] [--dry-run]` | Quartus compile and `metrics.json` for IPs with `fpga/<ip>/`; see the [fpga spec](../fpga/SPEC.md). |
| `asic --ip ID [--platform P] [--timeout S] [--dry-run]` | ORFS flow and `metrics.json` for one IP with `asic/<ip>/`; see the [asic spec](../asic/SPEC.md). |
| `arch --ip ID [--scenario NAME]` | Perf scenarios of a top-level IP's architecture model and `perf.json`; see the [arch spec](../arch/SPEC.md). |

Options shared by every command. A command with actions (`wiki build`,
`labels sync`, `ci unit`, `metrics collect`, ...) accepts them before or
after the action; if both are given, the one after the action wins.

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

The source tree is kind-first ([layout](../../project/layout.md)): every
folder directly under the source root is a kind (`rtl`, `dv`, `arch`,
`fpga`, `asic`), and an IP owns `<kind>/<id>/` under each kind it has. An IP
is a folder `rtl/<id>/` that holds `<id>.rtl.f` (`build.ip.discover`); its
**id** is that folder's name, for example `a`, or `a_b` for a sub-IP of `a`.
`rtl/common/` (and `dv/common/`) holds shared blocks and is never an IP;
hidden folders are skipped. IPs are sorted by id. No IP name is hard-coded
anywhere in `tools/` or `cfg/`. The id `regress` is reserved because
`workdir/regress/` holds the regression summary.

Each IP's `rtl/<id>/<id>.rtl.f` is resolved once at discovery. Every IP
whose resolved `.rtl.f` tree includes `X`'s `.rtl.f`, directly or through
another IP, is an **ancestor** of `X` (`Ip.ancestors`). `X` is a **sub-IP**
of each ancestor `P` whose id plus `_` prefixes its own (`X` is
`P_<name>`; `Ip.parents`); a **top-level IP** has no such parent. So a
top-level IP may integrate other top-level IPs through `-F` and they stay
top-level, with their own `arch/`, `fpga/` and `asic/`
([layout](../../project/layout.md#ips-and-sub-ips)); an id that names a
parent its filelists do not confirm is a layout-check finding. A `.rtl.f` that does
not resolve reaches nothing here (the filelist check reports it). A `-F`
cycle is such a failure: no filelist in it resolves, so every IP in the
cycle counts as top-level and the filelist check fails.

Each IP has these files:

| Path | Meaning |
| --- | --- |
| `rtl/<id>/<id>.rtl.f` | Synthesizable filelist; this file is what marks `rtl/<id>/` as an IP. |
| `dv/<id>/<id>.sim.f` | Simulation filelist, used by `sim` and `regress`. |
| `dv/<id>/` | Python root for cocotb test modules (`module: tests.test_x` is `dv/<id>/tests/test_x.py`). |
| `dv/<id>/regress/level{0,1,2}.yaml` | Regression lists. |
| `arch/<id>/`, `fpga/<id>/`, `asic/<id>/` | Top-level IPs only: the architecture model and its perf scenarios ([arch spec](../arch/SPEC.md)), FPGA and ASIC inputs. |

### Shared unit

The shared blocks in `rtl/common/` and its group folders are verified together as the **shared
unit**, id `common` ([layout](../../project/layout.md#shared-blocks)). It
exists when `dv/common/common.sim.f` exists (`build.ip.shared_unit`), and
it has an IP's DV files: `dv/common/common.sim.f`, `dv/common/` as the
Python root and `dv/common/regress/level{0,1,2}.yaml`. It is not an IP: it
is never top-level, has no `rtl/common/common.rtl.f`, HAS, `arch/`, `fpga/`
or `asic/`, and `check`, `fpga`, `asic`, `arch` and `labels` do not take it.
`ci unit` runs its `models/` tests like an IP's.
`sim`, `regress` and `clean` choose from every IP plus the shared unit
(`build.ip.units`), so `--ip common` works there and its output is
`workdir/common/`.

A **shared block** is a filelist `rtl/common/<block>.rtl.f` or, one folder
down, `rtl/common/<group>/<block>.rtl.f` (`build.ip.shared_blocks`, sorted
by block name; deeper filelists are not blocks); its top module is
`<block>`. `build.ip.block_group` gives its group folder (none for a block
directly in `rtl/common/`), and its wiki pages sit in the same group folder
under `wiki/src/{rtl,dv}/common/`. Which blocks belong in which group is a
`layout` check rule ([checks SPEC](../checks/SPEC.md#layout-check)).

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

`synthesis_defines(resolved, filelist)` returns the defines a synthesis
flow (FPGA, ASIC) passes: the filelist's plus `SYNTHESIS`
(`SYNTHESIS_DEFINE`), which compiles out the
[assertion macros](../../bkm/rtl.md#assertions). A filelist that defines
`SYNTHESIS` itself is an error, since simulation would then compile the
synthesis view.

## Simulation: `./build sim`

```
./build sim --ip <id> --test <name> [--seed N] [--waves] [--slots DIR] [--no-cache]
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
`dv/<id>/<id>.sim.f`. A bare define is passed as `-DNAME=`. The test entry's
`top` is the HDL toplevel, and its `params` become top-level parameter
overrides (`-G`). An entry with `backend: <kind>` swaps each source that has
that flow's [backend file](../../project/layout.md#source-backends) beside
it for that file and appends the kind's `dv_models`
(`build.backends`, from `cfg/backends.yaml`); an unknown kind or a filelist
that lists a backend file is a build `error`. Every build passes `--assert` (`BUILD_ARGS` in `sim.py`),
so the [assertion macros](../../bkm/rtl.md#assertions) are live in every
simulation; `SYNTHESIS` is never defined.

Each build and each test runs in a child process
(`python3 -B tools/build/sim.py build|test <job.json>`). The parent never
imports cocotb, and tests can run in parallel. The child's import path holds
the IP's `src/dv/<ip>/` and `tools/`, so a test can import the
[functional-coverage helper](../regress/SPEC.md#functional-coverage)
`regress.funcov`. Every child runs in its own process group:

- A test that runs past its `timeout_s` has its group killed.
- A build has a fixed wall-clock bound, `BUILD_TIMEOUT_S` (1800 s) in
  `sim.py`. Exceeding it is a build `error`.
- On Ctrl-C, every active group is killed and the command exits 130.
- With [CPU slots](#cpu-slots), a child starts only once it holds a slot.

The filelist is resolved before the build child starts. A resolve failure
is a build `error`, with the `file:line` message as the detail and in
`build.log`. Output layout (`<ip>` is the IP id; a sub-IP's output is a
flat sibling):

| Path | Content |
| --- | --- |
| `workdir/<ip>/build/<top>[-<hash>]/` | Verilator build and `build.log`. `<hash>` (8 hex digits) is added when the test has `params` or a `backend`, waves are on or it is a coverage build, so different elaborations never share a directory. With the [build cache](#build-cache) on, only `build.log` stays here (it names the cache entry); the executable runs from the entry. |
| `workdir/<ip>/<test>/<seed>/` | `results.xml`, `sim.log` (the full simulator output), waves (`dump.vcd`) with `--waves`, and `coverage.dat` and `funcov.json` in a coverage run ([regress coverage](../regress/SPEC.md#coverage)). This directory is cleared before each run. |

Because test directories share `workdir/<ip>/` with the tools' own output,
a test may not be named `build`, `fpga` and `fpga-synth` (the [fpga tool's](../fpga/SPEC.md#output) output),
`asic` and `asic-synth` (the [asic tool's](../asic/SPEC.md#output) output), `arch` (the
[arch tool's](../arch/SPEC.md#behavior-and-output) output) or `coverage`
(the [merged coverage](../regress/SPEC.md#coverage)).

A test's status comes from `results.xml`:

| Status | Meaning |
| --- | --- |
| `pass` | Every test case passed and the simulator exited 0. |
| `fail` | At least one test case has a failure or error. |
| `timeout` | The run exceeded `timeout_s` and was killed. |
| `error` | The build failed, `results.xml` is missing, empty or unreadable (for example truncated), or the simulator exited nonzero without a recorded failure. |

## CPU slots

Several agents share one host. `--slots DIR` on `./build sim` and
`./build regress` (default `$BUILD_SLOTS`; neither: no slots) names a slot
directory holding one lock file per CPU, `slot-<i>.lock` for `i` in
`0 .. N-1`, where `N` is the number of CPUs the process may run on (its
affinity mask). The directory is created when missing; one that cannot be
created or written is a usage error (exit 2).

- Each build and each test child holds one slot, an exclusive `flock` on its
  file, for as long as it runs: it scans the files for a free one and, when
  all are taken, retries every 0.2 s. Its wall time and timeout start once
  the slot is held, so waiting never counts as test time or a timeout.
- Every run that names the same directory shares the `N` slots, so together
  they never run more children than the host has CPUs, whatever `--jobs`
  each asked for; a small run waits only for a free CPU, not for another
  run to finish.
- The kernel releases a lock when its holder exits, so a killed run never
  leaves a slot taken. Ctrl-C while waiting starts nothing and exits 130.
- A slot is checked against the stop flag after it is taken, and a child
  starts under the lock that the Ctrl-C handler snapshots: a child is either
  killed by Ctrl-C or never started.
- A run prints `slots: DIR (N CPU slots ...)` before its first child.

Limits. Slots bound child processes, not busy CPUs: a Verilator build may
use more than one CPU. Each run derives `N` from its own affinity mask, so
runs with different masks see different counts. Waiters poll from slot 0
with no queue, so there is no fairness between runs. A run killed with
SIGKILL frees its slots while its children, in their own sessions, keep
running.

The [DevOps BKM](../../bkm/devops.md#local-evidence) owns the policy (one
directory per host for every agent). Tests: `tools/build/tests/test_slots.py`
(exclusive and released slots, waiting, a slot held by another process, Ctrl-C
while waiting, `$BUILD_SLOTS` and `--slots`, an unusable directory).

## Build cache

Every Verilator build is keyed by its content and kept in one cache per
host, outside every checkout, so a later run, another worktree or another
CI job on the same host reuses it instead of compiling again (#327).

- **Location:** `$BUILD_CACHE`, else
  `${XDG_CACHE_HOME:-~/.cache}/<repo>/build`, where `<repo>` is the name of
  the main worktree's folder (as the [specs cache](../specs/SPEC.md) does),
  so every worktree of a clone shares it. A directory inside the
  repository is refused (exit 2): a job checkout's clean would delete it.
  `$BUILD_CACHE=off` or `--no-cache` on `sim` and `regress` turns it off
  (and ccache with it, below): every build compiles afresh in `workdir/`,
  as before the cache.
- **Key:** sha256 of the build settings (top, `params`, waves, coverage,
  the Verilator options of `sim.py`, the filelist's defines), the build
  driver (every `tools/build/*.py`, so a change to how the build is invoked,
  such as a new runner argument in the build child, is a new key), the
  tools (`verilator --version`, its path and `$VERILATOR_ROOT`, the C++
  compiler's `--version` (`$CXX`, default `g++`), the cocotb version and
  install, whose library path is linked into the executable, and the
  Python), every source in filelist order and every file under every
  include directory.
  Files are named by their path relative to the worktree (the source root's
  parent), so the same tree in another worktree has the same key. The key
  is content only, never a time: a changed byte in any input is a new
  entry, and nothing stale is ever reused.
- **Entry:** `<cache>/builds/<top>-<key>/` holds the simulation executable
  (the only file a test needs), the build's `build.log` and `meta.json`
  (key, IP, settings and the worktree it was compiled from), written last.
  After a build the object files are deleted from `workdir/` (hundreds of
  MB per build); the log stays and names the entry. A hit writes a
  `build.log` that names the entry and takes no slot and no time.
- **Concurrent writers:** a build is copied into a private temporary
  directory in the cache and renamed into place, so a reader never sees a
  partial entry; when two runs build the same key at once, the first rename
  wins and the other run keeps its copy aside and uses the winner's. A
  failure to store (a full disk) is a warning in `build.log`, never a
  failed build.
- **Paths of a reused build:** Verilator records each source by the path it
  was compiled from, which may be another worktree with the same content.
  A coverage run's `coverage.dat` is rewritten to this worktree's paths
  after the run, so merging, the DV-only exclusion and annotation see this
  tree; an assertion message still names the compiling worktree's path
  (the content is identical). When the file names no source under the
  compiling tree (for example a checkout reached through a symlink), it is
  left as it is and the run's `sim.log` gets a `warning: coverage:` line.
- **Pruning:** at the end of every `regress` run, entries and
  [pass records](../regress/SPEC.md#reuse-of-unchanged-model-checks)
  unused for 14 days (`MAX_AGE_DAYS`), and temporary directories older
  than a day (a killed writer's), are removed; then, while the entries
  exceed 20 GiB (`MAX_BYTES`), the least recently used ones go. A hit marks
  its entry used. An entry is renamed out of its key's place before it is
  deleted, so a concurrent lookup never finds a half-deleted one.
- **ccache:** with the cache on and `ccache` on `PATH`, the build child
  sets `OBJCACHE=ccache` for Verilator's generated makefile, with
  `CCACHE_BASEDIR` the build directory, `CCACHE_NOHASHDIR` and
  `CCACHE_SLOPPINESS` for the generated headers' fresh timestamps and
  precompiled headers; so a build whose key changed (a one-line RTL
  change) recompiles only the C++ files whose content changed. Without
  ccache, or with the cache off, `OBJCACHE` is empty. The objects go to
  ccache's own directory (its configuration or `$CCACHE_DIR`, by default
  `~/.cache/ccache`): one per user and host, outside every checkout and
  shared by every run, so a host keeps its warm compiler cache.
- **Cold builds are bounded:** each build compiles with cocotb's `make -j`
  (at most `COMPILE_JOBS` = 4 compilers), and a compiler of the generated
  C++ may need up to 2 GiB (`COMPILE_MEM_BYTES`). At most
  `compile_slots()` builds compile at once: the compilers that the host's
  CPUs and `MemAvailable` (read once, at the run's first build) allow,
  divided by 4, at least one. A cache hit takes no compile slot. Without
  this bound a cold run (an empty ccache: a new host, a Verilator upgrade,
  eviction) started `--jobs $(nproc)` builds of four compilers each, and
  on a CI host 16 of 37 builds failed with
  `g++: fatal error: Killed signal terminated program cc1plus` (#327).
- **Output:** `sim` prints `build cache: <dir>` (the home directory as
  `~`, since CI logs are public) and `build pass (cached)` on a hit;
  `regress` prints `[build] <ip>:<key> pass cached (0.0s)` per hit, a
  final `build cache: <hits>/<builds> builds reused (<dir>)`, and records
  `build_cache` and each build's `cached` in the summary
  ([regress SPEC](../regress/SPEC.md#summary)).

**Trust.** The cache is content-keyed, so a reused entry is what a fresh
build of the same inputs makes. Only trusted runs write it: on the CI hosts
the [fork guard and the fork rules](../../bkm/devops.md#continuous-integration)
keep fork code off the runners, so every writer runs reviewed or same-repository
code, and locally only the host's own agents write it. The
[DevOps BKM](../../bkm/devops.md#build-cache) owns the CI policy.

Tests: `tools/build/tests/test_cache.py` (location default, override,
`off` and refusal inside the repository; key equality across worktrees and
change on every input; atomic store, a concurrent writer and partial
entries; pruning; a second build is a hit and runs; the coverage path
rewrite; ccache settings in the build child; the cold-build bound; the
driver in the key; pruning by size). Every tools test runs with a
private `$BUILD_CACHE` (`tools/conftest.py`).

## Failure excerpt

When a vendor flow step (`./build fpga`, `./build asic`) fails or times
out, the flow prints an excerpt of that step's log, so the tool's own error
messages reach the job log even when they are far from its end:

1. `--- error lines in <log>: <N> ---`, or `<N> (first 50 shown)` when there
   are more than 50, or `none matched`;
2. the matching lines in log order, at most 50, then
   `... <M> more error line(s) in <log>` when some were cut;
3. `--- last <n> lines of <log> ---` and the last 30 lines.

Each flow passes its tool's error pattern (in its own SPEC);
`logexcerpt.py` knows no tool.

## Unit tests

```
python3 -m pytest tools
```

`tools/pytest.ini` puts `tools/` on the path, collects every tool's tests
and excludes `fixtures/`. It uses pytest's default (prepend) import mode, so
test file basenames must be unique across `tools/`. The
fixture source tree `tools/build/tests/fixtures/src/` is a counter IP with a
flat sub-IP (`counter_inc`), and it exercises `-F`, `+incdir+`, `+define+`, `params`, seeds and one
test per level. The end-to-end tests (real build, pass, fail, timeout, build
error, waves, a broken filelist in one IP, and the `regress` and `sim` CLI)
are skipped when Verilator or cocotb is missing. Process-control tests use a
sleeping shell script in place of the child, so they need no simulator. A
killed group's grandchildren (such as the simulator executable) exit
asynchronously after the leader is reaped, so tests that check the group is
gone poll with a deadline instead of asserting right after the kill. `test_ip.py` covers discovery on synthetic kind-first trees: flat
sub-IPs, top-level versus sub-IP by `-F` reachability and id prefix (direct,
transitive and from two parents), integrated top-level IPs (a reached IP
without its includer's prefix stays top-level), `common/` and name mismatches that are not IPs, a broken
filelist, ownership by kind folder (never by id prefix), `locate`, and the
shared blocks and shared unit (`units` adds it only with
`dv/common/common.sim.f`; it owns the `common/` folders). `test_logexcerpt.py`
covers the failure excerpt: error lines before the tail in log order, the
cap with its count and cut note, exactly the cap, and no error lines.
`test_backends.py` covers `build.backends`: the repository configuration and
its stand-ins, `cfg/backends.yaml` errors (shape, suffix, `dv_models`
outside `dv/`, a shared suffix), backend naming (the portable file must
exist), in-place selection per flow kind, and the refusal of a listed
backend file; `test_sim.py` covers a backend build's sources and key and an
unknown kind as a build `error`.

Python dependencies (pinned in `tools/requirements.txt`): `PyYAML`,
`pytest`, `cocotb`.
