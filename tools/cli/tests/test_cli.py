import json
import subprocess
import sys

import pytest

from build.ip import REPO_ROOT
from cli.main import COMMANDS, main, make_parser


def test_dispatch_table():
    assert set(COMMANDS) == {"sim", "regress", "clean", "labels", "wiki", "check", "fpga", "asic",
                             "ci", "metrics", "arch", "specs", "pr"}


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
            "--level", "0", "--ip", "counter_inc", "--seed", "4", "--dry-run"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "[select] counter_inc: --ip" in out
    assert "L0 counter_inc:exhaustive seed=4" in out
    assert not (tmp_path / "regress").exists()


def test_user_errors_exit_2(fixture_src, tmp_path, capsys):
    (fixture_src / "dv/counter/regress/level0.yaml").write_text("tests: [1]\n")
    assert main(["regress", "--src-root", str(fixture_src), "--level", "0", "--dry-run"]) == 2
    assert "level0.yaml: tests[0]: a test entry must be a mapping" in capsys.readouterr().err
    assert main(["sim", "--src-root", str(fixture_src), "--ip", "nope", "--test", "t"]) == 2
    assert main(["regress", "--src-root", str(tmp_path / "none"), "--level", "0"]) == 2
    assert main(["regress", "--src-root", str(fixture_src), "--level", "0", "--jobs", "0"]) == 2


def test_clean(fixture_src, tmp_path):
    work = tmp_path / "work"
    (work / "counter/counts/1").mkdir(parents=True)
    (work / "counter/build").mkdir(parents=True)
    (work / "counter_inc/exhaustive/1").mkdir(parents=True)
    (work / "other").mkdir()
    base = ["clean", "--src-root", str(fixture_src), "--workdir", str(work)]
    assert main(base + ["--ip", "counter"]) == 0
    assert not (work / "counter").exists()
    assert (work / "counter_inc/exhaustive/1").is_dir()  # a sibling IP's output is kept
    assert main(base + ["--ip", "counter_inc"]) == 0
    assert not (work / "counter_inc").exists() and (work / "other").exists()
    assert main(base + ["--ip", "counter"]) == 0  # nothing left: still fine
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
    (fixture_src / "dv/counter/regress/level0.yaml").write_text("tests: 1\n")
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

    (fixture_src / "dv/counter/regress/level2.yaml").write_text(
        "tests:\n  - {name: r, module: tests.test_counter, top: counter,"
        f" seeds: {seeds}, timeout_s: 5}}\n")
    used = []
    monkeypatch.setattr(sim, "find_verilator", lambda: Path("/fake/verilator"))
    monkeypatch.setattr(sim, "build", lambda *a: sim.Result("pass", 0.0, Path("b.log")))

    def fake_run_test(spec, workdir, verilator, test, module, testcase, seed, timeout_s, built=None):
        used.append(seed)
        return sim.Result("pass", 0.0, Path("s.log"))

    monkeypatch.setattr(sim, "run_test", fake_run_test)
    base = ["sim", "--src-root", str(fixture_src), "--workdir", str(tmp_path), "--ip", "counter", "--test", "r"]
    assert main(base) == 0
    if expected is None:
        ip = find_ip(discover(fixture_src), "counter")
        (entry,) = [e for e in load_ip(ip) if e.name == "r"]
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


@pytest.mark.parametrize("argv", [
    ["wiki", "build"], ["wiki", "serve"], ["labels", "sync"], ["labels", "check", "7"], ["pr", "check", "7"],
    ["ci", "summary"], ["metrics", "guard", "--branch", "b", "--base", "m"],
    ["check"], ["clean"],
])
def test_shared_options_before_or_after_the_action(argv):
    parse = make_parser().parse_args
    shared = ["--workdir", "W", "--src-root", "S"]
    before = parse(argv[:1] + shared + argv[1:])
    after = parse(argv + shared)
    assert (before.workdir, before.src_root) == (after.workdir, after.src_root) == ("W", "S")
    neither = parse(argv)
    assert (neither.workdir, neither.src_root) == (None, None)
    if len(argv) > 1 and not argv[1].startswith("-"):  # an action: the later one wins
        both = parse(argv[:1] + ["--workdir", "A"] + argv[1:] + ["--workdir", "B"])
        assert both.workdir == "B" and both.src_root is None


def test_wiki_workdir_after_action(tmp_path, capsys):
    assert main(["wiki", "build", "--sha", "abc123", "--workdir", str(tmp_path)]) == 0
    assert f"out={tmp_path / 'wiki-site'}" in capsys.readouterr().out
