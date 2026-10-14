# DevOps BKM

> Provenance: adapted from nand2anything `wiki/bkm/bkm_devops.md` (commit
> `085ef4b`, same owner, no license file). Tool, regression-evidence and CI
> principles kept; project registries, exclusions, host routing and media
> allowances dropped.

## Scope

This page owns the build entry point, levels and budgets, CI selection, the
nightly run, labels and metrics. [`CLAUDE.md`](../../CLAUDE.md) owns the
delivery flow and merge gate; the `agent-flow` skill owns the procedure.
Each tool's `wiki/tools/<tool>/SPEC.md` owns its exact command syntax.

## Build entry point

`./build` at the repository root is the single entry point: a thin launcher
whose implementation lives in `tools/`. Humans, agents and workflows all call
it; nothing calls a tool script directly.

| Subcommand | Purpose |
| --- | --- |
| `check` | Static checks (below). |
| `sim` | One test of one IP, one seed. |
| `regress` | Regression by IP and level; `--affected <base>` selects IPs. |
| `fpga` | Quartus flow for one IP ([FPGA BKM](fpga.md)). |
| `asic` | ORFS flow for one IP ([PD BKM](pd.md)). |
| `labels` | Label sync from `cfg/labels.yaml`. |
| `wiki` | Wiki site build. |
| `clean` | Remove `workdir/` output. |

Subcommands land with their issues (#6 core, #7 check, #10 labels, #12 wiki,
#13 fpga, #14 asic); until a SPEC exists, the issue owns the contract.

## Tools and configuration

```text
build                       launcher only
tools/<tool>/               generic Python, unit tests in tools/<tool>/tests/
wiki/tools/<tool>/SPEC.md   outcomes, CLI, behavior, failures, tests
cfg/                        generic configuration (levels, ci, host, checks, labels)
workdir/                    all generated output, ignored
```

- Python 3 standard library, PyYAML and pytest only, unless a tool's issue
  approves more.
- Tools are design-neutral: they discover IPs by walking `src/` and never
  name an IP. Facts that vary live in `cfg/` or in `src/<ip>/`, not in Python
  conditionals.
- Tools validate inputs before expensive work, print what they will run,
  propagate failures with a nonzero exit, and use deterministic output paths.
- Generated output goes only under the worktree's `workdir/`; never commit
  logs, waves, reports, netlists or bitstreams.
- No wrapper layer until two real consumers share a need.

## Static checks

`./build check` runs, and fails nonzero on any failure:

| Check | Rule owner |
| --- | --- |
| Verilator `--lint-only -Wall` per `.rtl.f`, waivers only in the reviewed waiver file | [RTL BKM](rtl.md#lint-and-style-checks) |
| RTL style checker | [RTL BKM](rtl.md) |
| Filelists: every path resolves, no duplicate after flattening, no orphan RTL; every IP has its DV filelists and three valid level files | [layout](../project/layout.md#per-ip-files) |
| Neutrality: the word list in `cfg/checks.yaml`, with the scope and exemptions set by the contract | [`CLAUDE.md`](../../CLAUDE.md#design-neutrality) |

`tools/` unit tests run when `tools/` or `cfg/` change. Doc line caps are a
review guideline, not a check.

## Levels and budgets

Levels are exclusive ([DV BKM](dv.md#regression-lists)); callers combine
them with repeated `--level`.

| Level | Contents | Budget | Runs |
| --- | --- | --- | --- |
| L0 | Smoke and fastest directed tests | ≤ ~2 min locally for all IPs | Locally while developing; in `ci-gate` |
| L1 | Full directed set, a few seeds | L0+L1 of affected IPs plus checks ≤ 10 min | `ci-gate` |
| L2 | Long tests, seed sweeps, FPGA and ASIC flows, metrics | Nightly window | Nightly |

Budgets live in `cfg/levels.yaml`; `regress` warns when a level exceeds its
budget. An over-budget level is fixed by moving or speeding up tests, never
by raising the budget silently.

## Local evidence

| Change | Run before review |
| --- | --- |
| Docs only | Link check and the neutrality check |
| `tools/`, `cfg/` | `./build check`, the tool unit tests, L0 of all IPs |
| RTL or DV | `./build check`, `./build regress --level 0 --level 1 --affected origin/main` |
| Physical inputs (`fpga/`, `asic/`) | The above plus the flow on demand, when the host has the tool |

Run the required set once on the commit intended for review and record exact
commands, seeds, counts and every non-pass entry in the PR. Evidence shows
behavior, not command success. Missing evidence is a review finding.

## Continuous integration

Workflows live in `.github/workflows/` (`ci`, `nightly`, `pages`) and run on
self-hosted runners selected with `runs-on: [self-hosted, linux, x64]`. The
runners are registered to this repository only, so no workflow names the
design target. The repository is public, so fork code must never reach these
runners. Every job that a `pull_request` can trigger carries

```yaml
if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository
```

so it still runs on push and dispatch, runs for PRs from this repository, and
is skipped for fork PRs. `pull_request_target` never checks out PR code, and
workflows never echo secrets. The captain also requires approval for
outside-collaborator fork workflows in the repository settings. A skipped job
counts as passing a required check, so a fork PR is never merged on its
`ci-gate` alone: a maintainer pushes its commits to a branch in this
repository, and that PR's `ci-gate` is the gate. The CI host is the Omarchy machine; `cfg/host.yaml` pins its
toolchain.

**`ci-gate`** is the one required check. It runs on pull requests to `main`,
pushes to `main` and manual dispatch:

1. `./build check`.
2. `tools/` unit tests, when `tools/` or `cfg/` changed.
3. `./build regress --level 0 --level 1` for the affected IPs.

**Affected IPs** (selection rules in `cfg/ci.yaml`):

| Change | Selected |
| --- | --- |
| An IP's own files: under `src/<ip>/`, excluding nested sub-IP directories | That IP |
| A file reached through `-F` from an IP's filelists | That IP |
| `tools/**`, `cfg/**` or the `build` launcher | All IPs |
| Shared DV helpers under `src/common/dv/` (reached by no filelist) | All IPs |
| Push to `main`, manual dispatch | All IPs |
| Docs only | Checks only |

A sub-IP is an IP in its own right: a change inside `src/<ip>/<subip>/`
selects the sub-IP, and selects the parent only when the parent's filelists
reach the changed file.

Superseded PR runs are cancelled; every push to `main` keeps its run. Until
the runners are online, the temporary exception in
[`CLAUDE.md`](../../CLAUDE.md#flow) replaces `ci-gate` with recorded local
evidence.

## Nightly

The nightly workflow runs on the same runners:

1. `./build regress --level 0 --level 1 --level 2` across all IPs.
2. `./build fpga` and `./build asic` for every IP with `fpga/` or `asic/`
   inputs.
3. Metrics collection into `wiki/tracker/data/<date>.json` (versioned
   schema).
4. A data PR from branch `nightly/<date>`, labelled `type:data`, under the
   permanent exception in [`CLAUDE.md`](../../CLAUDE.md#flow) (guarded to
   `wiki/tracker/data/**`, auto-merged after `ci-gate`).
5. On failure, open or update the single `nightly-red` issue; close it when
   the nightly is green again.

## Metrics

Each nightly record holds:

| Group | Metrics |
| --- | --- |
| Code | LOC and file counts by kind and IP |
| Tests | Counts, pass/fail, runtime per level, slowest tests |
| GitHub flow | Issues, PRs, lead time, review rounds |
| FPGA | Per [FPGA BKM](fpga.md#metrics) |
| ASIC | Per [PD BKM](pd.md#metrics) |

The tracker pages under `wiki/tracker/` render history; the bot never edits
the README.

## Labels

Defined in `cfg/labels.yaml` and synced by `./build labels sync`
(`--dry-run` previews; labels not in the config are deleted only with
`--prune`); `./build labels check <number>` applies the rules below to an
issue or PR. The [labels spec](../tools/labels/SPEC.md) owns the config
schema and command behavior.

| Dimension | Rule | Values |
| --- | --- | --- |
| `type:` | Exactly one | `feat`, `bug`, `docs`, `infra`, `test`, `refactor`, `epic`, `data` |
| `area:` | At least one | `rtl`, `dv`, `build`, `ci`, `wiki`, `skills`, `fpga`, `pd` |
| `ip:` | Per IP touched | Derived from `src/` directories |
| `prio:` | Optional, at most one | `p0`, `p1`, `p2` |
| State | As needed | `in-progress` (claim), `blocked`, `needs-captain`, `nightly-red` |

`cfg/labels.yaml` is the source of truth for values; this table follows it.
