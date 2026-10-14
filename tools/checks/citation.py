"""Citation check: paraphrase and cite external standards ([wiki BKM,
citations]). A heuristic: it flags long quoted passages and citation-form
errors, it cannot prove that text was not copied.

Scans every text file below the source root and its sibling ``wiki/src/``
(hidden entries and binary files skipped):

- **Block quote** (Markdown only): consecutive ``>`` lines whose text has more
  than ``citation.max_quote_words`` words.
- **Quoted run**: text between typographic quotes, or between straight
  double quotes (a Markdown paragraph, or one line in any other file; triple
  quotes are not delimiters), with more than ``max_quote_words`` words.
- **Malformed citation**: ``[cite:`` that does not open exactly
  ``[cite: <standard>, rev <revision>, §<section>]``.
- **Uncited section reference**: a ``§`` or a dotted section number after
  ``section``/``sec.``/``chapter``/``clause`` outside a citation (one
  inside a malformed citation is reported once, as that).

Markdown code spans and fenced code blocks are not prose and are blanked
first.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from checks.common import CheckConfigError, CheckResult, line_of

CONFIG = "cfg/checks.yaml"
FORM = "[cite: <standard>, rev <revision>, §<section>]"

_OPEN = re.compile(r"\[\s*cite\s*:", re.IGNORECASE)
_FORM = re.compile(
    r"\[cite: [A-Za-z0-9](?:[A-Za-z0-9 ./+&()-]*[A-Za-z0-9)])?"
    r", rev [A-Za-z0-9][A-Za-z0-9.-]*"
    r", §[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*\]")
_SECTION_REF = re.compile(
    r"§|\b(?:section|sec\.|chapter|clause)\s*[0-9]+(?:\.[0-9]+)+", re.IGNORECASE)
_WORD = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*")
_CURLY = re.compile(r"“([^”]*)”")
# One straight-quoted run; a quote that is part of '""' or '"""' is no delimiter.
_STRAIGHT_LINE = re.compile(r'(?<!")"(?!")([^"\n]+)"(?!")')
_STRAIGHT_PARA = re.compile(r'(?<!")"(?!")((?:[^"\n]|\n(?![ \t]*\n))+)"(?!")')
_FENCE = re.compile(r"^[ \t]{0,3}(```|~~~).*?(?:^[ \t]{0,3}\1[^\n]*$|\Z)",
                    re.MULTILINE | re.DOTALL)
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1", re.DOTALL)
_BLOCK_QUOTE = re.compile(r"(?:^[ \t]{0,3}>.*(?:\n|\Z))+", re.MULTILINE)


def load_config(repo_root: Path, path: str = CONFIG) -> int:
    """``citation.max_quote_words`` from ``cfg/checks.yaml``."""
    p = Path(repo_root) / path
    try:
        data = yaml.safe_load(p.read_text())
    except OSError as exc:
        raise CheckConfigError(f"{p}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise CheckConfigError(f"{p}: invalid YAML: {exc}") from None
    c = data.get("citation") if isinstance(data, dict) else None
    if not isinstance(c, dict) or set(c) != {"max_quote_words"}:
        raise CheckConfigError(f"{p}: 'citation' must have 'max_quote_words' only")
    n = c["max_quote_words"]
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise CheckConfigError(f"{p}: citation.max_quote_words must be a positive integer")
    return n


def _blank(m: re.Match) -> str:
    return re.sub(r"[^\n]", " ", m.group())


def prose(text: str) -> str:
    """Markdown with fenced code blocks and code spans blanked (lines kept)."""
    return _CODE_SPAN.sub(_blank, _FENCE.sub(_blank, text))


def _snippet(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= 40 else s[:37] + "..."


def scan_text(text: str, markdown: bool, max_words: int) -> list[tuple[int, str]]:
    """(line, message) for every finding in one file's text."""
    if markdown:
        text = prose(text)
    hits: list[tuple[int, str]] = []
    if markdown:
        for m in _BLOCK_QUOTE.finditer(text):
            body = re.sub(r"^[ \t]{0,3}>", "", m.group(), flags=re.MULTILINE)
            n = len(_WORD.findall(body))
            if n > max_words:
                hits.append((line_of(text, m.start()),
                             f"block quote of {n} words (max {max_words}): {_snippet(body)!r}"))
    straight = _STRAIGHT_PARA if markdown else _STRAIGHT_LINE
    for rx in (_CURLY, straight):
        for m in rx.finditer(text):
            n = len(_WORD.findall(m.group(1)))
            if n > max_words:
                quote = _snippet(m.group(1))
                hits.append((line_of(text, m.start()),
                             f"quoted run of {n} words (max {max_words}): {quote!r}"))
    cited: list[tuple[int, int]] = []  # citations, good or bad, report once
    for m in _OPEN.finditer(text):
        good = _FORM.match(text, m.start())
        if good:
            cited.append(good.span())
        else:
            # The bad citation runs to its ']' or, if unclosed, the end of the line.
            eol = text.find("\n", m.start())
            eol = len(text) if eol < 0 else eol
            end = text.find("]", m.start(), eol)
            stop = eol if end < 0 else end + 1
            cited.append((m.start(), stop))
            hits.append((line_of(text, m.start()),
                         f"malformed citation {_snippet(text[m.start():stop])!r} "
                         f"(expected '{FORM}')"))
    for m in _SECTION_REF.finditer(text):
        if not any(a <= m.start() < b for a, b in cited):
            hits.append((line_of(text, m.start()),
                         f"section reference {m.group()!r} outside a citation "
                         f"(expected '{FORM}')"))
    return sorted(hits)


def _files(root: Path) -> list[Path]:
    found = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        found += [Path(dirpath) / f for f in files if not f.startswith(".")]
    return sorted(found)


def run(repo_root: Path, src_root: Path, config_path: str = CONFIG) -> CheckResult:
    max_words = load_config(repo_root, config_path)
    base = Path(src_root).resolve().parent
    roots = [Path(src_root).resolve(), base / "wiki" / "src"]
    result = CheckResult("citation")
    count = 0
    for root in roots:
        for p in _files(root):
            data = p.read_bytes()
            if b"\0" in data:
                continue
            count += 1
            text = data.decode("utf-8", errors="replace")
            rel = p.relative_to(base).as_posix()
            for n, msg in scan_text(text, p.suffix == ".md", max_words):
                result.findings.append(f"{rel}:{n}: {msg}")
    trees = ", ".join(r.relative_to(base).as_posix() + "/" for r in roots)
    result.checked = (f"{count} text file(s) under {trees}; quotes up to {max_words} words; "
                      f"citation form '{FORM}'")
    return result
