"""``./build`` command-line interface.

Exit codes: 0 success, 1 a test or check failed, 2 usage or configuration
error (including subcommands that are not implemented yet).
"""

from __future__ import annotations

import argparse
import shutil
import sys

from build import sim
from build.filelist import FilelistError
from build.ip import Context, DiscoveryError, discover, find_ip, make_context
from labels import core as labels
from regress import run as regress
from regress.affected import GitError
from regress.schema import LEVELS, SchemaError, load_ip

USER_ERRORS = (FilelistError, DiscoveryError, SchemaError, GitError, sim.SimError,
               labels.LabelsError)


def cmd_sim(ctx: Context, args) -> int:
    ips = discover(ctx.src_root)
    ip = find_ip(ips, args.ip)
    children = {o.path.name for o in ips if o.path.parent == ip.path}
    entries = {e.name: e for e in load_ip(ip, children)}
    if args.test not in entries:
        known = ", ".join(sorted(entries)) or "none"
        raise SchemaError(f"IP {ip.id} has no test {args.test!r} (known: {known})")
    entry = entries[args.test]
    # Default: the test's first regression seed (list order for a seed list).
    seed = args.seed if args.seed is not None else regress.seeds_for(entry, None)[0]
    verilator = sim.find_verilator()
    if verilator is None:
        raise sim.SimError(sim.not_found_message())
    spec = sim.BuildSpec(ip, entry.top, dict(entry.params), args.waves)
    built = sim.build(spec, ctx.workdir, verilator)
    print(f"build {built.status} ({built.runtime_s:.1f}s): {built.log}")
    if built.status != "pass":
        return 1
    res = sim.run_test(spec, ctx.workdir, verilator, entry.name, entry.module,
                       entry.testcase, seed, entry.timeout_s)
    print(f"{ip.id}:{entry.name} seed={seed} {res.status.upper()} "
          f"({res.runtime_s:.1f}s) {res.detail}")
    print(f"log: {res.log}")
    return 0 if res.status == "pass" else 1


def cmd_regress(ctx: Context, args) -> int:
    levels = sorted(set(args.level))
    if not args.dry_run:
        regress.remove_summary(ctx.workdir)
    selection = regress.select(ctx, levels, args.ip, args.affected, args.seed)
    for ip in selection.ips:
        print(f"[select] {ip.id}: {selection.reasons[ip.id]}")
    if not selection.ips:
        print("no IPs selected")
    if args.dry_run:
        for entry, seed in selection.runs:
            print(f"L{entry.level} {entry.ip}:{entry.name} seed={seed}")
        return 0
    if not selection.runs:
        print("no tests selected")
    summary = regress.run(ctx, selection, levels, jobs=args.jobs, waves=args.waves)
    code = regress.report(summary)
    print(f"summary: {regress.summary_path(ctx.workdir)}")
    return code


def cmd_clean(ctx: Context, args) -> int:
    if not args.ip:
        if ctx.workdir.exists():
            shutil.rmtree(ctx.workdir)
            print(f"removed {ctx.workdir}")
        return 0
    ips = discover(ctx.src_root)
    for ip_id in args.ip:
        ip = find_ip(ips, ip_id)
        # Sub-IP output nests under workdir/<ip>/<subip>/; keep it.
        keep = {o.path.name for o in ips if o.path.parent == ip.path}
        out = ip.work_dir(ctx.workdir)
        if not out.is_dir():
            continue
        for entry in sorted(out.iterdir()):
            if entry.name in keep:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        print(f"cleaned {out} (sub-IP output kept)" if keep else f"cleaned {out}")
    return 0


def cmd_labels(ctx: Context, args) -> int:
    standard = labels.load_repo_standard(ctx.cfg_dir, ctx.src_root)
    gh = labels.Gh(labels.resolve_repo(args.repo, ctx.repo_root))
    if args.action == "sync":
        return labels.sync(standard, gh, args.dry_run, args.prune, sys.stdout)
    errors, warnings = labels.check(standard, gh.issue_labels(args.number))
    for w in warnings:
        print(f"warning: #{args.number}: {w}")
    for e in errors:
        print(f"error: #{args.number}: {e}")
    print(f"#{args.number}: labels {'FAIL' if errors else 'OK'}")
    return 1 if errors else 0


def stub(issue: int):
    def run(ctx: Context, args) -> int:
        print(f"{args.command}: not implemented yet (see issue #{issue})", file=sys.stderr)
        return 2
    return run


def _sim_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", required=True, help="IP id, e.g. a or a/b")
    p.add_argument("--test", required=True, help="test name from the IP's level lists")
    p.add_argument("--seed", type=int, help="seed (default: the test's first regress seed)")
    p.add_argument("--waves", action="store_true", help="dump waves into the test directory")


def _regress_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--level", type=int, choices=LEVELS, action="append", required=True,
                   help="level to run; repeat to combine (levels are exclusive)")
    p.add_argument("--ip", action="append", help="restrict to this IP id; repeatable")
    p.add_argument("--affected", metavar="BASE",
                   help="only IPs affected by changes since the merge base with BASE")
    p.add_argument("--jobs", "-j", type=int, default=1, help="parallel builds/tests")
    p.add_argument("--seed", type=int, help="base seed; run i of a test uses seed+i")
    p.add_argument("--waves", action="store_true", help="dump waves for every test")
    p.add_argument("--dry-run", action="store_true", help="print the selection only")


def _clean_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", action="append",
                   help="only this IP's own output (sub-IP output is kept); repeatable")


def _labels_args(p: argparse.ArgumentParser) -> None:
    repo = argparse.ArgumentParser(add_help=False)
    repo.add_argument("--repo", help="OWNER/NAME (default: $GH_REPO, then the origin remote)")
    act = p.add_subparsers(dest="action", required=True, metavar="action")
    s = act.add_parser("sync", parents=[repo], help="make GitHub labels match cfg/labels.yaml")
    s.add_argument("--dry-run", action="store_true", help="print the diff, change nothing")
    s.add_argument("--prune", action="store_true", help="delete labels not in the config")
    c = act.add_parser("check", parents=[repo], help="check the label rules on one issue or PR")
    c.add_argument("number", type=int, help="issue or pull request number")


# The dispatch table: name -> (help, handler, argument builder).
# Later issues replace a stub(...) entry with their implementation.
COMMANDS = {
    "sim": ("run one test of one IP", cmd_sim, _sim_args),
    "regress": ("run regression levels", cmd_regress, _regress_args),
    "clean": ("remove generated output", cmd_clean, _clean_args),
    "check": ("static checks", stub(7), None),
    "labels": ("GitHub label sync and check", cmd_labels, _labels_args),
    "wiki": ("build the wiki site", stub(12), None),
    "fpga": ("FPGA flow", stub(13), None),
    "asic": ("ASIC flow", stub(14), None),
}


def make_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--src-root", help="source tree to discover IPs in "
                        "(default: $BUILD_SRC_ROOT or <repo>/src)")
    common.add_argument("--workdir", help="output directory "
                        "(default: $BUILD_WORKDIR or <repo>/workdir)")
    parser = argparse.ArgumentParser(prog="build", description="Factory build entry point.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, (help_text, handler, add_args) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text, parents=[common])
        if add_args is None:  # stubs accept anything until implemented
            p.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
        else:
            add_args(p)
        p.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if getattr(args, "jobs", 1) < 1:
        print("error: --jobs must be >= 1", file=sys.stderr)
        return 2
    ctx = make_context(args.src_root, args.workdir)
    try:
        return args.handler(ctx, args)
    except USER_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        sim.kill_all()
        print("interrupted", file=sys.stderr)
        return 130
