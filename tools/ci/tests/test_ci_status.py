"""``./build ci status``: result mapping, the data-branch-only rule, the
posted request, and failure propagation. No network: ``gh`` is faked."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from build.ip import Context
from ci import status
from metrics.gh import GhError

REPO_ROOT = Path(__file__).resolve().parents[3]
SHA = "0123456789abcdef0123456789abcdef01234567"


class FakeGh:
    def __init__(self, repo, fail=None):
        self.repo = repo
        self.fail = fail
        self.calls = []

    def api(self, method, path, fields=None):
        self.calls.append((method, path, list(fields or [])))
        if self.fail:
            raise self.fail


def _ctx(tmp_path):
    return Context(REPO_ROOT, REPO_ROOT / "src", tmp_path / "workdir")


def _args(**kw):
    base = dict(result="success", sha=SHA, ref="refs/heads/nightly/2026-01-02",
                target_url="https://example.invalid/run/1", repo="own/repo", dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.mark.parametrize("result, state", [
    ("success", "success"), ("failure", "failure"), ("cancelled", "error"), ("skipped", "error")])
def test_result_maps_to_state(result, state):
    f = status.plan(result, SHA, "nightly/2026-01-02", "nightly/")
    assert f == {"state": state, "context": "ci-gate",
                 "description": f"ci-gate job on nightly/2026-01-02: {result}"}


@pytest.mark.parametrize("extra", [0, 1, 200])
def test_description_capped_at_140(extra):
    short = status.plan("failure", SHA, "nightly/x", "nightly/")["description"]
    # A branch name that brings the description to exactly 140 + extra characters.
    branch = "nightly/" + "x" * (len("x") + status.DESCRIPTION_MAX - len(short) + extra)
    d = status.plan("failure", SHA, branch, "nightly/")["description"]
    assert len(d) == status.DESCRIPTION_MAX
    if extra:
        assert d.endswith("...") and d.startswith("ci-gate job on nightly/xxx")
    else:
        assert d == f"ci-gate job on {branch}: failure"


@pytest.mark.parametrize("ref", [
    "refs/heads/nightly/x", "nightly/x", "nightly/test-1"])
def test_data_branch_refs_accepted(ref):
    assert status.plan("success", SHA, ref, "nightly/")["state"] == "success"


@pytest.mark.parametrize("ref", [
    "main", "refs/heads/main", "feature/nightly/x", "nightly-x", "nightly/",
    "refs/tags/nightly/x", "refs/pull/5/merge", ""])
def test_other_refs_refused(ref):
    with pytest.raises(status.StatusError):
        status.plan("success", SHA, ref, "nightly/")


@pytest.mark.parametrize("bad", [
    dict(result="neutral"), dict(result=""), dict(sha="abc123"), dict(sha=SHA.upper()),
    dict(sha=SHA + "0")])
def test_bad_inputs_refused(bad):
    kw = dict(result="success", sha=SHA, ref="nightly/x", branch_prefix="nightly/")
    kw.update(bad)
    with pytest.raises(status.StatusError):
        status.plan(**kw)


def test_target_url_optional():
    assert "target_url" not in status.plan("failure", SHA, "nightly/x", "nightly/")
    assert status.plan("failure", SHA, "nightly/x", "nightly/", "u")["target_url"] == "u"


def test_run_posts_one_status(tmp_path, capsys):
    made = []

    def factory(repo):
        made.append(FakeGh(repo))
        return made[-1]

    assert status.run(_ctx(tmp_path), _args(result="failure"), gh_factory=factory) == 0
    [gh] = made
    assert gh.repo == "own/repo"
    assert gh.calls == [("POST", f"repos/own/repo/statuses/{SHA}", [
        ("context", "ci-gate"),
        ("description", "ci-gate job on nightly/2026-01-02: failure"),
        ("state", "failure"),
        ("target_url", "https://example.invalid/run/1")])]
    assert "ci-gate=failure" in capsys.readouterr().out


def test_run_uses_the_configured_prefix(tmp_path):
    # The real cfg/metrics.yaml: a non-data branch is refused before any call.
    made = []
    code = status.run(_ctx(tmp_path), _args(ref="refs/heads/main"),
                      gh_factory=lambda r: made.append(r) or FakeGh(r))
    assert code == 2 and made == []


def test_dry_run_posts_nothing(tmp_path, capsys):
    made = []
    assert status.run(_ctx(tmp_path), _args(dry_run=True),
                      gh_factory=lambda r: made.append(r) or FakeGh(r)) == 0
    assert made == [] and "dry run" in capsys.readouterr().out


def test_gh_failure_exits_2(tmp_path, capsys):
    code = status.run(_ctx(tmp_path), _args(),
                      gh_factory=lambda r: FakeGh(r, fail=GhError("gh api POST failed: 403", 403)))
    assert code == 2
    assert "error: gh api POST failed" in capsys.readouterr().out


def test_cli_wiring(tmp_path):
    import subprocess
    proc = subprocess.run(
        [sys.executable, "-B", str(REPO_ROOT / "build"), "ci", "--workdir", str(tmp_path), "status",
         "--result", "success", "--sha", SHA, "--ref", "nightly/x", "--repo", "own/repo",
         "--dry-run"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ci-gate=success" in proc.stdout
    proc = subprocess.run(
        [sys.executable, "-B", str(REPO_ROOT / "build"), "ci", "status", "--result", "success",
         "--sha", SHA, "--ref", "main", "--dry-run"], capture_output=True, text=True, cwd=REPO_ROOT)
    assert proc.returncode == 2 and "refusing" in proc.stdout
