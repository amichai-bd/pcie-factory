# DevOps BKM

> Provenance: adapted from nand2anything `wiki/bkm/bkm_devops.md` (commit
> `085ef4b`, same owner, no license file). Tool, regression-evidence and CI
> principles kept; project registries, exclusions, host routing and media
> allowances dropped.

## Scope

This page owns the build entry point, levels and budgets, local evidence,
CI selection, the nightly run, labels and metrics. The
[agent-flow](../../.claude/skills/agent-flow/SKILL.md) skill owns the delivery
flow and merge gate; [`CLAUDE.md`](../../CLAUDE.md#flow) owns its exception.
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
| `arch` | Architecture-model perf scenarios of a top-level IP ([arch BKM](arch.md)). |
| `labels` | Label sync from `cfg/labels.yaml`. |
| `ci` | `ci-gate` helpers: unit-test suites and job summary ([CI spec](../tools/ci/SPEC.md)). |
| `metrics` | Nightly metrics, the data guard and the `nightly-red` issue ([metrics spec](../tools/metrics/SPEC.md)). |
| `wiki` | Wiki site build. |
| `clean` | Remove `workdir/` output. |

Subcommands land with their issues (#6 core, #7 check, #10 labels, #11 ci, #12 wiki,
#13 fpga, #14 asic, #55 arch); until a SPEC exists, the issue owns the contract.

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
  name an IP. Facts that vary live in `cfg/` or in `src/<kind>/<ip>/`, not in Python
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
| Source layout: kind-first `src/<kind>/<ip>/`, folders name IPs, `arch`/`fpga`/`asic` for top-level IPs only, flat prefixed sub-IPs | [layout](../project/layout.md#ips-and-sub-ips) |
| Filelists: every path resolves, no duplicate after flattening, no orphan RTL; every IP has its DV filelists and three valid level files | [layout](../project/layout.md#per-ip-files) |
| Wiki mirror: `wiki/src/` has every IP's required pages and no folder without a `src/` counterpart | [wiki BKM](wiki.md#page-set) |
| Flow inputs: `src/fpga/<ip>/` and `src/asic/<ip>/` pass the FPGA and ASIC flows' `--dry-run` validation, without the vendor tools | [checks spec](../tools/checks/SPEC.md#flow-input-check) |
| Neutrality: the word list in `cfg/checks.yaml`, with the scope and exemptions set by the contract | [`CLAUDE.md`](../../CLAUDE.md#design-neutrality) |
| Publishability: no keys, tokens, URL credentials, IP addresses, home paths or personal email addresses in any file, with reviewed exemptions in `cfg/checks.yaml` | [wiki BKM](wiki.md#publishability) |

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
| Docs only | Link check and the neutrality and publishability checks |
| `tools/`, `cfg/` | `./build check`, the tool unit tests, L0 of all IPs |
| RTL or DV | `./build check`, `./build regress --level 0 --level 1 --affected origin/main` |
| Architecture models (`arch/`) | `./build check`, the model self-tests, `./build arch --ip <ip>` ([arch BKM](arch.md#evidence)) |
| Physical inputs (`fpga/`, `asic/`) | The above plus the flow on demand, when the host has the tool |

Run the required set once on the commit intended for review and record exact
commands, seeds, counts and every non-pass entry in the PR. Evidence shows
behavior, not command success. Treat every warning (lint, simulation, flow)
as a failure unless a reviewed waiver explains it. Logs, waves and reports
stay under `workdir/`; the PR summarizes them and never commits them.
Missing evidence is a review finding.

## Continuous integration

Workflows live in `.github/workflows/` (`ci`, `nightly`, `pages`, `fpga`,
`asic`) and run on self-hosted runners selected with
`runs-on: [self-hosted, linux, x64]`. The runners are registered to this
repository only, so no workflow names the design target. The CI host is
the Omarchy machine; `cfg/host.yaml` pins its toolchain.

**Forks.** The repository is public, so fork code must not reach these
runners. Every job that a `pull_request` can trigger carries

```yaml
if: github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name == github.repository
```

so it runs on push and dispatch and for PRs from this repository, and is
skipped for unmodified fork PRs. This guard is defense in depth only: a
`pull_request` run uses the workflow file from the PR's own merge commit, so
a fork's copy can drop it. The repository keeps GitHub's default fork-run
approval setting (approval for first-time contributors only); the captain
accepts the residual risk that a returning outside contributor's fork could
run code on the runners. Maintainers **never approve a fork PR's workflow
run**. Fork changes reach CI only by review and re-push: a maintainer reviews
the whole diff, including every workflow and `./build` change, and only then
pushes its commits to a branch in this repository, because the re-push is
when fork code reaches the self-hosted runners. The maintainer merges
through a PR from that branch, whose `ci-gate` is the gate (a skipped job
counts as passing a required check, so a fork PR's own run proves nothing).
Workflows never use `pull_request_target` with a PR checkout and never echo
secrets.

**`ci-gate`** is the one required check: branch protection on `main`
requires it, admins included, so a red or missing run blocks every merge. It
runs on pull requests to `main`,
pushes to `main` and manual dispatch:

1. The data guard: a `nightly/` branch may change only
   `wiki/tracker/data/**` ([metrics spec](../tools/metrics/SPEC.md#guard)).
   Other branches pass at once.
2. `./build check`.
3. `tools/` unit tests, when `tools/`, `cfg/` or `build` changed, and the
   model unit tests of the affected IPs (`./build ci unit`).
4. `./build regress --level 0 --level 1` for the affected IPs.

**`ci-gate` on the data PR.** The nightly opens its data PR with
`GITHUB_TOKEN`, which starts no runnable `pull_request` run, so it
dispatches `ci-gate` on the data branch. Protection does not count a
dispatched check run toward a PR, so the dispatched run's `ci-gate-status`
job mirrors the gate's result as a `ci-gate` commit status on the tested
commit; the required check is bound to GitHub Actions, which accepts a
status that app posts. Only that job holds `statuses: write`, and only for
a dispatch on a `nightly/` ref (push triggers only on `main`), never for a
`pull_request` (so never for a fork); its state is the gate job's own
result (a failure, cancellation or skip blocks the merge). A person or
another app cannot post a status that counts, because protection only
accepts `ci-gate` from GitHub Actions; a workflow a writer adds could,
exactly as a writer can already edit `ci.yml` in their own PR, so review
stays the control for writers
([CI spec](../tools/ci/SPEC.md#build-ci-status)). Any writer can dispatch
`ci.yml` on any `nightly/*` ref, and the status binds to the tested commit,
not to a PR or its base
([what a status vouches for](../tools/ci/SPEC.md#the-workflow)).

**Review is process, not enforcement.** Branch protection on `main`
requires `ci-gate` only, with no required approving reviews. The
independent `REVIEW-VERDICT` gate is kept by the
[agent flow](../../.claude/skills/agent-flow/SKILL.md#7-merge-firstmate),
not by GitHub; the nightly data PR is the one exception and merges on
`ci-gate` alone.

The [CI tool spec](../tools/ci/SPEC.md) owns the workflow's steps and
`cfg/ci.yaml`; the [regress spec](../tools/regress/SPEC.md#selection) owns
the exact selection.

**Affected IPs** (selection rules in `cfg/ci.yaml`):

| Change | Selected |
| --- | --- |
| An IP's own files: under `src/<kind>/<ip>/` for any kind | That IP |
| A file reached through `-F` from an IP's filelists | That IP |
| `tools/**`, `cfg/**` or the `build` launcher | All IPs |
| Shared DV helpers under `src/dv/common/` (reached by no filelist) | All IPs |
| A sub-IP's models under `src/dv/<sub>/models/` | The sub-IP and every ancestor IP (every IP whose `.rtl.f` reaches it) |
| Push to `main`, manual dispatch | All IPs |
| Docs only | Checks only |

A sub-IP is an IP in its own right: a change inside its folders
(`src/<kind>/<ip>_<sub>/`) selects the sub-IP, and selects the parent only
when the parent's filelists reach the changed file or the change is in the
sub-IP's models.

Superseded PR runs are cancelled; every push to `main` keeps its run.

## Nightly

The nightly workflow runs daily on the same runners (the
[metrics spec](../tools/metrics/SPEC.md#workflows) owns its jobs,
permissions and data PR mechanics):

1. `./build check`, then `./build regress --level 0 --level 1 --level 2`
   across all IPs.
2. `./build fpga` and `./build asic` for every IP with `src/fpga/<ip>/` or
   `src/asic/<ip>/` inputs.
3. Metrics: the per-commit series `wiki/tracker/data/commits.jsonl` and the
   nightly record `wiki/tracker/data/<date>.json` (versioned schemas).
4. A data PR from branch `nightly/<date>`, labelled `type:data`, under the
   permanent exception in [`CLAUDE.md`](../../CLAUDE.md#flow): the data
   guard in `ci-gate` fails it if it touches anything outside
   `wiki/tracker/data/**`, and it auto-merges after `ci-gate` (the
   dispatched gate's result, posted as the `ci-gate` commit status). Each
   night's data PR closes any older open one.
5. On failure, open or update the single `nightly-red` issue; close it when
   the nightly is green again.

## Metrics

Every trend is plotted against commits on `main`, in order. The data:

| File | Holds |
| --- | --- |
| `commits.jsonl` | One line per main commit: code stats of its tree and its push `ci-gate` result and duration. Appended nightly; the first run backfills history. |
| `<date>.json` | One record per night, keyed by the main SHA it ran on. |

| Group | Metrics |
| --- | --- |
| Code | LOC and file counts by kind and IP; extension and directory distribution; commits, contributor count, repository size |
| Tests | Counts, pass/fail, runtime per level and IP, slowest tests, seeds, nightly result |
| GitHub flow | Issues by label, PRs merged, lead time, review rounds |
| CI | `ci-gate` runs, pass rate, duration, latest result on main |
| Health | Open PR and issue ages, stale issues, `nightly-red`, recent pass rate, review rounds, APPROVE at the merged head |
| Distributions | Issue close and first-response times, PR lead time, size and review rounds, CI and nightly durations, test runtimes, file sizes |
| FPGA | Per [FPGA BKM](fpga.md#metrics) |
| ASIC | Per [PD BKM](pd.md#metrics) |

The [metrics spec](../tools/metrics/SPEC.md) owns the schemas field by
field. Records hold only counts, durations, label names and SHAs, never
names, emails, host or path details
([publishability](wiki.md#publishability)); the tool refuses a record that
fails its scan. The tracker pages under `wiki/tracker/` render history; the
bot never edits the README.

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
