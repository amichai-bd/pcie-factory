"""Spec-cache check: nothing from the reference cache is committed.

Owning spec: wiki/tools/checks/SPEC.md, "Spec-cache check". Scans every
file git lists (tracked, plus untracked files that are not ignored), like
the publishability check, and flags:

- ``pdf``: a PDF (a ``.pdf`` name, or content starting ``%PDF-``);
- ``cached-file``: a file byte-identical to a cached document or its text;
- ``cached-text``: a text file sharing a run of more than
  ``citation.max_quote_words`` consecutive words with a cached text.

It also requires every id in the reference list (``<src_root>/specs.yaml``)
to be an entry heading on ``wiki/src/references.md``. Without a cache, only
the PDF and reference-list rules apply.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from checks import citation
from checks.common import CheckConfigError, CheckResult, rel
from specs import core

PAGE = "references.md"
_WORD = re.compile(r"[a-z0-9]+")
_HEADING = re.compile(r"^#{2,6}[ \t]+`?([a-z0-9][a-z0-9-]*)`?[ \t]*$", re.MULTILINE)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise CheckConfigError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


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


def run(repo_root: Path, src_root: Path, cache: Path | None = None) -> CheckResult:
    result = CheckResult("spec-cache")
    add = result.findings.append
    n = citation.load_config(repo_root) + 1
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
    for f in files:
        p = Path(repo_root) / f
        if p.is_symlink() or not p.is_file():
            continue
        data = p.read_bytes()
        if f.lower().endswith(".pdf") or data.startswith(b"%PDF-"):
            add(f"{f}: pdf: a PDF document; reference documents are never committed")
            continue
        digest = hashlib.sha256(data).hexdigest()
        if digest in hashes:
            add(f"{f}: cached-file: identical to the cached {hashes[digest]}")
            continue
        if not index or b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        ws = words(text)
        for name, idx in index.items():
            i = first_shared_run(ws, idx, n)
            if i is not None:
                add(f"{f}:{_line_of(text, i)}: cached-text: {n} or more consecutive words "
                    f"match the cached {name}; paraphrase and cite instead")
                break
    result.checked = (f"{len(files)} file(s) (tracked and untracked, not ignored); "
                      f"{len(docs)} listed document(s); {len(cached)} cached file(s) in "
                      f"{core.show(cache) if cached else 'no cache (PDF and list rules only)'}; "
                      f"runs of {n} words")
    return result
