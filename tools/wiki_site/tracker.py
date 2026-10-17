"""Render the metrics tracker from the records in `wiki/tracker/data/`.

`wiki_site.py` calls `render()` when `wiki/tracker/index.md` exists. It reads
`commits.jsonl` (one line per commit on main) and the nightly `<date>.json`
records, and returns Markdown (headings plus raw HTML blocks) appended to
that landing page, plus detailed stats and one generated page per IP. Every trend shares one
x axis, main commits in order labelled by the first 7 hex digits of the SHA,
and the trends of a page share one x range. Charts are `<canvas>`
placeholders whose data travels in one inline JSON block per page;
`assets/tracker.js` draws them with the vendored Chart.js. Every chart has a
data table, so the page reads without JavaScript. Records are keyed on
`schema_version`; missing groups and fields are skipped, unknown ones ignored.
See wiki/tools/wiki_site/SPEC.md#tracker.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import quote

if str(Path(__file__).resolve().parents[1]) not in sys.path:  # tools/, when run as a script
    sys.path.append(str(Path(__file__).resolve().parents[1]))
from metrics import trends  # noqa: E402  per-IP trend series and the drop rule

SUPPORTED_SCHEMAS = (1,)
DATA_DIR = "data"  # under wiki/tracker/
COMMITS = "commits.jsonl"
IP_DIR = "ip"  # generated pages: wiki/tracker/ip/<id>/index.md (virtual sources)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
SHA = re.compile(r"^[0-9a-f]{7,64}$")
IP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)*$")
SLOTS = 8  # categorical colour slots (--series-1..8 in wiki.css); never cycled
TABLE_ROWS = 30  # most recent points in each trend's data table
LIST_ROWS = 20  # rows in open-PR / open-issue / slowest-test tables
BLANK_LINE = re.compile(r"\n[ \t]*\n")  # ends a Markdown HTML block (line endings normalised to \n first)
STALE_DAYS = 7  # an open issue with no activity for longer is stale
CI_WINDOW = 20  # ci-gate pass rate window, in runs on main
RECENT_COMMITS = 30  # landing trends only; full history stays in details

# Code kinds (cfg/metrics.yaml keys) in their fixed colour order: the first
# SLOTS own their slot; later and unknown kinds (sorted) take a slot left free
# by an absent kind, else they are not drawn and the chart says so.
KINDS = {
    "rtl_sv": "RTL SV", "dv_sv": "DV SV", "dv_py": "DV Python", "tools_py": "Tools Python",
    "wiki_md": "Markdown", "config": "Config", "workflows": "Workflows", "skills": "Skills",
    "arch_py": "Arch Python",
}


# ------------------------------------------------------------------ helpers
def dig(obj, path: str):
    """`a.b.c` lookup; None for any missing or non-dict step."""
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def num(value):
    """A finite number, or the `used`/`value` field of a dict; else None."""
    if isinstance(value, dict):
        value = value.get("used", value.get("value"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def first(obj, *paths: str):
    """The first numeric value found at one of `paths`."""
    for p in paths:
        v = num(dig(obj, p))
        if v is not None:
            return v
    return None


def text(obj, *paths: str) -> str | None:
    for p in paths:
        v = dig(obj, p)
        if isinstance(v, str):
            return v
    return None


def mapping(obj, *paths: str) -> dict:
    """The first dict found at one of `paths`."""
    for p in paths:
        v = dig(obj, p)
        if isinstance(v, dict):
            return v
    return {}


def flow_ips(obj, path: str) -> dict:
    """`{ip: metrics}` of a flow section `{status, ips: {...}}`; a section
    without `ips` (e.g. `{"status": "unavailable"}`) has no IPs."""
    return mapping(obj, f"{path}.ips")


def scaled(v, k):
    return None if v is None else v * k


def median(vals):
    vals = sorted(vals)
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2


def quantile(vals, q):
    vals = sorted(vals)
    if not vals:
        return None
    return vals[min(len(vals) - 1, max(0, math.ceil(q * len(vals)) - 1))]


# ------------------------------------------------------------------ records
@dataclass
class Point:
    """One commit on the x axis, or one nightly record placed on it."""
    idx: int  # position on the commit axis
    sha: str
    date: str  # YYYY-MM-DD
    data: dict


@dataclass
class Data:
    axis: list[tuple[str, str]] = field(default_factory=list)  # (short sha, date)
    commits: list[Point] = field(default_factory=list)
    nightly: list[Point] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.commits and not self.nightly


def _check(rel: str, data, problems: list[str]) -> bool:
    if not isinstance(data, dict):
        problems.append(f"{rel}: a record must be a JSON object")
        return False
    version = data.get("schema_version")
    if version not in SUPPORTED_SCHEMAS:
        problems.append(f"{rel}: schema_version {version!r} is not supported "
                        f"(supported: {', '.join(map(str, SUPPORTED_SCHEMAS))})")
        return False
    return True


def _date(value) -> str | None:
    if not isinstance(value, str) or not DATE.match(value):
        return None
    try:
        dt.date.fromisoformat(value[:10])
    except ValueError:
        return None
    return value[:10]


def _sha(data) -> str | None:
    s = text(data, "sha") if "sha" in data else nightly_sha(data)
    return s.lower() if s and SHA.match(s.lower()) else None


def load(data_dir: Path) -> Data:
    """Read commits.jsonl and every <date>.json under `data_dir`."""
    d = Data()
    base = f"wiki/tracker/{DATA_DIR}"
    seen: dict[str, int] = {}
    path = data_dir / COMMITS
    if path.is_file():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            d.problems.append(f"{base}/{COMMITS}: unreadable ({exc})")
            lines = []
        for n, line in enumerate(lines, 1):
            if not line.strip():
                continue
            rel = f"{base}/{COMMITS}:{n}"
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                d.problems.append(f"{rel}: not JSON ({exc})")
                continue
            if not _check(rel, data, d.problems):
                continue
            sha, date = _sha(data), _date(data.get("date"))
            if not sha or not date:
                d.problems.append(f"{rel}: needs a hex `sha` and an ISO `date`")
                continue
            if sha in seen:
                d.problems.append(f"{rel}: commit {sha[:7]} repeats line {seen[sha]}; skipped")
                continue
            seen[sha] = n
            d.commits.append(Point(0, sha, date, data))
    # The collector writes lines oldest first with an `index` on the
    # first-parent history; order by it when every line has one.
    if d.commits and all(isinstance(num(p.data.get("index")), int) for p in d.commits):
        d.commits.sort(key=lambda p: p.data["index"])
    for i, p in enumerate(d.commits):
        p.idx = i
        d.axis.append((p.sha[:7], p.date))
    nightly: dict[str, tuple[str, dict]] = {}
    for f in sorted(data_dir.glob("*.json")) if data_dir.is_dir() else []:
        rel = f"{base}/{f.name}"
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            d.problems.append(f"{rel}: not a readable JSON record ({exc})")
            continue
        if not _check(rel, data, d.problems):
            continue
        raw = nightly_date(data) or f.stem
        date = _date(raw)
        if not date:
            d.problems.append(f"{rel}: date {raw!r} is not YYYY-MM-DD")
            continue
        if date in nightly:
            d.problems.append(f"{rel}: a second record for {date}; the later file wins")
        nightly[date] = (rel, data)
    by_sha = {p.sha: p for p in d.commits}
    extra = []
    for date in sorted(nightly):
        rel, data = nightly[date]
        sha = _sha(data)
        if not sha:
            d.problems.append(f"{rel}: no main `sha`; left off the commit axis")
            continue
        hit = by_sha.get(sha) or next((p for s, p in by_sha.items() if s.startswith(sha) or sha.startswith(s)), None)
        if hit:
            d.nightly.append(Point(hit.idx, hit.sha, date, data))
        elif d.commits:
            continue  # not a main commit in the series (e.g. a test run on another branch): ignored
        else:
            extra.append((date, sha, data))  # no commit history yet: nightly SHAs form the axis
    for date, sha, data in extra:
        d.nightly.append(Point(len(d.axis), sha, date, data))
        d.axis.append((sha[:7], date))
    d.nightly.sort(key=lambda p: (p.idx, p.date))
    return d


# ------------------------------------------------------------- field paths
# Every path into a record lives here, so following the collector schema
# (`tools/metrics/`, wiki/tools/metrics/SPEC.md) edits this block only. The
# first path is the collector's; later ones are tolerated alternatives.
def _loc(b):                  return first(b, "code", "loc", "lines")
def code_total(r, what):      return _loc(dig(r, "code.total")) if what == "loc" else first(r, f"code.total.{what}")
def code_kinds(r):            return mapping(r, "code.kinds", "code.by_kind")
def kind_value(b, what):      return _loc(b) if what == "loc" else first(b, what)
def code_ip(r, ip):           return mapping(r, "code.ips", "code.by_ip").get(ip)
def code_ip_kinds(r, ip):     return mapping(code_ip(r, ip), "kinds", "by_kind")
def code_ip_total(r, ip, w):  return (_loc(code_ip(r, ip)) or _loc(dig(code_ip(r, ip), "total"))) if w == "loc" \
                                  else first(code_ip(r, ip), w, f"total.{w}")
def code_dist(r, what):       return mapping(r, f"code.{what}")  # by_ext, by_dir
def ci_result(r):             return text(r, "ci.conclusion", "ci.result")
def ci_duration(r):           return first(r, "ci.duration_s")
def nightly_sha(r):           return text(r, "meta.commit", "sha", "commit")
def nightly_date(r):          return dig(r, "meta.date") or r.get("date")
def nightly_result(r):        return text(r, "meta.nightly.result", "nightly.result")
def nightly_duration(r):      return first(r, "meta.nightly.duration_s")
def test_levels(r):           return mapping(r, "tests.totals", "tests.levels")
def test_ip_levels(r, ip):
    m = mapping(r, "tests.ips", "tests.by_ip").get(ip)
    return mapping(m, "levels") or (m if isinstance(m, dict) else {})
def test_slowest(r):          return dig(r, "tests.slowest")
def fpga_ip(r, ip):           return flow_ips(r, "fpga").get(ip)
def asic_ip(r, ip):           return flow_ips(r, "asic").get(ip)
def coverage_ip(r, ip):       return flow_ips(r, "coverage").get(ip)  # {line, toggle, functional: {pct}}
def mutants_ip(r, ip):        return flow_ips(r, "mutants").get(ip)  # {score_pct} or {killed, total}
def arch_ip(r, ip):           return flow_ips(r, "arch").get(ip)  # {status, scenarios: {name: {status, targets}}}
def arch_scenarios(r, ip):    return mapping(arch_ip(r, ip), "scenarios")
def arch_status(r, ip, sc):   return text(arch_scenarios(r, ip).get(sc), "status")
def arch_value(r, ip, sc, m): return num(mapping(arch_scenarios(r, ip).get(sc), "targets").get(m))
def issues_open(r):           return first(r, "github.issues.open")
def issues_by_label(r):       return mapping(r, "github.issues.open_by_label")
def issues_window(r, w, k):   return first(r, f"github.issues.windows.{w}.{k}")
def prs_open(r):              return first(r, "github.pulls.open", "github.prs.open")
def prs_merged_7d(r):         return first(r, "github.pulls.windows.7d.merged", "github.prs.merged_7d")
def h_open_prs(r):            return dig(r, "health.open_prs")  # {count, age_h: [..]}
def h_open_issues(r):         return dig(r, "health.open_issues")  # {count, age_h, stale, stale_days}
def h_nightly_red(r):         return first(r, "health.nightly_red.open")
def h_ci_main(r):             return dig(r, "health.ci_gate_main")  # {runs, pass_rate}
def h_rounds_median(r):       return first(r, "health.review_rounds_median")
def h_approve_share(r):       return first(r, "health.approve_at_merged_head.share")
def dist(r, key):             return dig(r, f"distributions.{key}")


def level_value(levels: dict, lvl: str, what: str):
    entry = levels.get(lvl)
    if what == "count":
        return first(entry, "tests", "count")
    if what == "runtime_s":
        return first(entry, "runtime_s", "duration_s")
    return first(entry, what)


def fmax(m):
    """Worst (lowest) Fmax over the clocks, or a flat `fmax_mhz`."""
    v = first(m, "fmax_mhz")
    if v is not None:
        return v
    clocks = dig(m, "timing.clocks")
    vals = [num(dig(c, "fmax_mhz")) for c in clocks.values()] if isinstance(clocks, dict) else []
    vals = [v for v in vals if v is not None]
    return min(vals) if vals else None


@dataclass
class Metric:
    key: str
    label: str
    unit: str
    get: object  # metrics dict -> number | None


FPGA = [
    Metric("fpga-le", "Logic elements", "LEs", lambda m: first(m, "resources.logic_elements", "logic_elements")),
    Metric("fpga-regs", "Registers", "registers", lambda m: first(m, "resources.registers", "registers")),
    Metric("fpga-m9k", "M9K blocks", "blocks", lambda m: first(m, "resources.memory_blocks", "memory_blocks")),
    Metric("fpga-fmax", "Fmax, worst clock", "MHz", fmax),
    Metric("fpga-slack", "Worst setup slack", "ns", lambda m: first(m, "timing.worst_setup_slack_ns", "setup_slack_ns")),
]
ASIC = [
    Metric("asic-wns", "WNS", "ns", lambda m: first(m, "timing.wns_ns", "wns_ns")),
    Metric("asic-tns", "TNS", "ns", lambda m: first(m, "timing.tns_ns", "tns_ns")),
    Metric("asic-area", "Std-cell area", "µm²", lambda m: first(m, "area.stdcell_um2", "area_um2")),
    Metric("asic-cells", "Cells", "cells", lambda m: first(m, "cells")),
    Metric("asic-fanout", "Max fanout", "pins", lambda m: first(m, "max_fanout")),
    Metric("asic-depth", "Logic depth", "cells", lambda m: first(m, "logic_depth")),
]
# Merged regression coverage per IP (`./build regress --coverage`), percent.
COVERAGE = [
    Metric("cov-line", "Line coverage", "%", lambda m: first(m, "line.pct")),
    Metric("cov-toggle", "Toggle coverage", "%", lambda m: first(m, "toggle.pct")),
    Metric("cov-functional", "Functional coverage", "%", lambda m: first(m, "functional.pct")),
]
# Mutant score per IP, where a record carries `mutants` (wiki/tools/metrics/SPEC.md#trends).
MUTANTS = [Metric("mutant-score", "Mutant score", "%", trends.mutant_score)]

# Histograms: (key in `distributions`, title, unit shown, factor from stored unit, integer bins)
HISTOGRAMS = {
    "issues": [("issue_close_h", "Issue time to close", "days", 1 / 24, False),
               ("issue_first_response_h", "Issue time to first claim or response", "hours", 1, False)],
    "prs": [("pr_lead_h", "PR lead time, opened to merged", "hours", 1, False),
            ("pr_size_lines", "PR size", "lines changed", 1, False),
            ("pr_review_rounds", "Review rounds per merged PR", "REVIEW-VERDICT comments", 1, True)],
    "ci": [("ci_gate_duration_s", "ci-gate duration", "minutes", 1 / 60, False)],
    "nightly": [("nightly_duration_s", "Nightly duration", "minutes", 1 / 60, False),
                ("test_runtime_s", "Test runtime", "seconds", 1, False)],
    "code": [("file_lines", "Lines per file", "lines", 1, False),
             ("file_bytes", "File size", "KiB", 1 / 1024, False)],
}


# --------------------------------------------------------------- formatting
def fmt(v, unit: str = "") -> str:
    if v is None:
        return "–"
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
        s = f"{int(v):,}"
    elif abs(v) >= 100:
        s = f"{v:,.1f}"
    else:
        s = f"{v:.3g}"
    if s.startswith("-"):
        s = "−" + s[1:]
    return s + unit if unit == "%" else f"{s} {unit}".strip()


def fmt_delta(now, then) -> str:
    if now is None or then is None:
        return "–"
    d = round(now - then, 6)
    return "0" if d == 0 else ("+" if d > 0 else "−") + fmt(abs(d))


def md_escape(s: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>()#+!|~])", r"\\\1", s)


def script_json(obj) -> str:
    """JSON safe inside <script> and inside the site's href/src rewriter."""
    s = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    for ch, rep in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"), ("'", "\\u0027")):
        s = s.replace(ch, rep)
    return s


def empty(msg: str) -> str:
    return f'<p class="tracker-empty">{escape(msg)}</p>'


def table(head: list[str], rows: list[list[str]], cls: str = "tracker-table") -> str:
    """Cells are already-escaped HTML."""
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>'


GOOD = {"pass", "passed", "success", "green", "ok"}
BAD = {"fail", "failed", "failure", "red", "error", "timed_out", "cancelled"}


def status_of(result: str | None) -> str | None:
    if result is None:
        return None
    return "good" if result.lower() in GOOD else "critical" if result.lower() in BAD else "info"


def badge(level: str | None, label: str) -> str:
    """Status text with an icon, never colour alone."""
    if level is None:
        return "–"
    icon = {"good": "✓", "warning": "!", "critical": "✗"}.get(level, "•")
    return f'<span class="tracker-status {level}"><span aria-hidden="true">{icon}</span> {escape(label)}</span>'


def nice_step(span: float, k: int) -> float:
    """The 1-2-5 step that splits `span` into the bin count nearest `k`."""
    p = 10 ** math.floor(math.log10(span / k))
    return min((m * p for m in (1, 2, 5, 10)), key=lambda step: (abs(span / step - k), step))


def bins(values: list[float], integer: bool) -> tuple[list[str], list[int]]:
    """Equal-width bins on a 1-2-5 step, about Sturges' count (5..15). Small
    integer ranges get one bin per value. A long tail (max above twice the
    p90) is cut at the p90 into one final ">= edge" bin, so the body of a
    skewed duration distribution keeps its resolution."""
    lo, hi = min(values), max(values)
    if integer and hi - lo <= 20:
        edges = list(range(math.floor(lo), math.floor(hi) + 2))
        counts = [sum(1 for v in values if math.floor(v) == e) for e in edges[:-1]]
        return [fmt(e) for e in edges[:-1]], counts
    p90 = quantile(values, 0.9)
    top = p90 if p90 > 0 and hi > 2 * p90 else hi
    k = min(15, max(5, math.ceil(math.log2(len(values))) + 1))
    step = nice_step(top - lo, k) if top > lo else 1
    if integer:
        step = max(1, round(step))
    start = math.floor(lo / step) * step
    n = max(1, math.floor((top - start) / step) + 1)
    tail = top < hi
    counts = [0] * (n + tail)
    for v in values:
        counts[min(n - 1 + tail, int((v - start) // step))] += 1
    labels = [f"{fmt(start + i * step)}–{fmt(start + (i + 1) * step)}" for i in range(n)]
    if tail:
        labels.append(f"≥ {fmt(start + n * step)}")
    return labels, counts


# ------------------------------------------------------------------- pages
class Page:
    """Accumulates one page's Markdown and chart data."""

    def __init__(self, axis: list[tuple[str, str]], where: str = "index.md"):
        self.axis = axis
        self.where = f"wiki/tracker/{where}"
        self.charts: dict = {}
        self.parts: list[str] = []
        self.problems: list[str] = []

    def md(self, s: str) -> None:
        self.parts.append(s)

    def html(self, s: str) -> None:
        """One Markdown HTML block: a blank line would end it, so a record
        value carrying one is folded onto one line and reported."""
        if BLANK_LINE.search(s.replace("\r\n", "\n").replace("\r", "\n")):
            msg = (f"{self.where}: a record value contains a blank line; "
                   "rendered with its line breaks folded to spaces")
            if msg not in self.problems:  # once per page
                self.problems.append(msg)
            s = re.sub(r"\s*[\r\n]\s*", " ", s)
        self.parts.append(s)

    def _figure(self, cid: str, title: str, unit: str, note: str, table_html: str, summary: str) -> None:
        note_html = f'<p class="tracker-note">{escape(note)}</p>' if note else ""
        self.html(
            f'<figure class="tracker-chart" id="chart-{escape(cid)}">'
            f'<figcaption>{escape(title)} <span class="tracker-unit">({escape(unit)})</span></figcaption>'
            f'<div class="tracker-canvas"><canvas data-chart="{escape(cid)}" role="img" '
            f'aria-label="{escape(title)}, in {escape(unit)}; the data table follows"></canvas></div>'
            f"{note_html}<details><summary>{escape(summary)}</summary>{table_html}</details></figure>")

    def trend(self, cid: str, title: str, unit: str, series: list[dict], note: str = "",
              ymin: float | None = None) -> bool:
        """series: {label, color: 1..8 | status | None, points: [Point], get: data -> value,
        line: bool, shape}. The first SLOTS series with a colour are drawn; any
        other series with values is named in the note and kept in the data
        table. Draws nothing and returns False without values."""
        cols, extra = [], []
        for i, s in enumerate(series):
            pts = [(p, s["get"](p.data)) for p in s["points"]]
            pts = [(p, v) for p, v in pts if v is not None]
            if pts:
                (cols if i < SLOTS and s.get("color") is not None else extra).append((s, pts))
        if not cols:
            return False
        if extra:
            note = (note + " " if note else "") + (
                f"{len(extra)} more series not drawn (no colour slot left): "
                f"{', '.join(s['label'] for s, _ in extra)}; the data table has their values.")
        whole = all(float(v).is_integer() for _, pts in cols for _, v in pts)
        self.charts[cid] = {"kind": "trend", "unit": unit, "integer": whole, "series": [
            {"label": s["label"], "color": str(s["color"]), "line": s.get("line", True),
             "shape": s.get("shape", "circle"), "points": [[p.idx, v] for p, v in pts]}
            for s, pts in cols]}
        if ymin is not None:
            self.charts[cid]["ymin"] = ymin
        idxs = sorted({p.idx for _, pts in cols + extra for p, _ in pts})[-TABLE_ROWS:]
        look = [{p.idx: v for p, v in pts} for _, pts in cols + extra]
        rows = [[f"<code>{escape(self.axis[i][0])}</code>", escape(self.axis[i][1])]
                + [escape(fmt(m.get(i))) for m in look] for i in reversed(idxs)]
        head = ["Commit", "Date"] + [escape(s["label"]) for s, _ in cols] \
            + [escape(s["label"]) + " (not drawn)" for s, _ in extra]
        self._figure(cid, title, unit, note, table(head, rows), f"Data table (latest {len(idxs)} commits)")
        return True

    def histogram(self, cid: str, title: str, unit: str, values, factor: float, integer: bool,
                  source: str) -> bool:
        vals = [num(v) for v in values] if isinstance(values, list) else []
        vals = [v * factor for v in vals if v is not None]
        if not vals:
            return False
        labels, counts = bins(vals, integer)
        self.charts[cid] = {"kind": "hist", "unit": unit, "labels": labels, "counts": counts}
        note = (f"n = {len(vals)}, median {fmt(median(vals), unit)}, p90 {fmt(quantile(vals, 0.9), unit)}; "
                f"{source}.")
        rows = [[escape(lbl), escape(fmt(c))] for lbl, c in zip(labels, counts)]
        self._figure(cid, title, unit, note, table([f"Bin ({escape(unit)})", "Count"], rows), "Data table")
        return True

    def text(self) -> str:
        body = "\n\n".join(self.parts)
        if self.charts:
            data = {"axis": [list(a) for a in self.axis], "charts": self.charts}
            xs = [pt[0] for c in self.charts.values() if c["kind"] == "trend"
                  for ser in c["series"] for pt in ser["points"]]
            if xs:
                data["xrange"] = [min(xs), max(xs)]  # every trend on the page shares it
            body += '\n\n<script type="application/json" id="tracker-data">' + script_json(data) + "</script>"
        return body + "\n"


def at_or_before(points: list[Point], date: str) -> Point | None:
    found = None
    for p in points:
        if p.date <= date:
            found = p
    return found


def compare(rows: list[tuple[str, str, list[Point], object]]) -> str:
    """'Latest vs 7 and 30 days ago'; rows: (label, unit, points, data -> value).
    'N days ago' is the last point dated at or before the latest point's date
    minus N days."""
    out = []
    for label, unit, points, get in rows:
        if not points:
            continue
        latest = points[-1]
        now = get(latest.data)
        if now is None:
            continue
        cells = [escape(label), escape(fmt(now, unit)) + f' <span class="tracker-when">{escape(latest.date)}</span>']
        for days in (7, 30):
            day = (dt.date.fromisoformat(latest.date) - dt.timedelta(days=days)).isoformat()
            then = at_or_before(points, day)
            v = get(then.data) if then else None
            cells += [escape(fmt(v, unit)), escape(fmt_delta(now, v))]
        out.append(cells)
    if not out:
        return empty("No comparable metrics yet.")
    return table(["Metric", "Latest", "7 days ago", "Δ 7 d", "30 days ago", "Δ 30 d"], out)


def kinds_of(points: list[Point], getter) -> list[str]:
    found = {k for p in points for k in getter(p.data) if isinstance(k, str)}
    return [k for k in KINDS if k in found] + sorted(found - set(KINDS))


def kind_slot(kind: str) -> int | None:
    """The first SLOTS known kinds own a fixed slot; other kinds own none."""
    i = list(KINDS).index(kind) if kind in KINDS else SLOTS
    return i + 1 if i < SLOTS else None


def kind_series(points, kinds, get_kinds, what) -> list[dict]:
    """Kinds with a slot first; a kind left without one follows them, so
    `Page.trend` leaves it out of the plot and names it in the chart's note.
    A kind that is zero (or missing) at every point counts as absent: it
    gives up its fixed slot, and takes a free slot only after every kind
    with data has one."""
    def live(k):
        return any(kind_value(get_kinds(p.data).get(k), what) for p in points)
    present = [k for k in kinds if live(k)]
    out, rest = [], []
    spare = [i + 1 for i in range(SLOTS) if list(KINDS)[i] not in present]
    for k in present + [k for k in kinds if k not in present]:
        slot = (kind_slot(k) if k in present else None) or (spare.pop(0) if spare else None)
        (out if slot else rest).append({"label": KINDS.get(k, k), "color": slot, "points": points,
                                        "get": lambda r, k=k: kind_value(get_kinds(r).get(k), what)})
    return out + rest


def levels_of(points: list[Point], getter) -> list[str]:
    found = {k for p in points for k in getter(p.data) if isinstance(k, str)}
    return sorted(found, key=lambda k: (len(k), k))


def level_label(k: str) -> str:
    m = re.fullmatch(r"(?:level)?(\d+)", k)
    return f"L{m.group(1)}" if m else k


def ip_ids(d: Data) -> tuple[list[str], list[str]]:
    """IPs named anywhere in the records; unsafe ids become problems."""
    seen: set[str] = set()
    for p in d.commits + d.nightly:
        for m in (mapping(p.data, "code.ips", "code.by_ip"), mapping(p.data, "tests.ips", "tests.by_ip"),
                  flow_ips(p.data, "fpga"), flow_ips(p.data, "asic"), flow_ips(p.data, "arch"),
                  flow_ips(p.data, "coverage"), flow_ips(p.data, "mutants")):
            seen.update(k for k in m if isinstance(k, str))
    good = sorted(i for i in seen if IP_ID.match(i) and ".." not in i.split("/"))
    return good, [f"wiki/tracker/{DATA_DIR}: IP id {i!r} is not a safe path; skipped"
                  for i in sorted(seen - set(good))]


def status_series(points, get_value, get_result, label) -> list[dict]:
    """Pass and fail runs as two marker series: status colour plus shape."""
    def pick(want):
        return lambda r: get_value(r) if status_of(get_result(r)) == want else None
    return [{"label": f"{label} passed", "color": "good", "line": False, "shape": "circle",
             "points": points, "get": pick("good")},
            {"label": f"{label} failed", "color": "critical", "line": False, "shape": "crossRot",
             "points": points, "get": pick("critical")},
            {"label": f"{label} other", "color": "info", "line": False, "shape": "triangle",
             "points": points, "get": pick("info")}]


# ----------------------------------------------------------------- sections
def test_summary(r) -> tuple[float | None, float | None, str]:
    """Never infer passed tests from a missing failure count."""
    levels = test_levels(r)
    counts = [level_value(levels, k, "count") for k in levels]
    passed = [level_value(levels, k, "passed") for k in levels]
    failed = [level_value(levels, k, "failed") for k in levels]
    total = sum(counts) if counts and all(v is not None and v >= 0 for v in counts) else None
    passes = sum(passed) if passed and all(v is not None and v >= 0 for v in passed) else None
    bad = dig(r, "tests.passed") is False or any(v is not None and v > 0 for v in failed)
    bad |= status_of(text(r, "tests.status")) == "critical"
    bad |= (first(r, "tests.builds.failed") or 0) > 0
    complete = total is not None and total > 0 and counts == passed and all(v == 0 for v in failed)
    return passes, total, "critical" if bad else "good" if complete else "info"


def coverage_status(r) -> str:
    reported = dig(r, "coverage.passed")
    floors = [dig(m, "floors_passed") for m in flow_ips(r, "coverage").values()]
    if reported is False or any(v is False for v in floors) or status_of(text(r, "coverage.status")) == "critical":
        return "critical"
    if reported is True and floors and all(v is True for v in floors):
        return "good"
    return "info"


def duration_label(seconds) -> str:
    if seconds is None or seconds < 0:
        return "Duration unavailable"
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes}m {seconds:02d}s"


def snapshot(point: Point | None, label: str) -> str:
    if point is None:
        return f"{escape(label)}: no record yet"
    return f"{escape(label)}: {escape(point.date)} · <code>{escape(point.sha[:7])}</code>"


def flow_failed(m) -> bool:
    """Physical flows may report a failed step with its diagnostic."""
    result = text(m, "status") or ""
    if (status_of(result) == "critical" or result in {"timeout", "incomplete"}
            or re.match(r"^(?:incomplete:|[^:]+: (?:fail\b|timeout\b))", result)):
        return True
    steps = mapping(m, "flow.steps")
    return (text(m, "flow.lint") in {"fail", "timeout"}
            or any(text(step, "status") in {"fail", "timeout"} for step in steps.values()))


def attention_html(items, prefix="") -> str:
    return '<ul class="tracker-attention">' + ''.join(
        f'<li>{badge(state, message)} <a href="{prefix}{target}" aria-label="Details: {escape(message)}">Details →</a></li>'
        for _, state, message, target in items) + '</ul>'


def overview_section(p: Page, d: Data, ips: list[str], details: Page, alerts) -> None:
    night = d.nightly[-1].data if d.nightly else {}
    p.html('<nav class="tracker-links" aria-label="Metrics views">'
           '<a href="details/index.md">Detailed stats →</a>'
           '<a href="ip/index.md">Explore by IP →</a></nav>')
    p.html('<p class="tracker-asof">' + snapshot(d.nightly[-1] if d.nightly else None, "Nightly snapshot")
           + '</p>')
    res = nightly_result(night)
    passed, total, tests_state = test_summary(night)
    cov = coverage_status(night)
    # Commit results are fresher than the health summary captured during nightly.
    runs = [c for c in d.commits if status_of(ci_result(c.data)) in ("good", "critical")][-CI_WINDOW:]
    rate = 100 * sum(status_of(ci_result(c.data)) == "good" for c in runs) / len(runs) if runs else None
    count = len(runs)
    if rate is None:
        rate = scaled(first(h_ci_main(night), "pass_rate"), 100)
        count = first(h_ci_main(night), "runs")
        if count is None or count <= 0 or rate is None or not 0 <= rate <= 100:
            rate = None
    ci_state = "info" if rate is None else "good" if rate >= 95 else "warning" if rate >= 80 else "critical"
    ci_note = f"Last {fmt(count)} runs on main" if rate is not None else "No completed runs recorded"
    if runs:
        ci_note = f"Last {fmt(count)} recorded main commits · through {runs[-1].date}"
    elif rate is not None:
        ci_note += " · nightly snapshot"
    test_value = f"{fmt(passed)} / {fmt(total)}" if total is not None else "Unknown"
    test_note = "Tests passed · " + ", ".join(level_label(k) for k in sorted(test_levels(night)))
    if tests_state == "info":
        test_note = "Incomplete test evidence"
    cards = [
        ("Nightly", status_of(res) or "info", res or "Unknown", duration_label(nightly_duration(night))),
        ("Tests", tests_state, test_value, test_note),
        ("CI pass rate", ci_state, fmt(rate, "%") if rate is not None else "Unknown", ci_note),
        ("Coverage floors", cov, {"good": "Met", "critical": "Not met", "info": "Unknown"}[cov],
         "Recorded coverage requirements"),
    ]
    p.html('<div class="tracker-cards tracker-overview-cards">' + ''.join(
        f'<div class="tracker-card"><div class="tracker-card-label">{escape(label)}</div>'
        f'<div class="tracker-card-value">{badge(state, value)}</div>'
        f'<div class="tracker-card-note">{escape(note)}</div></div>'
        for label, state, value, note in cards) + '</div>')
    attention: list[tuple[int, str, str, str]] = []
    if status_of(res) == "critical":
        attention.append((0, "critical", f"Nightly result: {res}.", "details/index.md#nightly-and-tests"))
    for step, result in sorted(mapping(night, "meta.nightly.steps").items()):
        if isinstance(result, str) and status_of(result) == "critical":
            attention.append((0, "critical", f"Nightly {step}: {result}.", "details/index.md#nightly-and-tests"))
    if tests_state == "critical":
        attention.append((0, "critical", "Test or regression build failures recorded.", "details/index.md#nightly-and-tests"))
    if runs and status_of(ci_result(runs[-1].data)) == "critical":
        attention.append((0, "critical", f"Latest recorded main CI failed ({runs[-1].sha[:7]}, {runs[-1].date}).",
                          "details/index.md#ci"))
    if cov == "critical":
        attention.append((1, "critical", "Coverage requirements were not met.", "details/index.md#coverage"))
    for ip in ips:
        for name, getter in (("FPGA", fpga_ip), ("ASIC", asic_ip), ("Architecture model", arch_ip)):
            m = getter(night, ip)
            target = f"{IP_DIR}/{quote(ip)}/index.md#{name.lower().replace(' ', '-')}"
            result = text(m, "status")
            if flow_failed(m):
                attention.append((0, "critical", f"{ip}: {name} result is {result or 'failed'}.", target))
            if name == "ASIC":
                wns = ASIC[0].get(m)
                clocks = [first(c, "wns_ns") for c in mapping(m, "timing.clocks").values()]
                values = [v for v in [wns, *clocks] if v is not None]
                if values and min(values) < 0:
                    attention.append((1, "warning", f"{ip}: ASIC timing gap · WNS {fmt(min(values), 'ns')}.", target))
            if name == "FPGA" and (dig(m, "timing.met") is False or
                                    (FPGA[-1].get(m) is not None and FPGA[-1].get(m) < 0)):
                attention.append((1, "warning", f"{ip}: FPGA timing requirements were not met.", target))
    for drop in alerts.latest:
        attention.append((1, "warning", trends.describe(drop) + ".",
                          f"{IP_DIR}/{quote(drop.ip)}/index.md#drop-alerts"))
    unknown = [label for label, state, _, _ in cards if state == "info"]
    if unknown:
        attention.append((2, "info", "Incomplete evidence: " + ", ".join(unknown) + ".", "details/index.md"))
    p.md("## Needs attention")
    if attention:
        ordered = sorted(attention, key=lambda a: a[0])
        p.html(attention_html(ordered[:5]))
        details.md("## Needs attention")
        details.html(attention_html(ordered, "../"))
        if len(ordered) > 5:
            p.html(f'<p><a href="details/index.md#needs-attention">{len(ordered) - 5} more items · view all metrics →</a></p>')
    else:
        p.html('<p>No failures or timing gaps reported in this snapshot. '
               '<a href="details/index.md">Inspect the full evidence →</a></p>')
    p.md("## Recent runtimes")
    p.html('<p class="tracker-asof">' + snapshot(d.commits[-1] if d.commits else None, "Commit data through")
           + f" · latest {RECENT_COMMITS} main commits</p>")
    start = max(0, len(d.axis) - RECENT_COMMITS)
    p.html('<div class="tracker-runtime-grid">')
    for cid, label, points, duration, result in (
        ("ci-duration", "CI", d.commits, ci_duration, ci_result),
        ("nightly-duration", "Nightly", d.nightly, nightly_duration, nightly_result),
    ):
        if not p.trend(cid, f"{label} runtime", "minutes", status_series(
                [pt for pt in points if pt.idx >= start], lambda r, duration=duration: scaled(duration(r), 1 / 60),
                result, label)):
            p.html(empty(f"No recent {label} runtime recorded."))
    p.html('</div>')
    for chart in p.charts.values():
        chart["max_ticks"] = 4
    p.md("## Explore by IP")
    p.html('<p class="tracker-links">' + ' '.join(
        f'<a href="{IP_DIR}/{quote(ip)}/index.md">{escape(ip)} →</a>' for ip in ips) + '</p>'
        if ips else empty("No record names an IP yet."))


def health_section(p: Page, d: Data) -> None:
    p.md("## Repo health")
    night = d.nightly[-1].data if d.nightly else {}
    cards = []
    red, res = h_nightly_red(night), nightly_result(night)
    if res:
        cards.append(("Nightly", badge(status_of(res), res)))
    elif red is not None:
        cards.append(("Nightly", badge("critical" if red else "good",
                                       "nightly-red open" if red else "green")))
    main = h_ci_main(night)
    rate, n_runs = scaled(first(main, "pass_rate"), 100), first(main, "runs")
    ci_source = "nightly run snapshot"
    if rate is None:
        ci_source = "recorded commits"
        runs = [ci_result(c.data) for c in d.commits if status_of(ci_result(c.data)) in ("good", "critical")]
        runs = runs[-CI_WINDOW:]
        if runs:
            rate, n_runs = 100 * sum(status_of(r) == "good" for r in runs) / len(runs), len(runs)
    if rate is not None:
        lvl = "good" if rate >= 95 else "warning" if rate >= 80 else "critical"
        cards.append((f"ci-gate pass rate, last {fmt(n_runs)} runs on main ({ci_source})", badge(lvl, fmt(rate, "%"))))
    prs = h_open_prs(night)
    pr_ages = _ages_days(prs)
    if isinstance(prs, dict) and first(prs, "count") is not None:
        old = max(pr_ages) if pr_ages else None
        lvl = "good" if old is None or old <= STALE_DAYS else "warning"
        cards.append(("Open PRs", badge(lvl, fmt(first(prs, "count")) + (f", oldest {fmt(old, 'd')}" if old else ""))))
    issues = h_open_issues(night)
    if isinstance(issues, dict) and first(issues, "count") is not None:
        stale = first(issues, "stale")
        lvl = "good" if not stale else "warning"
        cards.append(("Open issues", badge(lvl, f"{fmt(first(issues, 'count'))}, {fmt(stale or 0)} stale")))
    rounds = h_rounds_median(night)
    if rounds is not None:
        cards.append(("Median review rounds", escape(fmt(rounds))))
    share = scaled(h_approve_share(night), 100)
    if share is not None:
        lvl = "good" if share >= 100 else "warning" if share >= 90 else "critical"
        cards.append(("Merged PRs with APPROVE at the merged head", badge(lvl, fmt(share, "%"))))
    code = d.commits[-1].data if d.commits else night
    for label, what in (("Lines of code", "loc"), ("Files", "files")):
        v = code_total(code, what)
        if v is not None:
            cards.append((label, escape(fmt(v))))
    if not cards:
        p.html(empty("No health data yet."))
        return
    latest = (d.commits or d.nightly)[-1]
    p.html('<p class="tracker-asof">' + snapshot(latest, "Code and commit data through") + "; "
           + snapshot(d.nightly[-1] if d.nightly else None, "Health snapshot") + ". "
           f"{len(d.commits)} commits and {len(d.nightly)} nightly records. "
           f"Stale: no activity for more than {fmt(first(issues, 'stale_days') or STALE_DAYS)} days.</p>")
    p.html('<div class="tracker-cards">' + "".join(
        f'<div class="tracker-card"><div class="tracker-card-label">{escape(k)}</div>'
        f'<div class="tracker-card-value">{v}</div></div>' for k, v in cards) + "</div>")
    if pr_ages:
        p.md("### Open pull requests")
        ages = sorted(pr_ages, reverse=True)
        p.html(table(["Open PR", "Age"], [
            [escape(str(i + 1)), badge("warning" if a > STALE_DAYS else "good", fmt(round(a, 1), "d"))]
            for i, a in enumerate(ages[:LIST_ROWS])]))
        if len(ages) > LIST_ROWS:
            p.html(f'<p class="tracker-note">Oldest {LIST_ROWS} of {len(ages)} shown.</p>')
    issue_ages = _ages_days(issues)
    if issue_ages:
        p.md("### Open issues")
        p.histogram("hist-open-issue-age", "Open issue age", "days", issue_ages, 1, False,
                    f"from the {d.nightly[-1].date} nightly")


def _ages_days(group) -> list[float]:
    ages = dig(group, "age_h")
    return [v / 24 for v in map(num, ages) if v is not None] if isinstance(ages, list) else []


def overview_compare(p: Page, d: Data) -> None:
    p.md("### Latest vs 7 and 30 days ago")
    c, n = d.commits, d.nightly
    p.html(compare([
        ("Lines of code", "", c or n, lambda r: code_total(r, "loc")),
        ("Files", "", c or n, lambda r: code_total(r, "files")),
        ("ci-gate duration", "min", c, lambda r: scaled(ci_duration(r), 1 / 60)),
        ("Open issues", "", n, issues_open),
        ("Open PRs", "", n, prs_open),
        ("PRs merged, last 7 days", "", n, prs_merged_7d),
        ("Tests", "", n, total_tests),
        ("Nightly duration", "min", n, lambda r: scaled(nightly_duration(r), 1 / 60)),
    ]))


def total_tests(r):
    vals = [level_value(test_levels(r), k, "count") for k in test_levels(r)]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def histograms(p: Page, d: Data, group: str) -> bool:
    src = d.nightly[-1] if d.nightly else None
    drew = False
    for key, title, unit, factor, integer in HISTOGRAMS[group]:
        point = src
        if point is None:
            continue
        where = f"from the {point.date} record at {d.axis[point.idx][0]}"
        drew |= p.histogram(f"hist-{key}", title, unit, dist(point.data, key), factor, integer, where)
    return drew


def code_section(p: Page, d: Data, ips: list[str]) -> None:
    p.md("## Code")
    pts = d.commits or d.nightly
    kinds = kinds_of(pts, code_kinds)
    drew = p.trend("loc-kind", "Lines of code by kind", "lines", kind_series(pts, kinds, code_kinds, "loc"))
    drew |= p.trend("files-kind", "Files by kind", "files", kind_series(pts, kinds, code_kinds, "files"))
    if not drew:
        drew = p.trend("loc-total", "Lines of code", "lines",
                       [{"label": "Total", "color": 1, "points": pts, "get": lambda r: code_total(r, "loc")}])
    drew |= p.trend("loc-ip", "Lines of code per IP", "lines",
                    [{"label": ip, "color": i + 1, "points": pts, "get": lambda r, ip=ip: code_ip_total(r, ip, "loc")}
                     for i, ip in enumerate(ips)])
    drew |= histograms(p, d, "code")
    if not drew:
        p.html(empty("No code metrics yet."))
    p.md("### File-type distribution")
    latest = pts[-1]
    shown = False
    for title, what in (("By extension", "by_ext"), ("By top-level directory", "by_dir")):
        rows = [(k, first(v, "files"), _loc(v)) for k, v in code_dist(latest.data, what).items()]
        rows = [r for r in rows if r[1] is not None]
        if not rows:
            continue
        shown = True
        total = sum(r[1] for r in rows) or 1
        rows.sort(key=lambda r: (-r[1], r[0]))
        p.html(f'<p class="tracker-subhead">{escape(title)}, commit <code>{escape(d.axis[latest.idx][0])}</code></p>')
        p.html(table(["Name", "Files", "Share of files", "Lines"], [
            [f"<code>{escape(k)}</code>", escape(fmt(f)),
             f'<span class="tracker-share"><span class="tracker-share-bar" style="width:{f / total * 100:.1f}%">'
             f"</span>{f / total * 100:.1f}%</span>", escape(fmt(loc))] for k, f, loc in rows]))
    if not shown:
        p.html(empty("No file distribution yet."))


def ci_section(p: Page, d: Data) -> None:
    p.md("## CI")
    drew = p.trend("ci-duration", "ci-gate duration and result per commit", "minutes",
                   status_series(d.commits, lambda r: scaled(ci_duration(r), 1 / 60), ci_result, "ci-gate"),
                   note="Marker shape and colour give the result: circle passed, cross failed, triangle other.")
    drew |= histograms(p, d, "ci")
    if not drew:
        p.html(empty("No ci-gate data yet."))


def nightly_section(p: Page, d: Data) -> None:
    p.md("## Nightly and tests")
    n = d.nightly
    drew = p.trend("nightly-duration", "Nightly duration and result", "minutes",
                   status_series(n, lambda r: scaled(nightly_duration(r), 1 / 60), nightly_result, "Nightly"),
                   note="Marker shape and colour give the result: circle passed, cross failed, triangle other.")
    drew |= tests_trends(p, n, test_levels, "")
    drew |= histograms(p, d, "nightly")
    if not drew:
        p.html(empty("No nightly or test data yet."))
    p.md("### Slowest tests")
    slow = test_slowest(n[-1].data) if n else None
    good = [t for t in slow if isinstance(t, dict) and num(t.get("runtime_s")) is not None] \
        if isinstance(slow, list) else []
    good.sort(key=lambda t: -num(t.get("runtime_s")))
    if good:
        p.html(f'<p class="tracker-subhead">From the {escape(n[-1].date)} nightly</p>')
        p.html(table(["IP", "Test", "Level", "Seed", "Runtime"], [
            [escape(str(t.get("ip", "–"))), f"<code>{escape(str(t.get('name', t.get('test', '–'))))}</code>",
             escape(level_label(str(t.get("level", "–")))), escape(str(t.get("seed", "–"))),
             escape(fmt(num(t.get("runtime_s")), "s"))] for t in good[:LIST_ROWS]]))
    else:
        p.html(empty("No slowest-test data yet."))


def tests_trends(p: Page, points: list[Point], getter, prefix: str) -> bool:
    lv = levels_of(points, getter)
    def series(what, k=1):
        return [{"label": level_label(lvl), "color": i + 1, "points": points,
                 "get": lambda r, lvl=lvl: scaled(level_value(getter(r), lvl, what), k)} for i, lvl in enumerate(lv)]
    drew = p.trend(f"{prefix}tests-count", "Tests per level", "tests", series("count"))
    drew |= p.trend(f"{prefix}tests-failed", "Failing tests per level", "tests", series("failed"))
    drew |= p.trend(f"{prefix}tests-runtime", "Test runtime per level", "minutes", series("runtime_s", 1 / 60))
    return drew


def flow_section(p: Page, d: Data) -> None:
    p.md("## Issues and pull requests")
    n = d.nightly
    drew = p.trend("issues-open", "Open issues and PRs", "items", [
        {"label": "Open issues", "color": 1, "points": n, "get": issues_open},
        {"label": "Open PRs", "color": 2, "points": n, "get": prs_open}])
    drew |= p.trend("issues-flow", "Issues opened and closed, trailing 7 days", "issues", [
        {"label": "Opened", "color": 1, "points": n, "get": lambda r: issues_window(r, "7d", "opened")},
        {"label": "Closed", "color": 2, "points": n, "get": lambda r: issues_window(r, "7d", "closed")}])
    drew |= p.trend("prs-merged", "PRs merged, trailing 7 days", "PRs", [
        {"label": "Merged", "color": 1, "points": n, "get": prs_merged_7d}])
    drew |= histograms(p, d, "issues")
    drew |= histograms(p, d, "prs")
    labels = issues_by_label(n[-1].data) if n else {}
    if labels:
        p.html(f'<p class="tracker-subhead">Open issues by label ({escape(n[-1].date)})</p>')
        p.html(table(["Label", "Open"], [[f"<code>{escape(k)}</code>", escape(fmt(num(v)))]
                                         for k, v in sorted(labels.items())]))
        drew = True
    if not drew:
        p.html(empty("No issue or pull request data yet."))


def physical_section(p: Page, d: Data, ips: list[str], name: str, metrics: list[Metric], getter,
                     more: tuple = ()) -> None:
    """One chart per metric with a series per IP; `more` adds (metrics,
    getter) groups from other record sections under the same heading."""
    p.md(f"## {name}")
    n = d.nightly
    drew = False
    for group, get in ((metrics, getter), *more):
        with_data = [ip for ip in ips if any(isinstance(get(r.data, ip), dict) for r in n)]
        for m in group:  # slots go to IPs with data in this flow, in sorted order
            drew |= p.trend(m.key, m.label, m.unit, [
                {"label": ip, "color": i + 1, "points": n,
                 "get": lambda r, ip=ip, m=m, g=get: m.get(g(r, ip)) if isinstance(g(r, ip), dict) else None}
                for i, ip in enumerate(with_data)])
    if not drew:
        p.html(empty(f"No {name} metrics yet."))


ARCH_STATS = {"min", "max", "mean", "median", "avg", "p50", "p90", "p99", "total"}


def metric_unit(metric: str) -> str:
    """The unit a metric name carries (the arch BKM: `latency_cycles_max` is
    in cycles, `throughput_bytes_per_cycle` in bytes per cycle): the words
    after the first, less a trailing statistic; `value` when none is left."""
    words = [w for w in metric.split("_") if w]
    while len(words) > 1 and words[-1].lower() in ARCH_STATS:
        words.pop()
    return " ".join(words[1:]) or "value"


def chart_id(prefix: str, name: str, taken: set[str]) -> str:
    """`prefix-name` with unsafe characters folded to `-`, unique on the page."""
    base = f"{prefix}-{re.sub(r'[^A-Za-z0-9_-]+', '-', name)}"
    cid, n = base, 2
    while cid in taken:
        cid, n = f"{base}-{n}", n + 1
    taken.add(cid)
    return cid


def arch_keys(points: list[Point], ips: list[str]) -> list[tuple[str, str, str]]:
    """Every (IP, scenario, target metric) in the records, sorted."""
    found = set()
    for p in points:
        for ip in ips:
            for sc, entry in arch_scenarios(p.data, ip).items():
                for m in mapping(entry, "targets"):
                    if isinstance(sc, str) and isinstance(m, str):
                        found.add((ip, sc, m))
    return sorted(found)


def arch_table(points: list[Point], ips: list[str], with_ip: bool):
    """(latest point with arch data, rows) of each IP and scenario's status
    and target values; rows are escaped HTML cells."""
    latest = next((p for p in reversed(points) if any(arch_scenarios(p.data, ip) for ip in ips)), None)
    if latest is None:
        return None, []
    rows = []
    for ip in ips:
        for sc, entry in sorted((k, v) for k, v in arch_scenarios(latest.data, ip).items() if isinstance(k, str)):
            vals = ", ".join(f"{m} = {fmt(num(v))}" for m, v in sorted(mapping(entry, "targets").items())
                             if isinstance(m, str))
            status = text(entry, "status")
            rows.append(([escape(ip)] if with_ip else []) + [
                f"<code>{escape(sc)}</code>", badge(status_of(status), status) if status else "–",
                escape(vals) or "–"])
    return latest, rows


def arch_trends(p: Page, points: list[Point], ips: list[str], prefix: str, per_ip: bool) -> bool:
    """One chart per target metric; a series per IP and scenario (per scenario
    on an IP page), with a fixed slot in sorted order."""
    keys = arch_keys(points, ips)
    taken: set[str] = set()
    drew = False
    for metric in sorted({m for _, _, m in keys}):
        series = [{"label": sc if per_ip else f"{ip} / {sc}", "color": i + 1, "points": points,
                   "get": lambda r, ip=ip, sc=sc, m=metric: arch_value(r, ip, sc, m)}
                  for i, (ip, sc) in enumerate((ip, sc) for ip, sc, m in keys if m == metric)]
        drew |= p.trend(chart_id(prefix, metric, taken), metric, metric_unit(metric), series)
    return drew


def arch_section(p: Page, d: Data, ips: list[str]) -> None:
    """Architecture-model perf: scenario status and target metrics."""
    p.md("## Architecture model")
    n = d.nightly
    latest, rows = arch_table(n, ips, with_ip=True)
    if rows:
        p.html(f'<p class="tracker-subhead">Perf scenarios, from the {escape(latest.date)} nightly</p>')
        p.html(table(["IP", "Scenario", "Status", "Target metrics"], rows))
    with_data = [ip for ip in ips if any(arch_scenarios(r.data, ip) for r in n)]
    drew = p.trend("arch-failing", "Perf scenarios not passing", "scenarios", [
        {"label": ip, "color": i + 1, "points": n,
         "get": lambda r, ip=ip: (lambda scs: sum(text(e, "status") != "pass" for e in scs.values())
                                  if scs else None)(arch_scenarios(r, ip))}
        for i, ip in enumerate(with_data)], ymin=0)
    drew |= arch_trends(p, n, ips, "arch", per_ip=False)
    if not rows and not drew:
        p.html(empty("No architecture-model metrics yet."))


def drop_thresholds(cfg_dir: Path | None) -> tuple[dict | None, list[str]]:
    """`drop_alerts` of `cfg_dir/metrics.yaml`; None (alerts off) without
    that file, and a problem when it does not load."""
    if cfg_dir is None or not (cfg_dir / "metrics.yaml").is_file():
        return None, []
    from metrics.config import MetricsError, load as load_cfg  # PyYAML only when configured
    try:
        return load_cfg(cfg_dir).drop_alerts, []
    except MetricsError as exc:
        return None, [f"{exc}; drop alerts are off"]


@dataclass
class Alerts:
    """Drops beyond their thresholds over the nightly records (`trends`)."""
    thresholds: dict | None
    every: list = field(default_factory=list)  # trends.Drop, history order
    last: int = -1  # index of the latest nightly record

    @classmethod
    def of(cls, d: Data, ips: list[str], thresholds: dict | None) -> "Alerts":
        history = [x.data for x in d.nightly]
        return cls(thresholds, trends.drops(history, thresholds, ips) if thresholds else [], len(history) - 1)

    @property
    def latest(self) -> list:
        return [x for x in self.every if x.at == self.last]


def drops_section(p: Page, d: Data, alerts: Alerts, ip: str | None = None) -> None:
    """Every drop beyond its threshold, newest first; the latest record's
    drops are the alerts (Needs attention, nightly summary)."""
    p.md("## Drop alerts")
    if alerts.thresholds is None:
        p.html(empty("Drop alerts are off: no drop_alerts thresholds are configured."))
        return
    rule = ("A per-IP series drops when its value in a nightly record is lower than its previous "
            "recorded value by more than its threshold; a record without the value is a gap. "
            "Drops in the latest record are alerts.")
    p.html(f'<p class="tracker-note">{escape(rule)} Thresholds: ' + escape("; ".join(
        f"{s.label} {trends.fmt(alerts.thresholds[s.key], 'pts' if s.unit == '%' else s.unit)}"
        for s in trends.SERIES)) + ".</p>")
    rows = []
    for x in sorted(alerts.every, key=lambda x: (-x.at, x.ip, trends.KEYS.index(x.series.key))):
        if ip is not None and x.ip != ip:
            continue
        u = x.series.unit
        du = "pts" if u == "%" else u
        prev, at = d.nightly[x.before], d.nightly[x.at]
        rows.append([escape(at.date) + (" " + badge("warning", "alert") if x.at == alerts.last else "")]
                    + ([] if ip else [f'<a href="../{IP_DIR}/{quote(x.ip)}/index.md">{escape(x.ip)}</a>'])
                    + [escape(x.series.label),
                       escape(trends.fmt(x.previous, u)) + f' <span class="tracker-when">{escape(prev.date)}</span>',
                       escape(trends.fmt(x.value, u)), escape(trends.fmt(x.change, du)),
                       escape(trends.fmt(x.threshold, du))])
    if not rows:
        p.html(empty("No series has dropped beyond its threshold."))
        return
    p.html(table(["When", *([] if ip else ["IP"]), "Series", "Previous", "Value", "Change", "Threshold"], rows))


def ip_page(ip: str, d: Data, alerts: Alerts) -> Page:
    p = Page(d.axis, f"{IP_DIR}/{ip}/index.md")
    up = "../" * (ip.count("/") + 1)
    p.md(f"# {md_escape(ip)} metrics")
    p.html(f'<p>Per-IP view of the <a href="{up}../index.md">metrics tracker</a>; '
           f'<a href="{up}index.md">all IPs</a>.</p>')
    c = [x for x in d.commits if code_ip(x.data, ip)]
    n = [x for x in d.nightly if test_ip_levels(x.data, ip) or fpga_ip(x.data, ip) or asic_ip(x.data, ip)
         or code_ip(x.data, ip) or arch_ip(x.data, ip) or coverage_ip(x.data, ip) or mutants_ip(x.data, ip)]
    if not c and not n:
        p.html(empty("No data yet for this IP."))
        return p
    code_pts = c or n
    rows = [("Lines of code", "", code_pts, lambda r: code_ip_total(r, ip, "loc")),
            ("Files", "", code_pts, lambda r: code_ip_total(r, ip, "files")),
            ("Tests", "", n, lambda r: (lambda v: sum(v) if v else None)(
                [x for x in (level_value(test_ip_levels(r, ip), k, "count") for k in test_ip_levels(r, ip))
                 if x is not None]))]
    for name, metrics, getter in (("FPGA", FPGA, fpga_ip), ("ASIC", ASIC, asic_ip)):
        rows += [(f"{name} {m.label}", m.unit, n,
                  lambda r, m=m, g=getter: m.get(g(r, ip)) if isinstance(g(r, ip), dict) else None) for m in metrics]
    rows += [(f"Arch {sc} {m}", "", n, lambda r, sc=sc, m=m: arch_value(r, ip, sc, m))
             for _, sc, m in arch_keys(n, [ip])]
    p.md("## Latest vs 7 and 30 days ago")
    p.html(compare(rows))
    p.md("## Code")
    get_kinds = lambda r: code_ip_kinds(r, ip)  # noqa: E731
    if not p.trend("ip-loc", "Lines of code by kind", "lines",
                   kind_series(code_pts, kinds_of(code_pts, get_kinds), get_kinds, "loc")):
        if not p.trend("ip-loc", "Lines of code", "lines", [
                {"label": ip, "color": 1, "points": code_pts, "get": lambda r: code_ip_total(r, ip, "loc")}]):
            p.html(empty("No code metrics yet for this IP."))
    p.md("## Tests")
    if not tests_trends(p, n, lambda r: test_ip_levels(r, ip), "ip-"):
        p.html(empty("No test metrics yet for this IP."))
    for name, metrics, getter in (("FPGA", FPGA, fpga_ip), ("ASIC", ASIC, asic_ip)):
        p.md(f"## {name}")
        drew = False
        for m in metrics:
            drew |= p.trend(f"ip-{m.key}", m.label, m.unit, [
                {"label": ip, "color": 1, "points": n,
                 "get": lambda r, m=m, g=getter: m.get(g(r, ip)) if isinstance(g(r, ip), dict) else None}])
        if name == "ASIC":
            drew |= asic_clock_trends(p, n, ip)
        if not drew:
            p.html(empty(f"No {name} metrics yet for this IP."))
    ip_coverage(p, n, ip)
    drops_section(p, d, alerts, ip)
    ip_arch(p, n, ip)
    return p


def asic_clocks(r, ip) -> dict:
    """`{clock: {setup_ws_ns, wns_ns, tns_ns}}` of the IP's ASIC record."""
    return mapping(asic_ip(r, ip), "timing.clocks")


def asic_clock_trends(p: Page, n: list[Point], ip: str) -> bool:
    """WNS and TNS charts with a series per clock (sorted), when the records
    carry per-clock slacks."""
    clocks = sorted({c for x in n for c in asic_clocks(x.data, ip)})
    drew = False
    for key, label, field in (("asic-clock-wns", "WNS by clock", "wns_ns"),
                              ("asic-clock-tns", "TNS by clock", "tns_ns")):
        drew |= p.trend(f"ip-{key}", label, "ns", [
            {"label": c, "color": i + 1, "points": n,
             "get": lambda r, c=c, f=field: num(dig(asic_clocks(r, ip).get(c), f))}
            for i, c in enumerate(clocks)])
    return drew


def ip_arch(p: Page, n: list[Point], ip: str) -> None:
    """The IP's perf scenarios: latest status, and a chart per target metric."""
    p.md("## Architecture model")
    latest, rows = arch_table(n, [ip], with_ip=False)
    if rows:
        p.html(f'<p class="tracker-subhead">Perf scenarios, from the {escape(latest.date)} nightly</p>')
        p.html(table(["Scenario", "Status", "Target metrics"], rows))
    if not arch_trends(p, n, [ip], "ip-arch", per_ip=True) and not rows:
        p.html(empty("No architecture-model metrics yet for this IP."))


def ip_coverage(p: Page, n: list[Point], ip: str) -> None:
    """One chart of the IP's line, toggle and functional coverage, and its
    mutant score where recorded."""
    p.md("## Coverage")
    drew = p.trend("ip-coverage", "Coverage", "%", [
        {"label": m.label, "color": i + 1, "points": n,
         "get": lambda r, m=m: m.get(coverage_ip(r, ip)) if isinstance(coverage_ip(r, ip), dict) else None}
        for i, m in enumerate(COVERAGE)])
    drew |= p.trend("ip-mutant-score", "Mutant score", "%", [
        {"label": ip, "color": 1, "points": n,
         "get": lambda r: MUTANTS[0].get(mutants_ip(r, ip)) if isinstance(mutants_ip(r, ip), dict) else None}])
    if not drew:
        p.html(empty("No coverage metrics yet for this IP."))


def ip_index(ips: list[str], d: Data) -> Page:
    p = Page(d.axis, f"{IP_DIR}/index.md")
    p.md("# Per-IP metrics")
    p.html('<p>One page per IP named in the nightly records; back to the '
           '<a href="../index.md">metrics tracker</a>.</p>')
    if not ips:
        p.html(empty("No data yet: no record names an IP."))
        return p
    code = d.commits[-1].data if d.commits else d.nightly[-1].data
    night = d.nightly[-1].data if d.nightly else {}
    rows = []
    for ip in ips:
        fp, asic = fpga_ip(night, ip), asic_ip(night, ip)
        fp, asic = (fp if isinstance(fp, dict) else None), (asic if isinstance(asic, dict) else None)
        arch = text(arch_ip(night, ip), "status")
        rows.append([f'<a href="{quote(ip)}/index.md">{escape(ip)}</a>',
                     escape(fmt(code_ip_total(code, ip, "loc"))),
                     escape(fmt(FPGA[0].get(fp) if fp else None)),
                     escape(fmt(FPGA[3].get(fp) if fp else None, "MHz")),
                     escape(fmt(ASIC[0].get(asic) if asic else None, "ns")),
                     escape(fmt(ASIC[2].get(asic) if asic else None, "µm²")),
                     badge(status_of(arch), arch) if arch else "–"])
    p.html(table(["IP", "Lines of code", "FPGA LEs", "Fmax", "ASIC WNS", "Std-cell area", "Arch model"], rows))
    return p


def render(data_dir: Path, cfg_dir: Path | None = None) -> tuple[str, dict[str, str], list[str]]:
    """Return (Markdown appended to the tracker landing page,
    {path under wiki/tracker/: generated page Markdown}, problems).
    `cfg_dir` holds `metrics.yaml`, whose `drop_alerts` set the drop alerts."""
    d = load(data_dir)
    ips, bad = ip_ids(d)
    thresholds, cfg_problems = drop_thresholds(cfg_dir)
    problems = d.problems + bad + cfg_problems
    alerts = Alerts.of(d, ips, thresholds)
    p = Page(d.axis, "details/index.md")
    p.md("# Detailed stats")
    p.html('<p><a href="../index.md">← Status overview</a> · '
           '<a href="../ip/index.md">Per-IP metrics</a></p>')
    p.html('<p>Full history, comparisons and distributions. Trends share the main-commit axis; '
           'each chart includes a data table. Code and CI are recorded per commit; tests, coverage, '
           'physical results and repository health are nightly snapshots.</p>')
    p.html('<p>The nightly adds records through a data-only pull request, and the site rebuilds '
           'after it merges. Updates can lag by a day. Records contain public counts and durations; '
           'only the nightly bot writes them. '
           '<a href="../../tools/wiki_site/SPEC.md#tracker">Data fields and interpretation</a>.</p>')
    overview = Page(d.axis)
    generated = {"details/index.md": p, f"{IP_DIR}/index.md": ip_index(ips, d)}

    def done():
        pages = {path: g.text() for path, g in generated.items()}
        return overview.text(), pages, problems + [x for g in [overview, *generated.values()] for x in g.problems]
    if d.empty:
        overview.html(empty("No data yet: the nightly job has not published a snapshot."))
        p.md("## Repo health")
        p.html(empty("No data yet: the nightly job adds records to wiki/tracker/data/, "
                     "and this page then shows charts and tables."))
        return done()
    overview_section(overview, d, ips, p, alerts)
    health_section(p, d)
    overview_compare(p, d)
    code_section(p, d, ips)
    ci_section(p, d)
    nightly_section(p, d)
    physical_section(p, d, ips, "Coverage", COVERAGE, coverage_ip, more=((MUTANTS, mutants_ip),))
    flow_section(p, d)
    physical_section(p, d, ips, "FPGA", FPGA, fpga_ip)
    physical_section(p, d, ips, "ASIC", ASIC, asic_ip)
    drops_section(p, d, alerts)
    arch_section(p, d, ips)
    p.md("## Per-IP pages")
    p.html("<ul>" + "".join(f'<li><a href="../{IP_DIR}/{quote(ip)}/index.md">{escape(ip)}</a></li>' for ip in ips)
           + "</ul>" if ips else empty("No record names an IP yet."))
    for ip in ips:
        generated[f"{IP_DIR}/{ip}/index.md"] = ip_page(ip, d, alerts)
    return done()
