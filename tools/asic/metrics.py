"""Parse ORFS reports into the asic metrics.json (wiki/tools/asic/SPEC.md).

Inputs, all under the ORFS work home (``WORK_HOME``):

- ``logs/<platform>/<nick>/base/*.json``: per-step ORFS metrics, keys like
  ``finish__timing__setup__ws``; merged, the latest stage wins.
- ``reports/<platform>/<nick>/base/6_finish.rpt``: the final report; its
  ``report_checks -path_delay max`` section gives the worst setup path.
- ``reports/<platform>/<nick>/base/synth_stat.txt``: Yosys ``stat``; fallback
  cell count and area when the JSON lacks them.
- ``reports/<platform>/<nick>/base/asic_extra.json``: written by
  ``extra_metrics.tcl`` (max fanout, logic depth, setup slack per clock).

Every parser is pure (text in, dict out) so unit tests run on fixtures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ORFS metric prefixes, latest stage first.
STAGES = ("finish", "detailedroute", "globalroute", "cts", "detailedplace",
          "globalplace", "placeopt", "floorplan", "synth")

# Metrics that must be present for a run to count as complete.
REQUIRED = ("timing.setup_ws_ns", "timing.tns_ns", "area.stdcell_um2", "cells",
            "utilization", "max_fanout.value", "logic_depth.value",
            "worst_path.from", "worst_path.to")


# Metrics recorded when present and warned about when not, never failing the
# run: the per-clock slacks stay optional until a nightly shows them populated.
OPTIONAL = ("timing.clocks",)


def merge_step_json(texts: list[str]) -> dict:
    """Merge ORFS per-step metric JSON files (in name order); bad files skipped."""
    merged: dict = {}
    for text in texts:
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


def latest(metrics: dict, suffix: str):
    """The value of ``<stage>__<suffix>`` for the latest stage that has it."""
    for stage in STAGES:
        key = f"{stage}__{suffix}"
        if metrics.get(key) is not None:
            return metrics[key]
    return None


# "Number of cells: N" (older Yosys) or "  N  <area>  N  <area> cells" (0.5x+).
_STAT_CELLS = re.compile(r"^\s*Number of cells:\s*(\d+)\s*$|^\s*(\d+)\s+.*\bcells\s*$", re.M)
_STAT_AREA = re.compile(r"Chip area for (?:top )?module '\\?[^']*':\s*([0-9.]+)")


def parse_synth_stat(text: str) -> dict:
    """Cell count and area of the top from a Yosys ``stat`` report (the last
    block is the top in a hierarchical report)."""
    cells = _STAT_CELLS.findall(text)
    areas = _STAT_AREA.findall(text)
    out = {}
    if cells:
        last = cells[-1]
        out["cells"] = int(last[0] or last[1])
    if areas:
        out["area_um2"] = float(areas[-1])
    return out


# A path line that names a pin and its cell: "... ^ inst/PIN (cell)".
_PIN_LINE = re.compile(r"\s(?P<inst>\S+)/(?P<pin>[^/\s]+) \((?P<cell>[^)\s]+)\)\s*$")
_PORT_LINE = re.compile(r"\s(?P<port>\S+) \((?:in|out)\)\s*$")


def parse_worst_path(text: str) -> dict | None:
    """The worst setup path from an OpenSTA ``report_checks -path_delay max``
    section: start and end point, slack and the number of cells on the data
    path (instances strictly between start and end). None if absent."""
    start = text.find("report_checks -path_delay max")
    if start < 0:
        return None
    section = text[start:].splitlines()
    path: dict = {}
    pending = None  # "from_kind"/"to_kind" when the "(...)" wraps to the next line
    for line in section:
        m = re.match(r"\s+\((.*)\)\s*$", line)
        if pending and m:
            path[pending] = m.group(1)
            pending = None
            continue
        pending = None
        m = re.match(r"\s*Startpoint:\s*(\S+)(?:\s+\((.*)\))?", line)
        if m and "from" not in path:
            path["from"] = m.group(1)
            path["from_kind"] = m.group(2)
            pending = None if m.group(2) else "from_kind"
            continue
        m = re.match(r"\s*Endpoint:\s*(\S+)(?:\s+\((.*)\))?", line)
        if m and "to" not in path:
            path["to"] = m.group(1)
            path["to_kind"] = m.group(2)
            pending = None if m.group(2) else "to_kind"
            continue
        m = re.match(r"\s*Path Group:\s*(\S+)", line)
        if m and "group" not in path:
            path["group"] = m.group(1)
            continue
        m = re.match(r"\s*(-?[0-9.]+)\s+slack\s+\((\w+)\)", line)
        if m:
            path["slack_ns"] = float(m.group(1))
            break
    if "from" not in path or "to" not in path:
        return None
    path["cells"] = _data_path_cells(section, path["from"], path["to"])
    path["points"] = _data_path_points(section, path["from"])
    return path


# A timed path line: "... <time> ^|v <pin> (<cell>|in|out)".
_POINT_LINE = re.compile(r"(?P<time>-?[0-9.]+)\s+(?P<edge>[\^v])\s+(?P<pin>\S+)"
                         r"(?: \((?P<cell>[^)\s]+)\))?\s*$")


def _data_path_points(lines: list[str], frm: str) -> list[dict]:
    """Timed points of the data path, from the startpoint to the endpoint pin
    (clock-network lines before the startpoint skipped)."""
    points: list[dict] = []
    for line in lines:
        if "data arrival time" in line:
            break
        m = _POINT_LINE.search(line)
        if not m:
            continue
        pin = m.group("pin")
        if not points and pin != frm and not pin.startswith(frm + "/"):
            continue
        points.append({"arrival_ns": float(m.group("time")), "edge": m.group("edge"),
                       "pin": pin, "cell": m.group("cell")})
    return points


def format_worst_path(path: dict | None) -> str:
    """A short, tool-neutral text summary of the worst setup path."""
    if not path:
        return "worst setup path: not found\n"
    lines = [
        f"worst setup path ({path.get('group')}): slack {path.get('slack_ns')} ns, "
        f"{path.get('cells')} cells",
        f"from: {path['from']} ({path.get('from_kind')})",
        f"to:   {path['to']} ({path.get('to_kind')})",
        "",
        f"{'arrival_ns':>10}  edge  {'pin':<40} cell",
    ]
    for pt in path.get("points", []):
        lines.append(f"{pt['arrival_ns']:>10.2f}  {'rise' if pt['edge'] == '^' else 'fall':<4}  "
                     f"{pt['pin']:<40} {pt['cell'] or ''}")
    return "\n".join(lines) + "\n"


def _data_path_cells(lines: list[str], frm: str, to: str) -> int | None:
    """Count instances on the data path, from the startpoint (exclusive) to
    the endpoint (exclusive). Clock-network lines before the startpoint are
    skipped."""
    started = False
    seen: list[str] = []
    for line in lines:
        if "data arrival time" in line:
            break
        pin = _PIN_LINE.search(line)
        port = _PORT_LINE.search(line)
        name = pin.group("inst") if pin else (port.group("port") if port else None)
        if name is None:
            continue
        if name == frm:
            started = True
            continue
        if started and name != to and name not in seen:
            seen.append(name)
    return len(seen) if started else None


def _unescape(name):
    """odb keeps bus brackets escaped (``q\\[5\\]``); report them plainly."""
    return name.replace("\\[", "[").replace("\\]", "]") if isinstance(name, str) else name


def parse_extra(text: str) -> dict:
    """``asic_extra.json`` from extra_metrics.tcl, names unescaped."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: _unescape(v) for k, v in data.items()}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def clock_slacks(extra: dict) -> dict | None:
    """``{clock: {setup_ws_ns, wns_ns, tns_ns}}`` from the extra metrics'
    ``clocks``; None when absent, empty or malformed."""
    clocks = extra.get("clocks")
    if not isinstance(clocks, dict):
        return None
    out = {}
    for name, v in sorted(clocks.items()):
        if not isinstance(v, dict):
            continue
        ws, tns = _num(v.get("setup_ws")), _num(v.get("setup_tns"))
        out[name] = {"setup_ws_ns": ws, "wns_ns": None if ws is None else min(ws, 0.0),
                     "tns_ns": tns}
    return out or None


def clock_warning(extra: dict) -> str | None:
    """Why ``timing.clocks`` is missing, or None when it is present."""
    if clock_slacks(extra):
        return None
    if not extra:
        cause = "asic_extra.json is missing or unreadable (the extra step failed or did not run)"
    elif "clocks" not in extra:
        cause = "asic_extra.json has no clocks entry"
    elif extra["clocks"] == {}:
        cause = ("extra_metrics.tcl wrote no clock (its per-clock section failed or found no "
                 "constrained endpoint; see the asic_extra lines in flow.log)")
    else:
        cause = "the clocks entry in asic_extra.json is malformed"
    return f"timing.clocks missing (optional, run not failed): {cause}"


def summarize(step: dict, synth: dict, worst: dict | None, extra: dict) -> dict:
    """Combine the parsed inputs into the metrics.json body (minus run info)."""
    ws = latest(step, "timing__setup__ws")
    tns = latest(step, "timing__setup__tns")
    cells = latest(step, "design__instance__count__stdcell")
    area = latest(step, "design__instance__area__stdcell")
    return {
        "timing": {
            "setup_ws_ns": ws,
            "wns_ns": None if ws is None else min(ws, 0.0),
            "tns_ns": tns,
            "hold_ws_ns": latest(step, "timing__hold__ws"),
            "clocks": clock_slacks(extra),
        },
        "area": {
            "stdcell_um2": area if area is not None else synth.get("area_um2"),
            "core_um2": latest(step, "design__core__area"),
            "die_um2": latest(step, "design__die__area"),
        },
        "cells": cells if cells is not None else synth.get("cells"),
        "synth_cells": (step.get("synth__design__instance__count__stdcell")
                        if step.get("synth__design__instance__count__stdcell") is not None
                        else synth.get("cells")),
        "utilization": latest(step, "design__instance__utilization"),
        "max_fanout": {"value": extra.get("max_fanout"), "net": extra.get("max_fanout_net")},
        "logic_depth": {"value": extra.get("logic_depth"),
                        "endpoint": extra.get("logic_depth_endpoint")},
        "worst_path": ({k: v for k, v in worst.items() if k != "points"} if worst
                       else {"from": None, "to": None}),
        "power_w": latest(step, "power__total"),
        "route_drc_errors": step.get("detailedroute__route__drc_errors"),
        "warnings": [w for w in (clock_warning(extra),) if w],
    }


def missing(summary: dict) -> list[str]:
    """Names of REQUIRED metrics that are None or absent."""
    out = []
    for dotted in REQUIRED:
        value = summary
        for part in dotted.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value is None:
            out.append(dotted)
    return out


def _read(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def collect(work_home: Path, platform: str, nick: str,
            variant: str = "base") -> tuple[dict, str]:
    """Read every input from an ORFS work home: the summary and the
    worst-path text."""
    logs = work_home / "logs" / platform / nick / variant
    reports = work_home / "reports" / platform / nick / variant
    step = merge_step_json([t for p in sorted(logs.glob("*.json"))
                            if (t := _read(p)) is not None])
    synth = parse_synth_stat(_read(reports / "synth_stat.txt") or "")
    worst = parse_worst_path(_read(reports / "6_finish.rpt") or "")
    extra = parse_extra(_read(reports / "asic_extra.json") or "")
    return summarize(step, synth, worst, extra), format_worst_path(worst)
