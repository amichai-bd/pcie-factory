"""Unit tests for the metrics tracker (tools/wiki_site/tracker.py) and its
integration in the site build. Records are synthetic (fixtures/tracker/)."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tracker  # noqa: E402
import wiki_site  # noqa: E402

SHA = "0123abcd"
URL = "https://example.invalid/owner/repo"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tracker"
LANDING = "# Metrics tracker\n\nIntro text.\n"


def tree(root: Path, data: dict[str, str] | None = None, fixtures: bool = False) -> Path:
    files = {"wiki/index.md": "# Home\n\nSee [tracker](tracker/index.md).\n",
             "wiki/tracker/index.md": LANDING, "wiki/tracker/data/.gitkeep": "",
             "wiki/tools/wiki_site/SPEC.md": "# Wiki site\n\n## Tracker\n"}
    for rel, txt in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(txt, encoding="utf-8")
    if fixtures:
        for f in FIXTURES.iterdir():
            shutil.copy(f, root / "wiki/tracker/data" / f.name)
    for name, txt in (data or {}).items():
        (root / "wiki/tracker/data" / name).write_text(txt, encoding="utf-8")
    return root


def build(root: Path):
    out = root / "workdir" / "site"
    problems, stats = wiki_site.build(root, out, SHA, URL)
    return problems, out


def chart_data(html: str) -> dict:
    m = re.search(r'<script type="application/json" id="tracker-data">(.*?)</script>', html, re.S)
    assert m, "no tracker-data block"
    return json.loads(m.group(1))


def sha(n: int) -> str:
    return f"{n:040x}"


def commit(n: int, date: str, **extra) -> str:
    return json.dumps({"schema_version": 1, "sha": sha(n), "date": date + "T00:00:00Z", **extra}) + "\n"


def nightly(date: str, n: int, result: str | None = None, duration: float | None = None, **extra) -> str:
    meta = {"date": date, "commit": sha(n)}
    if result:
        meta["nightly"] = {"result": result, "duration_s": duration}
    return json.dumps({"schema_version": 1, "meta": meta, **extra})


# ------------------------------------------------------------ with data
def test_fixture_data_renders_every_section(tmp_path):
    problems, out = build(tree(tmp_path, fixtures=True))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    assert "Intro text." in (out / "tracker/index.html").read_text()
    for heading in ("Repo health", "Latest vs 7 and 30 days ago", "Code", "File-type distribution", "CI",
                    "Nightly and tests", "Slowest tests", "Issues and pull requests", "FPGA", "ASIC",
                    "Per-IP pages", "Open pull requests", "Open issues"):
        assert f">{heading}<a" in html, heading
    data = chart_data(html)
    assert len(data["axis"]) == 36 and all(len(a[0]) == 7 for a in data["axis"])
    charts = data["charts"]
    for cid in ("loc-kind", "files-kind", "loc-ip", "ci-duration", "nightly-duration", "tests-count",
                "tests-runtime", "issues-open", "issues-flow", "prs-merged", "fpga-le", "fpga-regs", "fpga-m9k",
                "fpga-fmax", "fpga-slack", "asic-wns", "asic-tns", "asic-area", "asic-cells",
                "asic-fanout", "asic-depth"):
        assert charts[cid]["kind"] == "trend", cid
        assert f'<canvas data-chart="{cid}"' in html
    for key in ("issue_close_h", "issue_first_response_h", "pr_lead_h", "pr_size_lines", "pr_review_rounds",
                "ci_gate_duration_s", "nightly_duration_s", "test_runtime_s", "file_lines", "file_bytes",
                "open-issue-age"):
        spec = charts[f"hist-{key}"]
        assert spec["kind"] == "hist" and len(spec["labels"]) == len(spec["counts"]) and sum(spec["counts"]) > 0
    # nightly points sit on the commit axis at the commit they ran on
    first_night = json.loads((FIXTURES / "2026-09-03.json").read_text())
    idx = [i for i, a in enumerate(data["axis"]) if first_night["meta"]["commit"].startswith(a[0])][0]
    assert charts["nightly-duration"]["series"][0]["points"][0][0] == idx
    # ci-gate pass and fail runs are separate marker series: colour plus shape
    series = {s["label"]: s for s in charts["ci-duration"]["series"]}
    assert series["ci-gate failed"]["color"] == "critical" and series["ci-gate failed"]["shape"] == "crossRot"
    assert not series["ci-gate passed"]["line"]
    # code kinds keep a fixed colour slot
    kinds = {s["label"]: s["color"] for s in charts["loc-kind"]["series"]}
    assert kinds["RTL SV"] == "1" and kinds["Skills"] == "8"
    assert "Data table (latest 30 commits)" in html
    # health cards: status carries an icon and a label
    assert 'class="tracker-status' in html and "5 stale" in html
    assert "ci-gate pass rate, last 20 runs on main" in html and "Merged PRs with APPROVE" in html
    assert html.count("<tr><td>") >= 4  # open PR ages
    # levels and slowest tests from the collector's shape
    assert [s["label"] for s in charts["tests-count"]["series"]] == ["L0", "L1", "L2"]
    assert "<code>test_case_" in html


def test_compare_uses_points_at_least_7_and_30_days_back(tmp_path):
    lines = "".join(commit(i, f"2026-09-{d:02d}", code={"total": {"loc": 100 * d}})
                    for i, d in enumerate((1, 2, 10, 20, 30)))
    problems, out = build(tree(tmp_path, {"commits.jsonl": lines}))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    row = re.search(r"<tr><td>Lines of code</td>(.*?)</tr>", html).group(1)
    cells = re.findall(r"<td>(.*?)</td>", row)
    assert cells[0].startswith("3,000") and "2026-09-30" in cells[0]
    assert cells[1:] == ["2,000", "+1,000", "–", "–"]  # 09-23 -> 09-20; nothing on or before 08-31


def test_per_ip_pages_and_sidebar(tmp_path):
    problems, out = build(tree(tmp_path, fixtures=True))
    assert problems == []
    for ip in ("alpha", "beta", "beta/core"):
        page = (out / f"tracker/ip/{ip}/index.html").read_text()
        assert "<h1" in page and f"{ip} metrics" in page
        assert chart_data(page)["charts"]["ip-loc"]["series"]
    core = (out / "tracker/ip/beta/core/index.html").read_text()
    assert 'href="../../../index.html">metrics tracker</a>' in core
    assert 'href="../../index.html">all IPs</a>' in core
    assert "No FPGA metrics yet for this IP." in core  # fixtures give beta/core no FPGA data
    alpha = (out / "tracker/ip/alpha/index.html").read_text()
    assert "fpga-le" in alpha and "asic-wns" in alpha
    index = (out / "tracker/ip/index.html").read_text()
    assert 'href="beta/core/index.html">beta/core</a>' in index
    landing = (out / "tracker/index.html").read_text()
    assert 'href="ip/beta/core/index.html">beta/core →</a>' in landing
    # sidebar: Per-IP metrics folder under the tracker, every IP page listed
    site = wiki_site.Site(tmp_path, SHA, URL)
    nav = site.nav_pages()
    assert nav.index("wiki/tracker/index.md") < nav.index("wiki/tracker/ip/index.md")
    assert {"wiki/tracker/ip/alpha/index.md", "wiki/tracker/ip/beta/core/index.md"} <= set(nav)


def test_ip_page_asic_per_clock_trends(tmp_path):
    rec = json.loads((FIXTURES / "2026-10-06.json").read_text())
    rec["meta"]["date"] = "2026-10-09"
    rec["asic"]["ips"]["beta"]["timing"]["clocks"] = {
        "rx_side": {"setup_ws_ns": 1.5, "wns_ns": 0.0, "tns_ns": 0.0},
        "core": {"setup_ws_ns": -0.25, "wns_ns": -0.25, "tns_ns": -3.0}}
    problems, out = build(tree(tmp_path, {"2026-10-09.json": json.dumps(rec)}, fixtures=True))
    assert problems == []
    charts = chart_data((out / "tracker/ip/beta/index.html").read_text())["charts"]
    wns, tns = charts["ip-asic-clock-wns"]["series"], charts["ip-asic-clock-tns"]["series"]
    assert [s["label"] for s in wns] == ["core", "rx_side"]
    assert wns[0]["points"][-1][1] == -0.25 and wns[1]["points"][-1][1] == 0.0
    assert tns[0]["points"][-1][1] == -3.0 and len(wns[0]["points"]) == 1
    alpha = chart_data((out / "tracker/ip/alpha/index.html").read_text())["charts"]
    assert "ip-asic-wns" in alpha and "ip-asic-clock-wns" not in alpha


def menu(html: str) -> str:
    return html.split('<ul class="menu">')[1].split("</aside>")[0]


def test_tracker_is_its_own_tab(tmp_path):
    root = tree(tmp_path, fixtures=True)
    (root / "wiki/other.md").write_text("# Other\n", encoding="utf-8")
    problems, out = build(root)
    assert problems == []
    site = wiki_site.Site(tmp_path, SHA, URL)
    # the tracker is not a wiki section; its own nav is the landing, then the IPs
    assert all(f.rel != "tracker" for f in site.sections)
    top, sections = site.nav("tracker/ip/alpha/index.html")
    assert top == ["wiki/tracker/index.md", "wiki/tracker/details/index.md"] and [f.rel for f in sections] == ["tracker/ip"]
    assert site.nav("other.html") == (site.top, site.sections)
    tabs = '<div class="navbar-tabs"><a{w} href="{r}index.html">Wiki</a><a{t} href="{r}tracker/index.html">Tracker</a></div>'
    on = ' class="active" aria-current="page"'
    other = (out / "other.html").read_text()
    assert tabs.format(w=on, t="", r="") in other
    assert "tracker/" not in menu(other)  # the wiki sidebar does not list the tracker
    for page, r in (("tracker/index.html", "../"), ("tracker/ip/beta/core/index.html", "../../../../")):
        html = (out / page).read_text()
        assert tabs.format(w="", t=on, r=r) in html
        assert '<nav class="sidebar-tabs" aria-label="Site sections">' in html  # phone drawer copy
        nav = menu(html)
        assert "Other" not in nav and "Home" not in nav  # no wiki tree on tracker pages
        assert f'href="{r}tracker/ip/alpha/index.html">' in nav and "<details open>" in nav
    # the home page links to the tracker; URLs are unchanged
    assert 'href="tracker/index.html"' in (out / "index.html").read_text()


def test_no_tracker_no_tabs(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki/index.md").write_text("# Home\n", encoding="utf-8")
    problems, out = build(tmp_path)
    assert problems == [] and "tabs" not in (out / "index.html").read_text()


# -------------------------------------------------------------- no data
@pytest.mark.parametrize("with_dir", [True, False])
def test_no_data_builds_with_clear_message(tmp_path, with_dir):
    root = tree(tmp_path)
    if not with_dir:
        shutil.rmtree(root / "wiki/tracker/data")
    problems, out = build(root)
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    assert "No data yet" in html and "tracker-data" not in html
    ip = (out / "tracker/ip/index.html").read_text()
    assert "No data yet" in ip
    assert (out / "assets/chart.umd.js").is_file() and (out / "assets/chart-LICENSE.md").is_file()
    assert (out / "assets/tracker.js").is_file()


def test_no_tracker_landing_means_no_tracker(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki/index.md").write_text("# Home\n")
    problems, out = build(tmp_path)
    assert problems == [] and not (out / "tracker").exists()


# ------------------------------------------------------- partial groups
@pytest.mark.parametrize(("result", "open_issues", "level", "label"), [
    ("pass", 1, "good", "pass"),
    ("fail", 0, "critical", "fail"),
    ("cancelled", 0, "critical", "cancelled"),
    ("unknown", 0, "info", "unknown"),
    (None, 1, "critical", "nightly-red open"),
    (None, 0, "good", "green"),
    (None, None, None, None),
])
def test_nightly_card_prefers_result_to_issue_snapshot(tmp_path, result, open_issues, level, label):
    # Collection precedes issue reporting: recovery can still have an open
    # issue, and the first failure can still have none. Old records may omit
    # the result, or both fields, without inventing a successful run.
    health = {} if open_issues is None else {"nightly_red": {"open": open_issues}}
    rec = nightly("2026-09-05", 7, result, health=health)
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    card = re.search(r'<div class="tracker-card-label">Nightly</div>'
                     r'<div class="tracker-card-value">(.*?)</div>', html)
    if level is None:
        assert card is None
    else:
        assert card is not None
        assert f'class="tracker-status {level}"' in card.group(1)
        assert f"</span> {label}</span>" in card.group(1)


def test_partial_groups_and_unknown_fields(tmp_path):
    rec = nightly("2026-09-05", 7, "fail", 600, tests={"status": "unavailable", "wall_s": 600},
                  future_group={"x": 1}, code={"total": {"code": 10}, "mystery": [1, 2]},
                  fpga={"status": "unavailable", "reason": "missing"})
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    data = chart_data(html)
    assert data["axis"] == [[sha(7)[:7], "2026-09-05"]]  # no commits.jsonl: nightly SHAs form the axis
    assert data["charts"]["nightly-duration"]["series"][0]["label"] == "Nightly failed"
    for msg in ("No ci-gate data yet.", "No FPGA metrics yet.", "No ASIC metrics yet.",
                "No slowest-test data yet.", "No file distribution yet.", "No issue or pull request data yet."):
        assert msg in html, msg
    assert "✗" in html and "fail" in html  # nightly card: icon plus label
    # an unavailable flow section names no IPs
    assert sorted(x.name for x in (out / "tracker/ip").iterdir()) == ["index.html"]


def test_commit_index_orders_the_axis(tmp_path):
    lines = commit(2, "2026-09-03", index=2) + commit(0, "2026-09-01", index=0) + commit(1, "2026-09-02", index=1)
    problems, out = build(tree(tmp_path, {"commits.jsonl": lines,
                                          "2026-09-02.json": nightly("2026-09-02", 1, "pass", 60)}))
    assert problems == []
    data = chart_data((out / "tracker/details/index.html").read_text())
    assert [a[1] for a in data["axis"]] == ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert data["charts"]["nightly-duration"]["series"][0]["points"] == [[1, 1]]


def test_nightly_duration_is_the_collector_field(tmp_path):
    # meta.nightly.duration_s is the nightly wall time; the regression wall
    # time tests.wall_s is a different quantity and never stands in for it.
    lines = commit(0, "2026-09-01") + commit(1, "2026-09-02")
    problems, out = build(tree(tmp_path, {"commits.jsonl": lines,
                                          "2026-09-01.json": nightly("2026-09-01", 0, "pass", tests={"wall_s": 60}),
                                          "2026-09-02.json": nightly("2026-09-02", 1, "pass", 180)}))
    assert problems == []
    data = chart_data((out / "tracker/details/index.html").read_text())
    assert data["charts"]["nightly-duration"]["series"][0]["points"] == [[1, 3]]


def test_trends_share_one_x_range(tmp_path):
    # commit trends span 0..3, the nightly trend only 2..3: all line up on 0..3
    lines = "".join(commit(i, f"2026-09-0{i + 1}", code={"total": {"loc": 10 * (i + 1)}}) for i in range(4))
    problems, out = build(tree(tmp_path, {"commits.jsonl": lines,
                                          "2026-09-03.json": nightly("2026-09-03", 2, "pass", 60),
                                          "2026-09-04.json": nightly("2026-09-04", 3, "pass", 120)}))
    assert problems == []
    data = chart_data((out / "tracker/details/index.html").read_text())
    assert data["xrange"] == [0, 3]
    assert [p[0] for p in data["charts"]["nightly-duration"]["series"][0]["points"]] == [2, 3]


def test_flow_charts_keep_every_ip_with_data(tmp_path):
    # ten IPs in the records, FPGA data for the last three only: all three drawn
    ips = [f"ip{i}" for i in range(10)]
    rec = nightly("2026-09-05", 1, code={"ips": {ip: {"code": 1, "files": 1} for ip in ips}},
                  fpga={"status": "ok", "ips": {ip: {"logic_elements": 5} for ip in ips[7:]}})
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    assert problems == []
    chart = chart_data((out / "tracker/details/index.html").read_text())["charts"]["fpga-le"]
    assert [(s["label"], s["color"]) for s in chart["series"]] == [("ip7", "1"), ("ip8", "2"), ("ip9", "3")]
    # more than eight IPs with data: eight drawn, the rest named in a note
    rec = nightly("2026-09-05", 1, fpga={"status": "ok", "ips": {ip: {"logic_elements": 5} for ip in ips}})
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    html = (out / "tracker/details/index.html").read_text()
    assert len(chart_data(html)["charts"]["fpga-le"]["series"]) == 8
    assert "2 more series not drawn" in html


def test_blank_line_in_a_value_is_a_problem(tmp_path):
    # LF, CRLF, bare CR, and a line holding only whitespace all end the block
    rec = nightly("2026-09-05", 1, "pass", 60,
                  github={"issues": {"open_by_label": {"a\n\nb": 2, "c\n \t\nd": 1}}},
                  tests={"totals": {"level0": {"tests": 1}},
                         "slowest": [{"ip": "x\r\n\r\ny", "name": "e\r\rf", "runtime_s": 1}]})
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    # one problem line per page, however many values carry blank lines
    assert len(problems) == 1 and "wiki/tracker/details/index.md: a record value contains a blank line" in problems[0]
    html = (out / "tracker/details/index.html").read_text()
    assert "<code>a b</code>" in html and "<code>c d</code>" in html
    assert "x y" in html and "<code>e f</code>" in html
    assert wiki_site.main(["build", "--strict", "--root", str(tmp_path), "--out", str(out),
                           "--sha", SHA]) == 1


def test_single_line_break_in_a_value_is_not_a_problem(tmp_path):
    # one CRLF, LF or CR is no blank line: it renders inside the block as is
    rec = nightly("2026-09-05", 1, "pass", 60, github={"issues": {"open_by_label": {
        "a\r\nb": 1, "c\nd": 2, "e\rf": 3, "g \r\n h": 4}}})
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    assert problems == []
    assert wiki_site.main(["build", "--strict", "--root", str(tmp_path), "--out", str(out),
                           "--sha", SHA]) == 0


def test_light_series_colours_clear_3_to_1_on_the_page():
    css = (Path(tracker.__file__).parent / "theme/wiki.css").read_text()
    root = re.search(r":root \{([^}]*)\}", css.split("/* ---- tracker")[0]).group(1)
    light = re.search(r"/\* ---- tracker.*?:root \{([^}]*)\}", css, re.S).group(1)
    bg = re.search(r"--bg:\s*(#[0-9a-f]{6})", root).group(1)

    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    slots = re.findall(r"--series-(\d): (#[0-9a-f]{6})", light)
    assert len(slots) == tracker.SLOTS
    for n, h in slots:
        hi, lo = sorted((lum(h), lum(bg)), reverse=True)
        assert (hi + 0.05) / (lo + 0.05) >= 3, f"--series-{n} {h}"


def test_commit_only_data(tmp_path):
    lines = commit(1, "2026-09-01", ci={"status": "ok", "conclusion": "success", "duration_s": 120}) + \
        commit(2, "2026-09-02", ci={"status": "ok", "conclusion": "failure", "duration_s": 180}) + \
        commit(3, "2026-09-03", ci={"status": "none"})
    problems, out = build(tree(tmp_path, {"commits.jsonl": lines}))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    assert "ci-gate pass rate, last 2 runs on main" in html and "50%" in html
    assert "No nightly or test data yet." in html


def test_bad_records_are_problems(tmp_path):
    lines = commit(1, "2026-09-01") + "not json\n" + commit(1, "2026-09-02") + \
        json.dumps({"schema_version": 1, "date": "2026-09-03"}) + "\n"
    data = {"commits.jsonl": lines,
            "2026-09-01.json": "{",
            "2026-09-02.json": json.dumps({"schema_version": 99, "meta": {"commit": sha(1)}}),
            "2026-09-03.json": "[1]",
            "2026-13-40.json": json.dumps({"schema_version": 1, "meta": {"commit": sha(1)}}),
            "2026-09-04.json": nightly("2026-09-04", 5),
            "2026-09-05.json": json.dumps({"schema_version": 1, "meta": {"date": "2026-09-05"}})}
    problems, out = build(tree(tmp_path, data))
    text = "\n".join(problems)
    assert "commits.jsonl:2: not JSON" in text
    assert "commits.jsonl:3: commit 0000000 repeats line 1" in text
    assert "commits.jsonl:4: needs a hex `sha`" in text
    assert "2026-09-01.json: not a readable JSON record" in text
    assert "2026-09-02.json: schema_version 99 is not supported" in text
    assert "2026-09-03.json: a record must be a JSON object" in text
    assert "2026-13-40.json: date" in text
    assert "2026-09-04.json" not in text  # a commit outside the series is ignored, not a problem
    assert "2026-09-05.json: no main `sha`" in text
    assert wiki_site.main(["build", "--strict", "--root", str(tmp_path), "--out", str(out),
                           "--sha", SHA]) == 1


def test_unsafe_ip_ids_are_skipped(tmp_path):
    rec = nightly("2026-09-05", 1, fpga={"status": "ok", "ips": {
        "../evil": {"logic_elements": 1}, "ok": {"logic_elements": 2}, "a b": {"logic_elements": 3}}})
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    assert any("'../evil' is not a safe path" in p for p in problems)
    assert any("'a b' is not a safe path" in p for p in problems)
    assert (out / "tracker/ip/ok/index.html").is_file()
    assert not (out / "tracker/evil").exists() and not (out / "tracker/ip/a b").exists()


def test_source_file_at_generated_path_is_a_problem(tmp_path):
    root = tree(tmp_path)
    (root / "wiki/tracker/ip").mkdir()
    (root / "wiki/tracker/ip/index.md").write_text("# Mine\n")
    problems, _ = build(root)
    assert any("generated by the tracker" in p for p in problems)


# ------------------------------------------------------------ escaping
def test_values_are_html_escaped(tmp_path):
    evil = "<script>alert('x')</script>"
    rec = nightly("2026-09-05", 1, evil,
                  tests={"wall_s": 60, "totals": {"level0": {"tests": 1}}, "slowest": [
                      {"ip": evil, "name": evil + '"&', "level": evil, "seed": evil, "runtime_s": 3}]},
                  github={"issues": {"open_by_label": {evil: 2}}},
                  code={"kinds": {evil: {"code": 5, "files": 1}},
                        "by_ext": {evil: {"files": 1, "code": 2}}})
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    assert evil not in html.replace('<script type="application/json" id="tracker-data">', "")
    assert "&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;" in html
    assert html.count("</script>") == 3  # theme bootstrap, the data block and wiki.js only
    block = re.search(r'id="tracker-data">(.*?)</script>', html, re.S).group(1)
    assert "<" not in block and "'" not in block and "&" not in block
    assert evil in json.dumps(json.loads(block))  # the label survives as data


# --------------------------------------------------------------- units
def test_bins_integer_values():
    labels, counts = tracker.bins([1, 1, 2, 3, 3, 3], integer=True)
    assert labels == ["1", "2", "3"] and counts == [2, 1, 3]


def test_bins_fold_long_tail():
    vals = [float(v) for v in range(1, 91)] + [1000.0] * 10
    labels, counts = tracker.bins(vals, integer=False)
    assert labels[-1].startswith("≥") and counts[-1] == 10 and sum(counts) == 100
    assert labels[0] == "0–10"


def test_fmt_and_delta():
    assert tracker.fmt(1234) == "1,234" and tracker.fmt(2.0) == "2" and tracker.fmt(-0.123, "ns") == "−0.123 ns"
    assert tracker.fmt(95.0, "%") == "95%" and tracker.fmt(None) == "–"
    assert tracker.fmt_delta(10, 7) == "+3" and tracker.fmt_delta(7, 10) == "−3" and tracker.fmt_delta(1, None) == "–"


def test_deterministic(tmp_path):
    root = tree(tmp_path, fixtures=True)
    _, out = build(root)
    first = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    _, out = build(root)
    second = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert first == second


def test_coverage_charts(tmp_path):
    tests = {"status": "ok", "ips": {"alpha": {"level0": {"tests": 1, "passed": 1, "failed": 0}}}}

    def cov(line, toggle, functional):
        return {"status": "ok", "passed": True, "ips": {"alpha": {
            "line": {"covered": 1, "total": 2, "pct": line},
            "toggle": {"covered": 1, "total": 2, "pct": toggle},
            "functional": {"covered": 0, "total": 0, "pct": functional},
            "floors": {}, "floors_passed": True}}}
    data = {"commits.jsonl": commit(1, "2026-09-01") + commit(2, "2026-09-02") + commit(3, "2026-09-03"),
            "2026-09-01.json": nightly("2026-09-01", 1, "pass", tests=tests, coverage=cov(50.0, 25.0, None)),
            "2026-09-02.json": nightly("2026-09-02", 2, "pass", tests=tests, coverage={"status": "unavailable"}),
            "2026-09-03.json": nightly("2026-09-03", 3, "pass", tests=tests, coverage=cov(75.0, 40.0, 90.0))}
    problems, out = build(tree(tmp_path, data))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    assert ">Coverage<a" in html
    charts = chart_data(html)["charts"]
    assert charts["cov-line"]["series"][0]["label"] == "alpha"
    assert charts["cov-line"]["series"][0]["points"] == [[0, 50.0], [2, 75.0]]
    assert charts["cov-functional"]["series"][0]["points"] == [[2, 90.0]]  # null is a gap
    page = (out / "tracker/ip/alpha/index.html").read_text()
    series = {s["label"]: s["points"] for s in chart_data(page)["charts"]["ip-coverage"]["series"]}
    assert series == {"Line coverage": [[0, 50.0], [2, 75.0]], "Toggle coverage": [[0, 25.0], [2, 40.0]],
                      "Functional coverage": [[2, 90.0]]}


def test_no_coverage_data_says_so(tmp_path):
    problems, out = build(tree(tmp_path, fixtures=True))
    assert problems == []
    assert "No Coverage metrics yet." in (out / "tracker/details/index.html").read_text()
    assert "No coverage metrics yet for this IP." in (out / "tracker/ip/alpha/index.html").read_text()


def arch(status="pass", **scenarios):
    """An `arch` section for IP alpha: scenario -> (status, {metric: value})."""
    return {"status": "ok", "ips": {"alpha": {"status": status, "scenarios": {
        name: {"status": st, "targets": targets} for name, (st, targets) in scenarios.items()}}}}


def test_arch_charts(tmp_path):
    data = {"commits.jsonl": commit(1, "2026-09-01") + commit(2, "2026-09-02") + commit(3, "2026-09-03"),
            # an older record without an arch section still reads
            "2026-09-01.json": nightly("2026-09-01", 1, "pass"),
            "2026-09-02.json": nightly("2026-09-02", 2, "pass", arch=arch(
                full=("pass", {"throughput_bytes_per_cycle": 1.0, "latency_cycles_max": 1}),
                half=("pass", {"throughput_ratio": 1.0, "latency_cycles_max": 1}))),
            "2026-09-03.json": nightly("2026-09-03", 3, "fail", arch=arch(
                "fail", full=("fail", {"throughput_bytes_per_cycle": 0.5, "latency_cycles_max": 2}),
                half=("pass", {"throughput_ratio": 1.0, "latency_cycles_max": 1})))}
    problems, out = build(tree(tmp_path, data))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    assert ">Architecture model<a" in html and "from the 2026-09-03 nightly" in html
    charts = chart_data(html)["charts"]
    lat = charts["arch-latency_cycles_max"]
    assert lat["unit"] == "cycles"
    assert {s["label"]: s["points"] for s in lat["series"]} == {
        "alpha / full": [[1, 1], [2, 2]], "alpha / half": [[1, 1], [2, 1]]}
    assert charts["arch-throughput_bytes_per_cycle"]["unit"] == "bytes per cycle"
    assert [s["points"] for s in charts["arch-failing"]["series"]] == [[[1, 0], [2, 1]]]
    assert "<code>full</code>" in html and "latency_cycles_max = 2, throughput_bytes_per_cycle = 0.5" in html
    page = (out / "tracker/ip/alpha/index.html").read_text()
    assert ">Architecture model<a" in page and "Arch full latency_cycles_max" in page
    series = chart_data(page)["charts"]["ip-arch-throughput_ratio"]["series"]
    assert [(s["label"], s["points"]) for s in series] == [("half", [[1, 1.0], [2, 1.0]])]
    index = (out / "tracker/ip/index.html").read_text()
    assert "<th>Arch model</th>" in index and "</span> fail</span>" in index


def test_no_arch_data_says_so(tmp_path):
    problems, out = build(tree(tmp_path, fixtures=True))
    assert problems == []
    assert "No architecture-model metrics yet." in (out / "tracker/details/index.html").read_text()
    assert "No architecture-model metrics yet for this IP." in (out / "tracker/ip/alpha/index.html").read_text()


def test_arch_names_are_escaped_and_ids_safe(tmp_path):
    rec = nightly("2026-09-05", 1, "pass", arch=arch(**{"<b>x</b>": ("pass", {"a<b": 1, "a>b": 2})}))
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    assert "<b>x</b>" not in html and "&lt;b&gt;x&lt;/b&gt;" in html
    assert {"arch-a-b", "arch-a-b-2"} <= set(chart_data(html)["charts"])


def test_metric_unit():
    assert tracker.metric_unit("latency_cycles_max") == "cycles"
    assert tracker.metric_unit("throughput_bytes_per_cycle") == "bytes per cycle"
    assert tracker.metric_unit("throughput_ratio") == "ratio"
    assert tracker.metric_unit("cycles") == "value"
    assert tracker.metric_unit("lat_max") == "value"


def test_code_kind_beyond_the_slots_is_noted(tmp_path):
    kinds = {k: {"files": 1, "lines": 1, "code": 1} for k in tracker.KINDS}  # nine kinds with data
    problems, out = build(tree(tmp_path, {"commits.jsonl": commit(1, "2026-09-01", code={"kinds": kinds})}))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    labels = [s["label"] for s in chart_data(html)["charts"]["loc-kind"]["series"]]
    assert len(labels) == 8 and "Arch Python" not in labels and "Skills" in labels
    # the note names the hidden series, and the data table keeps its values
    assert "1 more series not drawn (no colour slot left): Arch Python; the data table has their values." in html
    assert "<th>Arch Python (not drawn)</th>" in html


@pytest.mark.parametrize("gone", [None, {"files": 0, "lines": 0, "code": 0}])
def test_code_kind_without_data_gives_up_its_slot(tmp_path, gone):
    kinds = {k: {"files": 1, "lines": 1, "code": 1} for k in tracker.KINDS}
    if gone is None:
        del kinds["workflows"]          # absent
    else:
        kinds["workflows"] = gone       # zero everywhere counts as absent
    problems, out = build(tree(tmp_path, {"commits.jsonl": commit(1, "2026-09-01", code={"kinds": kinds})}))
    assert problems == []
    html = (out / "tracker/details/index.html").read_text()
    series = {s["label"]: s["color"] for s in chart_data(html)["charts"]["loc-kind"]["series"]}
    assert series["Arch Python"] == "7" and series["Skills"] == "8" and "Workflows" not in series
    if gone is not None:
        assert "(no colour slot left): Workflows;" in html  # the zero kind is named, not dropped
    # a kind that is non-zero at any point keeps its fixed slot
    kinds["workflows"] = {"files": 0, "lines": 0, "code": 0}
    lines = commit(1, "2026-09-01", code={"kinds": kinds}) + commit(
        2, "2026-09-02", code={"kinds": {**kinds, "workflows": {"files": 1, "lines": 1, "code": 1}}})
    problems, out = build(tree(tmp_path, {"commits.jsonl": lines}))
    series = {s["label"]: s["color"] for s in chart_data((out / "tracker/details/index.html").read_text())
              ["charts"]["loc-kind"]["series"]}
    assert series["Workflows"] == "7" and "Arch Python" not in series


def test_ip_series_beyond_the_slots_are_named(tmp_path):
    ips = [f"ip{i}" for i in range(10)]
    rec = commit(1, "2026-09-01", code={"ips": {ip: {"code": 1, "files": 1} for ip in ips}})
    problems, out = build(tree(tmp_path, {"commits.jsonl": rec}))
    html = (out / "tracker/details/index.html").read_text()
    assert len(chart_data(html)["charts"]["loc-ip"]["series"]) == 8
    assert "2 more series not drawn (no colour slot left): ip8, ip9;" in html
    assert "<th>ip9 (not drawn)</th>" in html


def test_arch_failing_chart_starts_at_zero(tmp_path):
    rec = nightly("2026-09-05", 1, "pass", arch=arch(full=("pass", {"lat": 1})))
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    charts = chart_data((out / "tracker/details/index.html").read_text())["charts"]
    assert charts["arch-failing"]["ymin"] == 0 and "ymin" not in charts["arch-lat"]


def test_repo_home_links_to_tracker_up_front():
    # The repository README (the site home) links to the published tracker
    # before its first section, by absolute URL so it works on GitHub too; the
    # build leaves absolute URLs as they are.
    owner, repo = wiki_site.REPO_URL.rstrip("/").split("/")[-2:]
    url = f"https://{owner}.github.io/{repo}/tracker/"
    readme = (wiki_site.REPO / wiki_site.HOME).read_text(encoding="utf-8")
    page = wiki_site.Page(wiki_site.new_md(), wiki_site.HOME, readme)
    intro = []
    for tok in page.tokens:
        if tok.type == "heading_open" and tok.tag == "h2":
            break
        intro += [c.attrGet("href") for c in tok.children or () if c.type == "link_open"]
    assert url in intro


# --------------------------------------------------------- compact overview

def overview_html(out):
    return (out / "tracker/index.html").read_text().split('<article class="markdown">')[-1].split('</article>')[0]


def test_overview_keeps_only_recent_runtime_charts_and_links_details(tmp_path):
    problems, out = build(tree(tmp_path, fixtures=True))
    assert problems == []
    html = overview_html(out)
    assert 'href="details/index.html">Detailed stats' in html
    charts = chart_data(html)["charts"]
    assert set(charts) == {"ci-duration", "nightly-duration"}
    assert all(chart["max_ticks"] == 4 for chart in charts.values())
    assert min(point[0] for chart in charts.values() for series in chart["series"]
               for point in series["points"]) >= 6  # 36 commits, last 30 only
    assert html.count('class="tracker-card"') == 4
    assert 'Data table (latest' in html
    assert html.index('tracker-overview-cards') < html.index('Needs attention') < html.index('Recent runtimes')
    assert 'id="code"' not in html and 'id="issues-and-pull-requests"' not in html
    details = (out / "tracker/details/index.html").read_text()
    assert 'href="../index.html">← Status overview' in details
    assert 'href="../ip/alpha/index.html">alpha</a>' in details
    assert 'Detailed stats' in menu((out / 'tracker/index.html').read_text())


@pytest.mark.parametrize(('result', 'red', 'state'), [('pass', 1, 'good'), ('fail', 0, 'critical'),
                                                      (None, 0, 'info'), (None, 1, 'info')])
def test_overview_nightly_uses_result_never_issue_count(tmp_path, result, red, state):
    rec = nightly('2026-09-01', 1, result, 125, health={'nightly_red': {'open': red}})
    problems, out = build(tree(tmp_path, {'2026-09-01.json': rec}))
    assert problems == []
    html = overview_html(out)
    card = html.split('tracker-card-label">Nightly</div>')[1].split('tracker-card-note')[0]
    assert f'tracker-status {state}' in card
    assert (result or 'Unknown') in card
    assert ('2m 05s' if result else 'Duration unavailable') in html


def test_overview_keeps_snapshot_dates_and_ci_source_separate(tmp_path):
    lines = commit(1, '2026-09-01', ci={'conclusion': 'failure', 'duration_s': 60}) + \
        commit(2, '2026-09-02', ci={'conclusion': 'success', 'duration_s': 120})
    rec = nightly('2026-09-01', 1, 'pass', 180,
                  health={'ci_gate_main': {'runs': 20, 'pass_rate': 1}})
    problems, out = build(tree(tmp_path, {'commits.jsonl': lines, '2026-09-01.json': rec}))
    assert problems == []
    html = overview_html(out)
    assert 'Nightly snapshot: 2026-09-01' in html
    assert 'Commit data through: 2026-09-02' in html
    assert 'Last 2 recorded main commits · through 2026-09-02' in html and '50%' in html
    assert '100%' not in html
    details = (out / 'tracker/details/index.html').read_text()
    assert 'nightly run snapshot' in details and '100%' in details


@pytest.mark.parametrize(('tests', 'coverage', 'test_state', 'cov_state'), [
    ({'totals': {'level0': {'tests': 2, 'passed': 2, 'failed': 0},
                 'level1': {'tests': 3, 'passed': 3, 'failed': 0}}},
     {'passed': True, 'ips': {'alpha': {'floors_passed': True}}}, 'good', 'good'),
    ({'totals': {'level0': {'tests': 2, 'passed': 2}}}, {'passed': True}, 'info', 'info'),
    ({'totals': {'level0': {'tests': 2, 'passed': 1, 'failed': 1}}},
     {'passed': True, 'ips': {'alpha': {'floors_passed': False}}}, 'critical', 'critical'),
    ({'totals': {'level0': {'tests': 2, 'passed': 2, 'failed': 0}}, 'builds': {'failed': 1}},
     {'passed': False, 'ips': {'alpha': {'floors_passed': True}}}, 'critical', 'critical'),
    ({'totals': {'level0': {'tests': 0, 'passed': 0, 'failed': 0}}},
     {'status': 'unavailable'}, 'info', 'info'),
])
def test_overview_tests_and_coverage_need_complete_evidence(tmp_path, tests, coverage, test_state, cov_state):
    rec = nightly('2026-09-01', 1, 'pass', tests=tests, coverage=coverage)
    problems, out = build(tree(tmp_path, {'2026-09-01.json': rec}))
    assert problems == []
    html = overview_html(out)
    for label, state in [('Tests', test_state), ('Coverage floors', cov_state)]:
        card = html.split(f'tracker-card-label">{label}</div>')[1].split('tracker-card-note')[0]
        assert f'tracker-status {state}' in card
    if test_state == 'good':
        assert '5 / 5' in html and 'Tests passed · L0, L1' in html


def test_overview_attention_puts_failure_before_timing_and_missing_evidence(tmp_path):
    rec = nightly('2026-09-01', 1, 'pass',
                  fpga={'ips': {'alpha': {'status': 'fail'}}},
                  asic={'ips': {'beta': {'status': 'pass', 'timing': {'wns_ns': -1.25}}}})
    problems, out = build(tree(tmp_path, {'2026-09-01.json': rec}))
    assert problems == []
    html = overview_html(out)
    assert html.index('alpha: FPGA result is fail') < html.index('beta: ASIC timing gap') < html.index('Incomplete evidence:')
    assert 'WNS −1.25 ns' in html
    assert 'href="ip/beta/index.html#asic"' in html


def test_overview_attention_is_bounded_with_remaining_count(tmp_path):
    rec = nightly('2026-09-01', 1, 'fail', fpga={'ips': {f'unit{i}': {'status': 'fail'} for i in range(8)}})
    problems, out = build(tree(tmp_path, {'2026-09-01.json': rec}))
    assert problems == []
    html = overview_html(out)
    attention = html.split('<ul class="tracker-attention">')[1].split('</ul>')[0]
    assert attention.count('<li>') == 5
    assert '5 more items · view all metrics' in html


def test_overview_missing_nightly_does_not_reuse_old_health(tmp_path):
    problems, out = build(tree(tmp_path, {'commits.jsonl': commit(1, '2026-09-01')}))
    assert problems == []
    html = overview_html(out)
    cards = html.split('tracker-overview-cards')[1].split('<h2')[0]
    assert cards.count('tracker-status info') == 4 and 'tracker-status good' not in cards
    assert 'Nightly snapshot: no record yet' in html


def test_overview_escapes_attention_and_keeps_unknown_results_neutral(tmp_path):
    rec = nightly('2026-09-01', 1, '<script>bad</script>')
    problems, out = build(tree(tmp_path, {'2026-09-01.json': rec}))
    assert problems == []
    html = overview_html(out)
    assert '<script>bad</script>' not in html and '&lt;script&gt;bad&lt;/script&gt;' in html
    assert 'tracker-status good' not in html


@pytest.mark.parametrize('result', ['flow: fail (exit 2)', 'clocks: timeout after 3600s',
                                    'incomplete: missing timing.clocks'])
def test_overview_reports_real_physical_failure_diagnostics(tmp_path, result):
    rec = nightly('2026-09-01', 1, 'pass', asic={'ips': {'alpha': {'status': result}}})
    problems, out = build(tree(tmp_path, {'2026-09-01.json': rec}))
    assert problems == []
    html = overview_html(out)
    assert f'alpha: ASIC result is {result}.' in html
    assert 'href="ip/alpha/index.html#asic"' in html
    details = (out / 'tracker/details/index.html').read_text()
    assert result in details and 'href="../ip/alpha/index.html#asic"' in details


def test_overview_reports_per_clock_gap_without_aggregate_wns(tmp_path):
    rec = nightly('2026-09-01', 1, 'pass', asic={'ips': {'alpha': {'status': 'pass', 'timing': {
        'clocks': {'fast': {'wns_ns': -0.25}, 'slow': {'wns_ns': 0}}}}}})
    problems, out = build(tree(tmp_path, {'2026-09-01.json': rec}))
    assert problems == []
    assert 'ASIC timing gap · WNS −0.25 ns' in overview_html(out)
