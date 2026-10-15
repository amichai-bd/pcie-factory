"""./build specs: reference list, cache location, fetch, status and CLI.

No network: documents are served from file:// URLs, and text extraction is
a stub, so pdftotext need not be installed.
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from cli.main import main
from specs import core

MAGIC = b"%" + b"PDF-"  # assembled, so this file is not itself flagged as a PDF
PDF = MAGIC + b"1.4\nfake reference document\n%%EOF\n"
SHA = hashlib.sha256(PDF).hexdigest()


def fake_extract(pdf: Path, txt: Path) -> None:
    txt.write_text(f"text of {pdf.name}\n")


def no_tool(pdf: Path, txt: Path) -> None:
    raise FileNotFoundError("pdftotext not found on PATH")


def doc(source: str, sha: str | None = SHA, id: str = "alpha") -> core.Doc:
    return core.Doc(id, "Alpha Spec", "1.0", "Publisher", source, sha, "internal use only")


@pytest.fixture
def served(tmp_path) -> str:
    p = tmp_path / "served" / "alpha.pdf"
    p.parent.mkdir()
    p.write_bytes(PDF)
    return p.as_uri()


def fetch(docs, tmp_path, extractor=fake_extract):
    lines = []
    code = core.fetch(docs, tmp_path / "cache", tmp_path / "inbox", extractor,
                      timeout=5, out=lines.append)
    return code, lines


# --- reference list -------------------------------------------------------

LIST = """documents:
  - id: alpha
    title: Alpha Spec
    revision: "1.0"
    publisher: Publisher
    source: https://example.com/alpha.pdf
    sha256: {sha}
    license: internal use only
  - id: beta
    title: Beta Spec
    revision: "2"
    publisher: Body
    source: local
    license: members only
"""


def test_list_absent_is_empty(tmp_path):
    assert core.load_list(tmp_path) == []


def test_list_parses(tmp_path):
    (tmp_path / "specs.yaml").write_text(LIST.format(sha=SHA))
    a, b = core.load_list(tmp_path)
    assert (a.id, a.sha256, a.local) == ("alpha", SHA, False)
    assert (b.id, b.sha256, b.local) == ("beta", None, True)


@pytest.mark.parametrize("bad, message", [
    ("documents: {}\n", "only a 'documents' list"),
    (LIST.format(sha="abc"), "64 lower-case hex"),
    (LIST.format(sha=SHA).replace("    sha256: " + SHA + "\n", ""), "needs a pinned sha256"),
    (LIST.format(sha=SHA).replace("https://example.com", "http://example.com"), "one of https://"),
    (LIST.format(sha=SHA).replace("https://example.com/alpha.pdf", "file:///tmp/alpha.pdf"),
     "one of https://"),
    (LIST.format(sha=SHA).replace("id: beta", "id: alpha"), "must be unique"),
    (LIST.format(sha=SHA).replace("    license: members only\n", ""), "needs"),
    (LIST.format(sha=SHA) + "    extra: x\n", "needs"),
    ("documents: [\n", "invalid YAML"),
])
def test_list_rejects_malformed(tmp_path, bad, message):
    (tmp_path / "specs.yaml").write_text(bad)
    with pytest.raises(core.SpecsError, match=message):
        core.load_list(tmp_path)


# --- cache location -------------------------------------------------------

def test_cache_default_is_outside_repo_and_named_after_it(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    env = {"XDG_CACHE_HOME": str(tmp_path / "xdg")}
    assert core.cache_dir(repo, env) == (tmp_path / "xdg" / "myrepo" / "specs").resolve()
    assert core.local_dir(repo, core.cache_dir(repo, env), env) == \
        (tmp_path / "xdg" / "myrepo" / "specs" / "local").resolve()


def test_cache_env_override_and_inside_repo_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "elsewhere"
    assert core.cache_dir(repo, {core.ENV_CACHE: str(out)}) == out.resolve()
    with pytest.raises(core.SpecsError, match="inside the repository"):
        core.cache_dir(repo, {core.ENV_CACHE: str(repo / "cache")})
    with pytest.raises(core.SpecsError, match="inside the repository"):
        core.local_dir(repo, out, {core.ENV_LOCAL: str(repo)})


def test_file_urls_only_when_a_test_allows_them(tmp_path, monkeypatch):
    (tmp_path / "specs.yaml").write_text(
        LIST.format(sha=SHA).replace("https://example.com/alpha.pdf", "file:///tmp/alpha.pdf"))
    monkeypatch.setattr(core, "SCHEMES", ("https://", "file://"))
    assert core.load_list(tmp_path)[0].source == "file:///tmp/alpha.pdf"


def test_show_hides_home():
    assert core.show(Path.home() / "x" / "y") == "~/x/y"
    assert core.show(Path("/opt/x")) == "/opt/x"


# --- fetch and status -----------------------------------------------------

def test_fetch_downloads_verifies_and_extracts(tmp_path, served):
    d = doc(served)
    code, lines = fetch([d], tmp_path)
    cache = tmp_path / "cache"
    assert code == 0
    assert (cache / "alpha.pdf").read_bytes() == PDF
    assert (cache / "alpha.txt").read_text() == "text of alpha.pdf\n"
    assert "matches the pin" in lines[0] and "text extracted" in lines[1]
    assert core.status(d, cache) == core.PRESENT
    # A second run keeps the verified copy and its text.
    code, lines = fetch([d], tmp_path)
    assert code == 0 and lines == [f"alpha: already cached {cache / 'alpha.pdf'}"]


def test_fetch_rejects_a_sha256_mismatch(tmp_path, served):
    d = doc(served, sha="0" * 64)
    code, lines = fetch([d], tmp_path)
    assert code == 1
    assert lines[0].startswith("ERROR alpha: sha256 mismatch") and SHA in lines[0]
    assert core.cached_files(tmp_path / "cache") == []
    assert core.status(d, tmp_path / "cache") == core.MISSING


def test_fetch_unreachable_is_skipped_not_failed(tmp_path):
    code, lines = fetch([doc((tmp_path / "nowhere.pdf").as_uri())], tmp_path)
    assert code == 0 and lines[0].startswith("alpha: unavailable, skipped")
    assert core.cached_files(tmp_path / "cache") == []


def test_fetch_without_extractor_keeps_document(tmp_path, served):
    d = doc(served)
    code, lines = fetch([d], tmp_path, no_tool)
    assert code == 0 and "text not extracted (pdftotext not found" in lines[1]
    assert core.status(d, tmp_path / "cache") == core.NO_TEXT


def test_tampered_cache_is_a_mismatch_and_refetched(tmp_path, served):
    d = doc(served)
    fetch([d], tmp_path)
    (tmp_path / "cache" / "alpha.pdf").write_bytes(MAGIC + b"changed")
    assert core.status(d, tmp_path / "cache") == core.MISMATCH
    code, _ = fetch([d], tmp_path)
    assert code == 0 and core.status(d, tmp_path / "cache") == core.PRESENT


def test_local_document_missing_then_provided(tmp_path):
    d = doc(core.LOCAL, sha=None, id="beta")
    code, lines = fetch([d], tmp_path)
    assert code == 0 and lines == [
        f"beta: local-not-provided: place your copy at {tmp_path / 'inbox' / 'beta.pdf'}"]
    assert core.status(d, tmp_path / "cache") == core.NOT_PROVIDED
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "beta.pdf").write_bytes(PDF)
    code, lines = fetch([d], tmp_path)
    assert code == 0 and "unpinned" in lines[0]
    assert core.status(d, tmp_path / "cache") == core.PRESENT


def test_unchanged_local_document_is_not_copied_or_extracted_again(tmp_path):
    d = doc(core.LOCAL, sha=None, id="beta")
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "beta.pdf").write_bytes(PDF)
    calls = []

    def counting(pdf, txt):
        calls.append(pdf)
        fake_extract(pdf, txt)

    fetch([d], tmp_path, counting)
    mtime = (tmp_path / "cache" / "beta.pdf").stat().st_mtime_ns
    code, lines = fetch([d], tmp_path, counting)
    assert code == 0 and len(calls) == 1
    assert lines == [f"beta: already cached {tmp_path / 'cache' / 'beta.pdf'}"]
    assert (tmp_path / "cache" / "beta.pdf").stat().st_mtime_ns == mtime
    # A changed local copy replaces the cached one and is extracted again.
    (tmp_path / "inbox" / "beta.pdf").write_bytes(PDF + b"v2\n")
    code, lines = fetch([d], tmp_path, counting)
    assert code == 0 and len(calls) == 2 and "unpinned" in lines[0]


def test_local_document_with_wrong_pin_fails(tmp_path):
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "beta.pdf").write_bytes(PDF)
    code, lines = fetch([doc(core.LOCAL, sha="1" * 64, id="beta")], tmp_path)
    assert code == 1 and "sha256 mismatch" in lines[0]


# --- command line ---------------------------------------------------------

@pytest.fixture
def src(tmp_path, served, monkeypatch) -> Path:
    monkeypatch.setattr(core, "SCHEMES", ("https://", "file://"))
    s = tmp_path / "src"
    s.mkdir()
    (s / "specs.yaml").write_text(
        LIST.format(sha=SHA).replace("https://example.com/alpha.pdf", served))
    return s


def test_cli_list_path_and_fetch(tmp_path, src, capsys, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv(core.ENV_CACHE, str(cache))
    monkeypatch.setattr(core, "pdftotext", fake_extract)
    assert main(["specs", "list", "--src-root", str(src)]) == 0
    out = capsys.readouterr().out
    assert "alpha  missing" in out and "beta   local-not-provided" in out
    assert main(["specs", "path", "alpha", "--src-root", str(src)]) == 1
    assert main(["specs", "fetch", "--id", "alpha", "--src-root", str(src)]) == 0
    capsys.readouterr()
    assert main(["specs", "path", "alpha", "--src-root", str(src)]) == 0
    assert capsys.readouterr().out.strip() == str(cache / "alpha.txt")
    assert main(["specs", "path", "--src-root", str(src)]) == 0
    assert capsys.readouterr().out.strip() == str(cache)
    assert main(["specs", "path", "alpha", "--absolute", "--src-root", str(src)]) == 0
    assert capsys.readouterr().out.strip() == str(cache / "alpha.txt")


def test_cli_path_shows_home_as_tilde(src, capsys, monkeypatch):
    monkeypatch.setenv(core.ENV_CACHE, str(Path.home() / ".cache" / "x-specs-test"))
    assert main(["specs", "path", "--src-root", str(src)]) == 0
    assert capsys.readouterr().out.strip() == "~/.cache/x-specs-test"
    assert main(["specs", "path", "--absolute", "--src-root", str(src)]) == 0
    assert capsys.readouterr().out.strip() == str(Path.home() / ".cache" / "x-specs-test")
    assert main(["specs", "fetch", "--id", "gamma", "--src-root", str(src)]) == 2


def test_cli_rejects_cache_inside_repo(src, monkeypatch, capsys):
    from build.ip import REPO_ROOT
    monkeypatch.setenv(core.ENV_CACHE, str(REPO_ROOT / "workdir" / "specs"))
    assert main(["specs", "list", "--src-root", str(src)]) == 2
    assert "inside the repository" in capsys.readouterr().err
