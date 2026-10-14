"""GitHub flow, CI, health and distribution metrics from the REST API.

``fetch`` gathers raw lists through a ``Gh``-like client; each source that
fails is recorded with a short reason instead of stopping the collection.
The ``*_section`` functions are pure: they take the raw lists and ``now``
and return only counts, durations, label names, numbers and short SHAs
(never logins, names, emails or bodies).
"""

from __future__ import annotations

import math
import re
import statistics
from datetime import datetime, timedelta, timezone

from .config import Config
from .gh import GhError

_VERDICT = (r"(?m)^\s*\**\s*{marker}\s*:?\s*\**\s*(APPROVE|REQUEST-CHANGES)\b[^@\n]*@\s*`?([0-9a-f]{{7,40}})")
_FAILED = {"failure", "timed_out", "startup_failure"}
UNAVAILABLE = "unavailable"


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hours(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 3600.0, 2)


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def p90(values: list[float]) -> float | None:
    """Nearest-rank 90th percentile."""
    if not values:
        return None
    s = sorted(values)
    return round(s[max(0, math.ceil(0.9 * len(s)) - 1)], 2)


def reason_of(exc: Exception) -> str:
    """A fixed, publishable reason for a failed source."""
    status = getattr(exc, "status", None)
    if status in (401, 403):
        return "forbidden"
    if status == 404:
        return "not-found"
    return "gh-error"


# --- fetch --------------------------------------------------------------------

def fetch(gh, cfg: Config, log=None) -> dict:
    """Raw lists keyed by source; a failed source maps to ``{"error": reason}``."""
    r = gh.repo
    raw: dict = {}

    def get(name, fn):
        try:
            raw[name] = fn()
        except GhError as exc:
            raw[name] = {"error": reason_of(exc)}
            if log:
                print(f"[metrics] github {name}: unavailable ({exc})", file=log)

    get("repo", lambda: gh.api("GET", f"repos/{r}"))
    get("issues", lambda: [x for x in gh.paginate(f"repos/{r}/issues?state=all")
                           if "pull_request" not in x])
    get("pulls", lambda: gh.paginate(f"repos/{r}/pulls?state=all"))
    get("comments", lambda: gh.paginate(f"repos/{r}/issues/comments?sort=created&direction=asc"))
    get("ci_runs", lambda: gh.paginate(
        f"repos/{r}/actions/workflows/{cfg.ci_workflow}/runs", key="workflow_runs", limit=2000))
    get("nightly_runs", lambda: gh.paginate(
        f"repos/{r}/actions/workflows/{cfg.nightly_workflow}/runs", key="workflow_runs",
        limit=500))
    pulls = raw.get("pulls")
    if isinstance(pulls, list):
        merged = sorted((p for p in pulls if p.get("merged_at") and not _is_data(p, cfg)),
                        key=lambda p: p["merged_at"], reverse=True)[:cfg.max_pr_details]
        get("pr_details", lambda: {p["number"]: gh.api("GET", f"repos/{r}/pulls/{p['number']}")
                                   for p in merged})
    return raw


def ok(raw: dict, *names: str) -> bool:
    return all(isinstance(raw.get(n), (list, dict)) and not
               (isinstance(raw.get(n), dict) and "error" in raw[n]) for n in names)


def unavailable(raw: dict, *names: str) -> dict:
    reasons = sorted({raw[n]["error"] if isinstance(raw.get(n), dict) and "error" in raw[n]
                      else "missing" for n in names if not ok(raw, n)})
    return {"status": UNAVAILABLE, "reason": ",".join(reasons)}


# --- helpers ------------------------------------------------------------------

def _is_data(pr: dict, cfg: Config) -> bool:
    return (pr.get("head") or {}).get("ref", "").startswith(cfg.branch_prefix)


def _labels(item: dict) -> list[str]:
    return sorted(x["name"] for x in item.get("labels", []))


def _wkey(days: int) -> str:
    return f"{days}d"


def _issue_number(comment: dict) -> int | None:
    url = comment.get("issue_url") or ""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def verdicts(comments: list[dict], cfg: Config) -> dict[int, list[tuple[str, str]]]:
    """Per issue/PR number: the (verdict, sha) of each review-verdict comment."""
    rx = re.compile(_VERDICT.format(marker=re.escape(cfg.review_marker)))
    out: dict[int, list[tuple[str, str]]] = {}
    for c in comments:
        n = _issue_number(c)
        if n is None:
            continue
        for m in rx.finditer(c.get("body") or ""):
            out.setdefault(n, []).append((m.group(1), m.group(2)))
    return out


def _run_seconds(run: dict) -> float | None:
    start = parse_time(run.get("run_started_at") or run.get("created_at"))
    end = parse_time(run.get("updated_at"))
    if not start or not end or run.get("status") != "completed":
        return None
    return round((end - start).total_seconds(), 1)


def _pass_rate(runs: list[dict]) -> float | None:
    decided = [x for x in runs if x.get("conclusion") == "success" or x.get("conclusion") in _FAILED]
    if not decided:
        return None
    return round(sum(x["conclusion"] == "success" for x in decided) / len(decided), 4)


# --- sections -----------------------------------------------------------------

def issues_section(raw: dict, cfg: Config, now: datetime) -> dict:
    if not ok(raw, "issues"):
        return unavailable(raw, "issues")
    issues = raw["issues"]
    open_ = [i for i in issues if i.get("state") == "open"]
    by_label: dict[str, int] = {}
    for i in open_:
        for lab in _labels(i):
            by_label[lab] = by_label.get(lab, 0) + 1
    windows = {}
    for d in cfg.windows:
        since = now - timedelta(days=d)
        opened = [i for i in issues if parse_time(i["created_at"]) >= since]
        closed = [i for i in issues if i.get("closed_at") and parse_time(i["closed_at"]) >= since]
        windows[_wkey(d)] = {
            "opened": len(opened),
            "closed": len(closed),
            "opened_by_label": _count_labels(opened),
            "closed_by_label": _count_labels(closed),
        }
    return {"status": "ok", "total": len(issues), "open": len(open_),
            "open_by_label": dict(sorted(by_label.items())), "windows": windows}


def _count_labels(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        for lab in _labels(i):
            out[lab] = out.get(lab, 0) + 1
    return dict(sorted(out.items()))


def pulls_section(raw: dict, cfg: Config, now: datetime) -> dict:
    if not ok(raw, "pulls"):
        return unavailable(raw, "pulls")
    pulls = raw["pulls"]
    rounds = verdicts(raw["comments"], cfg) if ok(raw, "comments") else None
    work = [p for p in pulls if not _is_data(p, cfg)]
    merged = [p for p in work if p.get("merged_at")]
    windows = {}
    for d in cfg.windows:
        since = now - timedelta(days=d)
        in_win = [p for p in merged if parse_time(p["merged_at"]) >= since]
        lead = [hours(parse_time(p["created_at"]), parse_time(p["merged_at"])) for p in in_win]
        w = {
            "merged": len(in_win),
            "opened": sum(parse_time(p["created_at"]) >= since for p in work),
            "data_merged": sum(1 for p in pulls if _is_data(p, cfg) and p.get("merged_at")
                               and parse_time(p["merged_at"]) >= since),
            "lead_time_h": {"median": median(lead), "p90": p90(lead)},
        }
        if rounds is not None:
            rr = [len(rounds.get(p["number"], [])) for p in in_win]
            w["review_rounds"] = {"median": median(rr), "max": max(rr) if rr else None}
        windows[_wkey(d)] = w
    out = {"status": "ok", "total": len(work), "open": sum(p.get("state") == "open" for p in work),
           "merged": len(merged), "windows": windows}
    if rounds is None:
        out["review_rounds"] = unavailable(raw, "comments")
    return out


def ci_section(raw: dict, cfg: Config, now: datetime) -> dict:
    if not ok(raw, "ci_runs"):
        return unavailable(raw, "ci_runs")
    runs = raw["ci_runs"]
    windows = {}
    for d in cfg.windows:
        since = now - timedelta(days=d)
        sel = [x for x in runs if parse_time(x["created_at"]) >= since]
        dur = [s for s in map(_run_seconds, sel) if s is not None]
        windows[_wkey(d)] = {
            "runs": len(sel),
            "completed": sum(x.get("status") == "completed" for x in sel),
            "pass_rate": _pass_rate(sel),
            "duration_s": {"median": median(dur), "p90": p90(dur)},
        }
    main_push = sorted((x for x in runs if x.get("head_branch") == "main"
                        and x.get("event") == "push" and x.get("status") == "completed"),
                       key=lambda x: x["created_at"], reverse=True)
    latest = None
    if main_push:
        x = main_push[0]
        latest = {"sha": x["head_sha"][:12], "conclusion": x.get("conclusion"),
                  "duration_s": _run_seconds(x)}
    return {"status": "ok", "workflow": cfg.ci_workflow, "windows": windows, "latest_main": latest}


def distributions(raw: dict, cfg: Config) -> dict:
    """Raw value lists for histograms; a source that failed gives ``None``."""
    out: dict = {}
    if ok(raw, "issues"):
        issues = raw["issues"]
        out["issue_close_h"] = sorted(hours(parse_time(i["created_at"]), parse_time(i["closed_at"]))
                                      for i in issues if i.get("closed_at"))
        if ok(raw, "comments"):
            first: dict[int, datetime] = {}
            for c in raw["comments"]:
                n = _issue_number(c)
                t = parse_time(c.get("created_at"))
                if n is not None and t and (n not in first or t < first[n]):
                    first[n] = t
            out["issue_first_response_h"] = sorted(
                hours(parse_time(i["created_at"]), first[i["number"]])
                for i in issues if i["number"] in first)
        else:
            out["issue_first_response_h"] = None
    else:
        out["issue_close_h"] = out["issue_first_response_h"] = None
    if ok(raw, "pulls"):
        merged = [p for p in raw["pulls"] if p.get("merged_at") and not _is_data(p, cfg)]
        out["pr_lead_h"] = sorted(hours(parse_time(p["created_at"]), parse_time(p["merged_at"]))
                                  for p in merged)
        if ok(raw, "comments"):
            rounds = verdicts(raw["comments"], cfg)
            out["pr_review_rounds"] = sorted(len(rounds.get(p["number"], [])) for p in merged)
        else:
            out["pr_review_rounds"] = None
        if ok(raw, "pr_details"):
            out["pr_size_lines"] = sorted(d.get("additions", 0) + d.get("deletions", 0)
                                          for d in raw["pr_details"].values())
        else:
            out["pr_size_lines"] = None
    else:
        out["pr_lead_h"] = out["pr_review_rounds"] = out["pr_size_lines"] = None
    for key, name in (("ci_gate_duration_s", "ci_runs"), ("nightly_duration_s", "nightly_runs")):
        out[key] = (sorted(s for s in map(_run_seconds, raw[name]) if s is not None)
                    if ok(raw, name) else None)
    return out


def health(raw: dict, cfg: Config, now: datetime) -> dict:
    out: dict = {}
    if ok(raw, "pulls"):
        open_prs = [p for p in raw["pulls"] if p.get("state") == "open"]
        out["open_prs"] = {"count": len(open_prs),
                           "age_h": sorted(hours(parse_time(p["created_at"]), now)
                                           for p in open_prs)}
        merged = [p for p in raw["pulls"] if p.get("merged_at") and not _is_data(p, cfg)]
        if ok(raw, "comments"):
            v = verdicts(raw["comments"], cfg)
            rr = [len(v.get(p["number"], [])) for p in merged]
            approved = sum(any(kind == "APPROVE" and p["head"]["sha"].startswith(sha)
                               for kind, sha in v.get(p["number"], [])) for p in merged)
            out["review_rounds_median"] = median(rr)
            out["approve_at_merged_head"] = {
                "merged": len(merged), "approved": approved,
                "share": round(approved / len(merged), 4) if merged else None}
        else:
            out["review_rounds_median"] = out["approve_at_merged_head"] = None
    else:
        out["open_prs"] = out["review_rounds_median"] = out["approve_at_merged_head"] = None
    if ok(raw, "issues"):
        open_issues = [i for i in raw["issues"] if i.get("state") == "open"]
        stale_since = now - timedelta(days=cfg.stale_days)
        out["open_issues"] = {
            "count": len(open_issues),
            "age_h": sorted(hours(parse_time(i["created_at"]), now) for i in open_issues),
            "stale": sum(parse_time(i["updated_at"]) < stale_since for i in open_issues),
            "stale_days": cfg.stale_days,
        }
        red = [i for i in open_issues if "nightly-red" in _labels(i)]
        out["nightly_red"] = {"open": len(red)}
    else:
        out["open_issues"] = out["nightly_red"] = None
    if ok(raw, "ci_runs"):
        main = sorted((x for x in raw["ci_runs"] if x.get("head_branch") == "main"
                       and x.get("status") == "completed"),
                      key=lambda x: x["created_at"], reverse=True)[:cfg.health_ci_runs]
        out["ci_gate_main"] = {"runs": len(main), "pass_rate": _pass_rate(main)}
    else:
        out["ci_gate_main"] = None
    return out


def ci_by_commit(raw: dict) -> dict[str, dict] | None:
    """Latest completed push run on main per head SHA: conclusion and duration."""
    if not ok(raw, "ci_runs"):
        return None
    out: dict[str, dict] = {}
    for x in sorted(raw["ci_runs"], key=lambda x: x["created_at"]):
        if x.get("event") == "push" and x.get("head_branch") == "main" \
                and x.get("status") == "completed":
            out[x["head_sha"]] = {"conclusion": x.get("conclusion"),
                                  "duration_s": _run_seconds(x)}
    return out
