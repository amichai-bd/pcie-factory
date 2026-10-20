"""``./build regress --coverage``: merge each IP's line, toggle and
functional coverage, report it and check its floors.

Each passing run of a coverage build leaves ``coverage.dat`` (Verilator line
and toggle points) and, when its test samples a covergroup, ``funcov.json``
(:mod:`regress.funcov`) in its test directory. Per IP, ``verilator_coverage``
merges the ``.dat`` files and this module sums the functional bins, into
``workdir/<ip>/coverage/``. Code points in DV-only sources (reached from
``<ip>.sim.f`` but not ``<ip>.rtl.f``) are not counted. See
wiki/tools/regress/SPEC.md#coverage.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from build import sim
from regress import funcov
from regress.funcov import CoverageError

METRICS = ("line", "toggle", "functional")
# Verilator point type (the `t` field of a coverage.dat key) -> metric.
POINT_TYPES = {"line": "line", "branch": "line", "toggle": "toggle"}
COVERAGE_DIR = "coverage"  # workdir/<ip>/coverage/
MERGE_TIMEOUT_S = 600.0


def find_tool(verilator: Path) -> Path:
    """``verilator_coverage``, installed next to ``verilator``."""
    tool = Path(verilator).parent / "verilator_coverage"
    if not tool.is_file():
        raise CoverageError(f"verilator_coverage not found next to {verilator}")
    return tool


def parse_dat(path: Path) -> dict[str, int]:
    """Point key -> hit count of a Verilator ``coverage.dat``."""
    points: dict[str, int] = {}
    for n, line in enumerate(Path(path).read_text(errors="replace").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        key, sep, count = line.rpartition("' ")
        if not line.startswith("C '") or not sep or not count.strip().isdigit():
            raise CoverageError(f"{path}:{n}: not a coverage point")
        points[key[3:]] = points.get(key[3:], 0) + int(count)
    return points


def point_field(key: str, field: str) -> str | None:
    """One field of a point key (fields are \\x01name\\x02value)."""
    for item in key.split("\x01"):
        name, sep, value = item.partition("\x02")
        if sep and name == field:
            return value
    return None


def point_type(key: str) -> str | None:
    """The ``t`` (point type) field of a point key."""
    return point_field(key, "t")


def source_point(key: str) -> tuple:
    """Source identity, independent of instance hierarchy and parameters.

    Verilator's f/l/n locate a statement or signal declaration. The type
    and object distinguish statement/branch outcomes and each signal bit's
    transition direction. page and h identify elaborations; S annotates the
    lines a statement spans and is not another source point.
    """
    return (str(Path(point_field(key, "f") or "").resolve()),
            *(point_field(key, field) for field in ("l", "n", "t", "o")))


def code_metrics(points: dict[str, int], exclude: frozenset[Path] = frozenset(),
                 *, by_source: bool = True) -> dict[str, dict]:
    """Source points (or full per-instance keys), covered by any instance.
    Leave out points whose source file is in ``exclude`` in either view.
    """
    grouped: dict[tuple | str, tuple[str, bool]] = {}
    for key, count in points.items():
        metric = POINT_TYPES.get(point_type(key) or "")
        if not metric or Path(point_field(key, "f") or "").resolve() in exclude:
            continue
        identity = source_point(key) if by_source else key
        grouped[identity] = (metric, count > 0 or grouped.get(identity, (metric, False))[1])
    out = {m: {"covered": 0, "total": 0} for m in ("line", "toggle")}
    for metric, covered in grouped.values():
        out[metric]["total"] += 1
        out[metric]["covered"] += covered
    return out


def pct(covered: int, total: int) -> float | None:
    return None if total == 0 else 100.0 * covered / total


def merge_dat(tool: Path, inputs: list[Path], out: Path) -> None:
    """Merge with ``verilator_coverage --write``; also write an lcov
    ``.info`` for annotation tools."""
    cmd = [str(tool), "--write", str(out), "--write-info", str(out.with_suffix(".info"))]
    try:
        proc = subprocess.run(cmd + [str(p) for p in inputs], capture_output=True, text=True,
                              timeout=MERGE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CoverageError(f"verilator_coverage failed: {exc}") from None
    if proc.returncode != 0 or not out.is_file():
        raise CoverageError(f"verilator_coverage exited with {proc.returncode}: "
                            f"{(proc.stderr or proc.stdout).strip()[:500]}")


def merge_ip(ip_dir: Path, test_dirs: list[Path], tool: Path | None,
             exclude: frozenset[Path] = frozenset()) -> dict:
    """Merge one IP's passing runs into ``ip_dir`` (replaced) and return its
    entry; code points in ``exclude`` (DV-only sources) are not counted."""
    shutil.rmtree(ip_dir, ignore_errors=True)
    ip_dir.mkdir(parents=True, exist_ok=True)
    dats = [d / sim.COVERAGE_DAT for d in test_dirs if (d / sim.COVERAGE_DAT).is_file()]
    fcov = [d / sim.FUNCOV_JSON for d in test_dirs if (d / sim.FUNCOV_JSON).is_file()]
    entry: dict = {"runs": len(test_dirs), "dat_files": len(dats), "funcov_files": len(fcov)}
    no_dat = [d for d in test_dirs if not (d / sim.COVERAGE_DAT).is_file()]
    entry["warnings"] = [f"{len(no_dat)} passing run(s) left no {sim.COVERAGE_DAT}: "
                         + ", ".join(f"{d.parent.name}/{d.name}" for d in no_dat[:5])
                         + (", ..." if len(no_dat) > 5 else "")] if no_dat else []
    code = {m: {"covered": 0, "total": 0} for m in ("line", "toggle")}
    instance_code = code
    if dats:
        if tool is None:
            raise CoverageError("verilator_coverage is needed to merge coverage.dat files")
        merged = ip_dir / "merged.dat"
        merge_dat(tool, dats, merged)
        points = parse_dat(merged)
        code = code_metrics(points, exclude)
        instance_code = code_metrics(points, exclude, by_source=False)
    for m in ("line", "toggle"):
        entry[m] = {**code[m], "pct": _round(pct(code[m]["covered"], code[m]["total"]))}
    entry["by_source"] = {m: entry[m] for m in ("line", "toggle")}
    entry["by_instance"] = {
        m: {**instance_code[m], "pct": _round(pct(instance_code[m]["covered"],
                                                 instance_code[m]["total"]))}
        for m in ("line", "toggle")}
    merged_f = funcov.merge([funcov.load(p) for p in fcov])
    fsum = funcov.summarize(merged_f)
    (ip_dir / "functional.json").write_text(
        json.dumps({"schema": funcov.SCHEMA, "groups": merged_f, "summary": fsum},
                   indent=1, sort_keys=True) + "\n")
    entry["functional"] = {"covered": fsum["covered"], "total": fsum["total"],
                           "pct": _round(pct(fsum["covered"], fsum["total"])),
                           "uncovered": len(fsum["uncovered"]),
                           "groups": {g: {"covered": s["covered"], "total": s["total"],
                                          "pct": _round(pct(s["covered"], s["total"]))}
                                      for g, s in fsum["groups"].items()}}
    return entry


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _floor_order(key) -> tuple:
    """Metrics in METRICS order; per-covergroup floors after ``functional``."""
    metric, group = (key, "") if isinstance(key, str) else key
    return (METRICS.index(metric), group)


def check_floors(entry: dict, floors: dict) -> list[str]:
    """Failure messages for every floor ``entry`` misses. ``floors`` maps a
    metric, or ("functional", covergroup), to (percent, the level file that
    set it). A metric with no points, or a covergroup absent from the merged
    results, misses any floor."""
    failures = []
    for key in sorted(floors, key=_floor_order):
        floor, where = floors[key]
        if isinstance(key, tuple):
            group = key[1]
            name = f"functional covergroup {group}"
            m = entry["functional"].get("groups", {}).get(group)
            if m is None:
                failures.append(f"{name} is not in the merged results (no passing run declared it), "
                                f"below its floor of {floor:g}% ({where})")
                continue
        else:
            name, m = key, entry[key]
        value = pct(m["covered"], m["total"])
        if value is None:
            failures.append(f"{name} has no coverage points, below its floor of {floor:g}% ({where})")
        elif value < floor:
            failures.append(f"{name} {value:.2f}% ({m['covered']}/{m['total']}) is below its floor "
                            f"of {floor:g}% ({where})")
    return failures


def effective_floors(level_floors: dict[int, dict], levels: list[int], names: dict[int, str]) -> dict:
    """The highest floor per metric, and per covergroup for a per-covergroup
    ``functional`` floor, over the selected levels."""
    out: dict = {}
    for level in sorted(levels):
        for metric, value in level_floors.get(level, {}).items():
            items = ({(metric, g): v for g, v in value.items()} if isinstance(value, dict)
                     else {metric: value})
            for key, v in items.items():
                if key not in out or v > out[key][0]:
                    out[key] = (v, names[level])
    return out


def floors_doc(floors: dict) -> dict:
    """The summary's ``floors``: metric -> percent, and per-covergroup
    functional floors under ``covergroups``."""
    doc: dict = {k: v for k, (v, _) in sorted(floors.items(), key=lambda i: _floor_order(i[0]))
                 if isinstance(k, str)}
    groups = {k[1]: v for k, (v, _) in floors.items() if isinstance(k, tuple)}
    if groups:
        doc["covergroups"] = dict(sorted(groups.items()))
    return doc


def report_lines(cov: dict) -> list[str]:
    """The summary table, one row per IP."""
    def cell(m):
        return "n/a" if m["pct"] is None else f"{m['pct']:.1f}% ({m['covered']}/{m['total']})"
    rows = [["coverage", *METRICS, "floors"]]
    for ip, e in sorted(cov["ips"].items()):
        floors = "none" if not e["floors"] else ("FAIL" if e["failures"] else "ok")
        rows.append([ip, *(cell(e[m]) for m in METRICS), floors])
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
