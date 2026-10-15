import shutil
import subprocess
from pathlib import Path

import pytest

from build.ip import REPO_ROOT, discover
from ci.config import load_rules
from regress.affected import GitError, changed_files, select_affected

# The real rules, so these tests pin the behavior cfg/ci.yaml configures.
RULES = load_rules(REPO_ROOT / "cfg")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path, fixture_src):
    """A git repo holding the fixture tree under src/ plus tools/, cfg/ and
    build, with one commit on main and a feature branch checked out."""
    r = tmp_path / "repo"
    r.mkdir()
    fixture_src.rename(r / "src")
    (r / "tools").mkdir()
    (r / "tools/x.py").write_text("")
    (r / "cfg").mkdir()
    (r / "cfg/levels.yaml").write_text("")
    (r / "build").write_text("")
    (r / "README.md").write_text("")
    git(r, "init", "-q", "-b", "main")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    git(r, "checkout", "-qb", "feature")
    return r


def affected(repo: Path) -> dict:
    root, changed = changed_files("main", repo / "src")
    return select_affected(discover(repo / "src"), root, changed, repo / "src", RULES)


def commit(repo: Path) -> None:
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "change")


def test_no_change(repo):
    assert affected(repo) == {}


def test_unrelated_file(repo):
    (repo / "README.md").write_text("x")
    assert affected(repo) == {}


def test_own_file_of_parent_only(repo):
    (repo / "src/dv/counter/tests/test_counter.py").write_text("# x\n")
    commit(repo)  # committed changes count
    assert affected(repo) == {"counter": "changed: src/dv/counter/tests/test_counter.py"}


def test_sub_ip_rtl_reaches_parent(repo):
    (repo / "src/rtl/counter_inc/inc.sv").write_text("// x\n")  # uncommitted
    got = affected(repo)
    assert got["counter_inc"] == "changed: src/rtl/counter_inc/inc.sv"
    assert got["counter"] == "reaches: src/rtl/counter_inc/inc.sv"


def test_sub_ip_dv_change_does_not_select_parent(repo):
    """A sub-IP's files are not the parent's own files; the parent is
    selected only if its filelists reach the changed file."""
    (repo / "src/dv/counter_inc/tests/test_inc.py").write_text("# x\n")
    assert affected(repo) == {"counter_inc": "changed: src/dv/counter_inc/tests/test_inc.py"}


def test_untracked_include_header_reaches(repo):
    (repo / "src/rtl/counter/include/new.svh").write_text("")
    assert set(affected(repo)) == {"counter"}


@pytest.mark.parametrize("path", ["tools/x.py", "cfg/levels.yaml", "build"])
def test_global_change_selects_all(repo, path):
    (repo / path).write_text("changed")
    got = affected(repo)
    assert set(got) == {"counter", "counter_inc"}
    assert all(v == f"global change: {path}" for v in got.values())


def test_broken_filelist_selects_ip(repo):
    (repo / "src/rtl/counter_inc/inc.sv").unlink()
    got = affected(repo)
    assert got["counter"] == "sim filelist does not resolve"


def test_path_with_spaces(repo):
    (repo / "src/dv/counter_inc/notes with spaces.txt").write_text("x")
    assert affected(repo) == {"counter_inc": "changed: src/dv/counter_inc/notes with spaces.txt"}


def test_rename_counts_old_and_new_path(repo):
    git(repo, "mv", "src/dv/counter_inc/tests/test_inc.py", "src/dv/counter/tests/moved.py")
    commit(repo)
    assert set(affected(repo)) == {"counter", "counter_inc"}


def test_bad_base(repo):
    with pytest.raises(GitError, match="merge-base"):
        changed_files("no-such-ref", repo / "src")


def test_shared_dv_change_selects_all(repo):
    (repo / "src/dv/common/helpers").mkdir(parents=True)
    (repo / "src/dv/common/helpers/util.py").write_text("")
    assert affected(repo) == {
        "counter": "shared DV change: src/dv/common/helpers/util.py",
        "counter_inc": "shared DV change: src/dv/common/helpers/util.py",
    }


def test_shared_rtl_change_selects_only_reaching_ips(repo):
    (repo / "src/rtl/common").mkdir(parents=True)
    (repo / "src/rtl/common/common_x.sv").write_text("")
    assert affected(repo) == {}


def test_sub_ip_model_change_selects_ancestors(repo):
    (repo / "src/dv/counter_inc/models").mkdir(parents=True)
    (repo / "src/dv/counter_inc/models/inc_model.py").write_text("")
    assert affected(repo) == {
        "counter": "sub-IP model: src/dv/counter_inc/models/inc_model.py",
        "counter_inc": "changed: src/dv/counter_inc/models/inc_model.py",
    }


def test_sub_ip_non_model_dv_change_selects_only_sub_ip(repo):
    (repo / "src/dv/counter_inc/tests/test_inc.py").write_text("# x\n")
    assert affected(repo) == {"counter_inc": "changed: src/dv/counter_inc/tests/test_inc.py"}


def test_parent_model_change_does_not_select_sub_ip(repo):
    (repo / "src/dv/counter/models").mkdir(parents=True)
    (repo / "src/dv/counter/models/counter_model.py").write_text("")
    assert affected(repo) == {"counter": "changed: src/dv/counter/models/counter_model.py"}


def test_arch_change_selects_only_its_ip(repo):
    (repo / "src/arch/counter/perf").mkdir(parents=True)
    (repo / "src/arch/counter/perf/s.yaml").write_text("")
    assert affected(repo) == {"counter": "changed: src/arch/counter/perf/s.yaml"}


def test_transitive_sub_ip_model_change_selects_all_ancestors(repo):
    deep = repo / "src/rtl/counter_inc_deep"
    deep.mkdir(parents=True)
    (deep / "counter_inc_deep.rtl.f").write_text("")
    inc_f = repo / "src/rtl/counter_inc/counter_inc.rtl.f"
    inc_f.write_text("-F ../counter_inc_deep/counter_inc_deep.rtl.f\n" + inc_f.read_text())
    commit_all(repo)
    (repo / "src/dv/counter_inc_deep/models").mkdir(parents=True)
    (repo / "src/dv/counter_inc_deep/models/deep_model.py").write_text("")
    rel = "src/dv/counter_inc_deep/models/deep_model.py"
    assert affected(repo) == {
        "counter": f"sub-IP model: {rel}",
        "counter_inc": f"sub-IP model: {rel}",
        "counter_inc_deep": f"changed: {rel}",
    }


def test_sibling_with_shared_prefix_is_not_owned(repo):
    """Ownership is by kind folder name, never by id prefix."""
    (repo / "src/dv/counter_incx").mkdir(parents=True)
    (repo / "src/dv/counter_incx/x.py").write_text("")
    assert affected(repo) == {}


def commit_all(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    git(repo, "branch", "-f", "main", "HEAD")


def test_repo_sub_ip_change_selects_every_ancestor(tmp_path):
    """On the repository's own src/ (IPs discovered, never named): changing
    any file of a sub-IP's rtl/ folder selects the sub-IP and every IP whose
    filelists reach it."""
    r = tmp_path / "repo"
    r.mkdir()
    shutil.copytree(REPO_ROOT / "src", r / "src")
    ips = discover(r / "src")
    subs = [ip for ip in ips if not ip.top_level]
    if not subs:
        pytest.skip("the repository has no sub-IP")
    for sub in subs:
        changed = sorted(p for p in sub.rtl_dir.glob("*.sv"))[0]
        rel = changed.relative_to(r).as_posix()
        got = select_affected(ips, r, [rel], r / "src", RULES)
        assert got[sub.id] == f"changed: {rel}"
        for ancestor in sub.ancestors:
            assert got[ancestor] == f"reaches: {rel}"


INTEGRATION = REPO_ROOT / "tools/checks/tests/fixtures/integration/src"


@pytest.fixture
def integration(tmp_path):
    """A git repo holding the integration fixture: top-level sys includes
    top-level a and b; a_x is a sub-IP of a."""
    r = tmp_path / "repo"
    shutil.copytree(INTEGRATION, r / "src")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    git(r, "checkout", "-qb", "feature")
    return r


def test_integrated_ip_change_selects_integrator(integration):
    rel = "src/rtl/a/xy_a.sv"
    (integration / rel).write_text((integration / rel).read_text() + "\n")
    assert affected(integration) == {"a": f"changed: {rel}", "sys": f"reaches: {rel}"}


def test_sub_ip_of_integrated_ip_selects_parent_and_integrator(integration):
    rel = "src/rtl/a_x/xy_a_x.sv"
    (integration / rel).write_text((integration / rel).read_text() + "\n")
    assert affected(integration) == {
        "a": f"reaches: {rel}", "a_x": f"changed: {rel}", "sys": f"reaches: {rel}"}


def test_integrated_ip_model_change_selects_integrator(integration):
    rel = "src/dv/b/models/b_model.py"
    (integration / "src/dv/b/models").mkdir()
    (integration / rel).write_text("")
    assert affected(integration) == {
        "b": f"changed: {rel}", "sys": f"integrated IP model: {rel}"}
