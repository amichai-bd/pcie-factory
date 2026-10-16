"""Required vendor compatibility checks; synthesis selection, never DV selection.

Spec: wiki/tools/ci/SPEC.md. Outputs remain owned by the individual flows.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

from build.filelist import FilelistError, resolve
from build.ip import Context, Ip, discover
from ci.config import CiRules
from regress.affected import changed_files

KINDS = ("fpga", "asic")


def select(ctx: Context, rules: CiRules, kind: str, affected_base: str | None) -> list[tuple[Ip, str]]:
    targets = [ip for ip in discover(ctx.src_root) if ip.dir(kind).is_dir()]
    if affected_base is None:
        return [(ip, "all targets") for ip in targets]
    repo, changed = changed_files(affected_base, ctx.repo_root)
    global_paths = [p for p in changed if rules.is_global(p) or p in rules.vendor_files]
    if global_paths:
        return [(ip, f"global change: {global_paths[0]}") for ip in targets]
    selected = []
    for ip in targets:
        try:
            rtl = resolve(ip.rtl_f)
        except FilelistError:
            # The real flow reports the broken closure instead of silently skipping it.
            selected.append((ip, "RTL filelist does not resolve"))
            continue
        for rel in changed:
            path = (repo / rel).resolve()
            if rtl.reaches(path) or ip.dir(kind) == path or ip.dir(kind) in path.parents:
                selected.append((ip, f"changed synthesis input: {rel}"))
                break
    return selected


def run_child(command: list[str], cwd: Path) -> int:
    """Forward cancellation to the flow so its own container cleanup runs."""
    proc = subprocess.Popen(command, cwd=cwd)
    cancelled = False

    def forward(signum, frame):
        nonlocal cancelled
        cancelled = True
        proc.send_signal(signum)

    previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        code = proc.wait()
        if cancelled:
            raise KeyboardInterrupt  # CLI returns 130; never start another target.
        return code
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run(ctx: Context, rules: CiRules, kind: str, affected_base: str | None,
        dry_run: bool = False) -> int:
    selected = select(ctx, rules, kind, affected_base)
    if not selected:
        print(f"[vendor] {kind}: no targets selected", flush=True)
    failed = False
    results = []
    summary = ctx.workdir / "ci" / f"vendor-{kind}.json"
    if not dry_run:
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.unlink(missing_ok=True)
    for ip, reason in selected:
        print(f"[vendor] {kind} {ip.id}: {reason}", flush=True)
        if dry_run:
            continue
        command = [sys.executable, str(ctx.repo_root / "build"), kind, "--ip", ip.id,
                   "--src-root", str(ctx.src_root), "--workdir", str(ctx.workdir)]
        if kind == "asic":
            command.append("--synth-only")
        started = time.monotonic()
        code = run_child(command, ctx.repo_root)
        failed |= code != 0
        results.append({"ip": ip.id, "status": "pass" if code == 0 else "fail",
                        "elapsed_s": round(time.monotonic() - started, 1)})
    if not dry_run:
        summary.write_text(json.dumps({"schema": 1, "kind": kind,
                                      "stage": "implementation" if kind == "fpga" else "synthesis",
                                      "targets": results}, indent=2) + "\n")
    return int(failed)
