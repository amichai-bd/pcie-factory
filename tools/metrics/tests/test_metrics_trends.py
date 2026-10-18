"""Per-IP trend series and drop alerts (tools/metrics/trends.py, ``./build metrics trends``).

The fixture history (fixtures/trends/) has four nightly records: the oldest
predates coverage, the third is a gap (coverage unavailable, ASIC missing),
`beta` is retired before the latest and `gamma` appears only in it. With the
repository thresholds, the latest record drops `alpha`'s line coverage across
the gap (90 -> 88.5), its mutant score (90 -> 80, from killed/total) and its
worst-clock Fmax (100 -> 97); functional coverage drops by exactly its
threshold (100 -> 99) and the other series are flat.
"""

from __future__ import annotations

import json
import types

import pytest

from metrics import cli, trends

from conftest import FIXTURES, REPO_ROOT

HISTORY = FIXTURES / "trends"
ZERO = {k: 0 for k in trends.KEYS}


def records() -> list[dict]:
    return [json.loads(f.read_text()) for f in sorted(HISTORY.glob("*.json"))]


def line(*values) -> list[dict]:
    """A history of `alpha` line coverage; None is a record without it."""
    return [{"coverage": {"ips": {"alpha": {"line": {"pct": v}}}}} if v is not None else {"coverage": {}}
            for v in values]


def keys(found) -> list[tuple[str, str, int]]:
    return [(d.ip, d.series.key, d.at) for d in found]


# --------------------------------------------------------------- the rule
def test_drop_beyond_threshold_alerts():
    found = trends.alerts(line(90.0, 88.0), {"coverage_line": 1.0})
    assert keys(found) == [("alpha", "coverage_line", 1)]
    assert (found[0].previous, found[0].value, found[0].change) == (90.0, 88.0, -2.0)


def test_flat_series_does_not_alert_even_at_zero_threshold():
    assert trends.drops(line(90.0, 90.0, 90.0), {"coverage_line": 0}) == []


def test_drop_equal_to_threshold_does_not_alert():
    assert trends.drops(line(90.0, 89.0), {"coverage_line": 1.0}) == []
    assert keys(trends.drops(line(90.0, 88.99), {"coverage_line": 1.0})) == [("alpha", "coverage_line", 1)]


def test_rise_does_not_alert():
    assert trends.drops(line(80.0, 95.0), {"coverage_line": 0}) == []


def test_gap_neither_alerts_nor_resets_the_comparison():
    found = trends.drops(line(90.0, None, 88.0), {"coverage_line": 1.0})
    assert keys(found) == [("alpha", "coverage_line", 2)]
    assert found[0].before == 0  # compared across the gap with the last recorded value


def test_gap_at_the_latest_record_is_not_an_alert():
    history = line(90.0, 80.0, None)
    assert keys(trends.drops(history, {"coverage_line": 1.0})) == [("alpha", "coverage_line", 1)]
    assert trends.alerts(history, {"coverage_line": 1.0}) == []  # history only


def test_series_without_threshold_never_alerts():
    assert trends.drops(line(90.0, 10.0), {}) == []


@pytest.mark.parametrize("entry", [
    None, "text", [], {"line": None}, {"line": {"pct": None}}, {"line": {"pct": "88"}},
    {"line": {"pct": True}}, {"line": {"pct": float("nan")}}, {"line": {"pct": float("inf")}},
])
def test_old_or_malformed_values_are_gaps(entry):
    history = line(90.0) + [{"coverage": {"ips": {"alpha": entry}}}] + line(90.0)
    assert trends.drops(history, ZERO) == []


def test_sections_without_ips_are_tolerated():
    history = [{}, {"coverage": {"status": "unavailable"}}, {"fpga": {"ips": "x"}}, {"asic": None}]
    assert trends.ips_of(history) == [] and trends.drops(history, ZERO) == []


def test_mutant_score_and_worst_fmax():
    assert trends.mutant_score({"score_pct": 70}) == 70
    assert trends.mutant_score({"killed": 3, "total": 4}) == 75
    assert trends.mutant_score({"killed": 3, "total": 0}) is None
    fmax = trends.SERIES[trends.KEYS.index("fpga_fmax")]
    rec = {"fpga": {"ips": {"a": {"timing": {"clocks": {"x": {"fmax_mhz": 90}, "y": {"fmax_mhz": 60}}}}}}}
    assert fmax.get(rec, "a") == 60


# ------------------------------------------------------- fixture history
def test_fixture_history_alerts(cfg):
    history = records()
    assert trends.ips_of(history) == ["alpha", "beta", "gamma"]
    assert keys(trends.alerts(history, cfg.drop_alerts)) == [
        ("alpha", "coverage_line", 3), ("alpha", "mutant_score", 3), ("alpha", "fpga_fmax", 3)]
    # with every threshold at zero the exact-threshold functional drop shows too,
    # while the flat series, retired beta and single-point gamma still do not
    assert keys(trends.alerts(history, ZERO)) == [
        ("alpha", "coverage_line", 3), ("alpha", "coverage_functional", 3),
        ("alpha", "mutant_score", 3), ("alpha", "fpga_fmax", 3)]


def test_describe_uses_points_for_percentages(cfg):
    lines = [trends.describe(d) for d in trends.alerts(records(), cfg.drop_alerts)]
    assert lines[0] == "alpha: Line coverage dropped 90% → 88.5% (−1.5 pts; threshold 1 pts)"
    assert lines[2] == "alpha: FPGA Fmax, worst clock dropped 100 MHz → 97 MHz (−3 MHz; threshold 2 MHz)"


# -------------------------------------------------------- the subcommand
def run_trends(tmp_path, capsys, *dirs) -> tuple[int, str, str]:
    ctx = types.SimpleNamespace(cfg_dir=REPO_ROOT / "cfg", repo_root=tmp_path)
    rc = cli.trend_alerts(ctx, types.SimpleNamespace(dir=[str(d) for d in dirs]))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_summary_lists_the_latest_drops(tmp_path, capsys):
    rc, out, _ = run_trends(tmp_path, capsys, HISTORY)
    assert rc == 0
    assert out.startswith("## Trend drop alerts\n")
    assert "Latest nightly 2026-01-04 at `0000000`, over 4 runs" in out
    rows = [r for r in out.splitlines() if r.startswith("| `")]
    assert rows == [
        "| `alpha` | Line coverage | 90% (2026-01-02) | 88.5% | −1.5 pts | 1 pts |",
        "| `alpha` | Mutant score | 90% (2026-01-02) | 80% | −10 pts | 1 pts |",
        "| `alpha` | FPGA Fmax, worst clock | 100 MHz (2026-01-03) | 97 MHz | −3 MHz | 2 MHz |",
    ]


def test_summary_without_drops_and_without_records(tmp_path, capsys):
    flat = tmp_path / "flat"
    flat.mkdir()
    for f in sorted(HISTORY.glob("*.json"))[:2]:
        (flat / f.name).write_text(f.read_text())
    rc, out, _ = run_trends(tmp_path, capsys, flat)
    assert rc == 0 and "No series dropped beyond its threshold." in out and "| `" not in out
    rc, out, _ = run_trends(tmp_path, capsys, tmp_path / "none")
    assert rc == 0 and "No nightly records found." in out


def test_later_dir_wins_and_bad_files_are_skipped(tmp_path, capsys):
    tonight = tmp_path / "tonight"
    tonight.mkdir()
    latest = json.loads((HISTORY / "2026-01-04.json").read_text())
    latest["fpga"]["ips"]["alpha"]["timing"]["clocks"]["clk_a"]["fmax_mhz"] = 100.0
    (tonight / "2026-01-04.json").write_text(json.dumps(latest))
    (tonight / "broken.json").write_text("{")
    (tonight / "2026-01-05.json").write_text(json.dumps({"schema_version": 2}))
    rc, out, err = run_trends(tmp_path, capsys, HISTORY, tonight)
    assert rc == 0 and "FPGA Fmax" not in out and "Line coverage" in out
    assert "broken.json: not a readable JSON record; skipped" in err
    assert "2026-01-05.json: not a schema 1 record; skipped" in err


def run(started: str | None, line_pct: float | None, generated: str | None = None, date: str | None = None) -> str:
    """A minimal record of one run with `alpha` line coverage."""
    meta = {"generated_at": generated or started, "started_at": started}
    if date:
        meta["date"] = date
    cov = {"ips": {"alpha": {"line": {"pct": line_pct}}}} if line_pct is not None else {}
    return json.dumps({"schema_version": 1, "meta": meta, "coverage": cov})


def test_same_day_runs_are_consecutive_records(tmp_path, capsys):
    # Three runs on one day plus an older date-named record: four records in
    # run order, and the drop between the day's last two runs is the alert.
    d = tmp_path / "data"
    d.mkdir()
    (d / "2026-02-01.json").write_text(json.dumps({"schema_version": 1, "meta": {"date": "2026-02-01"},
                                                   "coverage": {"ips": {"alpha": {"line": {"pct": 80.0}}}}}))
    (d / "2026-02-02T031700Z.json").write_text(run("2026-02-02T03:17:00Z", 90.0))
    (d / "2026-02-02T090000Z.json").write_text(run("2026-02-02T09:00:00Z", 91.0))
    (d / "2026-02-02T183000Z.json").write_text(run("2026-02-02T18:30:00Z", 88.0))
    history, notes = cli.load_history([d])
    assert notes == []
    assert [label for label, _ in history] == ["2026-02-01", "2026-02-02 03:17", "2026-02-02 09:00", "2026-02-02 18:30"]
    rc, out, _ = run_trends(tmp_path, capsys, d)
    assert "Latest nightly 2026-02-02 18:30, over 4 runs" in out
    rows = [r for r in out.splitlines() if r.startswith("| `")]
    assert rows == ["| `alpha` | Line coverage | 91% (2026-02-02 09:00) | 88% | −3 pts | 1 pts |"]


def test_run_order_is_the_start_time_not_the_file_name(tmp_path):
    # File names that disagree with the records: the start time orders them,
    # a record without a start falls back to generated_at, then to its date.
    d = tmp_path / "data"
    d.mkdir()
    (d / "b.json").write_text(run("2026-02-02T01:00:00Z", 90.0))
    (d / "a.json").write_text(run(None, 70.0, generated="2026-02-02T05:00:00Z"))
    (d / "c.json").write_text(run(None, 60.0, generated="not-a-time", date="2026-02-01"))
    history, _ = cli.load_history([d])
    assert [r["coverage"]["ips"]["alpha"]["line"]["pct"] for _, r in history] == [60.0, 90.0, 70.0]
    assert keys(trends.alerts([r for _, r in history], {"coverage_line": 1.0})) == [("alpha", "coverage_line", 2)]


def test_one_run_in_two_files_is_one_record(tmp_path):
    # A run restored under its per-run name while its old date file remains,
    # or tonight's file passed again by a later --dir: one record per run.
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(), new.mkdir()
    (old / "2026-02-02.json").write_text(run("2026-02-02T03:17:00Z", 90.0, generated="2026-02-02T05:00:00Z"))
    (old / "2026-02-02T031700Z.json").write_text(run("2026-02-02T03:17:00Z", 90.0, generated="2026-02-02T05:00:00Z"))
    (new / "2026-02-02T031700Z.json").write_text(run("2026-02-02T03:17:00Z", 85.0, generated="2026-02-02T05:00:00Z"))
    history, _ = cli.load_history([old])
    assert len(history) == 1
    history, _ = cli.load_history([old, new])
    assert len(history) == 1 and history[0][1]["coverage"]["ips"]["alpha"]["line"]["pct"] == 85.0


def test_run_helpers_on_odd_records():
    assert trends.run_date({}, "2026-02-03T120000Z") == "2026-02-03"
    assert trends.run_date({"meta": "x"}, "notes") is None
    assert trends.run_label({"meta": {"started_at": "2026-02-03T12:34:56+02:00"}}, "x") == "2026-02-03 10:34"
    assert trends.run_identity({"meta": {"generated_at": 5}}, "s") == "file:s"


def test_default_dir_is_the_data_dir(tmp_path, capsys):
    data = tmp_path / "wiki" / "tracker" / "data"
    data.mkdir(parents=True)
    for f in HISTORY.glob("*.json"):
        (data / f.name).write_text(f.read_text())
    rc, out, _ = run_trends(tmp_path, capsys)
    assert rc == 0 and out.count("| `alpha` |") == 3


def test_metrics_trends_parses():
    import argparse
    p = argparse.ArgumentParser()
    cli.add_args(p)
    args = p.parse_args(["trends", "--dir", "a", "--dir", "b"])
    assert args.action == "trends" and args.dir == ["a", "b"]
