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
             "wiki/tracker/index.md": LANDING, "wiki/tracker/data/.gitkeep": ""}
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


def nightly(date: str, n: int, result: str | None = None, **extra) -> str:
    meta = {"date": date, "commit": sha(n)}
    if result:
        meta["nightly"] = {"result": result}
    return json.dumps({"schema_version": 1, "meta": meta, **extra})


# ------------------------------------------------------------ with data
def test_fixture_data_renders_every_section(tmp_path):
    problems, out = build(tree(tmp_path, fixtures=True))
    assert problems == []
    html = (out / "tracker/index.html").read_text()
    assert "Intro text." in html  # the landing text stays on top
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
    html = (out / "tracker/index.html").read_text()
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
    assert 'href="ip/beta/core/index.html">beta/core</a>' in landing
    # sidebar: Per-IP metrics folder under the tracker, every IP page listed
    site = wiki_site.Site(tmp_path, SHA, URL)
    nav = site.nav_pages()
    assert nav.index("wiki/tracker/index.md") < nav.index("wiki/tracker/ip/index.md")
    assert {"wiki/tracker/ip/alpha/index.md", "wiki/tracker/ip/beta/core/index.md"} <= set(nav)


# -------------------------------------------------------------- no data
@pytest.mark.parametrize("with_dir", [True, False])
def test_no_data_builds_with_clear_message(tmp_path, with_dir):
    root = tree(tmp_path)
    if not with_dir:
        shutil.rmtree(root / "wiki/tracker/data")
    problems, out = build(root)
    assert problems == []
    html = (out / "tracker/index.html").read_text()
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
def test_partial_groups_and_unknown_fields(tmp_path):
    rec = nightly("2026-09-05", 7, "fail", tests={"status": "unavailable", "wall_s": 600},
                  future_group={"x": 1}, code={"total": {"code": 10}, "mystery": [1, 2]},
                  fpga={"status": "unavailable", "reason": "missing"})
    problems, out = build(tree(tmp_path, {"2026-09-05.json": rec}))
    assert problems == []
    html = (out / "tracker/index.html").read_text()
    data = chart_data(html)
    assert data["axis"] == [[sha(7)[:7], "2026-09-05"]]  # no commits.jsonl: nightly SHAs form the axis
    assert data["charts"]["nightly-duration"]["series"][0]["label"] == "Nightly failed"
    for msg in ("No ci-gate data yet.", "No FPGA metrics yet.", "No ASIC metrics yet.",
                "No slowest-test data yet.", "No file distribution yet.", "No issue or pull request data yet."):
        assert msg in html, msg
    assert "✗" in html and "fail" in html  # nightly card: icon plus label


def test_commit_only_data(tmp_path):
    lines = commit(1, "2026-09-01", ci={"status": "ok", "conclusion": "success", "duration_s": 120}) + \
        commit(2, "2026-09-02", ci={"status": "ok", "conclusion": "failure", "duration_s": 180}) + \
        commit(3, "2026-09-03", ci={"status": "none"})
    problems, out = build(tree(tmp_path, {"commits.jsonl": lines}))
    assert problems == []
    html = (out / "tracker/index.html").read_text()
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
    assert "2026-09-04.json: sha 0000000 is not in commits.jsonl" in text
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
    html = (out / "tracker/index.html").read_text()
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
