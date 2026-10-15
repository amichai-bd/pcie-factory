# Regress tool: `./build regress`

`./build regress` runs per-IP regression levels on Verilator and cocotb, and
it writes a JSON summary of the run. It uses the [build tool](../build/SPEC.md)
for IP discovery, filelists and simulation. The code lives in
`tools/regress/` (`schema.py`, `affected.py`, `run.py`, and `coverage.py`
and `funcov.py` for [coverage](#coverage)).

```
./build regress --level N [--level M]... [--ip ID]... [--affected BASE]
                [--jobs N] [--seed S] [--waves] [--coverage] [--dry-run]
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
  - Every IP is affected when any change is under a `shared_dv` directory
    (`<src-root>/dv/common/`): shared DV Python helpers are reached by no
    filelist, so their users cannot be told apart.
  - A changed file lies in the IP's **own files**: under `src/<kind>/<ip>/`
    for any kind (for the shared unit, `src/rtl/common/` or
    `src/dv/common/`; the latter also selects every IP by the previous rule). A change in sub-IP `a_b`'s folders therefore selects
    `a_b`; it selects the parent `a` only through the next two cases. A
    change under a top-level IP's `src/arch/<ip>/` (the architecture model,
    [arch BKM](../../bkm/arch.md)) is one of its own files, so it selects
    that IP alone.
  - A changed file lies in the `models` directory (`src/dv/<sub>/models/`)
    of one of the IP's sub-IPs (an IP its `.rtl.f` reaches, see
    [IP discovery](../build/SPEC.md#ip-discovery)): parent models import
    sub-IP models by path ([DV BKM](../../bkm/dv.md#reusing-a-sub-ip-model)),
    which no filelist shows. A change in `src/dv/a_b/models/` selects `a_b`
    and every ancestor, such as `a`.
  - The IP's `dv/<ip>/<ip>.sim.f` **reaches** a changed file: a source, a
    filelist in its `-F` tree, or a file in one of its include directories.
  - The IP's `sim.f` doesn't resolve (for example, a source it lists was
    deleted). The IP is selected, and its build reports the error.

The command prints each selected IP with the reason it was selected, or
`no IPs selected`. An empty selection passes, which is right for a
docs-only change. On a push to `main`, though, the merge base equals `HEAD`,
so `--affected` selects nothing. CI must not pass `--affected` there; it runs
every IP, as the DevOps BKM requires. The `summary.json` fields
`selected_ips` and `selected_tests` make an empty selection visible.

Use `--dry-run` to print the selection (IPs, tests and seeds) without
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
- **A list:** `seeds: [a, b, ...]` runs exactly those seeds, in sorted order
  in the output.
- **`--seed S`:** replaces either form with `S, S+1, ...`, using the same
  number of runs.

The seed is passed to cocotb as its random seed, which also seeds Python's
`random`. It's printed in the sim log and recorded in the summary.

## Execution

- **Builds:** every distinct (IP, top, params, waves) combination is built
  once, before any test runs. A failed build marks all of its tests `error`
  and the run continues with the other builds. A `sim.f` that doesn't
  resolve counts as a failed build, with the `file:line` message as the
  detail.
- **Tests:** builds and tests run on up to `--jobs N` workers (default 1).
  Each test runs with its own `timeout_s`.
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
  "builds": [{"ip": "a", "build": "a_top", "status": "pass",
              "runtime_s": 2.1, "log": "a/build/a_top/build.log"}],
  "tests": [{"ip": "a", "level": 0, "name": "smoke", "seed": 123,
             "status": "pass", "runtime_s": 0.9,
             "log": "a/smoke/123/sim.log", "detail": "1 case(s) passed"}],
  "totals": {"level0": {"tests": 1, "passed": 1, "failed": 0,
                        "runtime_s": 0.9, "budget_s": 120,
                        "over_budget": false}},
  "wall_s": 3.4,
  "coverage": {"ips": {"a": {
      "runs": 1, "dat_files": 1, "funcov_files": 1, "dir": "a/coverage",
      "line": {"covered": 15, "total": 15, "pct": 100.0},
      "toggle": {"covered": 218, "total": 228, "pct": 95.61},
      "functional": {"covered": 14, "total": 19, "pct": 73.68, "uncovered": 5},
      "floors": {"line": 90.0}, "failures": []}},
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
  sum of its test runtimes. It doesn't depend on `--jobs`.
- `coverage` is `null` without `--coverage`. Otherwise it holds, per
  selected IP, the passing runs merged (`runs`) and the files found, each
  metric's `covered`, `total` and `pct` (2 decimals; `null` with no
  points), the floors that applied, and the floor failure messages;
  `coverage.passed` is false when any IP missed a floor.
- `passed` is true only when every build and every test passed, and no
  coverage floor was missed.

## Coverage

`--coverage` turns every build of the run into a coverage build and, after
the tests, merges each selected IP's coverage, prints a table and checks the
IP's floors. The [shared unit](../build/SPEC.md#shared-unit) is a unit like
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

**Merge.** Only runs that passed are merged. Per IP,
`verilator_coverage --write` (installed next to `verilator`) merges the
`.dat` files, and the functional bins are summed over runs, the bin set
being their union. A missing `verilator_coverage`, a failed merge or an
unreadable `funcov.json` is an error (exit code 2). Output, replaced on
every coverage run of the IP:

| Path | Content |
| --- | --- |
| `workdir/<ip>/coverage/merged.dat` | Merged Verilator coverage; `verilator_coverage --annotate <dir> merged.dat` shows it per source line. |
| `workdir/<ip>/coverage/merged.info` | The same as an lcov `.info` file. |
| `workdir/<ip>/coverage/functional.json` | Merged bins per covergroup and item, and a summary with every uncovered bin as `group.item.bin`. |

Coverage covers the IP's whole `sim.f`, so a parent IP's code coverage
includes its sub-IPs' RTL as its tests exercise it. A metric with no points
(for example, no test samples a covergroup) is reported `n/a`.

**Table.** After the level totals, the run prints one row per selected IP:

```
coverage  line            toggle            functional      floors
a         100.0% (15/15)  95.6% (218/228)   73.7% (14/19)   none
```

The `floors` column is `none` (no floor applies), `ok` or `FAIL`.

**Cost.** On the seed IPs' L0+L1+L2 (24 runs, `--jobs 4`), coverage builds
added under 1% of CPU time and functional sampling about 8% (99.5 s to
108 s of CPU, mean of three runs each); the designs are small, so the
Python testbench dominates. Larger designs pay more for toggle coverage;
re-measure when the nightly budget gets tight.

### Coverage floors

A level file may set floors, in percent, for any of the three metrics:

```yaml
coverage_floors: {line: 90, toggle: 70, functional: 100}
```

- Each value is a number in `[0, 100]`; the mapping is non-empty and holds
  only `line`, `toggle` and `functional`. Anything else is a validation
  error (exit code 2), like a bad test entry.
- Floors are checked only with `--coverage`, and only the floors of
  selected levels apply. When several selected levels set a floor for the
  same metric, the highest one applies.
- The merged value must be at least the floor. A metric with no points
  misses any floor. Each miss prints
  `error: coverage floor: <ip>: <metric> <value>% (<hit>/<total>) is below its floor of <floor>% (level<N>.yaml)`
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

- A bin is a value, a collection of values (`range`, list, set) or a
  predicate. A sampled value hits every bin it matches. A cross bin is hit
  when each of its coverpoints is sampled in the same `sample` call and hits
  that bin.
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
  sampling every cycle stays cheap (about 1 µs a sample).

## Budgets

`cfg/levels.yaml` defines `level0`, `level1` and `level2`. Each level has
exactly two keys, `description` and a positive `budget_s`, and no other keys
are allowed. The DevOps BKM owns the values.

When a level's total `runtime_s` exceeds its budget, the run prints
`warning: levelN runtime Xs exceeds its budget of Ys` to stderr and sets
`over_budget` in the summary. The warning doesn't change the exit code.

## Exit codes

- `0`: every selected test passed.
- `1`: any build or test didn't pass, or a coverage floor was missed.
- `2`: a usage or configuration error.
