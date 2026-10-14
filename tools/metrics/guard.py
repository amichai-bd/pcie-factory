"""The data guard: a nightly data branch may change only the data directory."""

from __future__ import annotations

from pathlib import Path

from .config import Config
from .tree import git


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    """Paths changed between the merge base of ``base`` and ``head``, and ``head``.
    Renames count as a deletion plus an addition, so both paths are checked."""
    out = git(repo, "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}")
    return sorted(p for p in out.decode("utf-8", errors="replace").split("\0") if p)


def check(branch: str, files: list[str], cfg: Config) -> tuple[bool, list[str]]:
    """(passed, report lines) for one head branch and its changed files."""
    if not branch.startswith(cfg.branch_prefix):
        return True, [f"branch {branch!r} is not a {cfg.branch_prefix} branch; nothing to guard"]
    prefix = cfg.data_dir.rstrip("/") + "/"
    if not files:
        return False, [f"{branch}: empty diff; a data branch must change {prefix}"]
    outside = [p for p in files if not p.startswith(prefix)]
    lines = [f"{branch}: {len(files)} changed file(s)"]
    lines += [f"  outside {prefix}: {p}" for p in outside]
    if outside:
        lines.append(f"FAIL: a {cfg.branch_prefix} branch may change only {prefix}**")
        return False, lines
    lines.append(f"PASS: every change is under {prefix}")
    return True, lines
