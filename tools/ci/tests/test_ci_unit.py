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
    """Fixture tree with model tests for the parent and the sub-IP, and an
    arch model with a perf scenario for the parent."""
    r = tmp_path / "repo"
    r.mkdir()
    fixture_src.rename(r / "src")
    for ip in ("counter", "counter_inc"):
        d = r / "src/dv" / ip / "models"
        d.mkdir(parents=True)
        (d / "test_m.py").write_text("def test_ok():\n    assert True\n")
    arch = r / "src/arch/counter"
    (arch / "perf").mkdir(parents=True)
    (arch / "test_a.py").write_text("def test_ok():\n    assert True\n")
    (arch / "xy_counter_arch.py").write_text("def perf(workload, seed):\n    return {'x': 1}\n")
    (arch / "perf/s.yaml").write_text("description: d\nseed: 1\nworkload: {}\n"
                                      "targets: {x: {min: 1}}\n")
    (r / "tools").mkdir()
    (r / "tools/x.py").write_text("")
    (r / "README.md").write_text("")
    (r / "src/README.md").write_text("| Item | Value |\n| --- | --- |\n"
                                     "| Module/package/file prefix | `xy_` |\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    git(r, "checkout", "-qb", "feature")
    return r


def ctx(repo: Path) -> Context:
    return Context(repo, repo / "src", repo / "workdir")


def names(suites):
    return [s.name for s in suites]


ALL = ["tools", "models:counter", "arch:counter", "perf:counter", "models:counter_inc"]


def test_all_without_affected(repo):
    got = unit.plan(ctx(repo), RULES, None)
    assert names(got) == ALL
    assert got[0].args == ("-m", "pytest", "-q", "tools") and got[0].cwd == repo
    assert got[1].cwd == repo / "src/dv/counter/models"
    assert got[2].cwd == repo / "src/arch/counter" and got[2].args[:2] == ("-m", "pytest")
    assert got[3].args[1:4] == ("arch", "--ip", "counter")


def test_arch_change_runs_the_ip_suites(repo):
    (repo / "src/arch/counter/xy_counter_arch.py").write_text("def perf(w, s):\n    return {}\n")
    got = unit.plan(ctx(repo), RULES, "main")
    assert names(got) == ["models:counter", "arch:counter", "perf:counter"]
    assert got[0].reason == "changed: src/arch/counter/xy_counter_arch.py"


def test_arch_suites_run_and_perf_writes_its_result(repo, capsys):
    suites = [s for s in unit.plan(ctx(repo), RULES, None) if s.name.split(":")[0] in
              ("arch", "perf")]
    assert unit.run(suites, out=sys.stdout) == 0
    assert (repo / "workdir/counter/arch/perf.json").is_file()
    (repo / "src/arch/counter/perf/s.yaml").write_text(
        "description: d\nseed: 1\nworkload: {}\ntargets: {x: {min: 2}}\n")
    assert unit.run(suites, out=sys.stdout) == 1
    assert "[unit] perf:counter: FAIL (exit 1)" in capsys.readouterr().out


def test_docs_only_change_runs_nothing(repo):
    (repo / "README.md").write_text("x")
    assert unit.plan(ctx(repo), RULES, "main") == []


def test_global_change_runs_everything(repo):
    (repo / "tools/x.py").write_text("x = 1\n")
    got = unit.plan(ctx(repo), RULES, "main")
    assert names(got) == ALL
    assert got[0].reason == "global change: tools/x.py"


def test_sub_ip_model_change_runs_parent_models(repo):
    (repo / "src/dv/counter_inc/models/inc_model.py").write_text("")
    assert names(unit.plan(ctx(repo), RULES, "main")) == [
        "models:counter", "arch:counter", "perf:counter", "models:counter_inc"]


def test_parent_change_runs_only_parent_models(repo):
    (repo / "src/dv/counter/models/counter_model.py").write_text("")
    assert names(unit.plan(ctx(repo), RULES, "main")) == [
        "models:counter", "arch:counter", "perf:counter"]


def test_models_dir_without_tests_is_skipped(repo):
    (repo / "src/dv/counter_inc/models/test_m.py").unlink()
    assert names(unit.plan(ctx(repo), RULES, None)) == ALL[:-1]


def test_run_reports_failure_and_continues(repo, capsys):
    (repo / "src/dv/counter/models/test_m.py").write_text("def test_bad():\n    assert False\n")
    suites = [s for s in unit.plan(ctx(repo), RULES, None) if s.name.startswith("models:")]
    assert unit.run(suites, out=sys.stdout) == 1
    out = capsys.readouterr().out
    assert "[unit] models:counter: FAIL" in out and "[unit] models:counter_inc: PASS" in out
    assert "1/2 suite(s) passed; failed: models:counter" in out


def test_run_nothing_passes(capsys):
    assert unit.run([], out=sys.stdout) == 0
    assert "no suites selected" in capsys.readouterr().out
