import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from build import sim
from build.ip import REPO_ROOT
from cli.main import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
needs_verilator = pytest.mark.skipif(sim.find_verilator() is None,
                                     reason="verilator not available")


def check(capsys, *args):
    code = main(["check", *args])
    return code, capsys.readouterr()


def test_empty_src_passes_and_reports_coverage(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    code, out = check(capsys, "--src-root", str(tmp_path / "src"))
    assert code == 0
    lines = out.out.splitlines()
    assert lines[0].startswith("check: 0 IP(s) under ")
    assert [ln.split("]")[0] for ln in lines[1:-1]] == [
        "[layout", "[filelist", "[style", "[wiki", "[flows", "[neutrality", "[citation",
        "[publishability", "[lint"]
    assert "[filelist] PASS: 0 filelist(s), 0 IP(s)" in out.out
    assert "[wiki] PASS: 0 required page(s), 0 wiki folder(s)" in out.out
    assert "[citation] PASS: 0 text file(s)" in out.out
    assert "[lint] PASS: 0 IP(s), 0 shared block(s), 0 Verilator run(s)" in out.out
    assert lines[-1] == "check: PASS"


@pytest.mark.parametrize("only", ["layout", "filelist", "style", "wiki"])
def test_bad_fixture_fails_each_check(only, capsys):
    code, out = check(capsys, "--src-root", str(FIXTURES / "bad/src"), "--only", only)
    assert code == 1
    assert f"[{only}] FAIL (" in out.out
    assert out.out.splitlines()[-1] == f"check: FAIL ({only})"


@needs_verilator
def test_bad_fixture_fails_lint(capsys):
    code, out = check(capsys, "--src-root", str(FIXTURES / "bad/src"), "--only", "lint")
    assert code == 1 and "Warning-UNUSEDSIGNAL" in out.out


@needs_verilator
def test_good_fixture_passes_all(capsys):
    code, out = check(capsys, "--src-root", str(FIXTURES / "good/src"))
    assert code == 0, out.out
    assert "check: 2 IP(s)" in out.out and out.out.splitlines()[-1] == "check: PASS"


@needs_verilator
def test_integration_fixture_passes_all(capsys):
    """Top-level sys integrates top-level a and b (each with arch/, fpga/,
    asic/); a_x is a sub-IP of a."""
    code, out = check(capsys, "--src-root", str(FIXTURES / "integration/src"))
    assert code == 0, out.out
    assert "check: 4 IP(s)" in out.out and out.out.splitlines()[-1] == "check: PASS"


@pytest.mark.parametrize("only", ["layout", "filelist", "style", "wiki", "flows"])
def test_integration_fixture_passes_each_check(only, capsys):
    code, out = check(capsys, "--src-root", str(FIXTURES / "integration/src"), "--only", only)
    assert code == 0, out.out


def test_only_and_ip_selection(capsys):
    code, out = check(capsys, "--src-root", str(FIXTURES / "bad/src"),
                      "--only", "style", "--only", "filelist", "--ip", "broken")
    assert code == 1
    assert "--ip broken" in out.out
    assert "[style] PASS: 0 RTL file(s)" in out.out  # broken has no sources
    assert "[filelist] FAIL" in out.out and "[lint]" not in out.out
    assert out.out.index("[filelist]") < out.out.index("[style]")


def test_unknown_ip_and_bad_only_exit_2(capsys):
    assert main(["check", "--src-root", str(FIXTURES / "good/src"), "--ip", "nope"]) == 2
    assert "unknown IP 'nope'" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["check", "--only", "spelling"])
    assert exc.value.code == 2


def test_bad_waiver_file_exits_2(tmp_path, capsys):
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "good/src", src)
    (src / "lint_waivers.yaml").write_text("waivers: [{file: x}]\n")
    assert main(["check", "--src-root", str(src), "--only", "lint"]) == 2
    assert "lint_waivers.yaml: waivers[0]" in capsys.readouterr().err


def test_launcher_check_on_repo():
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "build"), "check",
                           "--only", "neutrality"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[neutrality] PASS" in proc.stdout


def test_launcher_wiki_mirror_on_repo():
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "build"), "check",
                           "--only", "wiki"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[wiki] PASS" in proc.stdout


def test_launcher_citation_on_repo():
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "build"), "check",
                           "--only", "citation"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[citation] PASS" in proc.stdout


def test_citation_failure_exits_1(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "wiki/src").mkdir(parents=True)
    (tmp_path / "wiki/src/x.md").write_text("Per [cite: Std, 2.0, 4.1].\n")
    code, out = check(capsys, "--src-root", str(tmp_path / "src"), "--only", "citation")
    assert code == 1
    assert "wiki/src/x.md:1: malformed citation" in out.out
    assert out.out.splitlines()[-1] == "check: FAIL (citation)"
