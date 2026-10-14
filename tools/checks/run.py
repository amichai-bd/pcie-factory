"""Run the selected checks and print a per-check summary."""

from __future__ import annotations

import sys

from build.ip import Context, discover, find_ip
from checks import citation, filelists, layout, lint, neutrality, style, wiki_mirror
from checks.common import CheckResult

# Report order. Lint runs last: it is the slowest and needs Verilator.
CHECKS = ("layout", "filelist", "style", "wiki", "neutrality", "citation", "lint")


def run_checks(ctx: Context, ip_ids: list[str] | None, only: list[str] | None,
               out=None) -> int:
    """Return 0 when every selected check passed, else 1."""
    out = out or sys.stdout
    ips = discover(ctx.src_root)
    ids = None
    if ip_ids:
        ids = {find_ip(ips, i).id for i in ip_ids}
    names = [c for c in CHECKS if not only or c in only]
    print(f"check: {len(ips)} IP(s) under {ctx.src_root}"
          + (f"; --ip {' '.join(sorted(ids))}" if ids else ""), file=out)
    results: list[CheckResult] = []
    for name in names:
        if name == "layout":
            r = layout.run(ctx.src_root, ips, ids)
        elif name == "filelist":
            r = filelists.run(ctx.src_root, ips, ids)
        elif name == "style":
            r = style.run(ctx.src_root, ips, ids)
        elif name == "wiki":
            r = wiki_mirror.run(ctx.src_root, ips, ids)
        elif name == "neutrality":
            r = neutrality.run(ctx.repo_root)
        elif name == "citation":
            r = citation.run(ctx.repo_root, ctx.src_root)
        else:
            r = lint.run(ctx.src_root, ips, ids)
        results.append(r)
        status = "PASS" if r.passed else f"FAIL ({len(r.findings)})"
        print(f"[{r.name}] {status}: {r.checked}", file=out)
        for f in r.findings:
            print(f"  {f}", file=out)
        out.flush()
    failed = [r.name for r in results if not r.passed]
    print(f"check: {'FAIL (' + ', '.join(failed) + ')' if failed else 'PASS'}", file=out)
    return 1 if failed else 0
