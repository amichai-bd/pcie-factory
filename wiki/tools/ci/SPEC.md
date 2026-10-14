# CI tool: `cfg/ci.yaml` and `./build ci`

The `ci-gate` workflow (`.github/workflows/ci.yml`) is the one required
check; the [DevOps BKM](../../bkm/devops.md#continuous-integration) owns
what it must cover. This page owns the selection rules file `cfg/ci.yaml`
and the `./build ci` helpers the workflow calls. The code lives in
`tools/ci/` (`config.py`, `unit.py`, `summary.py`).

```
./build ci [--src-root DIR] [--workdir DIR] unit [--affected BASE]
./build ci [--workdir DIR] summary
```

## Selection rules: `cfg/ci.yaml`

Facts that decide what a change selects live here, not in Python or in the
workflow. [`./build regress --affected`](../regress/SPEC.md#selection) and
`./build ci unit` read them.

```yaml
global:
  prefixes: [tools/, cfg/]   # repo-relative; each ends with '/'
  files: [build]             # repo-relative exact paths
shared_dv: [common/dv]       # relative to the source root
models: dv/models            # relative to each IP directory
tools_tests: [tools]         # repo-relative pytest paths
```

| Key | Meaning |
| --- | --- |
| `global` | A change under a prefix, or to one of the files, selects every IP and the tools unit tests. |
| `shared_dv` | Shared DV Python that no filelist reaches; a change under one selects every IP. |
| `models` | An IP's reference models and their unit tests. A change under a sub-IP's models directory also selects every ancestor IP, because parent models import sub-IP models ([DV BKM](../../bkm/dv.md#reusing-a-sub-ip-model)). `./build ci unit` runs the model tests of each selected IP. |
| `tools_tests` | The pytest paths of the tools unit tests. |

All five keys are required and no others are allowed. Paths are relative,
without `..`. A missing file, invalid YAML, a missing or unknown key, or a
value of the wrong type is a configuration error (exit 2) that names the
file and key. Never name an IP here.

## `./build ci unit`

Runs the pure-Python unit-test suites a change needs; no simulator.

- **`tools`:** `python -m pytest -q <tools_tests>` from the repository root.
- **`models:<ip>`:** `python -m pytest -q` inside `<ip>/<models>/`, one
  session per IP (model modules are imported by bare name), for each IP whose
  models directory holds a `test_*.py`.

Without `--affected`, every suite runs. With `--affected BASE`, the changed
files are those of [`regress --affected`](../regress/SPEC.md#selection)
(merge base of `BASE` and `HEAD`, plus uncommitted and untracked files):

- `tools` runs when a `global` path changed;
- `models:<ip>` runs for each IP that `regress --affected BASE` selects.

Each suite prints `[unit] <suite>: <reason>`, pytest's own output, then
`[unit] <suite>: PASS` or `FAIL (exit N)`. Every suite runs even after a
failure; the last line counts the passed suites and names the failed ones.
Suites run with `PYTHONDONTWRITEBYTECODE=1` and without the pytest cache, so
nothing is written under `src/`. Exit `0` when every suite passed (or none
was selected), `1` when any failed, `2` on a configuration or git error.

## `./build ci summary`

Prints `<workdir>/regress/summary.json` as short Markdown for the GitHub job
summary: the verdict and levels, each selected IP with its reason, a table
of per-level totals against budgets (marking over-budget levels), every
build or test that did not pass with its seed and log path, and the wall
time. A missing or unreadable summary prints one line saying so. It always
exits `0`; it reports, it does not gate.

## The workflow

`ci.yml` runs one job, `ci-gate`, on `[self-hosted, linux, x64]` for pull
requests to `main`, pushes to `main` and manual dispatch:

1. Toolchain discovery (prints `python3`, the Verilator found on `PATH` or
   under the `cfg/host.yaml` prefix, and cocotb); no Verilator fails the job.
2. Python dependencies: the runner's environment when it already satisfies
   `tools/requirements.txt` (`pip install --dry-run --no-index`), otherwise a
   job venv under `workdir/venv` put first on `PATH`.
3. The data guard, `./build metrics guard` ([metrics spec](../metrics/SPEC.md#guard)):
   on a pull request `--branch <head> --base HEAD^1`; on a push or dispatch
   of a `nightly/` ref `--branch <ref> --base origin/<base>`, where `<base>`
   is the base of the branch's open PR into `main` (else of any open PR,
   else `main`; the history is unshallowed first). Any other push or
   dispatch skips it. Only a `nightly/` branch can fail it.
4. `./build check`.
5. `./build ci unit`, then `./build regress --level 0 --level 1 --jobs
   $(nproc)`. On a pull request both get `--affected HEAD^1`: the checkout is
   the merge commit, whose first parent is the base tip it merged onto, so
   the diff is exactly the PR's change against the tree under test. On a push
   or dispatch neither gets `--affected` (every IP, every suite).
6. Steps 3 to 5 all run even if an earlier step fails (only cancellation
   stops them); then the job summary
   (step outcomes plus `./build ci summary`) and the `regress-summary`
   artifact (`workdir/regress/summary.json`).

**Forks:** the job carries the fork guard `if:` that the
[DevOps BKM](../../bkm/devops.md#continuous-integration) prescribes. That
guard lives in the PR's own copy of `ci.yml`, so a fork can remove it: it is
defense in depth only. Maintainers never approve a fork PR's workflow run;
the BKM's Forks paragraph owns the rule, the accepted residual risk and the
review-and-re-push route.

Concurrency cancels a superseded run of the same PR; every push and
dispatch has its own group. `permissions: contents: read` and
`pull-requests: read` (the guard's base lookup), actions pinned
to exact versions, and a 20-minute hard timeout. The 10-minute `ci-gate`
budget of the DevOps BKM is a target the job is expected to meet, not
enforced by the workflow; the timeout leaves headroom for a cold
dependency install.

Branch protection on `main` requires the status check `ci-gate`.

## Tests

`tools/ci/tests/`: rule loading and every malformed-input case
(`test_ci_config.py`), suite planning on a synthetic git repository and
failure propagation (`test_ci_unit.py`), and summary rendering
(`test_ci_summary.py`). The affected-IP rules are tested in
`tools/regress/tests/test_affected.py` against the real `cfg/ci.yaml`.
