# Wiki site generator

`tools/wiki_site/wiki_site.py` turns `wiki/` into a static HTML site that
GitHub Pages publishes. It is small on purpose: no nav config, no framework,
no Node and no view-time network access.

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
| `--sha` | `git rev-parse HEAD` | Commit used in links that leave `wiki/`. Exit 2 if it can't be resolved. |
| `--repo-url` | `https://github.com/amichai-bd/pcie-factory` | Repository used in links that leave `wiki/`. |
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
| (theme) | `assets/wiki.css`, `wiki.js`, `mermaid.min.js`, `mermaid-LICENSE.txt` |
| (generated) | `assets/search-index.json` |

The theme and generated paths under `assets/` are reserved: a wiki source that
would publish to one of them is a problem (see [Links](#links)).

Names that start with `.` are skipped, at any depth. `wiki/index.md` (or `wiki/README.md`)
is the site home page, and its first H1 is the site name in the navbar. The
navbar also links to `--repo-url` on every page. A page
title is its first H1, or the file stem if there is none.

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
- The home page comes first in the sidebar.
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
| a page under `wiki/` | relative `.html` path, keeping `#anchor` |
| a folder under `wiki/` | its landing page |
| another file under `wiki/` | relative path to the copied file |
| a file or folder in the repository but outside `wiki/` | `<repo-url>/blob/<sha>/<path>` (`tree` for folders), plus `?raw=true` for images |

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
  one folder), or a source that publishes to a reserved theme path such as
  `wiki/assets/wiki.js`;
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

## Publishing

`.github/workflows/pages.yml` runs on the self-hosted runners
(`[self-hosted, linux, x64]`). It installs the pinned dependencies into a venv under
`workdir/` with `pip install -r tools/requirements.txt`, and runs
`build --strict --sha $GITHUB_SHA`.

- **Pull requests** that touch `wiki/`, the tool or the workflow build only.
- **Pushes to `main` and manual runs** build, upload the site with
  `actions/upload-pages-artifact`, and deploy it with `actions/deploy-pages`.

The published site is **public** although the repository is private; the
[wiki BKM](../../bkm/wiki.md#publishability) owns what may be published. The
captain enables Pages by setting the repository's Pages source to "GitHub
Actions"; until then the deploy job fails while the build job still checks
every change.

## Files

| Path | Role |
| --- | --- |
| [`tools/wiki_site/wiki_site.py`](../../../tools/wiki_site/wiki_site.py) | Generator and CLI. |
| [`tools/cli/main.py`](../../../tools/cli/main.py) | `./build wiki` entry in `COMMANDS`. |
| [`tools/wiki_site/theme/`](../../../tools/wiki_site/theme/) | CSS, JS, vendored Mermaid, provenance. |
| [`tools/wiki_site/tests/`](../../../tools/wiki_site/tests/) | Unit tests (pytest). |
| [`.github/workflows/pages.yml`](../../../.github/workflows/pages.yml) | Build and deploy workflow. |
