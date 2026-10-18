import json
import shutil
from argparse import Namespace
from datetime import datetime, timezone

import pytest

from build.ip import Context
from metrics import cli, record
from metrics.config import MetricsError

from conftest import FIXTURES, FakeGh
from test_metrics_github import ROUTES

NOW = datetime(2026, 3, 31, 12, 0, tzinfo=timezone.utc)
VERSIONS = {"python": "3.12.1", "git": "2.43.0", "gh": None, "verilator": "5.052",
            "cocotb": "2.1.0", "pytest": "9.1.1", "pyyaml": "6.0.3"}
# Strings planted in the inputs that must never reach a record.
PLANTED = ["someone", "/home/", "example.com", "@", "secret title", "/opt/", "sha256",
           "u_core", "logs/", "run.log", "assert failed"]


def test_tests_section_whitelists(cfg):
    t = record.tests_section(FIXTURES / "workdir" / "regress" / "summary.json", cfg)
    assert t["status"] == "ok" and t["passed"] is False and t["levels"] == [0, 2]
    assert t["totals"]["level2"] == {"tests": 2, "passed": 1, "failed": 1, "runtime_s": 78.25,
                                     "budget_s": 7200, "over_budget": False}
    assert t["ips"]["blk"]["level2"] == {"tests": 2, "passed": 1, "failed": 1, "runtime_s": 78.25}
    assert t["builds"] == {"total": 2, "failed": 0}
    assert [r["seed"] for r in t["slowest"][:2]] == [7, 8]
    assert set(t["runs"][0]) == {"ip", "level", "name", "seed", "status", "runtime_s"}
    assert "/home/" not in json.dumps(t)


def test_tests_section_missing_and_invalid(tmp_path, cfg):
    assert record.tests_section(tmp_path / "none.json", cfg) == {
        "status": "unavailable", "reason": "missing"}
    bad = tmp_path / "s.json"
    for text in ("not json", '{"tests": []}',
                 '{"tests": [{"ip": "/abs", "level": 0, "name": "t", "seed": 1, "status": "pass",'
                 ' "runtime_s": 1}], "totals": {}, "levels": [0], "passed": true, "wall_s": 1}'):
        bad.write_text(text)
        assert record.tests_section(bad, cfg) == {"status": "unavailable", "reason": "invalid"}


def test_coverage_section(tmp_path):
    c = record.coverage_section(FIXTURES / "workdir" / "regress" / "summary.json")
    assert c["status"] == "ok" and c["passed"] is False and set(c["ips"]) == {"blk", "blk/sub"}
    assert c["ips"]["blk"] == {
        "line": {"covered": 8, "total": 10, "pct": 80.0},
        "toggle": {"covered": 5, "total": 10, "pct": 50.0},
        "functional": {"covered": 3, "total": 4, "pct": 75.0},
        "floors": {"line": 90.0}, "floors_passed": False}
    assert c["ips"]["blk/sub"]["functional"]["pct"] is None
    assert c["ips"]["blk/sub"]["floors_passed"] is True
    assert "/home/" not in json.dumps(c) and "below" not in json.dumps(c)
    assert record.coverage_section(tmp_path / "none.json") == {"status": "unavailable", "reason": "missing"}
    off = tmp_path / "off.json"
    off.write_text('{"coverage": null}')
    assert record.coverage_section(off) == {"status": "unavailable", "reason": "disabled"}
    for text in ("[]", '{"coverage": {"ips": {"/abs": {}}, "passed": true}}',
                 '{"coverage": {"ips": {"a": {"line": {}}}, "passed": true}}'):
        off.write_text(text)
        assert record.coverage_section(off) == {"status": "unavailable", "reason": "invalid"}


def test_flow_sections_whitelist(cfg):
    wd = FIXTURES / "workdir"
    f = record.flow_section(wd, ["blk", "other"], "fpga", cfg.fpga_fields)
    blk = f["ips"]["blk"]
    assert set(f["ips"]) == {"blk"}
    assert blk["resources"]["logic_elements"] == {"used": 120, "total": 1000}
    assert blk["timing"]["clocks"]["clk_50"]["fmax_mhz"] == 210.5
    assert blk["timing"]["critical_path"] == {"slack_ns": 15.2, "data_delay_ns": 4.1}
    assert blk["flow"]["steps"]["map"] == {"status": "pass", "runtime_s": 12.0, "warnings": 3}
    assert "project_dir" not in blk and "ip" not in blk
    a = record.flow_section(wd, ["blk"], "asic", cfg.asic_fields)["ips"]["blk"]
    assert a["max_fanout"] == {"value": 12} and a["logic_depth"] == {"value": 9}
    assert a["timing"]["clocks"] == {
        "aux_clock": {"setup_ws_ns": -0.5, "wns_ns": -0.5, "tns_ns": -2.0},
        "core_clock": {"setup_ws_ns": 1.25, "wns_ns": 0.0, "tns_ns": 0.0}}
    assert not {"toolchain", "source", "work_home", "contact"} & set(a)
    assert record.flow_section(wd, ["none"], "asic", cfg.asic_fields) == {
        "status": "unavailable", "reason": "missing"}


def test_arch_section_whitelists(tmp_path):
    a = record.arch_section(FIXTURES / "workdir", ["blk", "blk_sub", "other"])
    assert a == {"status": "ok", "ips": {"blk": {"status": "fail", "scenarios": {
        "full": {"status": "pass", "targets": {"lat": 1, "rate": 1.0}},
        "half": {"status": "fail", "targets": {"rate": 0.5}}}}}}
    assert record.arch_section(tmp_path, ["blk"]) == {"status": "unavailable", "reason": "missing"}


@pytest.mark.parametrize("doc", [
    "not json",
    "[]",
    {"status": "maybe", "scenarios": []},
    {"status": "pass"},
    {"status": "pass", "scenarios": [{"name": "/abs", "status": "pass"}]},
    {"status": "pass", "scenarios": [{"name": "a", "status": "skipped"}]},
    {"status": "pass", "scenarios": [{"name": "a", "status": "pass",
                                      "targets": [{"metric": "x y", "value": 1}]}]},
    {"status": "pass", "scenarios": [{"name": "a", "status": "pass",
                                      "targets": [{"metric": "x", "value": "1"}]}]},
    {"status": "pass", "scenarios": [{"name": "a", "status": "pass",
                                      "targets": [{"metric": "x", "value": True}]}]},
    {"status": "pass", "scenarios": [{"name": "a", "status": "pass",
                                      "targets": [{"metric": "x", "value": 10 ** 400}]}]},  # OverflowError
    {"status": "pass", "scenarios": [{"name": "a", "status": "pass",
                                      "targets": [{"metric": "x", "value": float("inf")}]}]},
])
def test_arch_section_invalid_file(tmp_path, doc):
    path = tmp_path / "blk/arch/perf.json"
    path.parent.mkdir(parents=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    assert record.arch_section(tmp_path, ["blk"]) == {"status": "ok", "ips": {"blk": {"status": "invalid"}}}


def test_huge_numbers_are_invalid_not_a_crash(tmp_path, cfg):
    """A number too large for a float (OverflowError) marks the input invalid."""
    s = tmp_path / "summary.json"
    s.write_text(json.dumps({"tests": [{"ip": "a", "level": 0, "name": "t", "seed": 1,
                                        "status": "pass", "runtime_s": 10 ** 400}],
                             "totals": {}, "levels": [0], "passed": True, "wall_s": 1}))
    assert record.tests_section(s, cfg) == {"status": "unavailable", "reason": "invalid"}
    s.write_text(json.dumps({"coverage": {"passed": True, "ips": {"a": {
        m: {"covered": 1, "total": 1, "pct": 10 ** 400} for m in record.COVERAGE_METRICS}
        | {"floors": {}, "failures": []}}}}))
    assert record.coverage_section(s) == {"status": "unavailable", "reason": "invalid"}


def test_pick_drops_unsafe_strings():
    doc = {"a": "/abs/path", "b": "ok value", "c": {"x": 1, "y": {"deep": 2}}, "d": [1, 2]}
    assert record.pick(doc, ["a", "b", "c.*", "d", "missing.x"]) == {"b": "ok value", "c": {"x": 1}}


@pytest.mark.parametrize("value, reason", [
    ("someone@example.com", "email"),
    ("/home/someone/x", "absolute path"),
    ("path: /opt/tool", "absolute path"),
    ("~/work", "absolute path"),
    ("C:\\Users\\x", "windows path"),
    ("ghp_abcdefghijklmnop", "token"),
    ("10.1.2.3", "ip address"),
    ("https://example.invalid/x", "url"),
    ("build.example.com", "domain name"),
    ("host.corp", "domain name"),
    ("fe80::1", "ipv6 address"),
    ("2001:db8:0:0:0:0:0:1", "ipv6 address"),
    ("AKIAABCDEFGHIJKLMNOP", "access key"),
    ("sk-abcdefghijklmnop1234", "access key"),
])
def test_forbidden_patterns(value, reason):
    problems = record.forbidden({"k": [value]})
    assert any(p.endswith(reason) for p in problems), problems
    assert record.forbidden({value: 1})  # keys are scanned too


def test_forbidden_host_terms():
    assert record.forbidden({"k": "built on buildbox-7"}, ["buildbox-7"])
    assert record.forbidden({"k": "by alice"}, ["alice"])
    assert record.forbidden({"k": "runner01"}, ["runner"])  # joined to digits
    assert record.forbidden({"k": "malice"}, ["alice"])  # long terms match inside tokens
    assert not record.forbidden({"k": "bobcat"}, ["bob"])  # short terms: whole tokens only
    assert not record.forbidden({"ip": "top_sub", "v": "23.1std.1 Build 10/25/2023", "n": 1.5,
                                 "t": "2026-10-14T18:26:47Z", "x": ".md", "py": "3.13.16"})


def _ctx(repo, workdir):
    return Context(repo, repo / "src", workdir)


def test_collect_end_to_end_is_publishable(repo, tmp_path, cfg, monkeypatch):
    workdir = tmp_path / "workdir"
    shutil.copytree(FIXTURES / "workdir", workdir)
    monkeypatch.setattr(record, "tool_versions", lambda: dict(VERSIONS))
    monkeypatch.setattr(record, "host_terms", lambda: ["someone"])
    (repo / "cfg").mkdir()
    shutil.copy(FIXTURES.parents[3] / "cfg" / "metrics.yaml", repo / "cfg" / "metrics.yaml")
    out = tmp_path / "out" / "2026-03-31.json"
    args = Namespace(out=str(out), no_github=False, step=["check=pass", "regress=fail"],
                     now="2026-03-31T12:00:00Z", started="2026-03-31T10:30:00Z", repo="own/repo")
    assert cli.collect(_ctx(repo, workdir), args, gh_factory=lambda r: FakeGh(dict(ROUTES), r)) == 0
    text = out.read_text()
    for s in PLANTED:
        assert s not in text, s
    rec = json.loads(text)
    assert record.forbidden(rec, ["someone"]) == []
    assert rec["schema_version"] == 1
    assert rec["meta"]["date"] == "2026-03-31"
    assert len(rec["meta"]["commit"]) == 40
    assert rec["meta"]["nightly"] == {"steps": {"check": "pass", "regress": "fail"}, "result": "fail",
                                     "duration_s": 5400.0}
    assert rec["code"]["commits"] == 2 and rec["code"]["contributors"] == 1
    assert rec["code"]["repo_size_kb"] == 1234
    assert set(rec["code"]["ips"]) == {"blk", "blk_sub"}
    assert rec["tests"]["status"] == rec["fpga"]["status"] == rec["asic"]["status"] == "ok"
    assert rec["arch"]["ips"]["blk"]["scenarios"]["full"] == {"status": "pass",
                                                              "targets": {"lat": 1, "rate": 1.0}}
    assert rec["coverage"]["ips"]["blk"]["line"]["pct"] == 80.0
    assert rec["github"]["issues"]["status"] == "ok"
    assert rec["distributions"]["test_runtime_s"] == [0.75, 1.5, 38.0, 40.25]
    assert rec["distributions"]["nightly_duration_s"] is None
    assert len(rec["distributions"]["file_bytes"]) == rec["code"]["total"]["files"]
    # Deterministic: same inputs, same bytes.
    out2 = tmp_path / "again.json"
    args.out = str(out2)
    cli.collect(_ctx(repo, workdir), args, gh_factory=lambda r: FakeGh(dict(ROUTES), r))
    assert out2.read_text() == text


def test_collect_without_github_or_producers(repo, tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(record, "tool_versions", lambda: dict(VERSIONS))
    (repo / "cfg").mkdir()
    shutil.copy(FIXTURES.parents[3] / "cfg" / "metrics.yaml", repo / "cfg" / "metrics.yaml")
    out = tmp_path / "r.json"
    args = Namespace(out=str(out), no_github=True, step=[], now="2026-03-31T12:00:00Z",
                     started=None, repo=None)
    assert cli.collect(_ctx(repo, tmp_path / "empty"), args) == 0
    rec = json.loads(out.read_text())
    for key in ("tests", "coverage", "fpga", "asic", "arch"):
        assert rec[key] == {"status": "unavailable", "reason": "missing"}
    assert rec["github"]["ci"] == {"status": "unavailable", "reason": "disabled"}
    assert rec["meta"]["nightly"]["result"] is None
    assert rec["meta"]["nightly"]["duration_s"] is None
    assert rec["meta"]["started_at"] is rec["meta"]["run_id"] is rec["meta"]["run_attempt"] is None


def test_each_run_writes_its_own_record(repo, tmp_path, monkeypatch):
    # Two runs on one day: two files named by each run's start, never one
    # overwriting the other; meta carries the start, run id and attempt.
    monkeypatch.setattr(record, "tool_versions", lambda: dict(VERSIONS))
    (repo / "cfg").mkdir()
    shutil.copy(FIXTURES.parents[3] / "cfg" / "metrics.yaml", repo / "cfg" / "metrics.yaml")
    out = tmp_path / "data"
    for started, now, run in (("2026-03-31T03:17:05Z", "2026-03-31T05:00:00Z", 11),
                              ("2026-03-31T14:02:09Z", "2026-03-31T15:30:00Z", 12)):
        args = Namespace(out=None, out_dir=str(out), no_github=True, step=[], now=now, started=started,
                         repo=None, run_id=run, run_attempt=1)
        assert cli.collect(_ctx(repo, tmp_path / "empty"), args) == 0
    names = sorted(f.name for f in out.iterdir())
    assert names == ["2026-03-31T031705Z.json", "2026-03-31T140209Z.json"]
    first, second = (json.loads((out / n).read_text())["meta"] for n in names)
    assert (first["started_at"], first["run_id"], first["run_attempt"]) == ("2026-03-31T03:17:05Z", 11, 1)
    assert (second["generated_at"], second["run_id"], second["date"]) == ("2026-03-31T15:30:00Z", 12, "2026-03-31")
    # without --started the name comes from the collection time; default dir under the workdir
    args = Namespace(out=None, out_dir=None, no_github=True, step=[], now="2026-04-01T00:00:07Z",
                     started=None, repo=None, run_id=None, run_attempt=None)
    assert cli.collect(_ctx(repo, tmp_path / "wd"), args) == 0
    assert (tmp_path / "wd" / "metrics" / "2026-04-01T000007Z.json").is_file()


def test_collect_run_arguments():
    import argparse
    p = argparse.ArgumentParser()
    cli.add_args(p)
    args = p.parse_args(["collect", "--out-dir", "d", "--run-id", "38623254395", "--run-attempt", "2"])
    assert (args.out_dir, args.run_id, args.run_attempt) == ("d", 38623254395, 2)
    for bad in (["--run-id", "0"], ["--run-id", "x1"], ["--run-attempt", "-1"], ["--out", "a", "--out-dir", "b"]):
        with pytest.raises(SystemExit):
            p.parse_args(["collect", *bad])


def test_write_refuses_leaks(tmp_path):
    out = tmp_path / "x.json"
    with pytest.raises(MetricsError, match="not publishable"):
        record.write_checked({"a": "someone@example.com"}, out, [])
    assert not out.exists()


def test_bad_step_argument():
    with pytest.raises(MetricsError, match="--step"):
        cli._steps(["check=maybe"])


def test_clock_study_never_substitutes_for_default_asic(tmp_path, cfg):
    study = tmp_path / "blk" / "asic-study" / "10ns" / "metrics.json"
    study.parent.mkdir(parents=True)
    study.write_text(json.dumps({"clock_period_ns": 10, "timing": {"setup_ws_ns": 2}}))
    assert record.flow_section(tmp_path, ["blk"], "asic", cfg.asic_fields) == {
        "status": "unavailable", "reason": "missing"}
    default = tmp_path / "blk" / "asic" / "metrics.json"
    default.parent.mkdir()
    default.write_text(json.dumps({"clock_period_ns": 8, "timing": {"setup_ws_ns": -1}}))
    row = record.flow_section(tmp_path, ["blk"], "asic", cfg.asic_fields)["ips"]["blk"]
    assert row["clock_period_ns"] == 8
    assert row["timing"]["setup_ws_ns"] == -1
