# Regress tool: `./build regress`

`./build regress` runs per-IP regression levels on Verilator and cocotb, and
it writes a JSON summary of the run. It uses the [build tool](../build/SPEC.md)
for IP discovery, filelists and simulation. The code lives in
`tools/regress/` (`schema.py`, `affected.py`, `run.py`).

```
./build regress --level N [--level M]... [--ip ID]... [--affected BASE]
                [--jobs N] [--seed S] [--waves] [--dry-run]
                [--src-root DIR] [--workdir DIR]
```

## Regression lists

This page owns the schema of `src/<ip>/dv/regress/level{0,1,2}.yaml`. The
[DV BKM](../../bkm/dv.md#regression-lists) owns how tests are placed in
levels.

An empty level is either a missing file or `tests: []`. An empty file and a
bare `[]` are errors. The DV BKM convention is that all three files exist.
This tool doesn't require that; the
[`./build check` filelist check](../checks/SPEC.md#filelist-check) enforces it.

```yaml
tests:
  - name: counts              # required; [A-Za-z0-9_][A-Za-z0-9_.-]*
    module: tests.test_counter  # required; cocotb module, dotted, under dv/
    testcase: counts          # optional; run only this cocotb test function
    top: counter              # required; HDL toplevel module
    seeds: 2                  # optional, default 1; see Seeds
    timeout_s: 60             # required; per-run wall-clock limit (> 0)
    params: {W: 4}            # optional; toplevel parameters (int or string)
```

| Key | Required | Type and default |
| --- | --- | --- |
| `name` | yes | String matching `[A-Za-z0-9_][A-Za-z0-9_.-]*`. |
| `module` | yes | Dotted Python module name, importable with `dv/` on the path. |
| `top` | yes | HDL identifier. |
| `timeout_s` | yes | Positive number. A run that exceeds it gets the `timeout` status. |
| `testcase` | no | Python identifier. If absent, every `@cocotb.test()` in the module runs. |
| `seeds` | no | Either a **count** `N >= 1` (default `1`) or an explicit **list** of distinct integers in `[0, 2**31)`. |
| `params` | no | Mapping from identifier to int or string, passed as `-G` overrides. |

These validation rules apply:

- The top level is a mapping whose only key is `tests`, which holds a list.
- Each entry is a mapping with all four required keys, only the keys listed
  above, and values of the types shown.
- `name` is unique across all three levels of the same IP. It also can't be
  `build` or the name of a sub-IP directory, because those share
  `workdir/<ip>/`.

Every error names the file and the entry index, plus the test name whenever
the entry has a string `name`. For example:
`.../level0.yaml: tests[2] (counts): missing key(s): top`. Validation covers
every discovered IP, not only the selected ones.

## Selection

Three rules decide which tests run:

- **Levels are exclusive.** `--level 1` runs only `level1.yaml`. To combine
  levels, repeat the option (`--level 0 --level 1`). At least one `--level`
  is required.
- **`--ip ID`** (repeatable) restricts the run to those IPs. IPs are flat:
  selecting a parent doesn't select its sub-IPs. Without `--ip`, every
  discovered IP is selected.
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
    (`<src-root>/common/dv/`): shared DV Python helpers are reached by no
    filelist, so their users cannot be told apart.
  - A changed file lies in the IP's **own files**: under `src/<ip>/`, but not
    inside a nested directory that is itself an IP. A change inside sub-IP
    `a/b` therefore selects `a/b`. It selects the parent `a` only through
    the next two cases. A change under a top-level IP's `arch/` (the
    architecture model, [arch BKM](../../bkm/arch.md)) is one of its own
    files, so it selects that IP alone.
  - A changed file lies in the `models` directory (`dv/models/`) of an IP
    nested anywhere below this one: parent models import sub-IP models by
    path ([DV BKM](../../bkm/dv.md#reusing-a-sub-ip-model)), which no
    filelist shows. A change in `a/b/dv/models/` selects `a/b` and `a`.
  - The IP's `dv/<name>.sim.f` **reaches** a changed file: a source, a
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

The pure-Python model unit tests under `src/**/dv/models/` are not part of
any regression level; [`./build ci unit`](../ci/SPEC.md#build-ci-unit) runs
them for the same selection in `ci-gate`.

## Seeds

Each seed is one run of the test, and the seed list comes from one of three
places:

- **A count:** `seeds: N` (the default is `1`) gives N runs. Run `i` uses
  the deterministic seed `crc32("<ip>:<name>:<i>") & 0x7fffffff`, so
  repeated regressions and `./build sim` use the same seeds.
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
  "ips": {"a": "changed: src/a/rtl/a.sv"},
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
- `passed` is true only when every build and every test passed.

## Budgets

`cfg/levels.yaml` defines `level0`, `level1` and `level2`. Each level has
exactly two keys, `description` and a positive `budget_s`, and no other keys
are allowed. The DevOps BKM owns the values.

When a level's total `runtime_s` exceeds its budget, the run prints
`warning: levelN runtime Xs exceeds its budget of Ys` to stderr and sets
`over_budget` in the summary. The warning doesn't change the exit code.

## Exit codes

- `0`: every selected test passed.
- `1`: any build or test didn't pass.
- `2`: a usage or configuration error.
