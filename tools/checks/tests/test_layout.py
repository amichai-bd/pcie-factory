import shutil
from pathlib import Path

import pytest

from build import ip
from build.ip import REPO_ROOT, discover
from checks import layout
from checks.common import CheckConfigError
from cli.main import main

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def good(tmp_path) -> Path:
    shutil.copytree(FIXTURES / "good/src", tmp_path / "src")
    return tmp_path / "src"


def run(src: Path, ip_ids=None):
    return layout.run(src, discover(src), ip_ids)


def write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_good_fixture_passes(good):
    r = run(good)
    assert r.passed, r.findings
    assert r.checked == "7 IP or shared folder(s) under rtl/, dv/, arch/, fpga/, asic/"


def test_bad_fixture_fails():
    assert run(FIXTURES / "bad/src").findings == [
        "asic/ghost/: names no IP (no rtl/ghost/ghost.rtl.f)"]


def ip_first_tree(root: Path) -> Path:
    """The superseded IP-first layout: src/<ip>/<kind>/, sub-IPs nested."""
    write(root / "blk/rtl/blk.rtl.f", "-F ../sub/rtl/sub.rtl.f\nxy_blk.sv\n")
    write(root / "blk/rtl/xy_blk.sv")
    write(root / "blk/sub/rtl/sub.rtl.f", "xy_blk_sub.sv\n")
    write(root / "blk/sub/rtl/xy_blk_sub.sv")
    write(root / "blk/dv/blk.sim.f", "-F ../rtl/blk.rtl.f\n")
    write(root / "common/rtl/common_lib.rtl.f")
    write(root / "README.md")
    return root


def test_ip_first_layout_fails(tmp_path, capsys):
    src = ip_first_tree(tmp_path / "src")
    assert discover(src) == []  # nothing is an IP in the old form
    assert run(src).findings == [
        "blk/: not a kind folder (rtl/, dv/, arch/, fpga/, asic/); sources live in <kind>/<ip>/",
        "common/: not a kind folder (rtl/, dv/, arch/, fpga/, asic/); sources live in "
        "<kind>/<ip>/",
    ]
    assert main(["check", "--src-root", str(src), "--only", "layout"]) == 1
    assert "check: FAIL (layout)" in capsys.readouterr().out


def test_nested_sub_ip_is_rejected(good):
    """A sub-IP nested in its parent's folder instead of a flat sibling."""
    write(good / "rtl/blk/sub/sub.rtl.f", "x.sv\n")
    write(good / "dv/blk/sub/sub.sim.f")
    assert run(good).findings == [
        "rtl/blk/sub/sub.rtl.f: only blk.rtl.f may live in rtl/blk/ "
        "(a sub-IP is a flat sibling rtl/blk_<sub>/)",
        "dv/blk/sub/sub.sim.f: only blk.dv.f, blk.sim.f may live in dv/blk/ "
        "(a sub-IP is a flat sibling dv/blk_<sub>/)",
    ]


def test_top_level_only_kinds(good):
    for kind in ("arch", "fpga", "asic"):
        (good / kind / "blk_sub").mkdir(parents=True, exist_ok=True)
    assert run(good).findings == [
        "arch/blk_sub/: arch/ is for top-level IPs only (blk_sub is a sub-IP of blk)",
        "fpga/blk_sub/: fpga/ is for top-level IPs only (blk_sub is a sub-IP of blk)",
        "asic/blk_sub/: asic/ is for top-level IPs only (blk_sub is a sub-IP of blk)",
    ]


def test_folders_must_name_an_ip(good):
    (good / "dv/nope").mkdir()
    (good / "fpga/common").mkdir()  # common/ is for rtl/ and dv/ only
    (good / "dv/common/tests").mkdir()  # fine
    write(good / "rtl/stray.sv")
    assert run(good).findings == [
        "rtl/stray.sv: a kind folder holds only IP folders",
        "dv/nope/: names no IP (no rtl/nope/nope.rtl.f)",
        "fpga/common/: names no IP (no rtl/common/common.rtl.f)",
    ]


def test_shared_folder_filelist_names(good):
    write(good / "rtl/common/lib.rtl.f")             # no common_ prefix
    write(good / "rtl/common/sub/common_x.rtl.f")    # a group folder: allowed
    write(good / "rtl/common/sub/deep/common_y.rtl.f")  # two levels down
    write(good / "dv/common/common_x.sim.f")         # not the shared unit's lists
    write(good / "dv/common/tb/common.dv.f")
    want = "common_<name>.rtl.f directly in it or in a group folder <group>/ (one per shared block)"
    assert run(good).findings == [
        f"rtl/common/lib.rtl.f: rtl/common/ holds no filelist but {want}",
        f"rtl/common/sub/deep/common_y.rtl.f: rtl/common/ holds no filelist but {want}",
        "dv/common/common_x.sim.f: dv/common/ holds no filelist but common.dv.f and common.sim.f",
        "dv/common/tb/common.dv.f: dv/common/ holds no filelist but common.dv.f and common.sim.f",
    ]


GROUPS = {"cdc": ("common_cdc_*",), "ram": ("common_*_ram", "common_*_ram_*")}


def test_shared_groups(good):
    write(good / "rtl/common/common_macros.rtl.f")   # matches no group: stays at the root
    write(good / "rtl/common/common_pkg.sv")
    write(good / "rtl/common/cdc/common_cdc_a.rtl.f")
    write(good / "rtl/common/cdc/common_cdc_a.sv")
    write(good / "rtl/common/ram/common_sp_ram.rtl.f")
    write(good / "rtl/common/ram/common_sp_ram.sv")
    write(good / "rtl/common/ram/common_sp_ram_held.rtl.f")
    assert layout.run(good, discover(good), None, GROUPS).passed
    write(good / "rtl/common/common_cdc_b.rtl.f")     # a crossing cell outside cdc/
    write(good / "rtl/common/ram/common_cdc_c.rtl.f")  # ... and in the wrong group
    write(good / "rtl/common/ram/common_misc.rtl.f")   # no group's pattern
    write(good / "rtl/common/other/common_y.rtl.f")    # not a listed group
    assert layout.run(good, discover(good), None, GROUPS).findings == [
        "rtl/common/common_cdc_b.rtl.f: block common_cdc_b belongs in rtl/common/cdc/ "
        "(it matches common_cdc_*)",
        "rtl/common/ram/common_cdc_c.rtl.f: block common_cdc_c belongs in rtl/common/cdc/ "
        "(it matches common_cdc_*)",
        "rtl/common/ram/common_misc.rtl.f: block common_misc does not belong in group ram/ "
        "(its blocks match common_*_ram, common_*_ram_*)",
        "rtl/common/other/: not a shared-block group folder (cdc/, ram/; cfg/checks.yaml "
        "layout.shared_groups)",
    ]
    # Without the group rules only the folder shape is checked.
    assert layout.run(good, discover(good), None).passed


def test_shared_group_rules_apply_to_every_source(good):
    # A cell's source is judged by its file stem, whatever filelist lists it:
    # a crossing cell in ram/ or at the root fails, as does a RAM in cdc/.
    write(good / "rtl/common/ram/common_sp_ram.rtl.f",
          "common_sp_ram.sv\ncommon_cdc_x.sv\n")
    write(good / "rtl/common/ram/common_sp_ram.sv")
    write(good / "rtl/common/ram/common_cdc_x.sv")
    write(good / "rtl/common/common_cdc_y.sv")
    write(good / "rtl/common/cdc/common_sp_ram_wt.sv")
    # Anything two folders down is out of place, a cell included.
    write(good / "rtl/common/cdc/deep/common_cdc_z.sv")
    write(good / "rtl/common/cdc/deep/notes.txt")
    assert layout.run(good, discover(good), None, GROUPS).findings == [
        "rtl/common/cdc/common_sp_ram_wt.sv: source common_sp_ram_wt belongs in "
        "rtl/common/ram/ (it matches common_*_ram, common_*_ram_*)",
        "rtl/common/cdc/deep/common_cdc_z.sv: rtl/common/ holds files only directly in it or "
        "in a group folder <group>/",
        "rtl/common/cdc/deep/notes.txt: rtl/common/ holds files only directly in it or in a "
        "group folder <group>/",
        "rtl/common/common_cdc_y.sv: source common_cdc_y belongs in rtl/common/cdc/ "
        "(it matches common_cdc_*)",
        "rtl/common/ram/common_cdc_x.sv: source common_cdc_x belongs in rtl/common/cdc/ "
        "(it matches common_cdc_*)",
    ]
    # The depth rule holds without the group rules too.
    assert run(good).findings == [
        "rtl/common/cdc/deep/common_cdc_z.sv: rtl/common/ holds files only directly in it or "
        "in a group folder <group>/",
        "rtl/common/cdc/deep/notes.txt: rtl/common/ holds files only directly in it or in a "
        "group folder <group>/",
    ]


def test_name_in_two_groups(good):
    write(good / "rtl/common/cdc/common_cdc_ram.rtl.f")
    write(good / "rtl/common/cdc/common_cdc_ram.sv")
    assert layout.run(good, discover(good), None, GROUPS).findings == [
        "rtl/common/cdc/common_cdc_ram.rtl.f: block common_cdc_ram matches more than one group "
        "(cdc, ram); fix cfg/checks.yaml layout.shared_groups",
        "rtl/common/cdc/common_cdc_ram.sv: source common_cdc_ram matches more than one group "
        "(cdc, ram); fix cfg/checks.yaml layout.shared_groups",
    ]


def test_cli_reports_a_cell_outside_its_group(good, capsys):
    write(good / "rtl/common/common_cdc_out.sv")
    assert main(["check", "--src-root", str(good), "--only", "layout"]) == 1
    out = capsys.readouterr().out
    assert "rtl/common/common_cdc_out.sv: source common_cdc_out belongs in rtl/common/cdc/" in out


def test_cli_bad_layout_config_exits_2(good, tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / "cfg").mkdir(parents=True)
    (repo / "cfg/checks.yaml").write_text("layout: {shared_groups: {cdc: []}}\n")
    monkeypatch.setattr(ip, "REPO_ROOT", repo)
    assert main(["check", "--src-root", str(good), "--only", "layout"]) == 2
    assert "layout.shared_groups.cdc" in capsys.readouterr().err


def test_repository_groups_load():
    groups = layout.load_groups(REPO_ROOT)
    assert groups and all(p.startswith("common_") for pats in groups.values() for p in pats)


@pytest.mark.parametrize("text", ["layout: {}\n", "layout: {shared_groups: {}}\n",
                                  "layout: {shared_groups: {Bad: [common_x]}}\n",
                                  "layout: {shared_groups: {cdc: [x_*]}}\n",
                                  "layout: {shared_groups: {cdc: []}}\n", "other: 1\n"])
def test_bad_groups_config(tmp_path, text):
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg/checks.yaml").write_text(text)
    with pytest.raises(CheckConfigError):
        layout.load_groups(tmp_path)


def test_included_ip_without_parent_prefix_is_top_level(good):
    """blk includes inc, whose id is not blk_<name>: inc stays top-level and
    may have arch/, fpga/ and asic/."""
    write(good / "rtl/inc/inc.rtl.f")
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text("-F ../inc/inc.rtl.f\n" + f.read_text())
    for kind in ("arch", "fpga", "asic"):
        (good / kind / "inc").mkdir(parents=True)
    ips = {ip.id: ip for ip in discover(good)}
    assert ips["inc"].top_level and ips["inc"].ancestors == ("blk",)
    assert run(good).passed
    # Reached through blk_sub too, a blk_sub_<name> id makes it a sub-IP.
    (good / "rtl/inc").rename(good / "rtl/blk_sub_inc")
    (good / "rtl/blk_sub_inc/inc.rtl.f").rename(good / "rtl/blk_sub_inc/blk_sub_inc.rtl.f")
    f.write_text(f.read_text().replace("-F ../inc/inc.rtl.f\n", ""))
    g = good / "rtl/blk_sub/blk_sub.rtl.f"
    g.write_text("-F ../blk_sub_inc/blk_sub_inc.rtl.f\n" + g.read_text())
    for kind in ("arch", "fpga", "asic"):
        (good / kind / "inc").rename(good / kind / "blk_sub_inc")
    assert run(good).findings == [
        f"{kind}/blk_sub_inc/: {kind}/ is for top-level IPs only "
        "(blk_sub_inc is a sub-IP of blk, blk_sub)" for kind in ("arch", "fpga", "asic")]


def test_unconfirmed_parent_prefix_is_a_finding(good):
    """An id <other>_<name> claims <other> as its parent; when that IP's
    filelist does not reach it, the name and the filelists disagree."""
    write(good / "rtl/blk_orphan/blk_orphan.rtl.f")
    assert run(good).findings == [
        "rtl/blk_orphan/: id names blk as its parent, but rtl/blk/blk.rtl.f does not "
        "reach it; include it there with -F ../blk_orphan/blk_orphan.rtl.f, or rename it"]
    assert run(good, {"blk"}).passed  # reported for the misnamed IP only
    # Reached only through an integrating IP: still not confirmed by blk.
    write(good / "rtl/sys/sys.rtl.f",
          "-F ../blk/blk.rtl.f\n-F ../blk_orphan/blk_orphan.rtl.f\n")
    assert len(run(good, {"blk_orphan"}).findings) == 1
    # Including it from blk confirms the parent.
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text("-F ../blk_orphan/blk_orphan.rtl.f\n" + f.read_text())
    assert run(good).passed


def test_integration_fixture_passes():
    """Top-level sys integrates top-level a and b, which keep arch/, fpga/
    and asic/; a_x is a sub-IP of a."""
    assert run(FIXTURES / "integration/src").passed


def test_ip_filter(good):
    (good / "fpga/blk_sub").mkdir()
    (good / "junk").mkdir()
    assert run(good, {"blk"}).passed
    assert run(good, {"blk_sub"}).findings == [
        "fpga/blk_sub/: fpga/ is for top-level IPs only (blk_sub is a sub-IP of blk)"]
    assert len(run(good).findings) == 2


def test_backend_lists_sit_next_to_their_block(good):
    write(good / "rtl/common/common_lib.alt.f", "common_lib_alt.sv\n")   # beside common_lib.rtl.f
    write(good / "rtl/common/common_none.alt.f")                       # no common_none.rtl.f
    assert run(good).findings == [
        "rtl/common/common_none.alt.f: backend list of common_none, which has no "
        "common_none.rtl.f in the same folder",
    ]
