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

Each IP keeps its own tests in `src/<ip>/dv/regress/level{0,1,2}.yaml`. A
missing or empty file means the IP has no tests at that level.

```yaml
tests:
  - name: counts              # required; [A-Za-z0-9_][A-Za-z0-9_.-]*
    module: tests.test_counter  # required; cocotb module under dv/
    testcase: counts          # optional; run only this cocotb test function
    top: counter              # required; HDL toplevel module
    seeds: 1                  # optional, default 1; number of seeded runs
    timeout_s: 60             # required; per-run wall-clock limit (> 0)
    params: {W: 4}            # optional; toplevel parameters (int or string)
```

These validation rules apply:

- The top level is a mapping whose only key is `tests`, which holds a list.
- Each entry is a mapping that has the three required keys and only the
  known keys, and every value has the type shown above.
- `name` is unique across all three levels of the same IP. It also can't be
  `build` or the name of a sub-IP directory, because those share
  `workdir/<ip>/`.

Every error names the file, the entry index and the test name. Validation
covers every discovered IP, not only the selected ones.

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
  uncommitted diff and untracked files that aren't ignored. An IP is
  affected in any of these cases:
  - Every IP is affected when any change is under `tools/` or `cfg/`, or is
    the `build` launcher (paths relative to the git repository root).
  - A changed file lies in the IP's **own files**: under `src/<ip>/`, but not
    inside a nested directory that is itself an IP. A change inside sub-IP
    `a/b` therefore selects `a/b`. It selects the parent `a` only through
    the next case.
  - The IP's `dv/<name>.sim.f` **reaches** a changed file: a source, a
    filelist in its `-F` tree, or a file in one of its include directories.
  - The IP's `sim.f` doesn't resolve (for example, a source it lists was
    deleted). The IP is selected, and its build reports the error.

The command prints each selected IP with the reason it was selected. Use
`--dry-run` to print the selected tests and seeds without running anything.

## Seeds

A test with `seeds: N` runs N times. By default run `i` uses the
deterministic seed `crc32("<ip>:<name>:<i>") & 0x7fffffff`, so repeated
regressions and `./build sim` use the same seeds. `--seed S` replaces these
with `S, S+1, ..., S+N-1`. The seed reaches cocotb (and Python's `random`)
as its random seed and is recorded in the summary.

## Execution

- **Builds:** every distinct (IP, top, params, waves) combination is built
  once, before any test runs. A failed build marks all of its tests `error`.
- **Tests:** builds and tests run on up to `--jobs N` workers (default 1).
  Each test runs with its own `timeout_s`.
- **Waves:** `--waves` dumps waves for every test.
- **Verilator:** a missing Verilator is an error (exit code 2), unless no
  tests are selected.

Each result prints as a line like
`[L0] <ip>:<test> seed=<s> PASS (<t>s)`. Per-test output follows the build
spec's layout.

## Summary

`workdir/regress/summary.json` is rewritten on every run, including a run
that selects no tests:

```json
{
  "levels": [0, 1],
  "src_root": "/abs/path/src",
  "ips": {"a": "changed: src/a/rtl/a.sv"},
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
