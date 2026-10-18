"""Shared, content-keyed build cache and pass record store
(wiki/tools/build/SPEC.md, "Build cache").

One directory per host, outside every checkout, shared by every run and
worktree of the repository:

    <cache>/builds/<top>-<key>/   a finished simulation executable, its
                                  build.log and meta.json (written last)
    <cache>/passes/<key>.json     a passing run of a reuse-on-unchanged
                                  entry (wiki/tools/regress/SPEC.md,
                                  "Reuse of unchanged model checks")
    <cache>/ccache/               compiler cache (Verilator's OBJCACHE)

Every key is a sha256 of content (sources, settings, tool versions), never
a time, so a reused entry is exactly what a fresh build would make. Entries
are written into a private temporary directory and renamed into place, so
a concurrent reader never sees a partial entry and two writers of the same
key keep one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

ENV = "BUILD_CACHE"         # cache directory, or "off" to disable it
OFF = "off"
SCHEMA = 1                   # bump to invalidate every entry
MAX_AGE_DAYS = 14            # an entry unused this long is pruned
BUILDS, PASSES, CCACHE = "builds", "passes", "ccache"
META = "meta.json"


class CacheError(Exception):
    pass


def main_root(repo_root: Path) -> Path:
    """The main worktree of ``repo_root``'s repository (itself outside
    git), so every worktree of a clone names the same cache."""
    proc = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--path-format=absolute",
                           "--git-common-dir"], capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip()).resolve().parent
    return Path(repo_root).resolve()


def cache_dir(repo_root: Path, env: dict | None = None) -> Path | None:
    """``$BUILD_CACHE`` (``off``: no cache), else
    ``${XDG_CACHE_HOME:-~/.cache}/<repo>/build``; refused inside the
    repository, where a job checkout's clean would delete it."""
    env = os.environ if env is None else env
    value = env.get(ENV, "")
    if value.strip().lower() == OFF:
        return None
    if value:
        path = Path(value).expanduser()
    else:
        base = Path(env.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        path = base / main_root(repo_root).name / "build"
    path = path.resolve()
    for root in {Path(repo_root).resolve(), main_root(repo_root)}:
        if path == root or root in path.parents:
            raise CacheError(f"build cache {path} is inside the repository {root}; "
                             f"set ${ENV} to a directory outside it (or {OFF})")
    return path


def show(path: Path) -> str:
    """``path`` with the home directory written ``~`` (logs are public)."""
    try:
        return "~/" + str(Path(path).relative_to(Path.home()))
    except ValueError:
        return str(path)


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_files(root: Path) -> list[Path]:
    """Regular files under ``root`` (or ``root`` itself), sorted, without
    ``__pycache__``."""
    root = Path(root)
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)


class Hasher:
    """sha256 over named parts; a file is named by its path relative to
    ``base`` (absolute outside it), so the same tree in another worktree
    gives the same key."""

    def __init__(self, base: Path) -> None:
        self.base = Path(base).resolve()
        self.h = hashlib.sha256()

    def name(self, path: Path) -> str:
        path = Path(path).resolve()
        try:
            return str(path.relative_to(self.base))
        except ValueError:
            return str(path)

    def value(self, tag: str, value) -> None:
        self.h.update(json.dumps([tag, value], sort_keys=True).encode() + b"\n")

    def file(self, tag: str, path: Path) -> None:
        self.value(tag, [self.name(path), file_digest(path) if Path(path).is_file() else None])

    def hexdigest(self) -> str:
        return self.h.hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}")
    tmp.write_text(text)
    os.replace(tmp, path)


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        try:
            for sub in (BUILDS, PASSES, CCACHE):
                (self.root / sub).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CacheError(f"cannot create build cache {self.root}: {exc.strerror}") from None
        if not os.access(self.root, os.W_OK):
            raise CacheError(f"build cache {self.root} is not writable")

    @property
    def ccache_dir(self) -> Path:
        return self.root / CCACHE

    # ------------------------------------------------------------- builds

    def entry(self, top: str, key: str) -> Path:
        return self.root / BUILDS / f"{top}-{key[:24]}"

    def lookup(self, top: str, key: str) -> dict | None:
        """The finished entry's meta (marking it used), or None."""
        meta = self.entry(top, key) / META
        try:
            data = json.loads(meta.read_text())
        except (OSError, ValueError):
            return None
        if data.get("key") != key or data.get("schema") != SCHEMA:
            return None
        try:
            os.utime(meta)  # last use, for pruning
        except OSError:
            pass
        return data

    def store(self, top: str, key: str, files: list[Path], meta: dict) -> Path:
        """Copy ``files`` into the entry for ``key`` atomically and return
        it; when another writer got there first, keep theirs."""
        final = self.entry(top, key)
        tmp = final.with_name(f".tmp-{final.name}-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        tmp.mkdir(parents=True)
        try:
            for f in files:
                shutil.copy2(f, tmp / Path(f).name)
            (tmp / META).write_text(json.dumps({**meta, "key": key, "schema": SCHEMA},
                                               indent=2, sort_keys=True) + "\n")
            try:
                os.rename(tmp, final)
            except OSError:
                if (final / META).is_file():  # a concurrent writer won
                    shutil.rmtree(tmp, ignore_errors=True)
                else:
                    raise
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return final

    # ------------------------------------------------------------- passes

    def pass_record(self, key: str) -> dict | None:
        try:
            data = json.loads((self.root / PASSES / f"{key}.json").read_text())
        except (OSError, ValueError):
            return None
        return data if data.get("key") == key and data.get("schema") == SCHEMA else None

    def record_pass(self, key: str, meta: dict) -> None:
        _atomic_write(self.root / PASSES / f"{key}.json",
                      json.dumps({**meta, "key": key, "schema": SCHEMA}, sort_keys=True) + "\n")

    # ------------------------------------------------------------ pruning

    def prune(self, max_age_days: float = MAX_AGE_DAYS, now: float | None = None) -> int:
        """Remove build entries and pass records unused for
        ``max_age_days``, and temporary directories older than a day (a
        killed writer's); return how many were removed."""
        now = time.time() if now is None else now
        removed = 0
        for d in (self.root / BUILDS).iterdir():
            mark = d / META
            try:
                age = now - (mark.stat().st_mtime if mark.is_file() else d.stat().st_mtime)
            except OSError:
                continue
            limit = 86400 if d.name.startswith(".tmp-") else max_age_days * 86400
            if age > limit:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        for f in (self.root / PASSES).glob("*.json"):
            try:
                if now - f.stat().st_mtime > max_age_days * 86400:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        return removed


def from_option(repo_root: Path, disabled: bool = False) -> Cache | None:
    """The cache for this run: None with ``--no-cache`` or
    ``$BUILD_CACHE=off``."""
    if disabled:
        return None
    root = cache_dir(repo_root)
    return Cache(root) if root is not None else None
