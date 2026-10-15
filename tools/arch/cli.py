"""``./build arch`` arguments and handler (wiki/tools/arch/SPEC.md)."""

from __future__ import annotations

import argparse

from build.ip import Context, discover, find_ip

from . import perf


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", required=True, help="top-level IP id with a src/arch/<ip>/ folder")
    p.add_argument("--scenario", help="run only this perf scenario (perf/<NAME>.yaml)")


def run(ctx: Context, args) -> int:
    ip = find_ip(discover(ctx.src_root), args.ip)
    return perf.run(ip, ctx.workdir, args.scenario)
