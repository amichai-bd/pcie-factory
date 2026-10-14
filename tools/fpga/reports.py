"""Quartus report parsing into ``metrics.json``.

Reads, from a compiled project directory:

    output_files/<rev>.fit.summary   key : value resource summary
    output_files/<rev>.fit.rpt       memory block count (M9K, M10K, ...)
    output_files/<rev>.sta.rpt       Fmax, setup and hold summary panels
    worst_path.txt                   key=value worst setup path, written by
                                     the generated worst_path.tcl

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


def parse_sta(path: Path) -> dict:
    """Per-clock Fmax and slack from every ``* Model ...`` corner panel of
    ``<rev>.sta.rpt``, keeping the worst value of each across corners."""
    tables = panels(_read(path))
    clocks: dict[str, dict] = {}

    def entry(name: str) -> dict:
        return clocks.setdefault(name, {
            "fmax_mhz": None, "restricted_fmax_mhz": None,
            "setup_slack_ns": None, "hold_slack_ns": None})

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
        for kind in ("Setup", "Hold"):
            if title.endswith(f"Model {kind} Summary"):
                seen.add(kind.lower())
                for row in rows:
                    e = entry(_cell(path, title, row, "Clock"))
                    keep_min(e, f"{kind.lower()}_slack_ns",
                             _float(_cell(path, title, row, "Slack")))
    for need in ("fmax", "setup", "hold"):
        if need not in seen:
            raise ReportError(f"{path}: no '* Model {need.capitalize()} Summary' panel")
    if not clocks:
        raise ReportError(f"{path}: no constrained clock has timing paths")
    return {name: clocks[name] for name in sorted(clocks)}


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
    clocks = parse_sta(out / f"{revision}.sta.rpt")
    setup = [c["setup_slack_ns"] for c in clocks.values() if c["setup_slack_ns"] is not None]
    hold = [c["hold_slack_ns"] for c in clocks.values() if c["hold_slack_ns"] is not None]
    worst_setup = min(setup) if setup else None
    worst_hold = min(hold) if hold else None
    met = (worst_setup is not None and worst_setup >= 0
           and worst_hold is not None and worst_hold >= 0)
    return {
        "schema": SCHEMA,
        "quartus_version": fit["quartus_version"],
        "family": fit["family"],
        "device": fit["device"],
        "top": fit["top"],
        "resources": fit["resources"],
        "timing": {
            "clocks": clocks,
            "worst_setup_slack_ns": worst_setup,
            "worst_hold_slack_ns": worst_hold,
            "met": met,
            "critical_path": parse_worst_path(Path(project_dir) / "worst_path.txt"),
        },
    }


def _read(path: Path) -> str:
    try:
        return Path(path).read_text(errors="replace")
    except OSError as exc:
        raise ReportError(f"cannot read report {path}: {exc.strerror}") from None
