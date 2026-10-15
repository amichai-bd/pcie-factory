"""Reference list, cache location, fetch and status (wiki/tools/specs/SPEC.md).

The reference list is ``<src_root>/specs.yaml``. Documents are cached
outside the repository, verified by sha256, with their text extracted next
to them. Nothing here ever writes inside the repository.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

LIST_FILE = "specs.yaml"
ENV_CACHE = "BUILD_SPECS_CACHE"
ENV_LOCAL = "BUILD_SPECS_LOCAL"
LOCAL = "local"
TEXT_TOOL = "pdftotext"
MAX_BYTES = 256 * 1024 * 1024
# URL schemes a listed source may use. Only the publisher's https:// URL is
# allowed; tests add "file://" here to serve documents without a network.
SCHEMES = ("https://",)
_KEYS = {"id", "title", "revision", "publisher", "source", "sha256", "license"}
_ID = re.compile(r"\A[a-z0-9][a-z0-9-]*\Z")
_SHA = re.compile(r"\A[0-9a-f]{64}\Z")

# Statuses `list` reports.
PRESENT, NO_TEXT, MISSING, NOT_PROVIDED, MISMATCH = (
    "present", "present-no-text", "missing", "local-not-provided", "mismatch")


class SpecsError(Exception):
    """Invalid reference list or cache location (exit 2)."""


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    revision: str
    publisher: str
    source: str          # an https:// or file:// URL, or "local"
    sha256: str | None   # required for a URL; optional for a local document
    license: str

    @property
    def local(self) -> bool:
        return self.source == LOCAL


def load_list(src_root: Path) -> list[Doc]:
    """The documents of ``<src_root>/specs.yaml``; [] when the file is absent."""
    p = Path(src_root) / LIST_FILE
    if not p.is_file():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecsError(f"{p}: {exc}") from None
    except yaml.YAMLError as exc:
        raise SpecsError(f"{p}: invalid YAML: {exc}") from None
    if not isinstance(data, dict) or set(data) != {"documents"} \
            or not isinstance(data["documents"], list):
        raise SpecsError(f"{p}: top level must be a mapping with only a 'documents' list")
    docs, seen = [], set()
    for i, e in enumerate(data["documents"]):
        where = f"{p}: documents[{i}]"
        if not isinstance(e, dict) or not (_KEYS - {"sha256"}) <= set(e) <= _KEYS:
            raise SpecsError(f"{where}: needs {', '.join(sorted(_KEYS - {'sha256'}))} "
                             "and optionally sha256, only")
        for k in _KEYS & set(e):
            if not isinstance(e[k], str) or not e[k].strip():
                raise SpecsError(f"{where}: '{k}' must be a non-empty string")
        if not _ID.match(e["id"]) or e["id"] in seen:
            raise SpecsError(f"{where}: id {e['id']!r} must be unique, lower case, "
                             "letters, digits and '-'")
        seen.add(e["id"])
        src, sha = e["source"].strip(), e.get("sha256")
        if src != LOCAL and not src.startswith(SCHEMES):
            raise SpecsError(f"{where}: source must be one of {', '.join(SCHEMES)} "
                             "(a URL) or 'local'")
        if sha is not None and not _SHA.match(sha):
            raise SpecsError(f"{where}: sha256 must be 64 lower-case hex digits")
        if src != LOCAL and sha is None:
            raise SpecsError(f"{where}: a downloaded document needs a pinned sha256")
        docs.append(Doc(e["id"], e["title"].strip(), e["revision"].strip(),
                        e["publisher"].strip(), src, sha, e["license"].strip()))
    return docs


def _git_main_root(repo_root: Path) -> Path:
    """The main worktree of ``repo_root``'s repository (itself for a plain
    clone or outside git), so every worktree shares one cache."""
    proc = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--path-format=absolute",
                           "--git-common-dir"], capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve().parent
    return Path(repo_root).resolve()


def _outside(path: Path, repo_root: Path, what: str) -> Path:
    path = Path(path).expanduser().resolve()
    for root in {Path(repo_root).resolve(), _git_main_root(repo_root)}:
        if path == root or root in path.parents:
            raise SpecsError(f"{what} {path} is inside the repository {root}; "
                             "it must be outside it, so that nothing cached can be committed")
    return path


def cache_dir(repo_root: Path, env: dict | None = None) -> Path:
    """``$BUILD_SPECS_CACHE``, else ``${XDG_CACHE_HOME:-~/.cache}/<repo>/specs``."""
    env = os.environ if env is None else env
    if env.get(ENV_CACHE):
        return _outside(Path(env[ENV_CACHE]), repo_root, ENV_CACHE)
    base = Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return _outside(base / _git_main_root(repo_root).name / "specs", repo_root, "cache")


def local_dir(repo_root: Path, cache: Path, env: dict | None = None) -> Path:
    """Where a captain-supplied copy ``<id>.pdf`` is read from."""
    env = os.environ if env is None else env
    if env.get(ENV_LOCAL):
        return _outside(Path(env[ENV_LOCAL]), repo_root, ENV_LOCAL)
    return cache / "local"


def show(path: Path) -> str:
    """``path`` with the home directory written ``~``, so that logs (public
    CI logs included) never name a user's home."""
    path, home = Path(path), Path.home()
    try:
        return "~/" + str(path.relative_to(home))
    except ValueError:
        return str(path)


def pdf_path(cache: Path, doc: Doc) -> Path:
    return cache / f"{doc.id}.pdf"


def text_path(cache: Path, doc: Doc) -> Path:
    return cache / f"{doc.id}.txt"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cached_files(cache: Path) -> list[Path]:
    """Every cached document and extracted text (not the local inbox)."""
    if not cache.is_dir():
        return []
    return sorted(p for p in cache.iterdir()
                  if p.is_file() and p.suffix in (".pdf", ".txt") and not p.name.startswith("."))


def status(doc: Doc, cache: Path) -> str:
    pdf = pdf_path(cache, doc)
    if not pdf.is_file():
        return NOT_PROVIDED if doc.local else MISSING
    if doc.sha256 and sha256_file(pdf) != doc.sha256:
        return MISMATCH
    return PRESENT if text_path(cache, doc).is_file() else NO_TEXT


Extractor = Callable[[Path, Path], None]


def pdftotext(pdf: Path, txt: Path) -> None:
    """Extract ``pdf``'s text to ``txt`` with ``pdftotext``; raises
    FileNotFoundError when it is not installed, RuntimeError on failure."""
    tool = shutil.which(TEXT_TOOL)
    if tool is None:
        raise FileNotFoundError(f"{TEXT_TOOL} not found on PATH")
    proc = subprocess.run([tool, "-enc", "UTF-8", str(pdf), str(txt)],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"{TEXT_TOOL} exit {proc.returncode}: {proc.stderr.strip()[:200]}")


def extractor_version() -> str:
    """``pdftotext <version>``, or a note that it is missing (for the log)."""
    tool = shutil.which(TEXT_TOOL)
    if tool is None:
        return f"{TEXT_TOOL} not found"
    try:
        proc = subprocess.run([tool, "-v"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{TEXT_TOOL} ({exc})"
    for line in (proc.stderr + proc.stdout).splitlines():
        if "version" in line:
            return f"{TEXT_TOOL} {line.split('version', 1)[1].strip()}"
    return f"{TEXT_TOOL} (version unknown)"


def _download(url: str, dest: Path, timeout: float) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "build-specs-fetch"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = 0
        for block in iter(lambda: r.read(1 << 20), b""):
            total += len(block)
            if total > MAX_BYTES:
                raise OSError(f"larger than {MAX_BYTES} bytes")
            f.write(block)


def _extract(doc: Doc, cache: Path, extractor: Extractor, say) -> None:
    pdf, txt = pdf_path(cache, doc), text_path(cache, doc)
    if txt.is_file() and txt.stat().st_mtime >= pdf.stat().st_mtime:
        return
    part = cache / f".{doc.id}.txt.part"
    try:
        extractor(pdf, part)
        part.replace(txt)
        say(f"{doc.id}: text extracted to {show(txt)}")
    except FileNotFoundError as exc:
        say(f"{doc.id}: text not extracted ({exc})")
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        say(f"{doc.id}: text not extracted ({exc})")
    finally:
        part.unlink(missing_ok=True)


def fetch(docs: list[Doc], cache: Path, local: Path, extractor: Extractor | None = None,
          timeout: float = 30.0, out=print) -> int:
    """Bring every document into ``cache``. Returns 1 when a document failed
    its sha256 pin (never cached), else 0: an unreachable download or a
    local document not provided is reported, not failed."""
    extractor = extractor or pdftotext
    cache.mkdir(parents=True, exist_ok=True)
    bad = 0
    for doc in docs:
        pdf = pdf_path(cache, doc)
        if pdf.is_file() and doc.sha256 and sha256_file(pdf) != doc.sha256:
            pdf.unlink()  # a stale or replaced copy: re-acquire it below
            text_path(cache, doc).unlink(missing_ok=True)
        part = cache / f".{doc.id}.pdf.part"
        if doc.local:
            given = local / f"{doc.id}.pdf"
            if given.is_file() and not (pdf.is_file()
                                        and sha256_file(given) == sha256_file(pdf)):
                shutil.copyfile(given, part)
                origin = show(given)
            elif pdf.is_file():
                origin = None  # unchanged: neither copied nor extracted again
            else:
                out(f"{doc.id}: {NOT_PROVIDED}: place your copy at {show(given)}")
                continue
        elif pdf.is_file():
            origin = None
        else:
            try:
                _download(doc.source, part, timeout)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                part.unlink(missing_ok=True)
                out(f"{doc.id}: unavailable, skipped ({getattr(exc, 'reason', exc)})")
                continue
            origin = doc.source
        if origin is not None:
            got = sha256_file(part)
            if doc.sha256 and got != doc.sha256:
                part.unlink()
                out(f"ERROR {doc.id}: sha256 mismatch for {origin}: got {got}, "
                    f"pinned {doc.sha256}; not cached")
                bad += 1
                continue
            part.replace(pdf)
            text_path(cache, doc).unlink(missing_ok=True)
            pin = "matches the pin" if doc.sha256 else "unpinned"
            out(f"{doc.id}: cached {show(pdf)} (sha256 {got}, {pin})")
        else:
            out(f"{doc.id}: already cached {show(pdf)}")
        _extract(doc, cache, extractor, out)
    return 1 if bad else 0
