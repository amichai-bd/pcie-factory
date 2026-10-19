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


def test_synthesis_summary(project):
    doc = reports.collect_synthesis(project, REV)
    assert doc["schema"] == 1 and doc["stage"] == "synthesis"
    assert doc["family"] == "Cyclone IV E" and doc["device"] is None
    assert doc["top"] == REV and doc["quartus_version"].startswith("25.1std.0")
    assert doc["resources"] == {"logic_elements": 1301, "registers": 470, "memory_bits": 8192,
                                "dsp_9bit": 2, "pins": 18, "plls": 0}
    assert "timing" not in doc  # synthesis only: no timing claim


def test_synthesis_summary_keeps_used_of_a_ratio_and_nulls_missing(project):
    path = project / "output_files" / f"{REV}.map.summary"
    text = path.read_text().replace("Total pins : 18", "Total pins : 18 / 529 ( 3 % )")
    path.write_text(text.replace("Total PLLs : 0\n", ""))
    r = reports.collect_synthesis(project, REV)["resources"]
    assert r["pins"] == 18 and r["plls"] is None


@pytest.mark.parametrize("old, new, msg", [
    ("Successful - Wed Oct 14 12:00:00 2026", "Failed - Wed Oct 14", "status is 'Failed"),
    ("Total registers : 470\n", "", "no 'Total registers' line"),
    ("Total logic elements : 1,301", "Total logic elements : many", "expected a count"),
])
def test_synthesis_summary_errors(project, old, new, msg):
    path = project / "output_files" / f"{REV}.map.summary"
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(ReportError, match=msg):
        reports.collect_synthesis(project, REV)


def test_synthesis_summary_missing(tmp_path):
    with pytest.raises(ReportError, match="cannot read report"):
        reports.collect_synthesis(tmp_path, REV)


def test_fit_summary():
    fit = reports.parse_fit_summary(FIX / "output_files" / f"{REV}.fit.summary")
    assert fit["device"] == "EP4CE115F29C7"
    assert fit["family"] == "Cyclone IV E"
    assert fit["quartus_version"] == "25.1std.0 Build 1129 10/21/2025 SC Lite Edition"
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


def test_fit_rams():
    rams = reports.parse_fit_rams(FIX / "output_files" / f"{REV}.fit.rpt")
    assert rams == [{
        "name": "widget_core:u_core|widget_fifo:u_fifo|altsyncram:u_ram|"
                "altsyncram_k1d1:auto_generated|ALTSYNCRAM",
        "mode": "Simple Dual Port", "clock_mode": "Single Clock",
        "port_a_depth": 256, "port_a_width": 32, "port_b_depth": 256, "port_b_width": 32,
        "bits": 8192, "mixed_rdw": "Old data", "blocks": 1}]


def test_fit_rams_absent_and_partial(tmp_path):
    rpt = tmp_path / "x.fit.rpt"
    rpt.write_text("; Total registers ; 4 ;\n")
    assert reports.parse_fit_rams(rpt) == []
    rpt.write_text("+--+\n; Fitter RAM Summary ;\n+--+\n; Name ; Mode ; M10Ks ;\n+--+\n"
                   "; a|b ; Single Port ; 2 ;\n+--+\n")
    (ram,) = reports.parse_fit_rams(rpt)
    assert ram["name"] == "a|b" and ram["blocks"] == 2 and ram["port_a_depth"] is None


def test_fit_rams_not_applicable(tmp_path):
    # A ROM row: Quartus prints "--" for port B and the mixed-port mode.
    rpt = tmp_path / "x.fit.rpt"
    rpt.write_text("+--+\n; Fitter RAM Summary ;\n+--+\n"
                   "; Name ; Mode ; Port A Depth ; Port A Width ; Port B Depth ; Port B Width ;"
                   " Size ; M9Ks ; Mixed Width RDW Mode ;\n+--+\n"
                   "; a|rom ; ROM ; 256 ; 128 ; -- ; -- ; 32768 ; 4 ; -- ;\n+--+\n")
    (ram,) = reports.parse_fit_rams(rpt)
    assert ram["mode"] == "ROM" and ram["port_a_depth"] == 256 and ram["bits"] == 32768
    assert ram["port_b_depth"] is None and ram["port_b_width"] is None
    assert ram["mixed_rdw"] is None and ram["blocks"] == 4


def test_fit_rams_malformed_count(tmp_path):
    rpt = tmp_path / "x.fit.rpt"
    rpt.write_text("+--+\n; Fitter RAM Summary ;\n+--+\n; Name ; M9Ks ;\n+--+\n"
                   "; a ; many ;\n+--+\n")
    with pytest.raises(reports.ReportError, match="expected a count"):
        reports.parse_fit_rams(rpt)


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
    assert tables["Parallel Compilation"][1] == {"Processors": "Maximum allowed", "Number": "2"}
    assert tables["Timing Closure Recommendations"] == []


def test_sta_worst_across_corners():
    sta = reports.parse_sta(FIX / "output_files" / f"{REV}.sta.rpt")
    clocks = sta["clocks"]
    assert list(clocks) == ["CLK_A", "CLK_B"]
    assert clocks["CLK_A"] == {"fmax_mhz": 187.55, "restricted_fmax_mhz": 187.55,
                               "setup_slack_ns": 14.668, "hold_slack_ns": 0.181,
                               "recovery_slack_ns": 17.31, "removal_slack_ns": 1.204}
    assert clocks["CLK_B"] == {"fmax_mhz": 260.42, "restricted_fmax_mhz": 250.0,
                               "setup_slack_ns": 4.16, "hold_slack_ns": 0.187,
                               "recovery_slack_ns": None, "removal_slack_ns": None}
    assert sta["unconstrained"]["unconstrained_input_port_paths"] == {"setup": 0, "hold": 0}
    assert len(sta["unconstrained"]) == 6


def test_sta_missing_unconstrained_panel(tmp_path):
    rpt = tmp_path / "x.sta.rpt"
    text = (FIX / "output_files" / f"{REV}.sta.rpt").read_text()
    rpt.write_text(text.replace("Unconstrained Paths Summary", "Something Else"))
    with pytest.raises(ReportError, match="Unconstrained Paths Summary"):
        reports.parse_sta(rpt)


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
    assert [r["blocks"] for r in m["resources"]["rams"]] == [1]
    t = m["timing"]
    assert t["worst_setup_slack_ns"] == 4.16
    assert t["worst_hold_slack_ns"] == 0.181
    assert t["worst_recovery_slack_ns"] == 17.31
    assert t["worst_removal_slack_ns"] == 1.204
    assert t["unconstrained_total"] == 0
    assert t["met"] is True
    assert t["critical_path"]["to"] == "u_core|acc_q[15]"
    json.dumps(m)  # serializable


def test_collect_negative_slack_not_met(project):
    rpt = project / "output_files" / f"{REV}.sta.rpt"
    rpt.write_text(rpt.read_text().replace("; CLK_A ; 0.181 ;", "; CLK_A ; -0.050 ;"))
    m = reports.collect(project, REV)
    assert m["timing"]["worst_hold_slack_ns"] == -0.05
    assert m["timing"]["met"] is False


def test_collect_negative_removal_not_met(project):
    rpt = project / "output_files" / f"{REV}.sta.rpt"
    rpt.write_text(rpt.read_text().replace("; CLK_A ; 1.204 ;", "; CLK_A ; -0.300 ;"))
    m = reports.collect(project, REV)
    assert m["timing"]["worst_removal_slack_ns"] == -0.3
    assert m["timing"]["met"] is False


def test_collect_unconstrained_not_met(project):
    rpt = project / "output_files" / f"{REV}.sta.rpt"
    rpt.write_text(rpt.read_text().replace(
        "; Unconstrained Input Port Paths  ; 0     ; 0    ;",
        "; Unconstrained Input Port Paths  ; 3     ; 3    ;"))
    m = reports.collect(project, REV)
    assert m["timing"]["unconstrained_total"] == 6
    assert m["timing"]["met"] is False


def test_collect_deterministic(project):
    assert reports.collect(project, REV) == reports.collect(project, REV)
