"""``./build regress``: select, run and summarize per-IP regression levels."""

from __future__ import annotations

import json
import re
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from build import cache as build_cache
from build import sim
from build.logexcerpt import excerpt
from build.filelist import resolve
from build.ip import Context, Ip, find_ip, shared_blocks, units
from ci.config import load_rules
from regress import coverage as cov
from regress.affected import changed_files, select_affected_levels
from regress.deps import ARCH, Imports
from regress.schema import LevelBudget, TestEntry, load_budgets, load_floors, load_ip


# Lines of a failed build's log printed in the run output: compiler, make,
# kernel and disk errors, then the tail.
BUILD_ERROR = re.compile(r"error|Error|ERROR|Killed|killed|fatal|No space left|Cannot allocate")
BUILD_TAIL = 20


def default_seed(entry: TestEntry, index: int) -> int:
    """Deterministic seed for the ``index``-th run of a test."""
    return zlib.crc32(f"{entry.ip}:{entry.name}:{index}".encode()) & 0x7FFFFFFF


def seeds_for(entry: TestEntry, base_seed: int | None) -> list[int]:
    """``--seed S`` gives S, S+1, ...; otherwise the explicit list, or seeds
    derived from the entry for a count."""
    if base_seed is not None:
        return [base_seed + i for i in range(entry.runs)]
    if isinstance(entry.seeds, tuple):
        return list(entry.seeds)
    return [default_seed(entry, i) for i in range(entry.seeds)]


def coverage_seeds(entry: TestEntry, base_seed: int | None) -> set[int]:
    """The seeds that run on the coverage build with ``--coverage``: the
    first ``coverage_seeds`` of the entry's seeds (in its own order)."""
    return set(seeds_for(entry, base_seed)[:entry.coverage_seeds])


def pmap(fn, items, jobs: int) -> list:
    """Map ``fn`` over ``items`` on ``jobs`` threads. On Ctrl-C, kill every
    running child process group, drop queued work and re-raise."""
    pool = ThreadPoolExecutor(max_workers=jobs)
    try:
        return list(pool.map(fn, items))
    except KeyboardInterrupt:
        sim.kill_all()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def load_all(ips: list[Ip]) -> dict[str, list[TestEntry]]:
    """Load and validate every IP's regression lists."""
    return {ip.id: load_ip(ip) for ip in ips}


@dataclass
class Selection:
    ips: list[Ip]
    reasons: dict[str, str]
    runs: list[tuple[TestEntry, int]]  # (test, seed)
    base_seed: int | None = None


def select(
    ctx: Context,
    levels: list[int],
    ip_ids: list[str] | None = None,
    affected_base: str | None = None,
    base_seed: int | None = None,
) -> Selection:
    all_ips = units(ctx.src_root)  # every IP and the shared unit
    tests = load_all(all_ips)  # validate everything, not only the selection
    if ip_ids:
        chosen = [find_ip(all_ips, i) for i in dict.fromkeys(ip_ids)]
        reasons = {ip.id: "--ip" for ip in chosen}
    else:
        chosen = list(all_ips)
        reasons = {ip.id: "all" for ip in chosen}
    if affected_base is not None:
        rules = load_rules(ctx.cfg_dir)
        repo, changed = changed_files(affected_base, ctx.src_root)
        hit = select_affected_levels(all_ips, repo, changed, ctx.src_root, rules)
        chosen = [ip for ip in chosen if ip.id in hit]
        caps = {ip.id: hit[ip.id][1] for ip in chosen}
        reasons = {ip.id: hit[ip.id][0] + ("" if caps[ip.id] is None else
                   f" (level {', '.join(map(str, caps[ip.id]))} only)") for ip in chosen}
    else:
        caps = {}
    chosen.sort(key=lambda ip: ip.id)
    runs = []
    for ip in chosen:
        cap = caps.get(ip.id)
        for entry in tests[ip.id]:
            if entry.level in levels and (cap is None or entry.level in cap):
                runs.extend((entry, s) for s in seeds_for(entry, base_seed))
    runs.sort(key=lambda r: (r[0].level, r[0].ip, r[0].name, r[1]))
    return Selection(chosen, reasons, runs, base_seed)


def _spec(ips: dict[str, Ip], entry: TestEntry, waves: bool, coverage: bool = False) -> sim.BuildSpec:
    return sim.BuildSpec(ips[entry.ip], entry.top, dict(entry.params), waves, coverage,
                         entry.backend)


def model_key(ctx: Context, ip: Ip, entry: TestEntry, seed: int, verilator: Path,
              graph: Imports, shared_dv: list[str]) -> str:
    """The key of a ``rerun_when: models_change`` run: the entry and seed,
    the tool versions, every file of the IP's DV and arch folders (tests,
    testbench, models), every model or import-root file their Python
    imports, the shared DV folders and the simulation driver. RTL is not
    in it: such an entry runs the models only (wiki/tools/regress/SPEC.md,
    "Reuse of unchanged model checks")."""
    h = build_cache.Hasher(sim.tree_root(ip))
    h.value("entry", {"ip": entry.ip, "name": entry.name, "module": entry.module,
                      "testcase": entry.testcase, "top": entry.top, "params": entry.params,
                      "backend": entry.backend, "timeout_s": entry.timeout_s, "seed": seed})
    h.value("tools", sim.tool_ident(verilator))
    files: set[Path] = set()
    for d in [ip.dv_dir, ip.dir(ARCH)] + [ip.src_root / s for s in shared_dv]:
        files.update(p.resolve() for p in build_cache.tree_files(d))
    files.update(graph.reach(ip)[0])
    files.update(p.resolve() for p in Path(sim.__file__).parent.glob("*.py"))
    for f in sorted(files, key=h.name):
        h.file("file", f)
    return h.hexdigest()


def run(
    ctx: Context,
    selection: Selection,
    levels: list[int],
    jobs: int = 1,
    waves: bool = False,
    out=sys.stdout,
    coverage: bool = False,
) -> dict:
    """Build and run the selection; write and return the summary. With
    ``coverage``, builds are coverage builds and each IP's coverage is
    merged and checked against its floors."""
    remove_summary(ctx.workdir)
    verilator = sim.find_verilator()
    if verilator is None and selection.runs:
        raise sim.SimError(sim.not_found_message())
    budgets = load_budgets(ctx.cfg_dir)
    floors = {ip.id: load_floors(ip) for ip in selection.ips} if coverage else {}
    cov_tool, cov_error = None, None
    if coverage and verilator is not None:
        try:
            cov_tool = cov.find_tool(verilator)
        except cov.CoverageError as exc:  # recorded in the summary after the tests
            cov_error = str(exc)
    ips = {ip.id: ip for ip in selection.ips}
    wall_start = time.monotonic()
    cache = sim.get_cache()

    # With --coverage only each entry's first `coverage_seeds` seeds run on
    # the coverage build; the rest run on the plain build.
    cov_of = {}
    for entry, seed in selection.runs:
        cov_of[(entry, seed)] = coverage and seed in coverage_seeds(entry, selection.base_seed)

    # A `rerun_when: models_change` run whose models are unchanged since a
    # pass on this host is reused, not rerun.
    keys: dict[tuple[TestEntry, int], str] = {}
    reused: dict[tuple[TestEntry, int], dict] = {}
    if cache is not None and verilator is not None and any(e.rerun_when for e, _ in selection.runs):
        rules = load_rules(ctx.cfg_dir)
        graph = Imports(units(ctx.src_root), (ctx.repo_root / rules.import_root).resolve(),
                        rules.models)
        for entry, seed in selection.runs:
            if entry.rerun_when:
                k = model_key(ctx, ips[entry.ip], entry, seed, verilator, graph, rules.shared_dv)
                keys[(entry, seed)] = k
                rec = cache.pass_record(k)
                if rec is not None:
                    reused[(entry, seed)] = rec

    specs: dict[tuple[str, str], sim.BuildSpec] = {}
    for entry, seed in selection.runs:
        if (entry, seed) in reused:
            continue
        spec = _spec(ips, entry, waves, cov_of[(entry, seed)])
        specs.setdefault((entry.ip, spec.key), spec)

    def do_build(spec: sim.BuildSpec):
        res = sim.build(spec, ctx.workdir, verilator)
        lines = [f"[build] {spec.ip.id}:{spec.key} {res.status}{' cached' if res.cached else ''} "
                 f"({res.runtime_s:.1f}s)"]
        if res.status != "pass" and res.log.is_file():
            # The compiler's own message (wiki/tools/build/SPEC.md, Failure excerpt).
            lines += excerpt(res.log.read_text(errors="replace"), BUILD_ERROR,
                             f"{spec.ip.id}:{spec.key} build.log", tail=BUILD_TAIL)
        print("\n".join(lines), file=out, flush=True)
        return res

    built = dict(zip(specs, pmap(do_build, list(specs.values()), jobs)))

    def do_test(run_item):
        entry, seed = run_item
        rec = reused.get(run_item)
        if rec is not None:
            log = sim.test_dir(ctx.workdir, ips[entry.ip], entry.name, seed) / "sim.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(f"reused: models unchanged since the pass of {rec.get('passed')} "
                           f"(key {keys[run_item]})\n")
            res = sim.Result("pass", 0.0, log, "reused: models unchanged")
            print(f"[L{entry.level}] {entry.ip}:{entry.name} seed={seed} PASS "
                  f"(reused: models unchanged since {rec.get('passed')})", file=out, flush=True)
            return res
        spec = _spec(ips, entry, waves, cov_of[run_item])
        b = built[(entry.ip, spec.key)]
        if b.status != "pass":
            res = sim.Result("error", 0.0, b.log, b.detail)
        else:
            res = sim.run_test(
                spec, ctx.workdir, verilator, entry.name, entry.module,
                entry.testcase, seed, entry.timeout_s, b,
            )
            if res.status == "pass" and run_item in keys and cache is not None:
                cache.record_pass(keys[run_item], {
                    "ip": entry.ip, "test": entry.name, "seed": seed,
                    "passed": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print(
            f"[L{entry.level}] {entry.ip}:{entry.name} seed={seed} "
            f"{res.status.upper()} ({res.runtime_s:.1f}s)"
            + (f" {res.detail}" if res.status != "pass" else ""),
            file=out, flush=True,
        )
        return res

    results = pmap(do_test, selection.runs, jobs)

    coverage_doc = None
    if coverage:
        try:
            if cov_error:
                raise cov.CoverageError(cov_error)
            coverage_doc = collect_coverage(ctx, selection, levels, results, floors, cov_tool,
                                            cov_of)
        except cov.CoverageError as exc:
            coverage_doc = {"error": str(exc)}
    summary = make_summary(
        ctx, selection, levels, budgets, list(built.items()), results,
        time.monotonic() - wall_start, coverage_doc, cov_of, set(reused),
    )
    summary["build_cache"] = None if cache is None else {
        "dir": build_cache.show(cache.root),
        "builds": len(built),
        "hits": sum(r.cached for r in built.values()),
        "reused_runs": len(reused),
    }
    if cache is not None:
        cache.prune()
    write_summary(ctx.workdir, summary)
    return summary


def dv_only_sources(ip: Ip) -> frozenset[Path]:
    """Sources ``<ip>.sim.f`` reaches that ``<ip>.rtl.f`` does not. The
    shared unit has no ``.rtl.f``: its RTL is every shared block's."""
    rtl_fs = shared_blocks(ip.src_root) if ip.shared else [ip.rtl_f]
    if not (ip.sim_f.is_file() and rtl_fs and all(f.is_file() for f in rtl_fs)):
        return frozenset()
    rtl = frozenset(s for f in rtl_fs for s in resolve(f).sources)
    return frozenset(resolve(ip.sim_f).sources) - rtl


def collect_coverage(
    ctx: Context,
    selection: Selection,
    levels: list[int],
    results: list[sim.Result],
    floors: dict[str, dict[int, dict[str, float]]],
    tool: Path | None,
    cov_of: dict | None = None,
) -> dict:
    """Merge each selected IP's passing coverage runs and check its floors."""
    ips = {ip.id: ip for ip in selection.ips}
    passing: dict[str, list[Path]] = {ip: [] for ip in ips}
    for (entry, seed), res in zip(selection.runs, results):
        if res.status == "pass" and (cov_of is None or cov_of[(entry, seed)]):
            passing[entry.ip].append(sim.test_dir(ctx.workdir, ips[entry.ip], entry.name, seed))
    out = {}
    for ip_id, ip in sorted(ips.items()):
        ip_dir = ip.work_dir(ctx.workdir) / cov.COVERAGE_DIR
        entry = cov.merge_ip(ip_dir, passing[ip_id], tool, dv_only_sources(ip))
        names = {lv: ip.level_file(lv).name for lv in levels}
        eff = cov.effective_floors(floors.get(ip_id, {}), levels, names)
        entry["floors"] = cov.floors_doc(eff)
        entry["failures"] = cov.check_floors(entry, eff)
        entry["dir"] = str(ip_dir.relative_to(ctx.workdir))
        out[ip_id] = entry
    return {"ips": out, "passed": not any(e["failures"] for e in out.values())}


def make_summary(
    ctx: Context,
    selection: Selection,
    levels: list[int],
    budgets: dict[int, LevelBudget],
    builds: list,
    results: list[sim.Result],
    wall_s: float,
    coverage: dict | None = None,
    cov_of: dict | None = None,
    reused: set | None = None,
) -> dict:
    def rel(p: Path) -> str:
        try:
            return str(Path(p).relative_to(ctx.workdir))
        except ValueError:
            return str(p)

    tests = []
    for (entry, seed), res in zip(selection.runs, results):
        tests.append({
            "ip": entry.ip,
            "level": entry.level,
            "name": entry.name,
            "seed": seed,
            "status": res.status,
            "runtime_s": round(res.runtime_s, 3),
            "cpu_s": round(res.cpu_s, 3),
            "log": rel(res.log),
            "detail": res.detail,
            "coverage": bool(cov_of and cov_of[(entry, seed)]),
            "reused": bool(reused and (entry, seed) in reused),
        })
    totals = {}
    for level in sorted(levels):
        rows = [t for t in tests if t["level"] == level]
        runtime = round(sum(t["runtime_s"] for t in rows), 3)
        cpu = round(sum(t["cpu_s"] for t in rows), 3)
        budget = budgets[level].budget_s
        totals[f"level{level}"] = {
            "tests": len(rows),
            "passed": sum(t["status"] == "pass" for t in rows),
            "reused": sum(t["reused"] for t in rows),
            "failed": sum(t["status"] != "pass" for t in rows),
            "runtime_s": runtime,
            "cpu_s": cpu,
            "budget_s": budget,
            # Budgets are CPU time (DevOps BKM, Levels and budgets): host
            # load stretches wall time, not CPU time.
            "over_budget": cpu > budget,
        }
    return {
        "levels": sorted(levels),
        "src_root": str(ctx.src_root),
        "ips": {ip.id: selection.reasons[ip.id] for ip in selection.ips},
        "selected_ips": len(selection.ips),
        "selected_tests": len(tests),
        "builds": [
            {"ip": ip_id, "build": key, "status": r.status, "cached": r.cached,
             "runtime_s": round(r.runtime_s, 3), "cpu_s": round(r.cpu_s, 3),
             "log": rel(r.log)}
            for (ip_id, key), r in builds
        ],
        "tests": tests,
        "totals": totals,
        "wall_s": round(wall_s, 3),
        "coverage": coverage,
        "passed": all(t["status"] == "pass" for t in tests)
        and all(r.status == "pass" for _, r in builds)
        and (coverage is None or coverage.get("passed") is True),
    }


def summary_path(workdir: Path) -> Path:
    return workdir / "regress" / "summary.json"


def remove_summary(workdir: Path) -> None:
    """Drop a previous run's summary so a failed or aborted run never leaves
    a stale result behind."""
    summary_path(workdir).unlink(missing_ok=True)


def write_summary(workdir: Path, summary: dict) -> Path:
    path = summary_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return path


def report(summary: dict, out=None, err=None) -> int:
    """Print totals, budget warnings and coverage; return the process exit
    code (2 when coverage could not be merged)."""
    out, err = out or sys.stdout, err or sys.stderr
    for key, t in summary["totals"].items():
        print(
            f"{key}: {t['passed']}/{t['tests']} passed"
            + (f" ({t['reused']} reused)" if t.get("reused") else "")
            + f", CPU {t['cpu_s']:.1f}s "
            f"(budget {t['budget_s']:.0f}s), test wall time {t['runtime_s']:.1f}s",
            file=out,
        )
        if t["over_budget"]:
            print(
                f"warning: {key} CPU time {t['cpu_s']:.1f}s (the sum of its tests' "
                f"CPU time) exceeds its budget of {t['budget_s']:.0f}s",
                file=err,
            )
    bc = summary.get("build_cache")
    if bc:
        print(f"build cache: {bc['hits']}/{bc['builds']} builds reused ({bc['dir']})", file=out)
    coverage = summary.get("coverage")
    if coverage and "error" in coverage:
        print(f"error: coverage: {coverage['error']}", file=err)
    elif coverage:
        for line in cov.report_lines(coverage):
            print(line, file=out)
        for ip_id, e in sorted(coverage["ips"].items()):
            for msg in e.get("warnings", []):
                print(f"warning: coverage: {ip_id}: {msg}", file=err)
            for msg in e["failures"]:
                print(f"error: coverage floor: {ip_id}: {msg}", file=err)
    print("PASSED" if summary["passed"] else "FAILED", file=out)
    if coverage and "error" in coverage:
        return 2
    return 0 if summary["passed"] else 1
