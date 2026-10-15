"""``./build ci unit``: run the pytest suites a change needs.

Four kinds of suite, all pure Python (no simulator):

- ``tools``: the tools unit tests (``tools_tests`` in cfg/ci.yaml).
- ``models:<ip>``: the reference-model unit tests in ``dv/<ip>/<models>/``
  (also for the shared unit, ``models:common``).
- ``arch:<ip>``: the architecture-model self-tests in ``arch/<ip>/``.
- ``perf:<ip>``: ``./build arch --ip <ip>``, the IP's perf scenarios.

Without ``--affected`` every suite runs. With it, ``tools`` runs when a
global path changed, and the per-IP suites run for the IPs that
``regress --affected`` would select.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from arch.perf import ARCH_DIR, PERF_DIR
from build.ip import REPO_ROOT, Context, Ip, units
from ci.config import CiRules
from regress.affected import changed_files, select_affected


@dataclass(frozen=True)
class Suite:
    name: str
    reason: str
    cwd: Path
    args: tuple[str, ...]  # after the Python interpreter


_PYTEST_HERE = ("-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", ".", ".")


def _has_tests(d: Path) -> bool:
    return d.is_dir() and any(d.glob("test_*.py"))


def plan(ctx: Context, rules: CiRules, affected_base: str | None) -> list[Suite]:
    ips = units(ctx.src_root)  # the shared unit's models run like an IP's
    if affected_base is None:
        run_tools = "all"
        hit = {ip.id: "all" for ip in ips}
    else:
        repo, changed = changed_files(affected_base, ctx.src_root)
        globals_ = [rel for rel in changed if rules.is_global(rel)]
        run_tools = f"global change: {globals_[0]}" if globals_ else None
        hit = select_affected(ips, repo, changed, ctx.src_root, rules)
    suites = []
    if run_tools:
        suites.append(Suite("tools", run_tools, ctx.repo_root,
                            ("-m", "pytest", "-q", *rules.tools_tests)))
    for ip in ips:
        if ip.id not in hit:
            continue
        # Run in the models or arch directory: modules there are imported by
        # bare name, and each is its own pytest session so names never mix.
        for kind, d in (("models", ip.dv_dir / rules.models), ("arch", ip.dir(ARCH_DIR))):
            if _has_tests(d):
                suites.append(Suite(f"{kind}:{ip.id}", hit[ip.id], d, _PYTEST_HERE))
        if (ip.dir(ARCH_DIR) / PERF_DIR).is_dir():
            suites.append(Suite(f"perf:{ip.id}", hit[ip.id], ctx.repo_root, (
                str(REPO_ROOT / "build"), "arch", "--ip", ip.id,
                "--src-root", str(ctx.src_root), "--workdir", str(ctx.workdir))))
    return suites


def run(suites: list[Suite], out=sys.stdout) -> int:
    """Run every suite (even after a failure); return 0 only if all passed."""
    if not suites:
        print("[unit] no suites selected", file=out)
        return 0
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    failed = []
    for s in suites:
        print(f"[unit] {s.name}: {s.reason}", file=out, flush=True)
        code = subprocess.run([sys.executable, *s.args],
                              cwd=s.cwd, env=env).returncode
        print(f"[unit] {s.name}: {'PASS' if code == 0 else f'FAIL (exit {code})'}",
              file=out, flush=True)
        if code != 0:
            failed.append(s.name)
    print(f"[unit] {len(suites) - len(failed)}/{len(suites)} suite(s) passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""), file=out)
    return 1 if failed else 0
