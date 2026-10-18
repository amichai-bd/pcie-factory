import pytest

from metrics import red
from metrics.config import MetricsError
from metrics.gh import GhError

from conftest import FakeGh

R = "repos/own/repo"
URL = "https://github.com/own/repo/actions/runs/123"


def gh_with(open_issues):
    items = [{"number": n, "labels": [{"name": red.LABEL}]} for n in open_issues]
    items.append({"number": 99, "pull_request": {}})  # a PR with the label is ignored
    return FakeGh({f"{R}/issues": items})


def test_failure_opens_one_issue():
    gh = gh_with([])
    assert red.sync(gh, "failure", URL) == ("opened", [101])
    (method, path, fields), = gh.writes()
    assert (method, path) == ("POST", f"{R}/issues")
    assert ("labels[]", "nightly-red") in fields
    assert ("labels[]", "type:bug") in fields and ("labels[]", "area:ci") in fields
    assert any(k == "body" and URL in v for k, v in fields)


def test_failure_updates_the_existing_issue():
    gh = gh_with([7, 5])
    assert red.sync(gh, "failure", URL) == ("updated", [5])
    (method, path, fields), = gh.writes()
    assert (method, path) == ("POST", f"{R}/issues/5/comments")
    assert URL in fields[0][1]


def test_success_closes_every_open_issue():
    gh = gh_with([5, 7])
    assert red.sync(gh, "success", URL) == ("closed", [5, 7])
    writes = gh.writes()
    assert [w[1] for w in writes] == [f"{R}/issues/5/comments", f"{R}/issues/5",
                                      f"{R}/issues/7/comments", f"{R}/issues/7"]
    assert ("state", "closed") in writes[1][2] and ("state_reason", "completed") in writes[1][2]


def test_success_with_nothing_open():
    gh = gh_with([])
    assert red.sync(gh, "success", URL) == ("none", [])
    assert gh.writes() == []


@pytest.mark.parametrize("outcome, url", [
    ("failure", "https://evil.example/actions/runs/1"),
    ("failure", URL + "\nmore"),
    ("maybe", URL),
])
def test_rejects_bad_input(outcome, url):
    gh = gh_with([])
    with pytest.raises(MetricsError):
        red.sync(gh, outcome, url)
    assert gh.writes() == []


# --- failure details: failed steps and commits since the last green nightly ----

HEAD = "b" * 40
GREEN = "a" * 40
RUNS = f"{R}/actions/workflows/nightly.yml/runs"
GREEN_RUN = {"head_sha": GREEN, "html_url": "https://github.com/own/repo/actions/runs/7"}


def compare(n):
    return {"total_commits": n,
            "commits": [{"sha": f"{i:x}".ljust(40, "c"), "commit": {"message": f"change {i}\n\nbody"}}
                        for i in range(1, min(n, 250) + 1)]}


def gh_details(open_issues=(), runs=(GREEN_RUN,), cmp=None):
    gh = gh_with(list(open_issues))
    gh.routes[RUNS] = {"workflow_runs": list(runs)}
    gh.routes[f"{R}/compare/{GREEN}...{HEAD}"] = compare(2) if cmp is None else cmp
    return gh


def body_of(gh):
    (_, _, fields), = gh.writes()
    return dict(fields)["body"]


def test_failure_names_steps_and_commits_since_last_green():
    gh = gh_details()
    steps = {"check": "pass", "regress": "fail", "fpga": "skipped"}
    assert red.sync(gh, "failure", URL, HEAD, steps, "nightly.yml") == ("opened", [101])
    body = body_of(gh)
    assert "Failed steps: `regress`." in body and "coverage floors" in body
    assert "Commits since the last green nightly (https://github.com/own/repo/actions/runs/7, " \
           f"at `{GREEN[:12]}`): 2." in body
    assert "- `1ccccccccccc` change 1" in body and "- `2ccccccccccc` change 2" in body
    assert "body" not in body.split("change 1")[1].split("\n")[0]  # subject line only
    assert f"https://github.com/own/repo/compare/{GREEN[:12]}...{HEAD[:12]}" in body
    get = [c[1] for c in gh.calls if c[0] == "GET"]
    assert f"{RUNS}?branch=main&status=success&per_page=1" in get


def test_update_comment_carries_the_details():
    gh = gh_details(open_issues=[5])
    assert red.sync(gh, "failure", URL, HEAD, {"regress": "fail"}, "nightly.yml") == \
        ("updated", [5])
    body = body_of(gh)
    assert body.startswith(f"Nightly still red: {URL}") and "change 2" in body


def test_long_ranges_are_capped():
    gh = gh_details(cmp=compare(80))
    red.sync(gh, "failure", URL, HEAD, None, "nightly.yml")
    body = body_of(gh)
    assert body.count("\n- `") == red.MAX_COMMITS and "- ... 30 more" in body
    assert "Failed steps" not in body  # no --step given


@pytest.mark.parametrize("runs, cmp, text", [
    ((), None, "No green nightly on record"),
    (({"head_sha": HEAD},), None, "No commits since the last green nightly"),
    ((GREEN_RUN,), GhError("gh api GET compare failed: boom", 500),
     "could not be listed: gh api GET compare failed: boom"),
])
def test_lookup_outcomes_still_open_the_issue(runs, cmp, text):
    gh = gh_details(runs=runs, cmp=cmp)
    assert red.sync(gh, "failure", URL, HEAD, {"regress": "fail"}, "nightly.yml")[0] == "opened"
    assert text in body_of(gh)


def test_success_ignores_details():
    gh = gh_details(open_issues=[5])
    assert red.sync(gh, "success", URL, HEAD, {"regress": "pass"}, "nightly.yml") == \
        ("closed", [5])
    assert not any("compare" in c[1] or "runs" in c[1] for c in gh.calls)


@pytest.mark.parametrize("kw", [{"head": "abc"}, {"branch": "main;rm"}, {"branch": ""}])
def test_rejects_bad_head_or_branch(kw):
    gh = gh_details()
    with pytest.raises(MetricsError):
        red.sync(gh, "failure", URL, workflow="nightly.yml", **kw)
    assert gh.writes() == []


def test_cli_passes_head_steps_and_the_configured_workflow(monkeypatch, capsys):
    from argparse import Namespace
    from types import SimpleNamespace
    from metrics import cli
    from conftest import REPO_ROOT
    gh = gh_details()
    monkeypatch.setattr(cli, "Gh", lambda repo: gh)
    ctx = SimpleNamespace(cfg_dir=REPO_ROOT / "cfg", repo_root=REPO_ROOT)
    args = Namespace(action="red", repo="own/repo", outcome="failure", run_url=URL,
                     head=HEAD, branch="main", step=["regress=fail", "check=pass"])
    assert cli.run(ctx, args) == 0
    assert "nightly-red: opened #101" in capsys.readouterr().out
    assert "Failed steps: `regress`." in body_of(gh)
    args.step = ["regress=maybe"]
    assert cli.run(ctx, args) == 2
