import subprocess
from pathlib import Path

import pytest

from build.ip import discover
from regress.affected import GitError, changed_files, select_affected


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
    return select_affected(discover(repo / "src"), root, changed, repo / "src")


def commit(repo: Path) -> None:
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "change")


def test_no_change(repo):
    assert affected(repo) == {}


def test_unrelated_file(repo):
    (repo / "README.md").write_text("x")
    assert affected(repo) == {}


def test_own_file_of_parent_only(repo):
    (repo / "src/counter/dv/tests/test_counter.py").write_text("# x\n")
    commit(repo)  # committed changes count
    assert affected(repo) == {"counter": "changed: src/counter/dv/tests/test_counter.py"}


def test_sub_ip_rtl_reaches_parent(repo):
    (repo / "src/counter/inc/rtl/inc.sv").write_text("// x\n")  # uncommitted
    got = affected(repo)
    assert got["counter/inc"] == "changed: src/counter/inc/rtl/inc.sv"
    assert got["counter"] == "reaches: src/counter/inc/rtl/inc.sv"


def test_sub_ip_dv_change_does_not_select_parent(repo):
    """Files under a nested sub-IP are not the parent's own files; the parent
    is selected only if its filelists reach the changed file."""
    (repo / "src/counter/inc/dv/tests/test_inc.py").write_text("# x\n")
    assert affected(repo) == {"counter/inc": "changed: src/counter/inc/dv/tests/test_inc.py"}


def test_untracked_include_header_reaches(repo):
    (repo / "src/counter/rtl/include/new.svh").write_text("")
    assert set(affected(repo)) == {"counter"}


@pytest.mark.parametrize("path", ["tools/x.py", "cfg/levels.yaml", "build"])
def test_global_change_selects_all(repo, path):
    (repo / path).write_text("changed")
    got = affected(repo)
    assert set(got) == {"counter", "counter/inc"}
    assert all(v == f"global change: {path}" for v in got.values())


def test_broken_filelist_selects_ip(repo):
    (repo / "src/counter/inc/rtl/inc.sv").unlink()
    got = affected(repo)
    assert got["counter"] == "sim filelist does not resolve"


def test_path_with_spaces(repo):
    (repo / "src/counter/inc/dv/notes with spaces.txt").write_text("x")
    assert affected(repo) == {"counter/inc": "changed: src/counter/inc/dv/notes with spaces.txt"}


def test_rename_counts_old_and_new_path(repo):
    git(repo, "mv", "src/counter/inc/dv/tests/test_inc.py", "src/counter/dv/tests/moved.py")
    commit(repo)
    assert set(affected(repo)) == {"counter", "counter/inc"}


def test_bad_base(repo):
    with pytest.raises(GitError, match="merge-base"):
        changed_files("no-such-ref", repo / "src")


def test_shared_dv_change_selects_all(repo):
    (repo / "src/common/dv/helpers").mkdir(parents=True)
    (repo / "src/common/dv/helpers/util.py").write_text("")
    assert affected(repo) == {
        "counter": "shared DV change: src/common/dv/helpers/util.py",
        "counter/inc": "shared DV change: src/common/dv/helpers/util.py",
    }


def test_shared_rtl_change_selects_only_reaching_ips(repo):
    (repo / "src/common/rtl").mkdir(parents=True)
    (repo / "src/common/rtl/common_x.sv").write_text("")
    assert affected(repo) == {}
