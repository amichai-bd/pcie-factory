from datetime import datetime, timezone

from metrics import github
from metrics.gh import GhError

from conftest import FakeGh

NOW = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
R = "repos/own/repo"


def issue(n, created, closed=None, labels=(), updated=None, state=None):
    return {"number": n, "created_at": created, "closed_at": closed,
            "updated_at": updated or closed or created, "state": state or ("closed" if closed else "open"),
            "labels": [{"name": x} for x in labels], "user": {"login": "someone"},
            "title": "secret title", "body": "mail me at someone@example.com"}


def pr(n, created, merged=None, ref="5-x", sha="abc1234def5678", state=None):
    return {"number": n, "created_at": created, "merged_at": merged, "closed_at": merged,
            "state": state or ("closed" if merged else "open"), "head": {"ref": ref, "sha": sha},
            "user": {"login": "someone"}}


def comment(n, created, body="ok"):
    return {"issue_url": f"https://api.github.com/{R}/issues/{n}", "created_at": created,
            "body": body, "user": {"login": "someone"}}


def run(created, conclusion="success", seconds=60, branch="main", event="push", sha="s1"):
    start = datetime.fromisoformat(created.replace("Z", "+00:00"))
    end = start.timestamp() + seconds
    return {"created_at": created, "run_started_at": created,
            "updated_at": datetime.fromtimestamp(end, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "completed", "conclusion": conclusion, "head_branch": branch,
            "event": event, "head_sha": sha}


ROUTES = {
    R: {"size": 1234},
    f"{R}/issues": [
        issue(1, "2026-03-01T00:00:00Z", "2026-03-02T00:00:00Z", ["type:feat", "area:rtl"]),
        issue(2, "2026-03-31T00:00:00Z", labels=["type:bug", "nightly-red"]),
        issue(3, "2026-03-10T00:00:00Z", labels=["type:infra"], updated="2026-03-12T00:00:00Z"),
        {**pr(10, "2026-03-30T00:00:00Z"), "pull_request": {}},  # PRs in the issues list are dropped
    ],
    f"{R}/pulls": [
        pr(10, "2026-03-30T00:00:00Z", "2026-03-31T00:00:00Z", sha="aaaaaaa1111"),
        pr(11, "2026-03-20T00:00:00Z", "2026-03-25T00:00:00Z", sha="bbbbbbb2222"),
        pr(12, "2026-03-31T06:00:00Z"),
        pr(13, "2026-03-31T00:00:00Z", "2026-03-31T01:00:00Z", ref="nightly/2026-03-31"),
    ],
    f"{R}/issues/comments": [
        comment(1, "2026-03-01T06:00:00Z", "Claimed"),
        comment(10, "2026-03-30T10:00:00Z", "REVIEW-VERDICT: REQUEST-CHANGES @aaaaaaa0000"),
        comment(10, "2026-03-30T20:00:00Z", "**REVIEW-VERDICT: APPROVE @aaaaaaa1111**"),
        comment(11, "2026-03-24T20:00:00Z", "REVIEW-VERDICT: APPROVE @ccccccc"),
    ],
    f"{R}/actions/workflows/ci.yml/runs": {"workflow_runs": [
        run("2026-03-31T10:00:00Z", "success", 100, sha="m2"),
        run("2026-03-30T10:00:00Z", "failure", 200, sha="m1"),
        run("2026-03-29T10:00:00Z", "cancelled", 10, branch="5-x", event="pull_request"),
    ]},
    f"{R}/actions/workflows/nightly.yml/runs": GhError("forbidden", 403),
    f"{R}/pulls/10": {"additions": 10, "deletions": 5},
    f"{R}/pulls/11": {"additions": 1, "deletions": 0},
}


def raw(cfg, routes=ROUTES):
    return github.fetch(FakeGh(dict(routes)), cfg)


def test_fetch_records_failed_source(cfg):
    r = raw(cfg)
    assert r["nightly_runs"] == {"error": "forbidden"}
    assert [i["number"] for i in r["issues"]] == [1, 2, 3]
    assert set(r["pr_details"]) == {10, 11}  # data PR 13 not fetched


def test_issues_section(cfg):
    s = github.issues_section(raw(cfg), cfg, NOW)
    assert s["status"] == "ok" and s["open"] == 2 and s["total"] == 3
    assert s["open_by_label"] == {"nightly-red": 1, "type:bug": 1, "type:infra": 1}
    assert s["windows"]["1d"]["opened"] == 1
    assert s["windows"]["30d"]["closed"] == 1
    assert s["windows"]["30d"]["closed_by_label"] == {"area:rtl": 1, "type:feat": 1}


def test_pulls_section(cfg):
    s = github.pulls_section(raw(cfg), cfg, NOW)
    w30, w1 = s["windows"]["30d"], s["windows"]["1d"]
    assert s["open"] == 1 and s["merged"] == 2 and s["total"] == 3
    assert w30["merged"] == 2 and w30["data_merged"] == 1
    assert w1["merged"] == 1 and w1["lead_time_h"] == {"median": 24.0, "p90": 24.0}
    assert w30["lead_time_h"]["median"] == 72.0  # (24 + 120) / 2
    assert w30["review_rounds"] == {"median": 1.5, "max": 2}


def test_ci_section(cfg):
    s = github.ci_section(raw(cfg), cfg, NOW)
    w = s["windows"]["7d"]
    assert w["runs"] == 3 and w["pass_rate"] == 0.5  # cancelled is not decided
    assert w["duration_s"]["median"] == 100.0
    assert s["latest_main"] == {"sha": "m2", "conclusion": "success", "duration_s": 100.0}


def test_distributions(cfg):
    d = github.distributions(raw(cfg), cfg)
    assert d["issue_close_h"] == [24.0]
    assert d["issue_first_response_h"] == [6.0]
    assert d["pr_lead_h"] == [24.0, 120.0]
    assert d["pr_review_rounds"] == [1, 2]
    assert d["pr_size_lines"] == [1, 15]
    assert d["ci_gate_duration_s"] == [10.0, 100.0, 200.0]
    assert d["nightly_duration_s"] is None


def test_health(cfg):
    h = github.health(raw(cfg), cfg, NOW)
    assert h["open_prs"] == {"count": 1, "age_h": [6.0]}
    assert h["open_issues"]["count"] == 2 and h["open_issues"]["stale"] == 1
    assert h["nightly_red"] == {"open": 1}
    assert h["ci_gate_main"] == {"runs": 2, "pass_rate": 0.5}
    assert h["review_rounds_median"] == 1.5
    # PR 10 approved at its head; PR 11's approval names another commit.
    assert h["approve_at_merged_head"] == {"merged": 2, "approved": 1, "share": 0.5}


def test_ci_by_commit(cfg):
    assert github.ci_by_commit(raw(cfg)) == {
        "m1": {"conclusion": "failure", "duration_s": 200.0},
        "m2": {"conclusion": "success", "duration_s": 100.0}}


def test_everything_unavailable_when_forbidden(cfg):
    routes = {k: GhError("blocked", 403) for k in ROUTES}
    r = raw(cfg, routes)
    for fn in (github.issues_section, github.pulls_section, github.ci_section):
        assert fn(r, cfg, NOW) == {"status": "unavailable", "reason": "forbidden"}
    assert all(v is None for v in github.distributions(r, cfg).values())
    assert all(v is None for v in github.health(r, cfg, NOW).values())
    assert github.ci_by_commit(r) is None


def test_percentiles():
    assert github.median([]) is None and github.p90([]) is None
    assert github.p90(list(range(1, 11))) == 9
    assert github.p90([5]) == 5
