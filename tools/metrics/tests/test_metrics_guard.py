import shutil
from argparse import Namespace

from build.ip import Context
from metrics import cli, guard

from conftest import REPO_ROOT, commit, git


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


def _guard(ctx, branch, base="main", head="HEAD"):
    return cli.run(ctx, Namespace(action="guard", branch=branch, base=base, head=head))


def test_guard_on_git_branches(repo, capsys):
    ctx = _ctx(repo)
    git(repo, "checkout", "-q", "-b", "nightly/2026-01-03")
    commit(repo, {"wiki/tracker/data/2026-01-03.json": "{}\n"}, "data", "2026-01-03T00:00:00")
    assert _guard(ctx, "nightly/2026-01-03") == 0
    assert "PASS" in capsys.readouterr().out
    # A rename out of the data directory shows both paths and fails.
    git(repo, "mv", "wiki/tracker/data/2026-01-03.json", "README2.md")
    commit(repo, {}, "move", "2026-01-03T01:00:00")
    assert _guard(ctx, "nightly/2026-01-03") == 1
    assert "README2.md" in capsys.readouterr().out
    # main moving on does not count against the branch (merge-base diff).
    git(repo, "checkout", "-q", "main")
    commit(repo, {"tools/x/new.py": "x = 1\n"}, "main moves", "2026-01-04T00:00:00")
    git(repo, "checkout", "-q", "-b", "nightly/2026-01-05")
    commit(repo, {"wiki/tracker/data/2026-01-05.json": "{}\n"}, "data", "2026-01-05T00:00:00")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"cfg/other.yaml": "a: 1\n"}, "main again", "2026-01-06T00:00:00")
    assert _guard(ctx, "nightly/2026-01-05", base="main", head="nightly/2026-01-05") == 0


def test_guard_bad_revision(repo, capsys):
    ctx = _ctx(repo)
    assert _guard(ctx, "nightly/x", base="no-such-ref") == 2
    assert "error: metrics guard" in capsys.readouterr().err
