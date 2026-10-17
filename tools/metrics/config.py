"""``cfg/metrics.yaml`` loading and validation, and the path glob matcher."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .trends import KEYS as TREND_KEYS

CONFIG = "metrics.yaml"
_KEYS = {
    "data_dir", "branch_prefix", "exclude", "kinds", "line_comments", "block_c",
    "windows", "review_marker", "ci_workflow", "slowest", "fpga_fields", "asic_fields",
    "src_root", "data_author", "nightly_workflow", "stale_days", "health_ci_runs", "max_pr_details",
    "drop_alerts",
}
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_FIELD_RE = re.compile(r"^(\*|[A-Za-z0-9_]+)(\.(\*|[A-Za-z0-9_]+))*$")


class MetricsError(Exception):
    """Configuration, input or GitHub error (exit 2)."""


@dataclass(frozen=True)
class Config:
    data_dir: str
    branch_prefix: str
    data_author: str
    exclude: tuple[str, ...]
    kinds: dict[str, tuple[str, ...]]
    line_comments: dict[str, tuple[str, ...]]
    block_c: frozenset[str]
    windows: tuple[int, ...]
    review_marker: str
    ci_workflow: str
    nightly_workflow: str
    src_root: str
    stale_days: int
    health_ci_runs: int
    max_pr_details: int
    slowest: int
    fpga_fields: tuple[str, ...]
    asic_fields: tuple[str, ...]
    drop_alerts: dict[str, float]


@lru_cache(maxsize=None)
def _glob_re(pattern: str) -> re.Pattern:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def match(path: str, patterns) -> bool:
    """True when the repo-relative POSIX ``path`` matches any glob pattern."""
    return any(_glob_re(p).match(path) for p in patterns)


def _str_list(where: str, value) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise MetricsError(f"{where}: must be a list of non-empty strings")
    return tuple(value)


def _rel(where: str, value) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in value.split("/"):
        raise MetricsError(f"{where}: must be a relative path without '..'")
    return value


def load(cfg_dir: Path) -> Config:
    path = Path(cfg_dir) / CONFIG
    where = f"cfg/{CONFIG}"
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise MetricsError(f"{where}: cannot read: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise MetricsError(f"{where}: invalid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise MetricsError(f"{where}: top level must be a mapping")
    missing, unknown = _KEYS - set(raw), set(raw) - _KEYS
    if missing:
        raise MetricsError(f"{where}: missing keys: {', '.join(sorted(missing))}")
    if unknown:
        raise MetricsError(f"{where}: unknown keys: {', '.join(sorted(unknown))}")

    kinds = raw["kinds"]
    if not isinstance(kinds, dict) or not kinds:
        raise MetricsError(f"{where}: kinds: must be a non-empty mapping")
    for name in kinds:
        if not isinstance(name, str) or not _KIND_RE.match(name):
            raise MetricsError(f"{where}: kinds: bad kind name {name!r}")
    comments = raw["line_comments"]
    if not isinstance(comments, dict):
        raise MetricsError(f"{where}: line_comments: must be a mapping")
    for ext in comments:
        if not isinstance(ext, str) or not ext.startswith("."):
            raise MetricsError(f"{where}: line_comments: extension {ext!r} must start with '.'")
    windows = raw["windows"]
    if not isinstance(windows, list) or not windows or not all(
            isinstance(w, int) and not isinstance(w, bool) and w > 0 for w in windows):
        raise MetricsError(f"{where}: windows: must be a list of positive integers")
    for key in ("slowest", "stale_days", "health_ci_runs", "max_pr_details"):
        v = raw[key]
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise MetricsError(f"{where}: {key}: must be a positive integer")
    for key in ("review_marker", "ci_workflow", "nightly_workflow", "branch_prefix",
                "data_author"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise MetricsError(f"{where}: {key}: must be a non-empty string")
    fields = {}
    for key in ("fpga_fields", "asic_fields"):
        fields[key] = _str_list(f"{where}: {key}", raw[key])
        bad = [f for f in fields[key] if not _FIELD_RE.match(f)]
        if bad:
            raise MetricsError(f"{where}: {key}: bad field path {bad[0]!r}")
    alerts = raw["drop_alerts"]
    if not isinstance(alerts, dict):
        raise MetricsError(f"{where}: drop_alerts: must be a mapping of series to threshold")
    missing, unknown = set(TREND_KEYS) - set(alerts), set(alerts) - set(TREND_KEYS)
    if missing or unknown:
        raise MetricsError(f"{where}: drop_alerts: needs exactly the series "
                           f"{', '.join(TREND_KEYS)} (missing: {', '.join(sorted(missing)) or 'none'}; "
                           f"unknown: {', '.join(sorted(map(str, unknown))) or 'none'})")
    for key, v in alerts.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v < float("inf"):
            raise MetricsError(f"{where}: drop_alerts.{key}: must be a non-negative number")
    return Config(
        data_dir=_rel(f"{where}: data_dir", raw["data_dir"]).rstrip("/"),
        branch_prefix=raw["branch_prefix"],
        data_author=raw["data_author"],
        exclude=_str_list(f"{where}: exclude", raw["exclude"]),
        kinds={k: _str_list(f"{where}: kinds.{k}", v) for k, v in kinds.items()},
        line_comments={k: _str_list(f"{where}: line_comments.{k}", v)
                       for k, v in comments.items()},
        block_c=frozenset(_str_list(f"{where}: block_c", raw["block_c"])),
        windows=tuple(sorted(set(windows))),
        review_marker=raw["review_marker"],
        ci_workflow=raw["ci_workflow"],
        nightly_workflow=raw["nightly_workflow"],
        src_root=_rel(f"{where}: src_root", raw["src_root"]).rstrip("/"),
        stale_days=raw["stale_days"],
        health_ci_runs=raw["health_ci_runs"],
        max_pr_details=raw["max_pr_details"],
        slowest=raw["slowest"],
        fpga_fields=fields["fpga_fields"],
        asic_fields=fields["asic_fields"],
        drop_alerts={k: alerts[k] for k in TREND_KEYS},
    )
