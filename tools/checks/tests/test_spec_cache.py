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
MAGIC = b"%" + b"PDF-"  # assembled, so this file is not itself flagged as a PDF
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
    write(c, "alpha.pdf", MAGIC + b"1.4 cached\n")
    write(c, "alpha.txt", CACHED_TEXT)
    return c


def run(repo: Path, cache: Path):
    return spec_cache.run(repo, repo / "src", cache)


def test_clean_repo_passes(repo, cache):
    r = run(repo, cache)
    assert r.passed, r.findings
    assert "1 listed document(s); 0 archive allow entry(ies); 2 cached file(s)" in r.checked
    assert r.checked.endswith("runs of 6 words")


def test_any_pdf_fails_with_or_without_cache(repo, tmp_path):
    write(repo, "docs/manual.PDF", b"not really a pdf")
    write(repo, "docs/renamed.bin", MAGIC + b"1.7\n\x00binary")
    (tmp_path / "empty").mkdir()
    for c in (tmp_path / "empty", tmp_path / "absent"):
        r = run(repo, c)
        assert r.findings == [
            "docs/manual.PDF: pdf: a PDF document; reference documents are never committed",
            "docs/renamed.bin: pdf: a PDF document; reference documents are never committed",
        ]
        assert "no cache (PDF, archive and list rules only)" in r.checked


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


def test_pdf_signature_anywhere_in_first_kib(repo, tmp_path):
    write(repo, "docs/late.bin", b"x" * 900 + MAGIC + b"1.5\n")
    write(repo, "docs/beyond.txt", b"y" * 1100 + MAGIC + b" named in prose\n")
    assert run(repo, tmp_path / "absent").findings == [
        "docs/late.bin: pdf: a PDF document; reference documents are never committed"]


@pytest.mark.parametrize("data", [
    CACHED_TEXT.encode("utf-16"),                       # with a byte-order mark
    b"\xfe\xff" + CACHED_TEXT.encode("utf-16-be"),      # big-endian mark
    CACHED_TEXT.encode("utf-16-le"),                    # no mark, clean UTF-16
    CACHED_TEXT.encode("utf-16-be"),
    b"\xef\xbb\xbf" + CACHED_TEXT[:80].encode() + b" end\n",  # UTF-8 with a BOM
    CACHED_TEXT.encode("utf-32"),                       # UTF-32 with a mark
    b"\x00\x00\xfe\xff" + CACHED_TEXT.encode("utf-32-be"),  # big-endian mark
    CACHED_TEXT.encode("utf-32-le"),                    # no mark, clean UTF-32
    CACHED_TEXT.encode("utf-32-be"),
], ids=["utf16-bom", "utf16be-bom", "utf16le", "utf16be", "utf8-bom",
        "utf32-bom", "utf32be-bom", "utf32le", "utf32be"])
def test_utf16_and_bom_text_is_decoded(repo, cache, data):
    write(repo, "notes/wide.txt", data)
    findings = run(repo, cache).findings
    assert len(findings) == 1 and findings[0].startswith("notes/wide.txt:")
    assert "cached-text" in findings[0] or "cached-file" in findings[0]


def test_binary_without_text_is_not_scanned(repo, cache):
    write(repo, "img/logo.bin", b"\x00\x01\x02\xff" * 64 + b"\x00")  # odd length
    assert run(repo, cache).passed


@pytest.mark.parametrize("name, data", [
    ("pkg/docs.zip", b"anything"),
    ("pkg/docs.tar", b"anything"),
    ("pkg/docs.tar.xz", b"anything"),
    ("pkg/docs.tgz", b"anything"),
    ("pkg/docs.gz", b"anything"),
    ("pkg/docs.7z", b"anything"),
    ("pkg/renamed.dat", b"PK\x03\x04rest"),
    ("pkg/renamed2.dat", b"\x1f\x8brest"),
    ("pkg/renamed3.dat", b"7z\xbc\xaf\x27\x1crest"),
    ("pkg/docs.xz", b"anything"),
    ("pkg/docs.bz2", b"anything"),
    ("pkg/docs.zst", b"anything"),
    ("pkg/docs.rar", b"anything"),
    ("pkg/docs.lz4", b"anything"),
    ("pkg/renamed4.dat", b"\xfd7zXZ\x00rest"),
    ("pkg/renamed5.dat", b"BZh91AY&SYrest"),
    ("pkg/renamed6.dat", b"\x28\xb5\x2f\xfdrest"),
    ("pkg/renamed7.dat", b"Rar!\x1a\x07\x01\x00rest"),
    ("pkg/renamed8.dat", b"\x04\x22\x4d\x18rest"),
])
def test_archives_fail_unless_allowed(repo, cache, name, data):
    write(repo, name, data)
    assert run(repo, cache).findings == [
        f"{name}: archive: an archive can hide a reference document; "
        "exempt it in cfg/checks.yaml spec_cache.allow with a reason"]
    write(repo, "cfg/checks.yaml", CONFIG + "spec_cache:\n  allow:\n"
          "    - path: pkg/*\n      reason: test fixture bundle\n")
    r = run(repo, cache)
    assert r.passed, r.findings
    assert "1 archive allow entry(ies)" in r.checked


def test_archive_look_alikes_pass(repo, cache):
    write(repo, "docs/my.target.txt", "a build target\n")
    write(repo, "docs/tarball-notes.md", "notes\n")
    write(repo, "docs/bzh.txt", "BZh9 is how a bzip2 stream starts\n")
    assert run(repo, cache).passed


def test_allow_glob_does_not_cross_folders(repo, cache):
    write(repo, "top.zip", b"anything")
    write(repo, "pkg/nested.zip", b"anything")
    write(repo, "cfg/checks.yaml", CONFIG + "spec_cache:\n  allow:\n"
          "    - path: '*.zip'\n      reason: top-level fixture only\n")
    assert run(repo, cache).findings == [
        "pkg/nested.zip: archive: an archive can hide a reference document; "
        "exempt it in cfg/checks.yaml spec_cache.allow with a reason"]


def test_stale_archive_allow_entry_fails(repo, cache):
    write(repo, "cfg/checks.yaml", CONFIG + "spec_cache:\n  allow:\n"
          "    - path: none/*\n      reason: nothing here\n")
    assert run(repo, cache).findings == [
        "cfg/checks.yaml: spec_cache.allow[0] (none/*): stale, matches no archive"]


@pytest.mark.parametrize("section", [
    "spec_cache: []\n",
    "spec_cache:\n  allow: {}\n",
    "spec_cache:\n  allow:\n    - path: x\n",
    "spec_cache:\n  allow:\n    - path: x\n      reason: ''\n",
    "spec_cache:\n  allow: []\n  extra: 1\n",
])
def test_bad_allow_config_is_an_error(repo, cache, section):
    write(repo, "cfg/checks.yaml", CONFIG + section)
    with pytest.raises(CheckConfigError, match="spec_cache"):
        run(repo, cache)
