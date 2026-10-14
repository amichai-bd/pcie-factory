"""Design-neutrality check (CLAUDE.md, "Design neutrality").

Scans every git-tracked (and untracked, not ignored) file outside the excluded trees for the word list in
``cfg/checks.yaml``, case-insensitively, as substrings; a space in a word
matches any run of spaces, ``-`` or ``_``. File paths are scanned as well as
contents; binary files (a NUL byte) are skipped.

Exemptions:
- the excluded path prefixes in ``cfg/checks.yaml`` (``src/``, ``wiki/src/``,
  ``wiki/tracker/data/``);
- the repository slug inside a GitHub URL: ``github.com/<owner>/<repo>``,
  ``github.com:<owner>/<repo>`` and ``<owner>.github.io/<repo>``, where the
  repository name is not followed by another name character;
- ``cfg/checks.yaml`` itself: it must hold the word list. To keep that
  exemption narrow, the file is schema-checked: only the keys below exist.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from checks.common import CheckConfigError, CheckResult

CONFIG = "cfg/checks.yaml"


@dataclass(frozen=True)
class Config:
    words: tuple[str, ...]
    exclude: tuple[str, ...]
    repo_slug: str | None


def load_config(repo_root: Path, path: str = CONFIG) -> Config:
    p = Path(repo_root) / path
    try:
        data = yaml.safe_load(p.read_text())
    except OSError as exc:
        raise CheckConfigError(f"{p}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise CheckConfigError(f"{p}: invalid YAML: {exc}") from None
    if not isinstance(data, dict) or set(data) != {"neutrality"}:
        raise CheckConfigError(f"{p}: top level must be a mapping with only 'neutrality'")
    n = data["neutrality"]
    if not isinstance(n, dict) or not set(n) <= {"words", "exclude", "repo_slug"} \
            or not {"words", "exclude"} <= set(n):
        raise CheckConfigError(
            f"{p}: 'neutrality' must have 'words', 'exclude' and optional 'repo_slug' only")
    for key in ("words", "exclude"):
        v = n[key]
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v):
            raise CheckConfigError(f"{p}: neutrality.{key} must be a list of non-empty strings")
    if not n["words"]:
        raise CheckConfigError(f"{p}: neutrality.words is empty")
    slug = n.get("repo_slug")
    if slug is not None and (not isinstance(slug, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", slug)):
        raise CheckConfigError(f"{p}: neutrality.repo_slug must be 'owner/repo'")
    return Config(tuple(w.strip() for w in n["words"]), tuple(n["exclude"]), slug)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise CheckConfigError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def slug_from_remote(repo: Path) -> str | None:
    """``owner/repo`` from the ``origin`` URL (https, ssh or a proxy path)."""
    proc = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    m = re.search(r"([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", proc.stdout.strip().replace(":", "/"))
    return f"{m.group(1)}/{m.group(2)}" if m else None


def word_pattern(words: tuple[str, ...]) -> re.Pattern:
    alts = [r"[\s_-]+".join(re.escape(p) for p in re.split(r"[\s_-]+", w)) for w in words]
    return re.compile("|".join(sorted(alts, key=len, reverse=True)), re.IGNORECASE)


def slug_pattern(slug: str | None) -> re.Pattern | None:
    if not slug:
        return None
    owner, repo = (re.escape(s) for s in slug.split("/"))
    end = r"(?![\w-])"
    return re.compile(
        rf"github\.com[/:]{owner}/{repo}{end}|{owner}\.github\.io/{repo}{end}", re.IGNORECASE)


def scan_text(text: str, words: re.Pattern, slug: re.Pattern | None) -> list[tuple[int, str]]:
    """(line number, matched word) for every hit outside an exempt slug."""
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        if slug is not None:
            line = slug.sub(lambda m: " " * len(m.group()), line)
        hits += [(n, m.group()) for m in words.finditer(line)]
    return hits


def run(repo_root: Path, config_path: str = CONFIG) -> CheckResult:
    cfg = load_config(repo_root, config_path)
    slug = cfg.repo_slug or slug_from_remote(repo_root)
    words, slug_re = word_pattern(cfg.words), slug_pattern(slug)
    tracked = [f for f in _git(repo_root, "ls-files", "-z", "--cached", "--others",
                                         "--exclude-standard").split("\0") if f]
    files = [f for f in sorted(set(tracked)) if f != config_path and not f.startswith(cfg.exclude)]
    result = CheckResult("neutrality")
    for f in files:
        for _, w in scan_text(f, words, None):
            result.findings.append(f"{f}: path contains {w!r}")
        p = Path(repo_root) / f
        if not p.is_file():  # deleted in the working tree, or a submodule
            continue
        data = p.read_bytes()
        if b"\0" in data:
            continue
        for n, w in scan_text(data.decode("utf-8", errors="replace"), words, slug_re):
            result.findings.append(f"{f}:{n}: {w!r}")
    result.checked = (f"{len(files)} tracked file(s) outside {', '.join(cfg.exclude)}; "
                      f"{len(cfg.words)} word(s); slug exempt in URLs: {slug or 'none'}")
    return result
