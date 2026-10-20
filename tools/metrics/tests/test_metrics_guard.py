import json
import shutil
from argparse import Namespace

import pytest

from build.ip import Context
from metrics import cli, guard

from conftest import REPO_ROOT, FakeGh, commit, git


def rec(when: str = "2026-01-03T00:00:00Z") -> str:
    """A minimal valid schema 1 record."""
    return json.dumps({"schema_version": 1, "meta": {"generated_at": when}}) + "\n"


def series(*shas: str) -> str:
    return "".join(json.dumps({"sha": s, "index": i}) + "\n" for i, s in enumerate(shas))


R1 = "wiki/tracker/data/2026-01-03T000000Z.json"
R2 = "wiki/tracker/data/2026-01-05T000000Z.json"
SERIES = "wiki/tracker/data/commits.jsonl"


def test_check_rules(cfg):
    data = ["wiki/tracker/data/2026-01-01.json", "wiki/tracker/data/commits.jsonl"]
    assert guard.check("nightly/2026-01-01", data, cfg)[0] is True
    ok, lines = guard.check("nightly/2026-01-01", data + ["tools/x.py"], cfg)
    assert ok is False and any("outside wiki/tracker/data/: tools/x.py" in x for x in lines)
    assert guard.check("nightly/2026-01-01", ["wiki/tracker/dataX/a.json"], cfg)[0] is False
    assert guard.check("nightly/2026-01-01", [], cfg)[0] is False
    # Other branches are not guarded.
    assert guard.check("15-feature", ["tools/x.py"], cfg)[0] is True


def _ctx(repo):
    (repo / "cfg").mkdir(exist_ok=True)
    shutil.copy(REPO_ROOT / "cfg" / "metrics.yaml", repo / "cfg" / "metrics.yaml")
    git(repo, "add", "-A")
    commit(repo, {}, "cfg", "2026-01-02T12:00:00")
    return Context(repo, repo / "src", repo / "workdir")


def _guard(ctx, branch, base="main", head="HEAD", **kw):
    return cli.run(ctx, Namespace(action="guard", branch=branch, base=base, head=head, **kw))


def test_guard_on_git_branches(repo, capsys):
    ctx = _ctx(repo)
    git(repo, "checkout", "-q", "-b", "nightly/2026-01-03")
    commit(repo, {R1: rec()}, "data", "2026-01-03T00:00:00")
    assert _guard(ctx, "nightly/2026-01-03") == 0
    assert "PASS" in capsys.readouterr().out
    # A rename out of the data directory shows both paths and fails.
    git(repo, "mv", R1, "README2.md")
    commit(repo, {}, "move", "2026-01-03T01:00:00")
    assert _guard(ctx, "nightly/2026-01-03") == 1
    assert "README2.md" in capsys.readouterr().out
    # main moving on does not count against the branch (merge-base diff).
    git(repo, "checkout", "-q", "main")
    commit(repo, {"tools/x/new.py": "x = 1\n"}, "main moves", "2026-01-04T00:00:00")
    git(repo, "checkout", "-q", "-b", "nightly/2026-01-05")
    commit(repo, {R2: rec("2026-01-05T00:00:00Z")}, "data", "2026-01-05T00:00:00")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"cfg/other.yaml": "a: 1\n"}, "main again", "2026-01-06T00:00:00")
    assert _guard(ctx, "nightly/2026-01-05", base="main", head="nightly/2026-01-05") == 0


def test_guard_bad_revision(repo, capsys):
    ctx = _ctx(repo)
    assert _guard(ctx, "nightly/x", base="no-such-ref") == 2
    assert "error: metrics guard" in capsys.readouterr().err


# ------------------------------------------------- data file content rules
@pytest.mark.parametrize("files, why", [
    ({R1: "not json\n"}, "not JSON"),
    ({R1: json.dumps({"schema_version": 2, "meta": {"generated_at": "2026-01-03T00:00:00Z"}})}, "schema 1"),
    ({R1: json.dumps({"schema_version": True, "meta": {"generated_at": "2026-01-03T00:00:00Z"}})}, "schema 1"),
    ({R1: json.dumps({"schema_version": 1.0, "meta": {"generated_at": "2026-01-03T00:00:00Z"}})}, "schema 1"),
    ({R1: json.dumps({"schema_version": 1, "meta": {}})}, "generated_at"),
    ({R1: json.dumps({"schema_version": 1, "meta": {"generated_at": "2026-02-30T00:00:00Z"}})}, "invalid meta.generated_at"),
    ({R1: json.dumps({"schema_version": 1, "meta": {"generated_at": "2026-01-03T25:00:00Z"}})}, "invalid meta.generated_at"),
    ({"wiki/tracker/data/2026-01-03.json": rec()}, "not a run record"),
    ({"wiki/tracker/data/sub/2026-01-03T000000Z.json": rec()}, "not a run record"),
    ({"wiki/tracker/data/notes.md": "x\n"}, "not a run record"),
    ({SERIES: "{}\n"}, "has no sha"),
    ({SERIES: "nope\n"}, "not JSON"),
    ({SERIES: series("a1", "a1")}, "duplicate sha"),
])
def test_malformed_data_files_fail(repo, capsys, files, why):
    ctx = _ctx(repo)
    git(repo, "checkout", "-q", "-b", "nightly/x")
    commit(repo, files, "data", "2026-01-03T00:00:00")
    assert _guard(ctx, "nightly/x") == 1
    assert why in capsys.readouterr().out


def test_removing_or_rewriting_data_fails(repo, capsys):
    ctx = _ctx(repo)
    commit(repo, {R1: rec(), SERIES: series("a1", "a2")}, "base data", "2026-01-03T00:00:00")
    git(repo, "checkout", "-q", "-b", "nightly/rm")
    commit(repo, {R1: None, R2: rec()}, "drop a record", "2026-01-04T00:00:00")
    assert _guard(ctx, "nightly/rm") == 1
    assert "deleted" in capsys.readouterr().out
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "nightly/rewrite")
    commit(repo, {R1: rec("2026-01-03T00:00:01Z")}, "rewrite", "2026-01-04T00:00:00")
    assert _guard(ctx, "nightly/rewrite") == 1
    assert "never rewritten" in capsys.readouterr().out
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "nightly/lose")
    commit(repo, {SERIES: series("a2", "a3")}, "lose a line", "2026-01-04T00:00:00")
    assert _guard(ctx, "nightly/lose") == 1
    assert "drops 1 line(s) of the base, e.g. a1" in capsys.readouterr().out
    # Growing the series and adding a record passes.
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "nightly/grow")
    commit(repo, {SERIES: series("a1", "a2", "a3"), R2: rec()}, "grow", "2026-01-04T00:00:00")
    assert _guard(ctx, "nightly/grow") == 0


def test_select_writes_the_merge_base_only_for_a_passing_data_branch(repo, tmp_path):
    ctx = _ctx(repo)
    base = git(repo, "rev-parse", "HEAD")
    env = tmp_path / "env"
    assert _guard(ctx, "15-feature", select=str(env)) == 0
    assert not env.exists()  # not a data branch: no selection
    git(repo, "checkout", "-q", "-b", "nightly/ok")
    commit(repo, {R1: rec()}, "data", "2026-01-03T00:00:00")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"tools/x/more.py": "y = 2\n"}, "main moves", "2026-01-04T00:00:00")
    assert _guard(ctx, "nightly/ok", head="nightly/ok", select=str(env)) == 0
    assert env.read_text() == f"AFFECTED=--affected {base}\n"
    # A mixed change fails and selects nothing.
    git(repo, "checkout", "-q", "-b", "nightly/mixed", "nightly/ok")
    commit(repo, {"tools/x/run.py": "print(1)\n"}, "mixed", "2026-01-05T00:00:00")
    env.unlink()
    assert _guard(ctx, "nightly/mixed", head="nightly/mixed", select=str(env)) == 1
    assert not env.exists()


def test_pr_base_resolution():
    pulls = "repos/own/repo/pulls"
    gh = FakeGh({pulls: [{"base": {"ref": "test-base"}}, {"base": {"ref": "main"}}]})
    seen = []
    gh.paginate = lambda path, key=None, limit=5000: seen.append(path) or FakeGh.paginate(gh, path, key, limit)
    assert cli.pr_base(gh, "nightly/x", "main") == "main"
    assert seen == [f"{pulls}?state=open&head=own:nightly/x"]
    assert cli.pr_base(FakeGh({pulls: [{"base": {"ref": "test-base"}}]}), "nightly/x", "main") == "test-base"
    assert cli.pr_base(FakeGh({pulls: []}), "nightly/x", "main") == "main"
    assert cli.pr_base(FakeGh({pulls: [{"base": {"ref": "bad ref;"}}]}), "nightly/x", "main") == "main"


def test_pr_base_fetches_the_base_and_guards(repo, tmp_path, capsys):
    ctx = _ctx(repo)
    origin = tmp_path / "origin.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "checkout", "-q", "-b", "nightly/pr")
    commit(repo, {R1: rec()}, "data", "2026-01-03T00:00:00")
    gh = FakeGh({"repos/own/repo/pulls": [{"base": {"ref": "main"}}]})
    args = Namespace(action="guard", branch="nightly/pr", base=None, pr_base="main", head="HEAD",
                     select=None, repo="own/repo")
    assert cli.guard_cmd(ctx, args, gh_factory=lambda repo: gh) == 0
    out = capsys.readouterr().out
    assert "base: origin/main" in out and "PASS" in out
    # Other branches pass at once, with no API call.
    args.branch = "15-feature"
    assert cli.guard_cmd(ctx, args, gh_factory=lambda repo: pytest.fail("no API call")) == 0
