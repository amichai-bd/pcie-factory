import subprocess
from pathlib import Path

import pytest

from build.ip import REPO_ROOT
from checks import neutrality
from checks.common import CheckConfigError

# A made-up word list: these tests must not name the real design target.
CONFIG = """# neutrality words for the test repository
neutrality:
  words: [frobnitz, widget bus]
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "remote", "add", "origin", "https://github.com/someone/frobnitz-factory.git")
    write(r, "cfg/checks.yaml", CONFIG)
    write(r, "tools/ok.py", "x = 1\n")
    write(r, "src/a/rtl/frobnitz_a.sv", "// frobnitz is fine in src/\n")
    write(r, "wiki/src/a/MAS.md", "Frobnitz\n")
    write(r, "wiki/tracker/data/1.json", '{"frobnitz": 1}\n')
    git(r, "add", "-A")
    return r


def findings(repo: Path):
    return neutrality.run(repo).findings


def test_clean_repo_passes(repo):
    r = neutrality.run(repo)
    assert r.passed, r.findings
    # cfg/checks.yaml and tools/ok.py; the three exempt trees are skipped
    assert r.checked.startswith("2 file(s) (tracked and untracked, not ignored) outside "
                                "src/, wiki/src/, wiki/tracker/data/")
    assert "slug exempt in URLs: someone/frobnitz-factory" in r.checked


def test_words_in_tools_and_wiki_fail(repo):
    write(repo, "tools/x.py", "NAME = 'FrobNitz'\n\nbus = 'Widget-Bus'\n")
    write(repo, "wiki/bkm/x.md", "a widget_bus, a  widget \t bus and a WidgetBus\n")
    assert findings(repo) == [
        "tools/x.py:1: 'FrobNitz'",
        "tools/x.py:3: 'Widget-Bus'",
        "wiki/bkm/x.md:1: 'widget_bus'",
        "wiki/bkm/x.md:1: 'widget \\t bus'",
        "wiki/bkm/x.md:1: 'WidgetBus'",  # separator-free variant
    ]


def test_path_names_are_scanned(repo):
    write(repo, "tools/frobnitz_util.py", "")
    assert findings(repo) == ["tools/frobnitz_util.py: path contains 'frobnitz'"]


@pytest.mark.parametrize("text, ok", [
    ("see https://github.com/someone/frobnitz-factory/issues/1", True),
    ("git@github.com:someone/frobnitz-factory.git", True),
    ("https://someone.github.io/frobnitz-factory/", True),
    ("https://GitHub.com/Someone/Frobnitz-Factory", True),
    ("github.com/someone/frobnitz-factory", True),
    ("https://github.com/someone/frobnitz-factory-extra", False),
    ("https://github.com/someone/frobnitz-factory.frobnitz", False),
    ("https://github.com/other/frobnitz-factory", False),
    ("the frobnitz-factory repository", False),
    ("https://example.com/someone/frobnitz-factory", False),
    ("https://evilgithub.com/someone/frobnitz-factory", False),
    ("https://xsomeone.github.io/frobnitz-factory/", False),
])
def test_slug_exempt_only_in_github_urls(repo, text, ok):
    write(repo, "README.md", text + "\n")
    assert (findings(repo) == []) is ok


def test_configured_slug_overrides_remote(repo):
    write(repo, "cfg/checks.yaml", CONFIG + "  repo_slug: me/frobnitz-x\n")
    write(repo, "README.md", "https://github.com/me/frobnitz-x and "
                             "https://github.com/someone/frobnitz-factory\n")
    assert findings(repo) == ["README.md:1: 'frobnitz'"]  # the slug value itself is blanked


def test_no_remote_no_slug(repo):
    git(repo, "remote", "remove", "origin")
    write(repo, "README.md", "https://github.com/someone/frobnitz-factory\n")
    assert findings(repo) == ["README.md:1: 'frobnitz'"]


def test_untracked_files_scanned_ignored_and_binary_skipped(repo):
    write(repo, ".gitignore", "workdir/\n")
    write(repo, "workdir/log.txt", "frobnitz\n")
    write(repo, "notes.md", "frobnitz\n")  # untracked, not ignored
    (repo / "tools/blob.bin").write_bytes(b"\0frobnitz")
    assert findings(repo) == ["notes.md:1: 'frobnitz'"]


def test_config_file_only_word_list_and_slug_are_blanked(repo):
    assert findings(repo) == []  # the word list itself does not trip the check
    write(repo, "cfg/checks.yaml", CONFIG + "# frobnitz design notes\n")
    assert findings(repo) == ["cfg/checks.yaml:4: 'frobnitz'"]
    write(repo, "cfg/checks.yaml", CONFIG.replace("neutrality:", "neutrality:  # widget bus"))
    assert findings(repo) == ["cfg/checks.yaml:2: 'widget bus'"]
    write(repo, "cfg/checks.yaml", CONFIG)
    write(repo, "cfg/other.yaml", "x: frobnitz\n")
    assert findings(repo) == ["cfg/other.yaml:1: 'frobnitz'"]


def test_exempt_trees_are_fixed(repo):
    write(repo, "docs/x.md", "frobnitz\n")
    write(repo, "wiki/srcx/x.md", "frobnitz\n")  # prefixes are whole directories
    assert findings(repo) == ["docs/x.md:1: 'frobnitz'", "wiki/srcx/x.md:1: 'frobnitz'"]
    write(repo, "cfg/checks.yaml", CONFIG + "  exclude: [docs/]\n")
    with pytest.raises(CheckConfigError, match="optional 'repo_slug' only"):
        neutrality.run(repo)


@pytest.mark.parametrize("text, msg", [
    ("neutrality: {words: [a]}\nextra: 1\n", "only 'neutrality'"),
    ("neutrality: {repo_slug: a/b}\n", "'words' and optional"),
    ("neutrality: {words: [a], note: x}\n", "'words' and optional"),
    ("neutrality: {words: []}\n", "words is empty"),
    ("neutrality: {words: [1]}\n", "neutrality.words must be a list"),
    ("neutrality:\n  words: [a]\n  repo_slug: nope\n", "owner/repo"),
    ("neutrality: {words: [a]}\n", "one flow list on one line"),
    ("neutrality:\n  words:\n    - a\n", "one flow list on one line"),
    ("neutrality:\n  words: [a,\n    b]\n", "one flow list on one line"),
    ("neutrality: [\n", "invalid YAML"),
])
def test_config_errors(repo, text, msg):
    write(repo, "cfg/checks.yaml", text)
    with pytest.raises(CheckConfigError, match=msg):
        neutrality.run(repo)


def test_missing_config(tmp_path):
    with pytest.raises(CheckConfigError):
        neutrality.load_config(tmp_path)


def test_slug_from_remote_forms(repo):
    for url in ("git@github.com:o/r.git", "https://github.com/o/r",
                "http://proxy@127.0.0.1:9/git/o/r"):
        git(repo, "remote", "set-url", "origin", url)
        assert neutrality.slug_from_remote(repo) == "o/r"


def test_real_config_and_repository_is_neutral():
    cfg = neutrality.load_config(REPO_ROOT)
    assert len(set(cfg.words)) == len(cfg.words) >= 5  # target, protocol and terms
    assert neutrality.EXEMPT == ("src/", "wiki/src/", "wiki/tracker/data/")
    r = neutrality.run(REPO_ROOT)
    assert r.passed, r.findings
