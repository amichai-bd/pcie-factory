import shutil
from pathlib import Path

import pytest

from build.ip import REPO_ROOT, discover
from checks import flows
from cli.main import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOOD_HOST = ("hosts:\n  ci:\n    orfs_image: \"orfs/img:1@sha256:" + "0" * 64 + "\"\n"
             "    orfs_cores: 2\n")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository root: the good fixture's src/ with fpga/ and asic/
    inputs for IP blk, and a cfg/ with valid flow settings."""
    shutil.copytree(FIXTURES / "good/src", tmp_path / "src")
    (tmp_path / "cfg").mkdir()
    shutil.copy(REPO_ROOT / "cfg/fpga.yaml", tmp_path / "cfg/fpga.yaml")
    (tmp_path / "cfg/host.yaml").write_text(GOOD_HOST)
    asic = tmp_path / "src/asic/blk"
    asic.mkdir(parents=True)
    (asic / "sky130hd.mk").write_text("export DESIGN_NAME = xy_blk\n")
    (asic / "xy_blk.sdc").write_text("create_clock -name clk -period 10 [get_ports clk_i]\n")
    return tmp_path


def run(root: Path, ip_ids=None):
    src = root / "src"
    return flows.run(src, discover(src), ip_ids, root, root / "workdir")


def test_good_tree_passes(repo):
    r = run(repo)
    assert r.passed, r.findings
    assert r.checked == ("1 FPGA and 1 ASIC target(s) with their flows' dry-run validation; "
                         "cfg/fpga.yaml, cfg/host.yaml ORFS")
    assert not (repo / "workdir").exists()  # validation writes nothing


def test_no_targets_reads_no_cfg(repo):
    shutil.rmtree(repo / "src/fpga")
    shutil.rmtree(repo / "src/asic")
    (repo / "cfg/fpga.yaml").write_text("board: [\n")
    (repo / "cfg/host.yaml").write_text("hosts: [\n")
    r = run(repo)
    assert r.passed and r.checked == "0 FPGA and 0 ASIC target(s) with their flows' dry-run validation"


def _write(name, text):
    return lambda root: (root / name).write_text(text)


def _unlink(name):
    return lambda root: (root / name).unlink()


FPGA = "src/fpga/blk/"
ASIC = "src/asic/blk/"

# One case per finding: (mutation of the good tree, expected finding text).
CASES = {
    "fpga-no-board-settings": (_unlink(FPGA + "de2_115.qsf.tcl"),
                               "fpga/blk/de2_115.qsf.tcl: board settings file not found"),
    "fpga-no-sdc": (_unlink(FPGA + "xy_blk_board.sdc"),
                    "fpga/blk: expected exactly one timing constraints file (*.sdc), found none"),
    "fpga-two-sdc": (_write(FPGA + "xy_blk_extra.sdc", ""),
                     "fpga/blk: expected exactly one timing constraints file (*.sdc), found "
                     "xy_blk_board.sdc, xy_blk_extra.sdc"),
    "fpga-two-wrappers": (_write(FPGA + "xy_blk_other.sv", ""),
                          "fpga/blk: expected exactly one board wrapper (*.sv), found "
                          "xy_blk_board.sv, xy_blk_other.sv"),
    "fpga-wrapper-module": (lambda r: (r / FPGA / "xy_blk_board.sv").rename(r / FPGA / "xy_blk_top.sv"),
                            "fpga/blk/xy_blk_top.sv: does not declare module xy_blk_top"),
    "fpga-wrapper-listed": (lambda r: (r / "src/rtl/blk/blk.rtl.f").open("a").write(
                                "../../fpga/blk/xy_blk_board.sv\n"),
                            "fpga/blk/xy_blk_board.sv: the board wrapper must not be in blk.rtl.f"),
    "fpga-cfg": (_write("cfg/fpga.yaml", "board: de2_115\nstep_timeout_s: 0\nprocessors: 1\n"),
                 "cfg/fpga.yaml: 'step_timeout_s' must be a positive number"),
    "asic-no-mk": (_unlink(ASIC + "sky130hd.mk"),
                   "IP blk has no ASIC config asic/blk/sky130hd.mk"),
    "asic-no-sdc": (_unlink(ASIC + "xy_blk.sdc"),
                    "IP blk: expected exactly one .sdc in asic/blk, found 0"),
    "asic-two-sdc": (_write(ASIC + "xy_blk_extra.sdc", ""),
                     "IP blk: expected exactly one .sdc in asic/blk, found 2"),
    "asic-tool-owned": (_write(ASIC + "sky130hd.mk",
                               "export DESIGN_NAME = xy_blk\noverride SDC_FILE := x.sdc\n"),
                        "asic/blk/sky130hd.mk: sets SDC_FILE, which ./build asic generates"),
    "asic-no-design-name": (_write(ASIC + "sky130hd.mk", "export CORE_UTILIZATION = 30\n"),
                            "asic/blk/sky130hd.mk: DESIGN_NAME is required"),
    "asic-image-not-pinned": (_write("cfg/host.yaml",
                                     "hosts:\n  ci:\n    orfs_image: \"orfs/img:1\"\n"),
                              "cfg/host.yaml: orfs_image 'orfs/img:1' is not pinned by digest"),
    "asic-no-orfs": (_write("cfg/host.yaml", "hosts:\n  ci:\n    description: x\n"),
                     "no ORFS configured: set a host's orfs_image or orfs_root in cfg/host.yaml"),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_each_finding(repo, case):
    mutate, expected = CASES[case]
    mutate(repo)
    r = run(repo)
    assert len(r.findings) == 1, r.findings
    assert r.findings[0].startswith(expected), r.findings
    assert str(repo) not in r.findings[0]  # paths are shown relative


def test_orfs_env_overrides_are_ignored(repo, monkeypatch):
    (repo / "cfg/host.yaml").write_text("hosts:\n  ci:\n    orfs_image: \"orfs/img:1\"\n")
    monkeypatch.setenv("ORFS_ROOT", "/opt/orfs")
    monkeypatch.setenv("ORFS_RUNNER", "native")
    assert "is not pinned by digest" in run(repo).findings[0]


def test_ip_selection(repo):
    (repo / FPGA / "xy_blk_extra.sdc").write_text("")
    assert run(repo, {"blk_sub"}).passed
    assert run(repo, {"blk_sub"}).checked.startswith("0 FPGA and 0 ASIC")
    assert not run(repo, {"blk"}).passed


def test_flows_bad_fixture_findings():
    src = FIXTURES / "flows_bad/src"
    r = flows.run(src, discover(src), None, REPO_ROOT, REPO_ROOT / "workdir")
    assert r.findings == [
        "fpga/blk/de2_115.qsf.tcl: board settings file not found",
        "IP blk: expected exactly one .sdc in asic/blk, found 0",
    ]


def test_cli_only_flows(capsys):
    assert main(["check", "--src-root", str(FIXTURES / "flows_bad/src"), "--only", "flows"]) == 1
    out = capsys.readouterr().out
    assert "[flows] FAIL (2)" in out and out.splitlines()[-1] == "check: FAIL (flows)"
