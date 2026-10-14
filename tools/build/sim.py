"""Verilator + cocotb simulation driver.

The parent process never imports cocotb. Each build and each test runs in a
child process (``python3 -B tools/build/sim.py <phase> <job.json>``) that uses
cocotb's Python runner, so a hung simulation can be killed on timeout and
tests can run in parallel.

Output layout (``<ip>`` is the IP id, e.g. ``a`` or ``a/b``):

    workdir/<ip>/build/<top>[-<hash>]/   Verilator build, build.log
    workdir/<ip>/<test>/<seed>/          results.xml, sim.log, waves
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):  # executed as a script by the parent process
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build.filelist import resolve  # noqa: E402
from build.ip import Ip  # noqa: E402

COCOTB_VERSION = "2.1.0"
VERILATOR_FALLBACK_DIRS = [Path("/opt/verilator/5.052/bin")]


class SimError(Exception):
    pass


def find_verilator() -> Path | None:
    """Locate ``verilator``: PATH, then ``$VERILATOR_ROOT/bin``, then the
    pinned install directories."""
    found = shutil.which("verilator")
    if found:
        return Path(found).resolve()
    candidates = []
    if os.environ.get("VERILATOR_ROOT"):
        candidates.append(Path(os.environ["VERILATOR_ROOT"]) / "bin")
    candidates += VERILATOR_FALLBACK_DIRS
    for d in candidates:
        exe = d / "verilator"
        if exe.is_file() and os.access(exe, os.X_OK):
            return exe
    return None


@dataclass
class BuildSpec:
    """One Verilator build: an IP's sim filelist elaborated for one top."""

    ip: Ip
    top: str
    params: dict = field(default_factory=dict)
    waves: bool = False

    @property
    def key(self) -> str:
        extra = {"params": self.params, "waves": self.waves}
        if not self.params and not self.waves:
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


def _run_child(phase: str, job: dict, log: Path, timeout_s: float | None) -> tuple[int | None, float]:
    """Run one child phase; return (exit code or None on timeout, runtime)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    job_file = log.with_suffix(".job.json")
    job_file.write_text(json.dumps(job, indent=2, sort_keys=True))
    cmd = [sys.executable, "-B", str(Path(__file__).resolve()), phase, str(job_file)]
    start = time.monotonic()
    with open(log, "w") as fh:
        proc = subprocess.Popen(
            cmd, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True
        )
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            code = None
    return code, time.monotonic() - start


def _job(spec: BuildSpec, workdir: Path, verilator: Path) -> dict:
    flat = resolve(spec.ip.sim_f)
    return {
        "verilator": str(verilator),
        "sources": [str(p) for p in flat.sources],
        "incdirs": [str(p) for p in flat.incdirs],
        "defines": flat.defines,
        "top": spec.top,
        "params": spec.params,
        "waves": spec.waves,
        "build_dir": str(spec.build_dir(workdir)),
        "dv_dir": str(spec.ip.dv_dir),
    }


def build(spec: BuildSpec, workdir: Path, verilator: Path) -> Result:
    """Elaborate and compile ``spec``; log at ``<build_dir>/build.log``."""
    job = _job(spec, workdir, verilator)
    log = spec.build_dir(workdir) / "build.log"
    code, runtime = _run_child("build", job, log, timeout_s=None)
    status = "pass" if code == 0 else "error"
    return Result(status, runtime, log, "" if code == 0 else f"build failed, see {log}")


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
    root = ET.parse(results_xml).getroot()
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


# --------------------------------------------------------------------------
# Child process: the only code that imports cocotb.


def _child(phase: str, job_file: str) -> int:
    job = json.loads(Path(job_file).read_text())
    import cocotb
    from cocotb_tools.runner import get_runner

    if cocotb.__version__ != COCOTB_VERSION:
        print(f"error: cocotb {COCOTB_VERSION} required, found {cocotb.__version__}")
        return 2
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
    )
    return 0


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1], sys.argv[2]))
