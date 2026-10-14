# Metrics tracker

How the factory is doing over time: code, CI, the nightly run, tests, the
GitHub flow, and FPGA and ASIC results per IP. Every trend uses the same x
axis, the commits on `main` in order, labelled by short SHA, with the date
shown on hover. Each chart has a data table under it, and each IP has its own
[page](ip/index.md).

| Section | Shows | Updated by |
| --- | --- | --- |
| Repo health | Nightly status, `ci-gate` pass rate over the last 20 runs on `main`, open PRs and issues with their ages (stale: no activity for more than 7 days), median review rounds, share of merged PRs with an `APPROVE` verdict | Nightly |
| Code | Lines and files by kind and per IP; lines per file and file size; file-type distribution by extension and top-level directory | Per commit |
| CI | `ci-gate` duration and result per commit; duration histogram | Per commit |
| Nightly and tests | Nightly duration and result; tests, failures and runtime per level; test runtime histogram; slowest tests | Nightly |
| Issues and pull requests | Open items, merged PRs; histograms of issue time to close and to first claim, PR lead time, PR size and review rounds | Nightly |
| FPGA, ASIC | Per-IP resources, Fmax and slack ([FPGA BKM](../bkm/fpga.md#metrics)); WNS, TNS, area, cells, max fanout and logic depth ([PD BKM](../bkm/pd.md#metrics)) | Nightly |

**Cadence.** The nightly workflow appends one line per new `main` commit to
`data/commits.jsonl` and writes one record per night, `data/<date>.json`,
that names the `main` commit it ran on. It then opens a data pull request
that may touch only `wiki/tracker/data/`, which auto-merges after `ci-gate`
([DevOps BKM](../bkm/devops.md#nightly)). The site rebuilds on every push to
`main`, so the charts follow within a day.

**Data.** Records are JSON with a `schema_version`. Only the nightly bot
writes `data/`; never edit it by hand. Everything there is public, so it holds
counts and durations only, never names, emails or host details
([publishability](../bkm/wiki.md#publishability)). The record schema is in the
metrics tool SPEC; how this page reads it is in the
[wiki site SPEC](../tools/wiki_site/SPEC.md#tracker).
