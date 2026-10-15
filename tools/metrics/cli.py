"""``./build metrics`` arguments and handlers (wiki/tools/metrics/SPEC.md)."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from build.ip import Context, DiscoveryError, discover

from . import commits, github, guard, red, record
from .config import MetricsError, load
from .gh import Gh, resolve_repo
from .tree import TreeReader

_STEP = re.compile(r"^([a-z][a-z0-9_-]*)=(%s)$" % "|".join(record.STEP_OUTCOMES))


def add_args(p: argparse.ArgumentParser) -> None:
    repo = argparse.ArgumentParser(add_help=False)
    repo.add_argument("--repo", help="OWNER/NAME (default: $GH_REPO, $GITHUB_REPOSITORY, "
                      "then the origin remote)")
    act = p.add_subparsers(dest="action", required=True, metavar="action")
    c = act.add_parser("collect", parents=[repo], help="write one nightly record")
    c.add_argument("--out", help="output file (default: <workdir>/metrics/<date>.json)")
    c.add_argument("--no-github", action="store_true", help="skip the GitHub sections")
    c.add_argument("--step", action="append", default=[], metavar="NAME=OUTCOME",
                   help="nightly step outcome (pass|fail|skipped); repeatable")
    c.add_argument("--started", metavar="TIME",
                   help="UTC start of the nightly job (ISO 8601), for meta.nightly.duration_s")
    c.add_argument("--now", help=argparse.SUPPRESS)  # ISO time, for tests
    s = act.add_parser("commits", parents=[repo], help="append main commits to the series")
    s.add_argument("--file", help="series file (default: <data_dir>/commits.jsonl)")
    s.add_argument("--ref", default="HEAD", help="main ref whose first-parent history is "
                   "the series (default: HEAD)")
    s.add_argument("--no-ci", action="store_true", help="skip the ci-gate run lookup")
    s.add_argument("--limit", type=int, help="add at most this many commits")
    g = act.add_parser("guard", help="fail when a nightly branch changes other paths")
    g.add_argument("--branch", required=True, help="head branch name of the change")
    g.add_argument("--base", required=True, help="base revision (diffed from the merge base)")
    g.add_argument("--head", default="HEAD", help="head revision (default: HEAD)")
    r = act.add_parser("red", parents=[repo], help="open, update or close the nightly-red issue")
    r.add_argument("--outcome", required=True, choices=red.OUTCOMES)
    r.add_argument("--run-url", required=True, help="the nightly run's URL")


def _now(value: str | None) -> datetime:
    if not value:
        return record.now_utc()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _steps(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values:
        m = _STEP.match(v)
        if not m:
            raise MetricsError(f"--step must be NAME=pass|fail|skipped, got {v!r}")
        out[m.group(1)] = m.group(2)
    return out


def _ip_ids(ctx: Context) -> list[str]:
    try:
        return [ip.id for ip in discover(ctx.src_root)]
    except DiscoveryError:
        return []


def collect(ctx: Context, args, gh_factory=Gh) -> int:
    cfg = load(ctx.cfg_dir)
    now = _now(args.now)
    steps = _steps(args.step)
    try:
        started = _now(args.started) if args.started else None
    except ValueError:
        raise MetricsError(f"--started must be an ISO 8601 time, got {args.started!r}") from None
    out = Path(args.out) if args.out else ctx.workdir / "metrics" / f"{now:%Y-%m-%d}.json"
    raw = None
    if not args.no_github:
        gh = gh_factory(resolve_repo(args.repo, ctx.repo_root))
        print(f"[metrics] github: {gh.repo} (REST)")
        raw = github.fetch(gh, cfg, log=sys.stdout)
    rec = record.build_record(repo=ctx.repo_root, workdir=ctx.workdir, ip_ids=_ip_ids(ctx),
                              cfg=cfg, now=now, raw=raw, steps=steps, started=started,
                              versions=record.tool_versions())
    record.write_checked(rec, out, record.host_terms())
    for key in ("code", "tests", "coverage", "fpga", "asic", "arch"):
        print(f"[metrics] {key}: {rec[key]['status']}")
    for key, sec in rec["github"].items():
        print(f"[metrics] github.{key}: {sec['status']}"
              + (f" ({sec['reason']})" if sec.get("reason") else ""))
    print(f"[metrics] wrote {out}")
    return 0


def series(ctx: Context, args, gh_factory=Gh) -> int:
    cfg = load(ctx.cfg_dir)
    path = Path(args.file) if args.file else ctx.repo_root / cfg.data_dir / "commits.jsonl"
    ci_map = None
    if not args.no_ci:
        gh = gh_factory(resolve_repo(args.repo, ctx.repo_root))
        raw = {}
        try:
            raw["ci_runs"] = gh.paginate(
                f"repos/{gh.repo}/actions/workflows/{cfg.ci_workflow}/runs?branch=main&event=push",
                key="workflow_runs", limit=5000)
        except github.GhError as exc:
            print(f"[metrics] ci runs unavailable ({exc})")
        ci_map = github.ci_by_commit(raw)
    lines, added, refreshed = commits.update(TreeReader(ctx.repo_root, cfg), args.ref,
                                             commits.load(path), ci_map, args.limit)
    problems = record.forbidden(lines, record.host_terms())
    if problems:
        raise MetricsError(f"series refused, not publishable: {'; '.join(problems[:5])}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(commits.dumps(lines))
    print(f"[metrics] commits: {len(lines)} lines, {added} added, {refreshed} ci refreshed"
          f"{'' if ci_map is not None else ' (ci unavailable)'}: {path}")
    return 0


def run(ctx: Context, args) -> int:
    try:
        if args.action == "collect":
            return collect(ctx, args)
        if args.action == "commits":
            return series(ctx, args)
        if args.action == "guard":
            cfg = load(ctx.cfg_dir)
            files = guard.changed_files(ctx.repo_root, args.base, args.head)
            passed, lines = guard.check(args.branch, files, cfg)
            print("\n".join(lines))
            return 0 if passed else 1
        gh = Gh(resolve_repo(args.repo, ctx.repo_root))
        action, numbers = red.sync(gh, args.outcome, args.run_url)
        print(f"[metrics] nightly-red: {action}" + "".join(f" #{n}" for n in numbers))
        return 0
    except MetricsError as exc:
        print(f"error: metrics {args.action}: {exc}", file=sys.stderr)
        return 2
