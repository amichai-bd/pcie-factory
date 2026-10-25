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
| `regress` | Regression by IP and level; `--affected <base>` selects IPs; `--coverage` merges coverage per IP. |
| `fpga` | Quartus flow for one IP ([FPGA BKM](fpga.md)). |
| `asic` | ORFS flow for one IP ([PD BKM](pd.md)). |
| `arch` | Architecture-model perf scenarios of a top-level IP ([arch BKM](arch.md)). |
| `labels` | Label sync from `cfg/labels.yaml`. |
| `pr` | Read-only pre-check of a PR body's recorded evidence before review ([pr spec](../tools/pr/SPEC.md)). |
| `ci` | `ci-gate` helpers: unit suites, vendor checks, aggregation and job summary ([CI spec](../tools/ci/SPEC.md)). |
| `metrics` | Nightly metrics, trend drop alerts, the data guard and the `nightly-red` issue ([metrics spec](../tools/metrics/SPEC.md)). |
| `wiki` | Wiki site build. |
| `specs` | Local cache of official reference documents, outside the repository and never committed ([specs spec](../tools/specs/SPEC.md)). |
| `clean` | Remove `workdir/` output. |

Subcommands land with their issues (#6 core, #7 check, #10 labels, #11 ci, #12 wiki,
#13 fpga, #14 asic, #55 arch); until a SPEC exists, the issue owns the contract.

## Tools and configuration

```text
build                       launcher only
tools/<tool>/               generic Python, unit tests in tools/<tool>/tests/
wiki/tools/<tool>/SPEC.md   outcomes, CLI, behavior, failures, tests
cfg/                        generic configuration (levels, ci, host, checks, labels, pr)
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
| Structural clock-domain crossings: no flop samples another clock's flop or annotated input port except through an approved crossing cell; `cdc-cell` directives and `cdc-domain` port annotations | [RTL BKM](rtl.md#clock-domain-crossings) |
| Source layout: kind-first `src/<kind>/<ip>/`, folders name IPs, `arch`/`fpga`/`asic` for top-level IPs only, flat prefixed sub-IPs | [layout](../project/layout.md#ips-and-sub-ips) |
| Filelists: every path resolves, no duplicate after flattening, no orphan RTL; every IP has its DV filelists and three valid level files | [layout](../project/layout.md#per-ip-files) |
| Wiki mirror: `wiki/src/` has every IP's required pages and no folder without a `src/` counterpart | [wiki BKM](wiki.md#page-set) |
| Flow inputs: `src/fpga/<ip>/` and `src/asic/<ip>/` pass the FPGA and ASIC flows' `--dry-run` validation, without the vendor tools | [checks spec](../tools/checks/SPEC.md#flow-input-check) |
| Neutrality: the word list in `cfg/checks.yaml`, and the IP ids discovered from `src/` used as identifiers, with the scope and exemptions set by the contract | [`CLAUDE.md`](../../CLAUDE.md#design-neutrality) |
| Citations: no long block quotes or quoted runs, and the citation form, in `src/` and `wiki/src/` (a heuristic; review is the main control) | [wiki BKM](wiki.md#citations) |
| Publishability: no keys, tokens, URL credentials, IP addresses, home paths or personal email addresses in any file, with reviewed exemptions in `cfg/checks.yaml` | [wiki BKM](wiki.md#publishability) |
| Spec cache: no PDF, no copy of a cached reference document or its text, and no run of its text longer than a quote, in any file; every listed document on the references page | [wiki BKM](wiki.md#citations) |

`tools/` unit tests run when `tools/` or `cfg/` change. Doc line caps are a
review guideline, not a check.

## Levels and budgets

Levels are exclusive ([DV BKM](dv.md#regression-lists)); callers combine
them with repeated `--level`. Each level has one purpose (captain decision,
2026-10-18,
[#325](https://github.com/amichai-bd/pcie-factory/issues/325)):

| Level | Purpose and contents | Budget | Runs |
| --- | --- | --- | --- |
| L0 | **Did we break the build:** files, names, filelists, elaboration (the design is alive), lint, style and the fastest smoke checks; seconds to a few minutes | 120 s of CPU for all IPs | Locally while developing; first in `ci-gate` |
| L1 | **The PR gate:** functional checks that give high confidence the change is good, but not the full test: each IP's RTL acceptance entries and fastest discriminating tests, a few seeds ([placement](dv.md#regression-lists)); affected IPs only. Beside it in `ci-gate`, Quartus analysis and synthesis of the affected FPGA targets | 1200 s of CPU for all IPs; `ci-gate` (L0+L1 of affected IPs plus checks) targets 10 min | `ci-gate` |
| L2 | **The full test:** long tests, seed sweeps, soaks, coverage floors, full mutant campaigns, the full FPGA flow (fit, timing), the ASIC synthesis-only check and the full ASIC flow ([paused](#asic-pause)), metrics | 9600 s of CPU; nightly window | Nightly; targeted entries (named tests or seeds) when fixing or chasing confidence, never the whole level for a PR |

**Where each implementation check runs** (captain decision, 2026-10-19,
[#362](https://github.com/amichai-bd/pcie-factory/issues/362): logic and
fast PR CI first):

| Check | `ci-gate` (PR, push) | Nightly (L2) |
| --- | --- | --- |
| FPGA analysis and synthesis (`./build fpga --synth-only`) | Affected targets, required | Inside the full flow |
| FPGA fit and timing (`./build fpga`) | No | Every target |
| ASIC synthesis (`./build asic --synth-only`) | No | Every target; a failure fails the nightly |
| ASIC place and route, timing (`./build asic`) | No | Every target, while not [paused](#asic-pause) |

The trade-off: a PR proves its logic, that it synthesizes in Quartus, and
nothing physical. An FPGA fit or timing problem and an ASIC-incompatible
construct surface at the next nightly, which fails and opens `nightly-red`,
not on the PR that caused them. A change that can move timing or fit, or
uses a construct the ASIC flow may reject, runs `./build fpga --ip <id>` or
`./build asic --ip <id> --synth-only` locally and records it in the PR.

Budgets live in `cfg/levels.yaml` and are **CPU time**: the sum, over a
level's tests, of each test's user plus system CPU time (its process tree,
simulator included). Wall time on the shared host varied about fourfold
with load for the same tests (#166: a serial L1 sum of 152 s to 622 s), so a
wall-time budget would measure the host, not the tests; CPU time barely
changes with load, and any host can check it. `regress` prints both and
warns, naming the CPU number, when a level exceeds its budget. It warns
rather than fails: CPU time still varies a little between hosts and
Python versions, and a red gate on a timing number would block correct
changes; the warning is in the job summary, and the fix is to move or
speed up tests, never to raise the budget silently.
Measured for #166 with all IPs: L1 356.7 s of CPU against 600 s
(134 tests at `32bb298`); L0 41.8 s against 120 s.

**Budget basis: the plain build** (captain decision on
[#435](https://github.com/amichai-bd/pcie-factory/issues/435#issuecomment-6394848460),
2026-10-25). The L0 (120 s) and L1 (1200 s) budgets apply to the plain
build `ci-gate` runs, without `--coverage`; a level meets its budget, and
its margin is measured, on that build. The nightly runs L0 and L1 with
coverage instrumentation, which costs more CPU; those figures are reported
in its summary but not budgeted, and a coverage-build overrun of L0 or L1
is no reason to move tests. The L2 budget stays sized on the nightly's
coverage build.

The L1 and L2 budgets were raised on 2026-10-18 (captain decision on
[#272](https://github.com/amichai-bd/pcie-factory/issues/272#issuecomment-6242089772))
after speed-ups and rebalancing: measured with all IPs and coverage at
`697d896`, L0 112.5 s, L1 1006.3 s and L2 7958.2 s of CPU. L1 rose from
600 s to 1200 s and L2 from 7200 s to 9600 s, the need plus about 20%.
Restoring one directed entry to L1 then added 48.2 s, for an L1 of
1054.5 s. A change to a single IP selects an affected L1 of 123 s to 577 s;
a change to a widely shared block selects nearly every IP, about 1054 s,
which the raised 1200 s budget covers.

Measured for [#327](https://github.com/amichai-bd/pcie-factory/issues/327)
on one 4-CPU host (`--jobs 4`), all IPs, at `005836f` and after it. L0 to L2
with `--coverage` (the nightly): L2 fell from 4642.7 s to 3394.6 s of CPU
with a cold cache and 3257.7 s warm (the reused model-only runs), the run
from 27.2 to 22.5 minutes cold and 17.4 minutes warm, with the merged
coverage of every IP identical point for point. L0+L1 without coverage
(`ci-gate`'s selection for a global change): the 37 builds took 150 s
before and none warm, the run 186 s before and 152 s warm; test CPU is
unchanged (each L1 entry above about 10 s is an IP's RTL acceptance entry
or the only catch of a recorded mutant, so it stays in L1).

## Local evidence

PR evidence is the **affected L0+L1**, the levels `ci-gate` runs for the
same selection. Everything heavier is the nightly's: full L2, coverage
floors and broad mutant campaigns never gate a PR, and a nightly floor miss
or L2 failure opens an issue naming the commits since the last green
nightly ([Nightly](#nightly)), so a drop surfaces within a day.

| Change | Run before review |
| --- | --- |
| Docs only | Link check and the neutrality and publishability checks |
| `tools/`, `cfg/` | `./build check`, the tool unit tests, L0 of all IPs (`ci-gate`'s selection; plus L1 of any IP another row selects) |
| RTL or DV | `./build check`, `./build regress --level 0 --level 1 --affected origin/main` (or cite `ci-gate`'s run of it on the head), mutants of the changed code |
| Architecture models (`arch/`) | `./build check`, the model self-tests, `./build arch --ip <ip>` ([arch BKM](arch.md#evidence)) |
| Physical inputs (`fpga/`, `asic/`) | The above plus the flow on demand, when the host has the tool |

| Evidence | Rule |
| --- | --- |
| Affected L0+L1 | Required. `ci-gate` runs exactly this selection on the head, so its green run may be cited in place of a local rerun (link the run, give its per-level counts and CPU). |
| Targeted L2 entries | Only when the change targets L2 behaviour (a long test, sweep or soak it changes or fixes) or a reviewer asks: the named entries and seeds, each with `./build sim --ip <ip> --test <name> --seed <s>`, never the whole level. |
| Mutants | Only against the code the PR changes: each a deliberate bug in a changed block, caught by a named entry ([DV BKM](dv.md#output-and-evidence)). Full mutant campaigns are nightly. |
| Coverage | Not PR evidence; the nightly merges coverage and checks the floors. A PR that sets or raises a floor cites the merged value it is based on ([coverage](dv.md#coverage)). |

Run the required set once on the commit intended for review and record exact
commands, seeds, counts and every non-pass entry in the PR. After a review
round, rerun only what the fixes affect; `ci-gate` reruns L0+L1 on every
push. Evidence shows
behavior, not command success. Treat every warning (lint, simulation, flow)
as a failure unless a reviewed waiver explains it. Logs, waves and reports
stay under `workdir/`; the PR summarizes them and never commits them.
Missing evidence is a review finding; `./build pr check <pr>` catches
missing or malformed evidence before review, and the mechanical slips that
cost review rounds: results labelled with a stale SHA, `Closes` when the
body says acceptance moved elsewhere, and design or IP names added outside
`src/` and `wiki/src/` ([pr spec](../tools/pr/SPEC.md)).

**Shared host: CPU slots.** Several crewmates share one machine. Instead of
one global lock that serializes every run, `./build sim` and
`./build regress` take `--slots DIR` (or `$BUILD_SLOTS`): each build and
simulation process holds one of `nproc` slot locks in `DIR` while it runs,
so all runs that name the same directory together never start more
processes than the host has CPUs, and a small run waits only for a free
CPU, not for another crewmate's whole regression. Use one directory per
host for every crewmate, any `--jobs` (`nproc` is a sensible upper bound),
and run mutants serially with `--jobs 1`, so the first failing entry is
deterministic ([build spec](../tools/build/SPEC.md#cpu-slots)).
The author owns all evidence: the reviewer audits it and the `ci-gate`
result and never re-runs anything; a claim without recorded proof goes back
to the author to run.

## Build cache

Every `./build sim` and `./build regress` reuses Verilator builds from one
content-keyed cache per host, outside every checkout
([build spec](../tools/build/SPEC.md#build-cache)): `$BUILD_CACHE`, else
`~/.cache/<repo>/build`. A build whose sources, include files, settings and
tool versions are byte-identical to an earlier one, in any run or worktree,
takes no compile; a changed input is a new key, so nothing stale is reused.
Generated C++ goes through ccache (Verilator's `OBJCACHE`), in ccache's
own per-user directory (`~/.cache/ccache` by default), so a build after a
small RTL change recompiles only what changed. Builds that do compile are
bounded by the host's CPUs and available memory, so a cold host does not
run out of memory. Entries are written to a temporary directory and renamed into
place, so concurrent runs never read a partial build. Entries unused for 14
days are pruned. `--no-cache` (or `$BUILD_CACHE=off`) builds afresh, for a
cold measurement or to rule the cache out.

**CI hosts.** `actions/checkout` cleans the job checkout, `workdir/`
included, so the cache lives at the fixed per-host default under the runner
user's home, like the [specs cache](../tools/specs/SPEC.md): every runner of
a host that runs as that user shares it, it persists across jobs, and the
first job on each host is cold. The WSL runners, the supported self-hosted
runners, keep it there; no workflow configures it.
**Trust:** the key is content, so a reused build is what a fresh build of
the same inputs makes, and the runs that write the cache are the runs the
runners execute anyway. Its residual risk is the runner host's own: the
[Forks](#continuous-integration) rules accept that a returning outside
contributor's fork PR can run on the runners without approval, with a
workflow copy that drops the guard. Such a run could write a planted
executable under a valid key, and later trusted runs would reuse it and
pass until it is pruned: a lasting false pass, not a one-off. The cache
adds no new way in, since that code could equally tamper with the host's
toolchain or runner environment, but it makes the effect persist. If a
compromise is suspected, delete `~/.cache/<repo>/build` and the ccache
directory (`ccache -C`, or `~/.cache/ccache`) on every runner host, or bump
`SCHEMA` in `tools/build/cache.py` so every old entry is ignored. Each
host's builds are bounded to 20 GiB, least recently used first.

**Model checks.** A regression entry marked `rerun_when: models_change`
(one that runs reference models only, no RTL) reuses its earlier pass on
the host while its models, testbench and tools are unchanged
([regress spec](../tools/regress/SPEC.md#reuse-of-unchanged-model-checks));
the nightly runs on one host type with this cache, so such an entry runs
there the first night after its inputs change. The DV plan names every
such entry and its reason.

## Continuous integration

Workflows live in `.github/workflows/` (`ci`, `nightly`, `pages`, `fpga`,
`asic`, `labels`) and every job selects
`runs-on: [self-hosted, linux, x64, wsl]`. The runners are registered to this
repository only, so no workflow names the design target. The WSL host,
whose runners carry the label `wsl`, is the only supported CI host
([host SPEC](../tools/host/SPEC.md#hosts)). The Omarchy host is unsupported
(captain decision, #321): on it `ci-tests` hit its 20-minute timeout and
`asic` stalled in detailed routing for two hours, where WSL finished the
same inputs well within budget. Its runners lack the `wsl` label, so no job
lands there; if no `wsl` runner is online, jobs wait in the queue rather
than fall back. Taking those runners offline is a host action for the
captain.

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
   `wiki/tracker/data/**`, by adding valid run records and growing the
   series ([metrics spec](../tools/metrics/SPEC.md#guard)). Other branches
   pass at once.
2. `./build specs fetch`, best effort (`continue-on-error`, 3 minutes), so the
   spec-cache check sees the reference documents; then `./build check`
   and `./build wiki build --strict`.
3. `tools/` unit tests, when `tools/`, `cfg/` or `build` changed (else only
   the Verilator-built vocabulary tests, when an RTL vocabulary header
   changed), and the model unit tests of the affected IPs (`./build ci unit`).
4. `./build regress --level 0 --level 1` for the affected IPs.
5. In parallel with those steps, Quartus analysis and synthesis only (no
   fit or timing) for affected FPGA targets. Source/filelist,
   wrapper/constraint and tool/config changes select targets; docs-only PRs
   select none ([selection](../tools/ci/SPEC.md#build-ci-vendor)). FPGA fit
   and timing, ASIC synthesis, full ASIC physical implementation and L2 are
   nightly ([levels](#levels-and-budgets)).

The single required `ci-gate` aggregates the simulation/checks and the
FPGA job. Both must explicitly succeed; failure, cancellation or an
unexpected skip fails the gate. Push/dispatch still select all targets.

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
([what a status vouches for](../tools/ci/SPEC.md#the-workflow)). That
dispatched gate is proportionate to the data: once the guard has proved,
against the PR's base, that the branch changes only valid data files, the
unit, regression and FPGA steps select nothing, and the checks
(publishability included) and the strict wiki build (the tracker rendered
from the new data) decide it. A mixed change fails the guard, and with it
`ci-gate` ([metrics spec](../tools/metrics/SPEC.md#workflows)).

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
| `tools/**`, `cfg/**` or the `build` launcher | All IPs, level 0 only (with the tools unit tests); an IP another row selects keeps every level |
| A file the IP's DV or arch Python imports, directly or through other files: under the import root (`import_root`, the tools on `sys.path` in simulations), or a module of another IP's arch or DV folder (a model, a testbench environment) imported by bare name | That IP, every level (found by an import scan; no filelist shows these) |
| Shared DV helpers under `src/dv/common/` (reached by no filelist) | All IPs |
| A sub-IP's or integrated IP's models under `src/dv/<id>/models/` | That IP and every ancestor IP (every IP whose `.rtl.f` reaches it) |
| Push to `main`, manual dispatch | All IPs |
| Dispatch on a `nightly/` branch that passes the data guard (data files only) | Checks and the strict wiki build only |
| Docs only (wiki, README, skills) | Checks and the strict wiki build only |

A sub-IP is an IP in its own right: a change inside its folders
(`src/<kind>/<ip>_<sub>/`) selects the sub-IP, and selects the parent only
when the parent's filelists reach the changed file or the change is in the
sub-IP's models. An integrated top-level IP is selected the same way
relative to the IP that integrates it.

**Superseded runs are cancelled.** A new PR head or push to `main` cancels
that PR's or branch's in-flight and queued `ci` run and `pages` build.
A dispatched `ci` run (the data PR's gate and `ci-gate-status`) and a
dispatched or scheduled `pages` build (the nightly's republish and the daily
backstop) each have a group of their own run, so a push never cancels them.
The nightly and the manual `fpga` and `asic` flows keep their own groups
without `cancel-in-progress` and run only by dispatch or schedule, so no
push reaches them. The pages deploy is never cancelled mid-run. The merge gate is unchanged: a
cancelled run's `ci-gate` fails on the old SHA only, and the new head always
gets its own run
([CI spec](../tools/ci/SPEC.md#concurrency)).

## Nightly

The nightly workflow runs daily on the WSL host's runners only (label
`wsl`), like every workflow; on the Omarchy runners it took about 2 hours
against about 27 minutes on WSL (captain decision, #273). If no `wsl` runner is
online, every nightly job waits in the queue rather than fall back, its
`report` job too, so no `nightly-red` issue opens; GitHub cancels a job
queued for 24 hours. The
[metrics spec](../tools/metrics/SPEC.md#workflows) owns its jobs,
permissions and data PR mechanics:

1. `./build check`, `./build ci unit` (every unit suite, including the
   Verilator-built vocabulary header tests and the `perf:<ip>` suites, which
   run `./build arch` for every IP with `src/arch/<ip>/perf/`; the nightly
   runs the perf scenarios only there), then `./build regress --level 0
   --level 1 --level 2 --coverage --rotate <date>` across all IPs (merged line, toggle and functional coverage
   per IP, with any floors checked; [regress SPEC](../tools/regress/SPEC.md#coverage)),
   plus the night's [rotated extra seeds](#seed-rotation).
2. `./build fpga` for every IP with `src/fpga/<ip>/` inputs; the ASIC
   synthesis-only check (`./build ci vendor --kind asic`, step
   `asic-synth`) for every IP with `src/asic/<ip>/` inputs, whose failure
   fails the nightly; and `./build asic` (the full flow) for those IPs
   while it is not [paused](#asic-pause). A failed step prints every error line of its
   log before the tail ([failure excerpt](../tools/build/SPEC.md#failure-excerpt))
   to the job log. That excerpt, and the FPGA summary's one sanitized line
   per unknown Quartus warning ID ([FPGA BKM](fpga.md#known-quartus-warnings)),
   are the only parts of a vendor log that
   leave the host: the logs themselves stay in `workdir/` and are never
   uploaded as artifacts (publishability).
3. Metrics: the per-commit series `wiki/tracker/data/commits.jsonl` and the
   run's own record `wiki/tracker/data/<date>T<HHMMSS>Z.json`, named by its
   UTC start so a same-day rerun never overwrites one (versioned schemas), and
   the per-IP trend drop alerts (thresholds in `cfg/metrics.yaml`) in the
   job summary and on the tracker ([trends](../tools/metrics/SPEC.md#trends)).
4. A data PR from branch `nightly/<date>`, labelled `type:data`, under the
   permanent exception in [`CLAUDE.md`](../../CLAUDE.md#flow): the data
   guard in `ci-gate` fails it if it touches anything outside
   `wiki/tracker/data/**` or removes or malforms data, and it auto-merges
   after `ci-gate` (the dispatched gate's result, posted as the `ci-gate`
   commit status). No run's record is lost: while a data PR is open, the
   next run commits on top of it, and any other open data PR is carried
   into it and closed ([stage](../tools/metrics/SPEC.md#stage)).
5. On failure, open or update the single `nightly-red` issue, naming the
   failed steps (a full-L2 failure, a coverage-floor miss or a failing
   extra seed fails `regress`), the commits since the last green nightly,
   with a compare link, and each failed regression run with its exact seed
   and the `./build sim --ip <ip> --test <name> --seed <seed>` command that
   reproduces it; close it when the nightly is green again
   ([metrics spec](../tools/metrics/SPEC.md#red)).

### ASIC pause

The full ASIC flow (place and route, timing) can be paused while the CI
host cannot carry it (captain decision, 2026-10-19,
[#362](https://github.com/amichai-bd/pcie-factory/issues/362): the WSL host
dropped three times under ASIC load). The one switch is `ASIC_PAUSED` in
the workflow-level `env` of `.github/workflows/nightly.yml`:

- `"true"`: the nightly's full ASIC step runs no flow and records
  `asic=paused`; tonight's record carries `{"status": "paused"}` for the
  `asic` section, never an earlier run's numbers, a paused step is not a
  failure and opens no `nightly-red` issue, and the tracker labels the ASIC
  section paused ([metrics spec](../tools/metrics/SPEC.md#fpga-asic)).
  **Do not dispatch `asic.yml`** while paused: it stays available for a run
  the captain approves.
- `"false"`: the full ASIC step runs again.

The pause does not touch the nightly's `asic-synth` step (synthesis only,
a few minutes per IP), which runs every night either way, nor the FPGA
flow in `ci-gate` and the nightly. Resuming needs a captain go once the
host is stable: an issue and PR that set `ASIC_PAUSED: "false"` and cite
the decision. The next nightly runs the full ASIC flow again; dispatch
`asic.yml` first for one IP if a trial run is wanted.

### Seed rotation

Pinned seeds repeat the same stimulus every night, and coverage cannot
fully measure the stimulus diversity lost when #327 trimmed L2 seeds. The
nightly therefore runs, besides every pinned seed, `extra_seeds` more seeds
per L2 entry derived from its UTC date (`--rotate <date>`; the rule is in
the [regress SPEC](../tools/regress/SPEC.md#seed-rotation)), so each night
explores new stimulus while PRs and `ci-gate` stay unchanged (#333):

- **Reproducible:** a seed is a pure function of the date, the IP and the
  test name, and the seed alone fixes the stimulus; a failing one is rerun
  with `./build sim --seed <seed>` as the `nightly-red` issue prints it, or
  the whole night's set with `./build regress --level 2 --rotate <date>`.
- **Coverage unchanged:** extra runs use plain builds and are never merged,
  so the coverage floors and the metrics series measure the pinned seeds
  only.
- **Budget:** `cfg/levels.yaml` `rotation` sets `extra_seeds: 1` and
  `budget_s: 3600` of CPU; no extra run starts once the extra runs have
  used it, and those not started are listed in the summary, not failed.
  The extra runs can pass the budget by the runs in flight when it is
  reached, at most `--jobs` (the nightly's `nproc`) of them; the plain
  builds only extra runs need (an entry whose pinned seeds all run on the
  coverage build) are built up front and charged to no budget, most from
  the build cache. With the pinned L2 at 3394.6 s of CPU (#327), L2 with its
  extra runs stays near 7000 s, inside the 9600 s level-2 budget and the
  nightly window. Record the first rotated nightly's measured rotation CPU
  and build time against these figures.
  Raise `extra_seeds` or the budget only with a measured nightly that
  still fits.
- **A failing extra seed is a real failure:** it fails `regress` and the
  nightly. Fix the bug it shows; when its stimulus deserves a permanent
  guard, pin the seed in the entry's list (or add a directed test) in the
  fix's PR.

## Metrics

Per-commit trends are plotted against commits on `main`, in order; nightly
trends against every nightly run, in run order. The data:

| File | Holds |
| --- | --- |
| `commits.jsonl` | One line per main commit: code stats of its tree and its push `ci-gate` result and duration. Appended nightly; the first run backfills history. |
| `<date>T<HHMMSS>Z.json` | One record per nightly run, named by its UTC start, naming the main SHA it ran on. |

| Group | Metrics |
| --- | --- |
| Code | LOC and file counts by kind and IP; extension and directory distribution; commits, contributor count, repository size |
| Tests | Counts, pass/fail, runtime per level and IP, slowest tests, seeds, nightly result |
| Coverage | Line, toggle and functional coverage per IP, and whether its floors held |
| GitHub flow | Issues by label, PRs merged, lead time, review rounds |
| CI | `ci-gate` runs, pass rate, duration, latest result on main |
| Health | Open PR and issue ages, stale issues, `nightly-red`, recent pass rate, review rounds, APPROVE at the merged head |
| Distributions | Issue close and first-response times, PR lead time, size and review rounds, CI and nightly durations, test runtimes, file sizes |
| FPGA | Per [FPGA BKM](fpga.md#metrics) |
| ASIC | Per [PD BKM](pd.md#metrics) |
| Arch | Per IP and perf scenario: status and the target metrics' values ([arch BKM](arch.md#evidence)) |

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
