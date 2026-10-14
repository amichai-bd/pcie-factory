"""Flow-input check: the FPGA and ASIC inputs pass their flows' validation.

Spec: wiki/tools/checks/SPEC.md#flow-input-check. Every IP with an
``fpga/<id>/`` folder is planned as ``./build fpga --dry-run`` plans it, and
every IP with an ``asic/<id>/`` folder as ``./build asic --dry-run`` does; the
cfg settings those flows read are loaded the same way. Nothing is written and
no vendor tool runs.
"""

from __future__ import annotations

from pathlib import Path

from asic import flow as asic
from build.filelist import FilelistError
from build.ip import Context, Ip
from checks.common import CheckResult
from fpga import flow as fpga

ERRORS = (fpga.FpgaError, asic.AsicError, FilelistError, OSError)


def _short(text: str, roots: list[Path]) -> str:
    """``text`` with absolute ``roots`` (and their resolved forms) shown
    relative, so findings read like the other checks'."""
    for root in roots:
        for form in dict.fromkeys((str(root), str(root.resolve()))):
            text = text.replace(form + "/", "")
    return text


def fpga_findings(ips: list[Ip], cfg_dir: Path, workdir: Path, short) -> list[str]:
    """``./build fpga --dry-run`` validation: cfg/fpga.yaml, then each
    target's plan and generated Tcl (quoting), without writing it."""
    if not ips:
        return []
    try:
        cfg = fpga.load_config(cfg_dir)
    except fpga.FpgaError as exc:
        return [short(str(exc))]
    out = []
    for ip in ips:
        try:
            p = fpga.plan(ip, workdir, cfg["board"], None, cfg["processors"])
            fpga.project_tcl(p)
            fpga.worst_path_tcl(p)
        except ERRORS as exc:
            out.append(short(str(exc)))
    return out


def asic_platforms(ip: Ip) -> list[str]:
    """Platforms the IP has a ``<platform>.mk`` for; the default platform
    when it has none, so that the flow reports the missing config."""
    found = [p for p in asic.PLATFORMS if (ip.dir("asic") / f"{p}.mk").is_file()]
    return found or [asic.PLATFORMS[0]]


def asic_findings(ips: list[Ip], ctx: Context, short) -> list[str]:
    """``./build asic --dry-run`` validation: each target's plan on every
    platform it configures, then the ORFS settings with the environment
    overrides ignored (so the cfg image must be digest-pinned)."""
    out = []
    for ip in ips:
        for platform in asic_platforms(ip):
            try:
                asic.render_config_mk(asic.make_plan(ctx, ip, platform))
            except ERRORS as exc:
                out.append(short(str(exc)))
    if ips:
        try:
            asic.load_orfs(ctx.cfg_dir, {})
        except asic.AsicError as exc:
            out.append(short(str(exc)))
    return out


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None,
        repo_root: Path, workdir: Path) -> CheckResult:
    src_root = Path(src_root).resolve()
    ctx = Context(Path(repo_root), src_root, Path(workdir))
    chosen = [ip for ip in ips if ip_ids is None or ip.id in ip_ids]
    fpga_ips = [ip for ip in chosen if ip.dir("fpga").is_dir()]
    asic_ips = [ip for ip in chosen if ip.dir("asic").is_dir()]

    def short(text: str) -> str:
        return _short(text, [src_root, ctx.repo_root])

    result = CheckResult("flows")
    result.findings += fpga_findings(fpga_ips, ctx.cfg_dir, ctx.workdir, short)
    result.findings += asic_findings(asic_ips, ctx, short)
    cfgs = (["fpga.yaml"] if fpga_ips else []) + (["host.yaml ORFS"] if asic_ips else [])
    result.checked = (f"{len(fpga_ips)} FPGA and {len(asic_ips)} ASIC target(s) "
                      "with their flows' dry-run validation"
                      + (f"; cfg/{', cfg/'.join(cfgs)}" if cfgs else ""))
    return result
