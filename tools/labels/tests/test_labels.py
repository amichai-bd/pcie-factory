import json
import subprocess
from types import SimpleNamespace

import pytest

from build.ip import REPO_ROOT
from cli.main import main
from labels import core
from labels.core import Gh, Label, LabelsError

CFG = """
dimensions:
  type:
    prefix: "type:"
    rule: exactly-one
    labels:
      feat: {color: "1D76DB", description: "New capability"}
      bug: {color: "d73a4a", description: "Broken"}
  area:
    prefix: "area:"
    rule: at-least-one
    labels:
      build: {color: "d4c5f9", description: "Build"}
      wiki: {color: "c2e0c6", description: "Wiki"}
  ip:
    prefix: "ip:"
    rule: any
    derived: ip
    color: "006b75"
    description: "Touches IP {id}"
  prio:
    prefix: "prio:"
    rule: at-most-one
    labels:
      p0: {color: "b60205", description: "Urgent"}
      p1: {color: "d93f0b", description: "Normal"}
  state:
    prefix: ""
    rule: any
    labels:
      in-progress: {color: "ededed", description: "Claimed"}
"""


def write(tmp_path, text=CFG):
    p = tmp_path / "labels.yaml"
    p.write_text(text)
    return p


@pytest.fixture
def std(tmp_path):
    return core.load(write(tmp_path), ["counter", "counter/inc"])


class FakeGh(Gh):
    """In-memory GitHub; records every write."""

    def __init__(self, labels, issues=None):
        super().__init__("o/r")
        self.labels = {lab.name: lab for lab in labels}
        self.issues = issues or {}
        self.calls = []

    def list_labels(self):
        return list(self.labels.values())

    def create_label(self, lab):
        self.calls.append(("create", lab.name))
        self.labels[lab.name] = lab

    def update_label(self, current, lab):
        self.calls.append(("update", current))
        del self.labels[current]
        self.labels[lab.name] = lab

    def delete_label(self, name):
        self.calls.append(("delete", name))
        del self.labels[name]

    def issue_labels(self, number):
        return self.issues[number]


# --- config -------------------------------------------------------------------

def test_load(std):
    names = [lab.name for lab in std.labels]
    assert names == ["type:feat", "type:bug", "area:build", "area:wiki", "ip:counter",
                     "ip:counter/inc", "prio:p0", "prio:p1", "in-progress"]
    feat = std.labels[0]
    assert feat.color == "1d76db"  # normalized to lower case
    assert std.labels[5].description == "Touches IP counter/inc"
    assert std.dimension_of("type:x").name == "type"
    assert std.dimension_of("in-progress").name == "state"
    assert std.dimension_of("bug") is None


def test_repo_config_loads():
    std = core.load(REPO_ROOT / "cfg" / "labels.yaml", ["x"])
    dims = {d.name: d for d in std.dimensions}
    assert dims["type"].rule == "exactly-one" and dims["area"].rule == "at-least-one"
    assert {lab.name for lab in dims["state"].labels} >= {"in-progress", "blocked",
                                                          "needs-captain", "nightly-red"}
    assert [lab.name for lab in dims["ip"].labels] == ["ip:x"]


@pytest.mark.parametrize("old, new, msg", [
    ('color: "1D76DB"', 'color: "#1d76db"', "color must be 6 hex digits"),
    ('description: "Broken"', 'description: ""', "description must be a non-empty"),
    ('description: "Broken"', 'description: "' + "x" * 101 + '"', "longer than 100"),
    ("rule: exactly-one", "rule: one", "rule must be one of"),
    ('prefix: "area:"', 'prefix: "type:"', "duplicate prefix"),
    ("derived: ip", "derived: dirs", "derived must be one of"),
    ('description: "Touches IP {id}"', 'description: "IP"', "containing '{id}'"),
    ("in-progress: {", "type:feat: {", "already defined"),
    ("in-progress: {", "prio:p9: {", "uses the prefix of dimensions.prio"),
    ('p1: {color: "d93f0b", description: "Normal"}', "p1: {color: \"d93f0b\"}", "exactly 'color'"),
    ("    rule: any\n    derived", "    rule: any\n    extra: 1\n    derived", "unknown keys"),
])
def test_load_errors(tmp_path, old, new, msg):
    assert old in CFG
    with pytest.raises(LabelsError, match=msg):
        core.load(write(tmp_path, CFG.replace(old, new, 1)), ["counter"])


@pytest.mark.parametrize("text", ["", "[]", "dimensions: {}", "dims: {}", "a: [\n"])
def test_load_bad_top_level(tmp_path, text):
    with pytest.raises(LabelsError):
        core.load(write(tmp_path, text), [])


def test_load_repo_standard_without_src(tmp_path):
    std = core.load_repo_standard(REPO_ROOT / "cfg", tmp_path / "missing")
    assert not [lab for lab in std.labels if lab.name.startswith("ip:")]


def test_load_repo_standard_discovers_ips(fixture_src):
    std = core.load_repo_standard(REPO_ROOT / "cfg", fixture_src)
    assert [lab.name for lab in std.labels if lab.name.startswith("ip:")] == \
        ["ip:counter", "ip:counter/inc"]


# --- repository slug ------------------------------------------------------------

@pytest.mark.parametrize("url, slug", [
    ("https://github.com/o/r", "o/r"),
    ("https://github.com/o/r.git\n", "o/r"),
    ("git@github.com:o/r.git", "o/r"),
    ("ssh://git@github.com/o/r.git", "o/r"),
    ("http://proxy@127.0.0.1:1234/git/o/r", "o/r"),
    ("https://github.com/only", None),
])
def test_slug_from_url(url, slug):
    assert core.slug_from_url(url) == slug


def test_resolve_repo(monkeypatch, tmp_path):
    monkeypatch.delenv("GH_REPO", raising=False)
    assert core.resolve_repo("a/b", tmp_path) == "a/b"
    monkeypatch.setenv("GH_REPO", "c/d")
    assert core.resolve_repo(None, tmp_path) == "c/d"
    with pytest.raises(LabelsError, match="OWNER/NAME"):
        core.resolve_repo("nope", tmp_path)
    monkeypatch.delenv("GH_REPO")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(LabelsError, match="no git remote"):
        core.resolve_repo(None, tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin",
                    "git@github.com:x/y.git"], check=True)
    assert core.resolve_repo(None, tmp_path) == "x/y"


# --- gh api -----------------------------------------------------------------------

def fake_run(responses, calls):
    def run(cmd, capture_output, text):
        calls.append(cmd)
        rc, out, err = responses.pop(0)
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)
    return run


def test_gh_commands(monkeypatch):
    calls = []
    page1 = [{"name": f"l{i}", "color": "AABBCC", "description": None} for i in range(100)]
    page2 = [{"name": "type:feat", "color": "1d76db", "description": "d"}]
    responses = [(0, json.dumps(page1), ""), (0, json.dumps(page2), ""),
                 (0, "{}", ""), (0, "{}", ""), (0, "", ""),
                 (0, json.dumps({"labels": [{"name": "type:bug"}]}), "")]
    monkeypatch.setattr(core.subprocess, "run", fake_run(responses, calls))
    gh = Gh("o/r")
    labels = gh.list_labels()
    assert len(labels) == 101 and labels[0] == Label("l0", "aabbcc", "")
    assert calls[0][-1] == "repos/o/r/labels?per_page=100&page=1"
    assert calls[1][-1] == "repos/o/r/labels?per_page=100&page=2"
    gh.create_label(Label("area:x", "000000", "X"))
    assert calls[2][:4] == ["gh", "api", "-X", "POST"]
    assert calls[2][7:] == ["-f", "name=area:x", "-f", "color=000000", "-f", "description=X"]
    gh.update_label("ip:a/b", Label("ip:a/b", "111111", "Y"))
    assert calls[3][3:7:3] == ["PATCH", "repos/o/r/labels/ip%3Aa%2Fb"]
    assert "new_name=ip:a/b" in calls[3]
    gh.delete_label("good first issue")
    assert calls[4][3:7:3] == ["DELETE", "repos/o/r/labels/good%20first%20issue"]
    assert gh.issue_labels(7) == ["type:bug"]
    assert calls[5][-1] == "repos/o/r/issues/7"


def test_gh_failure(monkeypatch):
    monkeypatch.setattr(core.subprocess, "run",
                        fake_run([(1, "", "gh: Forbidden (HTTP 403)\n")], []))
    with pytest.raises(LabelsError, match=r"POST repos/o/r/labels failed: .*HTTP 403"):
        Gh("o/r").create_label(Label("a", "000000", "x"))


# --- sync -------------------------------------------------------------------------

EXISTING = [
    Label("type:feat", "1d76db", "New capability"),     # unchanged
    Label("Type:Bug", "d73a4a", "Broken"),              # case differs -> rename
    Label("area:build", "ffffff", "Old"),               # color + description
    Label("in-progress", "ededed", ""),                 # description
    Label("bug", "d73a4a", "Something"),                # extra
]


def test_plan(std):
    changes = core.plan(std, EXISTING, prune=False)
    got = [(c.action, c.name) for c in changes]
    assert got == [("update", "area:build"), ("create", "area:wiki"),
                   ("update", "in-progress"), ("create", "ip:counter"),
                   ("create", "ip:counter/inc"), ("create", "prio:p0"),
                   ("create", "prio:p1"), ("update", "Type:Bug"), ("extra", "bug")]
    assert changes[0].detail == "color ffffff -> d4c5f9; description 'Old' -> 'Build'"
    assert changes[7].detail == "name -> 'type:bug'"
    assert ("delete", "bug") in [(c.action, c.name) for c in core.plan(std, EXISTING, True)]


def test_sync_dry_run_changes_nothing(std, capsys):
    gh = FakeGh(EXISTING)
    assert core.sync(std, gh, dry_run=True, prune=True, out=None) == 0
    out = capsys.readouterr().out
    assert gh.calls == []
    assert "labels dry run: o/r (9 labels in the config)" in out
    assert "~ area:build: color ffffff -> d4c5f9" in out
    assert "+ ip:counter/inc: color 006b75, 'Touches IP counter/inc'" in out
    assert "- bug: not in the config" in out
    assert "would apply: 5 create, 3 update, 1 delete; 0 extra kept" in out


@pytest.mark.parametrize("prune", [False, True])
def test_sync_applies_and_converges(std, prune, capsys):
    gh = FakeGh(EXISTING)
    assert core.sync(std, gh, dry_run=False, prune=prune, out=None) == 0
    assert ("delete", "bug") in gh.calls if prune else ("delete", "bug") not in gh.calls
    assert ("bug" in gh.labels) is not prune
    wanted = {lab.name: lab for lab in std.labels}
    assert {k: v for k, v in gh.labels.items() if k != "bug"} == wanted
    assert core.plan(std, gh.list_labels(), prune) == \
        ([] if prune else [core.Change("extra", "bug", None,
                                       "not in the config (kept; --prune deletes)")])
    assert "applied:" in capsys.readouterr().out


# --- check ------------------------------------------------------------------------

@pytest.mark.parametrize("names, errors", [
    (["type:feat", "area:build"], []),
    (["type:feat", "area:build", "area:wiki", "ip:counter/inc", "prio:p0", "in-progress"], []),
    (["area:build"], ["needs exactly one type:* label, has 0"]),
    (["type:feat", "type:bug", "area:build"],
     ["needs exactly one type:* label, has 2 (type:bug, type:feat)"]),
    (["type:feat"], ["needs at least one area:* label"]),
    (["type:feat", "area:build", "prio:p0", "prio:p1"],
     ["allows at most one prio:* label, has prio:p0, prio:p1"]),
    (["type:nope", "area:build", "ip:gone"],
     ["label 'ip:gone' is not a known ip value", "label 'type:nope' is not a known type value",
      "needs exactly one type:* label, has 0"]),
])
def test_check(std, names, errors):
    assert core.check(std, names)[0] == errors


def test_check_warns_on_foreign_labels(std):
    assert core.check(std, ["type:feat", "area:wiki", "bug"]) == \
        ([], ["label 'bug' is not part of the standard"])


# --- CLI ----------------------------------------------------------------------------

@pytest.fixture
def cli_gh(monkeypatch):
    gh = FakeGh([Label("bug", "d73a4a", "x")],
                {5: ["type:infra", "area:build", "in-progress"], 6: ["in-progress"]})
    monkeypatch.setattr(core, "Gh", lambda repo: gh)
    return gh


def test_cli_sync_and_check(cli_gh, tmp_path, capsys):
    base = ["labels", "--src-root", str(tmp_path / "nosrc")]
    assert main(base + ["sync", "--repo", "o/r", "--dry-run"]) == 0
    assert "? bug: not in the config (kept; --prune deletes)" in capsys.readouterr().out
    assert cli_gh.calls == []
    assert main(base + ["check", "5", "--repo", "o/r"]) == 0
    assert "#5: labels OK" in capsys.readouterr().out
    assert main(base + ["check", "6", "--repo", "o/r"]) == 1
    out = capsys.readouterr().out
    assert "error: #6: needs exactly one type:* label, has 0" in out
    assert "#6: labels FAIL" in out


def test_cli_errors_exit_2(monkeypatch, capsys):
    assert main(["labels", "sync", "--repo", "bad", "--dry-run"]) == 2
    assert "repository must be OWNER/NAME" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        main(["labels"])
    assert exc.value.code == 2


def test_gh_non_json_output(monkeypatch):
    monkeypatch.setattr(core.subprocess, "run", fake_run([(0, "<html>oops", "")], []))
    with pytest.raises(LabelsError, match="output is not JSON"):
        Gh("o/r").issue_labels(1)


def test_prune_refuses_derived_labels_without_discovery(tmp_path):
    std = core.load(write(tmp_path), [])  # no IPs discovered
    existing = [Label("ip:counter", "006b75", "Touches IP counter"), Label("bug", "d73a4a", "x")]
    with pytest.raises(LabelsError, match="refusing to prune 'ip:counter': no ip was discovered"):
        core.plan(std, existing, prune=True)
    # Without --prune it is only an extra; with IPs discovered, pruning a stale one is allowed.
    assert ("extra", "ip:counter") in [(c.action, c.name) for c in core.plan(std, existing, False)]
    std = core.load(write(tmp_path), ["other"])
    assert ("delete", "ip:counter") in [(c.action, c.name) for c in core.plan(std, existing, True)]


def test_cli_prune_guard_writes_nothing(monkeypatch, tmp_path, capsys):
    gh = FakeGh([Label("ip:gone", "006b75", "x")])
    monkeypatch.setattr(core, "Gh", lambda repo: gh)
    argv = ["labels", "--src-root", str(tmp_path / "missing"), "sync", "--repo", "o/r", "--prune"]
    assert main(argv) == 2
    assert "refusing to prune 'ip:gone'" in capsys.readouterr().err
    assert gh.calls == []
