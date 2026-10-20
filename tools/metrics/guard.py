"""The data guard: a nightly data branch may change only the data directory,
and only by adding valid run records and growing the series
(wiki/tools/metrics/SPEC.md#guard)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Config, MetricsError
from .tree import git

SERIES = "commits.jsonl"
RECORD = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z\.json$")  # the run name of `collect`
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def changes(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    """(status letter, path) changed between the merge base of ``base`` and
    ``head``, and ``head``. Renames count as a deletion plus an addition, so
    both paths are checked."""
    out = git(repo, "diff", "--name-status", "--no-renames", "-z", f"{base}...{head}")
    parts = [p for p in out.decode("utf-8", errors="replace").split("\0") if p]
    return sorted((parts[i][:1], parts[i + 1]) for i in range(0, len(parts) - 1, 2))


def changed_files(repo: Path, base: str, head: str) -> list[str]:
    return [p for _, p in changes(repo, base, head)]


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


def _record_problem(text: bytes) -> str | None:
    try:
        rec = json.loads(text.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return f"not JSON ({exc.__class__.__name__})"
    if not isinstance(rec, dict) or rec.get("schema_version") != 1:
        return "not a schema 1 record"
    meta = rec.get("meta")
    if not isinstance(meta, dict) or not _TIME.match(str(meta.get("generated_at"))):
        return "no meta.generated_at"
    return None


def _series(text: bytes | None) -> tuple[list[str], str | None]:
    """(shas in order, problem) of a commits.jsonl text; None is no file."""
    shas = []
    for n, line in enumerate((text or b"").decode("utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return shas, f"line {n} is not JSON"
        if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str):
            return shas, f"line {n} has no sha"
        shas.append(obj["sha"])
    return shas, None


def content(changed: list[tuple[str, str]], cfg: Config, read_head, read_base) -> list[str]:
    """Problems of a data branch's changes inside the data directory: only
    new run records (`<date>T<HHMMSS>Z.json`, valid schema 1) and the series
    (`commits.jsonl`, valid lines, no duplicate, every base line kept); never
    a deletion, a rewritten record or another file. ``read_head(path)`` and
    ``read_base(path)`` return the file's bytes, or None when absent."""
    prefix = cfg.data_dir.rstrip("/") + "/"
    problems = []
    for status, path in changed:
        if not path.startswith(prefix):
            continue  # check() reports it
        name = path[len(prefix):]
        if status == "D":
            problems.append(f"{path}: deleted; a data branch never removes data")
        elif name == SERIES:
            head, why = _series(read_head(path))
            base, _ = _series(read_base(path))
            dup = sorted({s for s in head if head.count(s) > 1})
            lost = sorted(set(base) - set(head))
            if why:
                problems.append(f"{path}: {why}")
            if dup:
                problems.append(f"{path}: duplicate sha {dup[0][:12]} ({len(dup)} duplicated)")
            if lost:
                problems.append(f"{path}: drops {len(lost)} line(s) of the base, e.g. {lost[0][:12]}")
        elif not RECORD.match(name):
            problems.append(f"{path}: not a run record (<date>T<HHMMSS>Z.json) or {SERIES}")
        elif status != "A":
            problems.append(f"{path}: an existing record is never rewritten")
        else:
            why = _record_problem(read_head(path) or b"")
            if why:
                problems.append(f"{path}: {why}")
    return problems


def run(repo: Path, branch: str, base: str, head: str, cfg: Config) -> tuple[bool, list[str], str | None]:
    """Guard ``head`` against ``base``: (passed, report lines, merge base
    when a data branch passed, else None)."""
    changed = changes(repo, base, head)
    passed, lines = check(branch, [p for _, p in changed], cfg)
    if not branch.startswith(cfg.branch_prefix) or not passed:
        return passed, lines, None
    merge_base = git(repo, "merge-base", base, head).decode().strip()

    def reader(rev):
        def read(path):
            try:
                return git(repo, "show", f"{rev}:{path}")
            except MetricsError:
                return None
        return read

    problems = content(changed, cfg, reader(head), reader(merge_base))
    if problems:
        lines = lines[:-1] + [f"  {p}" for p in problems]
        lines.append("FAIL: a data branch only adds valid run records and grows the series")
        return False, lines, None
    lines.append(f"PASS: {len(changed)} valid data file(s), nothing removed")
    return True, lines, merge_base
