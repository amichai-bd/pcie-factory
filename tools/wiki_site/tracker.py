"""Render the metrics tracker from the records in `wiki/tracker/data/`.

`wiki_site.py` calls `render()` when `wiki/tracker/index.md` exists. It reads
`commits.jsonl` (one line per commit on main) and the nightly `<date>.json`
records, and returns Markdown (headings plus raw HTML blocks) appended to
that landing page, plus one generated page per IP. Every trend shares one
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
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from urllib.parse import quote

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
STALE_DAYS = 7  # an open issue with no activity for longer is stale
CI_WINDOW = 20  # ci-gate pass rate window, in runs on main

# Code kinds (cfg/metrics.yaml keys) in their fixed colour order; unknown
# kinds follow, sorted.
KINDS = {
    "rtl_sv": "RTL SV", "dv_sv": "DV SV", "dv_py": "DV Python", "tools_py": "Tools Python",
    "wiki_md": "Markdown", "config": "Config", "workflows": "Workflows", "skills": "Skills",
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
        if "\n\n" in s or "\r\n\r\n" in s:
            self.problems.append(f"{self.where}: a record value contains a blank line; "
                                 "rendered with its line breaks folded to spaces")
            s = re.sub(r"\s*\n\s*", " ", s)
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
              hidden: int = 0) -> bool:
        """series: {label, color: 1..8 | status, points: [Point], get: data -> value,
        line: bool, shape}. Draws nothing and returns False without values."""
        cols = []
        for s in series[:SLOTS]:
            pts = [(p, s["get"](p.data)) for p in s["points"]]
            pts = [(p, v) for p, v in pts if v is not None]
            if pts:
                cols.append((s, pts))
        if not cols:
            return False
        hidden += max(0, len(series) - SLOTS)
        if hidden:
            note = (note + " " if note else "") + f"{hidden} more series not drawn; the per-IP pages have them."
        whole = all(float(v).is_integer() for _, pts in cols for _, v in pts)
        self.charts[cid] = {"kind": "trend", "unit": unit, "integer": whole, "series": [
            {"label": s["label"], "color": str(s["color"]), "line": s.get("line", True),
             "shape": s.get("shape", "circle"), "points": [[p.idx, v] for p, v in pts]}
            for s, pts in cols]}
        idxs = sorted({p.idx for _, pts in cols for p, _ in pts})[-TABLE_ROWS:]
        look = [{p.idx: v for p, v in pts} for _, pts in cols]
        rows = [[f"<code>{escape(self.axis[i][0])}</code>", escape(self.axis[i][1])]
                + [escape(fmt(m.get(i))) for m in look] for i in reversed(idxs)]
        head = ["Commit", "Date"] + [escape(s["label"]) for s, _ in cols]
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
    """Known kinds own a fixed slot; unknown kinds get none (folded out)."""
    return list(KINDS).index(kind) + 1 if kind in KINDS else None


def kind_series(points, kinds, get_kinds, what) -> list[dict]:
    out = []
    spare = [i + 1 for i in range(SLOTS) if list(KINDS)[i] not in kinds]
    for k in kinds:
        slot = kind_slot(k) or (spare.pop(0) if spare else None)
        if slot is None:
            continue
        out.append({"label": KINDS.get(k, k), "color": slot, "points": points,
                    "get": lambda r, k=k: kind_value(get_kinds(r).get(k), what)})
    return out


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
                  flow_ips(p.data, "fpga"), flow_ips(p.data, "asic")):
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
def health_section(p: Page, d: Data) -> None:
    p.md("## Repo health")
    night = d.nightly[-1].data if d.nightly else {}
    cards = []
    red, res = h_nightly_red(night), nightly_result(night)
    if red is not None:
        cards.append(("Nightly", badge("critical" if red else "good",
                                       "nightly-red open" if red else "green")))
    elif res:
        cards.append(("Nightly", badge(status_of(res), res)))
    main = h_ci_main(night)
    rate, n_runs = scaled(first(main, "pass_rate"), 100), first(main, "runs")
    if rate is None:
        runs = [ci_result(c.data) for c in d.commits if status_of(ci_result(c.data)) in ("good", "critical")]
        runs = runs[-CI_WINDOW:]
        if runs:
            rate, n_runs = 100 * sum(status_of(r) == "good" for r in runs) / len(runs), len(runs)
    if rate is not None:
        lvl = "good" if rate >= 95 else "warning" if rate >= 80 else "critical"
        cards.append((f"ci-gate pass rate, last {fmt(n_runs)} runs on main", badge(lvl, fmt(rate, "%"))))
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
    p.html(f'<p class="tracker-asof">As of commit <code>{escape(d.axis[latest.idx][0])}</code> '
           f"({escape(latest.date)}); {len(d.commits)} commits and {len(d.nightly)} nightly records. "
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
                     for i, ip in enumerate(ips[:SLOTS])], hidden=max(0, len(ips) - SLOTS))
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


def physical_section(p: Page, d: Data, ips: list[str], name: str, metrics: list[Metric], getter) -> None:
    p.md(f"## {name}")
    n = d.nightly
    with_data = [ip for ip in ips if any(isinstance(getter(r.data, ip), dict) for r in n)]
    drew = False
    for m in metrics:  # slots go to IPs with data in this flow, in sorted order
        drew |= p.trend(m.key, m.label, m.unit, [
            {"label": ip, "color": i + 1, "points": n,
             "get": lambda r, ip=ip, m=m: m.get(getter(r, ip)) if isinstance(getter(r, ip), dict) else None}
            for i, ip in enumerate(with_data)])
    if not drew:
        p.html(empty(f"No {name} metrics yet."))


def ip_page(ip: str, d: Data) -> Page:
    p = Page(d.axis, f"{IP_DIR}/{ip}/index.md")
    up = "../" * (ip.count("/") + 1)
    p.md(f"# {md_escape(ip)} metrics")
    p.html(f'<p>Per-IP view of the <a href="{up}../index.md">metrics tracker</a>; '
           f'<a href="{up}index.md">all IPs</a>.</p>')
    c = [x for x in d.commits if code_ip(x.data, ip)]
    n = [x for x in d.nightly if test_ip_levels(x.data, ip) or fpga_ip(x.data, ip) or asic_ip(x.data, ip)
         or code_ip(x.data, ip)]
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
        if not drew:
            p.html(empty(f"No {name} metrics yet for this IP."))
    return p


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
        rows.append([f'<a href="{quote(ip)}/index.md">{escape(ip)}</a>',
                     escape(fmt(code_ip_total(code, ip, "loc"))),
                     escape(fmt(FPGA[0].get(fp) if fp else None)),
                     escape(fmt(FPGA[3].get(fp) if fp else None, "MHz")),
                     escape(fmt(ASIC[0].get(asic) if asic else None, "ns")),
                     escape(fmt(ASIC[2].get(asic) if asic else None, "µm²"))])
    p.html(table(["IP", "Lines of code", "FPGA LEs", "Fmax", "ASIC WNS", "Std-cell area"], rows))
    return p


def render(data_dir: Path) -> tuple[str, dict[str, str], list[str]]:
    """Return (Markdown appended to the tracker landing page,
    {path under wiki/tracker/: generated page Markdown}, problems)."""
    d = load(data_dir)
    ips, bad = ip_ids(d)
    problems = d.problems + bad
    generated = {f"{IP_DIR}/index.md": ip_index(ips, d)}
    p = Page(d.axis)

    def done():
        pages = {path: g.text() for path, g in generated.items()}
        return p.text(), pages, problems + [x for g in [p, *generated.values()] for x in g.problems]
    if d.empty:
        p.md("## Repo health")
        p.html(empty("No data yet: the nightly job adds records to wiki/tracker/data/, "
                     "and this page then shows charts and tables."))
        return done()
    health_section(p, d)
    overview_compare(p, d)
    code_section(p, d, ips)
    ci_section(p, d)
    nightly_section(p, d)
    flow_section(p, d)
    physical_section(p, d, ips, "FPGA", FPGA, fpga_ip)
    physical_section(p, d, ips, "ASIC", ASIC, asic_ip)
    p.md("## Per-IP pages")
    p.html("<ul>" + "".join(f'<li><a href="{IP_DIR}/{quote(ip)}/index.md">{escape(ip)}</a></li>' for ip in ips)
           + "</ul>" if ips else empty("No record names an IP yet."))
    for ip in ips:
        generated[f"{IP_DIR}/{ip}/index.md"] = ip_page(ip, d)
    return done()
