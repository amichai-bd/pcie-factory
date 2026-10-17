"""Quartus FPGA flow for one IP: project generation, compile, metrics.

Inputs (``<ip>`` is the IP id, ``<board>`` from cfg/fpga.yaml):

    src/rtl/<ip>/<ip>.rtl.f           flattened by build.filelist; each
                                      source with an fpga backend file
                                      (cfg/backends.yaml) is replaced by it
    src/fpga/<ip>/<board>.qsf.tcl     device, pins, I/O standards
    src/fpga/<ip>/*.sdc               exactly one timing constraints file
    src/fpga/<ip>/*.sv                exactly one board wrapper; its file
                                      stem is the top-level module

Output, all under ``workdir/<ip>/fpga/`` (cleared first):

    project.tcl, worst_path.tcl       generated Quartus Tcl
    <top>.qpf, <top>.qsf              written by project.tcl
    output_files/                     Quartus reports and bitstream
    logs/<step>.log                   stdout+stderr of each step
    metrics.json                      see wiki/tools/fpga/SPEC.md
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from build import backends
from build.filelist import SYNTHESIS_DEFINE, resolve, synthesis_defines
from build.ip import REPO_ROOT, Ip
from build.logexcerpt import excerpt
from checks import lint as check_lint
from checks.common import CheckConfigError
from fpga import reports

QUARTUS_SH = "quartus_sh"
FLOW_KIND = "fpga"  # this flow's key in cfg/backends.yaml
SOURCE_TYPES = {".sv": "SYSTEMVERILOG_FILE", ".v": "VERILOG_FILE"}
_PART_RE = re.compile(r"^[A-Za-z0-9]+$")


class FpgaError(Exception):
    """A usage, input or configuration error (exit 2)."""


# --------------------------------------------------------------------------
# Configuration and tool discovery


def load_config(cfg_dir: Path) -> dict:
    path = Path(cfg_dir) / "fpga.yaml"
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise FpgaError(f"cannot read {path}: {exc}") from None
    board = data.get("board")
    if not isinstance(board, str) or not re.match(r"^[A-Za-z0-9_]+$", board):
        raise FpgaError(f"{path}: 'board' must be a plain name, got {board!r}")
    timeout, procs = data.get("step_timeout_s"), data.get("processors")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise FpgaError(f"{path}: 'step_timeout_s' must be a positive number")
    if not isinstance(procs, int) or isinstance(procs, bool) or procs < 1:
        raise FpgaError(f"{path}: 'processors' must be a positive integer")
    try:
        known = backends.load(cfg_dir)
    except backends.BackendError as exc:
        raise FpgaError(str(exc)) from None
    return {"board": board, "step_timeout_s": float(timeout), "processors": procs,
            "backends": known}


def host_quartus_root(cfg_dir: Path) -> Path | None:
    """``hosts.omarchy.quartus_root`` from cfg/host.yaml, if filled in."""
    try:
        data = yaml.safe_load((Path(cfg_dir) / "host.yaml").read_text()) or {}
        root = data["hosts"]["omarchy"]["quartus_root"]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return None
    return Path(str(root)) if root else None


def quartus_bin_candidates(cfg_dir: Path) -> list[Path]:
    """Directories searched after PATH: ``$QUARTUS_ROOTDIR/bin``, then the
    host pin's ``<quartus_root>/bin``."""
    dirs = []
    if os.environ.get("QUARTUS_ROOTDIR"):
        dirs.append(Path(os.environ["QUARTUS_ROOTDIR"]) / "bin")
    root = host_quartus_root(cfg_dir)
    if root is not None:
        dirs.append(root / "bin")
    return dirs


def find_quartus(cfg_dir: Path) -> Path | None:
    """The directory holding ``quartus_sh``: PATH, ``$QUARTUS_ROOTDIR/bin``,
    then cfg/host.yaml ``hosts.omarchy.quartus_root``/bin."""
    found = shutil.which(QUARTUS_SH)
    if found:
        return Path(found).parent
    for d in quartus_bin_candidates(cfg_dir):
        exe = d / QUARTUS_SH
        if exe.is_file() and os.access(exe, os.X_OK):
            return d
    return None


def quartus_not_found(cfg_dir: Path) -> str:
    places = ["PATH", "$QUARTUS_ROOTDIR/bin"]
    root = host_quartus_root(cfg_dir)
    places.append(f"{root}/bin" if root else "cfg/host.yaml hosts.omarchy.quartus_root (empty)")
    return (f"{QUARTUS_SH} not found (looked in {', '.join(places)}); "
            "Quartus runs only on the FPGA CI host")


# --------------------------------------------------------------------------
# Planning


@dataclass
class Plan:
    ip: Ip
    top: str
    board: str
    part: str | None
    processors: int
    sources: list[Path]        # Quartus sources: backends selected, wrapper last
    incdirs: list[Path]
    defines: dict[str, str | None]
    sdc: Path
    qsf_tcl: Path
    wrapper: Path
    out_dir: Path
    steps: list[tuple[str, list[str]]] = field(default_factory=list)
    lint_sources: list[Path] = field(default_factory=list)  # portable view, wrapper last
    swapped: list[tuple[Path, Path]] = field(default_factory=list)  # (portable, backend)


def _one(fpga_dir: Path, pattern: str, what: str) -> Path:
    found = sorted(fpga_dir.glob(pattern))
    if len(found) != 1:
        names = ", ".join(p.name for p in found) or "none"
        raise FpgaError(f"{fpga_dir}: expected exactly one {what} ({pattern}), found {names}")
    return found[0]


def fpga_ips(ips: list[Ip]) -> list[Ip]:
    """IPs that are FPGA synthesis targets (have an ``fpga/<ip>/`` folder)."""
    return [ip for ip in ips if ip.dir("fpga").is_dir()]


def plan(ip: Ip, workdir: Path, board: str, part: str | None = None,
         processors: int = 1, known: dict[str, backends.Backend] | None = None) -> Plan:
    """Validate an IP's FPGA inputs and resolve its sources. ``known`` is
    cfg/backends.yaml: Quartus gets this flow's backend files in place of
    their portable files; lint keeps the portable (simulation) view."""
    fpga_dir = ip.dir("fpga")
    if not fpga_dir.is_dir():
        raise FpgaError(f"IP {ip.id} has no fpga/{ip.id}/ folder (not an FPGA target)")
    if part is not None and not _PART_RE.match(part):
        raise FpgaError(f"--part must be a device name like EP4CE115F29C7, got {part!r}")
    qsf_tcl = fpga_dir / f"{board}.qsf.tcl"
    if not qsf_tcl.is_file():
        raise FpgaError(f"{qsf_tcl}: board settings file not found")
    sdc = _one(fpga_dir, "*.sdc", "timing constraints file")
    wrapper = _one(fpga_dir, "*.sv", "board wrapper")
    top = wrapper.stem
    if not re.search(rf"^\s*module\s+{re.escape(top)}\b", wrapper.read_text(), re.M):
        raise FpgaError(f"{wrapper}: does not declare module {top} (the wrapper's "
                        "file stem names the top-level module)")
    resolved = resolve(ip.rtl_f)
    portable = list(resolved.sources)
    if wrapper.resolve() in portable:
        raise FpgaError(f"{wrapper}: the board wrapper must not be in {ip.rtl_f.name}")
    known = known or {}
    try:
        sources = backends.select(portable, known.get(FLOW_KIND), known)
    except backends.BackendError as exc:
        raise FpgaError(str(exc)) from None
    swapped = [(a, b) for a, b in zip(portable, sources) if a != b]
    portable.append(wrapper.resolve())
    sources.append(wrapper.resolve())
    for src in sources:
        if src.suffix not in SOURCE_TYPES:
            raise FpgaError(f"{src}: unsupported source type {src.suffix!r} "
                            f"(expected {', '.join(sorted(SOURCE_TYPES))})")
    out_dir = ip.work_dir(workdir) / "fpga"
    p = Plan(ip, top, board, part, processors, sources, list(resolved.incdirs),
             synthesis_defines(resolved, ip.rtl_f), sdc.resolve(), qsf_tcl.resolve(),
             wrapper.resolve(), out_dir, lint_sources=portable, swapped=swapped)
    p.steps = [
        ("project", [QUARTUS_SH, "-t", "project.tcl"]),
        ("map", ["quartus_map", top, "-c", top]),
        ("fit", ["quartus_fit", top, "-c", top]),
        ("sta", ["quartus_sta", top, "-c", top]),
        ("worst_path", ["quartus_sta", "-t", "worst_path.tcl"]),
    ]
    return p


def lint_command(p: Plan, verilator: Path | str) -> list[str]:
    """Verilator lint of the flattened sources plus the wrapper as top, in
    the simulation view like ``./build check`` (without ``SYNTHESIS``, so
    assertion-only signals have their reader). ``-Wno-fatal`` lets every
    warning print; ``lint_findings`` decides pass or fail."""
    cmd = [str(verilator), "--lint-only", "-Wall", "-Wno-fatal", "--top-module", p.top]
    cmd += [f"+incdir+{d}" for d in p.incdirs]
    cmd += [f"+define+{n}" if v is None else f"+define+{n}={v}" for n, v in p.defines.items()
            if n != SYNTHESIS_DEFINE]
    cmd += [str(s) for s in p.lint_sources]
    return cmd


# --------------------------------------------------------------------------
# Generated Tcl


def _brace(text: str) -> str:
    if any(c in text for c in "{}\\\n"):
        raise FpgaError(f"cannot quote {text!r} for Tcl")
    return "{" + text + "}"


def project_tcl(p: Plan) -> str:
    """The script ``quartus_sh -t`` runs to create ``<top>.qpf/.qsf``.

    Order: generic settings, sources, SDC, then the IP's board file (device,
    pins), then the ``--part`` override if given."""
    t = _brace(p.top)
    lines = [
        f"# Generated by ./build fpga for IP {p.ip.id}; do not edit.",
        f"project_new {t} -revision {t} -overwrite",
        f"set_global_assignment -name TOP_LEVEL_ENTITY {t}",
        "set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files",
        "set_global_assignment -name VERILOG_INPUT_VERSION SYSTEMVERILOG_2005",
        f"set_global_assignment -name NUM_PARALLEL_PROCESSORS {p.processors}",
    ]
    for d in p.incdirs:
        lines.append(f"set_global_assignment -name SEARCH_PATH {_brace(str(d))}")
    for name, value in p.defines.items():
        macro = name if value is None else f"{name}={value}"
        lines.append(f"set_global_assignment -name VERILOG_MACRO {_brace(macro)}")
    for src in p.sources:
        lines.append(f"set_global_assignment -name {SOURCE_TYPES[src.suffix]} {_brace(str(src))}")
    lines.append(f"set_global_assignment -name SDC_FILE {_brace(str(p.sdc))}")
    lines.append(f"source {_brace(str(p.qsf_tcl))}")
    if p.part:
        lines.append(f"set_global_assignment -name DEVICE {p.part}")
    lines += ["export_assignments", "project_close", ""]
    return "\n".join(lines)


def worst_path_tcl(p: Plan) -> str:
    """The ``quartus_sta -t`` script that writes the worst setup path to
    ``worst_path.txt`` as ``key=value`` lines (empty when there is none)."""
    t = _brace(p.top)
    return "\n".join([
        f"# Generated by ./build fpga for IP {p.ip.id}; do not edit.",
        f"project_open {t} -revision {t}",
        "create_timing_netlist",
        "read_sdc",
        "update_timing_netlist",
        "set fh [open worst_path.txt w]",
        "foreach_in_collection path [get_timing_paths -setup -npaths 1] {",
        "    puts $fh \"slack=[get_path_info $path -slack]\"",
        "    puts $fh \"from=[get_node_info -name [get_path_info $path -from]]\"",
        "    puts $fh \"to=[get_node_info -name [get_path_info $path -to]]\"",
        "    foreach side {from_clock to_clock} {",
        "        set name {}",
        "        catch {set name [get_clock_info -name [get_path_info $path -$side]]}",
        "        puts $fh \"$side=$name\"",
        "    }",
        "    puts $fh \"data_delay=[get_path_info $path -data_delay]\"",
        "}",
        "close $fh",
        "delete_timing_netlist",
        "project_close",
        "",
    ])


def generate(p: Plan, clean: bool = True) -> None:
    """Write the generated Tcl scripts into ``p.out_dir``. With ``clean``
    (a real run) the directory is recreated first; without it (``--dry-run``)
    a previous compile is left in place."""
    if clean and p.out_dir.exists():
        shutil.rmtree(p.out_dir)
    (p.out_dir / "logs").mkdir(parents=True, exist_ok=True)
    (p.out_dir / "project.tcl").write_text(project_tcl(p))
    (p.out_dir / "worst_path.tcl").write_text(worst_path_tcl(p))


# --------------------------------------------------------------------------
# Running


@dataclass
class StepResult:
    name: str
    status: str  # pass | fail | timeout
    runtime_s: float
    log: Path


def run_step(name: str, cmd: list[str], cwd: Path, log: Path, timeout_s: float) -> StepResult:
    """Run in a process group; kill and reap it on timeout or cancellation."""
    start = time.monotonic()
    with open(log, "w") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        proc = None
        interruptible = False
        cancelled = False

        def cancel(signum, frame):
            nonlocal cancelled
            cancelled = True
            # Popen must return its child handle before cancellation can kill
            # that group. Raising inside process creation can lose the handle.
            if interruptible:
                raise KeyboardInterrupt

        previous = {sig: signal.signal(sig, cancel)
                    for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                        start_new_session=True)
            except OSError as exc:
                if cancelled:
                    raise KeyboardInterrupt
                fh.write(f"cannot start: {exc}\n")
                return StepResult(name, "fail", 0.0, log)
            interruptible = True
            if cancelled:
                raise KeyboardInterrupt
            code = proc.wait(timeout=timeout_s)
            status = "pass" if code == 0 else "fail"
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            interruptible = False  # Repeated signals must not interrupt kill/reap.
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            if isinstance(exc, KeyboardInterrupt):
                fh.write("\ncancelled; process group killed\n")
                raise
            fh.write(f"\ntimeout after {timeout_s:.0f}s; process group killed\n")
            status = "timeout"
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return StepResult(name, status, time.monotonic() - start, log)


def lint_findings(log: Path, src_root: Path, exited_ok: bool) -> list[str]:
    """Verilator messages in the lint log that the reviewed waivers in
    ``<src_root>/lint_waivers.yaml`` do not cover, matched as the
    ``./build check`` lint check matches them; a nonzero exit with no error
    message is a finding too. Stale waivers are that check's business."""
    try:
        waivers = check_lint.load_waivers(src_root)
        text = log.read_text(errors="replace")
    except (CheckConfigError, OSError) as exc:
        return [str(exc)]
    msgs = check_lint.parse_output(text, src_root)
    out = [m.render() for m in msgs if not any(w.covers(m) for w in waivers)]
    if not exited_ok and not any(m.severity == "Error" for m in msgs):
        out.append("verilator exited nonzero")
    return out


def quartus_cmd(cmd: list[str], bin_dir: Path) -> list[str]:
    return [str(bin_dir / cmd[0])] + cmd[1:]


def run(p: Plan, bin_dir: Path, timeout_s: float, verilator: Path | None) -> int:
    """Lint (when Verilator is available), compile, parse. Returns 0 when
    metrics were written and timing is met, 1 otherwise."""
    generate(p)
    for old, new in p.swapped:
        print(f"[backend] {old.name} -> {new.name}", flush=True)
    logs = p.out_dir / "logs"
    flow: dict = {"lint": "skipped", "steps": {}}
    steps = list(p.steps)
    if verilator is not None:
        steps.insert(0, ("lint", lint_command(p, verilator)))
    else:
        print("lint: skipped (verilator not found)")
    for name, cmd in steps:
        full = cmd if name == "lint" else quartus_cmd(cmd, bin_dir)
        print(f"[{name}] {' '.join(full)}", flush=True)
        res = run_step(name, full, p.out_dir, logs / f"{name}.log", timeout_s)
        if name == "lint" and res.status != "timeout":
            findings = lint_findings(res.log, p.ip.src_root, res.status == "pass")
            for f in findings:
                print(f"[lint] {f}")
            res.status = "fail" if findings else "pass"
        print(f"[{name}] {res.status} ({res.runtime_s:.1f}s) log: {res.log}", flush=True)
        if name == "lint":
            flow["lint"] = res.status
        else:
            flow["steps"][name] = {"status": res.status, "runtime_s": round(res.runtime_s, 1),
                                   "warnings": quartus_warnings(res.log)}
        if res.status != "pass":
            print_failure(res.log)
            return 1
    try:
        metrics = reports.collect(p.out_dir, p.top)
    except reports.ReportError as exc:
        print(f"error: {exc}")
        return 1
    metrics = {"ip": p.ip.id, "board": p.board, **metrics, "flow": flow}
    path = p.out_dir / "metrics.json"
    path.write_text(json.dumps(metrics, indent=2) + "\n")
    print_summary(metrics)
    print(f"metrics: {path}")
    if not metrics["timing"]["met"]:
        print("timing: NOT MET (negative slack, or unconstrained paths)")
        return 1
    return 0


_DONE_RE = re.compile(r"was successful\. \d+ errors?, (\d+) warnings?")


def quartus_warnings(log: Path) -> int | None:
    """Warning count from a Quartus step's closing "was successful" line."""
    try:
        m = _DONE_RE.findall(log.read_text(errors="replace"))
    except OSError:
        return None
    return int(m[-1]) if m else None


def print_summary(m: dict) -> None:
    r, t = m["resources"], m["timing"]
    le = r["logic_elements"]
    print(f"{m['ip']}: {m['device']} ({m['quartus_version']})")
    print(f"  logic elements {le['used']}/{le['total']}, registers {r['registers']}")
    mb = r.get("memory_blocks")
    if mb:
        print(f"  {mb['type']} blocks {mb['used']}/{mb['total']}")
    for ram in r.get("rams") or []:
        print(f"  RAM {ram['name']}: {ram['mode']}, {ram['clock_mode']}, "
              f"{ram['port_a_depth']}x{ram['port_a_width']}, {ram['blocks']} block(s), "
              f"mixed RDW {ram['mixed_rdw']}")
    for name, c in t["clocks"].items():
        print(f"  clock {name}: fmax {c['fmax_mhz']} MHz (restricted "
              f"{c['restricted_fmax_mhz']}), setup {c['setup_slack_ns']} ns, "
              f"hold {c['hold_slack_ns']} ns")
    print(f"  unconstrained paths: {t['unconstrained_total']}")
    cp = t["critical_path"]
    warned = {n: s["warnings"] for n, s in m["flow"]["steps"].items() if s["warnings"]}
    if warned:
        print(f"  quartus warnings (review in logs/): {warned}")
    if cp:
        print(f"  critical path {cp['from']} -> {cp['to']} (slack {cp['slack_ns']} ns)")


# Quartus "Error (<id>):" and "Critical Warning[ (<id>)]:" messages, and
# Verilator "%Error" lines from the lint step.
ERROR_RE = re.compile(r"^\s*(?:Error \(\d+\)|Critical Warning(?: \(\d+\))?):|^%Error")


def print_failure(log: Path) -> None:
    """Every error line of a failed step's log (capped), then its tail."""
    try:
        text = log.read_text(errors="replace")
    except OSError:
        return
    for line in excerpt(text, ERROR_RE, str(log)):
        print(line, flush=True)
