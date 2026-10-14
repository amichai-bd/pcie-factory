"""Report parsers on the fixture reports (fixtures/orfs/README.md)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from asic import metrics

ORFS = Path(__file__).resolve().parent / "fixtures" / "orfs"


def text(rel: str) -> str:
    return (ORFS / rel).read_text()


def step_metrics() -> dict:
    return metrics.merge_step_json([p.read_text() for p in sorted((ORFS / "logs").glob("*.json"))])


def test_latest_stage_wins():
    step = step_metrics()
    # Synthesis count 105; the routed design adds tap, clock and repair cells.
    assert step["synth__design__instance__count__stdcell"] == 105
    assert metrics.latest(step, "design__instance__count__stdcell") == 167
    assert metrics.latest(step, "timing__setup__ws") == pytest.approx(6.01343)
    assert metrics.latest(step, "no__such__metric") is None


def test_merge_skips_bad_json():
    assert metrics.merge_step_json(['{"a": 1}', "not json", "[1]", '{"a": 2, "b": 3}']) == \
        {"a": 2, "b": 3}


def test_synth_stat():
    assert metrics.parse_synth_stat(text("reports/synth_stat.txt")) == \
        {"cells": 105, "area_um2": pytest.approx(1253.7024)}


def test_synth_stat_old_format():
    old = "   Number of cells:                 42\n\n   Chip area for module '\\x': 12.5\n"
    assert metrics.parse_synth_stat(old) == {"cells": 42, "area_um2": 12.5}
    assert metrics.parse_synth_stat("") == {}


def test_worst_path():
    path = metrics.parse_worst_path(text("reports/6_finish.rpt"))
    assert path == {
        "from": "valid_i",
        "from_kind": "input port clocked by clk",
        "to": "u_inc.state_q[10]$_SDFFE_PP1P_",
        "to_kind": "rising edge-triggered flip-flop clocked by clk",
        "group": "clk",
        "slack_ns": 6.01,
        # input11 (input buffer), _092_, _102_, _105_; clock buffers excluded.
        "cells": 4,
    }


REG_TO_REG = """\
finish report_checks -path_delay max
--------------------------------------------------------------------------
Startpoint: a_q$_DFF_P_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: b_q$_DFF_P_ (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

                  0.00    0.00 ^ clkbuf_0_clk/A (sky130_fd_sc_hd__clkbuf_8)
     2    0.01    0.04    0.10 ^ clkbuf_0_clk/X (sky130_fd_sc_hd__clkbuf_8)
                  0.04    0.00 ^ a_q$_DFF_P_/CLK (sky130_fd_sc_hd__dfxtp_1)
     1    0.00    0.05    0.30 ^ a_q$_DFF_P_/Q (sky130_fd_sc_hd__dfxtp_1)
                  0.05    0.00 ^ _1_/A (sky130_fd_sc_hd__inv_1)
     1    0.00    0.05    0.30 v _1_/Y (sky130_fd_sc_hd__inv_1)
                  0.05    0.00 v b_q$_DFF_P_/D (sky130_fd_sc_hd__dfxtp_1)
                                  0.70   data arrival time
                                 -0.12   slack (VIOLATED)
"""


def test_worst_path_reg_to_reg_violated():
    path = metrics.parse_worst_path(REG_TO_REG)
    assert (path["from"], path["to"], path["cells"]) == ("a_q$_DFF_P_", "b_q$_DFF_P_", 1)
    assert path["slack_ns"] == -0.12


@pytest.mark.parametrize("bad", ["", "finish report_checks -path_delay max\nnothing here\n"])
def test_worst_path_absent(bad):
    assert metrics.parse_worst_path(bad) is None


def test_summary_and_required():
    extra = json.loads(text("reports/asic_extra.json"))
    summary = metrics.summarize(step_metrics(), metrics.parse_synth_stat(text("reports/synth_stat.txt")),
                                metrics.parse_worst_path(text("reports/6_finish.rpt")), extra)
    assert summary["timing"] == {"setup_ws_ns": pytest.approx(6.01343), "wns_ns": 0.0,
                                 "tns_ns": 0, "hold_ws_ns": pytest.approx(0.248099)}
    assert summary["area"]["stdcell_um2"] == pytest.approx(1443.88)
    assert summary["cells"] == 167 and summary["synth_cells"] == 105
    assert summary["utilization"] == pytest.approx(0.360963)
    assert summary["max_fanout"] == {"value": 25, "net": "net11"}
    assert summary["route_drc_errors"] == 0
    assert metrics.missing(summary) == []


def test_negative_slack_is_wns():
    summary = metrics.summarize({"finish__timing__setup__ws": -0.4}, {}, None, {})
    assert summary["timing"]["wns_ns"] == -0.4
    assert "worst_path.from" in metrics.missing(summary)


def test_synth_fallback_when_json_lacks_counts():
    summary = metrics.summarize({}, {"cells": 7, "area_um2": 3.5}, None, {})
    assert (summary["cells"], summary["area"]["stdcell_um2"]) == (7, 3.5)


def test_collect_from_work_home(tmp_path):
    logs = tmp_path / "logs" / "sky130hd" / "n" / "base"
    reports = tmp_path / "reports" / "sky130hd" / "n" / "base"
    shutil.copytree(ORFS / "logs", logs)
    shutil.copytree(ORFS / "reports", reports)
    (logs / "broken.json").write_text("{")
    summary = metrics.collect(tmp_path, "sky130hd", "n")
    assert summary["cells"] == 167 and metrics.missing(summary) == []


def test_collect_empty_work_home(tmp_path):
    summary = metrics.collect(tmp_path, "sky130hd", "n")
    assert set(metrics.missing(summary)) == set(metrics.REQUIRED)
