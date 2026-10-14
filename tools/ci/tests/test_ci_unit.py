import subprocess
import sys
from pathlib import Path

import pytest

from build.ip import REPO_ROOT, Context
from ci import unit
from ci.config import load_rules

RULES = load_rules(REPO_ROOT / "cfg")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, fixture_src):
    """Fixture tree with model tests for the parent and the sub-IP."""
    r = tmp_path / "repo"
    r.mkdir()
    fixture_src.rename(r / "src")
    for ip in ("counter", "counter/inc"):
        d = r / "src" / ip / "dv/models"
        d.mkdir(parents=True)
        (d / "test_m.py").write_text("def test_ok():\n    assert True\n")
    (r / "tools").mkdir()
    (r / "tools/x.py").write_text("")
    (r / "README.md").write_text("")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    git(r, "checkout", "-qb", "feature")
    return r


def ctx(repo: Path) -> Context:
    return Context(repo, repo / "src", repo / "workdir")


def names(suites):
    return [s.name for s in suites]


def test_all_without_affected(repo):
    got = unit.plan(ctx(repo), RULES, None)
    assert names(got) == ["tools", "models:counter", "models:counter/inc"]
    assert got[0].args == ("-q", "tools") and got[0].cwd == repo
    assert got[1].cwd == repo / "src/counter/dv/models"


def test_docs_only_change_runs_nothing(repo):
    (repo / "README.md").write_text("x")
    assert unit.plan(ctx(repo), RULES, "main") == []


def test_global_change_runs_everything(repo):
    (repo / "tools/x.py").write_text("x = 1\n")
    got = unit.plan(ctx(repo), RULES, "main")
    assert names(got) == ["tools", "models:counter", "models:counter/inc"]
    assert got[0].reason == "global change: tools/x.py"


def test_sub_ip_model_change_runs_parent_models(repo):
    (repo / "src/counter/inc/dv/models/inc_model.py").write_text("")
    assert names(unit.plan(ctx(repo), RULES, "main")) == ["models:counter", "models:counter/inc"]


def test_parent_change_runs_only_parent_models(repo):
    (repo / "src/counter/dv/models/counter_model.py").write_text("")
    assert names(unit.plan(ctx(repo), RULES, "main")) == ["models:counter"]


def test_models_dir_without_tests_is_skipped(repo):
    (repo / "src/counter/inc/dv/models/test_m.py").unlink()
    assert names(unit.plan(ctx(repo), RULES, None)) == ["tools", "models:counter"]


def test_run_reports_failure_and_continues(repo, capsys):
    (repo / "src/counter/dv/models/test_m.py").write_text("def test_bad():\n    assert False\n")
    suites = [s for s in unit.plan(ctx(repo), RULES, None) if s.name != "tools"]
    assert unit.run(suites, out=sys.stdout) == 1
    out = capsys.readouterr().out
    assert "[unit] models:counter: FAIL" in out and "[unit] models:counter/inc: PASS" in out
    assert "1/2 suite(s) passed; failed: models:counter" in out


def test_run_nothing_passes(capsys):
    assert unit.run([], out=sys.stdout) == 0
    assert "no suites selected" in capsys.readouterr().out
