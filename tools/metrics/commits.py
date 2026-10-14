"""The per-commit series ``commits.jsonl``: one line per main commit.

Lines are ordered by position on the first-parent history of the main ref.
A run appends the commits that are missing (the first run is the backfill)
and refreshes the ``ci`` entry of lines that had no completed push run yet.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import MetricsError
from .record import iso
from .tree import TreeReader, git

SERIES_VERSION = 1


def ci_entry(sha: str, ci_map: dict | None) -> dict:
    if ci_map is None:
        return {"status": "unavailable"}
    run = ci_map.get(sha)
    if run is None:
        return {"status": "none"}
    return {"status": "ok", "conclusion": run["conclusion"], "duration_s": run["duration_s"]}


def load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    lines = []
    for n, text in enumerate(path.read_text().splitlines(), 1):
        if not text.strip():
            continue
        try:
            obj = json.loads(text)
            if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str):
                raise ValueError("no sha")
        except ValueError as exc:
            raise MetricsError(f"{path.name}:{n}: invalid series line: {exc}") from None
        lines.append(obj)
    return lines


def dumps(lines: list[dict]) -> str:
    return "".join(json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n" for x in lines)


def update(reader: TreeReader, ref: str, lines: list[dict], ci_map: dict | None,
           limit: int | None = None) -> tuple[list[dict], int, int]:
    """Return (new lines, added, refreshed)."""
    shas = git(reader.repo, "rev-list", "--first-parent", "--reverse", ref).decode().split()
    index = {sha: i for i, sha in enumerate(shas)}
    have = {x["sha"]: x for x in lines}
    refreshed = 0
    for x in lines:
        if x.get("ci", {}).get("status") != "ok":
            new = ci_entry(x["sha"], ci_map)
            if new["status"] == "ok" or (x.get("ci", {}).get("status") == "unavailable"
                                         and new["status"] == "none"):
                x["ci"] = new
                refreshed += 1
    added = 0
    for sha in shas:
        if sha in have:
            continue
        if limit is not None and added >= limit:
            break
        stats, _ = reader.stats(sha)
        cdate = git(reader.repo, "show", "-s", "--format=%cI", sha).decode().strip()
        have[sha] = {
            "schema_version": SERIES_VERSION,
            "sha": sha,
            "short": sha[:12],
            "date": iso(datetime.fromisoformat(cdate)),
            "code": stats,
            "ci": ci_entry(sha, ci_map),
        }
        added += 1
    ordered = sorted(have.values(), key=lambda x: (index.get(x["sha"], len(shas)), x["date"]))
    for x in ordered:
        x["index"] = index.get(x["sha"])
    return ordered, added, refreshed
