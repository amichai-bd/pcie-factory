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
from build.backends import BackendError
from build.filelist import FilelistError
from build.ip import Context, DiscoveryError, discover, find_ip, make_context, units
from checks.common import CheckConfigError
from ci import status as ci_status
from ci import summary as ci_summary
from ci import unit as ci_unit
from ci import vendor as ci_vendor
from ci import gate as ci_gate
from ci.config import CiConfigError, load_rules
from checks.run import CHECKS, run_checks
from fpga import flow as fpga
from labels import core as labels
from metrics import cli as metrics
from pr import check as pr_check
from build import cache as build_cache
from build import slots as host_slots
from regress import run as regress
from regress.affected import GitError
from regress.coverage import CoverageError
from regress.schema import LEVELS, SchemaError, load_ip, load_rotation
from specs import cli as specs_cli
from specs.core import SpecsError

WIKI_SITE = Path(__file__).resolve().parents[1] / "wiki_site"
USER_ERRORS = (FilelistError, DiscoveryError, SchemaError, GitError, sim.SimError, CoverageError,
               fpga.FpgaError, labels.LabelsError, CheckConfigError, CiConfigError,
               ArchError, SpecsError, pr_check.PrError, BackendError,
               host_slots.SlotError, build_cache.CacheError)


def _use_slots(args) -> None:
    """Hold a host CPU slot per build and test (`--slots DIR`, else
    $BUILD_SLOTS); wiki/tools/build/SPEC.md, "CPU slots"."""
    pool = host_slots.from_option(args.slots)
    sim.set_slots(pool)
    if pool is not None:
        print(f"slots: {pool.directory} ({pool.count} CPU slots shared by every run "
              "that names it)", flush=True)


def _use_cache(ctx: Context, args) -> None:
    """Reuse builds from the host's shared build cache (`--no-cache` or
    $BUILD_CACHE=off: none); wiki/tools/build/SPEC.md, "Build cache"."""
    cache = build_cache.from_option(ctx.repo_root, args.no_cache)
    sim.set_cache(cache)
    if cache is None:
        print("build cache: off", flush=True)
    else:
        print(f"build cache: {build_cache.show(cache.root)}", flush=True)


def cmd_sim(ctx: Context, args) -> int:
    ips = units(ctx.src_root)
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
    _use_slots(args)
    _use_cache(ctx, args)
    spec = sim.BuildSpec(ip, entry.top, dict(entry.params), args.waves, backend=entry.backend)
    built = sim.build(spec, ctx.workdir, verilator)
    print(f"build {built.status}{' (cached)' if built.cached else ''} "
          f"({built.runtime_s:.1f}s): {built.log}")
    if built.status != "pass":
        return 1
    res = sim.run_test(spec, ctx.workdir, verilator, entry.name, entry.module,
                       entry.testcase, seed, entry.timeout_s, built)
    print(f"{ip.id}:{entry.name} seed={seed} {res.status.upper()} "
          f"({res.runtime_s:.1f}s) {res.detail}")
    print(f"log: {res.log}")
    return 0 if res.status == "pass" else 1


def cmd_regress(ctx: Context, args) -> int:
    levels = sorted(set(args.level))
    if not args.dry_run:
        regress.remove_summary(ctx.workdir)
    if args.extra_seeds is not None and args.rotate is None:
        raise SchemaError("--extra-seeds needs --rotate KEY")
    rotation = None if args.rotate is None else load_rotation(ctx.cfg_dir, args.rotate,
                                                              args.extra_seeds)
    selection = regress.select(ctx, levels, args.ip, args.affected, args.seed, rotation)
    for ip in selection.ips:
        print(f"[select] {ip.id}: {selection.reasons[ip.id]}")
    if not selection.ips:
        print("no IPs selected")
    if rotation is not None:
        print(f"[rotate] key {rotation.key}: {rotation.extra_seeds} extra seed(s) per "
              f"level{rotation.level} entry, {len(selection.extra)} run(s), plain builds, "
              f"budget {rotation.budget_s:.0f}s of CPU")
    if args.dry_run:
        for entry, seed in selection.runs:
            print(f"L{entry.level} {entry.ip}:{entry.name} seed={seed}")
        for entry, seed in selection.extra:
            print(f"L{entry.level} {entry.ip}:{entry.name} seed={seed} (extra)")
        return 0
    _use_slots(args)
    _use_cache(ctx, args)
    if not selection.runs:
        print("no tests selected")
    summary = regress.run(ctx, selection, levels, jobs=args.jobs, waves=args.waves,
                          coverage=args.coverage)
    code = regress.report(summary)
    print(f"summary: {regress.summary_path(ctx.workdir)}")
    return code


def cmd_clean(ctx: Context, args) -> int:
    if not args.ip:
        if ctx.workdir.exists():
            shutil.rmtree(ctx.workdir)
            print(f"removed {ctx.workdir}")
        return 0
    ips = units(ctx.src_root)
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


def cmd_pr(ctx: Context, args) -> int:
    rules = pr_check.load_rules(ctx.cfg_dir)
    standard = labels.load_repo_standard(ctx.cfg_dir, ctx.src_root)
    if args.body_file:
        if args.head is None:
            raise pr_check.PrError("--body-file needs --head SHA")
        pr = pr_check.from_file(args.number, Path(args.body_file), args.head, args.files,
                                None if args.labels is None else
                                [x for x in args.labels.split(",") if x],
                                None if args.diff is None else Path(args.diff))
    else:
        if args.head or args.files is not None or args.labels is not None or args.diff:
            raise pr_check.PrError("--head, --files, --labels and --diff need --body-file")
        gh = labels.Gh(labels.resolve_repo(args.repo, ctx.repo_root))
        pr = pr_check.fetch(gh, args.number)
    neutral = pr_check.load_neutral(ctx.repo_root, ctx.src_root)
    findings, notes = pr_check.check(pr, rules, standard, neutral)
    return pr_check.report(pr, findings, notes, sys.stdout)


def cmd_ci(ctx: Context, args) -> int:
    if args.action == "summary":
        print(ci_summary.render(regress.summary_path(ctx.workdir)), end="")
        return 0
    if args.action == "status":
        return ci_status.run(ctx, args)
    if args.action == "gate":
        return ci_gate.run({"tests": args.tests, "fpga": args.fpga})
    rules = load_rules(ctx.cfg_dir)
    if args.action == "vendor":
        return ci_vendor.run(ctx, rules, args.kind, args.affected, args.dry_run)
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
    plans = [fpga.plan(ip, ctx.workdir, cfg["board"], args.part, cfg["processors"],
                       cfg["backends"], args.synth_only) for ip in targets]
    roots = fpga.log_roots(ctx.workdir)
    if args.dry_run:
        for p in plans:
            fpga.generate(p, clean=False)
            print(fpga.public(f"[{p.ip.id}] top {p.top}, board {p.board}, project {p.out_dir}",
                              roots))
            for old, new in p.swapped:
                print(f"[backend] {old.name} -> {new.name}")
            print(fpga.public(f"[lint] {' '.join(fpga.lint_command(p, 'verilator'))}", roots))
            for name, cmd in p.steps:
                print(f"[{name}] {' '.join(cmd)}")
        return 0
    found = fpga.find_quartus()
    if found is None:
        raise fpga.FpgaError(fpga.quartus_not_found())
    bin_dir, where = found
    print(f"quartus: {where}")
    verilator = sim.find_verilator()
    code = 0
    for p in plans:
        print(fpga.public(f"[{p.ip.id}] top {p.top}, board {p.board}, project {p.out_dir}",
                          roots))
        code = max(code, fpga.run(p, bin_dir, timeout, verilator,
                                  cfg["known_warnings"]))
    return code


def stub(issue: int):
    def run(ctx: Context, args) -> int:
        print(f"{args.command}: not implemented yet (see issue #{issue})", file=sys.stderr)
        return 2
    return run


def _sim_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", required=True, help="IP id, e.g. a or a_b, or common")
    p.add_argument("--test", required=True, help="test name from the IP's level lists")
    p.add_argument("--seed", type=int, help="seed (default: the test's first regress seed)")
    p.add_argument("--waves", action="store_true", help="dump waves into the test directory")
    p.add_argument("--slots", metavar="DIR",
                   help="host CPU slot directory shared with other runs: each build and test "
                        f"holds one of nproc slots (default: ${host_slots.ENV})")
    p.add_argument("--no-cache", action="store_true",
                   help="build afresh: neither use nor fill the host build cache "
                        f"(default: on, ${build_cache.ENV} or ~/.cache/<repo>/build)")


def _regress_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--level", type=int, choices=LEVELS, action="append", required=True,
                   help="level to run; repeat to combine (levels are exclusive)")
    p.add_argument("--ip", action="append",
                   help="restrict to this IP id (or common); repeatable")
    p.add_argument("--affected", metavar="BASE",
                   help="only IPs affected by changes since the merge base with BASE")
    p.add_argument("--jobs", "-j", type=int, default=1, help="parallel builds/tests")
    p.add_argument("--seed", type=int, help="base seed; run i of a test uses seed+i")
    p.add_argument("--waves", action="store_true", help="dump waves for every test")
    p.add_argument("--coverage", action="store_true",
                   help="coverage builds; merge line, toggle and functional coverage per IP "
                        "and check the floors")
    p.add_argument("--rotate", metavar="KEY",
                   help="also run extra seeds derived from KEY (a date or run id) on each "
                        "entry of the rotation level (cfg/levels.yaml rotation), within its "
                        "CPU budget and without coverage")
    p.add_argument("--extra-seeds", type=int, metavar="N",
                   help="extra seeds per entry with --rotate (default: cfg/levels.yaml)")
    p.add_argument("--dry-run", action="store_true", help="print the selection only")
    p.add_argument("--slots", metavar="DIR",
                   help="host CPU slot directory shared with other runs: each build and test "
                        f"holds one of nproc slots (default: ${host_slots.ENV})")
    p.add_argument("--no-cache", action="store_true",
                   help="build afresh: neither use nor fill the host build cache "
                        f"(default: on, ${build_cache.ENV} or ~/.cache/<repo>/build)")


def _clean_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", action="append", help="only this IP's output; repeatable")


def _check_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--ip", action="append",
                   help="restrict layout, filelist, style, wiki, flows and lint to this IP id; repeatable")
    p.add_argument("--only", action="append", choices=CHECKS,
                   help="run only this check; repeatable (default: all)")


def _fpga_args(p: argparse.ArgumentParser) -> None:
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--ip", action="append", help="IP id with an fpga/<ip>/ folder; repeatable")
    which.add_argument("--all", action="store_true", help="every IP that has an fpga/<ip>/ folder")
    p.add_argument("--part", help="override the device set by the board settings file")
    p.add_argument("--timeout", type=float, help="seconds per step (default: cfg/fpga.yaml)")
    p.add_argument("--synth-only", action="store_true",
                   help="analysis and synthesis only (no fit or timing), in workdir/<ip>/fpga-synth/")
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


def _pr_args(p: argparse.ArgumentParser) -> None:
    act = p.add_subparsers(dest="action", required=True, metavar="action")
    c = act.add_parser("check", help="pre-check a PR body's recorded evidence (read-only)")
    c.add_argument("number", type=int, help="pull request number")
    c.add_argument("--repo", help="OWNER/NAME (default: $GH_REPO, then the origin remote)")
    c.add_argument("--body-file", metavar="FILE",
                   help="read the body from FILE instead of GitHub (offline)")
    c.add_argument("--head", metavar="SHA", help="with --body-file: the PR head SHA")
    c.add_argument("--files", nargs="*", metavar="PATH",
                   help="with --body-file: the changed paths (default: unknown, "
                        "the mutation rule is skipped)")
    c.add_argument("--labels", metavar="A,B",
                   help="with --body-file: the PR labels (default: unknown, skipped)")
    c.add_argument("--diff", metavar="FILE",
                   help="with --body-file: the PR's unified diff (git diff output), for the "
                        "neutrality rule; also gives --files when that is absent")


def _ci_args(p: argparse.ArgumentParser) -> None:
    act = p.add_subparsers(dest="action", required=True, metavar="action")
    u = act.add_parser("unit", help="run the tools and model unit tests a change needs")
    u.add_argument("--affected", metavar="BASE",
                   help="only the suites affected since the merge base with BASE")
    v = act.add_parser("vendor", help="run required vendor checks for affected synthesis targets")
    v.add_argument("--kind", choices=ci_vendor.KINDS, required=True)
    v.add_argument("--affected", metavar="BASE", help="select since the merge base with BASE")
    v.add_argument("--dry-run", action="store_true", help="print target selection without vendor execution")
    g = act.add_parser("gate", help="fail unless every required CI job succeeded")
    for name in ("tests", "fpga"):
        g.add_argument(f"--{name}", required=True, help="required job result")
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
    "check": ("static checks: layout, filelist, style, wiki mirror, flow inputs, neutrality, citation, publishability, spec-cache, lint, cdc", cmd_check, _check_args),
    "labels": ("GitHub label sync and check", cmd_labels, _labels_args),
    "pr": ("read-only pre-check of a PR's recorded evidence", cmd_pr, _pr_args),
    "ci": ("ci-gate helpers: tests, vendor flows, aggregate, summary, data-branch status", cmd_ci, _ci_args),
    "wiki": ("build or serve the wiki site", cmd_wiki, _wiki_args),
    "metrics": ("nightly metrics, data guard and red issue", metrics.run, metrics.add_args),
    "fpga": ("FPGA flow: Quartus compile and metrics", cmd_fpga, _fpga_args),
    "asic": ("ASIC flow (ORFS)", asic_cli.run, asic_cli.add_args),
    "arch": ("architecture-model perf scenarios", arch_cli.run, arch_cli.add_args),
    "specs": ("local cache of official reference documents (never committed)",
              specs_cli.run, specs_cli.add_args),
}


def _shared_args(p: argparse.ArgumentParser, default=None) -> None:
    p.add_argument("--src-root", default=default, help="source tree to discover IPs in "
                   "(default: $BUILD_SRC_ROOT or <repo>/src)")
    p.add_argument("--workdir", default=default, help="output directory "
                   "(default: $BUILD_WORKDIR or <repo>/workdir)")


def _nested_actions(p: argparse.ArgumentParser):
    """Every parser below `p` in its subcommand tree (actions of an action...)."""
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            for child in dict.fromkeys(action.choices.values()):  # aliases share a parser
                yield child
                yield from _nested_actions(child)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="build", description="Factory build entry point.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name, (help_text, handler, add_args) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text)
        _shared_args(p)
        if add_args is None:  # stubs accept anything until implemented
            p.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
        else:
            add_args(p)
        # Shared options are also accepted after an action (`wiki build
        # --workdir D`). SUPPRESS keeps a value given before the action: an
        # action parser that did not see the option adds nothing to the
        # namespace, while one that did overrides the earlier value.
        for child in _nested_actions(p):
            _shared_args(child, default=argparse.SUPPRESS)
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
