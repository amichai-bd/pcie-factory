import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from build import sim, slots


def test_cpu_count_is_positive():
    assert slots.cpu_count() >= 1


def test_default_count_is_the_cpu_count(tmp_path):
    assert slots.Slots(tmp_path / "s").count == slots.cpu_count()
    assert (tmp_path / "s").is_dir()


def test_slots_are_exclusive_and_released(tmp_path):
    pool = slots.Slots(tmp_path, count=2)
    a = pool.try_acquire()
    b = pool.try_acquire()
    assert a is not None and b is not None and {a[0], b[0]} == {0, 1}
    assert pool.try_acquire() is None  # both taken
    import os
    os.close(a[1])
    c = pool.try_acquire()
    assert c is not None and c[0] == a[0]
    os.close(b[1])
    os.close(c[1])


def test_hold_waits_for_a_slot(tmp_path):
    pool = slots.Slots(tmp_path, count=1)
    order = []
    with pool.hold() as index:
        assert index == 0

        def other():
            with pool.hold():
                order.append("other")

        t = threading.Thread(target=other)
        t.start()
        time.sleep(slots.POLL_S * 2)
        order.append("first done")
    t.join(10)
    assert order == ["first done", "other"]


def test_a_slot_held_by_another_process_blocks(tmp_path):
    """The pool is shared across processes (flock), not only threads."""
    pool = slots.Slots(tmp_path, count=1)
    code = ("import fcntl, os, sys, time\n"
            f"fd = os.open({str(pool.path(0))!r}, os.O_RDWR | os.O_CREAT)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "sys.stdin.readline()\n")
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        assert pool.try_acquire() is None
    finally:
        proc.stdin.close()
        proc.wait(10)
    got = pool.try_acquire()  # the kernel released it when the holder exited
    assert got is not None


def test_stop_while_waiting_raises(tmp_path):
    pool = slots.Slots(tmp_path, count=1)
    stop = threading.Event()
    with pool.hold():
        stop.set()
        with pytest.raises(KeyboardInterrupt):
            with pool.hold(stop):
                pass


def test_from_option_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv(slots.ENV, raising=False)
    assert slots.from_option(None) is None
    assert slots.from_option(str(tmp_path / "a")).directory == tmp_path / "a"
    monkeypatch.setenv(slots.ENV, str(tmp_path / "b"))
    assert slots.from_option(None).directory == tmp_path / "b"
    assert slots.from_option(str(tmp_path / "a")).directory == tmp_path / "a"  # option wins


def test_bad_directory_and_count(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    with pytest.raises(slots.SlotError, match="cannot create slot directory"):
        slots.Slots(blocker / "sub")
    with pytest.raises(slots.SlotError, match=">= 1"):
        slots.Slots(tmp_path, count=0)


def test_run_child_waits_for_a_slot_and_stops_cleanly(tmp_path, monkeypatch):
    """A child does not start while every slot is taken; Ctrl-C (kill_all)
    while it waits raises without starting it."""
    started = []
    monkeypatch.setattr(sim, "_run_child_now", lambda *a: started.append(a) or (0, 0.0, 0.0))
    pool = slots.Slots(tmp_path / "s", count=1)
    sim.set_slots(pool)
    errors = []
    try:
        with pool.hold():
            def work():
                try:
                    sim._run_child("test", {}, tmp_path / "x/sim.log", timeout_s=None)
                except KeyboardInterrupt as exc:
                    errors.append(exc)

            t = threading.Thread(target=work)
            t.start()
            time.sleep(slots.POLL_S * 2)
            assert started == []
            sim._STOPPING.set()
            t.join(10)
        assert not t.is_alive() and len(errors) == 1 and started == []
        sim._STOPPING.clear()
        assert sim._run_child("test", {}, tmp_path / "y/sim.log", None) == (0, 0.0, 0.0)
        assert len(started) == 1
    finally:
        sim._STOPPING.clear()
        sim.set_slots(None)


def test_cli_rejects_an_unusable_slot_directory(tmp_path, capsys):
    from cli.main import main
    blocker = tmp_path / "file"
    blocker.write_text("")
    src = Path(__file__).resolve().parent / "fixtures" / "src"
    if not src.is_dir():
        pytest.skip("no fixture src tree")
    rc = main(["regress", "--level", "0", "--src-root", str(src),
               "--workdir", str(tmp_path / "w"), "--slots", str(blocker / "sub")])
    sim.set_slots(None)
    assert rc == 2
    assert "cannot create slot directory" in capsys.readouterr().err
