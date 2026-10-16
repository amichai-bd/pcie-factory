# CI tool: `cfg/ci.yaml` and `./build ci`

The `ci-gate` workflow (`.github/workflows/ci.yml`) is the one required
check; the [DevOps BKM](../../bkm/devops.md#continuous-integration) owns
what it must cover. This page owns the selection rules file `cfg/ci.yaml`
and the `./build ci` helpers the workflow calls. The code lives in
`tools/ci/` (`config.py`, `unit.py`, `vendor.py`, `gate.py`, `summary.py`, `status.py`).

```
./build ci [--src-root DIR] [--workdir DIR] unit [--affected BASE]
./build ci [--src-root DIR] [--workdir DIR] vendor --kind {fpga,asic} [--affected BASE] [--dry-run]
./build ci gate --tests RESULT --fpga RESULT --asic-synth RESULT
./build ci [--workdir DIR] summary
./build ci status --result RESULT --sha SHA --ref REF [--target-url URL] [--repo OWNER/NAME] [--dry-run]
```

## Selection rules: `cfg/ci.yaml`

Facts that decide what a change selects live here, not in Python or in the
workflow. [`./build regress --affected`](../regress/SPEC.md#selection) and
`./build ci unit` and `./build ci vendor` read them.

The configured paths are in [`cfg/ci.yaml`](../../../cfg/ci.yaml); the table
below defines each required key.

| Key | Meaning |
| --- | --- |
| `global` | A change under a prefix, or to one of the files, selects every IP and the tools unit tests. An IP that only such a change selects runs only the regression `levels` (a non-empty list of level numbers) among those requested; an IP another rule selects runs every requested level ([regress selection](../regress/SPEC.md#selection)). |
| `shared_dv` | Shared DV Python that no filelist reaches; a change under one selects every IP. |
| `models` | An IP's reference models and their unit tests. A change under a sub-IP's (or an integrated IP's) models directory also selects every ancestor IP, because parent models import sub-IP models ([DV BKM](../../bkm/dv.md#reusing-a-sub-ip-model)). `./build ci unit` runs the model tests of each selected IP. |
| `import_root` | The directory whose Python the DV imports at run time (on `sys.path` in every simulation). `--affected` scans the imports of each IP's DV and arch Python and selects the IP, at every level, when a file it reaches under this root or in another IP's arch or models folder changes ([regress selection](../regress/SPEC.md#selection)). |
| `vendor.files` | Additional repo-relative files whose changes select every FPGA and ASIC target. Global paths already select all vendor targets; regression levels do not limit vendor checks. |
| `tools_tests` | The pytest paths of the tools unit tests. |
| `vocabulary_tests` | `marker`: a pytest marker on tools tests that build source files (the RTL vocabulary headers, with Verilator); `paths`: repo-relative files (a trailing `/` makes a prefix) whose change runs those tests; a checks unit test keeps them equal to the style check's vocabulary header list ([checks SPEC](../checks/SPEC.md#style-check)). |

A top-level IP's architecture models, self-tests and `perf/` scenarios
live in `src/arch/<ip>/`, fixed by the [layout](../../project/layout.md), so
they need no key: a change there selects that IP (it is one of the IP's own
folders, and DV may import the models), and `./build ci unit` runs its
self-tests and perf scenarios ([arch BKM](../../bkm/arch.md)).

All keys are required and no others are allowed. Paths are relative,
without `..`. A missing file, invalid YAML, a missing or unknown key, or a
value of the wrong type is a configuration error (exit 2) that names the
file and key. Never name an IP here.

## `./build ci unit`

Runs the unit-test suites a change needs. They are pure Python, except
tools tests that call Verilator when it is installed (and skip otherwise).

- **`tools`:** `python -m pytest -q <tools_tests>` from the repository root.
- **`vocabulary`:** `python -m pytest -q -m <marker> <tools_tests>` from the
  repository root: only the marked tools tests.
- **`models:<ip>`:** `python -m pytest -q` inside `src/dv/<ip>/<models>/`, one
  session per IP (model modules are imported by bare name), for each IP whose
  models directory holds a `test_*.py`. The
  [shared unit](../build/SPEC.md#shared-unit) counts as an IP here
  (`models:common`, in `src/dv/common/<models>/`).
- **`arch:<ip>`:** the same inside `src/arch/<ip>/`, for each IP whose arch
  folder holds a `test_*.py` (the model self-tests).
- **`perf:<ip>`:** `./build arch --ip <ip>` with the same `--src-root` and
  `--workdir` ([arch spec](../arch/SPEC.md)), for each IP with an
  `src/arch/<ip>/perf/` folder; a missed target fails it.

Without `--affected`, every suite runs. With `--affected BASE`, the changed
files are those of [`regress --affected`](../regress/SPEC.md#selection)
(merge base of `BASE` and `HEAD`, plus uncommitted and untracked files):

- `tools` runs when a `global` path changed;
- `vocabulary` runs when a `vocabulary_tests` path changed and no `global`
  path did (`tools` already includes the marked tests); it never runs
  without `--affected`, where `tools` runs;
- `models:<ip>`, `arch:<ip>` and `perf:<ip>` run for each IP that
  `regress --affected BASE` selects.

Each suite prints `[unit] <suite>: <reason>`, pytest's own output, then
`[unit] <suite>: PASS` or `FAIL (exit N)`. Every suite runs even after a
failure; the last line counts the passed suites and names the failed ones.
Suites run with `PYTHONDONTWRITEBYTECODE=1` and without the pytest cache, so
nothing is written under `src/`. Exit `0` when every suite passed (or none
was selected), `1` when any failed, `2` on a configuration or git error.

## `./build ci vendor`

Discovers IPs with the requested kind folder, including folders with malformed
or missing required inputs: the real flow must diagnose those. Without
`--affected`, every target runs. With it, the diff uses the same merge-base,
uncommitted and untracked semantics as `ci unit`. Global paths and
`vendor.files` select all targets. Otherwise a target is selected when its
RTL filelist reaches a changed source, nested filelist or include-directory
header, or a file under its corresponding vendor folder changes (wrapper,
constraints, configuration). Deleted paths participate; an unresolvable RTL
closure selects the target so the flow reports the failure. Selection does
not use simulation filelists or Python/DV imports. Docs-only changes select
none. Reasons print before execution; `--dry-run` only prints the selection.

Selected targets run in sorted order: `./build fpga --ip ID` performs the
full compile, fit and timing acceptance, and `./build asic --ip ID
--synth-only` performs the [synthesis check](../asic/SPEC.md#synthesis-only-compatibility-check).
Each receives the caller's source root and workdir. A failed target does
not suppress later targets. SIGINT/SIGTERM are forwarded to the active
flow so its cleanup can run; signals during child creation are queued until
the handle is available. Cancellation starts no later target and exits 130. Exit 0 means every selected target passed (or
none selected), 1 means a child failed, 2 means selection/configuration failed.
`workdir/ci/vendor-<kind>.json` records schema 1, kind, stage and each target's
status and elapsed seconds; it is replaced each execution, even with no targets.
Only that summary and the flows' summary JSON are uploaded, never raw logs.

## `./build ci gate`

Returns 0 only when all three required results equal `success`; `failure`,
`cancelled`, `skipped`, empty or unknown results return 1. Missing arguments
are usage errors (2). It prints every dependency result before deciding.

## `./build ci summary`

Prints `<workdir>/regress/summary.json` as short Markdown for the GitHub job
summary: the verdict and levels, each selected IP with its reason, a table
of per-level totals against budgets (marking over-budget levels), every
build or test that did not pass with its seed and log path, and the wall
time. A missing or unreadable summary prints one line saying so. It always
exits `0`; it reports, it does not gate.

## `./build ci status`

Posts the `ci-gate` commit status for a nightly data branch, from the
dispatched gate job's result; the workflow's `ci-gate-status` job is its
only caller. Why it exists: the data PR is opened with `GITHUB_TOKEN`, so
its gate runs by dispatch, and branch protection does not count a
dispatched check run toward a PR; it does count a commit status with
context `ci-gate` posted by GitHub Actions, the app the required check is
bound to.

- `--result` is the job result (`needs.ci-gate.result`): `success` posts
  `success`, `failure` posts `failure`, `cancelled` and `skipped` post
  `error`. Any other value is refused.
- `--ref` must name a branch under `branch_prefix` of `cfg/metrics.yaml`
  (`nightly/`), as `refs/heads/<branch>` or `<branch>`; any other ref,
  including tags, PR refs and the bare prefix, is refused before any API call.
- `--sha` must be a full 40-hex lowercase commit SHA (`$GITHUB_SHA`, the
  commit the gate tested).
- It sends one `POST repos/<repo>/statuses/<sha>` with `state`, `context`
  `ci-gate`, `description` `ci-gate job on <branch>: <result>` (cut to 140
  characters, ending in `...`, because GitHub rejects a longer one) and,
  when given, `target_url` (the run). The repository is `--repo`, then
  `$GH_REPO`, `$GITHUB_REPOSITORY`, the origin remote; `gh` reads
  `GH_TOKEN`. `--dry-run` prints the status and posts nothing.

Exit `0` when posted (or dry run), whatever the posted state: the gate job
itself already carries the failure. Exit `2` on a refused input, a
configuration error or a failed API call (the job then fails and no status
is posted, so the PR stays blocked).

## The workflow

`ci.yml` runs `ci-tests` and the vendor/aggregate jobs below on `[self-hosted, linux, x64]` for pull
requests to `main`, pushes to `main` and manual dispatch:

1. Toolchain discovery (prints `python3`, the Verilator found on `PATH` or
   under the `cfg/host.yaml` prefix, and cocotb); no Verilator fails the job.
2. Python dependencies: the runner's environment when it already satisfies
   `tools/requirements.txt` (`pip install --dry-run --no-index`), otherwise a
   job venv under `workdir/venv` put first on `PATH`.
3. The data guard, `./build metrics guard` ([metrics spec](../metrics/SPEC.md#guard)):
   on a pull request `--branch <head> --base HEAD^1`; on a dispatch on a
   `nightly/` ref (push triggers only on `main`, so a `nightly/` ref arrives
   only by dispatch) `--branch <ref> --base origin/<base>`, where `<base>`
   is the base of the branch's open PR into `main` (else of any open PR,
   else `main`; the history is unshallowed first). Any other push or
   dispatch skips it. Only a `nightly/` branch can fail it.
4. `./build check`, then `./build wiki build --strict` (a few seconds; it
   gates a docs-only change, which selects no IP and no suite).
5. `./build ci unit`, then `./build regress --level 0 --level 1 --jobs
   $(nproc)`. On a pull request both get `--affected HEAD^1`: the checkout is
   the merge commit, whose first parent is the base tip it merged onto, so
   the diff is exactly the PR's change against the tree under test. On a push
   or dispatch neither gets `--affected` (every IP, every suite). With
   `--affected`, a change only under `global` paths runs the tools unit
   tests and level 0 of every IP; a change in an IP's sources or DV runs
   L0 and L1 of that IP and of every IP whose filelists reach it.
6. Steps 3 to 5 all run even if an earlier step fails (only cancellation
   stops them); then the job summary
   (step outcomes plus `./build ci summary`) and the `regress-summary`
   artifact (`workdir/regress/summary.json`).

The steps above are the `ci-tests` job. Two independent jobs run in parallel:
`ci-fpga` calls `./build ci vendor --kind fpga $AFFECTED`, and
`ci-asic-synth` calls it with `--kind asic`. Both use the same checkout,
Python pins and PR-base selection; push and dispatch run every target.
No-target jobs succeed after executing selection, rather than being skipped.
The required `ci-gate` job depends on all three and uses `always()` plus the
fork guard, so failed, cancelled or unexpectedly skipped dependencies reach
`./build ci gate`. The aggregate takes each actual `needs.<job>.result`;
only explicit success from all three satisfies protection.

**Forks:** every job carries the fork guard `if:` that the
[DevOps BKM](../../bkm/devops.md#continuous-integration) prescribes. That
guard lives in the PR's own copy of `ci.yml`, so a fork can remove it: it is
defense in depth only. Maintainers never approve a fork PR's workflow run;
the BKM's Forks paragraph owns the rule, the accepted residual risk and the
review-and-re-push route.

Concurrency cancels a superseded run of the same PR; every push and
dispatch has its own group. `permissions: contents: read` and
`pull-requests: read` (the guard's base lookup), actions pinned
to exact versions, and a 20-minute hard timeout per work job (5 minutes for aggregation). The 10-minute `ci-gate`
budget of the DevOps BKM is a target the job is expected to meet, not
enforced by the workflow; the timeout leaves headroom for a cold
dependency install.

**`ci-gate-status`** is a separate job, `needs: ci-gate` with `always()`,
that runs only when the event is not `pull_request` and the ref is
`refs/heads/nightly/*`, which in practice means a dispatch on a data branch
(the nightly's own, or a writer's). It
checks out the branch without persisted credentials, installs the Python
pins like step 2, and runs `./build ci status` with the gate job's result
(`needs.ci-gate.result`), `$GITHUB_SHA` and `$GITHUB_REF`, and the run as
target URL. It is the only job with `statuses: write` (plus
`contents: read`); the gate job never holds it, so no gate step, including
the guard that receives a token, can post a status.

Branch protection on `main` requires the status check `ci-gate`, bound to
GitHub Actions (app 15368). On a pull request from this repository the
`ci-gate` check run satisfies it; on the nightly data PR the commit status
from `ci-gate-status` does ([metrics spec](../metrics/SPEC.md#workflows)).

**Who can satisfy `ci-gate`.** Protection only accepts `ci-gate` from
GitHub Actions, so a status posted by a person or by another app does not
count. Inside Actions: fork `pull_request` runs get a read-only token and
the status job never runs for `pull_request`; the nightly's data commits
pass `./build metrics guard` from a trusted checkout before they are
pushed; the posted state is the gate job's own result. The residual path is
a writer adding or editing a workflow on their own branch, which can equally
make any `ci-gate` check pass today. Review before merge is the control for
that, unchanged by this job, and it is **process, not enforcement**: branch
protection on `main` requires only `ci-gate` and no approving reviews, so
the `REVIEW-VERDICT` gate of the
[agent flow](../../../.claude/skills/agent-flow/SKILL.md#7-merge-firstmate)
is kept by the agents and maintainers, not by GitHub.

**What a status vouches for.** Any writer can dispatch `ci.yml` on any
`nightly/*` ref, and every such dispatch posts a `ci-gate` status. A commit
status binds to a commit SHA, not to a PR, a branch or a base: the state
says that the gate passed on that commit, with the data guard run against
the base it resolved at dispatch time (the open PR's base, else `main`). A
PR with the same head commit, into another base or opened later, sees the
same status. The guard base is not in the description; the run behind
`target_url` shows it in its guard step log.

## Tests

`tools/ci/tests/`: all 64 aggregate success/failure/cancelled/skipped combinations
and unknown-result rejection through the CLI, workflow dependency/guard wiring,
vendor selection on synthetic git trees, child failure propagation, actual-child signal forwarding, no-target
and dry-run behavior (`test_ci_gate.py`, `test_ci_vendor.py`); rule loading and every malformed-input case
(`test_ci_config.py`), suite planning on a synthetic git repository
(including an IP with an arch model and scenario, and a vocabulary header
change selecting only the marked tools tests), running the arch and perf
suites, and failure propagation (`test_ci_unit.py`), summary rendering
(`test_ci_summary.py`), and the status mapping, the 140-character description cap, the data-branch-only rule,
posted request, dry run, API failure and CLI wiring (`test_ci_status.py`,
`gh` faked). The affected-IP rules are tested in
`tools/regress/tests/test_affected.py` against the real `cfg/ci.yaml`,
including sub-IP and integrated-IP changes selecting every ancestor (on the fixtures and on
the repository's own `src/`, IPs discovered, never named).
