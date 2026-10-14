"""Report parsing against hand-made fixtures (see fixtures/README.md)."""

import json
import shutil
from pathlib import Path

import pytest

from fpga import reports
from fpga.reports import ReportError

FIX = Path(__file__).resolve().parent / "fixtures"
REV = "widget_board"


@pytest.fixture
def project(tmp_path) -> Path:
    dst = tmp_path / "proj"
    shutil.copytree(FIX, dst, ignore=shutil.ignore_patterns("README.md"))
    return dst


def test_fit_summary():
    fit = reports.parse_fit_summary(FIX / "output_files" / f"{REV}.fit.summary")
    assert fit["device"] == "EP4CE115F29C7"
    assert fit["family"] == "Cyclone IV E"
    assert fit["quartus_version"].startswith("23.1std.1 Build 993")
    r = fit["resources"]
    assert r["logic_elements"] == {"used": 1234, "total": 114480}
    assert r["registers"] == 456
    assert r["memory_bits"] == {"used": 8192, "total": 3981312}
    assert r["dsp_9bit"] == {"used": 2, "total": 532}
    assert r["pins"] == {"used": 18, "total": 529}
    assert r["plls"] == {"used": 0, "total": 4}


def test_fit_memory_blocks():
    blocks = reports.parse_fit_memory_blocks(FIX / "output_files" / f"{REV}.fit.rpt")
    assert blocks == {"type": "M9K", "used": 1, "total": 432}


def test_fit_memory_blocks_absent(tmp_path):
    rpt = tmp_path / "x.fit.rpt"
    rpt.write_text("; Total registers ; 4 ;\n")
    assert reports.parse_fit_memory_blocks(rpt) is None


def test_fit_failed_status(tmp_path):
    s = tmp_path / "x.fit.summary"
    s.write_text("Fitter Status : Failed - Wed Oct 14 12:00:00 2026\n")
    with pytest.raises(ReportError, match="fitter status is 'Failed"):
        reports.parse_fit_summary(s)


def test_fit_malformed_ratio(tmp_path):
    s = tmp_path / "x.fit.summary"
    s.write_text("Fitter Status : Successful\nDevice : X\nTotal registers : 3\n"
                 "Total logic elements : lots\n")
    with pytest.raises(ReportError, match="used / total"):
        reports.parse_fit_summary(s)


def test_missing_report(tmp_path):
    with pytest.raises(ReportError, match="cannot read report"):
        reports.parse_fit_summary(tmp_path / "nope.fit.summary")


def test_panels_split():
    tables = reports.panels((FIX / "output_files" / f"{REV}.sta.rpt").read_text())
    assert tables["Slow 1200mV 85C Model Recovery Summary"] == []
    assert tables["Clocks"][1]["Period"] == "8.000"
    assert "Table of Contents" in tables


def test_sta_worst_across_corners():
    clocks = reports.parse_sta(FIX / "output_files" / f"{REV}.sta.rpt")
    assert list(clocks) == ["CLK_A", "CLK_B"]
    assert clocks["CLK_A"] == {"fmax_mhz": 187.55, "restricted_fmax_mhz": 187.55,
                               "setup_slack_ns": 14.668, "hold_slack_ns": 0.181}
    assert clocks["CLK_B"] == {"fmax_mhz": 260.42, "restricted_fmax_mhz": 250.0,
                               "setup_slack_ns": 4.16, "hold_slack_ns": 0.187}


def test_sta_missing_panel(tmp_path):
    rpt = tmp_path / "x.sta.rpt"
    text = (FIX / "output_files" / f"{REV}.sta.rpt").read_text()
    rpt.write_text(text.replace("Model Hold Summary", "Model Hold Overview"))
    with pytest.raises(ReportError, match="Hold Summary"):
        reports.parse_sta(rpt)


def test_sta_no_clocks(tmp_path):
    rpt = tmp_path / "x.sta.rpt"
    rpt.write_text("\n".join(
        f"-----\n; Slow 1200mV 85C Model {k} Summary ;\n-----\nNo paths to report.\n"
        for k in ("Fmax", "Setup", "Hold")))
    with pytest.raises(ReportError, match="no constrained clock"):
        reports.parse_sta(rpt)


def test_worst_path():
    cp = reports.parse_worst_path(FIX / "worst_path.txt")
    assert cp == {"from": "u_core|acc_q[3]", "to": "u_core|acc_q[15]",
                  "from_clock": "CLK_B", "to_clock": "CLK_B",
                  "slack_ns": 4.16, "data_delay_ns": 3.721}


def test_worst_path_empty(tmp_path):
    (tmp_path / "w.txt").write_text("")
    assert reports.parse_worst_path(tmp_path / "w.txt") is None


def test_worst_path_malformed(tmp_path):
    (tmp_path / "w.txt").write_text("slack=1.0\nbogus line\n")
    with pytest.raises(ReportError, match=r"w.txt:2"):
        reports.parse_worst_path(tmp_path / "w.txt")


def test_collect(project):
    m = reports.collect(project, REV)
    assert m["schema"] == reports.SCHEMA
    assert m["resources"]["memory_blocks"] == {"type": "M9K", "used": 1, "total": 432}
    t = m["timing"]
    assert t["worst_setup_slack_ns"] == 4.16
    assert t["worst_hold_slack_ns"] == 0.181
    assert t["met"] is True
    assert t["critical_path"]["to"] == "u_core|acc_q[15]"
    json.dumps(m)  # serializable


def test_collect_negative_slack_not_met(project):
    rpt = project / "output_files" / f"{REV}.sta.rpt"
    rpt.write_text(rpt.read_text().replace("; CLK_A ; 0.181 ;", "; CLK_A ; -0.050 ;"))
    m = reports.collect(project, REV)
    assert m["timing"]["worst_hold_slack_ns"] == -0.05
    assert m["timing"]["met"] is False


def test_collect_deterministic(project):
    assert reports.collect(project, REV) == reports.collect(project, REV)
