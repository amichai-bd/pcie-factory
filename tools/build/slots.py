"""Host CPU slots shared by every ``./build sim`` and ``./build regress`` on a
machine (wiki/tools/build/SPEC.md, "CPU slots").

A slot directory holds one lock file per CPU (``slot-<i>.lock``). Each build
or simulation child process holds one slot (an exclusive ``flock``) while it
runs, so all runs that name the same directory together keep at most one
child per CPU busy, whatever ``--jobs`` each asked for. The kernel drops a
lock when its holder exits, so a killed run never leaves a slot taken.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ENV = "BUILD_SLOTS"  # default slot directory when --slots is not given
POLL_S = 0.2


class SlotError(Exception):
    pass


def cpu_count() -> int:
    """CPUs this process may run on (its affinity mask), at least 1."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


class Slots:
    def __init__(self, directory: Path, count: int | None = None):
        self.directory = Path(directory)
        self.count = cpu_count() if count is None else count
        if self.count < 1:
            raise SlotError(f"slot count must be >= 1, got {self.count}")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SlotError(f"cannot create slot directory {self.directory}: "
                            f"{exc.strerror}") from None
        if not os.access(self.directory, os.W_OK):
            raise SlotError(f"slot directory {self.directory} is not writable")

    def path(self, index: int) -> Path:
        return self.directory / f"slot-{index}.lock"

    def try_acquire(self) -> tuple[int, int] | None:
        """Take a free slot without waiting: (slot index, open fd) or None."""
        for i in range(self.count):
            fd = os.open(self.path(i), os.O_RDWR | os.O_CREAT, 0o666)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            return i, fd
        return None

    @contextmanager
    def hold(self, stop: threading.Event | None = None) -> Iterator[int]:
        """Wait for a free slot and hold it for the ``with`` body. Raises
        ``KeyboardInterrupt`` when ``stop`` is set while waiting."""
        while True:
            got = self.try_acquire()
            if got is not None:
                break
            if stop is not None and stop.is_set():
                raise KeyboardInterrupt
            time.sleep(POLL_S)
        index, fd = got
        try:
            yield index
        finally:
            os.close(fd)  # closing the descriptor releases the flock


def from_option(value: str | None) -> Slots | None:
    """The slot pool named by ``--slots DIR``, else ``$BUILD_SLOTS``, else None."""
    directory = value or os.environ.get(ENV) or None
    return Slots(Path(directory)) if directory else None
