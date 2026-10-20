import shutil
import subprocess
from pathlib import Path

import pytest

import regress.run as regress
from build.ip import REPO_ROOT, Context, discover, units
from ci.config import load_rules
from regress.affected import GitError, changed_files, select_affected, select_affected_levels
from regress.deps import imported_names

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


def affected_levels(repo: Path) -> dict:
    root, changed = changed_files("main", repo / "src")
    return select_affected_levels(discover(repo / "src"), root, changed, repo / "src", RULES)


def test_global_change_alone_caps_every_ip_at_the_global_levels(repo):
    # A tools-only change: every IP, but only the smoke level (cfg/ci.yaml).
    (repo / "tools/x.py").write_text("changed")
    got = affected_levels(repo)
    assert got == {ip: ("global change: tools/x.py", (0,))
                   for ip in ("counter", "counter_inc")}


def test_design_change_keeps_every_level_beside_a_global_change(repo):
    # The IP a design change selects (and every IP that reaches it) runs
    # every requested level; the rest get the global smoke only.
    (repo / "tools/x.py").write_text("changed")
    (repo / "src/dv/counter/tests/test_counter.py").write_text("# x\n")
    got = affected_levels(repo)
    assert got["counter"] == ("changed: src/dv/counter/tests/test_counter.py", None)
    assert got["counter_inc"] == ("global change: tools/x.py", (0,))
    (repo / "src/rtl/counter_inc/inc.sv").write_text("// x\n")
    got = affected_levels(repo)
    assert got["counter_inc"] == ("changed: src/rtl/counter_inc/inc.sv", None)
    assert got["counter"][1] is None


def test_shared_dv_change_runs_every_level(repo):
    d = repo / "src/dv/common"
    d.mkdir(parents=True, exist_ok=True)
    (d / "helper.py").write_text("")
    got = affected_levels(repo)
    assert got and all(levels is None for _, levels in got.values())


def test_docs_only_selects_nothing(repo):
    (repo / "README.md").write_text("x")
    assert affected_levels(repo) == {}


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


def test_shared_unit_owns_the_common_folders(repo):
    (repo / "src/dv/common").mkdir(parents=True)
    (repo / "src/dv/common/common.sim.f").write_text("-F ../../rtl/common/common_x.rtl.f\n")
    git(repo, "add", "-A")
    commit(repo)
    git(repo, "branch", "-f", "main")
    (repo / "src/rtl/common").mkdir(parents=True)
    (repo / "src/rtl/common/common_x.sv").write_text("")
    root, changed = changed_files("main", repo / "src")
    hit = select_affected(units(repo / "src"), root, changed, repo / "src", RULES)
    assert hit == {"common": "changed: src/rtl/common/common_x.sv"}


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
    a file the sub-IP owns selects the sub-IP and every IP whose filelists
    reach it."""
    r = tmp_path / "repo"
    r.mkdir()
    shutil.copytree(REPO_ROOT / "src", r / "src")
    ips = discover(r / "src")
    subs = [ip for ip in ips if not ip.top_level]
    if not subs:
        pytest.skip("the repository has no sub-IP")
    for sub in subs:
        changed = sub.rtl_f  # every IP has one, whatever source types it lists
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


def test_regress_select_runs_only_the_global_levels_for_a_tools_change(repo, tmp_path):
    # The end-to-end selection CI uses: L0+L1 requested, a tools-only change
    # runs L0 of every IP and nothing of L1; a design change adds that IP's L1.
    ctx = Context(REPO_ROOT, repo / "src", tmp_path / "work")
    full = {(e.level, e.ip) for e, _ in regress.select(ctx, [0, 1, 2]).runs}
    assert any(lv > 0 for lv, _ in full)  # the fixture has tests beyond level 0
    (repo / "tools/x.py").write_text("changed")
    sel = regress.select(ctx, [0, 1, 2], affected_base="main")
    assert sel.runs and {e.level for e, _ in sel.runs} == {0}
    assert {ip.id for ip in sel.ips} == {ip.id for ip in units(repo / "src")}
    (repo / "src/dv/counter/tests/test_counter.py").write_text("# x\n")
    sel = regress.select(ctx, [0, 1, 2], affected_base="main")
    got = {(e.level, e.ip) for e, _ in sel.runs}
    assert {k for k in full if k[1] == "counter"} <= got
    assert all(lv == 0 for lv, ip in got if ip != "counter")


def test_tools_module_dv_imports_runs_every_level_for_its_importers(repo):
    # A DV test imports an import-root helper (as cocotb tests import the
    # functional-coverage helper): a change to it is global, but the IP that
    # imports it runs every level; the old selection capped it at level 0.
    (repo / "tools/pkg").mkdir()
    (repo / "tools/pkg/__init__.py").write_text("")
    (repo / "tools/pkg/helper.py").write_text("from pkg import inner\n")
    (repo / "tools/pkg/inner.py").write_text("X = 1\n")
    (repo / "tools/pkg/tests").mkdir()
    (repo / "tools/pkg/tests/test_inner.py").write_text("from pkg import inner\n")
    (repo / "src/dv/counter/tests/test_counter.py").write_text("from pkg import helper\n")
    commit_all(repo)
    (repo / "tools/pkg/inner.py").write_text("X = 2\n")  # reached through helper
    got = affected_levels(repo)
    assert got["counter"] == ("imports: tools/pkg/inner.py", None)
    assert got["counter_inc"] == ("global change: tools/pkg/inner.py", (0,))
    git(repo, "checkout", "-q", "--", ".")
    (repo / "tools/pkg/tests/test_inner.py").write_text("# x\n")  # tools tests: no importer
    assert all(lv == (0,) for _, lv in affected_levels(repo).values())


def test_deleted_tools_module_still_selects_its_importer(repo):
    (repo / "tools/pkg").mkdir()
    (repo / "tools/pkg/__init__.py").write_text("")
    (repo / "tools/pkg/helper.py").write_text("")
    (repo / "src/dv/counter/tests/test_counter.py").write_text("import pkg.helper\n")
    commit_all(repo)
    (repo / "tools/pkg/helper.py").unlink()
    assert affected_levels(repo)["counter"] == ("imports: tools/pkg/helper.py", None)


def test_other_ips_arch_model_import_selects_the_importer(repo):
    # One IP's DV imports another IP's architecture model by module name (on
    # sys.path by path): no filelist shows it, so the old selection picked
    # only the model's owner.
    (repo / "src/arch/counter").mkdir(parents=True)
    (repo / "src/arch/counter/counter_arch.py").write_text("import counter_util\n")
    (repo / "src/arch/counter/counter_util.py").write_text("Y = 1\n")
    (repo / "src/dv/counter_inc/tests/test_inc.py").write_text("import counter_arch\n")
    commit_all(repo)
    (repo / "src/arch/counter/counter_util.py").write_text("Y = 2\n")
    got = affected(repo)
    assert got["counter"] == "changed: src/arch/counter/counter_util.py"
    assert got["counter_inc"] == "imports: src/arch/counter/counter_util.py"


def test_other_ips_testbench_module_import_selects_the_importer(repo):
    # One IP's DV reuses a Python testbench module of another IP's DV (not a
    # reference model) by bare name, putting that folder on sys.path by path:
    # a change confined to that module selects both IPs at every level.
    (repo / "src/dv/counter/tb").mkdir(parents=True)
    (repo / "src/dv/counter/tb/__init__.py").write_text("")
    (repo / "src/dv/counter/tb/counter_env.py").write_text("import counter_helper\n")
    (repo / "src/dv/counter/tb/counter_helper.py").write_text("Y = 1\n")
    (repo / "src/dv/counter_inc/tb").mkdir(parents=True)
    (repo / "src/dv/counter_inc/tb/inc_env.py").write_text("import counter_env as base\n")
    commit_all(repo)
    rel = "src/dv/counter/tb/counter_env.py"
    (repo / rel).write_text("import counter_helper\nX = 2\n")
    got = affected_levels(repo)
    assert got == {"counter": (f"changed: {rel}", None), "counter_inc": (f"imports: {rel}", None)}
    git(repo, "checkout", "-q", "--", ".")
    helper = "src/dv/counter/tb/counter_helper.py"  # reached through counter_env
    (repo / helper).write_text("Y = 2\n")
    assert affected(repo)["counter_inc"] == f"imports: {helper}"
    git(repo, "checkout", "-q", "--", ".")
    (repo / rel).unlink()  # a deleted module still selects its importer
    assert affected(repo)["counter_inc"] == f"imports: {rel}"


def test_testbench_package_init_and_tests_are_not_bare_modules(repo):
    # A package __init__ and a test file are never imported by bare name, so
    # changing them selects only their owner, even if a name matches.
    (repo / "src/dv/counter/tb").mkdir(parents=True)
    (repo / "src/dv/counter/tb/__init__.py").write_text("")
    (repo / "src/dv/counter_inc/tb").mkdir(parents=True)
    (repo / "src/dv/counter_inc/tb/inc_env.py").write_text("import __init__\nimport test_counter\n")
    commit_all(repo)
    for rel in ("src/dv/counter/tb/__init__.py", "src/dv/counter/tests/test_counter.py"):
        (repo / rel).write_text("# x\n")
        assert affected(repo) == {"counter": f"changed: {rel}"}
        git(repo, "checkout", "-q", "--", ".")


def test_repo_cross_ip_dv_imports_select_the_importer(tmp_path):
    """On the repository's own src/ (IPs discovered, never named): for every
    module of one IP's DV or arch folder that another IP's DV or arch Python
    imports by bare name (found here by a plain scan of top-level import
    names), a change confined to that module selects the importer."""
    r = tmp_path / "repo"
    r.mkdir()
    shutil.copytree(REPO_ROOT / "src", r / "src", ignore=shutil.ignore_patterns("__pycache__"))
    ips = units(r / "src")
    files = {ip.id: sorted(p for d in (ip.dv_dir, ip.dir("arch")) if d.is_dir()
                           for p in d.rglob("*.py") if "__pycache__" not in p.parts)
             for ip in ips}
    pairs = []
    for importer in ips:
        names = {n.split(".")[0] for f in files[importer.id] for n in imported_names(f)}
        for owner in ips:
            if owner.id != importer.id:
                pairs += [(importer.id, owner.id, f) for f in files[owner.id]
                          if f.stem in names and not f.name.startswith("test_")
                          and f.stem not in ("__init__", "conftest")]
    if not pairs:
        pytest.skip("no IP imports another IP's DV or arch Python")
    for importer, owner, f in pairs:
        rel = f.relative_to(r).as_posix()
        got = select_affected(ips, r, [rel], r / "src", RULES)
        assert owner in got and importer in got, (rel, importer)


def test_ci_unit_reasons_never_name_levels(repo):
    (repo / "tools/x.py").write_text("changed")
    assert set(affected(repo).values()) == {"global change: tools/x.py"}

