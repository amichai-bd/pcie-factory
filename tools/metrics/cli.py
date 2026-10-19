"""``./build metrics`` arguments and handlers (wiki/tools/metrics/SPEC.md)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from build.ip import Context, DiscoveryError, discover

from . import commits, github, guard, red, record, trends
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
    out = c.add_mutually_exclusive_group()
    out.add_argument("--out", help="output file (default: <workdir>/metrics/<run name>)")
    out.add_argument("--out-dir", metavar="DIR",
                     help="write DIR/<run name>, <run name> being <date>T<HHMMSS>Z.json of the run start")
    c.add_argument("--no-github", action="store_true", help="skip the GitHub sections")
    c.add_argument("--step", action="append", default=[], metavar="NAME=OUTCOME",
                   help="nightly step outcome (pass|fail|skipped|paused); repeatable")
    c.add_argument("--started", metavar="TIME",
                   help="UTC start of the nightly job (ISO 8601), for meta.nightly.duration_s")
    c.add_argument("--run-id", type=_positive, metavar="ID",
                   help="the workflow run id, for meta.run_id")
    c.add_argument("--run-attempt", type=_positive, metavar="N",
                   help="the workflow run attempt, for meta.run_attempt")
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
    t = act.add_parser("trends", help="print the per-IP drop alerts of the latest nightly record")
    t.add_argument("--dir", action="append", default=[], metavar="DIR",
                   help="directory of nightly records; repeatable, a later one wins on "
                   "the same run (default: <data_dir>)")
    r = act.add_parser("red", parents=[repo], help="open, update or close the nightly-red issue")
    r.add_argument("--outcome", required=True, choices=red.OUTCOMES)
    r.add_argument("--run-url", required=True, help="the nightly run's URL")
    r.add_argument("--head", metavar="SHA",
                   help="the commit the nightly ran on: a failure lists the commits since "
                        "the last green nightly")
    r.add_argument("--branch", default="main",
                   help="branch of the nightly runs searched for the last green one "
                        "(default: main)")
    r.add_argument("--step", action="append", default=[], metavar="NAME=OUTCOME",
                   help="nightly step outcome (pass|fail|skipped|paused); a failure names the "
                        "failed steps; repeatable")
    r.add_argument("--summary", metavar="FILE",
                   help="the run's ./build regress summary.json: a failure lists each failed "
                        "run with its exact seed and ./build sim command")


def _positive(value: str) -> int:
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value!r}")
    return int(value)


def _now(value: str | None) -> datetime:
    if not value:
        return record.now_utc()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _steps(values: list[str]) -> dict[str, str]:
    out = {}
    for v in values:
        m = _STEP.match(v)
        if not m:
            raise MetricsError(f"--step must be NAME=pass|fail|skipped|paused, got {v!r}")
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
    name = record.file_name(started or now)
    out_dir = getattr(args, "out_dir", None)
    out = Path(args.out) if args.out else (Path(out_dir) if out_dir else ctx.workdir / "metrics") / name
    raw = None
    if not args.no_github:
        gh = gh_factory(resolve_repo(args.repo, ctx.repo_root))
        print(f"[metrics] github: {gh.repo} (REST)")
        raw = github.fetch(gh, cfg, log=sys.stdout)
    rec = record.build_record(repo=ctx.repo_root, workdir=ctx.workdir, ip_ids=_ip_ids(ctx),
                              cfg=cfg, now=now, raw=raw, steps=steps, started=started,
                              versions=record.tool_versions(),
                              run=(getattr(args, "run_id", None), getattr(args, "run_attempt", None)))
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


def load_history(dirs: list[Path]) -> tuple[list[tuple[str, dict]], list[str]]:
    """(run label, record) in run order from every `*.json` in `dirs`, one
    per run (a later dir wins on the same run), and notes on the files
    skipped (unreadable, not schema 1, no date)."""
    by_run, notes = {}, []
    for d in dirs:
        for f in sorted(d.glob("*.json")) if d.is_dir() else []:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                notes.append(f"{f.name}: not a readable JSON record; skipped")
                continue
            if not isinstance(data, dict) or data.get("schema_version") != record.SCHEMA_VERSION:
                notes.append(f"{f.name}: not a schema {record.SCHEMA_VERSION} record; skipped")
                continue
            if not trends.run_date(data, f.stem):
                notes.append(f"{f.name}: no YYYY-MM-DD date; skipped")
                continue
            by_run[trends.run_identity(data, f.stem)] = (trends.run_order(data, f.stem), f.stem, data)
    ordered = sorted(by_run.values(), key=lambda x: x[0])
    return [(trends.run_label(data, stem), data) for _, stem, data in ordered], notes


def _short_sha(data: dict) -> str | None:
    meta = data.get("meta")
    sha = meta.get("commit") if isinstance(meta, dict) else None
    return sha[:7] if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{7,64}", sha) else None


def summary(history: list[tuple[str, dict]], thresholds: dict) -> str:
    """The nightly summary's Markdown: the latest record's drop alerts."""
    out = ["## Trend drop alerts", ""]
    if not history:
        return "\n".join(out + ["No nightly records found.", ""])
    records = [r for _, r in history]
    label, last = history[-1]
    commit = _short_sha(last)
    found = trends.alerts(records, thresholds)
    out.append(f"Latest nightly {label}{f' at `{commit}`' if commit else ''}, over {len(history)} runs; "
               "each series is compared with its previous recorded value, run by run "
               "(thresholds: `drop_alerts` in `cfg/metrics.yaml`).")
    out.append("")
    if not found:
        return "\n".join(out + ["No series dropped beyond its threshold.", ""])
    out += ["| IP | Series | Previous | Latest | Change | Threshold |", "| --- | --- | --- | --- | --- | --- |"]
    for d in found:
        u = d.series.unit
        du = "pts" if u == "%" else u
        out.append(f"| `{d.ip}` | {d.series.label} | {trends.fmt(d.previous, u)} ({history[d.before][0]}) "
                   f"| {trends.fmt(d.value, u)} | {trends.fmt(d.change, du)} | {trends.fmt(d.threshold, du)} |")
    return "\n".join(out + [""])


def trend_alerts(ctx: Context, args) -> int:
    cfg = load(ctx.cfg_dir)
    dirs = [Path(d) for d in args.dir] or [ctx.repo_root / cfg.data_dir]
    history, notes = load_history(dirs)
    for n in notes:
        print(f"[metrics] trends: {n}", file=sys.stderr)
    print(summary(history, cfg.drop_alerts), end="")
    return 0


def run(ctx: Context, args) -> int:
    try:
        if args.action == "collect":
            return collect(ctx, args)
        if args.action == "commits":
            return series(ctx, args)
        if args.action == "trends":
            return trend_alerts(ctx, args)
        if args.action == "guard":
            cfg = load(ctx.cfg_dir)
            files = guard.changed_files(ctx.repo_root, args.base, args.head)
            passed, lines = guard.check(args.branch, files, cfg)
            print("\n".join(lines))
            return 0 if passed else 1
        gh = Gh(resolve_repo(args.repo, ctx.repo_root))
        steps = _steps(args.step) if args.step else None
        action, numbers = red.sync(gh, args.outcome, args.run_url, args.head, steps,
                                   load(ctx.cfg_dir).nightly_workflow, args.branch,
                                   Path(args.summary) if args.summary else None)
        print(f"[metrics] nightly-red: {action}" + "".join(f" #{n}" for n in numbers))
        return 0
    except MetricsError as exc:
        print(f"error: metrics {args.action}: {exc}", file=sys.stderr)
        return 2
