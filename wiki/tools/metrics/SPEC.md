# Metrics tool: `./build metrics`

`./build metrics` writes the nightly metrics record and the per-commit
series under `wiki/tracker/data/`, guards the nightly data branch, and keeps
the single `nightly-red` issue. The [DevOps BKM](../../bkm/devops.md#metrics)
owns what is measured and when; this page owns the command line, the data
schemas, the publishability rules and the workflows. Code: `tools/metrics/`
(`config.py`, `tree.py`, `gh.py`, `github.py`, `record.py`, `commits.py`,
`guard.py`, `stage.py`, `red.py`, `trends.py`, `cli.py`); configuration: `cfg/metrics.yaml`.

```
./build metrics collect [--out FILE | --out-dir DIR] [--no-github] [--step NAME=pass|fail|skipped|paused]... [--started TIME] [--run-id ID] [--run-attempt N] [--repo OWNER/NAME]
./build metrics commits [--file FILE] [--ref REV] [--no-ci] [--limit N] [--repo OWNER/NAME]
./build metrics guard   --branch NAME (--base REV | --pr-base DEFAULT) [--head REV] [--select FILE] [--repo OWNER/NAME]
./build metrics stage   --base BRANCH --branch NAME --data DIR --worktree DIR --message TEXT [--out FILE] [--repo OWNER/NAME]
./build metrics trends  [--dir DIR]...
./build metrics red     --outcome success|failure --run-url URL [--head SHA] [--branch NAME]
                        [--step NAME=pass|fail|skipped|paused]... [--summary FILE] [--repo OWNER/NAME]
```

The shared `--src-root` and `--workdir` options work as in the
[build spec](../build/SPEC.md#command-line). `--repo` defaults to
`$GH_REPO`, then `$GITHUB_REPOSITORY`, then the `origin` remote. GitHub is
reached only through `gh api` (REST, never GraphQL), authenticated by
`gh`'s own login or `$GH_TOKEN`.

Exit codes: `0` success; `1` the guard failed; `2` a configuration, git,
GitHub (for `red`, `stage` and `guard --pr-base`) or publishability error,
or a refused staging.

## Configuration: `cfg/metrics.yaml`

| Key | Meaning |
| --- | --- |
| `data_dir` | Where records are committed (`wiki/tracker/data`); the only path a data branch may change. |
| `branch_prefix` | Head-branch prefix of data PRs (`nightly/`). |
| `data_author` | Author email of the nightly data commits (the GitHub Actions bot). |
| `src_root` | Source root in each commit's tree, for IP discovery (`src`). |
| `exclude` | Globs left out of every code statistic (the data directory). |
| `kinds` | Kind name to globs: `rtl_sv`, `dv_sv`, `dv_py`, `arch_py` (architecture models and their self-tests), `tools_py`, `wiki_md`, `config`, `workflows`, `skills`. A file may match several kinds. |
| `line_comments`, `block_c` | Per extension, line-comment prefixes, and the extensions with `/* */` blocks, for code lines. |
| `windows` | Trailing windows in days (`[1, 7, 30]`). |
| `review_marker` | The review-verdict comment marker (`REVIEW-VERDICT`). |
| `ci_workflow`, `nightly_workflow` | Workflow files whose runs feed `ci`, the series and the nightly duration; `red` finds the last green nightly among `nightly_workflow`'s runs. |
| `stale_days`, `health_ci_runs` | Health: stale-issue age, and how many latest main runs the pass rate covers. |
| `max_pr_details` | How many most recently merged PRs are fetched one by one for their size. |
| `slowest` | Number of slowest test runs kept. |
| `fpga_fields`, `asic_fields` | The whitelist of dotted keys copied from each IP's flow `metrics.json`; `*` matches one key. |
| `drop_alerts` | Per [trend series](#trends), the drop (in the series' unit) beyond which it alerts: exactly the eight series keys, each a non-negative number. |

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
  runtime (no log paths, failure details or source root). Architecture
  perf results keep only statuses, scenario names and the target metrics'
  numeric values (no descriptions, workloads, error details or model file).
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

Writes one record per run: `--out`, else `<run name>` in `--out-dir`
(default `<workdir>/metrics/`); the nightly passes `--out-dir` with its data
directory. The **run name** is `<date>T<HHMMSS>Z.json`, the UTC time of
`--started` (else of the collection), so every run, including a same-day
rerun or a re-run attempt, gets its own file and the names sort in run order.
Records written before #322 were named `<date>.json`, one per UTC date, so a
same-day rerun overwrote the earlier run; they still load
([trends](#trends)), and the merged ones are restored per run
([restored records](#restored-records)). JSON with sorted keys and one-space
indent. Code statistics come from the **committed tree at `HEAD`** (`git
ls-tree` and `git cat-file`), never from the working tree. A section whose
producer is absent is `{"status": "unavailable", "reason": R}` with `R` one
of `missing` (no input file), `invalid` (unreadable input), `disabled`
(`--no-github`), `forbidden` (HTTP 401/403), `not-found` (404), `gh-error`;
present sections carry `"status": "ok"`. Missing producers never fail the
command. `--step` records the nightly's step outcomes, `--started` its
start time, and `--run-id` and `--run-attempt` (positive integers) the
workflow run (`$GITHUB_RUN_ID`, `$GITHUB_RUN_ATTEMPT`).

Top level: `schema_version` (integer, `1`; bumped on any incompatible
change), then the sections below.

### `meta`

| Field | Value |
| --- | --- |
| `schema_version` | Same as the top level. |
| `generated_at` | UTC time of the collection, `YYYY-MM-DDTHH:MM:SSZ`; one per run, so it identifies the run. |
| `started_at` | `--started` in UTC, same form (the nightly job's start; the run name's time); `null` without it. |
| `run_id`, `run_attempt` | `--run-id` and `--run-attempt`: the workflow run and its attempt (a re-run attempt is another run, with its own start and record); `null` without them. |
| `date` | UTC date of `generated_at`. |
| `restored_from` | Only on a [restored record](#restored-records): `{commit, pr, file}`, the merge commit and data PR it came from and its file name there. |
| `commit`, `commit_short`, `commit_date` | `HEAD` SHA (40 and 12 hex) and its committer date (UTC); on the scheduled run, the main commit the night ran on: the x-axis key shared with the series. |
| `tools` | `python`, `git`, `gh`, `verilator`, `cocotb`, `pytest`, `pyyaml`: version strings only, `null` when absent. |
| `nightly.steps` | `{name: pass\|fail\|skipped\|paused}` from `--step` (the nightly passes `check`, `unit`, `regress`, `fpga`, `asic-synth` (the synthesis-only check), `asic` (the full flow); arch perf runs inside `unit`). `paused`: a pause switch turned the step off ([ASIC pause](../../bkm/devops.md#asic-pause)). |
| `nightly.result` | `fail` if any step failed, else `pass` (`skipped` and `paused` are not failures); `null` without steps. |
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
| `ips.<ip>` | Bucket of the files the IP owns (its folders `src/<kind>/<ip>/` under every kind), plus `kinds` with the non-empty kinds. IPs are discovered in the tree as in the [build spec](../build/SPEC.md#ip-discovery): `src/rtl/<id>/<id>.rtl.f`, `rtl/common/` excluded. |
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
Extra rotated seeds (rows with `extra` true,
[seed rotation](../regress/SPEC.md#seed-rotation)) are left out of
`totals`, `ips`, `slowest` and `runs`, and the builds only they need (summary
builds with `extra` true) out of `builds`, so the series compare the pinned
seeds night to night. Two fields still include them: `passed`, which a
failing extra run or extra-only build makes false (`red` then lists the
failed runs, an extra-only build failure as its runs' `error`), and
`wall_s`, the wall time of the whole regress run, extra runs included.

| Field | Value |
| --- | --- |
| `levels`, `passed`, `wall_s` | As in the summary. |
| `totals.level<N>` | `tests`, `passed`, `failed`, `runtime_s`, `budget_s`, `over_budget`. |
| `builds` | `{total, failed}`. |
| `ips.<ip>.level<N>` | `{tests, passed, failed, runtime_s}`. |
| `slowest` | The `slowest` longest runs, each `{ip, level, name, seed, status, runtime_s}`. |
| `runs` | Every run in that form, sorted by IP, level, name, seed: the seeds record. |

### `coverage`

From the `coverage` object of the same summary, present when the nightly
ran `regress --coverage` ([regress spec](../regress/SPEC.md#coverage));
`{"status": "unavailable", "reason": "disabled"}` when the summary has none.

| Field | Value |
| --- | --- |
| `passed` | False when any IP missed a coverage floor. |
| `ips.<ip>.line`, `.toggle`, `.functional` | `{covered, total, pct}`; `pct` is `null` when the metric has no points. |
| `ips.<ip>.floors` | The floors (percent) that applied, by metric; `{}` for none. |
| `ips.<ip>.floors_passed` | Whether the IP met all of them. |

Bin names, failure messages and paths are dropped.

### `fpga`, `asic`

`{status, ips: {<ip>: {status, ...}}}` from `<workdir>/<ip>/fpga/metrics.json`
and `<workdir>/<ip>/asic/metrics.json` of every discovered IP that has one,
reduced to `fpga_fields` / `asic_fields`. The owning schemas are in the
FPGA and ASIC tool specs: the whitelist keeps device, resources
(`used`/`total`), per-clock Fmax and slacks, worst slacks, `met`, critical
path slack and delay, step status, runtime and warnings (FPGA); and
design, platform, clock period, status, WNS/TNS/hold slack, per-clock
setup slack, WNS and TNS, areas, cells,
utilization, max fanout and logic depth values, worst-path slack, cells and
group, power, DRC errors and elapsed time (ASIC). Net, pin and path names,
tool paths, images and work directories are dropped. An IP whose file does
not parse is `{"status": "invalid"}`. When the step of the same name is
`paused` (`--step asic=paused`), the section is `{"status": "paused"}` and no
file is read, so files an earlier run left in the workdir never pass for
tonight's.

### `arch`

`{status, ips: {<ip>: {...}}}` from `<workdir>/<ip>/arch/perf.json`
([arch spec](../arch/SPEC.md#behavior-and-output)) of every discovered IP
that has one; `{"status": "unavailable", "reason": "missing"}` when none
has (an IP without `src/arch/<ip>/`, or a night that did not run
`./build arch`, is absent, not a failure). Each IP keeps only:

| Field | Value |
| --- | --- |
| `status` | The file's `pass` or `fail`. |
| `scenarios.<name>.status` | `pass`, `fail` or `error`. |
| `scenarios.<name>.targets.<metric>` | The value of each metric the scenario's targets name (a finite number). |

Bounds, other metrics, descriptions, seeds, workloads, `detail`, the model
file name and any unknown field are dropped. A file that does not parse, or
whose status, scenario name (`[a-z0-9][a-z0-9_]*`), metric name
(`[A-Za-z0-9_]+`) or value breaks these rules, makes that IP
`{"status": "invalid"}`. A number too large for a float in this file, or
in the regression summary, marks that input `invalid` too; it never fails
the command.

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

<a id="restored-records"></a>
### Restored records

Before per-run names, a same-day rerun overwrote the day's `<date>.json`, so
the tree kept only each day's last run. Every merged version of those files
is in git history, one per data PR; each was restored once (#322) as its own
per-run file, its measured content unchanged, with `meta` gaining:
`started_at`, `generated_at` less `meta.nightly.duration_s` (the collector
computed that duration from `--started`, so this is the job start it was
given); `run_id` and `run_attempt`, of the run whose `publish` job pushed
the merged head; and `restored_from`. That run is the `nightly.yml` run
(Actions API) whose `head_sha` is the record's `meta.commit`, and whose
`publish` job, in that attempt, succeeded around the data PR's head commit;
the `ci.yml` run dispatched on that head confirms it. The run link in the
data PR's body is not used: it names the run that first opened the PR, and
a later run that force-pushes onto the open PR keeps that link (#52 names an
earlier run than the one whose push merged). The `<date>.json` files were
then removed: each was the latest version of its date, restored like the
others. Runs whose data PR closed unmerged left no record and cannot be
restored.

| File | `restored_from` (merge commit, data PR, file) | Run, attempt |
| --- | --- | --- |
| `2026-10-14T200319Z.json` | `6b84433`, #52, `2026-10-14.json` | 38628703968, 1 |
| `2026-10-14T211537Z.json` | `bb52576`, #97, `2026-10-14.json` | 38638803579, 1 |
| `2026-10-15T081643Z.json` | `7f08df0`, #155, `2026-10-15.json` | 38704349529, 1 |
| `2026-10-15T085635Z.json` | `52cc15b`, #161, `2026-10-15.json` | 38708338857, 2 |
| `2026-10-15T183026Z.json` | `2658e30`, #224, `2026-10-15.json` | 38786155881, 1 |
| `2026-10-16T062658Z.json` | `eb5f0ae`, #239, `2026-10-16.json` | 38861246114, 1 |
| `2026-10-16T072045Z.json` | `ed166e7`, #242, `2026-10-16.json` | 38866601042, 1 |
| `2026-10-16T080752Z.json` | `79df5ff`, #247, `2026-10-16.json` | 38871634746, 1 |
| `2026-10-17T074525Z.json` | `8000066`, #266, `2026-10-17.json` | 39014812287, 1 |

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
once. Existing lines and nightly records are never rewritten (the one-time
restoration only added `meta` keys, [restored records](#restored-records)): IPs are keyed
by the id that was current when the line or record was written, so when an
IP id changes (a layout move or a rename) its history before the change
stays under the old id and the new id starts at the change. A fresh backfill
discovers IPs with the current layout rules only. `--no-ci` skips the API; `--limit N` adds at most `N` commits.

## `trends`

Per-IP nightly trend series and their drop alerts. `tools/metrics/trends.py`
owns the series and the rule (standard library only); the
[tracker](../wiki_site/SPEC.md#tracker) imports it, so the site and the
nightly summary flag the same drops. Every series is higher-is-better:

| Key | Series | Unit | Record path, per IP |
| --- | --- | --- | --- |
| `coverage_line`, `coverage_toggle`, `coverage_functional` | Merged regression coverage | % | `coverage.ips.<ip>.<metric>.pct` |
| `mutant_score` | Mutant score | % | `mutants.ips.<ip>.score_pct`, else `100 * killed / total` |
| `fpga_fmax` | FPGA Fmax, worst clock | MHz | `fpga.ips.<ip>.fmax_mhz`, else the lowest `fpga.ips.<ip>.timing.clocks.*.fmax_mhz` |
| `fpga_setup_slack` | FPGA worst setup slack | ns | `fpga.ips.<ip>.timing.worst_setup_slack_ns`, else `.setup_slack_ns` |
| `asic_wns`, `asic_tns` | ASIC WNS, TNS | ns | `asic.ips.<ip>.timing.wns_ns`, else `.wns_ns`; `.timing.tns_ns`, else `.tns_ns` |

The flat keys after "else" are the legacy forms the tracker charts also
read, in the same order, so a series alerts on exactly the values its chart
plots. Higher is better for slack too: a WNS going from −1.0 ns to −1.5 ns is
a 0.5 ns drop.

`mutants` is an optional record section that no nightly step writes yet;
the series is read where a record carries it. Values must be finite numbers;
anything else (a missing section, `unavailable`, `null`, a string, a
boolean) is a gap.

**Rule.** Over the nightly records in order, a series *drops* at a record
when its value there is lower than its previous recorded value (the nearest
earlier record carrying the series) by more than its `drop_alerts`
threshold; a drop equal to the threshold does not count. A gap neither
drops nor resets the comparison, so a drop across a gap still counts. The
drops at the latest record are the **alerts**; earlier drops are history.
An IP absent from the latest record (retired) therefore never alerts, but
its history stays.

**Run order.** The history is one record per nightly run, in run order: by
the run time, `meta.started_at`, else `meta.generated_at` (a record with
neither sorts at the start of its date, `meta.date` else the file stem's
`YYYY-MM-DD`), then by file name. So two runs on one day are two
consecutive records, and "previous" is the previous run, not the previous
date. A run is identified by its `meta.generated_at` (else its file name):
two files of one run (a restored record beside its old date file, or
tonight's file passed again by a later `--dir`) are one record. A run's
label is its run time as `YYYY-MM-DD HH:MM` (UTC), else its date.
`trends.py` owns these rules (`run_order`, `run_identity`, `run_label`) and
the tracker uses them.

**`./build metrics trends`** reads every `*.json` record in each `--dir`
(default `data_dir`), old `<date>.json` and per-run names alike, one per run
in run order; a later `--dir` wins on the same run, and an unreadable file,
a schema other than `1` or a record without a date is skipped with a note
on stderr. It prints Markdown to stdout: a `## Trend drop alerts` heading,
the latest run's label and commit and the number of runs, and a table of
the alerts (IP, series, previous value and its run's label, latest value,
change, threshold), or a line saying no series dropped or no records were
found. Alerts never fail the command; it exits 2
only on a configuration error.

**The nightly step has no `continue-on-error`** (a deliberate choice):
alerts never fail the command, so a failure means a broken tool or
configuration, and the night is reported red for it. The data upload and
`publish` do not depend on this step, so tonight's record is still
published.

**The Fmax threshold** (`fpga_fmax`, 2 MHz absolute) was checked against
the first week of history on 2026-10-20 (15 runs from 2026-10-14): Quartus
repeats its result exactly on the same commit, so every change seen came
from a design change (−0.43, −0.20, +1.41 and +5.91 MHz). The worst clock of
every IP is its slow core clock, between 50 and 57 MHz, where 2 MHz is 3.5 to
4 %, so a relative threshold would act the same way for now and the
threshold stays absolute. The re-check on two weeks of history (from
2026-10-28) is owned by #441.

## `guard`

Lists the paths changed between the merge base of `--base` and `--head`
and `--head` (`git diff --name-status --no-renames`, so a rename shows both
paths). A branch that does not start with `branch_prefix` passes at once.
A data branch passes only when it changes at least one path, every path is
under `data_dir/`, and every change is one of these; otherwise it prints
each offending path and exits 1:

| Path in `data_dir/` | Allowed change |
| --- | --- |
| `<date>T<HHMMSS>Z.json` (a run name of `collect`, directly in `data_dir/`) | Added (never modified or deleted), a JSON object with `schema_version` `1` and `meta.generated_at` a UTC `YYYY-MM-DDTHH:MM:SSZ`. |
| `commits.jsonl` | Every line a JSON object with a string `sha`, no `sha` twice, and every `sha` of the merge base's file still present. |
| Anything else, or any deletion | Never. |

So a data branch can only add records and grow the series: it never loses
or rewrites data, and a malformed file fails the guard (publishability is
`./build check`'s, which runs beside it in `ci-gate`).

`--pr-base DEFAULT` replaces `--base` in CI: for a data branch it looks up
the branch's open PRs (`GET repos/{repo}/pulls?state=open&head={owner}:{branch}`),
takes the base of the one into `DEFAULT`, else of any, else `DEFAULT`,
fetches the full history (`git fetch --unshallow`, best effort) and that
branch, and guards against `origin/<base>`; any other branch passes at once
with no API call or fetch. `--select FILE`, when a data branch passes,
appends `AFFECTED=--affected <merge base SHA>` to `FILE` (the job's
`$GITHUB_ENV`); it writes nothing for another branch or a failure. With that
selection `./build ci unit`, `./build regress` and `./build ci vendor` see
only data files changed and select nothing
([data PR CI](#workflows)).

## `stage`

Builds the data branch for one nightly run in `--worktree` and commits it,
so that no run's record is lost when runs publish faster than their data
PRs merge. The `publish` job runs it; it needs PyYAML and `gh` only.

1. Lists the open PRs into `--base` whose head is a `branch_prefix` branch
   of this repository (`GET repos/{repo}/pulls?state=open&base=`; a fork's
   head is ignored), fetches the base and each head, and runs the guard on
   each head against the base. One that fails stops staging (exit 2): a
   person must fix or close it.
2. **Continue or start.** With open data PRs, the newest is continued: the
   worktree starts at its head, so this run's commit lands on top of the
   earlier runs' records (`mode=continue`). Without one, `--branch` starts
   at the base tip (`mode=new`).
3. **Carry.** Every other open data PR's added records and its series are
   copied in; the caller closes those PRs once the branch is pushed
   (`carried`).
4. Copies this run's files from `--data`: each record (a run name only),
   and `commits.jsonl` merged by commit. **The series merge** keeps one line
   per `sha` from the branch's file, the carried ones and this run's, a later
   source winning on the same `sha` (this run's lines are the newest), in
   `index` order, then by date. Main's first-parent history only grows, so
   nothing is lost and nothing is duplicated.
5. A record already on the branch under the same name must be identical
   (exit 2 otherwise). With no change, nothing is committed
   (`changed=false`); otherwise it commits with `--message`, with the
   identity of the environment (the workflow sets the `data_author` bot).

It prints, and with `--out` writes, `KEY=VALUE` lines: `branch`, `mode`
(`new` or `continue`), `number` (the continued PR), `carried` (PR numbers,
space-separated), `changed` (`true` or `false`) and `sha` (the commit).
Branch names must match `[A-Za-z0-9._/-]+` without `..` or a leading `-`.

## `red`

Keeps one open issue labelled `nightly-red`. `--run-url` must be
`https://github.com/OWNER/NAME/actions/runs/ID`.

| Outcome | Open `nightly-red` issues | Action |
| --- | --- | --- |
| `failure` | none | Open "Nightly run is red" with the run link and the failure details, labelled `nightly-red`, `type:bug`, `area:ci`. |
| `failure` | one or more | Comment the run link and the failure details on the lowest-numbered one. |
| `success` | any | Comment "green again" with the run link on each and close it (completed). |

PRs carrying the label are ignored.

**Failure details.** A full-L2 failure, a coverage-floor miss or a failing
[rotated extra seed](../regress/SPEC.md#seed-rotation) fails the nightly's
`regress` step (the floors are checked in that run), so the issue names
what changed since the nightly last held and what failed:

- with `--step` (the nightly passes every step's outcome): `Failed steps:`
  the steps whose outcome is `fail`;
- with `--head` (the commit the nightly ran on, a full 40-character SHA):
  the newest successful run of `nightly_workflow` on `--branch` (default
  `main`; `GET repos/{repo}/actions/workflows/{file}/runs?branch=&status=success&per_page=1`)
  is the last green nightly, and `GET repos/{repo}/compare/{green}...{head}`
  lists the commits since it, oldest first: the count, each commit's
  12-character SHA and subject line (the last 50 when there are more, then
  `... <n> more`) and a compare link. With no green run on record, or none
  newer than the last green commit, the issue says so (the second points to
  a flaky test, host or tool). A failed lookup becomes a line saying the
  commits could not be listed: the issue still opens or updates.
- with `--summary` (the run's `./build regress` `summary.json`):
  `Failed regression runs: <n>`, then one line per run whose status is not
  `pass` (the first 30, then `... <n> more`), each with its level, IP, test,
  exact seed and status, `extra seed of rotation <key>` for an extra
  rotated seed, and the command that reproduces it,
  `./build sim --ip <ip> --test <name> --seed <seed>`. Only those names,
  seeds and statuses are copied (names must match the regression-list name
  rule), never a log, path or detail message. A missing summary (regress
  never ran) or an unreadable one becomes a line saying the runs are not
  listed; a summary with no failed run adds nothing.

`--head` must be a full lower-case SHA and `--branch` a plain branch name;
otherwise `red` exits 2 before any write.

## Workflows

**`nightly.yml`** runs at 03:17 UTC daily and on `workflow_dispatch`, never
on `pull_request`. Every job runs on `[self-hosted, linux, x64, wsl]`, the
WSL host's runners only
([DevOps BKM, Nightly](../../bkm/devops.md#nightly)). Its `base` input
(default `main`) is the data PR's base; any other base is for testing the
workflow and skips auto-merge and the `nightly-red` issue. The data branch
is `nightly/<date>` for `main` and `nightly/<date>-<base>` (slashes in the
base become `-`) for any other base, so a test dispatch never touches the
day's real data PR. Those are the names of a new branch; **no record is
lost** when runs publish faster than their PRs merge: while a data PR into
the base is open, the next run commits its record on top of that PR's head
([`stage`](#stage)), and any other open data PR is carried in and closed.
Runs never overlap (the workflow's concurrency group queues them), and a
push that loses the race with the open PR's auto-merge (the PR merged at the
older head, or the branch moved) stages again on the new base, up to three
attempts, before the job fails.

| Job | Permissions | Does |
| --- | --- | --- |
| `nightly` (480 min) | read: contents, issues, pull-requests, actions | `./build check`; `./build ci unit` (every suite, including `./build arch --ip <ip>` for each IP with `src/arch/<ip>/perf/`, the only arch perf run); `./build regress --level 0 --level 1 --level 2 --coverage --rotate <date>` (all IPs, with the date's [rotated extra seeds](../regress/SPEC.md#seed-rotation)); `./build fpga --all`; `./build ci vendor --kind asic` (the synthesis-only check, step `asic-synth`, every IP with `src/asic/<ip>/`, its summaries uploaded as `asic-synth-summary`); the full ASIC flow for IPs with `src/asic/<ip>/` (`--all`, else one `--ip` per IP; `paused`, with no flow run, while the workflow env `ASIC_PAUSED` is `"true"`, [ASIC pause](../../bkm/devops.md#asic-pause)); `./build metrics commits --ref origin/main` continuing the base branch's series, then `./build metrics collect --out-dir` with the step outcomes, the start time and the run id and attempt (this run's own `<date>T<HHMMSS>Z.json`); `./build metrics trends --dir wiki/tracker/data --dir <tonight's data>` into the job summary (the nightly summary); uploads both files. Every step runs even after a failure; a step whose subcommand is still a stub, or has no IP inputs, is `skipped`. |
| `publish` (15 min) | write: contents, pull-requests, actions | Checks out without persisted credentials and installs PyYAML; `./build metrics stage` commits only the data files (this run's record, a new file beside the earlier runs', and the merged `commits.jsonl`), as `github-actions[bot]`, on top of the open data PR's head, else on a new branch from the base tip; runs `./build metrics guard` from this trusted checkout; pushes with the token passed for that one command (fast-forward for a continued branch, forced for a new one) and, for a continued PR, checks it is still open at that commit, else stages again; closes, with a comment, each carried data PR; comments the run on a continued PR, or opens the PR, labelled `type:data` and `area:wiki`; dispatches `ci.yml` (`ci-gate`, with the data guard) on the branch; on `main`, enables squash auto-merge and dispatches `pages.yml` on `main`. |
| `report` (5 min) | read contents and actions, write issues | Downloads the run's `regress-summary` artifact (a missing one is not an error); `./build metrics red` with `success` only when `nightly` and `publish` both succeeded, `--head` the run's commit, `--step` each nightly step's outcome (the `nightly` job's outputs; a missing one is `fail`) and `--summary` that summary. |

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
blocked. The PR's own `pull_request` runs of `ci.yml` and `pages.yml` stay
waiting; they are not required and did not hold auto-merge back on the
first nightly after #61 (see **Evidence** below). GitHub has no REST call to enable
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

**Evidence: the first data PR merged this way** (read back through the
REST API). Nightly run
[38628703968](https://github.com/amichai-bd/pcie-factory/actions/runs/38628703968),
on `main` at `c477275` (the #61 merge), force-pushed `nightly/2026-10-14` to
`cf8e30f` at 20:07:05 UTC and reused data PR
[#52](https://github.com/amichai-bd/pcie-factory/pull/52), whose squash
auto-merge was already enabled. Its dispatched `ci.yml` run
[38629345324](https://github.com/amichai-bd/pcie-factory/actions/runs/38629345324)
passed `ci-gate` at 20:08:22, and `ci-gate-status` posted the `ci-gate`
commit status `success` at 20:08:36. Auto-merge squash-merged #52 at
20:09:28 as [`6b84433`](https://github.com/amichai-bd/pcie-factory/commit/6b844332ef7840dad36a8c0b6ca662370d849d64).
The PR's `pull_request` runs of `ci.yml`
([38629348666](https://github.com/amichai-bd/pcie-factory/actions/runs/38629348666))
and `pages.yml`
([38629348539](https://github.com/amichai-bd/pcie-factory/actions/runs/38629348539)),
created at 20:07:09, never started a job and completed as `failure` with
zero jobs at 20:09:29, after the PR closed. So auto-merge took the commit
status as the required `ci-gate` and merged while those non-required runs
were pending. GitHub does not keep a PR's pre-merge `mergeable_state` (it
reads `unknown` after the merge), so whether it showed `unstable` then is
not recorded; the merge is the evidence. The merge commit has no push
`ci-gate` run, as expected.

**The data guard** is a step of the `ci-gate` job in `ci.yml` (the
[CI spec](../ci/SPEC.md#the-workflow) owns its invocation), so the single
required check also gates the data PR. On a pull request it compares the
merge commit with its first parent. On a dispatch on a `nightly/` ref (push
triggers only on `main`) it compares the branch with the base of its open PR
into `main` (else of any open PR, else `main`), which is how the nightly's dispatched run
checks the data PR; any other push or dispatch passes at once, without an
API call or history fetch. Like every PR check, a PR can
edit its own copy of the workflow; the `publish` job's own guard run, from
the trusted checkout, is what keeps the bot honest. A superseded data PR
(for example one opened before a fix to this flow) is closed by the next
night's `publish`, which carries its records and closes it.

**Data PR CI** runs what a data-only change needs. The dispatched run's
guard step runs `./build metrics guard --pr-base main --select`: once the
guard has proved, against the PR's real base and from the merge base, that
the branch changes only valid data files, the run's unit, regression and
FPGA steps get `--affected <merge base>`, so they select no suite, IP or
target, just as a docs-only pull request. `./build check` (publishability
of the data among the rest) and the strict wiki build (the tracker
rendered from the new data) still run, and the data guard validates every
record and the series. The gate result is the run's own, posted on the
tested commit by `ci-gate-status`; neither the preceding nightly's result
nor the dispatch itself counts. A guard failure (a mixed change, a
malformed or deleted file) fails `ci-gate` and leaves the selection at
everything. Every other push, dispatch and pull request keeps its usual
selection ([CI spec](../ci/SPEC.md#the-workflow)). Measured before this
path, the gate of a data PR took 7m10s of runner time (units 3m13s,
regression 3m09s) and 8m10s end to end
([run 38713182485](https://github.com/amichai-bd/pcie-factory/actions/runs/38713182485)).

## Consumers

The tracker pages (`wiki/tracker/`, rendered by `tools/wiki_site/tracker.py`)
read only `commits.jsonl` and the nightly records (`<date>T<HHMMSS>Z.json`,
and older `<date>.json`), keyed by `schema_version`. They plot the
commit-series trends against `commits.jsonl` order (`index`) and the nightly
trends against every nightly run in [run order](#trends), each run naming
its commit by `meta.commit`. The
commit label on charts and tables is the first 7 hex digits of `sha` (git's
default abbreviation); the 12-hex `short` and `commit_short` are for
unambiguous references in tables and links; a record whose commit is not in the series (a test run on
another branch) is ignored. Missing sections and `null` values are normal
and must render as gaps.

## Tests

`tools/metrics/tests/` runs without network or simulator, with a fake
`gh` client and a synthetic git repository with neutral IP names:
configuration loading and every malformed case, the glob matcher, line
counting, kind-first IP discovery and ownership (the superseded IP-first
form is not an IP), tree statistics and determinism
(`test_metrics_config.py`, `test_metrics_tree.py`); the series backfill,
incremental append, CI refresh and idempotence (`test_metrics_tree.py`);
every GitHub section, distribution and health value from fixtures, and
unavailable sources (`test_metrics_github.py`); the test, FPGA and ASIC
whitelists, a paused flow section and step, the nightly's `ASIC_PAUSED`
wiring, the coverage section, the arch section (whitelist, missing and
invalid files), the `arch_py` kind, the forbidden-pattern scan, and an end-to-end `collect` whose
inputs plant paths, emails and names that must not appear in the output
(`test_metrics_record.py`); the guard on real branches including renames
and a moving base, each malformed data file (bad JSON, wrong schema, no
`generated_at`, a non-run name, a subfolder, a series line without `sha`,
a duplicate `sha`), a deleted or rewritten record and a lost series line,
`--select` writing the merge base only for a passing data branch (not for
another branch or a mixed change), and `--pr-base` resolution, fetch and
the no-API pass of other branches (`test_metrics_guard.py`); staging
(`test_metrics_stage.py`): a new branch from the base tip, two runs back to
back where the second commits on the open PR's head and both records and
one line per commit remain, another open data PR carried, a fork's PR
ignored, an open PR failing the guard, a differing record refused, nothing
to publish, the series merge, bad branch names and the `--out` lines; and the red-issue rule
(`test_metrics_red.py`: open, update and close, and on failure the failed
steps, the commits since the last green nightly, the cap on long ranges,
no green run, no new commits, a failed lookup, bad `--head` or
`--branch`, and the failed runs of `--summary` with their exact seeds and
`./build sim` commands, extra rotated seeds marked, capped, no log or path
copied, and a missing or unreadable summary); extra rotated seeds left out
of the `tests` section (`test_metrics_record.py`); the trend series and drop rule on a fixture
history (`fixtures/trends/`) with a drop that alerts across a gap, a flat
series, a drop equal to its threshold, a rise, a gap at the latest record,
retired and new IPs, malformed and missing values, a negative series
(WNS −1.0 → −1.5 alerts, −1.75 → −1.0 does not), the legacy flat keys read
like the tracker (`tools/wiki_site/tests/test_tracker.py` also checks that
both read the same value from one record), and the `trends`
summary, its `--dir` precedence and skipped files; run order: three runs on
one day after a date-named record, consecutive and alerting between the
day's last two runs, order by start time over file names with the
`generated_at` and date fallbacks, and one run in two files read once
(`test_metrics_trends.py`); one record per run from `collect`, two same-day
runs in two files with their start, run id and attempt, and the `--run-id`,
`--run-attempt` and `--out`/`--out-dir` arguments
(`test_metrics_record.py`). Each rule is pinned by a mutant the suite
catches (recorded in the delivering PR).
