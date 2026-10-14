"""``./build ci unit``: run the pytest suites a change needs.

Two kinds of suite, both pure Python (no simulator):

- ``tools``: the tools unit tests (``tools_tests`` in cfg/ci.yaml).
- ``models:<ip>``: the reference-model unit tests in ``<ip>/<models>/``.

Without ``--affected`` every suite runs. With it, ``tools`` runs when a
global path changed, and the model suites run for the IPs that
``regress --affected`` would select.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from build.ip import Context, Ip, discover
from ci.config import CiRules
from regress.affected import changed_files, select_affected


@dataclass(frozen=True)
class Suite:
    name: str
    reason: str
    cwd: Path
    args: tuple[str, ...]


def _has_tests(d: Path) -> bool:
    return d.is_dir() and any(d.glob("test_*.py"))


def plan(ctx: Context, rules: CiRules, affected_base: str | None) -> list[Suite]:
    ips = discover(ctx.src_root)
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
                            ("-q", *rules.tools_tests)))
    for ip in ips:
        d = ip.path / rules.models
        if ip.id in hit and _has_tests(d):
            # Run in the models directory: model modules are imported by bare
            # name, and each IP is its own pytest session so names never mix.
            suites.append(Suite(f"models:{ip.id}", hit[ip.id], d,
                                ("-q", "-p", "no:cacheprovider", "--rootdir", ".", ".")))
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
        code = subprocess.run([sys.executable, "-m", "pytest", *s.args],
                              cwd=s.cwd, env=env).returncode
        print(f"[unit] {s.name}: {'PASS' if code == 0 else f'FAIL (exit {code})'}",
              file=out, flush=True)
        if code != 0:
            failed.append(s.name)
    print(f"[unit] {len(suites) - len(failed)}/{len(suites)} suite(s) passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""), file=out)
    return 1 if failed else 0
