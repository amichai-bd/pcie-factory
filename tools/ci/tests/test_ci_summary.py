import json

from ci.summary import render

SUMMARY = {
    "levels": [0, 1],
    "ips": {"a": "changed: src/rtl/a/a.sv"},
    "builds": [{"ip": "a", "build": "a_top", "status": "pass"}],
    "tests": [
        {"ip": "a", "level": 0, "name": "smoke", "seed": 1, "status": "pass", "log": "a/smoke/1/sim.log"},
        {"ip": "a", "level": 1, "name": "full", "seed": 7, "status": "fail", "log": "a/full/7/sim.log"},
    ],
    "totals": {
        "level0": {"tests": 1, "passed": 1, "failed": 0, "runtime_s": 0.9, "budget_s": 120, "over_budget": False},
        "level1": {"tests": 1, "passed": 0, "failed": 1, "runtime_s": 700.0, "budget_s": 600, "over_budget": True},
    },
    "wall_s": 3.4,
    "passed": False,
}


def test_render_failures_and_budget(tmp_path):
    p = tmp_path / "summary.json"
    p.write_text(json.dumps(SUMMARY))
    md = render(p)
    assert md.startswith("### Regression L0, L1: FAIL\n")
    assert "| `a` | changed: src/rtl/a/a.sv |" in md
    assert "| level1 | 0/1 | 700.0 (over budget) | 600 |" in md
    assert "- `a:full` seed=7: fail (`a/full/7/sim.log`)" in md
    assert "smoke" not in md
    assert render(p) == md  # deterministic


def test_render_empty_selection(tmp_path):
    p = tmp_path / "summary.json"
    p.write_text(json.dumps({"levels": [0], "ips": {}, "builds": [], "tests": [],
                             "totals": {}, "wall_s": 0.0, "passed": True}))
    md = render(p)
    assert "Regression L0: PASS" in md and "No IPs selected." in md


def test_render_missing_and_bad(tmp_path):
    assert "regress did not finish" in render(tmp_path / "none.json")
    (tmp_path / "bad.json").write_text("{")
    assert "Unreadable summary" in render(tmp_path / "bad.json")


def test_cli_summary_reads_workdir(tmp_path, capsys):
    from cli.main import main

    (tmp_path / "regress").mkdir()
    (tmp_path / "regress/summary.json").write_text(json.dumps(SUMMARY))
    assert main(["ci", "--workdir", str(tmp_path), "summary"]) == 0
    assert "### Regression L0, L1: FAIL" in capsys.readouterr().out
