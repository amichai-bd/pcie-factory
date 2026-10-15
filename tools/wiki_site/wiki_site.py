"""Build the wiki as a small static HTML site.

Every `wiki/**/*.md` page becomes an HTML page at its mirrored path
(`index.md` or `README.md` -> the folder's `index.html`); other wiki files are
copied byte for byte. The sidebar is derived from the `wiki/` directory tree.
When the repository has a root `README.md`, it is the home page, `CLAUDE.md`
is published beside it with a toggle between the two, the wiki's own landing
page moves to `wiki.html`, and `.claude/skills/<name>/SKILL.md` become a
Skills section.
Links are rewritten: `.md` -> `.html`, folders -> their landing page, and
repository paths outside `wiki/` -> GitHub blob/tree URLs at the built commit.
`wiki/tracker/index.md` gains the metrics tracker rendered from
`wiki/tracker/data/` (tracker.py), plus generated per-IP pages; the tracker is
its own navbar tab with its own sidebar, outside the wiki tree.
`--strict` turns every broken link or anchor into a failure. The same tree and
SHA always build the same bytes. See wiki/tools/wiki_site/SPEC.md.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import posixpath
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from html import escape
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

import tracker

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
WIKI = "wiki"
REPO_URL = "https://github.com/amichai-bd/pcie-factory"
LANDINGS = ("index.md", "README.md")
HOME = "README.md"  # repo-root page that, if present, is the site home
AGENTS = "CLAUDE.md"  # agent contract, published beside the home page
SKILLS = ".claude/skills"  # <name>/SKILL.md -> skills/<name>.html
FRONTMATTER = re.compile(r"\A---\n(?:(.*?)\n)?---[ \t]*(?:\n|\Z)", re.S)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
LEXER_ALIASES = {"sh": "bash", "shell": "bash", "console": "bash", "sv": "systemverilog"}
PLAIN = {"", "text", "txt", "plain"}
THEME = ("wiki.css", "wiki.js", "tracker.js", "vendor/mermaid.min.js", "vendor/mermaid-LICENSE.txt",
         "vendor/chart.umd.js", "vendor/chart-LICENSE.md")
TRACKER = "wiki/tracker"  # landing page gets the rendered tracker; data/ holds the records
TABS = ("Wiki", "Tracker")  # navbar tabs when the tracker exists: everything else, the tracker
# `(?<![\w-])`, not `\b`: `-` is a word boundary, so `\b` would also match the
# tail of `data-src=` or `data-id=`.
ATTR = re.compile(r"""((?<![\w-])(?:href|src)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
HTML_ID = re.compile(r"""(?<![\w-])(?:id|name)\s*=\s*["']([^"']+)["']""", re.I)
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
# Site paths the build writes itself; no wiki source may publish to them.
RESERVED = tuple(f"assets/{PurePosixPath(n).name}" for n in THEME) + ("assets/search-index.json",)
TEXT_LIMIT = 4000  # characters of body text per page in the search index


def github_slug(text: str) -> str:
    """GitHub's heading anchor: lowercase, keep letters, marks, numbers, `_`,
    `-` and spaces, then spaces -> `-`."""
    kept = (c for c in text.lower() if c in " -_" or unicodedata.category(c)[0] in "LMN")
    return "".join(kept).replace(" ", "-")


def unique_slug(base: str, seen: dict[str, int]) -> str:
    """A repeated heading gets the next free `-N` suffix, as on GitHub."""
    slug = base
    while slug in seen:
        seen[base] += 1
        slug = f"{base}-{seen[base]}"
    seen[slug] = 0
    return slug


def read_error(rel: str, exc: OSError | UnicodeDecodeError) -> str:
    """One-line message naming `rel`, telling a decode error from a read error."""
    if isinstance(exc, UnicodeDecodeError):
        return f"{rel}: not valid UTF-8 ({exc.reason} at byte {exc.start})"
    return f"{rel}: cannot read ({exc.strerror or exc})"


class SourceError(Exception):
    """Sources the build cannot read; `messages` names each one."""

    def __init__(self, messages: list[str]):
        super().__init__("; ".join(messages))
        self.messages = messages


def out_path(rel: str) -> str:
    """Site path of a file given relative to wiki/ (POSIX)."""
    p = PurePosixPath(rel)
    if p.name in LANDINGS:
        return str(p.parent / "index.html") if str(p.parent) != "." else "index.html"
    return str(p.with_suffix(".html")) if p.suffix == ".md" else rel


def skill_text(text: str) -> str:
    """A SKILL.md without its YAML frontmatter (see `skill_page`)."""
    return skill_page(text)[0]


def skill_page(text: str) -> tuple[str, str]:
    """(Markdown, description) of a SKILL.md. The frontmatter is dropped; the
    `description` (one line, indented continuation lines, or a `>`/`|` block)
    becomes an escaped HTML quote under the first H1, so it is shown
    literally, never as Markdown, and is returned for the search text."""
    m = FRONTMATTER.match(text)
    if not m:
        return text, ""
    meta: dict[str, str] = {}
    key = None
    for line in (m.group(1) or "").splitlines():
        if key and line[:1] in (" ", "\t"):
            meta[key] += " " + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            meta[key] = "" if value in (">", "|", ">-", "|-") else value
    body = text[m.end():]
    desc = " ".join(meta.get("description", "").split())
    if not desc:
        return body, ""
    lines = body.split("\n")
    at, fence = 0, None
    for i, line in enumerate(lines):
        f = FENCE.match(line)
        if f and (fence is None or f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence)):
            fence = None if fence else f.group(1)
        elif fence is None and line.startswith("# "):
            at = i + 1
            break
    quote_html = f'<blockquote class="skill-description"><p>{escape(desc)}</p></blockquote>'
    return "\n".join(lines[:at] + ["", quote_html, ""] + lines[at:]), desc


def inline_text(token) -> str:
    return "".join(c.content if c.type not in ("softbreak", "hardbreak") else " "
                   for c in token.children or () if c.type in ("text", "code_inline", "softbreak", "hardbreak")).strip()


def new_md() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])


class Page:
    """One Markdown source: tokens, title, headings and the ids links may name."""

    def __init__(self, md: MarkdownIt, rel: str, text: str):
        self.rel = rel  # repo-relative source path
        self.source = text  # the Markdown as read
        self.env: dict = {}
        self.tokens = md.parse(text, self.env)
        self.title = ""
        self.headings: list[tuple[int, str, str]] = []  # (level, text, anchor)
        self.ids: set[str] = set()
        words: list[str] = []
        seen: dict[str, int] = {}
        for i, tok in enumerate(self.tokens):
            if tok.type == "heading_open":
                text_ = inline_text(self.tokens[i + 1])
                anchor = unique_slug(github_slug(text_), seen)
                tok.attrSet("id", anchor)
                self.tokens[i + 2].meta["anchor"] = anchor
                self.headings.append((int(tok.tag[1]), text_, anchor))
                if tok.tag == "h1" and not self.title:
                    self.title = text_
            elif tok.type == "inline":
                words.append(inline_text(tok))
            elif tok.type == "html_block":
                self.ids.update(HTML_ID.findall(tok.content))
            for child in tok.children or ():
                if child.type == "html_inline":
                    self.ids.update(HTML_ID.findall(child.content))
        self.ids.update(a for _, _, a in self.headings)
        self.title = self.title or PurePosixPath(rel).stem
        self.text = " ".join(" ".join(words).split())[:TEXT_LIMIT]


@dataclass
class Folder:
    """A sidebar section: a wiki directory, or a section the build adds."""
    rel: str  # path relative to wiki/ ("" for the root); its name is the label
    landing: str | None = None  # repo-relative source of index.md / README.md
    pages: list[str] = field(default_factory=list)
    folders: list["Folder"] = field(default_factory=list)


class Site:
    def __init__(self, root: Path, sha: str, repo_url: str = REPO_URL):
        self.root, self.sha, self.repo_url = root, sha, repo_url.rstrip("/")
        self.md = new_md()
        self.md.add_render_rule("fence", render_fence)
        self.md.add_render_rule("heading_close", render_heading_close)
        self.pages: dict[str, Page] = {}  # repo-relative source -> page
        self.assets: list[str] = []  # repo-relative non-Markdown wiki files
        self.out: dict[str, str] = {}  # repo-relative source -> site path
        self.problems: list[str] = []
        self.unreadable: list[str] = []  # sources that could not be read (fatal)
        self.links = 0
        self._foreign: dict[str, set[str] | None] = {}
        self.tree = self._scan(root / WIKI)
        self.tracker: Folder | None = None  # the tracker tab's nav, outside the wiki tree
        self._add_tracker()
        for rel in list(self.pages) + self.assets:
            self.out[rel] = out_path(rel[len(WIKI) + 1:])
        self.home = self.tree.landing
        self.toggle: tuple[str, str] | None = None  # (home, agents) sources
        self.top: list[str] = ([self.tree.landing] if self.tree.landing else []) + self.tree.pages
        self.sections: list[Folder] = list(self.tree.folders)
        if (root / HOME).is_file():
            self._add_repo_pages()
        taken: dict[str, str] = {out: "the theme" for out in RESERVED}
        for rel in sorted(self.out):
            out = self.out[rel]
            if out in taken:
                self.problems.append(f"{rel}: publishes to {out}, already written by {taken[out]}")
            taken[out] = rel
        if "index.html" not in taken:
            self.problems.append(f"{WIKI}/: no index.md or README.md for the site home page")
        if self.unreadable:
            raise SourceError(self.unreadable)
        self.tracker_out = {self.site_path(p) for p in self.nav_pages(self.tracker)} if self.tracker else set()

    def read(self, rel: str) -> str:
        """A source's text; an unreadable source is recorded and reads as ""
        so that the scan goes on and reports every bad file at once."""
        try:
            return (self.root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.unreadable.append(read_error(rel, exc))
            return ""

    def _add_tracker(self) -> None:
        """Append the rendered tracker to wiki/tracker's landing page, add
        the generated per-IP pages (virtual sources under wiki/tracker/ip/),
        and move the tracker folder out of the wiki tree into its own tab."""
        landing = self.landing_of(TRACKER)
        folder = next((f for f in self.tree.folders if f.rel == "tracker"), None)
        if landing is None or folder is None:
            return
        body, pages, problems = tracker.render(self.root / TRACKER / tracker.DATA_DIR)
        self.problems += problems
        text = self.pages[landing].source.rstrip("\n") + "\n\n" + body
        self.pages[landing] = Page(self.md, landing, text)
        ips = Folder(f"tracker/{tracker.IP_DIR}")
        for rel, md in sorted(pages.items()):
            src = f"{TRACKER}/{rel}"
            if (self.root / src).exists():
                self.problems.append(f"{src}: generated by the tracker; remove the source file")
                continue
            self.pages[src] = Page(self.md, src, md)
            if rel == f"{tracker.IP_DIR}/index.md":
                ips.landing = src
            else:
                ips.pages.append(src)
        folder.folders.insert(0, ips)
        self.tree.folders = [f for f in self.tree.folders if f is not folder]
        self.tracker = folder

    def _add(self, src: str, out: str, text: str) -> None:
        self.pages[src] = Page(self.md, src, text)
        self.out[src] = out

    def _add_repo_pages(self) -> None:
        """README.md is home, CLAUDE.md sits beside it, the wiki tree becomes
        one section headed by its landing page, and skills another."""
        read = self.read
        self._add(HOME, "index.html", read(HOME))
        self.home, self.top = HOME, [HOME]
        if (self.root / AGENTS).is_file():
            self._add(AGENTS, "agents.html", read(AGENTS))
            self.toggle = (HOME, AGENTS)
            self.top.append(AGENTS)
        if self.tree.landing:
            self.out[self.tree.landing] = "wiki.html"
        wiki = Folder(WIKI, self.tree.landing, self.tree.pages, self.tree.folders)
        self.sections = [wiki] if wiki.landing or wiki.pages or wiki.folders else []
        skills = Folder("Skills")
        skill_dir = self.root / SKILLS
        for f in sorted(skill_dir.glob("*/SKILL.md")) if skill_dir.is_dir() else ():
            src = f.relative_to(self.root).as_posix()
            md, desc = skill_page(read(src))
            self._add(src, f"skills/{f.parent.name}.html", md)
            if desc:  # the quote is an HTML block, so add it to the search text here
                page = self.pages[src]
                page.text = f"{desc} {page.text}"[:TEXT_LIMIT].rstrip()
            skills.pages.append(src)
        if skills.pages:
            self.sections.append(skills)

    def _scan(self, d: Path) -> Folder:
        rel = d.relative_to(self.root / WIKI).as_posix()
        folder = Folder("" if rel == "." else rel)
        for child in sorted(d.iterdir(), key=lambda p: p.name):
            if child.name.startswith("."):
                continue
            src = child.relative_to(self.root).as_posix()
            if child.is_dir():
                sub = self._scan(child)
                if sub.landing or sub.pages or sub.folders:
                    folder.folders.append(sub)
            elif child.suffix == ".md":
                self.pages[src] = Page(self.md, src, self.read(src))
                if child.name in LANDINGS and folder.landing is None:
                    folder.landing = src
                else:
                    folder.pages.append(src)
            else:
                self.assets.append(src)
        # A folder holding just one page and nothing else is listed as that
        # page (after the folder's own pages), not as a one-entry section.
        single = [f for f in folder.folders if not f.landing and not f.folders
                  and len(f.pages) == 1]
        folder.pages += [f.pages[0] for f in single]
        folder.folders = [f for f in folder.folders if all(f is not g for g in single)]
        return folder

    def site_path(self, src: str) -> str:
        return self.out[src]

    def landing_of(self, d: str) -> str | None:
        for name in LANDINGS:
            if f"{d}/{name}" in self.pages:
                return f"{d}/{name}"
        return None

    def anchors_of(self, target: str) -> set[str] | None:
        """Anchors a link may name in a repo file, or None when not checkable."""
        if target in self.pages:
            return self.pages[target].ids
        if not target.endswith(".md"):
            return None
        if target not in self._foreign:
            try:
                text = (self.root / target).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                self.problems.append(f"{read_error(target, exc)}; its anchors are not checked")
                self._foreign[target] = None
            else:
                self._foreign[target] = Page(self.md, target, text).ids
        return self._foreign[target]

    def resolve(self, src: str, href: str, raw: bool = False) -> str:
        """Rewrite one link found in page `src`; record any problem."""
        if not href or SCHEME.match(href) or href.startswith("//"):
            return href
        self.links += 1
        path, _, frag = href.partition("#")
        path = path.split("?", 1)[0]
        anchor = unquote(frag)
        if not path:
            if frag and anchor not in self.pages[src].ids:
                self.problems.append(f"{src}: broken anchor #{frag}")
            return href
        if path.startswith("/"):
            self.problems.append(f"{src}: absolute link {href}; use a relative path")
            return href
        target = posixpath.normpath(posixpath.join(posixpath.dirname(src), unquote(path)))
        full = self.root / target
        if target == ".." or target.startswith("../"):
            self.problems.append(f"{src}: link {href} leaves the repository")
            return href
        if not full.exists() and target not in self.pages:  # generated pages have no file
            self.problems.append(f"{src}: broken link {href} ({target} does not exist)")
            return href
        in_wiki = target == WIKI or target.startswith(WIKI + "/")
        if in_wiki and any(part.startswith(".") for part in target.split("/")):
            self.problems.append(f"{src}: link {href} names {target}, which the build skips (name starts with '.')")
            return href
        dest: str | None = target
        if full.is_dir():
            dest = self.landing_of(target) if in_wiki else next(
                (f"{target}/{n}" for n in LANDINGS if (full / n).is_file()), None)
            if dest is None and in_wiki:
                self.problems.append(f"{src}: link {href} names folder {target}, which has no index.md")
                return href
        if frag:
            known = self.anchors_of(dest) if dest else set()
            if known is not None and anchor not in known:
                self.problems.append(f"{src}: broken anchor {href} (no #{anchor} in {dest or target})")
        suffix = "#" + frag if frag else ""
        if not in_wiki and dest not in self.pages:
            kind = "tree" if full.is_dir() else "blob"
            tail = "?raw=true" if raw and kind == "blob" else ""
            return f"{self.repo_url}/{kind}/{self.sha}/{quote(target)}{tail}{suffix}"
        rel = posixpath.relpath(self.site_path(dest), posixpath.dirname(self.site_path(src)) or ".")
        return quote(rel, safe="/-._~") + suffix

    def _rewrite_html(self, src: str, html: str) -> str:
        def sub(m):
            raw = m.group(1).strip().lower().startswith("src")
            return f"{m.group(1)}{m.group(2)}{self.resolve(src, m.group(3), raw)}{m.group(2)}"
        return ATTR.sub(sub, html)

    def rewrite(self, page: Page) -> None:
        for tok in page.tokens:
            if tok.type == "html_block":
                tok.content = self._rewrite_html(page.rel, tok.content)
            for child in tok.children or ():
                if child.type == "link_open":
                    child.attrSet("href", self.resolve(page.rel, child.attrGet("href") or ""))
                elif child.type == "image":
                    child.attrSet("src", self.resolve(page.rel, child.attrGet("src") or "", raw=True))
                elif child.type == "html_inline":
                    child.content = self._rewrite_html(page.rel, child.content)

    # ------------------------------------------------------------- sidebar
    def tabs(self, active: str, root: str) -> str:
        """Navbar tabs: the wiki (home) and the tracker; empty without a tracker."""
        if not self.tracker:
            return ""
        links = []
        for label, out in zip(TABS, ("index.html", self.site_path(self.tracker.landing))):
            on = (active in self.tracker_out) == (label == TABS[1])
            cls = ' class="active" aria-current="page"' if on else ""
            links.append(f'<a{cls} href="{root}{quote(out, safe="/-._~")}">{label}</a>')
        return "".join(links)

    def nav(self, active: str) -> tuple[list[str], list[Folder]]:
        """(top pages, sections) of the sidebar for site path `active`: the
        tracker's own nav on tracker pages, else the wiki's."""
        if active in self.tracker_out:
            return [self.tracker.landing] + self.tracker.pages, self.tracker.folders
        return self.top, self.sections

    def sidebar(self, active: str, root: str) -> str:
        top, sections = self.nav(active)
        expand = active in self.tracker_out  # the tracker nav is short: show it all
        pages = "".join(self._link(p, active, root) for p in top)
        items = "".join(self._folder(f, active, root, expand) for f in sections)
        return f'<ul class="menu">{pages}{items}</ul>'

    def _link(self, src: str, active: str, root: str) -> str:
        out = self.site_path(src)
        cls = ' class="active"' if out == active else ""
        return f'<li><a{cls} href="{root}{quote(out, safe="/-._~")}">{escape(self.pages[src].title)}</a></li>'

    def _folder(self, folder: Folder, active: str, root: str, expand: bool = False) -> str:
        name = PurePosixPath(folder.rel).name
        if folder.landing:
            out = self.site_path(folder.landing)
            cls = ' class="active"' if out == active else ""
            head = f'<a{cls} href="{root}{quote(out, safe="/-._~")}">{escape(self.pages[folder.landing].title)}</a>'
        else:
            head = f"<span>{escape(name)}</span>"
        kids = "".join(self._link(p, active, root) for p in folder.pages)
        kids += "".join(self._folder(f, active, root, expand) for f in folder.folders)
        is_open = " open" if expand or active in {self.site_path(p) for p in self.nav_pages(folder)} else ""
        return f"<li><details{is_open}><summary>{head}</summary><ul>{kids}</ul></details></li>"

    def nav_pages(self, folder: Folder | None = None) -> list[str]:
        """Every page a sidebar links to (in `folder`), in sidebar order: the
        wiki's sidebar, then the tracker's."""
        if folder is None:
            tracker = self.nav_pages(self.tracker) if self.tracker else []
            return self.top + [p for f in self.sections for p in self.nav_pages(f)] + tracker
        out = ([folder.landing] if folder.landing else []) + folder.pages
        for sub in folder.folders:
            out += self.nav_pages(sub)
        return out


def render_fence(renderer, tokens, idx, options, env) -> str:
    tok = tokens[idx]
    lang = tok.info.strip().split()[0].lower() if tok.info.strip() else ""
    if lang == "mermaid":  # drawn in the browser by assets/mermaid.min.js (wiki.js)
        return f'<pre class="mermaid">{escape(tok.content)}</pre>\n'
    lexer = None
    if lang not in PLAIN:
        try:
            lexer = get_lexer_by_name(LEXER_ALIASES.get(lang, lang))
        except ClassNotFound:
            pass
    body = highlight(tok.content, lexer, HtmlFormatter(nowrap=True)) if lexer else escape(tok.content)
    label = f' data-lang="{escape(lang)}"' if lang else ""
    return f'<pre class="highlight"{label}><code>{body}</code></pre>\n'


def render_heading_close(renderer, tokens, idx, options, env) -> str:
    anchor = escape(tokens[idx].meta.get("anchor", ""))
    return f'<a class="hash-link" href="#{anchor}" aria-label="Link to this heading">#</a></{tokens[idx].tag}>\n'


def theme_css() -> str:
    light = HtmlFormatter(style="default").get_style_defs(".highlight")
    dark = HtmlFormatter(style="github-dark").get_style_defs('[data-theme="dark"] .highlight')
    return ((HERE / "theme" / "wiki.css").read_text(encoding="utf-8")
            + "\n/* pygments: default (light) */\n" + light + "\n/* pygments: github-dark */\n" + dark + "\n")


def layout(site: Site, out: str, page: Page, body: str, brand: str) -> str:
    root = "../" * out.count("/")
    site_tabs = site.tabs(out, root)
    toc = [(lvl, t, a) for lvl, t, a in page.headings if lvl in (2, 3)]
    toc_html = ""
    if toc:
        toc_html = ('<aside class="toc"><div class="toc-title">On this page</div><ul>' + "".join(
            f'<li class="toc-l{lvl}"><a href="#{escape(a)}">{escape(t)}</a></li>' for lvl, t, a in toc)
            + "</ul></aside>")
    title = escape(page.title if page.title == brand else f"{page.title} | {brand}")
    if site.toggle and page.rel in site.toggle:
        tabs = []
        for src in site.toggle:
            cls = ' class="active"' if src == page.rel else ""
            tabs.append(f'<a{cls} href="{root}{site.site_path(src)}">{escape(src)}</a>')
        body = f'<nav class="doc-toggle" aria-label="Landing page">{"".join(tabs)}</nav>\n' + body
    return f"""<!doctype html>
<html lang="en" data-root="{root}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script>try{{var t=localStorage.getItem("theme")}}catch(e){{}}document.documentElement.dataset.theme=t||(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light")</script>
<link rel="stylesheet" href="{root}assets/wiki.css">
</head>
<body>
<nav class="navbar">
<button class="navbar-toggle" type="button" aria-label="Toggle navigation">&#9776;</button>
<a class="navbar-brand" href="{root}index.html">{escape(brand)}</a>
{site_tabs and f'<div class="navbar-tabs">{site_tabs}</div>'}
<div class="navbar-right">
<a class="navbar-repo" href="{escape(site.repo_url)}" title="Source on GitHub">GitHub</a>
<div class="search"><input type="search" placeholder="Search" aria-label="Search the wiki" autocomplete="off"><ul class="search-results" hidden></ul></div>
<button class="theme-toggle" type="button" aria-label="Toggle dark mode" title="Toggle dark mode"><span class="sun">&#9728;</span><span class="moon">&#9790;</span></button>
</div>
</nav>
<div class="layout">
<aside class="sidebar">{site_tabs and f'<nav class="sidebar-tabs" aria-label="Site sections">{site_tabs}</nav>'}{site.sidebar(out, root)}</aside>
<main class="doc"><article class="markdown">
{body}</article></main>
{toc_html}
</div>
<script src="{root}assets/wiki.js"></script>
</body>
</html>
"""


def search_index(site: Site) -> str:
    entries = []
    for src in site.nav_pages():
        p = site.pages[src]
        entries.append({"title": p.title, "url": site.site_path(src), "text": p.text,
                        "headings": [[t, a] for lvl, t, a in p.headings if lvl in (2, 3)]})
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def build(root: Path, out: Path, sha: str, repo_url: str = REPO_URL) -> tuple[list[str], dict]:
    """Build the site into `out` (replaced); return (problems, stats).
    Raise SourceError, leaving `out` untouched, if a source cannot be read."""
    site = Site(root, sha, repo_url)
    for page in site.pages.values():
        site.rewrite(page)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    brand = site.pages[site.home].title if site.home else "Wiki"
    for src, page in sorted(site.pages.items()):
        dest = site.site_path(src)
        (out / dest).parent.mkdir(parents=True, exist_ok=True)
        body = site.md.renderer.render(page.tokens, site.md.options, page.env)
        (out / dest).write_text(layout(site, dest, page, body, brand), encoding="utf-8")
    for src in site.assets:
        (out / site.site_path(src)).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / src, out / site.site_path(src))
    assets = out / "assets"
    assets.mkdir(exist_ok=True)
    for name in THEME:
        shutil.copyfile(HERE / "theme" / name, assets / PurePosixPath(name).name)
    (assets / "wiki.css").write_text(theme_css(), encoding="utf-8")
    index = search_index(site)
    (assets / "search-index.json").write_text(index, encoding="utf-8")
    stats = {"pages": len(site.pages), "assets": len(site.assets), "links": site.links,
             "search_index_bytes": len(index.encode())}
    return site.problems, stats


def head_sha(root: Path) -> str:
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def serve(out: Path, port: int) -> int:
    if not (out / "index.html").is_file():
        print(f"ERROR: {out}/index.html not found; run the build first", file=sys.stderr)
        return 2
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Serving {out} at http://127.0.0.1:{port}/ (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build the site")
    b.add_argument("--strict", action="store_true", help="fail on any broken link or anchor")
    b.add_argument("--out", type=Path, default=REPO / "workdir" / "wiki-site", help="output directory (replaced)")
    b.add_argument("--sha", help="commit for GitHub links (default: git rev-parse HEAD)")
    b.add_argument("--repo-url", default=REPO_URL, help="repository URL for links to unpublished files")
    b.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    s = sub.add_parser("serve", help="serve the built site over HTTP")
    s.add_argument("--out", type=Path, default=REPO / "workdir" / "wiki-site", help="built site directory")
    s.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve(args.out.resolve(), args.port)
    try:
        sha = args.sha or head_sha(args.root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: cannot resolve the built commit ({exc}); pass --sha", file=sys.stderr)
        return 2
    try:
        problems, stats = build(args.root.resolve(), args.out.resolve(), sha, args.repo_url)
    except SourceError as exc:
        for m in exc.messages:
            print(f"ERROR: {m}", file=sys.stderr)
        print(f"FAIL: unreadable={len(exc.messages)} out={args.out} (not written)")
        return 1
    tag = "ERROR" if args.strict else "WARN"
    for p in problems:
        print(f"{tag}: {p}", file=sys.stderr)
    failed = bool(problems) and args.strict
    summary = " ".join(f"{k}={v}" for k, v in stats.items())
    print(f"{'FAIL' if failed else 'PASS'}: {summary} problems={len(problems)} out={args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
