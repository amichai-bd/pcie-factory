import shutil
from pathlib import Path

import pytest

from build.ip import discover
from checks import layout
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
    assert r.checked == "6 IP or shared folder(s) under rtl/, dv/, arch/, fpga/, asic/"


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
    (good / "dv/common").mkdir()    # fine
    write(good / "rtl/stray.sv")
    assert run(good).findings == [
        "rtl/stray.sv: a kind folder holds only IP folders",
        "dv/nope/: names no IP (no rtl/nope/nope.rtl.f)",
        "fpga/common/: names no IP (no rtl/common/common.rtl.f)",
    ]


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
