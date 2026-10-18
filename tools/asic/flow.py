"""``./build asic``: run OpenROAD-flow-scripts (ORFS) on one IP.

Spec: wiki/tools/asic/SPEC.md. Method: wiki/bkm/pd.md.

Provenance: the ORFS input split, per-step JSON merge and summary idea are
adapted from nand2anything tools/asic/asic.py (commit 085ef4b, same owner, no
license file); rewritten for this repository's IP layout and runners.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from build import backends
from build.filelist import resolve, synthesis_defines
from build.ip import Context, Ip
from build.logexcerpt import excerpt

from . import metrics as asic_metrics

PLATFORMS = ("sky130hd",)
FLOW_KIND = "asic"  # this flow's key in cfg/backends.yaml
VARIANT = "base"
DEFAULT_TIMEOUT_S = 3600
EXTRA_TCL = Path(__file__).resolve().parent / "extra_metrics.tcl"
STUDY_MARKER = "# build-asic: study-clock-period"
CLOCKS_TCL = Path(__file__).resolve().parent / "clock_slacks.tcl"

# Variables the tool generates; an IP's <platform>.mk must not set them.
TOOL_OWNED = ("VERILOG_FILES", "VERILOG_INCLUDE_DIRS", "VERILOG_DEFINES",
              "SDC_FILE", "PLATFORM", "DESIGN_NICKNAME", "DESIGN_CONFIG", "WORK_HOME",
              "FLOW_VARIANT", "NUM_CORES", "RESULTS_DIR", "LOG_DIR", "REPORTS_DIR",
              "OBJECTS_DIR", "ASIC_INPUT_DIR")
RUNNERS = ("docker", "native")

ENV_RUNNER = "ORFS_RUNNER"
ENV_ROOT = "ORFS_ROOT"
# ORFS checkout inside the official openroad/orfs images.
IMAGE_ROOT = "/OpenROAD-flow-scripts"
# Container limits on the shared host: memory (swap included, as on the
# host's verified invocation) and process count. CPUs come from cfg.
# Memory is 10 GB under the captain's decision of 2026-10-18 (up from 6 GB,
# so detailed route of the largest target completes; one ORFS job at a time
# per host): https://github.com/amichai-bd/pcie-factory/issues/296#issuecomment-6253639191.
# Before that: 6 GB (decision 2 of 2026-10-17,
# https://github.com/amichai-bd/pcie-factory/issues/203#issuecomment-6240731810).
DOCKER_MEMORY = "10g"
DOCKER_PIDS = 4096
DEFAULT_CORES = 2
DIGEST = "@sha256:"

# "[override] [export] [override] NAME op value" and "[...] define NAME".
_PREFIX = r"^\s*(?:override\s+)?(?:export\s+)?(?:override\s+)?"
_ASSIGN = re.compile(_PREFIX + r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?|:|::|:::|\+|!)?=\s*(.*?)\s*$")
_DEFINE = re.compile(_PREFIX + r"define\s+([A-Za-z_][A-Za-z0-9_]*)")
# OpenROAD "[ERROR <code>]", Yosys "ERROR:", OpenSTA "Error:" and make's
# "make[<n>]: *** ... Error <n>" lines.
ERROR_RE = re.compile(r"\[ERROR\b|^(?:ERROR|Error):|^make(?:\[\d+\])?: \*\*\*")


class AsicError(Exception):
    """A usage or configuration error (exit 2)."""


@dataclass(frozen=True)
class Orfs:
    """How to reach ORFS: a docker image or a native checkout."""

    runner: str
    root: str                  # ORFS checkout (inside the image for docker)
    image: str | None = None
    source: str = "cfg"        # where the setting came from, for the log
    commit: str | None = None  # recorded ORFS revision of that image or root
    cores: int = DEFAULT_CORES  # NUM_CORES for ORFS and the container CPU cap


def host_orfs(cfg_dir: Path) -> dict:
    """The ``orfs_*`` fields of the host entry in cfg/host.yaml that records
    an ORFS image or root (at most one host may)."""
    path = cfg_dir / "host.yaml"
    try:
        hosts = (yaml.safe_load(path.read_text()) or {}).get("hosts") or {}
    except (OSError, yaml.YAMLError, AttributeError) as exc:
        raise AsicError(f"{path}: {exc}") from exc
    if not isinstance(hosts, dict):
        raise AsicError(f"{path}: 'hosts' must be a mapping")
    found = {name: h for name, h in hosts.items()
             if isinstance(h, dict) and (h.get("orfs_image") or h.get("orfs_root"))}
    if len(found) > 1:
        raise AsicError(f"{path}: more than one host records ORFS ({', '.join(sorted(found))})")
    return next(iter(found.values()), {})


def load_orfs(cfg_dir: Path, env: dict) -> Orfs:
    """ORFS settings from the host entry in cfg/host.yaml (``orfs_image``,
    ``orfs_root``, ``orfs_commit``, ``orfs_cores``), with ``ORFS_ROOT`` and
    ``ORFS_RUNNER`` overrides for a native checkout. The image comes only from
    cfg and must be pinned by digest; an ``ORFS_IMAGE`` environment variable
    is ignored."""
    path = cfg_dir / "host.yaml"
    host = host_orfs(cfg_dir)
    image = host.get("orfs_image") or None
    native_root = env.get(ENV_ROOT) or host.get("orfs_root") or None
    runner = env.get(ENV_RUNNER)
    if runner is None:
        if env.get(ENV_ROOT):
            runner = "native"
        elif image:
            runner = "docker"
        elif native_root:
            runner = "native"
    if runner not in RUNNERS:
        raise AsicError(f"no ORFS configured: set a host's orfs_image or orfs_root in {path}, "
                        f"or ${ENV_ROOT}"
                        + (f" (${ENV_RUNNER}={runner!r} is not one of "
                           f"{', '.join(RUNNERS)})" if runner else ""))
    if runner == "docker":
        if not image:
            raise AsicError(f"docker runner needs orfs_image in {path}")
        if DIGEST not in str(image):
            raise AsicError(f"{path}: orfs_image {image!r} is not pinned by digest "
                            f"(<name>:<tag>{DIGEST}<hex>)")
        root = IMAGE_ROOT
        recorded = True
    else:
        if not native_root:
            raise AsicError(f"native runner needs the ORFS root: set ${ENV_ROOT} or orfs_root in {path}")
        root = native_root
        recorded = root == host.get("orfs_root")
    cores = host.get("orfs_cores", DEFAULT_CORES)
    if isinstance(cores, bool) or not isinstance(cores, int) or cores < 1:
        raise AsicError(f"{path}: orfs_cores must be a positive integer, got {cores!r}")
    source = "env" if any(env.get(k) for k in (ENV_RUNNER, ENV_ROOT)) else "cfg"
    commit = host.get("orfs_commit") if recorded else None
    return Orfs(runner, str(root), str(image) if image else None, source,
                str(commit) if commit else None, cores)


def read_design_mk(path: Path) -> dict[str, str]:
    """Variables a design .mk assigns (``=``, ``:=``, ``?=``, ``+=``, ``!=``,
    with ``export``/``override``) or defines (``define NAME``), with line
    continuations joined. Used to validate it, not to evaluate make."""
    text = re.sub(r"\\\n", " ", path.read_text())
    out = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = _DEFINE.match(line)
        if m:
            out[m.group(1)] = "<define>"
            continue
        m = _ASSIGN.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


@dataclass
class Plan:
    ip: Ip
    platform: str
    design: str
    nick: str
    out: Path               # workdir/<ip>/asic
    design_mk: Path
    sdc: Path
    sources: list[Path]
    incdirs: list[Path]
    defines: dict[str, str | None]
    clock_period_ns: float | None
    synth_only: bool = False
    study_clock_period_ns: float | None = None
    backends: list[tuple[Path, Path]] = field(default_factory=list)  # (portable, backend)

    @property
    def effective_sdc(self) -> Path:
        return self.out / "study.sdc" if self.study_clock_period_ns is not None else self.sdc

    @property
    def config_mk(self) -> Path:
        return self.out / "config.mk"

    @property
    def work_home(self) -> Path:
        return self.out / "run"

    @property
    def metrics_json(self) -> Path:
        return self.out / "metrics.json"

    @property
    def synthesis_json(self) -> Path:
        return self.out / "synthesis.json"

    @property
    def worst_path_txt(self) -> Path:
        return self.out / "worst_path.txt"

    @property
    def flow_log(self) -> Path:
        return self.out / "flow.log"


def find_inputs(ip: Ip, platform: str) -> tuple[Path, Path]:
    asic = ip.dir("asic")
    mk = asic / f"{platform}.mk"
    if not mk.is_file():
        raise AsicError(f"IP {ip.id} has no ASIC config {mk} (an IP is an ASIC target "
                        f"only with asic/{ip.id}/<platform>.mk and one asic/{ip.id}/*.sdc)")
    sdcs = sorted(asic.glob("*.sdc"))
    if len(sdcs) != 1:
        raise AsicError(f"IP {ip.id}: expected exactly one .sdc in {asic}, found "
                        f"{len(sdcs)}")
    return mk, sdcs[0]


def parse_clock_period(sdc_text: str) -> float | None:
    """The first ``create_clock -period`` value (in the SDC time unit, ns).
    The value may be a literal or a ``$var`` set earlier with ``set var <num>``."""
    m = re.search(r"create_clock\b[^\n]*?-period\s+(\$\{?\w+\}?|[0-9.]+)", sdc_text)
    if not m:
        return None
    value = m.group(1)
    if value.startswith("$"):
        name = value.strip("${}")
        sets = re.findall(rf"^\s*set\s+{re.escape(name)}\s+([0-9.]+)\s*$",
                          sdc_text[:m.start()], re.M)
        if not sets:
            return None
        value = sets[-1]
    try:
        return float(value)
    except ValueError:
        return None


def make_plan(ctx: Context, ip: Ip, platform: str, *, synth_only: bool = False,
              study_clock_period_ns: float | None = None) -> Plan:
    if platform not in PLATFORMS:
        raise AsicError(f"unknown platform {platform!r} (known: {', '.join(PLATFORMS)})")
    mk, sdc = find_inputs(ip, platform)
    if study_clock_period_ns is not None:
        if not math.isfinite(study_clock_period_ns) or study_clock_period_ns <= 0:
            raise AsicError("--study-clock-period must be finite and > 0 ns")
        if synth_only:
            raise AsicError("--study-clock-period cannot be combined with --synth-only")
        if STUDY_MARKER not in sdc.read_text().splitlines():
            raise AsicError(f"{sdc}: does not support --study-clock-period (missing {STUDY_MARKER})")
    out = ip.work_dir(ctx.workdir) / ("asic-synth" if synth_only else "asic")
    if study_clock_period_ns is not None:
        period = str(float(study_clock_period_ns)).removesuffix(".0")
        out = ip.work_dir(ctx.workdir) / "asic-study" / f"{period}ns"
    knobs = read_design_mk(mk)
    owned = sorted(k for k in knobs if k in TOOL_OWNED)
    if owned:
        raise AsicError(f"{mk}: sets {', '.join(owned)}, which ./build asic generates; "
                        "remove them")
    design = knobs.get("DESIGN_NAME")
    if not design:
        raise AsicError(f"{mk}: DESIGN_NAME is required")
    resolved = resolve(ip.rtl_f)
    if not resolved.sources:
        raise AsicError(f"{ip.rtl_f}: no sources")
    portable = list(resolved.sources)
    try:
        known = backends.load(ctx.cfg_dir)
        sources = backends.select(portable, known.get(FLOW_KIND), known)
    except backends.BackendError as exc:
        raise AsicError(str(exc)) from None
    swapped = [(a, b) for a, b in zip(portable, sources) if a != b]
    return Plan(
        ip=ip, platform=platform, design=design, nick=ip.id,
        out=out, study_clock_period_ns=study_clock_period_ns,
        design_mk=mk, sdc=sdc, synth_only=synth_only,
        sources=sources, incdirs=resolved.incdirs, backends=swapped,
        defines=synthesis_defines(resolved, ip.rtl_f),
        clock_period_ns=parse_clock_period(sdc.read_text()),
    )


def _mk_list(items: list[str]) -> str:
    return " \\\n    ".join(items) if items else ""


def render_config_mk(plan: Plan) -> str:
    """The ORFS DESIGN_CONFIG: the IP's knobs plus the generated inputs."""
    defines = [f"-D{k}" if v is None else f"-D{k}={v}" for k, v in plan.defines.items()]
    lines = [
        "# Generated by ./build asic; do not edit (wiki/tools/asic/SPEC.md).",
        f"# IP: {plan.ip.id}",
        "",
        "# The IP's ASIC input folder, for files its .mk names (e.g. PDN_TCL).",
        f"export ASIC_INPUT_DIR = {plan.design_mk.parent}",
        f"include {plan.design_mk}",
        "",
        f"export PLATFORM = {plan.platform}",
        f"export DESIGN_NICKNAME = {plan.nick}",
        f"export SDC_FILE = {plan.effective_sdc}",
        f"export VERILOG_FILES = {_mk_list([str(p) for p in plan.sources])}",
    ]
    if plan.incdirs:
        lines.append(f"export VERILOG_INCLUDE_DIRS = {_mk_list([str(p) for p in plan.incdirs])}")
    if defines:
        lines.append(f"export VERILOG_DEFINES = {' '.join(defines)}")
    return "\n".join(lines) + "\n"


def make_args(orfs: Orfs, plan: Plan, *targets: str) -> str:
    """``make`` invocation for ORFS, as a shell-quoted string."""
    argv = ["make", "-C", f"{orfs.root}/flow", f"DESIGN_CONFIG={plan.config_mk}",
            f"WORK_HOME={plan.work_home}", f"FLOW_VARIANT={VARIANT}",
            f"NUM_CORES={orfs.cores}", *targets]
    return shlex.join(argv)


def step_scripts(orfs: Orfs, plan: Plan) -> list[tuple[str, str]]:
    """The bash scripts to run, in order, inside the ORFS environment."""
    env_sh = f"{orfs.root}/env.sh"
    prologue = (f"set -o pipefail; if [ -f {shlex.quote(env_sh)} ]; then "
                f"source {shlex.quote(env_sh)} >/dev/null; fi; ")
    versions = (
        prologue
        + "echo \"openroad: $(openroad -version 2>&1 | head -1)\"; "
        + "echo \"yosys: $(yosys -V 2>&1 | head -1)\"; "
        + f"echo \"orfs: $(git -C {shlex.quote(orfs.root)} describe --always --dirty "
        + "2>/dev/null || echo unknown)\""
    )
    if plan.synth_only:
        return [("versions", versions), ("synth", prologue + make_args(orfs, plan, "synth"))]
    return [
        ("versions", versions),
        ("flow", prologue + make_args(orfs, plan, "finish")),
        ("extra", prologue + make_args(orfs, plan, f"RUN_SCRIPT={EXTRA_TCL}",
                                       "RUN_LOG_NAME_STEM=asic_extra", "run")),
        ("clocks", prologue + make_args(orfs, plan, f"RUN_SCRIPT={CLOCKS_TCL}",
                                        "RUN_LOG_NAME_STEM=asic_clocks", "run")),
    ]


def wrap(orfs: Orfs, plan: Plan, ctx: Context, script: str, name: str) -> list[str]:
    """argv that runs ``script`` with bash, natively or in the pinned image.

    Docker mounts the repository and source tree read-only and the workdir
    read-write at their own paths, so generated absolute paths work as-is."""
    if orfs.runner == "native":
        return ["bash", "-c", script]
    mounts = []
    for path, mode in docker_mounts(ctx):
        mounts += ["-v", f"{path}:{path}:{mode}"]
    return ["docker", "run", "--rm", "--name", name,
            "--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            f"--pids-limit={DOCKER_PIDS}", f"--cpus={orfs.cores}",
            f"--memory={DOCKER_MEMORY}", f"--memory-swap={DOCKER_MEMORY}",
            "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
            *mounts, "-w", str(plan.out), orfs.image, "bash", "-c", script]


def docker_mounts(ctx: Context) -> list[tuple[Path, str]]:
    """Read-only repository and source tree (the latter only when outside the
    repository) and the read-write workdir."""
    mounts = [(ctx.repo_root, "ro")]
    if ctx.src_root != ctx.repo_root and ctx.repo_root not in ctx.src_root.parents:
        mounts.append((ctx.src_root, "ro"))
    mounts.append((ctx.workdir, "rw"))
    return mounts


def redact(text: str, ctx: Context) -> str:
    """Replace host paths with placeholders for printing (CI job logs are
    public): the workdir becomes ``<repo>/workdir`` when it is the repository
    default, else ``<workdir>``; the source root ``<src>``; the repository
    ``<repo>``. Longest path first, so nested paths map correctly."""
    names = {ctx.repo_root: "<repo>", ctx.src_root: "<src>", ctx.workdir: "<workdir>"}
    if ctx.src_root == ctx.repo_root / "src":
        names[ctx.src_root] = "<repo>/src"
    if ctx.workdir == ctx.repo_root / "workdir":
        names[ctx.workdir] = "<repo>/workdir"
    for path in sorted(names, key=lambda p: len(str(p)), reverse=True):
        text = text.replace(str(path), names[path])
    return text


def container_name(plan: Plan) -> str:
    """Unique per run (several runners share the host); steps append a suffix."""
    return f"asic-{plan.nick.replace('_', '-')}-{uuid.uuid4().hex[:12]}"


class Terminated(KeyboardInterrupt):
    """SIGTERM (e.g. a cancelled CI job); handled like Ctrl-C."""


def _on_sigterm(signum, frame):
    raise Terminated()


def write_inputs(plan: Plan, clean: bool) -> None:
    if clean and plan.out.exists():
        shutil.rmtree(plan.out)
    plan.out.mkdir(parents=True, exist_ok=True)
    plan.config_mk.write_text(render_config_mk(plan))
    if plan.study_clock_period_ns is not None:
        # Snapshot the complete design constraints, rather than substituting
        # clock text: the opted-in SDC owns its domain and boundary budgets.
        plan.effective_sdc.write_text(
            "# Generated clock study; the source SDC and default target are unchanged.\n"
            f"set study_clock_period_ns {plan.study_clock_period_ns!r}\n"
            + plan.sdc.read_text() + "\n"
            + """# Reject an opt-in that forgot a clock or ignored the requested period.
if {[llength [all_clocks]] == 0} {
  error "clock study: no clocks defined"
}
foreach study_clk [all_clocks] {
  if {abs([get_property $study_clk period] - $study_clock_period_ns) > 1.0e-9} {
    error "clock study: period mismatch on [get_property $study_clk name]"
  }
}
""")


def _kill(proc: subprocess.Popen, orfs: Orfs, name: str) -> None:
    """Stop a step: remove its container (dockerd owns it, so killing the
    client is not enough), then kill the step's process group."""
    if orfs.runner == "docker":
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def run_step(argv: list[str], log, timeout_s: float, orfs: Orfs, name: str) -> str:
    """Run one step, appending its output to ``log``. Returns pass, fail or
    timeout."""
    proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        rc = proc.wait(timeout=max(timeout_s, 1))
    except subprocess.TimeoutExpired:
        _kill(proc, orfs, name)
        return "timeout"
    except KeyboardInterrupt:
        _kill(proc, orfs, name)
        raise
    return "pass" if rc == 0 else f"fail (exit {rc})"


def parse_versions(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition(": ")
        if sep and key in ("openroad", "yosys", "orfs"):
            out[key] = value.strip()
    return out


def source_revision(repo_root: Path) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


def _section(log: Path, mark: int) -> str:
    """One step's part of ``flow.log``: everything after byte ``mark``."""
    with log.open(errors="replace") as fh:
        fh.seek(mark)
        return fh.read()


def print_failure(ctx: Context, plan: Plan, step: str, text: str) -> None:
    """Every error line of a failed step's output (capped), then its tail,
    with host paths redacted."""
    for line in excerpt(text, ERROR_RE, f"step {step} in {plan.flow_log}"):
        print(redact(line, ctx), flush=True)


def run(ctx: Context, plan: Plan, orfs: Orfs, timeout_s: float) -> tuple[int, dict]:
    """Run every step under one wall-clock budget, then write metrics.json."""
    write_inputs(plan, clean=True)
    name = container_name(plan)
    previous = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        return _run(ctx, plan, orfs, timeout_s, name)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _run(ctx: Context, plan: Plan, orfs: Orfs, timeout_s: float, name: str) -> tuple[int, dict]:
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    status = "pass"
    versions: dict = {}
    with plan.flow_log.open("w") as log:
        for step, script in step_scripts(orfs, plan):
            argv = wrap(orfs, plan, ctx, script, f"{name}-{step}")
            print(redact(f"[asic] {step}: {shlex.join(argv)}", ctx), flush=True)
            log.write(f"### step {step}: {shlex.join(argv)}\n")
            log.flush()
            mark = plan.flow_log.stat().st_size
            budget = deadline - time.monotonic()
            result = run_step(argv, log, budget, orfs, f"{name}-{step}")
            log.flush()
            if step == "versions":
                versions = parse_versions(_section(plan.flow_log, mark))
            if (plan.synth_only or plan.study_clock_period_ns is not None) and result == "pass" and any(
                    ERROR_RE.search(line) for line in _section(plan.flow_log, mark).splitlines()):
                result = "fail (error in log)"
            if result != "pass":
                print_failure(ctx, plan, step, _section(plan.flow_log, mark))
                status = f"{step}: {result if result != 'timeout' else f'timeout after {timeout_s:g}s'}"
                break
    if plan.synth_only:
        results = plan.work_home / "results" / plan.platform / plan.nick / VARIANT
        required = ("1_2_yosys.v", "1_synth.odb", "1_synth.sdc")
        absent = [name for name in required if not (results / name).is_file()
                  or (results / name).is_symlink() or (results / name).stat().st_size == 0]
        if status == "pass" and absent:
            status = f"incomplete: missing synthesis outputs {', '.join(absent)}"
        doc = {
            "schema": 1, "stage": "synthesis", "ip": plan.ip.id,
            "design": plan.design, "platform": plan.platform, "status": status,
            "outputs": [str((results / name).relative_to(ctx.workdir))
                        for name in required if name not in absent],
            "toolchain": {"runner": orfs.runner, "image": orfs.image,
                          "orfs_pin": orfs.commit, **versions},
            "source": source_revision(ctx.repo_root),
            "elapsed_s": round(time.monotonic() - started, 1),
        }
        plan.synthesis_json.write_text(json.dumps(doc, indent=2) + "\n")
        return (0 if status == "pass" else 1), doc
    summary, worst_text = asic_metrics.collect(plan.work_home, plan.platform, plan.nick,
                                               VARIANT)
    plan.worst_path_txt.write_text(worst_text)
    absent = asic_metrics.missing(summary)
    for warning in summary["warnings"]:
        print(f"[asic] warning: {warning}", flush=True)
    if status == "pass" and absent:
        status = f"incomplete: missing {', '.join(absent)}"
    doc = {
        "schema": 1,
        "ip": plan.ip.id,
        "design": plan.design,
        "platform": plan.platform,
        "status": status,
        "clock_period_ns": plan.study_clock_period_ns or plan.clock_period_ns,
        **summary,
        "toolchain": {"runner": orfs.runner, "image": orfs.image, "root": orfs.root,
                      "orfs_pin": orfs.commit, **versions},
        "source": source_revision(ctx.repo_root),
        "elapsed_s": round(time.monotonic() - started, 1),
        # Relative to the workdir: metrics.json is published, host paths are not.
        "work_home": plan.work_home.relative_to(ctx.workdir).as_posix(),
    }
    if plan.study_clock_period_ns is not None:
        doc["study"] = {"kind": "clock-period", "clock_period_ns": plan.study_clock_period_ns,
                        "target_clock_period_ns": plan.clock_period_ns}
    plan.metrics_json.write_text(json.dumps(doc, indent=2) + "\n")
    return (0 if status == "pass" else 1), doc
