"""Unit tests for tools/wiki_site/wiki_site.py on small synthetic trees."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wiki_site  # noqa: E402

SHA = "0123abcd"
URL = "https://example.invalid/owner/repo"


def make(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def run(root: Path, files: dict[str, str]):
    make(root, files)
    out = root / "workdir" / "site"
    problems, stats = wiki_site.build(root, out, SHA, URL)
    return problems, stats, out


BASE = {
    "wiki/index.md": "# Home\n\nSee [b](b.md) and [sec](sec/).\n",
    "wiki/b.md": "# Bee\n\n## Part one\n\ntext\n",
    "wiki/a.md": "# Ay\n",
    "wiki/sec/README.md": "# Section\n",
    "wiki/sec/z.md": "# Zed\n",
    "wiki/sec/deep/x.md": "# Ex\n",
    "wiki/noland/y.md": "# Why\n",
}


def test_nav_from_tree(tmp_path):
    problems, stats, out = run(tmp_path, BASE)
    assert problems == []
    site = wiki_site.Site(tmp_path, SHA, URL)
    # landing first, then files by name, then folders by name, depth first
    assert site.nav_pages() == ["wiki/index.md", "wiki/a.md", "wiki/b.md", "wiki/noland/y.md",
                                "wiki/sec/README.md", "wiki/sec/z.md", "wiki/sec/deep/x.md"]
    assert set(site.nav_pages()) == set(site.pages)  # every page is in the nav
    html = (out / "sec" / "deep" / "x.html").read_text()
    assert '<a class="active" href="../../sec/deep/x.html">Ex</a>' in html
    assert '<summary><span>noland</span></summary>' in html
    assert '<summary><a href="../../sec/index.html">Section</a></summary>' in html
    assert "<details open><summary><span>deep</span>" in html
    assert stats["pages"] == 7


def test_landing_pages_and_link_rewrite(tmp_path):
    problems, _, out = run(tmp_path, BASE)
    assert problems == []
    assert (out / "sec" / "index.html").is_file() and (out / "index.html").is_file()
    html = (out / "index.html").read_text()
    assert 'href="b.html"' in html and 'href="sec/index.html"' in html


def test_links_leaving_wiki_go_to_github(tmp_path):
    files = dict(BASE, **{
        "CLAUDE.md": "# Contract\n\n## Layout\n",
        "src/top/rtl.sv": "module m; endmodule\n",
        "wiki/sec/z.md": "# Zed\n\n[c](../../CLAUDE.md#layout) [d](../../src/top/) [f](../../src/top/rtl.sv)\n"
                         '<img src="../../src/pic.png">\n',
        "src/pic.png": "x",
    })
    problems, _, out = run(tmp_path, files)
    assert problems == []
    html = (out / "sec" / "z.html").read_text()
    assert f'href="{URL}/blob/{SHA}/CLAUDE.md#layout"' in html
    assert f'href="{URL}/tree/{SHA}/src/top"' in html
    assert f'href="{URL}/blob/{SHA}/src/top/rtl.sv"' in html
    assert f'src="{URL}/blob/{SHA}/src/pic.png?raw=true"' in html


@pytest.mark.parametrize("text, expect", [
    ("[x](missing.md)", "broken link missing.md"),
    ("[x](b.md#nope)", "broken anchor b.md#nope"),
    ("[x](#nope)", "broken anchor #nope"),
    ("[x](noland/)", "names folder wiki/noland, which has no index.md"),
    ("[x](../../outside.md)", "leaves the repository"),
    ("[x](/abs.md)", "absolute link"),
    ("[x](../NOPE.md)", "NOPE.md does not exist"),
    ('<a href="b.md#nope">x</a>', "broken anchor b.md#nope"),
])
def test_strict_failures(tmp_path, text, expect, capsys):
    run(tmp_path, dict(BASE, **{"wiki/a.md": f"# Ay\n\n{text}\n"}))
    rc = wiki_site.main(["build", "--strict", "--sha", SHA, "--root", str(tmp_path),
                         "--out", str(tmp_path / "workdir" / "s2")])
    err = capsys.readouterr().err
    assert rc == 1 and expect in err
    rc = wiki_site.main(["build", "--sha", SHA, "--root", str(tmp_path), "--out", str(tmp_path / "workdir" / "s3")])
    assert rc == 0  # without --strict, problems are warnings


def test_valid_anchors_pass(tmp_path):
    files = dict(BASE, **{"wiki/a.md": "# Ay\n\n## Dup\n\n## Dup\n\n[1](b.md#part-one) [2](#dup-1) "
                                       '[3](#custom)\n\n<a id="custom"></a>\n'})
    problems, _, out = run(tmp_path, files)
    assert problems == []
    assert 'id="dup-1"' in (out / "a.html").read_text()


def test_index_and_readme_collide(tmp_path):
    problems, _, _ = run(tmp_path, dict(BASE, **{"wiki/sec/index.md": "# Other\n"}))
    assert any("publishes to sec/index.html" in p for p in problems)


def test_missing_home_page(tmp_path):
    files = {k: v for k, v in BASE.items() if k != "wiki/index.md"}
    problems, _, _ = run(tmp_path, files)
    assert any("no index.md or README.md" in p for p in problems)


def test_mermaid_passthrough_and_highlight(tmp_path):
    files = dict(BASE, **{"wiki/a.md": "# Ay\n\n```mermaid\ngraph LR\n  A-->B & C\n```\n\n"
                                       "```python\nx = 1\n```\n\n```text\n<raw>\n```\n"})
    problems, _, out = run(tmp_path, files)
    assert problems == []
    html = (out / "a.html").read_text()
    assert '<pre class="mermaid">graph LR\n  A--&gt;B &amp; C\n</pre>' in html
    assert '<pre class="highlight" data-lang="python"><code><span class="n">x</span>' in html
    assert "&lt;raw&gt;" in html
    assert (out / "assets" / "mermaid.min.js").is_file() and (out / "assets" / "mermaid-LICENSE.txt").is_file()


def test_search_index(tmp_path):
    problems, _, out = run(tmp_path, BASE)
    data = json.loads((out / "assets" / "search-index.json").read_text())
    assert [e["url"] for e in data] == ["index.html", "a.html", "b.html", "noland/y.html",
                                         "sec/index.html", "sec/z.html", "sec/deep/x.html"]
    bee = data[2]
    assert bee["title"] == "Bee" and bee["headings"] == [["Part one", "part-one"]]
    assert "text" in bee["text"]


def test_assets_copied_and_deterministic(tmp_path):
    files = dict(BASE, **{"wiki/img/d.svg": "<svg/>", "wiki/b.md": "# Bee\n\n![d](img/d.svg)\n"})
    problems, _, out = run(tmp_path, files)
    assert problems == []
    assert (out / "img" / "d.svg").read_text() == "<svg/>"
    assert 'src="img/d.svg"' in (out / "b.html").read_text()
    first = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    wiki_site.build(tmp_path, out, SHA, URL)
    second = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert first == second


def test_slug():
    assert wiki_site.github_slug("Hello, World: v2.0!") == "hello-world-v20"
    assert wiki_site.github_slug("a_b - c") == "a_b---c"


def test_serve_requires_build(tmp_path, capsys):
    assert wiki_site.main(["serve", "--out", str(tmp_path / "none")]) == 2
    assert "run the build first" in capsys.readouterr().err
