import json
import subprocess
import sys

import pytest

from build.ip import REPO_ROOT
from cli.main import COMMANDS, main

STUBS = {"check": 7, "asic": 14}


@pytest.mark.parametrize("name, issue", sorted(STUBS.items()))
def test_stubs_exit_2(name, issue, capsys):
    assert main([name, "anything", "--x"]) == 2
    assert f"not implemented yet (see issue #{issue})" in capsys.readouterr().err


def test_dispatch_table():
    assert set(COMMANDS) == {"sim", "regress", "clean", "labels", "wiki", "fpga",
                             "metrics"} | set(STUBS)


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
    (work / "counter/counts/1").mkdir(parents=True)
    (work / "counter/build").mkdir(parents=True)
    (work / "counter/inc/exhaustive/1").mkdir(parents=True)
    (work / "other").mkdir()
    base = ["clean", "--src-root", str(fixture_src), "--workdir", str(work)]
    assert main(base + ["--ip", "counter"]) == 0
    assert sorted(p.name for p in (work / "counter").iterdir()) == ["inc"]
    assert (work / "counter/inc/exhaustive/1").is_dir()  # sub-IP output kept
    assert main(base + ["--ip", "counter/inc"]) == 0
    assert list((work / "counter/inc").iterdir()) == [] and (work / "other").exists()
    assert main(base) == 0
    assert not work.exists()


def test_no_ips_selected(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    work = tmp_path / "work"
    assert main(["regress", "--src-root", str(src), "--workdir", str(work), "--level", "0"]) == 0
    assert "no IPs selected" in capsys.readouterr().out
    summary = json.loads((work / "regress/summary.json").read_text())
    assert (summary["selected_ips"], summary["selected_tests"], summary["passed"]) == (0, 0, True)


def test_stale_summary_removed_on_error_kept_on_dry_run(fixture_src, tmp_path):
    work = tmp_path / "work"
    stale = work / "regress/summary.json"
    stale.parent.mkdir(parents=True)
    stale.write_text('{"passed": true}')
    base = ["regress", "--src-root", str(fixture_src), "--workdir", str(work), "--level", "0"]
    assert main(base + ["--dry-run"]) == 0
    assert stale.is_file()  # --dry-run neither writes nor removes the summary
    (fixture_src / "counter/dv/regress/level0.yaml").write_text("tests: 1\n")
    assert main(base) == 2
    assert not stale.exists()


@pytest.mark.parametrize(
    "seeds, expected",
    [("[9, 5]", 9), ("2", None)],  # list: first in list order; count: first derived
)
def test_sim_default_seed_is_first_regression_seed(fixture_src, tmp_path, monkeypatch, seeds, expected):
    from pathlib import Path

    from build import sim
    from regress import run as regress
    from regress.schema import load_ip
    from build.ip import discover, find_ip

    (fixture_src / "counter/dv/regress/level2.yaml").write_text(
        "tests:\n  - {name: r, module: tests.test_counter, top: counter,"
        f" seeds: {seeds}, timeout_s: 5}}\n")
    used = []
    monkeypatch.setattr(sim, "find_verilator", lambda: Path("/fake/verilator"))
    monkeypatch.setattr(sim, "build", lambda *a: sim.Result("pass", 0.0, Path("b.log")))

    def fake_run_test(spec, workdir, verilator, test, module, testcase, seed, timeout_s):
        used.append(seed)
        return sim.Result("pass", 0.0, Path("s.log"))

    monkeypatch.setattr(sim, "run_test", fake_run_test)
    base = ["sim", "--src-root", str(fixture_src), "--workdir", str(tmp_path), "--ip", "counter", "--test", "r"]
    assert main(base) == 0
    if expected is None:
        ip = find_ip(discover(fixture_src), "counter")
        (entry,) = [e for e in load_ip(ip, {"inc"}) if e.name == "r"]
        expected = regress.default_seed(entry, 0)
    assert used == [expected]
    assert main(base + ["--seed", "3"]) == 0 and used[-1] == 3


def test_wiki_build_and_serve(tmp_path, capsys):
    # Builds the repository's own wiki into <workdir>/wiki-site.
    assert main(["wiki", "--workdir", str(tmp_path), "build", "--strict", "--sha", "abc123"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS:") and f"out={tmp_path / 'wiki-site'}" in out
    assert (tmp_path / "wiki-site" / "index.html").is_file()
    assert "/blob/abc123/" in (tmp_path / "wiki-site" / "index.html").read_text()
    assert main(["wiki", "serve", "--out", str(tmp_path / "none")]) == 2
    assert "run the build first" in capsys.readouterr().err


def test_wiki_requires_action():
    with pytest.raises(SystemExit) as exc:
        main(["wiki"])
    assert exc.value.code == 2
