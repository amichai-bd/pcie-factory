# Regress tool: `./build regress`

`./build regress` runs per-IP regression levels on Verilator and cocotb, and
it writes a JSON summary of the run. It uses the [build tool](../build/SPEC.md)
for IP discovery, filelists and simulation. The code lives in
`tools/regress/` (`schema.py`, `affected.py`, `run.py`, and `coverage.py`
and `funcov.py` for [coverage](#coverage)).

```
./build regress --level N [--level M]... [--ip ID]... [--affected BASE]
                [--jobs N] [--seed S] [--waves] [--coverage] [--dry-run]
                [--rotate KEY [--extra-seeds N]] [--slots DIR] [--no-cache]
                [--src-root DIR] [--workdir DIR]
```

## Regression lists

This page owns the schema of `src/dv/<ip>/regress/level{0,1,2}.yaml`. The
[DV BKM](../../bkm/dv.md#regression-lists) owns how tests are placed in
levels.

An empty level is either a missing file or `tests: []`. An empty file and a
bare `[]` are errors. The DV BKM convention is that all three files exist.
This tool doesn't require that; the
[`./build check` filelist check](../checks/SPEC.md#filelist-check) enforces it.

```yaml
tests:
  - name: counts              # required; [A-Za-z0-9_][A-Za-z0-9_.-]*
    module: tests.test_counter  # required; cocotb module, dotted, under dv/<ip>/
    testcase: counts          # optional; run only this cocotb test function
    top: counter              # required; HDL toplevel module
    seeds: 2                  # optional, default 1; see Seeds
    timeout_s: 60             # required; per-run wall-clock limit (> 0)
    params: {W: 4}            # optional; toplevel parameters (int or string)
    backend: fpga             # optional; build with that flow's source backends
    coverage_seeds: 1         # optional, default 1; see Coverage
    rerun_when: models_change # optional; see Reuse of unchanged model checks
    expect_fatal: a_label     # optional; see Expected fatal stops
coverage_floors:              # optional; see Coverage floors
  line: 90
```

| Key | Required | Type and default |
| --- | --- | --- |
| `name` | yes | String matching `[A-Za-z0-9_][A-Za-z0-9_.-]*`. |
| `module` | yes | Dotted Python module name, importable with `dv/<ip>/` on the path. |
| `top` | yes | HDL identifier. |
| `timeout_s` | yes | Positive number. A run that exceeds it gets the `timeout` status. |
| `testcase` | no | Python identifier. If absent, every `@cocotb.test()` in the module runs. |
| `seeds` | no | Either a **count** `N >= 1` (default `1`) or an explicit **list** of distinct integers in `[0, 2**31)`. |
| `params` | no | Mapping from identifier to int or string, passed as `-G` overrides. |
| `coverage_seeds` | no | Count from `0` to the entry's number of runs, default `1`: with `--coverage`, the first that many of its seeds (in the entry's own order: list order, or derived seeds 0, 1, ...) run on the coverage build and the rest on the plain build ([coverage](#coverage)). `0`: never a coverage run. |
| `rerun_when` | no | `models_change`: a passing run is reused until the models it checks change ([reuse](#reuse-of-unchanged-model-checks)). Requires `coverage_seeds: 0`. |
| `expect_fatal` | no | An assertion label (HDL identifier): the run passes only when the simulation stops on that assertion ([expected fatal stops](#expected-fatal-stops)). Requires `coverage_seeds: 0` and no `rerun_when`. |
| `backend` | no | A flow kind of `cfg/backends.yaml` (an identifier; checked when building). The build swaps each source that has that flow's [backend file](../../project/layout.md#source-backends) beside it for that file and appends the kind's `dv_models`, so the same tests run on the backend. An unknown kind is a build `error`. |

These validation rules apply:

- The top level is a mapping with the key `tests`, which holds a list, and
  optionally `coverage_floors` ([coverage floors](#coverage-floors)); no
  other keys.
- Each entry is a mapping with all four required keys, only the keys listed
  above, and values of the types shown.
- `name` is unique across all three levels of the same IP. It also can't be
  `build`, `fpga`, `asic`, `arch` or `coverage`, because the tools' output
  shares `workdir/<ip>/`.

Every error names the file and the entry index, plus the test name whenever
the entry has a string `name`. For example:
`.../level0.yaml: tests[2] (counts): missing key(s): top`. Validation covers
every discovered IP and the shared unit, not only the selected ones.

## Selection

Three rules decide which tests run:

- **Levels are exclusive.** `--level 1` runs only `level1.yaml`. To combine
  levels, repeat the option (`--level 0 --level 1`). At least one `--level`
  is required.
- **`--ip ID`** (repeatable) restricts the run to those IPs. IPs are flat:
  selecting a parent doesn't select its sub-IPs. Without `--ip`, every
  discovered IP and the [shared unit](../build/SPEC.md#shared-unit) (`common`)
  are selected; `--ip common` selects the shared unit. Everything this page
  says about an IP's lists, seeds and output applies to it.
- **`--affected BASE`** narrows the selection (combined with `--ip`, the
  result is the intersection) to IPs that are affected by changes since
  `git merge-base BASE HEAD`. The changed files are the committed diff, the
  uncommitted diff and untracked files that aren't ignored. Git runs with
  `-z` (paths may contain spaces) and `--no-renames` (a move counts as both
  its old and new path). An IP is affected in any of these cases, using
  the path facts in [`cfg/ci.yaml`](../ci/SPEC.md#selection-rules-cfgciyaml)
  (defaults shown):
  - Every IP is affected when any change is under a `global` path: `tools/`
    or `cfg/`, or the `build` launcher (relative to the git repository root).
    An IP that only such a change selects runs only the levels in
    `global.levels` (default `[0]`) among those requested, and its reason
    reads `global change: <path> (level <levels> only)`, for example
    `(level 0 only)`; an IP that any other rule below selects runs every
    requested level. (`./build ci unit` gets the same selection with the
    bare reason.) A tools change is proved by
    the tools unit tests and a smoke of every IP that shows the tools still
    drive its simulations; a design or DV change by that IP's full set.
  - Every IP is affected when any change is under a `shared_dv` directory
    (`<src-root>/dv/common/`): shared DV Python helpers are reached by no
    filelist, so their users cannot be told apart.
  - A changed file lies in the IP's **own files**: under `src/<kind>/<ip>/`
    for any kind (for the shared unit, `src/rtl/common/` or
    `src/dv/common/`; the latter also selects every IP by the previous rule). A change in sub-IP `a_b`'s folders therefore selects
    `a_b`; it selects the parent `a` only through the next two cases, and
    the same holds for a top-level IP integrating another (`sys` including
    `a` is selected when its `sim.f` reaches the changed file). A
    change under a top-level IP's `src/arch/<ip>/` (the architecture model,
    [arch BKM](../../bkm/arch.md)) is one of its own files, so it selects
    that IP alone.
  - A changed file lies in the `models` directory (`src/dv/<sub>/models/`)
    of an IP the IP's `.rtl.f` reaches (a sub-IP, or a top-level IP it
    integrates; its ancestors in
    [IP discovery](../build/SPEC.md#ip-discovery)): parent models import
    sub-IP models by path ([DV BKM](../../bkm/dv.md#reusing-a-sub-ip-model)),
    which no filelist shows. A change in `src/dv/a_b/models/` selects `a_b`
    and every ancestor, such as `a` and any IP integrating `a`; the reason
    reads `sub-IP model` for a parent and `integrated IP model` otherwise.
  - The IP's `dv/<ip>/<ip>.sim.f` **reaches** a changed file: a source, a
    filelist in its `-F` tree, or a file in one of its include directories.
  - The IP's `sim.f` doesn't resolve (for example, a source it lists was
    deleted). The IP is selected, and its build reports the error.
  - The IP's DV or arch Python **imports** a changed file, directly or
    through other files: a module under the `import_root` of
    `cfg/ci.yaml` (`import pkg.mod`, `from pkg import mod`; a package's
    `__init__.py` counts), or a module of another IP's `arch/<ip>/` or
    `dv/<ip>/<models>/` folder imported by its bare name. Imports are read
    with `ast` (constant `import_module` and `__import__` names too;
    relative imports resolve inside their package); the scan follows
    import-root and model files transitively, skips `tests/` folders under
    the import root, and also matches a deleted module by name. The reason
    reads `imports: <path>`, and the IP runs every requested level even when
    the change is also global. No filelist shows these imports, so without
    the scan a helper change that only a level-1 test exercises, or another
    IP's model change, would not run that IP's tests.

The command prints each selected IP with the reason it was selected, or
`no IPs selected`. An empty selection passes, which is right for a
docs-only change. On a push to `main`, though, the merge base equals `HEAD`,
so `--affected` selects nothing. CI must not pass `--affected` there; it runs
every IP, as the DevOps BKM requires. The `summary.json` fields
`selected_ips` and `selected_tests` make an empty selection visible.

Use `--dry-run` to print the selection (IPs, tests and seeds, and any
[extra rotated seeds](#seed-rotation) marked `(extra)`) without
building or running anything. A dry run neither writes nor removes
`summary.json`.

The pure-Python model unit tests under `src/dv/*/models/` are not part of
any regression level; [`./build ci unit`](../ci/SPEC.md#build-ci-unit) runs
them for the same selection in `ci-gate`.

## Seeds

Each seed is one run of the test, and the seed list comes from one of three
places:

- **A count:** `seeds: N` (the default is `1`) gives N runs. Run `i` uses
  the deterministic seed `crc32("<ip>:<name>:<i>") & 0x7fffffff`, so
  repeated regressions and `./build sim` use the same seeds. Renaming an IP
  (its id) changes its derived seeds; pin a list to keep them.
- **A list:** `seeds: [a, b, ...]` runs exactly those seeds. Its order is
  meaningful: the first `coverage_seeds` seeds of the list, in list order,
  are the coverage runs ([coverage](#coverage)), and `./build sim` without
  `--seed` runs the first one. The order of the result lines and of the
  summary is unrelated to it. Reordering a list is therefore a change: it
  can move a seed into or out of the coverage runs and so change the
  merged coverage and the floors.
- **`--seed S`:** replaces either form with `S, S+1, ...`, using the same
  number of runs.

The seed is passed to cocotb as its random seed, which also seeds Python's
`random`. It's printed in the sim log and recorded in the summary.

## Seed rotation

Pinned seeds give the same stimulus every run, so seeds trimmed from an
entry (#327) are gone for good. `--rotate KEY` adds **extra seeds** that
change with `KEY`, so a run per key (the nightly passes its UTC date,
`YYYY-MM-DD`) explores new stimulus at no cost to the pinned runs (#333):

- **Which entries:** every selected entry of the rotation `level`
  (`cfg/levels.yaml`, below; `2`, the full test) when that level is
  selected. Other levels get none; the pinned runs are unchanged.
- **How many:** `extra_seeds` per entry (`cfg/levels.yaml`), or
  `--extra-seeds N` (a count `>= 0`, only with `--rotate`).
- **Derivation:** extra seed `j` of an entry is
  `crc32("rotate:<KEY>:<ip>:<name>:<j>") & 0x7fffffff` for j = 0, 1, ...,
  skipping any value equal to one of the entry's pinned seeds (after
  `--seed`) or to an earlier extra seed. The same key always gives the same
  seeds, a new key new ones, and a smaller N a prefix. `KEY` matches
  `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` (a date or a run id); anything else
  exits 2.
- **No coverage:** extra runs run on the plain build, never the coverage
  build, and are never merged, so `--coverage` and the floors see exactly
  the pinned runs ([coverage](#coverage)). An entry whose pinned runs all
  use the coverage build (such as a single seed with `coverage_seeds: 1`)
  therefore needs a plain build for its extra runs alone; such
  **extra-only builds** are built (or taken from the build cache) up front
  with the others, even when the budget later stops their runs, carry
  `extra` true in the summary's `builds`, and are charged to no budget. An
  extra run of a `rerun_when` entry follows the
  [reuse](#reuse-of-unchanged-model-checks) rule with its own seed in the
  key; a new key gives new seeds, so it is never reused across nights.
- **Budget:** extra runs start after every pinned run has started, in an
  order shuffled by `crc32("<KEY>:<ip>:<name>:<seed>")`, so a different
  set waits each night when the budget is short. An extra run starts only
  while the CPU time of the extra runs finished so far (a run killed on its
  timeout counts its wall time) is below `budget_s`; runs already started
  finish, so the extra runs' CPU time is at most `budget_s` plus the runs
  in flight when it was reached (at most `--jobs` of them). Each one not started prints `[L<n>] <ip>:<test> seed=<s> not
  started (rotation budget spent)` and is listed in the summary; it is not
  a failure.
- **Failure:** an extra run that does not pass fails the run like any
  other (exit 1) and prints
  `error: extra seed <status>: <ip>:<test> seed=<s>; reproduce: ./build sim --ip <ip> --test <test> --seed <s>`
  to stderr. That command reruns it exactly: the seed alone determines the
  stimulus.

After the level totals the run prints
`rotation <KEY>: <p>/<n> extra runs passed (<N> per level<L> entry), CPU <x>s (budget <b>s), <k> not started`.

`cfg/levels.yaml` holds the rotation settings beside the level budgets;
all three keys are required, and a missing or malformed section is an
error (exit 2) only when `--rotate` is given:

```yaml
rotation:
  level: 2          # the level whose entries get extra seeds
  extra_seeds: 1    # per entry, a count >= 0
  budget_s: 3600    # CPU seconds of extra runs, > 0
```

The [DevOps BKM](../../bkm/devops.md#nightly) owns the values and how they
fit the nightly window.

## Expected fatal stops

An entry with `expect_fatal: <label>` is a negative test of an assertion:
it shows that the assertion fires, for example one guarding an obligation
of the neighbouring layer that legal stimulus never breaks. Its run's
status ([build spec](../build/SPEC.md#simulation-build-sim)) is decided
from the simulator log (`sim.log`), not from `results.xml`:

- `pass` when the first `%Fatal` line of the log is Verilator's message
  for a failed assertion whose scope ends in `<label>`
  (`%Fatal: <file>:<line>: Assertion failed in <scope>.<label>: ...`);
  the detail reads `stopped on assertion <label> as expected`;
- `fail` when the run finished without a fatal (a clean pass or a test
  failure), the detail naming which, or when its first `%Fatal` is any
  other fatal (another assertion, a bare `$fatal`), the detail quoting it;
- `timeout` as for any run.

`./build sim` applies the same rule to the entry. Such an entry needs
`coverage_seeds: 0` (a run stopped by a fatal writes no coverage file),
and it cannot carry `rerun_when` (its result depends on the RTL, which
reuse ignores). The test itself should fail if it reaches its end, so a
stop that never comes is a failure even without this rule.

Tests: `tools/build/tests/test_sim.py` (`test_fatal_label`,
`test_expected_fatal`; in `test_e2e_pass_fail_timeout_and_build_error` the
fixture's labelled assertion passes, another label, a clean pass and a
test failure fail; in `test_e2e_regress_cli_all_levels` an entry passes
on its label and fails on another) and `test_schema.py` (the key's
validation).

## Execution

- **Builds:** every distinct (IP, top, params, waves, coverage, backend) combination is built
  once, before any test runs. A failed build marks all of its tests `error`
  and the run continues with the other builds. A `sim.f` that doesn't
  resolve counts as a failed build, with the `file:line` message as the
  detail.
- **Tests:** builds and tests run on up to `--jobs N` workers (default 1).
  Each test runs with its own `timeout_s`.
- **Build cache:** builds are reused from the host's
  [build cache](../build/SPEC.md#build-cache) when their content key
  matches, and stored there otherwise; `--no-cache` (or
  `$BUILD_CACHE=off`) builds everything afresh and reuses no pass.
- **Slots:** with `--slots DIR` (or `$BUILD_SLOTS`), each build and test also
  holds one of the host's [CPU slots](../build/SPEC.md#cpu-slots) while it
  runs, shared with every other run naming `DIR`.
- **Waves:** `--waves` dumps waves for every test.
- **Verilator:** a missing Verilator is an error (exit code 2), unless no
  tests are selected.
- **Ctrl-C:** the run kills every running build or simulation process
  group, starts nothing new and exits 130. No summary is written.

Each result prints as a line like
`[L0] <ip>:<test> seed=<s> PASS (<t>s)`. Per-test output follows the build
spec's layout.

## Summary

Each (non-dry) run deletes `workdir/regress/summary.json` before it does
anything else. That includes runs that then fail on a configuration error,
so a stale result is never left behind. The run writes the summary again
when it finishes, even if it selected no tests:

```json
{
  "levels": [0, 1],
  "src_root": "/abs/path/src",
  "ips": {"a": "changed: src/rtl/a/a.sv"},
  "selected_ips": 1,
  "selected_tests": 1,
  "builds": [{"ip": "a", "build": "a_top", "status": "pass", "cached": false,
              "runtime_s": 2.1, "cpu_s": 3.9, "log": "a/build/a_top/build.log",
              "extra": false}],
  "tests": [{"ip": "a", "level": 0, "name": "smoke", "seed": 123,
             "status": "pass", "runtime_s": 0.9, "cpu_s": 0.8,
             "log": "a/smoke/123/sim.log", "detail": "1 case(s) passed",
             "coverage": false, "reused": false, "extra": false}],
  "totals": {"level0": {"tests": 1, "passed": 1, "reused": 0, "failed": 0,
                        "runtime_s": 0.9, "cpu_s": 0.8, "budget_s": 120,
                        "over_budget": false}},
  "wall_s": 3.4,
  "build_cache": {"dir": "~/.cache/repo/build", "builds": 1, "hits": 0,
                  "reused_runs": 0},
  "rotation": null,
  "coverage": {"ips": {"a": {
      "runs": 1, "dat_files": 1, "funcov_files": 1, "dir": "a/coverage",
      "line": {"covered": 15, "total": 15, "pct": 100.0},
      "toggle": {"covered": 218, "total": 228, "pct": 95.61},
      "functional": {"covered": 14, "total": 19, "pct": 73.68, "uncovered": 5,
                     "groups": {"a_ctrl": {"covered": 14, "total": 19, "pct": 73.68}}},
      "floors": {"line": 90.0, "covergroups": {"a_ctrl": 70.0}},
      "failures": [], "warnings": []}},
    "passed": true},
  "passed": true
}
```

The fields mean the following:

- `status` is one of `pass`, `fail`, `timeout` or `error`, as defined in
  the build spec.
- Log paths are relative to the workdir.
- `failed` counts every status other than `pass`.
- `runtime_s` in `totals` is the serial-equivalent runtime of the level, the
  sum of its test runtimes (wall time). It doesn't depend on `--jobs`, but
  host load stretches it.
- `cpu_s` of a test is the user plus system CPU time of its child process
  tree (the cocotb runner and the simulator under it, from `wait4`); 0 for a
  test killed on its timeout. `cpu_s` in `totals` is the level's sum, the
  number the [budget](#budgets) is compared with.
- A build's `cached` is true when it came from the build cache (then its
  `runtime_s` and `cpu_s` are 0); its `cpu_s` is the build child's CPU
  time, not counted in any level budget. A test's `coverage` says whether
  it ran on a coverage build; `reused` whether it was a
  [reused pass](#reuse-of-unchanged-model-checks) (0 runtime and CPU), and
  `totals.<level>.reused` counts those. `build_cache` is `null` with the
  cache off; otherwise its directory (home as `~`), the run's builds, how
  many were hits, and the reused runs.
- A test's `extra` is true for an [extra rotated seed](#seed-rotation), a
  build's `extra` for an extra-only build (one only extra runs need).
  Extra runs are left out of `totals`, whose budgets cover the pinned
  seeds. `rotation` is `null` without `--rotate`; otherwise
  `{key, level, extra_seeds, budget_s, runs, passed, failed, cpu_s,
  skipped}`: the extra runs that ran, how many passed and failed, their CPU
  time, and each one not started as `{ip, name, seed}`.
- `coverage` is `null` without `--coverage`. Otherwise it holds, per
  selected IP, the passing runs merged (`runs`) and the files found, each
  metric's `covered`, `total` and `pct` (2 decimals; `null` with no
  points), the same per covergroup under `functional.groups`, the floors
  that applied (per-covergroup functional floors under
  `floors.covergroups`), the floor failure messages and the merge warnings;
  `coverage.passed` is false when any IP missed a floor. When the coverage
  could not be merged, `coverage` is `{"error": "<message>"}` instead and
  the test results are still recorded.
- `passed` is true only when every build and every test passed, and, with
  `--coverage`, the coverage was merged and no floor was missed.

## Coverage

`--coverage` runs each entry's first `coverage_seeds` seeds (default 1) on
a coverage build and the rest on the plain build and, after the tests,
merges each selected IP's coverage runs, prints a table and checks the
IP's floors. Every seed still runs and must pass; only the instrumentation
is limited, since a sweep's further seeds mostly repeat the first's points
(#327). An entry whose further seeds reach points the first does not sets
`coverage_seeds` higher, with the reason in its DV plan; the floors on the
merged levels are the guard. The [shared unit](../build/SPEC.md#shared-unit) is a unit like
an IP here: `common` gets its own row, `workdir/common/coverage/` and
floors from `dv/common/regress/`. Without it, nothing below happens and the summary's `coverage`
is `null`.

| Metric | Source | Unit |
| --- | --- | --- |
| `line` | Verilator `--coverage-line`: statement-block (`line`) and `branch` points | points hit at least once / points |
| `toggle` | Verilator `--coverage-toggle`: each signal bit's 0→1 and 1→0 transitions | points hit / points |
| `functional` | Bins and cross bins sampled through [`regress.funcov`](#functional-coverage) | bins hit / bins |

A coverage build has its own build directory (the build key's hash includes
it), so coverage and plain builds never share one. Each test run writes
`coverage.dat` (via `+verilator+coverage+file+`) and, when its test samples a
covergroup, `funcov.json` into its test directory.

**Merge.** Only coverage runs that passed are merged: a failing run may have
reached code or bins without its checks holding, so its coverage would
credit behaviour that was not verified. A passing run that left no
`coverage.dat` prints `warning: coverage: <ip>: <n> passing run(s) left no
coverage.dat: <test>/<seed>, ...` to stderr and is listed in the IP's
`warnings`; it doesn't change the exit code. Per IP,
`verilator_coverage --write` (installed next to `verilator`) merges the
`.dat` files, and the functional bins are summed over runs, the bin set
being their union. A missing `verilator_coverage`, a failed merge or an
unreadable `funcov.json` is an error: the tests still run, the summary
records `coverage: {"error": ...}`, the run prints
`error: coverage: <message>` and exits with code 2. Output, replaced on
every coverage run of the IP:

| Path | Content |
| --- | --- |
| `workdir/<ip>/coverage/merged.dat` | Merged Verilator coverage; `verilator_coverage --annotate <dir> merged.dat` shows it per source line. |
| `workdir/<ip>/coverage/merged.info` | `verilator_coverage --write-info` output for lcov tools. It holds per-line `DA` records and every point as branch data (`BRDA`, `BRF`/`BRH`), but no `LF`/`LH` line totals, so lcov's line summary doesn't match the table. |
| `workdir/<ip>/coverage/functional.json` | Merged bins per covergroup and item, and a summary with every uncovered bin as `group.item.bin`. |

Code coverage counts the RTL that `<ip>.rtl.f` reaches, so a parent IP's
code coverage includes its sub-IPs' RTL as its tests exercise it. Points in
DV-only sources (files `<ip>.sim.f` reaches through `<ip>.dv.f` but
`<ip>.rtl.f` does not, such as testbench tops and bind checkers; for the
shared unit, files `common.sim.f` reaches that no shared block's `.rtl.f`
does) are left out
of the table and the floors; `merged.dat` and `merged.info` still hold
them. A point is attributed to the file Verilator reports, so an
`` `include``d file counts unless it is itself a DV-only source. Verilator
keeps one set of points per parameterization of a module (the point's
`page`), so a module built with several parameter sets, in one build or
across the IP's builds, counts each set; a set whose builds run few tests
lowers the metric. A metric
with no points (for example, no test samples a covergroup) is reported
`n/a`.

**Table.** After the level totals, the run prints one row per selected IP:

```
coverage  line            toggle            functional      floors
a         100.0% (15/15)  95.6% (218/228)   73.7% (14/19)   none
```

The `floors` column is `none` (no floor applies), `ok` or `FAIL`.

**Cost.** Measured on the since-retired M0 seed design's L0+L1+L2 (24 runs, `--jobs 4`), coverage builds
added under 1% of CPU time and functional sampling about 8% (99.5 s to
108 s of CPU, mean of three runs each); the designs are small, so the
Python testbench dominates. Type-strict bins raised the cost of one sample
from about 0.8 µs to 1.2 µs, which kept the same run's CPU time within
noise (75 to 79 s on another host, two runs each). Larger designs pay more for toggle coverage;
re-measure when the nightly budget gets tight.

### Coverage floors

A level file may set floors, in percent, for any of the three metrics:

```yaml
coverage_floors: {line: 90, toggle: 70, functional: 100}
```

`functional` is either one number or a mapping of covergroup name to
percent:

```yaml
coverage_floors: {line: 90, functional: {a_arcs: 100, a_data: 80}}
```

- Each value is a number in `[0, 100]`; the mapping is non-empty and holds
  only `line`, `toggle` and `functional`. A per-covergroup mapping is
  non-empty and its names follow the covergroup name rule. Anything else is
  a validation error (exit code 2), like a bad test entry.
- **Aggregation.** Every bin counts once, coverpoint and cross bins alike,
  whatever its hit count. A number for `functional` applies to all of the
  IP's covergroups combined: covered bins of every covergroup over all
  their bins. A per-covergroup floor applies to that covergroup's bins
  alone; covergroups it doesn't name have no floor.
- Floors are checked only with `--coverage`, and only the floors of
  selected levels apply. When several selected levels set a floor for the
  same metric, or the same covergroup, the highest one applies. A combined
  `functional` floor and per-covergroup floors from different levels both
  apply.
- The merged value must be at least the floor. A metric with no points
  misses any floor, and so does a named covergroup that is not in the
  merged results (no passing run declared it, for example a misspelled
  name). Each miss prints
  `error: coverage floor: <ip>: <metric> <value>% (<hit>/<total>) is below its floor of <floor>% (level<N>.yaml)`
  (`<metric>` is `functional covergroup <name>` for a per-covergroup floor)
  to stderr and makes the run fail (exit code 1), even when every test
  passed.

Floors are optional and set per IP; the DV BKM says when to add them.

### Functional coverage

`regress.funcov` is a small helper (standard library only) that a cocotb
test imports to declare and sample covergroups:

```python
from regress.funcov import Covergroup

COV = Covergroup("ctrl")                       # unique per simulation
COV.coverpoint("valid", bins={"idle": 0, "busy": 1})
COV.coverpoint("data", bins={"zero": 0, "max": 0xFF, "mid": range(1, 0xFF)})
COV.coverpoint("len", [1, 2, 4])               # bins named "1", "2", "4"
COV.cross("valid_x_data", "valid", "data")     # one bin per combination
...
COV.sample(valid=1, data=0x3C)                 # every matching bin counts
```

- A bin is a value, a collection of values (`range`, list, set,
  frozenset) or a predicate. A tuple is one value, not a collection:
  `("A", "B")` is hit by the sampled tuple `("A", "B")` only, never by a
  bare `"A"` or `"B"`. A sampled value hits every bin it matches.
- Matching is type-strict: a sampled value matches a bin value, or an item
  of a collection, only when both have the same type and are equal, item
  by item inside tuples and frozensets. `True` hits neither `1`, `[1]` nor
  `range(2)`, and `1` doesn't hit `1.0`; a `range` matches `int` values
  only. So these never hit a plain `int` bin (a value, a list item or a
  `range`): an `IntEnum` member, a numpy integer (`numpy.int64`,
  `numpy.uint8`), `False` or `True`, and a cocotb `LogicArray`; and a
  namedtuple never hits a plain tuple arc. `sig.value` is a value object
  (a `LogicArray` or similar), not an `int`: sample plain Python values
  (`int(sig.value)`, an `Enum` member, `tuple(arc)`).
- When a sample hits no bin of a coverpoint and no value bin of it has the
  sample's type, the helper issues a `CoverageWarning` (a `UserWarning`)
  once per coverpoint and type, naming the bin types. Predicates add no
  type, so a coverpoint of predicates only never warns.
- A class (`str`, `int`, an `Enum` class), a class inside a collection
  (`[str, int]`), an iterator or generator, a mapping or a mapping view
  (`d.keys()`), or a sequence that is neither a tuple, a `str`, `bytes` nor
  one of the collections (a `collections.deque`) as a bin spec raises
  `CoverageError`: a class is callable but would hit every value, and the
  others could never be hit. Write a predicate such as
  `lambda v: isinstance(v, str)` instead. A cross
  bin is hit when each of its coverpoints is sampled in the same `sample`
  call and hits that bin.
- `cross(name, *points, unreachable=[...])` leaves out the listed
  combinations, each a tuple of one bin name per coverpoint (in `points`
  order), that the stimulus cannot produce: they are not cross bins, so
  they never count as misses, and a `sample` that hits one raises
  `CoverageError`, which fails the test, so the claim stays checked. A
  combination that is not such a tuple, or a list that leaves the cross no
  bin, raises `CoverageError`. The DV plan says why each one is
  unreachable ([DV BKM](../../bkm/dv.md#coverage)).
- Group, coverpoint, cross and bin names match
  `[A-Za-z0-9_][A-Za-z0-9_.-]*`; a duplicate name, an empty bin set, a cross
  of fewer than two known coverpoints or an unknown coverpoint in `sample`
  raises `CoverageError`.
- Sampling always counts. The results are written only in a coverage run:
  the sim tool sets `FUNCOV_FILE` to the run's `funcov.json`, and the helper
  writes it at interpreter exit (or earlier with `funcov.write()`). The file
  is `{"schema": 1, "groups": {group: {item: {bin: hits}}}}`, cross bins
  named `<bin>,<bin>`.
- Repeated samples reuse the increments computed for the same values, so
  sampling every cycle stays cheap (about 1.2 µs a sample for four
  coverpoints and two crosses). The reuse key matches the type-strict
  bins: values are the same when they are equal and of the same type, also
  inside tuples and frozensets. A predicate is therefore
  evaluated once per distinct value and must be a pure function of it: no
  state, no time, no reads of the DUT.

**Worked example: state-machine arcs.** To require every listed arc of a
state machine at L2, declare one covergroup whose bins are the named arcs,
each a `(from, to)` tuple, and sample it once per transition:

```python
from regress.funcov import Covergroup

ARCS = Covergroup("a_fsm_arcs")
ARCS.coverpoint("arc", bins={
    "idle__busy": ("IDLE", "BUSY"),
    "busy__done": ("BUSY", "DONE"),
    "busy__idle": ("BUSY", "IDLE"),            # abort
    "done__idle": ("DONE", "IDLE"),
})

prev = "IDLE"
...                                            # on every observed state change:
ARCS.sample(arc=(prev, state))
prev = state
```

An arc that isn't listed, such as a self-loop, hits no bin, and a sampled
bare state never does. A per-covergroup floor in the IP's `level2.yaml`
then requires every listed arc, merged over all L2 runs and seeds, without
holding the IP's other covergroups to 100%:

```yaml
coverage_floors: {functional: {a_fsm_arcs: 100}}
```

A missed arc fails the run with
`error: coverage floor: a: functional covergroup a_fsm_arcs 75.00% (3/4) is below its floor of 100% (level2.yaml)`,
and `functional.json` lists it as `a_fsm_arcs.arc.<arc>`.

## Reuse of unchanged model checks

An entry with `rerun_when: models_change` checks a reference model, not the
RTL (for example a copy of an RTL scenario with every layer replaced by its
model). Rerunning it can only find something when the models or the
testbench change, so its run (one per seed) is reused from an earlier pass
on the same host while none of its inputs changed (#327):

- **Key:** sha256 of the entry (name, module, testcase, top, `params`,
  `backend`, `timeout_s`) and seed, the tools (as the
  [build cache key](../build/SPEC.md#build-cache)), and the content and
  path (relative to the worktree) of every file under the IP's
  `dv/<ip>/` and `arch/<ip>/` folders (tests, testbench, regression lists,
  models), every file their Python imports (the
  [import scan](#selection) of `--affected`: the import root and other
  IPs' arch and models modules), the `shared_dv` folders of
  `cfg/ci.yaml`, and the simulation driver `tools/build/*.py`. RTL is not
  in the key: the rule is only for entries that run no RTL.
- **Record:** a passing run writes `<cache>/passes/<key>.json` in the
  [build cache](../build/SPEC.md#build-cache) (atomically; IP, test, seed
  and UTC time). A later run with the same key prints
  `[L<n>] <ip>:<test> seed=<s> PASS (reused: models unchanged since <time>)`,
  builds nothing for it, writes a one-line `sim.log`, and counts as a pass
  with 0 runtime and CPU (`reused` in the summary). Any input change, the
  cache off (`--no-cache`), an unused record pruned after 14 days or a new
  host runs it again. A failing run writes no record.
- **Coverage:** such an entry needs `coverage_seeds: 0`: a reused pass has
  no coverage to merge, and a model-only run exercises no RTL, so the IP's
  coverage never depends on whether it ran.

The nightly runs on one host type with a persistent cache
([DevOps BKM](../../bkm/devops.md#build-cache)), so there such an entry runs
in the first nightly after its models change.

Tests: `tools/regress/tests/test_run.py`
(`test_coverage_on_the_first_seeds_only`: only the first seed runs on the
coverage build and is merged, the others on the plain build;
`test_model_check_pass_is_reused_until_its_inputs_change`: a second run
reuses both seeds and builds nothing, an RTL change still reuses, a
testbench change and the cache off rerun;
`test_rotated_seeds_are_derived_from_the_key`: the derivation formula,
determinism, a new key rotates, N gives a prefix;
`test_rotated_seeds_skip_pinned_seeds`;
`test_rotation_adds_extra_runs_on_the_rotation_level_only`;
`test_load_rotation`: the `cfg/levels.yaml` section and every malformed
case; `test_extra_runs_are_plain_marked_and_outside_the_level_totals`;
`test_rotation_budget_stops_starting_extra_runs`;
`test_pinned_runs_start_first_then_extras_in_key_order`: with `--jobs 1`
every pinned run starts before any extra, the extras in `rotation_order`
of the key (another key, another order), and coverage gets the pinned
results only; `test_extra_only_builds_are_marked`;
`test_failing_extra_seed_fails_the_run_with_its_repro`), `test_schema.py`
(the `coverage_seeds` and `rerun_when` validation) and
`tools/cli/tests/test_cli.py` (`test_regress_rotation_dry_run`).

## Budgets

`cfg/levels.yaml` defines `level0`, `level1` and `level2`. Each level has
exactly two keys, `description` and a positive `budget_s`; the only other
key allowed is `rotation` ([seed rotation](#seed-rotation)). The DevOps BKM owns the values.

A budget is CPU time: when a level's total `cpu_s` exceeds its budget, the
run prints `warning: levelN CPU time Xs (the sum of its tests' CPU time)
exceeds its budget of Ys` to stderr and sets `over_budget` in the summary.
The per-level line prints both numbers, `levelN: P/T passed, CPU Xs
(budget Ys), test wall time Zs`. The warning doesn't change the exit code.
Why CPU time and why a warning: the
[DevOps BKM](../../bkm/devops.md#levels-and-budgets).

## Exit codes

- `0`: every selected test passed.
- `1`: any build or test didn't pass, or a coverage floor was missed.
- `2`: a usage or configuration error, or, with `--coverage`, coverage
  that could not be merged (the summary is still written).
