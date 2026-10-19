"""Synthesis selection on a real fixture git tree and child failure propagation."""

import json
import subprocess

import pytest

from build.ip import Context, Ip, REPO_ROOT
from ci.config import load_rules
from ci import vendor

RULES = load_rules(REPO_ROOT / "cfg")


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], check=True, capture_output=True)


@pytest.fixture
def ctx(tmp_path, fixture_src):
    repo = tmp_path / "repo"
    repo.mkdir()
    fixture_src.rename(repo / "src")
    for kind in vendor.KINDS:
        (repo / "src" / kind / "counter").mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    return Context(repo, repo / "src", repo / "workdir")


def ids(ctx, kind="fpga", base="main"):
    return [ip.id for ip, _ in vendor.select(ctx, RULES, kind, base)]


@pytest.mark.parametrize("kind", vendor.KINDS)
def test_all_targets_without_affected(ctx, kind):
    assert ids(ctx, kind, None) == ["counter"]
    assert ids(ctx, kind) == []


@pytest.mark.parametrize("path", ["README.md", "wiki/intro.md", "src/dv/counter/test_new.py",
                                   "src/arch/counter/model.py"])
def test_docs_and_dv_do_not_select_vendor_targets(ctx, path):
    p = ctx.repo_root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("new")
    assert ids(ctx) == ids(ctx, "asic") == []


@pytest.mark.parametrize("path", ["src/rtl/counter_inc/inc.sv", "src/rtl/counter_inc/counter_inc.rtl.f",
                                   "src/rtl/counter/include/nested/new.svh", "tools/asic/flow.py",
                                   "cfg/host.yaml", "build", ".github/workflows/ci.yml",
                                   ".github/workflows/fpga.yml", ".github/workflows/asic.yml",
                                   ".github/workflows/nightly.yml"])
def test_synthesis_closure_and_global_changes(ctx, path):
    p = ctx.repo_root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write("\n// changed\n")
    assert ids(ctx) == ids(ctx, "asic") == ["counter"]


@pytest.mark.parametrize("kind", vendor.KINDS)
def test_wrapper_constraint_or_config_selects_only_its_flow(ctx, kind):
    (ctx.src_root / kind / "counter/input.sdc").write_text("changed")
    assert ids(ctx, kind) == ["counter"]
    other = next(k for k in vendor.KINDS if k != kind)
    assert ids(ctx, other) == []


def test_backend_file_selects_only_the_flow_that_selects_it(ctx):
    (ctx.repo_root / "cfg").mkdir()
    (ctx.repo_root / "cfg/backends.yaml").write_text("fpga:\n  suffix: alt\n")
    git(ctx.repo_root, "add", ".")
    git(ctx.repo_root, "commit", "-qm", "backends")
    # inc.sv is in counter's closure; inc_alt.sv is its fpga backend file.
    (ctx.src_root / "rtl/counter_inc/inc_alt.sv").write_text("module inc; endmodule\n")
    assert ids(ctx) == ["counter"]
    assert ids(ctx, "asic") == []


def test_deleted_source_broken_closure_is_selected(ctx):
    (ctx.src_root / "rtl/counter_inc/inc.sv").unlink()
    assert ids(ctx) == ids(ctx, "asic") == ["counter"]


def test_removed_constraint_does_not_hide_target(ctx):
    p = ctx.src_root / "asic/counter/target.sdc"
    p.write_text("old")
    git(ctx.repo_root, "add", ".")
    git(ctx.repo_root, "commit", "-qm", "constraint")
    p.unlink()
    # Directory is still a target even though the required input is now absent.
    assert ids(ctx, "asic") == ["counter"]


def test_failing_child_fails_vendor_and_records_summary(ctx, monkeypatch):
    calls = []
    def child(command, cwd):
        calls.append(command)
        return 3
    monkeypatch.setattr(vendor, "run_child", child)
    assert vendor.run(ctx, RULES, "asic", None) == 1
    assert calls[0][-1] == "--synth-only"
    doc = json.loads((ctx.workdir / "ci/vendor-asic.json").read_text())
    assert doc["stage"] == "synthesis" and doc["targets"][0]["status"] == "fail"


def test_fpga_vendor_check_is_synthesis_only(ctx, monkeypatch):
    # PR CI runs Quartus analysis and synthesis only; fit and timing are nightly.
    monkeypatch.setattr(vendor, "select", lambda *args: [(Ip("a", ctx.src_root), "all")])
    calls = []
    monkeypatch.setattr(vendor, "run_child", lambda command, cwd: calls.append(command) or 0)
    assert vendor.run(ctx, RULES, "fpga", None) == 0
    assert calls[0][2:5] == ["fpga", "--ip", "a"] and calls[0][-1] == "--synth-only"
    doc = json.loads((ctx.workdir / "ci/vendor-fpga.json").read_text())
    assert doc["stage"] == "synthesis" and doc["targets"][0]["status"] == "pass"


def test_empty_selection_succeeds_without_toolchain(ctx, monkeypatch):
    monkeypatch.setattr(vendor, "select", lambda *args: [])
    monkeypatch.setattr(vendor, "run_child", lambda *a, **k: pytest.fail("ran vendor"))
    assert vendor.run(ctx, RULES, "fpga", "main") == 0
    assert json.loads((ctx.workdir / "ci/vendor-fpga.json").read_text())["targets"] == []


def test_dry_run_selects_without_tools_or_output(ctx, monkeypatch):
    monkeypatch.setattr(vendor, "run_child", lambda *a, **k: pytest.fail("ran vendor"))
    assert vendor.run(ctx, RULES, "asic", None, dry_run=True) == 0
    assert not ctx.workdir.exists()


def test_failure_does_not_suppress_later_targets(ctx, monkeypatch):
    monkeypatch.setattr(vendor, "select", lambda *args: [
        (Ip("a", ctx.src_root), "all"), (Ip("b", ctx.src_root), "all")])
    calls = []
    def child(command, cwd):
        calls.append(command)
        return 1 if len(calls) == 1 else 0
    monkeypatch.setattr(vendor, "run_child", child)
    assert vendor.run(ctx, RULES, "fpga", None) == 1
    assert len(calls) == 2
    results = json.loads((ctx.workdir / "ci/vendor-fpga.json").read_text())["targets"]
    assert [(r["ip"], r["status"]) for r in results] == [("a", "fail"), ("b", "pass")]


@pytest.mark.parametrize("signame", ["SIGINT", "SIGTERM"])
def test_cancellation_reaches_actual_child_and_restores_handlers(tmp_path, signame):
    import os
    import signal
    import sys
    import threading
    import time

    ready, stopped = tmp_path / "ready", tmp_path / "stopped"
    script = """import pathlib, signal, sys
ready, stopped = map(pathlib.Path, sys.argv[1:])
def stop(signum, frame):
    stopped.write_text(str(signum))
    sys.exit(130)
signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
ready.write_text('ready')
while True:
    signal.pause()
"""
    sig = getattr(signal, signame)
    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    def cancel():
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        os.kill(os.getpid(), sig)
    thread = threading.Thread(target=cancel)
    thread.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            vendor.run_child([sys.executable, "-c", script, str(ready), str(stopped)], tmp_path)
    finally:
        thread.join(timeout=6)
    assert stopped.read_text() == str(sig)
    assert all(signal.getsignal(s) == h for s, h in before.items())


def test_cancellation_stops_before_later_target(ctx, monkeypatch):
    monkeypatch.setattr(vendor, "select", lambda *args: [
        (Ip("a", ctx.src_root), "all"), (Ip("b", ctx.src_root), "all")])
    calls = []
    def child(command, cwd):
        calls.append(command)
        raise KeyboardInterrupt
    monkeypatch.setattr(vendor, "run_child", child)
    with pytest.raises(KeyboardInterrupt):
        vendor.run(ctx, RULES, "asic", None)
    assert len(calls) == 1


@pytest.mark.parametrize("when", ["before", "after"])
def test_wrapper_cancellation_during_child_creation(tmp_path, monkeypatch, when):
    import os
    import signal
    import sys

    popen = vendor.subprocess.Popen
    children = []
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    def interrupted_popen(*args, **kwargs):
        if when == "before":
            os.kill(os.getpid(), signal.SIGTERM)
        child = popen(*args, **kwargs)
        children.append(child)
        if when == "after":
            os.kill(os.getpid(), signal.SIGTERM)
        return child
    monkeypatch.setattr(vendor.subprocess, "Popen", interrupted_popen)
    try:
        with pytest.raises(KeyboardInterrupt):
            vendor.run_child([sys.executable, "-c", "import time; time.sleep(60)"], tmp_path)
        assert len(children) == 1 and children[0].returncode == -signal.SIGTERM
        assert all(signal.getsignal(sig) == handler for sig, handler in before.items())
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
