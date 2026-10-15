"""Spec-cache check: nothing from the reference cache is committed.

Owning spec: wiki/tools/checks/SPEC.md, "Spec-cache check". Scans every
file git lists (tracked, plus untracked files that are not ignored), like
the publishability check, and flags:

- ``pdf``: a PDF (a ``.pdf`` name, or the PDF signature in its first 1 KiB);
- ``archive``: a ``.zip``, ``.gz``, ``.tgz``, ``.7z``, ``.xz``, ``.bz2``,
  ``.zst``, ``.rar``, ``.lz4`` or ``.tar*`` file, or one with the signature
  of any of these formats, unless ``spec_cache.allow`` in
  ``cfg/checks.yaml`` exempts its path with a reason (an archive can hide a
  document from every other rule). An allow glob matches whole path
  segments: ``*`` never crosses a ``/``;
- ``cached-file``: a file byte-identical to a cached document or its text;
- ``cached-text``: a text file sharing a run of more than
  ``citation.max_quote_words`` consecutive words with a cached text. UTF-8
  files are read as such; a file with a UTF-32 or UTF-16 byte-order mark,
  or one with NUL bytes that decodes cleanly as UTF-32 or UTF-16, is
  decoded first.

It also requires every id in the reference list (``<src_root>/specs.yaml``)
to be an entry heading on ``wiki/src/references.md``. Without a cache, only
the PDF, archive and reference-list rules apply.
"""

from __future__ import annotations

import codecs
import hashlib
import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from checks import citation
from checks.common import CheckConfigError, CheckResult, rel
from specs import core

CONFIG = "cfg/checks.yaml"
PAGE = "references.md"
PDF_WINDOW = 1024
# The PDF signature, assembled so that this file does not carry it.
PDF_MAGIC = b"%" + b"PDF-"
# Compressed and archive names, and .tar alone or with a compression suffix.
_ARCHIVE_NAME = re.compile(
    r"\.(?:zip|gz|tgz|7z|xz|bz2|zst|rar|lz4|tar(?:\.[a-z0-9]+)?)$")
ARCHIVE_MAGIC = (
    b"PK\x03\x04", b"PK\x05\x06",       # zip
    b"\x1f\x8b",                         # gzip
    b"7z\xbc\xaf\x27\x1c",                 # 7z
    b"\xfd7zXZ\x00",                      # xz
    b"\x28\xb5\x2f\xfd",                   # zstd
    b"Rar!\x1a\x07",                      # rar (4 and 5)
    b"\x04\x22\x4d\x18",                   # lz4 frame
)
# bzip2: "BZh", a block size digit, then a block or end-of-stream marker,
# so a text file that starts with "BZh" is not an archive.
_BZIP2 = re.compile(rb"BZh[1-9](?:1AY&SY|\x17rE8P\x90)")
_WORD = re.compile(r"[a-z0-9]+")
_HEADING = re.compile(r"^#{2,6}[ \t]+`?([a-z0-9][a-z0-9-]*)`?[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Allow:
    path: str
    reason: str


def load_config(repo_root: Path, path: str = CONFIG) -> tuple[Allow, ...]:
    """The ``spec_cache.allow`` entries; no section means none."""
    p = Path(repo_root) / path
    try:
        data = yaml.safe_load(p.read_text())
    except OSError as exc:
        raise CheckConfigError(f"{p}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise CheckConfigError(f"{p}: invalid YAML: {exc}") from None
    sec = data.get("spec_cache") if isinstance(data, dict) else None
    if sec is None:
        return ()
    if not isinstance(sec, dict) or set(sec) != {"allow"}:
        raise CheckConfigError(f"{p}: 'spec_cache' must be a mapping with only 'allow'")
    entries = [] if sec["allow"] is None else sec["allow"]
    if not isinstance(entries, list):
        raise CheckConfigError(f"{p}: spec_cache.allow must be a list")
    out = []
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or set(e) != {"path", "reason"} or not all(
                isinstance(e[k], str) and e[k].strip() for k in ("path", "reason")):
            raise CheckConfigError(f"{p}: spec_cache.allow[{i}]: needs a non-empty "
                                   "'path' glob and 'reason', only")
        out.append(Allow(e["path"], e["reason"].strip()))
    return tuple(out)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise CheckConfigError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def is_pdf(path: str, data: bytes) -> bool:
    return path.lower().endswith(".pdf") or PDF_MAGIC in data[:PDF_WINDOW]


def is_archive(path: str, data: bytes) -> bool:
    name = path.lower().rsplit("/", 1)[-1]
    return (bool(_ARCHIVE_NAME.search(name)) or data.startswith(ARCHIVE_MAGIC)
            or bool(_BZIP2.match(data)))


def allowed(path: str, pattern: str) -> bool:
    """``path`` matches the fnmatch ``pattern`` segment by segment, so
    ``*`` never crosses a ``/`` and ``*.zip`` matches only a top-level zip."""
    ps, gs = path.split("/"), pattern.split("/")
    return len(ps) == len(gs) and all(fnmatchcase(p, g) for p, g in zip(ps, gs))


def texts(data: bytes) -> list[str]:
    """The readings of ``data`` to scan for words: UTF-8 (or UTF-8 with a
    BOM); UTF-32 or UTF-16 with a BOM; or, for data with NUL bytes, each
    UTF-32 and UTF-16 byte order that decodes cleanly. Empty for other
    binary data."""
    if data.startswith(codecs.BOM_UTF8):
        return [data[3:].decode("utf-8", errors="replace")]
    # Before UTF-16: the UTF-32 LE mark starts with the UTF-16 LE one.
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return [data.decode("utf-32", errors="replace")]
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return [data.decode("utf-16", errors="replace")]
    if b"\0" not in data:
        return [data.decode("utf-8", errors="replace")]
    out = []
    encs = ("utf-16-le", "utf-16-be") if len(data) % 2 == 0 else ()
    if len(data) % 4 == 0:
        encs = ("utf-32-le", "utf-32-be") + encs
    for enc in encs:
        try:
            out.append(data.decode(enc))
        except UnicodeDecodeError:
            pass
    return out


def words(text: str) -> list[str]:
    """Lower-case alphanumeric words: layout, case and punctuation ignored."""
    return _WORD.findall(text.lower())


def runs(ws: list[str], n: int) -> set[int]:
    """Hashes of every run of ``n`` consecutive words."""
    return {hash(" ".join(ws[i:i + n])) for i in range(len(ws) - n + 1)}


def first_shared_run(ws: list[str], index: set[int], n: int) -> int | None:
    """Index of the first word of a run of ``n`` words found in ``index``."""
    for i in range(len(ws) - n + 1):
        if hash(" ".join(ws[i:i + n])) in index:
            return i
    return None


def _line_of(text: str, word_index: int) -> int:
    for k, m in enumerate(_WORD.finditer(text.lower())):
        if k == word_index:
            return text.count("\n", 0, m.start()) + 1
    return 1


def page_ids(page: Path) -> set[str]:
    try:
        return set(_HEADING.findall(page.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError):
        return set()


def run(repo_root: Path, src_root: Path, cache: Path | None = None,
        config_path: str = CONFIG) -> CheckResult:
    result = CheckResult("spec-cache")
    add = result.findings.append
    n = citation.load_config(repo_root, config_path) + 1
    allow = load_config(repo_root, config_path)
    try:
        docs = core.load_list(src_root)
        cache = core.cache_dir(repo_root) if cache is None else cache
    except core.SpecsError as exc:
        raise CheckConfigError(str(exc)) from None

    page = Path(src_root).resolve().parent / "wiki" / "src" / PAGE
    ids = page_ids(page)
    listed = rel(Path(src_root).resolve() / core.LIST_FILE, Path(repo_root).resolve())
    for d in docs:
        if d.id not in ids:
            add(f"{listed}: document {d.id!r} has no entry heading on wiki/src/{PAGE}")

    cached = core.cached_files(cache)
    hashes = {core.sha256_file(p): p.name for p in cached}
    index: dict[str, set[int]] = {}
    for p in cached:
        if p.suffix == ".txt":
            index[p.name] = runs(words(p.read_text(encoding="utf-8", errors="replace")), n)

    files = sorted({f for f in _git(repo_root, "ls-files", "-z", "--cached", "--others",
                                    "--exclude-standard").split("\0") if f})
    used: set[int] = set()
    for f in files:
        p = Path(repo_root) / f
        if p.is_symlink() or not p.is_file():
            continue
        data = p.read_bytes()
        if is_pdf(f, data):
            add(f"{f}: pdf: a PDF document; reference documents are never committed")
            continue
        if is_archive(f, data):
            hit = [i for i, e in enumerate(allow) if allowed(f, e.path)]
            used.update(hit)
            if not hit:
                add(f"{f}: archive: an archive can hide a reference document; "
                    f"exempt it in {config_path} spec_cache.allow with a reason")
                continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in hashes:
            add(f"{f}: cached-file: identical to the cached {hashes[digest]}")
            continue
        if not index:
            continue
        for text in texts(data):
            ws = words(text)
            found = next(((name, i) for name, idx in index.items()
                          if (i := first_shared_run(ws, idx, n)) is not None), None)
            if found:
                add(f"{f}:{_line_of(text, found[1])}: cached-text: {n} or more consecutive "
                    f"words match the cached {found[0]}; paraphrase and cite instead")
                break
    for i, e in enumerate(allow):
        if i not in used:
            add(f"{config_path}: spec_cache.allow[{i}] ({e.path}): stale, matches no archive")
    result.checked = (f"{len(files)} file(s) (tracked and untracked, not ignored); "
                      f"{len(docs)} listed document(s); {len(allow)} archive allow entry(ies); "
                      f"{len(cached)} cached file(s) in "
                      f"{core.show(cache) if cached else 'no cache (PDF, archive and list rules only)'}; "
                      f"runs of {n} words")
    return result
