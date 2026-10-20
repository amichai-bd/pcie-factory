"""Design-neutrality check (CLAUDE.md, "Design neutrality").

Scans every file git lists (tracked, plus untracked files that are not
ignored) outside the exempt trees for the word list in ``cfg/checks.yaml``,
case-insensitively, as substrings; a space, ``-`` or ``_`` inside a word
matches any run of spaces, ``-`` and ``_``, including none (``widget bus``
also matches ``widgetbus``). File paths are scanned as well as contents;
binary files (a NUL byte) are skipped.

It also scans the same files for the IP ids discovered from ``src/`` used
as identifiers (see ``ip_pattern``), so that a bare IP name that is also an
ordinary word is caught only where it names the IP.

Exemptions:
- the trees the contract names: ``src/``, ``wiki/src/``, ``wiki/tracker/data/``
  (hard-coded, not configurable, so a config line cannot widen them);
- the repository slug inside a GitHub URL: ``github.com/<owner>/<repo>``,
  ``github.com:<owner>/<repo>`` and ``<owner>.github.io/<repo>``, with a name
  boundary on both sides;
- in ``cfg/checks.yaml`` only the ``words`` list and the ``repo_slug`` value
  are blanked before scanning; its comments and every other line are scanned
  like any other file;
- the contents (not the path) of vendored minified files (``*.min.js``,
  ``*.min.css``) for the IP-identifier rule only; the word list still scans
  them.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from checks.common import CheckConfigError, CheckResult

CONFIG = "cfg/checks.yaml"
# The exempt trees, as CLAUDE.md "Design neutrality" fixes them.
EXEMPT = ("src/", "wiki/src/", "wiki/tracker/data/")
# Top-level sections of cfg/checks.yaml; each check validates its own.
SECTIONS = {"neutrality", "citation", "publishability", "spec_cache", "layout"}
# Vendored minified files: their contents are third-party code whose short
# quoted strings collide with IP ids, so only the IP-identifier rule skips
# them (the word list still scans them); their paths are still scanned.
MINIFIED = (".min.js", ".min.css")

_WORDS_LINE = re.compile(r"^\s*words\s*:\s*(\[[^\]\n]*\])", re.MULTILINE)
_SLUG_LINE = re.compile(r"^\s*repo_slug\s*:\s*(\S+)", re.MULTILINE)


@dataclass(frozen=True)
class Config:
    words: tuple[str, ...]
    repo_slug: str | None


def load_config(repo_root: Path, path: str = CONFIG) -> Config:
    p = Path(repo_root) / path
    try:
        text = p.read_text()
        data = yaml.safe_load(text)
    except OSError as exc:
        raise CheckConfigError(f"{p}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise CheckConfigError(f"{p}: invalid YAML: {exc}") from None
    if not isinstance(data, dict) or "neutrality" not in data or not set(data) <= SECTIONS:
        raise CheckConfigError(
            f"{p}: top level must be a mapping with 'neutrality' and only {sorted(SECTIONS)}")
    n = data["neutrality"]
    if not isinstance(n, dict) or "words" not in n or not set(n) <= {"words", "repo_slug"}:
        raise CheckConfigError(f"{p}: 'neutrality' must have 'words' and optional 'repo_slug' only")
    words = n["words"]
    if not isinstance(words, list) or not all(isinstance(x, str) and x.strip() for x in words):
        raise CheckConfigError(f"{p}: neutrality.words must be a list of non-empty strings")
    if not words:
        raise CheckConfigError(f"{p}: neutrality.words is empty")
    if len(re.findall(r"^\s*words\s*:", text, re.MULTILINE)) != 1 or not _WORDS_LINE.search(text):
        raise CheckConfigError(
            f"{p}: neutrality.words must be one flow list on one line ('words: [a, b]'), "
            "so that only it is exempt from the scan")
    if len(re.findall(r"^\s*repo_slug\s*:", text, re.MULTILINE)) > 1:
        raise CheckConfigError(f"{p}: neutrality.repo_slug appears more than once")
    slug = n.get("repo_slug")
    if slug is not None and (not isinstance(slug, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", slug)):
        raise CheckConfigError(f"{p}: neutrality.repo_slug must be 'owner/repo'")
    return Config(tuple(w.strip() for w in words), slug)


def blank_config_values(text: str) -> str:
    """``cfg/checks.yaml`` with only the words list and the repo_slug value
    replaced by spaces (positions kept); everything else is scanned."""
    for rx in (_WORDS_LINE, _SLUG_LINE):
        text = rx.sub(lambda m: m.group()[:m.start(1) - m.start()]
                      + " " * len(m.group(1)), text)
    return text


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
    alts = [r"[\s_-]*".join(re.escape(p) for p in re.split(r"[\s_-]+", w)) for w in words]
    return re.compile("|".join(sorted(alts, key=len, reverse=True)), re.IGNORECASE)


def slug_pattern(slug: str | None) -> re.Pattern | None:
    if not slug:
        return None
    owner, repo = (re.escape(s) for s in slug.split("/"))
    end = r"(?![\w-])"
    return re.compile(
        rf"(?<![\w.-])github\.com[/:]{owner}/{repo}{end}"
        rf"|(?<![\w.-]){owner}\.github\.io/{repo}{end}", re.IGNORECASE)


def scan_text(text: str, words: re.Pattern, slug: re.Pattern | None) -> list[tuple[int, str]]:
    """(line number, matched word) for every hit outside an exempt slug."""
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        if slug is not None:
            line = slug.sub(lambda m: " " * len(m.group()), line)
        hits += [(n, m.group()) for m in words.finditer(line)]
    return hits


def run(repo_root: Path, config_path: str = CONFIG,
        src_root: Path | None = None) -> CheckResult:
    """Scan for design-target words, and for IP ids discovered from
    ``src_root`` (default ``<repo_root>/src``; none when it is missing) used
    as identifiers."""
    cfg = load_config(repo_root, config_path)
    slug = cfg.repo_slug or slug_from_remote(repo_root)
    words, slug_re = word_pattern(cfg.words), slug_pattern(slug)
    src = Path(src_root) if src_root is not None else Path(repo_root) / "src"
    ids = ip_ids(src, repo_root) if src.is_dir() else ()
    ips = ip_pattern(ids)
    listed = [f for f in _git(repo_root, "ls-files", "-z", "--cached", "--others",
                                "--exclude-standard").split("\0") if f]
    files = [f for f in sorted(set(listed)) if not f.startswith(EXEMPT)]
    result = CheckResult("neutrality")
    for f in files:
        for _, w in scan_text(f, words, None):
            result.findings.append(f"{f}: path contains {w!r}")
        for _, w in scan_ip_text(f, ips):
            result.findings.append(f"{f}: path names IP id {w!r}")
        p = Path(repo_root) / f
        if not p.is_file():  # deleted in the working tree, or a submodule
            continue
        data = p.read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        if f == config_path:
            text = blank_config_values(text)
        for n, w in scan_text(text, words, slug_re):
            result.findings.append(f"{f}:{n}: {w!r}")
        if f.endswith(MINIFIED):
            continue
        for n, w in scan_ip_text(text, ips, slug_re):
            result.findings.append(f"{f}:{n}: IP id {w!r} used as an identifier")
    result.checked = (f"{len(files)} file(s) (tracked and untracked, not ignored) outside "
                      f"{', '.join(EXEMPT)}; {len(cfg.words)} word(s); "
                      f"{len(ids)} IP id(s) as identifiers; "
                      f"slug exempt in URLs: {slug or 'none'}")
    return result


# --- IP names used as identifiers (#311, #325) --------------------------------
#
# Used by run() on the whole repository and by ./build pr check on a diff.
# IP ids are discovered from src/ and many are ordinary words, so a bare
# substring match would flag plain English. An id counts only where it is
# used as an identifier:
#   - a whole code span or quoted string: `<id>`, '<id>', "<id>";
#   - a directory path segment: <id>/... or .../<id>/... (a file stem such as
#     <id>.py, or a last segment such as "die/<id>", is not one);
#   - an IP selector: --ip <id>, --ip=<id>, or a `<id>:<test>` reference;
#   - the id followed by "IP" or "sub-IP" ("<id> IP").
# Ids equal to a top-level entry of the repository (such as its configuration
# folder) are skipped: as path segments they name that entry, not the IP.


def ip_ids(src_root: Path, repo_root: Path | None = None) -> tuple[str, ...]:
    """Discovered IP ids, less those that equal a top-level repository entry."""
    from build.ip import DiscoveryError, discover
    try:
        ids = [ip.id for ip in discover(src_root)]
    except DiscoveryError as exc:
        raise CheckConfigError(str(exc)) from None
    top = set()
    if repo_root is not None and Path(repo_root).is_dir():
        top = {p.name for p in Path(repo_root).iterdir()}
    return tuple(i for i in ids if i not in top)


def ip_pattern(ids: tuple[str, ...]) -> re.Pattern | None:
    """Matches an IP id used as an identifier; group ``id`` holds the id."""
    if not ids:
        return None
    alt = "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True))
    forms = [
        rf"(?P<q>[`'\"])(?P<id>{alt})(?P=q)",                      # whole span / string
        rf"(?<![\w.-])(?P<id2>{alt})/",                             # leading segment
        rf"(?<=/)(?P<id3>{alt})/",                                  # inner segment
        rf"--ip[ =](?P<id4>{alt})\b",                               # selector
        rf"`(?P<id5>{alt}):\w",                                     # <id>:<test>
        rf"(?<![\w.-])(?P<id6>{alt})\s+(?:sub-)?IPs?\b",            # "<id> IP"
    ]
    return re.compile("|".join(f"(?:{f})" for f in forms))


def scan_ip_text(text: str, pattern: re.Pattern | None,
                 slug: re.Pattern | None = None) -> list[tuple[int, str]]:
    """(line number, IP id) for every identifier use outside an exempt slug."""
    if pattern is None:
        return []
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        if slug is not None:
            line = slug.sub(lambda m: " " * len(m.group()), line)
        for m in pattern.finditer(line):
            ip = next(v for k, v in m.groupdict().items() if k.startswith("id") and v)
            hits.append((n, ip))
    return hits
