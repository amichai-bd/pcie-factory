"""Verilator + cocotb simulation driver.

The parent process never imports cocotb. Each build and each test runs in a
child process (``python3 -B tools/build/sim.py <phase> <job.json>``) that uses
cocotb's Python runner, so a hung simulation can be killed on timeout and
tests can run in parallel.

Output layout (``<ip>`` is the IP id, e.g. ``a`` or ``a_b``):

    workdir/<ip>/build/<top>[-<hash>]/   Verilator build, build.log (with the
                                         build cache only build.log: the
                                         executable is in the cache entry)
    workdir/<ip>/<test>/<seed>/          results.xml, sim.log, waves,
                                         coverage.dat and funcov.json (coverage)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

import yaml

if __package__ in (None, ""):  # executed as a script by the parent process
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build import backends  # noqa: E402
from build import cache as build_cache  # noqa: E402
from build.slots import Slots  # noqa: E402
from build.filelist import FilelistError, resolve  # noqa: E402
from build.ip import REPO_ROOT, Ip  # noqa: E402

BUILD_TIMEOUT_S = 1800.0  # wall-clock bound on one Verilator build
# Verilator options of every simulation build: assertions are enabled (the
# RTL BKM's assertion macros, immediate and concurrent).
BUILD_ARGS = ["--assert"]
# Verilator options of a coverage build; the regress SPEC owns the choice.
COVERAGE_ARGS = ["--coverage-line", "--coverage-toggle"]
COVERAGE_DAT = "coverage.dat"  # code coverage of one run, in its test directory
FUNCOV_JSON = "funcov.json"  # functional coverage of one run (regress.funcov)
# ccache settings for the generated C++ (Verilator's OBJCACHE): paths under
# the build directory hashed relative to it, generated headers' fresh
# timestamps and precompiled-header defines accepted.
CCACHE_SLOPPINESS = "pch_defines,time_macros,include_file_mtime,include_file_ctime"
# Cold-build bound (wiki/tools/build/SPEC.md, "Build cache"): each build runs
# cocotb's `make -j` (at most COMPILE_JOBS compilers), and a compiler of the
# generated C++ may take up to COMPILE_MEM_BYTES; at most compile_slots()
# builds compile at once, so all compilers together fit the host's CPUs and
# available memory. A CI host ran out of memory without it (#327).
COMPILE_JOBS = 4
COMPILE_MEM_BYTES = 2 * 2**30


class SimError(Exception):
    pass


def fallback_dirs() -> list[Path]:
    """``<prefix>/bin`` for the pinned Verilator in ``cfg/host.yaml``
    (``verilator.prefix``); empty when the file or key is absent."""
    path = REPO_ROOT / "cfg" / "host.yaml"
    try:
        data = yaml.safe_load(path.read_text()) or {}
        prefix = data["verilator"]["prefix"]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return []
    return [Path(str(prefix)) / "bin"] if prefix else []


def find_verilator() -> Path | None:
    """Locate ``verilator``: PATH, then ``$VERILATOR_ROOT/bin``, then the
    pinned install prefix from ``cfg/host.yaml``."""
    found = shutil.which("verilator")
    if found:
        return Path(found).resolve()
    candidates = []
    if os.environ.get("VERILATOR_ROOT"):
        candidates.append(Path(os.environ["VERILATOR_ROOT"]) / "bin")
    candidates += fallback_dirs()
    for d in candidates:
        exe = d / "verilator"
        if exe.is_file() and os.access(exe, os.X_OK):
            return exe
    return None


def not_found_message() -> str:
    places = ["PATH", "$VERILATOR_ROOT/bin"] + [str(d) for d in fallback_dirs()]
    return "verilator not found (looked in " + ", ".join(places) + ")"


@dataclass
class BuildSpec:
    """One Verilator build: an IP's sim filelist elaborated for one top.
    With ``backend`` (a flow kind of cfg/backends.yaml) the sources are that
    flow's selection (build.backends.select) plus its DV-only stand-ins."""

    ip: Ip
    top: str
    params: dict = field(default_factory=dict)
    waves: bool = False
    coverage: bool = False
    backend: str | None = None

    @property
    def key(self) -> str:
        """The workdir name of this build (not the cache key, which also
        hashes the content: :func:`content_key`)."""
        extra = {"params": self.params, "waves": self.waves}
        if self.coverage:
            extra["coverage"] = True
        if self.backend:
            extra["backend"] = self.backend
        if not self.params and not self.waves and not self.coverage and not self.backend:
            return self.top
        digest = hashlib.sha1(json.dumps(extra, sort_keys=True).encode()).hexdigest()
        return f"{self.top}-{digest[:8]}"

    def build_dir(self, workdir: Path) -> Path:
        return self.ip.work_dir(workdir) / "build" / self.key


@dataclass
class Result:
    status: str  # pass | fail | error | timeout
    runtime_s: float
    log: Path
    detail: str = ""
    cpu_s: float = 0.0  # user + system CPU time of the child process tree
    build_dir: Path | None = None  # a build: where its executable is
    cached: bool = False  # a build reused from the build cache
    root: str | None = None  # a build: the source tree root it was compiled from


def test_dir(workdir: Path, ip: Ip, test: str, seed: int) -> Path:
    return ip.work_dir(workdir) / test / str(seed)


# Child processes currently running, so an interrupt can kill them all.
_ACTIVE: set[subprocess.Popen] = set()
_LOCK = threading.Lock()
_STOPPING = threading.Event()
# Host CPU slots each child holds while it runs (None: no slot pool);
# set by `--slots`/$BUILD_SLOTS (wiki/tools/build/SPEC.md, "CPU slots").
_SLOTS: Slots | None = None
# The shared build cache (None: off); set by `--no-cache`/$BUILD_CACHE
# (wiki/tools/build/SPEC.md, "Build cache").
_CACHE: build_cache.Cache | None = None


_COMPILE_SEM: threading.BoundedSemaphore | None = None


def mem_available() -> int | None:
    """MemAvailable from /proc/meminfo, in bytes; None when unknown."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def compile_slots(cpus: int | None = None, mem: int | None = None) -> int:
    """How many builds may compile at once: the compilers the host's CPUs
    and available memory allow, divided by each build's ``make -j``."""
    from build.slots import cpu_count
    cpus = cpu_count() if cpus is None else cpus
    procs = cpus if mem is None else min(cpus, mem // COMPILE_MEM_BYTES)
    return max(1, procs // min(COMPILE_JOBS, cpus))


def _compile_sem() -> threading.BoundedSemaphore:
    global _COMPILE_SEM
    with _LOCK:
        if _COMPILE_SEM is None:
            _COMPILE_SEM = threading.BoundedSemaphore(compile_slots(mem=mem_available()))
        return _COMPILE_SEM


def set_slots(slots: Slots | None) -> None:
    global _SLOTS
    _SLOTS = slots


def set_cache(cache: build_cache.Cache | None) -> None:
    global _CACHE
    _CACHE = cache


def get_cache() -> build_cache.Cache | None:
    return _CACHE


def kill_all() -> None:
    """Kill every running child process group and refuse to start new ones
    (used on Ctrl-C; children run in their own sessions and would otherwise
    survive the parent)."""
    _STOPPING.set()
    with _LOCK:
        procs = list(_ACTIVE)
    for proc in procs:
        if proc.returncode is None:
            _killpg(proc)


def _killpg(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


_POLL_S = 0.05


def _wait_rusage(proc: subprocess.Popen, timeout_s: float | None) -> tuple[int, float]:
    """Wait for ``proc`` like ``Popen.wait`` and return (exit code, CPU
    seconds). ``wait4`` reports the child's user and system time including
    every descendant it waited for (the simulator under the cocotb runner).
    Raises ``subprocess.TimeoutExpired`` after ``timeout_s``."""
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        pid, status, ru = os.wait4(proc.pid, os.WNOHANG)
        if pid == proc.pid:
            code = os.waitstatus_to_exitcode(status)
            proc.returncode = code
            return code, ru.ru_utime + ru.ru_stime
        if deadline is not None and time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(proc.args, timeout_s)
        time.sleep(_POLL_S)


def _run_child(phase: str, job: dict, log: Path,
               timeout_s: float | None) -> tuple[int | None, float, float]:
    """Run one child phase; return (exit code or None on timeout, wall
    runtime, CPU time of the child process tree; 0.0 when it was killed)."""
    if _STOPPING.is_set():
        raise KeyboardInterrupt
    if _SLOTS is None:
        return _run_child_now(phase, job, log, timeout_s)
    # Wait for a host CPU slot first; runtime and timeout start once it is held.
    with _SLOTS.hold(_STOPPING):
        return _run_child_now(phase, job, log, timeout_s)


def _run_child_now(phase: str, job: dict, log: Path,
                   timeout_s: float | None) -> tuple[int | None, float, float]:
    log.parent.mkdir(parents=True, exist_ok=True)
    job_file = log.with_suffix(".job.json")
    job_file.write_text(json.dumps(job, indent=2, sort_keys=True))
    cmd = [sys.executable, "-B", str(Path(__file__).resolve()), phase, str(job_file)]
    start = time.monotonic()
    code: int | None = None
    cpu = 0.0
    with open(log, "w") as fh:
        # Start and register the child under the lock that kill_all() takes for
        # its snapshot, checking the stop flag there: a child is either seen by
        # kill_all() or never started.
        with _LOCK:
            if _STOPPING.is_set():
                raise KeyboardInterrupt
            proc = subprocess.Popen(
                cmd, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True
            )
            _ACTIVE.add(proc)
        try:
            code, cpu = _wait_rusage(proc, timeout_s)
        except subprocess.TimeoutExpired:
            code = None
        except BaseException:  # Ctrl-C in this thread: take the sim down too
            code = None
            raise
        finally:
            if code is None:  # timed out or interrupted: kill the whole group
                _killpg(proc)
                proc.wait()
            with _LOCK:
                _ACTIVE.discard(proc)
    if _STOPPING.is_set():
        raise KeyboardInterrupt
    return code, time.monotonic() - start, cpu


def _job(spec: BuildSpec, workdir: Path, verilator: Path, build_dir: Path | None = None) -> dict:
    return {
        "verilator": str(verilator),
        "top": spec.top,
        "params": spec.params,
        "waves": spec.waves,
        "coverage": spec.coverage,
        "build_dir": str(build_dir or spec.build_dir(workdir)),
        "dv_dir": str(spec.ip.dv_dir),
    }


_TOOL_IDENT: dict[str, dict] = {}


def tool_ident(verilator: Path) -> dict:
    """What a build depends on besides its sources: the Verilator version
    and install (``$VERILATOR_ROOT``), the C++ compiler, the cocotb version
    and install (its library path is linked into the executable) and the
    Python that runs it."""
    key = str(verilator)
    if key not in _TOOL_IDENT:
        try:
            out = subprocess.run([str(verilator), "--version"], capture_output=True,
                                 text=True, timeout=60).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        import importlib.metadata
        import importlib.util
        try:
            cocotb_version = importlib.metadata.version("cocotb")
        except importlib.metadata.PackageNotFoundError:
            cocotb_version = None
        spec = importlib.util.find_spec("cocotb")
        cxx = os.environ.get("CXX") or "g++"
        try:
            cxx_out = subprocess.run([cxx, "--version"], capture_output=True, text=True,
                                     timeout=60).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            cxx_out = ""
        _TOOL_IDENT[key] = {
            "verilator": out, "verilator_path": key,
            "verilator_root": os.environ.get("VERILATOR_ROOT"),
            "cxx": cxx, "cxx_version": cxx_out,
            "cocotb": cocotb_version, "cocotb_path": spec.origin if spec else None,
            "python": sys.version, "python_path": sys.executable,
        }
    return _TOOL_IDENT[key]


def driver_files() -> list[Path]:
    """The build driver: every module of ``tools/build/`` (this file, the
    filelist resolver, the backends, the cache), which decides how a build is
    made beyond its settings."""
    return sorted(Path(__file__).resolve().parent.glob("*.py"))


def tree_root(ip) -> Path:
    """The root that cache keys and coverage paths are relative to: the
    parent of the source root (the worktree)."""
    return Path(ip.src_root).resolve().parent


def content_key(spec: BuildSpec, sources: list[Path], incdirs: list[Path],
                defines: dict, verilator: Path) -> str:
    """The build cache key: sha256 of the build settings, the tool versions,
    the build driver (``tools/build/*.py``: how the build is invoked),
    every source in order and every file under every include directory, by
    content and by path relative to the worktree (so another worktree with
    the same content shares the entry)."""
    h = build_cache.Hasher(tree_root(spec.ip))
    for f in driver_files():
        h.file("driver", f)
    h.value("settings", {
        "schema": build_cache.SCHEMA, "top": spec.top, "params": spec.params,
        "waves": spec.waves, "coverage": spec.coverage,
        "args": BUILD_ARGS + (COVERAGE_ARGS if spec.coverage else []),
        "defines": {k: ("" if v is None else v) for k, v in defines.items()},
    })
    h.value("tools", tool_ident(verilator))
    for src in sources:
        h.file("source", src)
    for inc in incdirs:
        h.value("incdir", h.name(inc))
        for f in build_cache.tree_files(inc):
            h.file("include", f)
    return h.hexdigest()


def sources_for(spec: BuildSpec, sources: list[Path], cfg_dir: Path) -> list[Path]:
    """The build's sources: the sim filelist's, or with ``spec.backend`` that
    flow's backend selection followed by its DV-only stand-ins."""
    if not spec.backend:
        return list(sources)
    known = backends.load(cfg_dir)
    if spec.backend not in known:
        raise backends.BackendError(f"backend {spec.backend!r} is not a flow kind of "
                                    f"cfg/{backends.CONFIG} (known: "
                                    f"{', '.join(sorted(known)) or 'none'})")
    chosen = known[spec.backend]
    return (backends.select(sources, chosen, known)
            + backends.dv_models(chosen, spec.ip.src_root))


def build(spec: BuildSpec, workdir: Path, verilator: Path,
          cfg_dir: Path = REPO_ROOT / "cfg") -> Result:
    """Elaborate and compile ``spec``; log at ``<build_dir>/build.log``.
    A filelist that does not resolve, or a bad backend, is a build ``error``
    (not an exception), with the message as detail and in the log."""
    log = spec.build_dir(workdir) / "build.log"
    try:
        flat = resolve(spec.ip.sim_f)
        sources = sources_for(spec, flat.sources, cfg_dir)
    except (FilelistError, backends.BackendError) as exc:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"error: {exc}\n")
        return Result("error", 0.0, log, str(exc))
    cache = _CACHE
    root = str(tree_root(spec.ip))
    key = None
    if cache is not None:
        key = content_key(spec, sources, flat.incdirs, flat.defines, verilator)
        hit = cache.lookup(spec.top, key)
        if hit is not None:
            entry = cache.entry(spec.top, key)
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f"build cache hit: {build_cache.show(entry)}\n"
                           f"key {key}\nbuilt from {hit.get('root')}\n")
            return Result("pass", 0.0, log, "cached", build_dir=entry, cached=True,
                          root=hit.get("root"))
    job = _job(spec, workdir, verilator)
    job.update(
        sources=[str(p) for p in sources],
        incdirs=[str(p) for p in flat.incdirs],
        defines=flat.defines,
    )
    if cache is not None:
        job["ccache"] = True
    with _compile_sem():
        code, runtime, cpu = _run_child("build", job, log, timeout_s=BUILD_TIMEOUT_S)
    if code is None:
        return Result("error", runtime, log, f"build timed out after {BUILD_TIMEOUT_S}s")
    if code != 0:
        return Result("error", runtime, log, f"build failed, see {log}", cpu)
    bdir = spec.build_dir(workdir)
    if cache is None or key is None:
        return Result("pass", runtime, log, cpu_s=cpu, build_dir=bdir, root=root)
    exe = bdir / spec.top
    try:
        entry = cache.store(spec.top, key, [exe, log],
                            {"root": root, "ip": spec.ip.id, "top": spec.top,
                             "params": spec.params, "coverage": spec.coverage,
                             "waves": spec.waves, "backend": spec.backend})
    except OSError as exc:  # a full or broken cache never fails the build
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as fh:
            fh.write(f"warning: not stored in the build cache: {exc}\n")
        return Result("pass", runtime, log, cpu_s=cpu, build_dir=bdir, root=root)
    # The executable now lives in the cache entry; drop the object files
    # (hundreds of MB per build) and keep the log.
    for p in bdir.iterdir():
        if p.name != log.name:
            shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
    with open(log, "a") as fh:
        fh.write(f"stored in the build cache: {build_cache.show(entry)}\nkey {key}\n")
    return Result("pass", runtime, log, cpu_s=cpu, build_dir=entry, root=root)


def run_test(
    spec: BuildSpec,
    workdir: Path,
    verilator: Path,
    test: str,
    module: str,
    testcase: str | None,
    seed: int,
    timeout_s: float | None,
    built: Result | None = None,
) -> Result:
    """Run one already-built test with one seed; ``built`` is its build's
    result (where the executable is, and the tree it was compiled from)."""
    tdir = test_dir(workdir, spec.ip, test, seed)
    if tdir.exists():
        shutil.rmtree(tdir)
    job = _job(spec, workdir, verilator, built.build_dir if built else None)
    job.update(
        module=module,
        testcase=testcase,
        seed=seed,
        test_dir=str(tdir),
        results=str(tdir / "results.xml"),
    )
    log = tdir / "sim.log"
    code, runtime, cpu = _run_child("test", job, log, timeout_s)
    if code is None:
        return Result("timeout", runtime, log, f"timed out after {timeout_s}s")
    status, detail = parse_results(tdir / "results.xml")
    if code != 0 and status == "pass":
        status, detail = "error", f"simulator exited with {code}"
    if spec.coverage and built is not None and built.root:
        if not rebase_coverage(tdir / COVERAGE_DAT, built.root, str(tree_root(spec.ip))):
            with open(log, "a") as fh:
                fh.write(f"warning: coverage: {COVERAGE_DAT} names no source under {built.root}, "
                         "the tree the reused build was compiled from; its paths were not "
                         "rewritten to this tree\n")
    return Result(status, runtime, log, detail, cpu)


def rebase_coverage(dat: Path, old_root: str, new_root: str) -> bool:
    """Point a reused build's coverage at this tree: Verilator records each
    source by the path it was compiled from, and a cached build may come
    from another worktree with the same content. Returns False when the
    file names no source under ``old_root`` (nothing could be rewritten)."""
    if old_root == new_root or not dat.is_file():
        return True
    old, new = "\x02" + old_root.rstrip("/") + "/", "\x02" + new_root.rstrip("/") + "/"
    text = dat.read_text(errors="surrogateescape")
    if old not in text:
        return False
    dat.write_text(text.replace(old, new), errors="surrogateescape")
    return True


def parse_results(results_xml: Path) -> tuple[str, str]:
    """Map a cocotb results.xml to (status, detail)."""
    if not results_xml.is_file():
        return "error", "no results.xml (simulation terminated abnormally)"
    try:
        root = ET.parse(results_xml).getroot()
    except ET.ParseError as exc:
        return "error", f"unreadable results.xml: {exc}"
    cases = root.findall(".//testcase")
    if not cases:
        return "error", "results.xml contains no test cases"
    failed = [
        c.get("name", "?")
        for c in cases
        if c.find("failure") is not None or c.find("error") is not None
    ]
    if failed:
        return "fail", "failed: " + ", ".join(failed)
    return "pass", f"{len(cases)} case(s) passed"


def _coverage_test_args(job: dict) -> dict:
    """Where a coverage run writes its code and functional coverage."""
    if not job["coverage"]:
        return {}
    tdir = Path(job["test_dir"])
    return {"plusargs": [f"+verilator+coverage+file+{tdir / COVERAGE_DAT}"],
            "extra_env": {"FUNCOV_FILE": str(tdir / FUNCOV_JSON)}}


# --------------------------------------------------------------------------
# Child process: the only code that imports cocotb.


def _ccache_env(job: dict) -> None:
    """Compile the generated C++ through ccache when it is installed and the
    build cache is on: OBJCACHE for Verilator's makefile, into ccache's own
    directory (its configuration, by default ~/.cache/ccache: one per user
    and host, shared by every run; wiki/tools/build/SPEC.md, "Build
    cache"). Otherwise compile directly (OBJCACHE empty)."""
    if not job.get("ccache") or shutil.which("ccache") is None:
        os.environ["OBJCACHE"] = ""
        return
    os.environ["OBJCACHE"] = "ccache"
    os.environ["CCACHE_BASEDIR"] = job["build_dir"]
    os.environ["CCACHE_NOHASHDIR"] = "true"
    os.environ.setdefault("CCACHE_SLOPPINESS", CCACHE_SLOPPINESS)


def _child(phase: str, job_file: str) -> int:
    job = json.loads(Path(job_file).read_text())
    from cocotb_tools.runner import get_runner

    os.environ["PATH"] = str(Path(job["verilator"]).parent) + os.pathsep + os.environ["PATH"]
    # The runner exports sys.path as PYTHONPATH, so the IP's dv/ is importable.
    sys.path.insert(0, job["dv_dir"])
    runner = get_runner("verilator")
    if phase == "build":
        _ccache_env(job)
        runner.build(
            sources=job["sources"],
            includes=job["incdirs"],
            defines={k: ("" if v is None else v) for k, v in job["defines"].items()},
            parameters=job["params"],
            hdl_toplevel=job["top"],
            build_dir=job["build_dir"],
            waves=job["waves"],
            build_args=BUILD_ARGS + (COVERAGE_ARGS if job["coverage"] else []),
            always=True,
        )
        return 0
    runner.test(  # reuses the build in build_dir
        test_module=job["module"],
        testcase=job["testcase"],
        hdl_toplevel=job["top"],
        hdl_toplevel_lang="verilog",
        build_dir=job["build_dir"],
        seed=job["seed"],
        test_dir=job["test_dir"],
        results_xml=job["results"],
        waves=job["waves"],
        **_coverage_test_args(job),
    )
    return 0


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1], sys.argv[2]))
