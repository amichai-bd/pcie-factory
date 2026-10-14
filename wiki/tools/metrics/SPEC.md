# Metrics tool: `./build metrics`

`./build metrics` writes the nightly metrics record and the per-commit
series under `wiki/tracker/data/`, guards the nightly data branch, and keeps
the single `nightly-red` issue. The [DevOps BKM](../../bkm/devops.md#metrics)
owns what is measured and when; this page owns the command line, the data
schemas, the publishability rules and the workflows. Code: `tools/metrics/`
(`config.py`, `tree.py`, `gh.py`, `github.py`, `record.py`, `commits.py`,
`guard.py`, `red.py`, `cli.py`); configuration: `cfg/metrics.yaml`.

```
./build metrics collect [--out FILE] [--no-github] [--step NAME=pass|fail|skipped]... [--started TIME] [--repo OWNER/NAME]
./build metrics commits [--file FILE] [--ref REV] [--no-ci] [--limit N] [--repo OWNER/NAME]
./build metrics guard   --branch NAME --base REV [--head REV]
./build metrics red     --outcome success|failure --run-url URL [--repo OWNER/NAME]
```

The shared `--src-root` and `--workdir` options work as in the
[build spec](../build/SPEC.md#command-line). `--repo` defaults to
`$GH_REPO`, then `$GITHUB_REPOSITORY`, then the `origin` remote. GitHub is
reached only through `gh api` (REST, never GraphQL), authenticated by
`gh`'s own login or `$GH_TOKEN`.

Exit codes: `0` success; `1` the guard failed; `2` a configuration, git,
GitHub (for `red`) or publishability error.

## Configuration: `cfg/metrics.yaml`

| Key | Meaning |
| --- | --- |
| `data_dir` | Where records are committed (`wiki/tracker/data`); the only path a data branch may change. |
| `branch_prefix` | Head-branch prefix of data PRs (`nightly/`). |
| `data_author` | Author email of the nightly data commits (the GitHub Actions bot). |
| `src_root` | Source root in each commit's tree, for IP discovery (`src`). |
| `exclude` | Globs left out of every code statistic (the data directory). |
| `kinds` | Kind name to globs: `rtl_sv`, `dv_sv`, `dv_py`, `tools_py`, `wiki_md`, `config`, `workflows`, `skills`. A file may match several kinds. |
| `line_comments`, `block_c` | Per extension, line-comment prefixes, and the extensions with `/* */` blocks, for code lines. |
| `windows` | Trailing windows in days (`[1, 7, 30]`). |
| `review_marker` | The review-verdict comment marker (`REVIEW-VERDICT`). |
| `ci_workflow`, `nightly_workflow` | Workflow files whose runs feed `ci`, the series and the nightly duration. |
| `stale_days`, `health_ci_runs` | Health: stale-issue age, and how many latest main runs the pass rate covers. |
| `max_pr_details` | How many most recently merged PRs are fetched one by one for their size. |
| `slowest` | Number of slowest test runs kept. |
| `fpga_fields`, `asic_fields` | The whitelist of dotted keys copied from each IP's flow `metrics.json`; `*` matches one key. |

Globs are repository-relative: `*` stays within a path segment, `**`
crosses segments. Every key is required and no other is allowed; a missing
file, invalid YAML, a missing or unknown key or a bad value is exit 2.

## Publishability

The records are committed to a public repository and published on the
site ([publishability](../../bkm/wiki.md#publishability)). So:

- **Whitelist only.** Every field below is built from a named source field;
  no input document is copied whole. Flow `metrics.json` keys outside the
  `*_fields` lists are dropped, and so is any string value that is longer
  than 120 characters, starts with `/`, or has characters outside a
  conservative set. Test runs keep only IP, level, name, seed, status and
  runtime (no log paths, failure details or source root).
- **Counts, durations, label names, numbers, short SHAs.** No logins,
  names, emails, titles or bodies from GitHub; contributors are a count.
- **Last-line scan.** Before writing, every key and string of the record
  (and of each series line) is scanned; any hit refuses the whole file
  (exit 2). It flags email addresses, absolute, home or Windows paths,
  GitHub tokens, AWS (`AKIA`/`ASIA`) and `sk-` key shapes, IPv4 and IPv6
  addresses, URLs, domain names (common public and internal suffixes), and
  this machine's host name or user name (`hostname`, `$USER`, `$HOME`'s last
  part): a whole token, or, for names of four or more characters, a name
  joined to other letters or digits (`runner01`). Tests cover each pattern and an end-to-end record built from
  inputs with planted paths, emails and names.

## `collect`: the nightly record

Writes `--out` (default `<workdir>/metrics/<date>.json`; the nightly passes
`wiki/tracker/data/<date>.json`), JSON with sorted keys and one-space
indent. Code statistics come from the **committed tree at `HEAD`** (`git
ls-tree` and `git cat-file`), never from the working tree. A section whose
producer is absent is `{"status": "unavailable", "reason": R}` with `R` one
of `missing` (no input file), `invalid` (unreadable input), `disabled`
(`--no-github`), `forbidden` (HTTP 401/403), `not-found` (404), `gh-error`;
present sections carry `"status": "ok"`. Missing producers never fail the
command. `--step` records the nightly's step outcomes and `--started` its
start time.

Top level: `schema_version` (integer, `1`; bumped on any incompatible
change), then the sections below.

### `meta`

| Field | Value |
| --- | --- |
| `schema_version` | Same as the top level. |
| `generated_at` | UTC time of the run, `YYYY-MM-DDTHH:MM:SSZ`. |
| `date` | UTC date of the run; the file name. |
| `commit`, `commit_short`, `commit_date` | `HEAD` SHA (40 and 12 hex) and its committer date (UTC); on the scheduled run, the main commit the night ran on: the x-axis key shared with the series. |
| `tools` | `python`, `git`, `gh`, `verilator`, `cocotb`, `pytest`, `pyyaml`: version strings only, `null` when absent. |
| `nightly.steps` | `{name: pass\|fail\|skipped}` from `--step` (the nightly passes `check`, `regress`, `fpga`, `asic`). |
| `nightly.result` | `fail` if any step failed, else `pass`; `null` without steps. |
| `nightly.duration_s` | Seconds from `--started` (the nightly job's start) to `generated_at`: the nightly wall time up to metrics collection; `null` without `--started`. |

### `code`

A *bucket* is `{files, lines, code}`: tracked files, their non-blank lines,
and their non-blank lines that are not comments (`line_comments`,
`block_c`; other extensions count every non-blank line; binary files count
zero lines).

| Field | Value |
| --- | --- |
| `total` | Bucket over every tracked file outside `exclude`, plus `bytes`. |
| `kinds.<kind>` | Bucket per configured kind (all kinds, zeros included). |
| `ips.<ip>` | Bucket of the files the IP owns (its directory minus nested sub-IP directories), plus `kinds` with the non-empty kinds. IPs are discovered in the tree as in the [build spec](../build/SPEC.md#ip-discovery). |
| `by_ext.<ext>` | Bucket per file extension (`(none)` without one). |
| `by_dir.<dir>` | Bucket per top-level directory (`.` for files at the root). |
| `commits` | `git rev-list --count HEAD`. |
| `contributors` | Number of distinct author emails in `HEAD`'s history; the emails are never stored. |
| `repo_size_kb` | GitHub's repository `size`, or `null` without GitHub. |

### `github`

Three sections from the REST API. Window keys are `1d`, `7d`, `30d`
(from `windows`), trailing from `generated_at`.

**`github.issues`** (issues only, PRs excluded): `total`, `open`,
`open_by_label` (`{label: count}` over open issues, every label), and
`windows.<w>`: `opened`, `closed`, `opened_by_label`, `closed_by_label`.

**`github.pulls`**: data PRs (head branch starts with `branch_prefix`) are
left out of everything except `data_merged`. `total`, `open`, `merged`, and
`windows.<w>`: `opened`, `merged`, `data_merged`, `lead_time_h`
(`{median, p90}` hours from opened to merged, over PRs merged in the
window), `review_rounds` (`{median, max}` over the same PRs). One review
round is one `REVIEW-VERDICT: APPROVE|REQUEST-CHANGES @<sha>` comment.

**`github.ci`**: runs of `ci_workflow` (the workflow whose one job is
`ci-gate`; the Actions REST API, which works on the runner with
`GITHUB_TOKEN`). The windows count **every** run of the workflow: pull
requests, pushes to `main` and dispatches (including the nightly's
dispatched data-branch runs), so their pass rate is a mixed, all-events
number; `latest_main` and `health.ci_gate_main` are the main-only views.
`workflow`; `windows.<w>`: `runs`, `completed`,
`pass_rate` (success over success plus failure, timed out or startup
failure; cancelled and skipped runs do not count), `duration_s` (`{median,
p90}`, `run_started_at` to `updated_at` of completed runs);
`latest_main`: `{sha, conclusion, duration_s}` of the newest completed push
run on `main`, or `null`.

Percentiles are nearest-rank; medians are the usual midpoint; both are
`null` for no data. Hours and seconds are rounded to two and one decimals.

### `health`

A snapshot at `generated_at`; each entry is `null` when its source is
unavailable.

| Field | Value |
| --- | --- |
| `open_prs` | `{count, age_h}`: open PRs and their ages in hours, sorted. |
| `open_issues` | `{count, age_h, stale, stale_days}`: open issues, their ages, and how many had no activity (`updated_at`) for more than `stale_days`. |
| `nightly_red` | `{open}`: open issues labelled `nightly-red`. |
| `ci_gate_main` | `{runs, pass_rate}` over the latest `health_ci_runs` completed runs on `main`. |
| `review_rounds_median` | Median review rounds over merged (non-data) PRs. |
| `approve_at_merged_head` | `{merged, approved, share}`: merged PRs, how many have an `APPROVE` verdict naming a prefix of their final head SHA, and the ratio. |

### `tests`

From `<workdir>/regress/summary.json` (the [regress spec](../regress/SPEC.md)).

| Field | Value |
| --- | --- |
| `levels`, `passed`, `wall_s` | As in the summary. |
| `totals.level<N>` | `tests`, `passed`, `failed`, `runtime_s`, `budget_s`, `over_budget`. |
| `builds` | `{total, failed}`. |
| `ips.<ip>.level<N>` | `{tests, passed, failed, runtime_s}`. |
| `slowest` | The `slowest` longest runs, each `{ip, level, name, seed, status, runtime_s}`. |
| `runs` | Every run in that form, sorted by IP, level, name, seed: the seeds record. |

### `fpga`, `asic`

`{status, ips: {<ip>: {status, ...}}}` from `<workdir>/<ip>/fpga/metrics.json`
and `<workdir>/<ip>/asic/metrics.json` of every discovered IP that has one,
reduced to `fpga_fields` / `asic_fields`. The owning schemas are in the
FPGA and ASIC tool specs: the whitelist keeps device, resources
(`used`/`total`), per-clock Fmax and slacks, worst slacks, `met`, critical
path slack and delay, step status, runtime and warnings (FPGA); and
design, platform, clock period, status, WNS/TNS/hold slack, areas, cells,
utilization, max fanout and logic depth values, worst-path slack, cells and
group, power, DRC errors and elapsed time (ASIC). Net, pin and path names,
tool paths, images and work directories are dropped. An IP whose file does
not parse is `{"status": "invalid"}`.

### `distributions`

Raw value lists for histograms, each sorted ascending, `null` when the
source is unavailable:

| Field | Values |
| --- | --- |
| `issue_close_h` | Hours from opened to closed, per closed issue. |
| `issue_first_response_h` | Hours from opened to the first comment (a claim or a reply), per issue with a comment. |
| `pr_lead_h` | Hours from opened to merged, per merged non-data PR. |
| `pr_size_lines` | Additions plus deletions, per merged non-data PR (latest `max_pr_details`). |
| `pr_review_rounds` | Review rounds per merged non-data PR. |
| `ci_gate_duration_s` | Duration of every completed `ci_workflow` run fetched (up to 2000). |
| `nightly_duration_s` | Duration of every completed `nightly_workflow` run (up to 500). |
| `test_runtime_s` | Runtime of every test run in the summary. |
| `file_bytes`, `file_lines` | Size and non-blank lines of every counted file. |

## `commits`: the per-commit series

`--file` (default `<data_dir>/commits.jsonl`) holds one compact JSON object
per line, one line per commit on the first-parent history of `--ref`
(the nightly passes `origin/main`), oldest first. The x-axis of every trend
is this list: plot by `index`, label with `short` and `date`.

| Field | Value |
| --- | --- |
| `schema_version` | `1`. |
| `index` | Position on the first-parent history (0 = first commit). |
| `sha`, `short` | Full and 12-hex SHA. |
| `date` | Committer date, UTC. |
| `code` | The `collect` code statistics of that commit's tree: `total`, `kinds`, `ips`, `by_ext`, `by_dir` (same shapes). |
| `ci` | That commit's push `ci-gate` run on `main`: `{"status": "ok", conclusion, duration_s}` (the latest completed run), `{"status": "none"}` when it has none, `{"status": "unavailable"}` when the Actions API was not reachable, or `{"status": "n/a"}` for a data-only commit. |
| `data_only` | `true` for a nightly data commit: authored by `data_author` and changing only `data_dir/`. A merge by GitHub Actions auto-merge starts no push run, so these commits never have a `ci-gate` result; they stay on the axis (a nightly record may name one as its `meta.commit`) and renderers may skip them in CI charts. |

Each run appends missing commits (the first run, on an empty file, is the
one-time backfill), refreshes `ci` on lines that were `none` or
`unavailable` and now have a run, and leaves every other existing line
unchanged (`n/a` is final). Line counts are cached per blob, so a backfill reads each blob
once. `--no-ci` skips the API; `--limit N` adds at most `N` commits.

## `guard`

Lists the paths changed between the merge base of `--base` and `--head`
and `--head` (`git diff --name-only --no-renames`, so a rename shows both
paths). A branch that does not start with `branch_prefix` passes at once.
A data branch passes only when it changes at least one path and every path
is under `data_dir/`; otherwise it prints each offending path and exits 1.

## `red`

Keeps one open issue labelled `nightly-red`. `--run-url` must be
`https://github.com/OWNER/NAME/actions/runs/ID`.

| Outcome | Open `nightly-red` issues | Action |
| --- | --- | --- |
| `failure` | none | Open "Nightly run is red" with the run link, labelled `nightly-red`, `type:bug`, `area:ci`. |
| `failure` | one or more | Comment the run link on the lowest-numbered one. |
| `success` | any | Comment "green again" with the run link on each and close it (completed). |

PRs carrying the label are ignored.

## Workflows

**`nightly.yml`** runs at 03:17 UTC daily and on `workflow_dispatch`, never
on `pull_request`, on `[self-hosted, linux, x64]`. Its `base` input
(default `main`) is the data PR's base; any other base is for testing the
workflow and skips auto-merge and the `nightly-red` issue. The data branch
is `nightly/<date>` for `main` and `nightly/<date>-<base>` (slashes in the
base become `-`) for any other base, so a test dispatch never force-pushes
over the day's real data PR.

| Job | Permissions | Does |
| --- | --- | --- |
| `nightly` (480 min) | read: contents, issues, pull-requests, actions | `./build check`; `./build regress --level 0 --level 1 --level 2` (all IPs); `./build fpga --all` and the ASIC flow for IPs with `fpga/` or `asic/` (`--all`, else one `--ip` per IP); `./build metrics commits --ref origin/main` continuing the base branch's series, then `./build metrics collect` with the step outcomes; uploads both files. Every step runs even after a failure; a step whose subcommand is still a stub, or has no IP inputs, is `skipped`. |
| `publish` (15 min) | write: contents, pull-requests, actions | Checks out without persisted credentials and installs PyYAML; commits only the two data files, as `github-actions[bot]`, on the data branch from the base tip; runs `./build metrics guard` from this trusted checkout; force-pushes the branch with the token passed for that one command; closes, with a comment, any other open `nightly/*` PR into the same base (they would conflict on `commits.jsonl`); opens (or reuses) the PR labelled `type:data` and `area:wiki`; dispatches `ci.yml` (`ci-gate`, with the data guard) on the branch; on `main`, enables squash auto-merge and dispatches `pages.yml` on `main`. |
| `report` (5 min) | read contents, write issues | `./build metrics red` with `success` only when `nightly` and `publish` both succeeded. |

Pushes and PRs made with `GITHUB_TOKEN` start no runnable workflows (a
push starts none; the PR's `pull_request` runs are created as
`action_required` and wait for an approval nobody gives), so the data PR gets
its checks by `workflow_dispatch` on its branch. Branch protection does not
count the dispatched `ci-gate` check run toward the PR, even on the PR's
head commit (issue #53: PR blocked, `405 Required status check "ci-gate" is
expected`). The dispatched run's `ci-gate-status` job therefore posts the
gate's own result as a commit status with context `ci-gate` on the head
commit ([`./build ci status`](../ci/SPEC.md#build-ci-status)); a status from
GitHub Actions satisfies the required check, which is bound to that app, so
a green gate lets auto-merge proceed and a red or cancelled one keeps the PR
blocked. The PR then shows `unstable` (the waiting `pull_request` runs are
not required), which auto-merge accepts. GitHub has no REST call to enable
auto-merge, so `publish` uses `gh pr merge --auto --squash` (GraphQL, which
works with `GITHUB_TOKEN` on the runner). It needs the repository setting
**Allow auto-merge** and a required check on `main`; without them the step
warns and the PR waits for a maintainer. Opening the PR at all needs
**Settings > Actions > General > Allow GitHub Actions to create and approve
pull requests**; without it `publish` fails with that remedy and the pushed
branch's compare link, so the night is reported red.

A merge by GitHub Actions auto-merge is a push by `GITHUB_TOKEN`, which
starts no workflows: neither `pages.yml` nor a push `ci-gate` runs for the
data commit. `publish` therefore dispatches `pages.yml` on `main` (it may
run before the merge lands), and `pages.yml` also runs daily at 05:17 UTC,
two hours after the nightly, as the backstop. The series marks those
commits `data_only` with `ci` `n/a`.

**The data guard** is a step of the `ci-gate` job in `ci.yml` (the
[CI spec](../ci/SPEC.md#the-workflow) owns its invocation), so the single
required check also gates the data PR. On a pull request it compares the
merge commit with its first parent. On a push or dispatch of a `nightly/`
ref it compares the branch with the base of its open PR into `main` (else
of any open PR, else `main`), which is how the nightly's dispatched run
checks the data PR; any other push or dispatch passes at once, without an
API call or history fetch. Like every PR check, a PR can
edit its own copy of the workflow; the `publish` job's own guard run, from
the trusted checkout, is what keeps the bot honest. A superseded data PR
(for example one opened before a fix to this flow) is closed by the next
night's `publish`, which closes every other open `nightly/*` PR into the
same base.

A data PR changes only `wiki/tracker/data/**`, a docs-only change: on a
`pull_request` run `ci-gate` selects no IPs. The nightly's dispatched run is
a dispatch, so it runs every IP's L0+L1 (about the same few minutes as any
push to `main`).

## Consumers

The tracker pages (`wiki/tracker/`, rendered by `tools/wiki_site/tracker.py`)
read only `commits.jsonl` and the `<date>.json` records, keyed by
`schema_version`. They plot every trend against `commits.jsonl` order
(`index`) and place nightly records on that axis by `meta.commit`. The
commit label on charts and tables is the first 7 hex digits of `sha` (git's
default abbreviation); the 12-hex `short` and `commit_short` are for
unambiguous references in tables and links; a record whose commit is not in the series (a test run on
another branch) is ignored. Missing sections and `null` values are normal
and must render as gaps.

## Tests

`tools/metrics/tests/` runs without network or simulator, with a fake
`gh` client and a synthetic git repository with neutral IP names:
configuration loading and every malformed case, the glob matcher, line
counting, IP ownership, tree statistics and determinism
(`test_metrics_config.py`, `test_metrics_tree.py`); the series backfill,
incremental append, CI refresh and idempotence (`test_metrics_tree.py`);
every GitHub section, distribution and health value from fixtures, and
unavailable sources (`test_metrics_github.py`); the test, FPGA and ASIC
whitelists, the forbidden-pattern scan, and an end-to-end `collect` whose
inputs plant paths, emails and names that must not appear in the output
(`test_metrics_record.py`); the guard on real branches including renames
and a moving base (`test_metrics_guard.py`); and the red-issue rule
(`test_metrics_red.py`).
