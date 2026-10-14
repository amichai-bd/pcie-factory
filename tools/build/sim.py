"""Verilator + cocotb simulation driver.

The parent process never imports cocotb. Each build and each test runs in a
child process (``python3 -B tools/build/sim.py <phase> <job.json>``) that uses
cocotb's Python runner, so a hung simulation can be killed on timeout and
tests can run in parallel.

Output layout (``<ip>`` is the IP id, e.g. ``a`` or ``a_b``):

    workdir/<ip>/build/<top>[-<hash>]/   Verilator build, build.log
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

from build.filelist import FilelistError, resolve  # noqa: E402
from build.ip import REPO_ROOT, Ip  # noqa: E402

BUILD_TIMEOUT_S = 1800.0  # wall-clock bound on one Verilator build
# Verilator options of a coverage build; the regress SPEC owns the choice.
COVERAGE_ARGS = ["--coverage-line", "--coverage-toggle"]
COVERAGE_DAT = "coverage.dat"  # code coverage of one run, in its test directory
FUNCOV_JSON = "funcov.json"  # functional coverage of one run (regress.funcov)


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
    """One Verilator build: an IP's sim filelist elaborated for one top."""

    ip: Ip
    top: str
    params: dict = field(default_factory=dict)
    waves: bool = False
    coverage: bool = False

    @property
    def key(self) -> str:
        extra = {"params": self.params, "waves": self.waves}
        if self.coverage:
            extra["coverage"] = True
        if not self.params and not self.waves and not self.coverage:
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


def test_dir(workdir: Path, ip: Ip, test: str, seed: int) -> Path:
    return ip.work_dir(workdir) / test / str(seed)


# Child processes currently running, so an interrupt can kill them all.
_ACTIVE: set[subprocess.Popen] = set()
_LOCK = threading.Lock()
_STOPPING = threading.Event()


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


def _run_child(phase: str, job: dict, log: Path, timeout_s: float | None) -> tuple[int | None, float]:
    """Run one child phase; return (exit code or None on timeout, runtime)."""
    if _STOPPING.is_set():
        raise KeyboardInterrupt
    log.parent.mkdir(parents=True, exist_ok=True)
    job_file = log.with_suffix(".job.json")
    job_file.write_text(json.dumps(job, indent=2, sort_keys=True))
    cmd = [sys.executable, "-B", str(Path(__file__).resolve()), phase, str(job_file)]
    start = time.monotonic()
    code: int | None = None
    with open(log, "w") as fh:
        proc = subprocess.Popen(
            cmd, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True
        )
        with _LOCK:
            _ACTIVE.add(proc)
        try:
            code = proc.wait(timeout=timeout_s)
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
    return code, time.monotonic() - start


def _job(spec: BuildSpec, workdir: Path, verilator: Path) -> dict:
    return {
        "verilator": str(verilator),
        "top": spec.top,
        "params": spec.params,
        "waves": spec.waves,
        "coverage": spec.coverage,
        "build_dir": str(spec.build_dir(workdir)),
        "dv_dir": str(spec.ip.dv_dir),
    }


def build(spec: BuildSpec, workdir: Path, verilator: Path) -> Result:
    """Elaborate and compile ``spec``; log at ``<build_dir>/build.log``.
    A filelist that does not resolve is a build ``error`` (not an exception),
    with the ``file:line`` message as detail and in the log."""
    log = spec.build_dir(workdir) / "build.log"
    try:
        flat = resolve(spec.ip.sim_f)
    except FilelistError as exc:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(f"error: {exc}\n")
        return Result("error", 0.0, log, str(exc))
    job = _job(spec, workdir, verilator)
    job.update(
        sources=[str(p) for p in flat.sources],
        incdirs=[str(p) for p in flat.incdirs],
        defines=flat.defines,
    )
    code, runtime = _run_child("build", job, log, timeout_s=BUILD_TIMEOUT_S)
    if code is None:
        return Result("error", runtime, log, f"build timed out after {BUILD_TIMEOUT_S}s")
    if code != 0:
        return Result("error", runtime, log, f"build failed, see {log}")
    return Result("pass", runtime, log)


def run_test(
    spec: BuildSpec,
    workdir: Path,
    verilator: Path,
    test: str,
    module: str,
    testcase: str | None,
    seed: int,
    timeout_s: float | None,
) -> Result:
    """Run one already-built test with one seed."""
    tdir = test_dir(workdir, spec.ip, test, seed)
    if tdir.exists():
        shutil.rmtree(tdir)
    job = _job(spec, workdir, verilator)
    job.update(
        module=module,
        testcase=testcase,
        seed=seed,
        test_dir=str(tdir),
        results=str(tdir / "results.xml"),
    )
    log = tdir / "sim.log"
    code, runtime = _run_child("test", job, log, timeout_s)
    if code is None:
        return Result("timeout", runtime, log, f"timed out after {timeout_s}s")
    status, detail = parse_results(tdir / "results.xml")
    if code != 0 and status == "pass":
        status, detail = "error", f"simulator exited with {code}"
    return Result(status, runtime, log, detail)


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


def _child(phase: str, job_file: str) -> int:
    job = json.loads(Path(job_file).read_text())
    from cocotb_tools.runner import get_runner

    os.environ["PATH"] = str(Path(job["verilator"]).parent) + os.pathsep + os.environ["PATH"]
    # The runner exports sys.path as PYTHONPATH, so the IP's dv/ is importable.
    sys.path.insert(0, job["dv_dir"])
    runner = get_runner("verilator")
    if phase == "build":
        runner.build(
            sources=job["sources"],
            includes=job["incdirs"],
            defines={k: ("" if v is None else v) for k, v in job["defines"].items()},
            parameters=job["params"],
            hdl_toplevel=job["top"],
            build_dir=job["build_dir"],
            waves=job["waves"],
            build_args=COVERAGE_ARGS if job["coverage"] else [],
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
