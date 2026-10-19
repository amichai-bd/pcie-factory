"""Quartus report parsing into ``metrics.json``.

Reads, from a compiled project directory:

    output_files/<rev>.fit.summary   key : value resource summary
    output_files/<rev>.fit.rpt       memory block count (M9K, M10K, ...) and
                                     the per-instance Fitter RAM Summary
    output_files/<rev>.sta.rpt       Fmax, setup and hold summary panels
    worst_path.txt                   key=value worst setup path, written by
                                     the generated worst_path.tcl

or, after ``--synth-only`` (analysis and synthesis only):

    output_files/<rev>.map.summary   key : value synthesis summary

All parsing is plain text; Quartus is never imported or run here.
"""

from __future__ import annotations

import re
from pathlib import Path

SCHEMA = 1


class ReportError(Exception):
    """A report is missing, malformed or says the compile failed."""


# --------------------------------------------------------------------------
# Small value parsers


def _int(text: str) -> int:
    return int(text.replace(",", "").strip())


_RATIO_RE = re.compile(r"^\s*([\d,]+)\s*/\s*([\d,]+)")


def _ratio(text: str) -> dict:
    """``"45 / 114,480 ( < 1 % )"`` -> ``{"used": 45, "total": 114480}``."""
    m = _RATIO_RE.match(text)
    if not m:
        raise ReportError(f"expected 'used / total', got {text!r}")
    return {"used": _int(m.group(1)), "total": _int(m.group(2))}


def _mhz(text: str) -> float:
    m = re.match(r"^\s*([\d.]+)\s*MHz\s*$", text)
    if not m:
        raise ReportError(f"expected '<n> MHz', got {text!r}")
    return float(m.group(1))


def _float(text: str) -> float:
    try:
        return float(text.strip())
    except ValueError:
        raise ReportError(f"expected a number, got {text!r}") from None


# --------------------------------------------------------------------------
# Fitter


# Fitter summary key -> metrics key; ratios become {"used", "total"}.
_FIT_RATIOS = {
    "Total logic elements": "logic_elements",
    "Total memory bits": "memory_bits",
    "Embedded Multiplier 9-bit elements": "dsp_9bit",
    "Total pins": "pins",
    "Total PLLs": "plls",
}


def parse_fit_summary(path: Path) -> dict:
    """Parse ``<rev>.fit.summary``. Fails unless the fitter succeeded."""
    fields = {}
    for line in _read(path).splitlines():
        key, sep, value = line.partition(" : ")
        if sep:
            fields[key.strip()] = value.strip()
    status = fields.get("Fitter Status", "")
    if not status.startswith("Successful"):
        raise ReportError(f"{path}: fitter status is {status or 'missing'!r}")
    for required in ("Total logic elements", "Total registers", "Device"):
        if required not in fields:
            raise ReportError(f"{path}: no {required!r} line")
    resources = {"registers": _int(fields["Total registers"])}
    for key, name in _FIT_RATIOS.items():
        resources[name] = _ratio(fields[key]) if key in fields else None
    return {
        "quartus_version": fields.get("Quartus Prime Version"),
        "family": fields.get("Family"),
        "device": fields["Device"],
        "top": fields.get("Top-level Entity Name"),
        "resources": resources,
    }


_MEM_BLOCK_RE = re.compile(r"^;\s*(M\d+Ks)\s*;\s*([^;]+);")


def parse_fit_memory_blocks(path: Path) -> dict | None:
    """The block-RAM line of the fitter resource usage summary, e.g.
    ``; M9Ks ; 0 / 432 ( 0 % ) ;``; None if the report has none."""
    for line in _read(path).splitlines():
        m = _MEM_BLOCK_RE.match(line)
        if m:
            return {"type": m.group(1)[:-1], **_ratio(m.group(2))}
    return None


_RAM_PANEL = "Fitter RAM Summary"
# metrics key -> Fitter RAM Summary column; a missing column gives None.
_RAM_COLUMNS = {
    "name": "Name", "mode": "Mode", "clock_mode": "Clock Mode",
    "port_a_depth": "Port A Depth", "port_a_width": "Port A Width",
    "port_b_depth": "Port B Depth", "port_b_width": "Port B Width",
    "bits": "Size", "mixed_rdw": "Mixed Width RDW Mode",
}
_INT_RAM_KEYS = ("port_a_depth", "port_a_width", "port_b_depth", "port_b_width", "bits")


def parse_fit_rams(path: Path) -> list[dict]:
    """One entry per row of the "Fitter RAM Summary" panel: the inferred or
    instantiated RAM's hierarchical name, mode, clock mode, port geometry,
    size in bits, mixed-port read-during-write mode and its memory block
    count (``blocks``, from the ``M<n>Ks`` column). Empty when the design
    has no RAM (Quartus then prints no such panel)."""
    tables = panels(_read(path))
    out = []
    for row in tables.get(_RAM_PANEL, []):
        if not row.get("Name"):
            continue
        entry = {k: (row.get(col) or None) for k, col in _RAM_COLUMNS.items()}
        for k in _INT_RAM_KEYS:
            if entry[k] is not None:
                entry[k] = _count(entry[k])
        block_cols = [c for c in row if re.fullmatch(r"M\d+Ks", c)]
        entry["blocks"] = _count(row[block_cols[0]]) if block_cols else None
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# Timing analyzer


def panels(text: str) -> dict[str, list[dict[str, str]]]:
    """Split a Quartus ``.rpt`` into ``{title: rows}``.

    A panel is a run of lines starting with ``+``, ``-`` or ``;``. Its first
    ``;`` row is the title, the second the column header, and the rest the
    data rows, each as ``{header: cell}``. A panel with no rows (e.g. "No
    paths to report.") maps to an empty list. A repeated title keeps the
    first occurrence.
    """
    result: dict[str, list[dict[str, str]]] = {}
    block: list[list[str]] = []

    def flush() -> None:
        if block and len(block[0]) == 1:
            title, header, rows = block[0][0], block[1] if len(block) > 1 else [], block[2:]
            if title not in result:
                result[title] = [dict(zip(header, row)) for row in rows]
        block.clear()

    for line in text.splitlines():
        if line.startswith(";"):
            block.append([c.strip() for c in line.strip().strip(";").split(";")])
        elif line.startswith(("+", "-")):
            continue
        else:
            flush()
    flush()
    return result


SLACK_KINDS = ("setup", "hold", "recovery", "removal")


def parse_sta(path: Path) -> dict:
    """Timing from ``<rev>.sta.rpt``.

    ``clocks``: per clock, Fmax and restricted Fmax from every ``* Model Fmax
    Summary`` panel and the setup, hold, recovery and removal slack from
    every ``* Model <Kind> Summary`` panel, keeping the worst value of each
    across corners (``None`` when a kind has no paths for that clock).
    ``unconstrained``: the "Unconstrained Paths Summary" counts, as
    ``{property: {"setup": n, "hold": n}}``.

    Fmax, setup and hold panels and the unconstrained summary are required;
    recovery and removal panels are optional.
    """
    tables = panels(_read(path))
    clocks: dict[str, dict] = {}

    def entry(name: str) -> dict:
        return clocks.setdefault(name, {
            "fmax_mhz": None, "restricted_fmax_mhz": None,
            **{f"{k}_slack_ns": None for k in SLACK_KINDS}})

    def keep_min(d: dict, key: str, value: float) -> None:
        d[key] = value if d[key] is None else min(d[key], value)

    seen = set()
    for title, rows in tables.items():
        if title.endswith("Model Fmax Summary"):
            seen.add("fmax")
            for row in rows:
                e = entry(_cell(path, title, row, "Clock Name"))
                keep_min(e, "fmax_mhz", _mhz(_cell(path, title, row, "Fmax")))
                keep_min(e, "restricted_fmax_mhz",
                         _mhz(_cell(path, title, row, "Restricted Fmax")))
        for kind in SLACK_KINDS:
            if title.endswith(f"Model {kind.capitalize()} Summary"):
                seen.add(kind)
                for row in rows:
                    e = entry(_cell(path, title, row, "Clock"))
                    keep_min(e, f"{kind}_slack_ns",
                             _float(_cell(path, title, row, "Slack")))
    for need in ("fmax", "setup", "hold"):
        if need not in seen:
            raise ReportError(f"{path}: no '* Model {need.capitalize()} Summary' panel")
    if not clocks:
        raise ReportError(f"{path}: no constrained clock has timing paths")
    title = "Unconstrained Paths Summary"
    if title not in tables:
        raise ReportError(f"{path}: no {title!r} panel")
    unconstrained = {}
    for row in tables[title]:
        name = _cell(path, title, row, "Property").lower().replace(" ", "_")
        unconstrained[name] = {k: _count(_cell(path, title, row, k.capitalize()))
                               for k in ("setup", "hold")}
    return {"clocks": {name: clocks[name] for name in sorted(clocks)},
            "unconstrained": unconstrained}


def _count(text: str) -> int:
    try:
        return _int(text)
    except ValueError:
        raise ReportError(f"expected a count, got {text!r}") from None


def _cell(path: Path, title: str, row: dict, column: str) -> str:
    if column not in row:
        raise ReportError(f"{path}: panel {title!r} has no column {column!r}")
    return row[column]


_PATH_KEYS = ("slack", "from", "to", "from_clock", "to_clock", "data_delay")


def parse_worst_path(path: Path) -> dict | None:
    """``key=value`` lines written by worst_path.tcl; None if it found no
    path (the file is then empty)."""
    fields = {}
    for lineno, line in enumerate(_read(path).splitlines(), start=1):
        if not line.strip():
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in _PATH_KEYS:
            raise ReportError(f"{path}:{lineno}: unexpected line {line!r}")
        fields[key] = value.strip()
    if not fields:
        return None
    for key in ("slack", "from", "to"):
        if key not in fields:
            raise ReportError(f"{path}: no {key}= line")
    return {
        "from": fields["from"],
        "to": fields["to"],
        "from_clock": fields.get("from_clock") or None,
        "to_clock": fields.get("to_clock") or None,
        "slack_ns": round(_float(fields["slack"]), 3),
        "data_delay_ns": (round(_float(fields["data_delay"]), 3)
                          if fields.get("data_delay") else None),
    }


# --------------------------------------------------------------------------
# Metrics


def collect(project_dir: Path, revision: str) -> dict:
    """Build the metrics dict (without the flow fields the caller adds)."""
    out = Path(project_dir) / "output_files"
    fit = parse_fit_summary(out / f"{revision}.fit.summary")
    fit["resources"]["memory_blocks"] = parse_fit_memory_blocks(out / f"{revision}.fit.rpt")
    fit["resources"]["rams"] = parse_fit_rams(out / f"{revision}.fit.rpt")
    sta = parse_sta(out / f"{revision}.sta.rpt")
    clocks = sta["clocks"]
    worst = {}
    for kind in SLACK_KINDS:
        values = [c[f"{kind}_slack_ns"] for c in clocks.values()
                  if c[f"{kind}_slack_ns"] is not None]
        worst[kind] = min(values) if values else None
    unconstrained_total = sum(v["setup"] + v["hold"] for v in sta["unconstrained"].values())
    # Met: setup and hold paths exist with no negative slack, recovery and
    # removal (if any) have no negative slack, and nothing is unconstrained.
    met = (worst["setup"] is not None and worst["setup"] >= 0
           and worst["hold"] is not None and worst["hold"] >= 0
           and all(worst[k] is None or worst[k] >= 0 for k in ("recovery", "removal"))
           and unconstrained_total == 0)
    return {
        "schema": SCHEMA,
        "quartus_version": fit["quartus_version"],
        "family": fit["family"],
        "device": fit["device"],
        "top": fit["top"],
        "resources": fit["resources"],
        "timing": {
            "clocks": clocks,
            **{f"worst_{k}_slack_ns": worst[k] for k in SLACK_KINDS},
            "unconstrained": sta["unconstrained"],
            "unconstrained_total": unconstrained_total,
            "met": met,
            "critical_path": parse_worst_path(Path(project_dir) / "worst_path.txt"),
        },
    }


# Synthesis summary key -> resource name. Each value is the used count
# (a "used / total" value keeps the used part); a missing optional key is null.
_MAP_COUNTS = {
    "Total logic elements": "logic_elements",
    "Total registers": "registers",
    "Total memory bits": "memory_bits",
    "Embedded Multiplier 9-bit elements": "dsp_9bit",
    "Total pins": "pins",
    "Total PLLs": "plls",
}
_MAP_REQUIRED = ("Total logic elements", "Total registers")
_COUNT_RE = re.compile(r"^\s*([\d,]+)\b")


def _used(text: str) -> int:
    m = _COUNT_RE.match(text)
    if not m:
        raise ReportError(f"expected a count, got {text!r}")
    return _int(m.group(1))


def collect_synthesis(project_dir: Path, revision: str) -> dict:
    """``synthesis.json`` fields from ``<rev>.map.summary`` (analysis and
    synthesis only): resource counts before fitting, and no timing."""
    path = Path(project_dir) / "output_files" / f"{revision}.map.summary"
    fields = {}
    for line in _read(path).splitlines():
        key, sep, value = line.partition(" : ")
        if sep:
            fields[key.strip()] = value.strip()
    status = fields.get("Analysis & Synthesis Status", "")
    if not status.startswith("Successful"):
        raise ReportError(f"{path}: analysis and synthesis status is {status or 'missing'!r}")
    for required in _MAP_REQUIRED:
        if required not in fields:
            raise ReportError(f"{path}: no {required!r} line")
    return {
        "schema": SCHEMA,
        "stage": "synthesis",
        "quartus_version": fields.get("Quartus Prime Version"),
        "family": fields.get("Family"),
        "device": fields.get("Device"),
        "top": fields.get("Top-level Entity Name"),
        "resources": {name: _used(fields[key]) if key in fields else None
                      for key, name in _MAP_COUNTS.items()},
    }


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError as exc:
        raise ReportError(f"cannot read report {path}: {exc.strerror}") from None
