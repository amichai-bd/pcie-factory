"""``./build asic`` arguments and handler (wiki/tools/asic/SPEC.md)."""

from __future__ import annotations

import argparse
import os
import shlex
import sys

from build.ip import Context, discover, find_ip

from . import flow


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", required=True, help="IP id with an asic/ directory, e.g. a or a/b")
    p.add_argument("--platform", default=flow.PLATFORMS[0], choices=flow.PLATFORMS,
                   help=f"ORFS platform (default: {flow.PLATFORMS[0]})")
    p.add_argument("--timeout", type=float, default=flow.DEFAULT_TIMEOUT_S,
                   help=f"wall-clock budget in seconds for the whole flow "
                        f"(default: {flow.DEFAULT_TIMEOUT_S})")
    p.add_argument("--dry-run", action="store_true",
                   help="write the ORFS config and print the commands; run nothing")


def run(ctx: Context, args) -> int:
    try:
        ip = find_ip(discover(ctx.src_root), args.ip)
        plan = flow.make_plan(ctx, ip, args.platform)
        orfs = flow.load_orfs(ctx.cfg_dir, dict(os.environ))
    except flow.AsicError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("error: --timeout must be > 0", file=sys.stderr)
        return 2
    print(f"[asic] {ip.id}: {plan.design} on {plan.platform}, "
          f"runner {orfs.runner} ({orfs.image or orfs.root}, from {orfs.source})")
    if args.dry_run:
        flow.write_inputs(plan, clean=False)
        print(f"[asic] wrote {plan.config_mk}")
        name = flow.container_name(plan)
        for step, script in flow.step_scripts(orfs, plan):
            print(f"[asic] {step}: {shlex.join(flow.wrap(orfs, plan, ctx, script, f'{name}-{step}'))}")
        print(f"[asic] metrics: {plan.metrics_json}")
        return 0
    code, doc = flow.run(ctx, plan, orfs, args.timeout)
    t, a = doc["timing"], doc["area"]
    print(f"[asic] {doc['status']}: WNS {t['wns_ns']} ns, TNS {t['tns_ns']} ns, "
          f"area {a['stdcell_um2']} um2, cells {doc['cells']}, util {doc['utilization']}, "
          f"max fanout {doc['max_fanout']['value']}, logic depth {doc['logic_depth']['value']}, "
          f"worst path {doc['worst_path'].get('from')} -> {doc['worst_path'].get('to')}")
    print(f"[asic] metrics: {plan.metrics_json}")
    print(f"[asic] log: {plan.flow_log}")
    return code
