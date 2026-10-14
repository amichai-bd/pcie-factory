import subprocess
import sys

import pytest

from build.ip import REPO_ROOT
from cli.main import COMMANDS, main

STUBS = {"check": 7, "labels": 10, "wiki": 12, "fpga": 13, "asic": 14}


@pytest.mark.parametrize("name, issue", sorted(STUBS.items()))
def test_stubs_exit_2(name, issue, capsys):
    assert main([name, "anything", "--x"]) == 2
    assert f"not implemented yet (see issue #{issue})" in capsys.readouterr().err


def test_dispatch_table():
    assert set(COMMANDS) == {"sim", "regress", "clean"} | set(STUBS)


def test_launcher_runs():
    proc = subprocess.run([sys.executable, str(REPO_ROOT / "build"), "--help"],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and "regress" in proc.stdout


def test_regress_requires_level(fixture_src):
    with pytest.raises(SystemExit) as exc:
        main(["regress", "--src-root", str(fixture_src)])
    assert exc.value.code == 2


def test_regress_dry_run(fixture_src, tmp_path, capsys):
    argv = ["regress", "--src-root", str(fixture_src), "--workdir", str(tmp_path),
            "--level", "0", "--ip", "counter/inc", "--seed", "4", "--dry-run"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "[select] counter/inc: --ip" in out
    assert "L0 counter/inc:exhaustive seed=4" in out
    assert not (tmp_path / "regress").exists()


def test_user_errors_exit_2(fixture_src, tmp_path, capsys):
    (fixture_src / "counter/dv/regress/level0.yaml").write_text("tests: [1]\n")
    assert main(["regress", "--src-root", str(fixture_src), "--level", "0", "--dry-run"]) == 2
    assert "level0.yaml: tests[0]: a test entry must be a mapping" in capsys.readouterr().err
    assert main(["sim", "--src-root", str(fixture_src), "--ip", "nope", "--test", "t"]) == 2
    assert main(["regress", "--src-root", str(tmp_path / "none"), "--level", "0"]) == 2
    assert main(["regress", "--src-root", str(fixture_src), "--level", "0", "--jobs", "0"]) == 2


def test_clean(fixture_src, tmp_path):
    work = tmp_path / "work"
    (work / "counter/inc/x").mkdir(parents=True)
    (work / "other").mkdir()
    base = ["clean", "--src-root", str(fixture_src), "--workdir", str(work)]
    assert main(base + ["--ip", "counter/inc"]) == 0
    assert not (work / "counter/inc").exists() and (work / "other").exists()
    assert main(base) == 0
    assert not work.exists()
