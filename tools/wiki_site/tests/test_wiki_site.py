"""Unit tests for tools/wiki_site/wiki_site.py on small synthetic trees."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
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


# ---------------------------------------------------------------- issue #21

@pytest.mark.parametrize("name", ["wiki.css", "wiki.js", "mermaid.min.js", "mermaid-LICENSE.txt",
                                  "search-index.json"])
def test_theme_outputs_are_reserved(tmp_path, name):
    problems, _, _ = run(tmp_path, dict(BASE, **{f"wiki/assets/{name}": "mine"}))
    assert f"wiki/assets/{name}: publishes to assets/{name}, already written by the theme" in problems


@pytest.mark.parametrize("files, text", [
    ({"wiki/.hid/x.md": "# X\n"}, "[h](.hid/x.md)"),
    ({"wiki/.hid/index.md": "# X\n"}, "[h](.hid/)"),
    ({"wiki/.note.md": "# N\n"}, "[h](.note.md)"),
    ({"wiki/.pic.png": "x"}, "![p](.pic.png)"),
])
def test_links_to_skipped_dot_names_fail(tmp_path, files, text):
    problems, _, out = run(tmp_path, dict(BASE, **files, **{"wiki/a.md": f"# Ay\n\n{text}\n"}))
    assert any("which the build skips" in p for p in problems), problems
    assert not (out / ".hid").exists()


def test_dot_names_outside_wiki_link_to_github(tmp_path):
    files = dict(BASE, **{".github/ci.yml": "on: push\n", "wiki/a.md": "# Ay\n\n[ci](../.github/ci.yml)\n"})
    problems, _, out = run(tmp_path, files)
    assert problems == []
    assert f'href="{URL}/blob/{SHA}/.github/ci.yml"' in (out / "a.html").read_text()


def test_data_attributes_are_not_links(tmp_path):
    text = ('<img data-src="nope.png" src="b.md">\n\n<a data-href="nope.md" href="b.md">x</a>\n\n'
            '<span data-id="fake"></span> [f](#fake)\n')
    problems, _, out = run(tmp_path, dict(BASE, **{"wiki/a.md": f"# Ay\n\n{text}"}))
    assert problems == ["wiki/a.md: broken anchor #fake"]  # data-id is not an anchor either
    html = (out / "a.html").read_text()
    assert 'data-src="nope.png" src="b.html"' in html
    assert 'data-href="nope.md" href="b.html"' in html


def test_non_utf8_md_target_is_a_problem(tmp_path):
    make(tmp_path, BASE)
    (tmp_path / "doc.md").write_bytes(b"# Bad \xff\xfe\n")
    files = {"wiki/a.md": "# Ay\n\n[d](../doc.md#bad) [e](../doc.md#other)\n"}
    problems, _, out = run(tmp_path, files)
    assert len(problems) == 1 and problems[0].startswith("doc.md: cannot read as UTF-8")
    assert f'href="{URL}/blob/{SHA}/doc.md#bad"' in (out / "a.html").read_text()


def test_special_characters_are_escaped(tmp_path):
    files = dict(BASE, **{
        "wiki/index.md": "# Home & `<Co>`\n",
        "wiki/a.md": '# Use `a<b && "c"`\n\n## Part `<x>` & y\n\n### `i<j`\n',
    })
    problems, _, out = run(tmp_path, files)
    assert problems == []
    html = (out / "a.html").read_text()
    assert "<title>Use a&lt;b &amp;&amp; &quot;c&quot; | Home &amp; &lt;Co&gt;</title>" in html
    assert '<a class="navbar-brand" href="index.html">Home &amp; &lt;Co&gt;</a>' in html
    assert '<a class="active" href="a.html">Use a&lt;b &amp;&amp; &quot;c&quot;</a>' in html  # sidebar
    assert '<li class="toc-l2"><a href="#part-x--y">Part &lt;x&gt; &amp; y</a></li>' in html
    assert '<li class="toc-l3"><a href="#ij">i&lt;j</a></li>' in html
    assert "<x>" not in html and "<Co>" not in html
    data = json.loads((out / "assets" / "search-index.json").read_text())
    assert data[1]["title"] == 'Use a<b && "c"'  # raw in JSON; wiki.js inserts it as text


def test_unresolvable_head_sha_exits_2(tmp_path, capsys):
    make(tmp_path, BASE)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # no commits: HEAD unresolvable
    rc = wiki_site.main(["build", "--root", str(tmp_path), "--out", str(tmp_path / "workdir" / "s")])
    assert rc == 2
    assert "cannot resolve the built commit" in capsys.readouterr().err
    assert not (tmp_path / "workdir" / "s").exists()


def test_serve_existing_build(tmp_path, monkeypatch, capsys):
    _, _, out = run(tmp_path, BASE)
    started: list = []

    class Server(wiki_site.http.server.ThreadingHTTPServer):
        def serve_forever(self, *a, **kw):
            started.append(self)
            super().serve_forever(*a, **kw)

    monkeypatch.setattr(wiki_site.http.server, "ThreadingHTTPServer", Server)
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(wiki_site.main(["serve", "--out", str(out),
                                                                           "--port", "0"])))
    thread.start()
    try:
        for _ in range(500):
            if started:
                break
            time.sleep(0.01)
        host, port = started[0].server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/a.html", timeout=5) as resp:
            assert resp.status == 200 and b"<title>Ay | Home</title>" in resp.read()
        with urllib.request.urlopen(f"http://{host}:{port}/assets/search-index.json", timeout=5) as resp:
            assert json.loads(resp.read())[0]["url"] == "index.html"
    finally:
        if started:
            started[0].shutdown()
        thread.join(5)
    assert result == [0]
    assert f"Serving {out}" in capsys.readouterr().out
