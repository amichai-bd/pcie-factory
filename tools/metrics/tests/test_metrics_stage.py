"""Staging a run's data files on the data branch (tools/metrics/stage.py,
``./build metrics stage``): two runs that publish before the first data PR
merges both keep their record, other open data PRs are carried, and the
series holds each commit once."""

from __future__ import annotations

import json
import shutil
from argparse import Namespace

import pytest

from build.ip import Context
from metrics import cli, guard, stage
from metrics.config import MetricsError

from conftest import REPO_ROOT, FakeGh, commit, git

DATA = "wiki/tracker/data"
BOT = "bot@example.invalid"


def rec(when: str) -> str:
    return json.dumps({"schema_version": 1, "meta": {"generated_at": when}}) + "\n"


def line(sha: str, index: int, ci: str = "none") -> dict:
    return {"sha": sha, "index": index, "date": f"2026-01-0{index + 1}T00:00:00Z", "ci": {"status": ci}}


def series(*lines: dict) -> str:
    return "".join(json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n" for x in lines)


@pytest.fixture
def env(repo, tmp_path, monkeypatch):
    """(local clone, origin) with cfg/ and a base series on main."""
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "bot")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, BOT)
    (repo / "cfg").mkdir(exist_ok=True)
    shutil.copy(REPO_ROOT / "cfg" / "metrics.yaml", repo / "cfg" / "metrics.yaml")
    commit(repo, {f"{DATA}/commits.jsonl": series(line("a0", 0))}, "base data", "2026-01-02T12:00:00")
    origin = tmp_path / "origin.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(origin))
    local = tmp_path / "local"
    git(tmp_path, "clone", "-q", str(origin), str(local))
    return local, origin


def run_dir(tmp_path, name: str, files: dict[str, str]):
    d = tmp_path / name
    d.mkdir()
    for k, v in files.items():
        (d / k).write_text(v)
    return d


def do_stage(local, cfg, gh, data, tmp_path, branch="nightly/2026-01-03"):
    return stage.stage(local, gh, cfg, "main", branch, data, tmp_path / "wt", "nightly: test",
                       log=lambda *_: None)


def push(local, branch):
    git(local, "push", "-q", "--force", "origin", f"refs/heads/{branch}:refs/heads/{branch}")


def prs(*heads):
    return FakeGh({"repos/own/repo/pulls": [
        {"number": n, "head": {"ref": ref, "repo": {"full_name": "own/repo"}}} for n, ref in heads]})


def files_on(origin, rev):
    return sorted(git(origin, "ls-tree", "--name-only", f"{rev}:{DATA}").split())


def series_on(origin, rev):
    return [json.loads(x) for x in git(origin, "show", f"{rev}:{DATA}/commits.jsonl").splitlines()]


R1, R2, R3 = "2026-01-03T010000Z.json", "2026-01-03T020000Z.json", "2026-01-04T010000Z.json"


def test_new_branch_when_no_data_pr_is_open(env, cfg, tmp_path):
    local, origin = env
    data = run_dir(tmp_path, "run1", {R1: rec("2026-01-03T01:00:00Z"),
                                      "commits.jsonl": series(line("a0", 0, "ok"), line("a1", 1))})
    out = do_stage(local, cfg, prs(), data, tmp_path)
    assert (out["mode"], out["branch"], out["number"], out["carried"], out["changed"]) == \
        ("new", "nightly/2026-01-03", "", "", "true")
    wt = tmp_path / "wt"
    assert git(wt, "rev-parse", "HEAD~1") == git(local, "rev-parse", "origin/main")
    assert git(wt, "log", "-1", "--format=%ae") == BOT
    assert guard.run(local, out["branch"], "origin/main", out["sha"], cfg)[0] is True


def test_back_to_back_runs_both_reach_the_branch(env, cfg, tmp_path):
    """Run 2 publishes while run 1's data PR (#10) is still open: it commits
    on top of that PR's head, keeping run 1's record, with one line per commit."""
    local, origin = env
    run1 = run_dir(tmp_path, "run1", {R1: rec("2026-01-03T01:00:00Z"),
                                      "commits.jsonl": series(line("a0", 0), line("a1", 1))})
    first = do_stage(local, cfg, prs(), run1, tmp_path)
    push(local, first["branch"])
    run2 = run_dir(tmp_path, "run2", {R2: rec("2026-01-03T02:00:00Z"),
                                      "commits.jsonl": series(line("a0", 0, "ok"), line("a1", 1),
                                                              line("a2", 2))})
    second = do_stage(local, cfg, prs((10, "nightly/2026-01-03")), run2, tmp_path,
                      branch="nightly/2026-01-04")
    assert (second["mode"], second["branch"], second["number"]) == ("continue", "nightly/2026-01-03", "10")
    wt = tmp_path / "wt"
    assert git(wt, "rev-parse", "HEAD~1") == first["sha"]  # on top of the open PR's head
    push(local, second["branch"])
    assert files_on(origin, second["branch"]) == [R1, R2, "commits.jsonl", "old.json"]
    lines = series_on(origin, second["branch"])
    assert [x["sha"] for x in lines] == ["a0", "a1", "a2"]  # no duplicate
    assert lines[0]["ci"] == {"status": "ok"}  # the later run's line wins
    assert guard.run(local, second["branch"], "origin/main", second["sha"], cfg)[0] is True


def test_other_open_data_prs_are_carried(env, cfg, tmp_path):
    local, origin = env
    for name, branch, r, when, lines in (
            ("run1", "nightly/2026-01-03", R1, "2026-01-03T01:00:00Z", [line("a0", 0), line("a1", 1)]),
            ("run2", "nightly/2026-01-04", R3, "2026-01-04T01:00:00Z", [line("a0", 0), line("a1", 1)])):
        git(local, "checkout", "-q", "main")
        out = do_stage(local, cfg, prs(), run_dir(tmp_path, name, {r: rec(when), "commits.jsonl": series(*lines)}),
                       tmp_path, branch=branch)
        push(local, out["branch"])
    run3 = run_dir(tmp_path, "run3", {R2: rec("2026-01-03T02:00:00Z"),
                                      "commits.jsonl": series(line("a0", 0), line("a1", 1), line("a2", 2))})
    gh = prs((11, "nightly/2026-01-04"), (10, "nightly/2026-01-03"))
    out = do_stage(local, cfg, gh, run3, tmp_path)
    assert (out["mode"], out["number"], out["carried"]) == ("continue", "11", "10")
    push(local, out["branch"])
    assert files_on(origin, out["branch"]) == [R1, R2, R3, "commits.jsonl", "old.json"]
    assert [x["sha"] for x in series_on(origin, out["branch"])] == ["a0", "a1", "a2"]


def test_a_data_pr_from_a_fork_is_ignored(env, cfg, tmp_path):
    local, _ = env
    gh = FakeGh({"repos/own/repo/pulls": [
        {"number": 5, "head": {"ref": "nightly/2026-01-03", "repo": {"full_name": "fork/repo"}}},
        {"number": 6, "head": {"ref": "15-feature", "repo": {"full_name": "own/repo"}}}]})
    assert stage.open_data_prs(gh, "main", cfg) == []


def test_an_open_data_pr_failing_the_guard_stops_staging(env, cfg, tmp_path):
    local, _ = env
    git(local, "checkout", "-q", "-b", "nightly/bad")
    commit(local, {"tools/x/run.py": "print(2)\n"}, "not data", "2026-01-03T00:00:00")
    push(local, "nightly/bad")
    git(local, "checkout", "-q", "main")
    data = run_dir(tmp_path, "run", {R1: rec("2026-01-03T01:00:00Z")})
    with pytest.raises(MetricsError, match="#7 .* fails the data guard"):
        do_stage(local, cfg, prs((7, "nightly/bad")), data, tmp_path)


def test_a_different_record_of_the_same_name_is_refused(env, cfg, tmp_path):
    local, _ = env
    out = do_stage(local, cfg, prs(), run_dir(tmp_path, "run1", {R1: rec("2026-01-03T01:00:00Z")}), tmp_path)
    push(local, out["branch"])
    again = run_dir(tmp_path, "run2", {R1: rec("2026-01-03T01:00:09Z")})
    with pytest.raises(MetricsError, match="differs from the record already on the branch"):
        do_stage(local, cfg, prs((10, out["branch"])), again, tmp_path)


def test_nothing_to_publish(env, cfg, tmp_path):
    local, _ = env
    data = run_dir(tmp_path, "run", {"commits.jsonl": series(line("a0", 0))})
    out = do_stage(local, cfg, prs(), data, tmp_path)
    assert (out["changed"], out["sha"]) == ("false", "")


def test_merge_series():
    old = [line("b", 1), line("a", 0)]
    new = [line("a", 0, "ok"), line("c", 2)]
    merged = stage.merge_series(old, new)
    assert [x["sha"] for x in merged] == ["a", "b", "c"]
    assert merged[0]["ci"] == {"status": "ok"}


@pytest.mark.parametrize("name", ["../x", "-x", "a b", "a..b", None])
def test_bad_branch_names_are_refused(name):
    with pytest.raises(MetricsError, match="invalid branch name"):
        stage._ref(name)


def test_stage_command_writes_its_outputs(env, cfg, tmp_path, capsys):
    local, _ = env
    data = run_dir(tmp_path, "run", {R1: rec("2026-01-03T01:00:00Z")})
    ctx = Context(local, local / "src", local / "workdir")
    args = Namespace(action="stage", repo="own/repo", base="main", branch="nightly/2026-01-03",
                     data=str(data), worktree=str(tmp_path / "wt"), message="m", out=str(tmp_path / "o"))
    assert cli.stage_cmd(ctx, args, gh_factory=lambda repo: prs()) == 0
    text = (tmp_path / "o").read_text()
    assert "mode=new\n" in text and "changed=true\n" in text and "branch=nightly/2026-01-03\n" in text


def test_nightly_publish_stages_and_never_closes_unmerged_data():
    """publish stages through `./build metrics stage`, guards, pushes forward
    for a continued PR and re-stages on a lost race; it closes only carried
    PRs. The trends step deliberately has no continue-on-error."""
    import yaml
    wf = yaml.safe_load((REPO_ROOT / ".github/workflows/nightly.yml").read_text())
    script = next(s["run"] for s in wf["jobs"]["publish"]["steps"] if "metrics stage" in s.get("run", ""))
    assert "for attempt in 1 2 3" in script
    assert "./build metrics guard --branch" in script
    assert '[ "$mode" = new ] && force=--force' in script
    assert '"open $sha"' in script  # still open at our commit, else stage again
    assert "for old in $(out carried)" in script and "Superseded" not in script
    assert script.index("./build metrics guard") < script.index("push $force")
    trends_step = next(s for s in wf["jobs"]["nightly"]["steps"] if s.get("name") == "Trend drop alerts")
    assert "continue-on-error" not in trends_step
    env = next(s for s in wf["jobs"]["publish"]["steps"] if "metrics stage" in s.get("run", ""))["env"]
    from metrics.config import load
    assert env["GIT_AUTHOR_EMAIL"] == env["GIT_COMMITTER_EMAIL"] == load(REPO_ROOT / "cfg").data_author
