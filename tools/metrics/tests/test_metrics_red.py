import pytest

from metrics import red
from metrics.config import MetricsError

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
