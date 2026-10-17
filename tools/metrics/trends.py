"""Per-IP nightly trend series and drop alerts (wiki/tools/metrics/SPEC.md#trends).

Standard library only: the tracker (`tools/wiki_site/tracker.py`) imports it
to flag drops on the site, and `./build metrics trends` prints the same
alerts for the nightly summary. Thresholds come from `drop_alerts` in
`cfg/metrics.yaml`; series, their record paths and the rule live here.

Rule: a series drops at a nightly record when its value there is lower than
its previous *recorded* value (the nearest earlier record that carries the
series) by more than the series' threshold. Every series is higher-is-better.
A record that lacks the series is a gap: it neither alerts nor resets the
comparison, so a drop across a gap still alerts. Alerts are the drops at the
latest record; earlier drops are history.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _dig(obj, path: str):
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _ip(record, section: str, ip: str):
    ips = _dig(record, f"{section}.ips")
    entry = ips.get(ip) if isinstance(ips, dict) else None
    return entry if isinstance(entry, dict) else None


def _at(section: str, path: str):
    return lambda record, ip: _num(_dig(_ip(record, section, ip), path))


def _worst_fmax(record, ip):
    clocks = _dig(_ip(record, "fpga", ip), "timing.clocks")
    vals = [_num(_dig(c, "fmax_mhz")) for c in clocks.values()] if isinstance(clocks, dict) else []
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


def mutant_score(m):
    """An IP's `mutants` entry: `score_pct`, else 100 * killed / total."""
    pct = _num(_dig(m, "score_pct"))
    if pct is not None:
        return pct
    killed, total = _num(_dig(m, "killed")), _num(_dig(m, "total"))
    return 100 * killed / total if killed is not None and total else None


@dataclass(frozen=True)
class Series:
    key: str  # the cfg/metrics.yaml drop_alerts key
    label: str
    unit: str
    section: str  # record section whose `ips` names the IPs
    get: object  # (record, ip) -> number | None


SERIES = (
    Series("coverage_line", "Line coverage", "%", "coverage", _at("coverage", "line.pct")),
    Series("coverage_toggle", "Toggle coverage", "%", "coverage", _at("coverage", "toggle.pct")),
    Series("coverage_functional", "Functional coverage", "%", "coverage", _at("coverage", "functional.pct")),
    Series("mutant_score", "Mutant score", "%", "mutants", lambda r, ip: mutant_score(_ip(r, "mutants", ip))),
    Series("fpga_fmax", "FPGA Fmax, worst clock", "MHz", "fpga", _worst_fmax),
    Series("fpga_setup_slack", "FPGA worst setup slack", "ns", "fpga", _at("fpga", "timing.worst_setup_slack_ns")),
    Series("asic_wns", "ASIC WNS", "ns", "asic", _at("asic", "timing.wns_ns")),
    Series("asic_tns", "ASIC TNS", "ns", "asic", _at("asic", "timing.tns_ns")),
)
KEYS = tuple(s.key for s in SERIES)


@dataclass(frozen=True)
class Drop:
    ip: str
    series: Series
    before: int  # index of the previous recorded value in the history
    at: int  # index of the record where the drop shows
    previous: float
    value: float
    threshold: float

    @property
    def change(self) -> float:
        return self.value - self.previous


def ips_of(history) -> list[str]:
    """Every IP named by a trend series' section in any record, sorted."""
    found = set()
    for record in history:
        for section in {s.section for s in SERIES}:
            ips = _dig(record, f"{section}.ips")
            if isinstance(ips, dict):
                found.update(k for k in ips if isinstance(k, str))
    return sorted(found)


def drops(history, thresholds: dict, ips=None) -> list[Drop]:
    """Every drop beyond its threshold in `history` (records, oldest first),
    in history order, then IP, then series order. A series without a
    threshold never alerts."""
    out = []
    for ip in ips_of(history) if ips is None else ips:
        for s in SERIES:
            limit = thresholds.get(s.key)
            if limit is None:
                continue
            prev = None
            for i, record in enumerate(history):
                v = s.get(record, ip)
                if v is None:
                    continue  # a gap: keep comparing with the last recorded value
                if prev is not None and prev[1] - v > limit:
                    out.append(Drop(ip, s, prev[0], i, prev[1], v, limit))
                prev = (i, v)
    return sorted(out, key=lambda d: (d.at, d.ip, KEYS.index(d.series.key)))


def alerts(history, thresholds: dict, ips=None) -> list[Drop]:
    """The drops at the latest record."""
    last = len(history) - 1
    return [d for d in drops(history, thresholds, ips) if d.at == last]


def fmt(v: float, unit: str) -> str:
    """Two decimals at most, unit attached as the tracker does (`%` glued)."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    s = "0" if s == "-0" else s.replace("-", "−")
    return f"{s}{unit}" if unit == "%" else f"{s} {unit}"


def describe(d: Drop) -> str:
    """`ip: label dropped previous → value (change; threshold t)`; a
    percentage's change and threshold are in percentage points."""
    u = d.series.unit
    du = "pts" if u == "%" else u
    return (f"{d.ip}: {d.series.label} dropped {fmt(d.previous, u)} → {fmt(d.value, u)} "
            f"({fmt(d.change, du)}; threshold {fmt(d.threshold, du)})")
