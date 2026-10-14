"""Minimal GitHub REST client over ``gh api`` (no GraphQL).

Tests substitute a fake with the same ``api``/``paginate`` methods.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .config import MetricsError

_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_HTTP = re.compile(r"\(HTTP (\d{3})\)")
PER_PAGE = 100


class GhError(MetricsError):
    """A failed ``gh api`` call; ``status`` is the HTTP code when known."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def resolve_repo(explicit: str | None, repo_root: Path) -> str:
    """``--repo``, then ``$GH_REPO``, then ``$GITHUB_REPOSITORY``, then origin."""
    slug = explicit or os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if not slug:
        try:
            url = subprocess.run(["git", "-C", str(repo_root), "remote", "get-url", "origin"],
                                 capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise MetricsError("no --repo, $GH_REPO or git remote 'origin'") from exc
        m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?/?$", url) or \
            re.search(r"/git/([^/]+/[^/]+?)(?:\.git)?/?$", url)
        if not m:
            raise MetricsError("cannot derive OWNER/NAME from the origin remote; use --repo")
        slug = m.group(1)
    if not _SLUG.match(slug):
        raise MetricsError(f"repository must be OWNER/NAME, got {slug!r}")
    return slug


class Gh:
    def __init__(self, repo: str, exe: str = "gh"):
        self.repo = repo
        self.exe = exe

    def api(self, method: str, path: str, fields: list[tuple[str, str]] | None = None):
        cmd = [self.exe, "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
        for key, value in fields or ():
            cmd += ["-f", f"{key}={value}"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as exc:
            raise GhError(f"cannot run {self.exe}: {exc}") from exc
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip()
            m = _HTTP.search(err)
            status = int(m.group(1)) if m else None
            last = err.splitlines()[-1] if err else "no output"
            raise GhError(f"gh api {method} {path.split('?')[0]} failed: {last}", status)
        if not proc.stdout.strip():
            return None
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GhError(f"gh api {method} {path}: output is not JSON: {exc}") from exc

    def paginate(self, path: str, key: str | None = None, limit: int = 5000) -> list:
        """GET every page of a list endpoint (``key``: the list inside an object)."""
        out, page = [], 1
        sep = "&" if "?" in path else "?"
        while len(out) < limit:
            data = self.api("GET", f"{path}{sep}per_page={PER_PAGE}&page={page}")
            batch = data.get(key, []) if key else data
            out += batch
            if len(batch) < PER_PAGE:
                break
            page += 1
        return out[:limit]
