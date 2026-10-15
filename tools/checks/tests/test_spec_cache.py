"""spec-cache check: nothing from the reference cache can be committed.

Fixtures are built at run time in a temporary git repository with a fake
cache, so no PDF or document text is ever stored in this repository.
"""

import subprocess
from pathlib import Path

import pytest

from checks import spec_cache
from checks.common import CheckConfigError

CONFIG = ("neutrality:\n  words: [frobnitz]\ncitation:\n  max_quote_words: 5\n"
          "publishability:\n  allow: []\n")
LIST = """documents:
  - id: alpha
    title: Alpha Spec
    revision: "1.0"
    publisher: Publisher
    source: local
    license: internal use only
"""
CACHED_TEXT = ("Chapter one. The quick brown fox jumps over the lazy dog\n"
               "while seven sleepy otters watch from the river bank.\n")


def write(root: Path, rel: str, data) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    (p.write_bytes if isinstance(data, bytes) else p.write_text)(data)


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    write(r, "cfg/checks.yaml", CONFIG)
    write(r, "src/specs.yaml", LIST)
    write(r, "wiki/src/references.md", "# References\n\n## alpha\n\nLinks only.\n")
    write(r, "tools/ok.md", "Our own words about foxes and otters.\n")
    return r


@pytest.fixture
def cache(tmp_path) -> Path:
    c = tmp_path / "cache"
    write(c, "alpha.pdf", b"%PDF-1.4 cached\n")
    write(c, "alpha.txt", CACHED_TEXT)
    return c


def run(repo: Path, cache: Path):
    return spec_cache.run(repo, repo / "src", cache)


def test_clean_repo_passes(repo, cache):
    r = run(repo, cache)
    assert r.passed, r.findings
    assert "1 listed document(s); 2 cached file(s)" in r.checked
    assert r.checked.endswith("runs of 6 words")


def test_any_pdf_fails_with_or_without_cache(repo, tmp_path):
    write(repo, "docs/manual.PDF", b"not really a pdf")
    write(repo, "docs/renamed.bin", b"%PDF-1.7\n\x00binary")
    (tmp_path / "empty").mkdir()
    for c in (tmp_path / "empty", tmp_path / "absent"):
        r = run(repo, c)
        assert r.findings == [
            "docs/manual.PDF: pdf: a PDF document; reference documents are never committed",
            "docs/renamed.bin: pdf: a PDF document; reference documents are never committed",
        ]
        assert "no cache (PDF and list rules only)" in r.checked


def test_extracted_text_file_fails(repo, cache):
    write(repo, "notes/spec.txt", CACHED_TEXT)
    assert run(repo, cache).findings == [
        "notes/spec.txt: cached-file: identical to the cached alpha.txt"]


def test_excerpt_longer_than_a_quote_fails(repo, cache):
    # Six consecutive cached words, across a line break and with other case
    # and punctuation, on line 4.
    write(repo, "wiki/page.md", "# Page\n\nIntro.\nLAZY dog, while seven sleepy -- otters!\n")
    assert run(repo, cache).findings == [
        "wiki/page.md:4: cached-text: 6 or more consecutive words match the cached "
        "alpha.txt; paraphrase and cite instead"]


def test_short_overlap_passes(repo, cache):
    write(repo, "wiki/page.md", "a lazy dog while seven sleepy cats\n")  # five words shared
    assert run(repo, cache).passed


def test_ignored_files_are_not_scanned(repo, cache):
    write(repo, ".gitignore", "/workdir/\n")
    write(repo, "workdir/copy.txt", CACHED_TEXT)
    assert run(repo, cache).passed


def test_listed_document_needs_a_page_entry(repo, cache):
    write(repo, "wiki/src/references.md", "# References\n\nalpha is mentioned, not a heading.\n")
    assert run(repo, cache).findings == [
        "src/specs.yaml: document 'alpha' has no entry heading on wiki/src/references.md"]


def test_bad_list_is_a_config_error(repo, cache):
    write(repo, "src/specs.yaml", "documents: {}\n")
    with pytest.raises(CheckConfigError, match="documents"):
        run(repo, cache)


def test_cache_inside_repo_is_a_config_error(repo, monkeypatch):
    monkeypatch.setenv("BUILD_SPECS_CACHE", str(repo / "cache"))
    with pytest.raises(CheckConfigError, match="inside the repository"):
        spec_cache.run(repo, repo / "src")
