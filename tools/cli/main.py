"""``./build`` command-line interface.

Exit codes: 0 success, 1 a test or check failed, 2 usage or configuration
error (including subcommands that are not implemented yet).
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from arch import cli as arch_cli
from arch.perf import ArchError
from asic import cli as asic_cli
from build import sim
from build.filelist import FilelistError
from build.ip import Context, DiscoveryError, discover, find_ip, make_context
from checks.common import CheckConfigError
from ci import status as ci_status
from ci import summary as ci_summary
from ci import unit as ci_unit
from ci.config import CiConfigError, load_rules
from checks.run import CHECKS, run_checks
from fpga import flow as fpga
from labels import core as labels
from metrics import cli as metrics
from regress import run as regress
from regress.affected import GitError
from regress.schema import LEVELS, SchemaError, load_ip

WIKI_SITE = Path(__file__).resolve().parents[1] / "wiki_site"
USER_ERRORS = (FilelistError, DiscoveryError, SchemaError, GitError, sim.SimError,
               fpga.FpgaError, labels.LabelsError, CheckConfigError, CiConfigError,
               ArchError)


def cmd_sim(ctx: Context, args) -> int:
    ips = discover(ctx.src_root)
    ip = find_ip(ips, args.ip)
    entries = {e.name: e for e in load_ip(ip)}
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
        out = find_ip(ips, ip_id).work_dir(ctx.workdir)
        if out.is_dir():
            shutil.rmtree(out)
            print(f"cleaned {out}")
    return 0


def cmd_check(ctx: Context, args) -> int:
    return run_checks(ctx, args.ip, args.only)


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


def cmd_ci(ctx: Context, args) -> int:
    if args.action == "summary":
        print(ci_summary.render(regress.summary_path(ctx.workdir)), end="")
        return 0
    if args.action == "status":
        return ci_status.run(ctx, args)
    rules = load_rules(ctx.cfg_dir)
    return ci_unit.run(ci_unit.plan(ctx, rules, args.affected))


def cmd_wiki(ctx: Context, args) -> int:
    # Imported here so that other commands do not need the generator's
    # dependencies (markdown-it-py, Pygments) installed.
    if str(WIKI_SITE) not in sys.path:
        sys.path.insert(0, str(WIKI_SITE))
    try:
        import wiki_site
    except ImportError as exc:
        print(f"error: wiki: {exc} (run: pip install -r tools/requirements.txt)", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else ctx.workdir / "wiki-site"
    argv = [args.action, "--out", str(out)]
    if args.action == "build":
        argv += ["--strict"] * args.strict
        argv += ["--sha", args.sha] if args.sha else []
        argv += ["--repo-url", args.repo_url] if args.repo_url else []
    else:
        argv += ["--port", str(args.port)]
    return wiki_site.main(argv)


def cmd_fpga(ctx: Context, args) -> int:
    cfg = fpga.load_config(ctx.cfg_dir)
    ips = discover(ctx.src_root)
    if args.all:
        targets = fpga.fpga_ips(ips)
        if not targets:
            raise fpga.FpgaError("no IP has an fpga/<ip>/ folder")
    else:
        targets = [find_ip(ips, ip_id) for ip_id in args.ip]
    timeout = args.timeout if args.timeout is not None else cfg["step_timeout_s"]
    plans = [fpga.plan(ip, ctx.workdir, cfg["board"], args.part, cfg["processors"]) for ip in targets]
    if args.dry_run:
        for p in plans:
            fpga.generate(p, clean=False)
            print(f"[{p.ip.id}] top {p.top}, board {p.board}, project {p.out_dir}")
            print(f"[lint] {' '.join(fpga.lint_command(p, 'verilator'))}")
            for name, cmd in p.steps:
                print(f"[{name}] {' '.join(cmd)}")
        return 0
    bin_dir = fpga.find_quartus(ctx.cfg_dir)
    if bin_dir is None:
        raise fpga.FpgaError(fpga.quartus_not_found(ctx.cfg_dir))
    print(f"quartus: {bin_dir}")
    verilator = sim.find_verilator()
    code = 0
    for p in plans:
        print(f"[{p.ip.id}] top {p.top}, board {p.board}, project {p.out_dir}")
        code = max(code, fpga.run(p, bin_dir, timeout, verilator))
    return code


def stub(issue: int):
    def run(ctx: Context, args) -> int:
        print(f"{args.command}: not implemented yet (see issue #{issue})", file=sys.stderr)
        return 2
    return run


def _sim_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", required=True, help="IP id, e.g. a or a_b")
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
    p.add_argument("--ip", action="append", help="only this IP's output; repeatable")


def _check_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", action="append",
                   help="restrict layout, filelist, style, wiki and lint to this IP id; repeatable")
    p.add_argument("--only", action="append", choices=CHECKS,
                   help="run only this check; repeatable (default: all)")


def _fpga_args(p: argparse.ArgumentParser) -> None:
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--ip", action="append", help="IP id with an fpga/<ip>/ folder; repeatable")
    which.add_argument("--all", action="store_true", help="every IP that has an fpga/<ip>/ folder")
    p.add_argument("--part", help="override the device set by the board settings file")
    p.add_argument("--timeout", type=float, help="seconds per step (default: cfg/fpga.yaml)")
    p.add_argument("--dry-run", action="store_true",
                   help="generate the project scripts and print the commands only")


def _labels_args(p: argparse.ArgumentParser) -> None:
    repo = argparse.ArgumentParser(add_help=False)
    repo.add_argument("--repo", help="OWNER/NAME (default: $GH_REPO, then the origin remote)")
    act = p.add_subparsers(dest="action", required=True, metavar="action")
    s = act.add_parser("sync", parents=[repo], help="make GitHub labels match cfg/labels.yaml")
    s.add_argument("--dry-run", action="store_true", help="print the diff, change nothing")
    s.add_argument("--prune", action="store_true", help="delete labels not in the config")
    c = act.add_parser("check", parents=[repo], help="check the label rules on one issue or PR")
    c.add_argument("number", type=int, help="issue or pull request number")


def _ci_args(p: argparse.ArgumentParser) -> None:
    act = p.add_subparsers(dest="action", required=True, metavar="action")
    u = act.add_parser("unit", help="run the tools and model unit tests a change needs")
    u.add_argument("--affected", metavar="BASE",
                   help="only the suites affected since the merge base with BASE")
    act.add_parser("summary", help="print the regress summary as Markdown")
    st = act.add_parser("status", help="post the ci-gate commit status on a nightly data branch")
    st.add_argument("--result", required=True, help="the ci-gate job result: "
                    + ", ".join(ci_status.STATES))
    st.add_argument("--sha", required=True, help="full commit SHA the job tested")
    st.add_argument("--ref", required=True, help="branch the job ran on (must be a data branch)")
    st.add_argument("--target-url", help="link shown with the status (the run)")
    st.add_argument("--repo", help="OWNER/NAME (default: $GH_REPO, $GITHUB_REPOSITORY, origin)")
    st.add_argument("--dry-run", action="store_true", help="print the status, post nothing")


def _wiki_args(p: argparse.ArgumentParser) -> None:
    actions = p.add_subparsers(dest="action", required=True, metavar="action")
    b = actions.add_parser("build", help="build the site (replaces --out)")
    b.add_argument("--strict", action="store_true", help="fail on any broken link or anchor")
    b.add_argument("--out", help="output directory (default: <workdir>/wiki-site)")
    b.add_argument("--sha", help="commit for links outside wiki/ (default: git rev-parse HEAD)")
    b.add_argument("--repo-url", help="repository URL for links outside wiki/ "
                   "(default: the generator's REPO_URL)")
    s = actions.add_parser("serve", help="serve an existing build on 127.0.0.1")
    s.add_argument("--out", help="built site directory (default: <workdir>/wiki-site)")
    s.add_argument("--port", type=int, default=8000, help="port (default: 8000)")


# The dispatch table: name -> (help, handler, argument builder).
# Later issues replace a stub(...) entry with their implementation.
COMMANDS = {
    "sim": ("run one test of one IP", cmd_sim, _sim_args),
    "regress": ("run regression levels", cmd_regress, _regress_args),
    "clean": ("remove generated output", cmd_clean, _clean_args),
    "check": ("static checks: layout, filelist, style, wiki mirror, neutrality, lint", cmd_check, _check_args),
    "labels": ("GitHub label sync and check", cmd_labels, _labels_args),
    "ci": ("ci-gate helpers: unit suites, job summary, data-branch status", cmd_ci, _ci_args),
    "wiki": ("build or serve the wiki site", cmd_wiki, _wiki_args),
    "metrics": ("nightly metrics, data guard and red issue", metrics.run, metrics.add_args),
    "fpga": ("FPGA flow: Quartus compile and metrics", cmd_fpga, _fpga_args),
    "asic": ("ASIC flow (ORFS)", asic_cli.run, asic_cli.add_args),
    "arch": ("architecture-model perf scenarios", arch_cli.run, arch_cli.add_args),
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
