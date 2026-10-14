# Wiki site generator

`tools/wiki_site/wiki_site.py` turns `wiki/`, plus the repository's
`README.md`, `CLAUDE.md` and skills, into a static HTML site that GitHub Pages
publishes, including the [metrics tracker](#tracker). It is small on
purpose: no nav config, no framework and no Node. The theme, Mermaid and the
chart library are served from the site itself; the only view-time fetches
are images a page links by URL, such as the README's status badges.

## Usage

```sh
./build wiki build [--strict] [--out DIR] [--sha SHA] [--repo-url URL]
./build wiki serve [--out DIR] [--port 8000]
```

`./build wiki` forwards to `tools/wiki_site/wiki_site.py`, which takes the
same arguments and can be run directly. Through `./build`, `--out` defaults to
`<workdir>/wiki-site`, so `./build wiki --workdir DIR build` and
`$BUILD_WORKDIR` move it. `./build wiki` exits 2 if the dependencies below are
not installed; other `./build` commands do not need them.

| Option | Default | Meaning |
| --- | --- | --- |
| `--out` | `workdir/wiki-site` | Output directory. `build` deletes and rewrites it. |
| `--strict` | off | Exit 1 if any problem is found; otherwise problems print as `WARN`. |
| `--sha` | `git rev-parse HEAD` | Commit used in links to files the site does not publish. Exit 2 if it can't be resolved. |
| `--repo-url` | `https://github.com/amichai-bd/pcie-factory` | Repository used in links to files the site does not publish, and in the navbar link. |
| `--port` | `8000` | `serve` binds to `127.0.0.1` on this port. |

`build` prints one summary line, `PASS:` or `FAIL:`, followed by the page,
asset and link counts and the problem count. `serve` serves an existing build
with Python's `http.server`. It exits 2 if the build directory has no
`index.html`.

Dependencies are `markdown-it-py` and `Pygments`, pinned in
`tools/requirements.txt` (`pip install -r tools/requirements.txt`).

## Inputs and outputs

| Source under `wiki/` | Output under `--out` |
| --- | --- |
| `<dir>/index.md` or `<dir>/README.md` | `<dir>/index.html` (the folder's landing page) |
| `<path>.md` | `<path>.html` |
| any other file | copied byte for byte to the same path |
| `README.md` (repository root) | `index.html`, the home page |
| `CLAUDE.md` (repository root) | `agents.html` |
| `.claude/skills/<name>/SKILL.md` | `skills/<name>.html` |
| (theme) | `assets/wiki.css`, `wiki.js`, `tracker.js`, `mermaid.min.js`, `mermaid-LICENSE.txt`, `chart.umd.js`, `chart-LICENSE.md` |
| (tracker) | `tracker/index.html` gains the rendered tracker; `tracker/ip/index.html` and `tracker/ip/<id>/index.html` are generated ([Tracker](#tracker)) |
| (generated) | `assets/search-index.json` |

The theme and generated paths under `assets/` are reserved: a wiki source that
would publish to one of them is a problem (see [Links](#links)).

Names under `wiki/` that start with `.` are skipped, at any depth.

The home page is the repository's root `README.md`. Then:

- `CLAUDE.md`, if present, publishes to `agents.html`. Both pages open with a
  `README.md` / `CLAUDE.md` toggle that links to the other.
- `wiki/index.md` (or `wiki/README.md`) publishes to `wiki.html` instead of
  `index.html`. Every other wiki path is unchanged.
- Each `.claude/skills/<name>/SKILL.md` is published. Its YAML frontmatter
  (between `---` lines at the top, possibly empty) is not rendered. Its
  `description` (a single line, indented continuation lines, or a `>` or `|`
  block) is shown as a quote under the first H1 outside a code fence, or at
  the top if there is none. The text is HTML-escaped and shown literally, not
  parsed as Markdown.

Without a root `README.md`, `wiki/index.md` (or `wiki/README.md`) is the home
page at `index.html`, and `CLAUDE.md` and skills are not published.

The home page's first H1 is the site name in the navbar. The navbar also links
to `--repo-url` on every page. A page title is its first H1, or the file stem
if there is none.

The same tree and SHA always build the same bytes.

## Markdown

The parser is CommonMark with tables, strikethrough and raw HTML. Headings get
GitHub-style anchors: lowercase, keep only letters, marks, numbers, `_`, `-`
and spaces, then turn spaces into `-`. A repeated heading gets `-1`, `-2` and
so on. Ids and names in raw HTML can also be link targets.

A fenced code block whose info string names a Pygments lexer is highlighted.
`sh`, `shell` and `console` map to `bash`, and `sv` maps to `systemverilog`.
`text`, an empty info string and unknown names stay plain. The CSS has a light
and a dark Pygments style.

A `mermaid` fence becomes `<pre class="mermaid">` holding the escaped source.
`wiki.js` loads the vendored Mermaid bundle only on pages that have such a
block. It draws with `securityLevel: "strict"` in the light or dark theme and
redraws when the theme changes. The bundle and its MIT license are vendored
under `tools/wiki_site/theme/vendor/`. `tools/wiki_site/theme/PROVENANCE.md`
records the version, hash and origin.

## Navigation

The sidebar is the `wiki/` directory tree, so a page is listed as soon as it
exists. There is no nav config file. Each folder that contains pages becomes a
collapsible entry:

- The landing page (`index.md`, else `README.md`) is the folder's heading
  link. A folder without one shows its name as plain text.
- Inside a folder, pages come first, sorted by file name, then subfolders,
  sorted by name.
- A folder with no landing page, no subfolders and exactly one page is not
  a section: its page is listed in the parent, after the parent's own pages
  and in folder-name order. This applies bottom-up, so the one-page mirror
  folders of `wiki/src/` (`arch/HAS.md`, `rtl/MAS.md`, ...) and
  `wiki/tools/<tool>/SPEC.md` each read as one link labelled by the page's
  title.
- The home page comes first in the sidebar.
- With a root `README.md`, the sidebar is: the home page, `CLAUDE.md`, one
  section for the whole wiki tree headed by the wiki landing page, then a
  **Skills** section with one entry per skill, sorted by name.
- The current page is highlighted, and its ancestor folders start open.

Every page is in the sidebar. Number file names (`0001-…`) to set an order.
Each page also gets an "On this page" table of contents built from its H2 and
H3 headings.

## Links

Links in Markdown (`[]()`, images) and `href`/`src` attributes in raw HTML are
resolved relative to the source file. Only the exact attribute names count:
`data-src`, `data-href` and the like are left alone. Likewise only `id` and
`name` attributes (not `data-id`) add anchors.

| Target | Rewritten to |
| --- | --- |
| URL with a scheme, `//host`, or empty | unchanged |
| `#anchor` | unchanged; the anchor must exist in the page |
| a page the site publishes (under `wiki/`, `README.md`, `CLAUDE.md`, a skill) | relative `.html` path, keeping `#anchor` |
| a folder under `wiki/` | its landing page |
| another file under `wiki/` | relative path to the copied file |
| any other file or folder in the repository | `<repo-url>/blob/<sha>/<path>` (`tree` for folders), plus `?raw=true` for images |

Problems are reported as `WARN` or, with `--strict`, as `ERROR` and exit 1:

- a target that does not exist;
- an anchor missing from the target page (checked for `.md` targets, inside
  or outside `wiki/`; a folder link with an anchor checks the folder's
  landing page);
- a link to a `wiki/` folder that has no landing page;
- a link to a `wiki/` path with a component that starts with `.`, which the
  build skips;
- a `.md` target outside `wiki/` that is not valid UTF-8, when the link has an
  anchor to check (reported once per file; the link is still rewritten);
- a path that leaves the repository, or an absolute `/path`;
- two sources that publish to the same output (`index.md` and `README.md` in
  one folder, or `wiki/agents.md` beside `CLAUDE.md`), or a source that
  publishes to a reserved theme path such as `wiki/assets/wiki.js`;
- no home page.

## Search

The search box in the navbar loads `assets/search-index.json` on first focus.
The index is a list of `{title, url, headings: [[text, anchor]], text}` in
sidebar order, where `headings` are H2 and H3 and `text` is the first 4000
characters of the page's text. Every whitespace-separated word must match,
ignoring case. Title matches rank first, then heading matches, then text
matches (shown with a snippet). Up to 20 hits are shown. `/` focuses the box,
and arrows and Enter pick a hit. The index is fetched, so search works over
HTTP (`serve`, Pages) but not from `file://`.

## Tracker

When `wiki/tracker/index.md` exists, `tools/wiki_site/tracker.py` renders the
records in `wiki/tracker/data/` below that page's own text and generates one
page per IP. The data files are also copied to the site like any other wiki
file. The record schema is owned by the
metrics tool SPEC, `wiki/tools/metrics/SPEC.md` (#15); this section owns how
the site reads it.

**Inputs.**

| File | Holds |
| --- | --- |
| `data/commits.jsonl` | One JSON object per line, one line per commit on `main`, in commit order: `sha`, ISO `date`, `code` stats of that commit's tree (`total`, `kinds`, `ips`, `by_ext`, `by_dir`), and its push `ci-gate` run (`ci.conclusion`, `ci.duration_s`). |
| `data/<date>.json` | One nightly record: `meta` (`date`, else the file stem; `commit`, the `main` SHA it ran on; `nightly.result`), and the `code`, `github`, `health`, `tests`, `fpga`, `asic` and `distributions` groups. |

Lines of code are a bucket's `code` count (non-blank, non-comment lines),
else its `lines`. The nightly runtime is `meta.nightly.duration_s`, else the
regression wall time `tests.wall_s`.

Every record carries `schema_version`; a version other than `1` is a problem
and the record is skipped. Every group and field is optional: a missing one
leaves its chart or table out, and unknown fields are ignored. Field paths
live in one block at the top of `tracker.py`, so a schema change edits only
that block. Problems, reported like link problems (fatal with `--strict`):
unreadable JSON, a non-object record, an unsupported `schema_version`, a bad
date or SHA, a repeated commit or date (the later date file wins), an IP id that is not a safe
relative path (letters, digits, `_`, `.`, `-`, `/`-separated, no `..`), and a
source file at a generated page's path.

**X axis.** Every trend plots against the commits of `commits.jsonl`, in
`index` order when every line has one, else in file order, labelled by short
SHA, with SHA and date in the tooltip. A nightly record sits at the commit it
ran on (`meta.commit`); a record whose commit is not in the series is
ignored. Without `commits.jsonl`, the nightly records' SHAs, in date order,
form the axis.

**`/tracker/`** shows, in order:

| Section | Content |
| --- | --- |
| Repo health | Cards: nightly status (an open `nightly-red` issue, else the latest nightly result), `ci-gate` pass rate over the last runs on `main` (from `health`, else the last 20 results in `commits.jsonl`), open PRs with the oldest age, open issues with the stale count, median review rounds, share of merged PRs with `APPROVE` at the merged head, LOC and files. A table of open PR ages (oldest 20) and an open-issue age histogram. |
| Latest vs 7 and 30 days ago | Each metric's latest value against the last point dated at least 7 and 30 days earlier, with the difference. |
| Code | LOC and files by kind, LOC per IP, lines-per-file and file-size histograms, and tables of the file-type distribution by extension and top-level directory with share bars. |
| CI | `ci-gate` duration per commit, marked by result; duration histogram. |
| Nightly and tests | Nightly duration marked by result; tests, failing tests and runtime per level; test runtime histogram; slowest tests table. |
| Issues and pull requests | Open issues and PRs, issues opened and closed and PRs merged in the trailing 7 days; histograms of issue time to close and to first response or claim, PR lead time, PR size and review rounds; open issues by label. |
| FPGA, ASIC | One chart per metric with a series per IP: LEs, registers, M9K blocks, worst-clock Fmax and setup slack; WNS, TNS, std-cell area, cells, max fanout and logic depth. |

**`/tracker/ip/<id>/`** (`<id>` may contain `/` for a sub-IP) shows that
IP's comparison table, LOC by kind, tests per level, and its FPGA and ASIC
charts. `/tracker/ip/` lists the IPs; the sidebar lists them under it.

**Charts.** Each chart is a `<canvas>` with a caption naming the quantity and
unit, an axis title on both axes, and a data table (the latest 30 points, or
every bin) in a `<details>` below it, so values never depend on colour or
JavaScript. A page's chart data is one inline JSON block; `wiki.js` loads the
vendored Chart.js and `tracker.js` only on pages that have one, and redraws on
a theme change. Colours are CSS custom properties: eight categorical slots
(light and dark steps validated for colour-vision deficiency), assigned in a
fixed order per entity (code kind, level, or IP in sorted order) and never
cycled; a ninth series is not drawn and the chart says so. Status (good,
warning, critical) always pairs a colour with an icon and a label; pass and
fail runs also differ by marker shape. Histograms use equal 1-2-5 bins, about
Sturges' count; a small integer range gets one bin per value, and a long tail
(maximum above twice the p90) is folded into a final "≥" bin from the
first bin edge above the p90.

**No data.** With an empty or missing `data/`, the site still builds:
`/tracker/` and `/tracker/ip/` say "No data yet", and each empty section or
IP group says so too. Every value from a record is HTML-escaped, and the
inline JSON escapes `<`, `>`, `&` and `'`.

## Publishing

`.github/workflows/pages.yml` runs on the self-hosted runners
(`[self-hosted, linux, x64]`). It installs the pinned dependencies into a venv under
`workdir/` with `pip install -r tools/requirements.txt`, and runs
`build --strict --sha $GITHUB_SHA`.

- **Pull requests** that touch `wiki/`, `README.md`, `CLAUDE.md`,
  `.claude/skills/`, the tool or the workflow build only.
- **Pushes to `main`, manual runs and a daily schedule (05:17 UTC)** build,
  upload the site with `actions/upload-pages-artifact`, and deploy it with
  `actions/deploy-pages`. The schedule, and a dispatch by the nightly, cover
  nightly data merged by GitHub Actions auto-merge, whose push starts no
  workflows ([metrics spec](../metrics/SPEC.md#workflows)).

The repository and the published site are **public**; the
[wiki BKM](../../bkm/wiki.md#publishability) owns what may be published. The
captain enables Pages by setting the repository's Pages source to "GitHub
Actions"; until then the deploy job fails while the build job still checks
every change.

## Files

| Path | Role |
| --- | --- |
| [`tools/wiki_site/wiki_site.py`](../../../tools/wiki_site/wiki_site.py) | Generator and CLI. |
| [`tools/cli/main.py`](../../../tools/cli/main.py) | `./build wiki` entry in `COMMANDS`. |
| [`tools/wiki_site/tracker.py`](../../../tools/wiki_site/tracker.py) | Tracker rendering. |
| [`tools/wiki_site/theme/`](../../../tools/wiki_site/theme/) | CSS, JS (`wiki.js`, `tracker.js`), vendored Mermaid and Chart.js, provenance. |
| [`tools/wiki_site/tests/`](../../../tools/wiki_site/tests/) | Unit tests (pytest); synthetic tracker records in `fixtures/tracker/`. |
| [`.github/workflows/pages.yml`](../../../.github/workflows/pages.yml) | Build and deploy workflow. |
