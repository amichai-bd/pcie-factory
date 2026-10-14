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
    assert not {"toolchain", "source", "work_home", "contact"} & set(a)
    assert record.flow_section(wd, ["none"], "asic", cfg.asic_fields) == {
        "status": "unavailable", "reason": "missing"}


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
])
def test_forbidden_patterns(value, reason):
    problems = record.forbidden({"k": [value]})
    assert any(p.endswith(reason) for p in problems), problems
    assert record.forbidden({value: 1})  # keys are scanned too


def test_forbidden_host_terms():
    assert record.forbidden({"k": "built on buildbox-7"}, ["buildbox-7"])
    assert record.forbidden({"k": "by alice"}, ["alice"])
    assert not record.forbidden({"k": "malice"}, ["alice"])  # whole tokens only
    assert not record.forbidden({"ip": "top/sub", "v": "23.1std.1 Build 10/25/2023", "n": 1.5})


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
                     now="2026-03-31T12:00:00Z", repo="own/repo")
    assert cli.collect(_ctx(repo, workdir), args, gh_factory=lambda r: FakeGh(dict(ROUTES), r)) == 0
    text = out.read_text()
    for s in PLANTED:
        assert s not in text, s
    rec = json.loads(text)
    assert record.forbidden(rec, ["someone"]) == []
    assert rec["schema_version"] == 1
    assert rec["meta"]["date"] == "2026-03-31"
    assert len(rec["meta"]["commit"]) == 40
    assert rec["meta"]["nightly"] == {"steps": {"check": "pass", "regress": "fail"}, "result": "fail"}
    assert rec["code"]["commits"] == 2 and rec["code"]["contributors"] == 1
    assert rec["code"]["repo_size_kb"] == 1234
    assert set(rec["code"]["ips"]) == {"blk", "blk/sub"}
    assert rec["tests"]["status"] == rec["fpga"]["status"] == rec["asic"]["status"] == "ok"
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
    args = Namespace(out=str(out), no_github=True, step=[], now="2026-03-31T12:00:00Z", repo=None)
    assert cli.collect(_ctx(repo, tmp_path / "empty"), args) == 0
    rec = json.loads(out.read_text())
    for key in ("tests", "fpga", "asic"):
        assert rec[key] == {"status": "unavailable", "reason": "missing"}
    assert rec["github"]["ci"] == {"status": "unavailable", "reason": "disabled"}
    assert rec["meta"]["nightly"]["result"] is None


def test_write_refuses_leaks(tmp_path):
    out = tmp_path / "x.json"
    with pytest.raises(MetricsError, match="not publishable"):
        record.write_checked({"a": "someone@example.com"}, out, [])
    assert not out.exists()


def test_bad_step_argument():
    with pytest.raises(MetricsError, match="--step"):
        cli._steps(["check=maybe"])
