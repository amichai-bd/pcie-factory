"""``./build asic``: run OpenROAD-flow-scripts (ORFS) on one IP.

Spec: wiki/tools/asic/SPEC.md. Method: wiki/bkm/pd.md.

Provenance: the ORFS input split, per-step JSON merge and summary idea are
adapted from nand2anything tools/asic/asic.py (commit 085ef4b, same owner, no
license file); rewritten for this repository's IP layout and runners.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from build.filelist import resolve
from build.ip import Context, Ip

from . import metrics as asic_metrics

PLATFORMS = ("sky130hd",)
VARIANT = "base"
DEFAULT_TIMEOUT_S = 3600
EXTRA_TCL = Path(__file__).resolve().parent / "extra_metrics.tcl"

# Variables the tool generates; an IP's <platform>.mk must not set them.
TOOL_OWNED = ("VERILOG_FILES", "VERILOG_INCLUDE_DIRS", "VERILOG_DEFINES",
              "SDC_FILE", "PLATFORM", "DESIGN_NICKNAME", "WORK_HOME", "FLOW_VARIANT")
RUNNERS = ("docker", "native")

ENV_RUNNER = "ORFS_RUNNER"
ENV_IMAGE = "ORFS_IMAGE"
ENV_ROOT = "ORFS_ROOT"
# ORFS checkout inside the official openroad/orfs images.
IMAGE_ROOT = "/OpenROAD-flow-scripts"
# Container memory cap (swap included), as on the host's verified invocation.
DOCKER_MEMORY = "3g"

_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\?|:|::|\+)?=\s*(.*?)\s*$")


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
    """ORFS settings: environment (ORFS_RUNNER, ORFS_IMAGE, ORFS_ROOT), then
    the host's ``orfs_image``/``orfs_root`` in cfg/host.yaml. An image selects
    the docker runner, a root the native runner; the environment wins."""
    path = cfg_dir / "host.yaml"
    host = host_orfs(cfg_dir)
    image = env.get(ENV_IMAGE) or host.get("orfs_image") or None
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
        raise AsicError(f"no ORFS configured: set ${ENV_IMAGE} or ${ENV_ROOT}, or a host's "
                        f"orfs_image/orfs_root in {path}"
                        + (f" (${ENV_RUNNER}={runner!r} is not one of "
                           f"{', '.join(RUNNERS)})" if runner else ""))
    if runner == "docker":
        if not image:
            raise AsicError(f"docker runner needs an image: set ${ENV_IMAGE} or orfs_image in {path}")
        root = IMAGE_ROOT
        recorded = image == host.get("orfs_image")
    else:
        if not native_root:
            raise AsicError(f"native runner needs the ORFS root: set ${ENV_ROOT} or orfs_root in {path}")
        root = native_root
        recorded = root == host.get("orfs_root")
    source = "env" if any(env.get(k) for k in (ENV_RUNNER, ENV_IMAGE, ENV_ROOT)) else "cfg"
    commit = host.get("orfs_commit") if recorded else None
    return Orfs(runner, str(root), image, source, str(commit) if commit else None)


def read_design_mk(path: Path) -> dict[str, str]:
    """Plain ``NAME = value`` assignments of a design .mk (line continuations
    joined). Used to validate it, not to evaluate make."""
    text = re.sub(r"\\\n", " ", path.read_text())
    out = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
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
    def flow_log(self) -> Path:
        return self.out / "flow.log"


def find_inputs(ip: Ip, platform: str) -> tuple[Path, Path]:
    asic = ip.path / "asic"
    mk = asic / f"{platform}.mk"
    if not mk.is_file():
        raise AsicError(f"IP {ip.id} has no ASIC config {mk} (an IP is an ASIC target "
                        "only with asic/<platform>.mk and one asic/*.sdc)")
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


def make_plan(ctx: Context, ip: Ip, platform: str) -> Plan:
    if platform not in PLATFORMS:
        raise AsicError(f"unknown platform {platform!r} (known: {', '.join(PLATFORMS)})")
    mk, sdc = find_inputs(ip, platform)
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
    return Plan(
        ip=ip, platform=platform, design=design, nick=ip.id.replace("/", "_"),
        out=ip.work_dir(ctx.workdir) / "asic", design_mk=mk, sdc=sdc,
        sources=resolved.sources, incdirs=resolved.incdirs, defines=resolved.defines,
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
        f"include {plan.design_mk}",
        "",
        f"export PLATFORM = {plan.platform}",
        f"export DESIGN_NICKNAME = {plan.nick}",
        f"export SDC_FILE = {plan.sdc}",
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
            f"WORK_HOME={plan.work_home}", f"FLOW_VARIANT={VARIANT}", *targets]
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
    return [
        ("versions", versions),
        ("flow", prologue + make_args(orfs, plan, "finish")),
        ("extra", prologue + make_args(orfs, plan, f"RUN_SCRIPT={EXTRA_TCL}",
                                       "RUN_LOG_NAME_STEM=asic_extra", "run")),
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


def container_name(plan: Plan) -> str:
    return f"asic-{plan.nick}-{os.getpid()}"


def write_inputs(plan: Plan, clean: bool) -> None:
    if clean and plan.out.exists():
        shutil.rmtree(plan.out)
    plan.out.mkdir(parents=True, exist_ok=True)
    plan.config_mk.write_text(render_config_mk(plan))


def _kill(proc: subprocess.Popen, orfs: Orfs, name: str) -> None:
    if orfs.runner == "docker":
        subprocess.run(["docker", "kill", name], capture_output=True)
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


def run(ctx: Context, plan: Plan, orfs: Orfs, timeout_s: float) -> tuple[int, dict]:
    """Run every step under one wall-clock budget, then write metrics.json."""
    write_inputs(plan, clean=True)
    name = container_name(plan)
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    status = "pass"
    versions: dict = {}
    with plan.flow_log.open("w") as log:
        for step, script in step_scripts(orfs, plan):
            argv = wrap(orfs, plan, ctx, script, f"{name}-{step}")
            print(f"[asic] {step}: {shlex.join(argv)}", flush=True)
            log.write(f"### step {step}: {shlex.join(argv)}\n")
            log.flush()
            mark = plan.flow_log.stat().st_size
            result = run_step(argv, log, deadline - time.monotonic(), orfs, f"{name}-{step}")
            log.flush()
            if step == "versions":
                with plan.flow_log.open() as fh:
                    fh.seek(mark)
                    versions = parse_versions(fh.read())
            if result != "pass":
                status = f"{step}: {result if result != 'timeout' else f'timeout after {timeout_s:g}s'}"
                break
    summary = asic_metrics.collect(plan.work_home, plan.platform, plan.nick, VARIANT)
    absent = asic_metrics.missing(summary)
    if status == "pass" and absent:
        status = f"incomplete: missing {', '.join(absent)}"
    doc = {
        "schema": 1,
        "ip": plan.ip.id,
        "design": plan.design,
        "platform": plan.platform,
        "status": status,
        "clock_period_ns": plan.clock_period_ns,
        **summary,
        "toolchain": {"runner": orfs.runner, "image": orfs.image, "root": orfs.root,
                      "orfs_pin": orfs.commit, **versions},
        "source": source_revision(ctx.repo_root),
        "elapsed_s": round(time.monotonic() - started, 1),
        "work_home": str(plan.work_home),
    }
    plan.metrics_json.write_text(json.dumps(doc, indent=2) + "\n")
    return (0 if status == "pass" else 1), doc
