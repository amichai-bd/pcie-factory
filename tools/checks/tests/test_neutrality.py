import subprocess
from pathlib import Path

import pytest

from build.ip import REPO_ROOT
from checks import neutrality
from checks.common import CheckConfigError

# A made-up word list: these tests must not name the real design target.
CONFIG = """neutrality:
  words: [frobnitz, widget bus]
  exclude: [src/, wiki/src/, wiki/tracker/data/]
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
    assert "1 tracked file(s)" in r.checked  # tools/ok.py; cfg/checks.yaml is exempt
    assert "slug exempt in URLs: someone/frobnitz-factory" in r.checked


def test_words_in_tools_and_wiki_fail(repo):
    write(repo, "tools/x.py", "NAME = 'FrobNitz'\n\nbus = 'Widget-Bus'\n")
    write(repo, "wiki/bkm/x.md", "a widget_bus and a  widget \t bus\n")
    assert findings(repo) == [
        "tools/x.py:1: 'FrobNitz'",
        "tools/x.py:3: 'Widget-Bus'",
        "wiki/bkm/x.md:1: 'widget_bus'",
        "wiki/bkm/x.md:1: 'widget \\t bus'",
    ]


def test_path_names_are_scanned(repo):
    write(repo, "tools/frobnitz_util.py", "")
    assert findings(repo) == ["tools/frobnitz_util.py: path contains 'frobnitz'"]


@pytest.mark.parametrize("text, ok", [
    ("see https://github.com/someone/frobnitz-factory/issues/1", True),
    ("git@github.com:someone/frobnitz-factory.git", True),
    ("https://someone.github.io/frobnitz-factory/", True),
    ("https://GitHub.com/Someone/Frobnitz-Factory", True),
    ("https://github.com/someone/frobnitz-factory-extra", False),
    ("https://github.com/other/frobnitz-factory", False),
    ("the frobnitz-factory repository", False),
    ("https://example.com/someone/frobnitz-factory", False),
])
def test_slug_exempt_only_in_github_urls(repo, text, ok):
    write(repo, "README.md", text + "\n")
    assert (findings(repo) == []) is ok


def test_configured_slug_overrides_remote(repo):
    write(repo, "cfg/checks.yaml", CONFIG + "  repo_slug: me/frobnitz-x\n")
    write(repo, "README.md", "https://github.com/me/frobnitz-x and "
                             "https://github.com/someone/frobnitz-factory\n")
    assert findings(repo) == ["README.md:1: 'frobnitz'"]


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


def test_config_file_itself_is_exempt_but_others_in_cfg_are_not(repo):
    write(repo, "cfg/other.yaml", "x: frobnitz\n")
    assert findings(repo) == ["cfg/other.yaml:1: 'frobnitz'"]


@pytest.mark.parametrize("text, msg", [
    ("neutrality: {words: [a], exclude: [src/]}\nextra: 1\n", "only 'neutrality'"),
    ("neutrality: {words: [a]}\n", "'words', 'exclude'"),
    ("neutrality: {words: [a], exclude: [src/], note: frobnitz}\n", "'words', 'exclude'"),
    ("neutrality: {words: [], exclude: [src/]}\n", "words is empty"),
    ("neutrality: {words: [1], exclude: [src/]}\n", "neutrality.words must be a list"),
    ("neutrality: {words: [a], exclude: [src/], repo_slug: nope}\n", "owner/repo"),
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


def test_real_config_contains_words_and_repository_is_neutral():
    cfg = neutrality.load_config(REPO_ROOT)
    assert cfg.words and cfg.exclude == ("src/", "wiki/src/", "wiki/tracker/data/")
    r = neutrality.run(REPO_ROOT)
    assert r.passed, r.findings
