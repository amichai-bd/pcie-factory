import shutil
from pathlib import Path

import pytest

from build.ip import discover
from checks import filelists

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def good(tmp_path) -> Path:
    dst = tmp_path / "src"
    shutil.copytree(FIXTURES / "good/src", dst)
    return dst


def run(src: Path, ip_ids=None):
    return filelists.run(src, discover(src), ip_ids)


def test_good_fixture_passes(good):
    r = run(good)
    assert r.passed, r.findings
    assert r.checked == ("7 filelist(s), 2 IP(s) with regression lists, "
                         "5 RTL file(s) for orphans")


def test_bad_fixture_fails():
    r = run(FIXTURES / "bad/src")
    text = "\n".join(r.findings)
    assert "blk/rtl/blk.rtl.f: lists blk/rtl/xy_blk.sv more than once" in text
    assert "broken/rtl/broken.rtl.f: does not resolve" in text
    assert "source not found: xy_broken.sv" in text
    assert "blk: missing blk/dv/regress/level2.yaml" in text
    assert "level1.yaml: tests[0]: a test entry must be a mapping" in text
    assert "blk/rtl/xy_blk_old.sv: orphan" in text


def test_duplicate_across_filelists(good):
    # The sub-IP's source listed again by the parent: reached twice.
    f = good / "blk/rtl/blk.rtl.f"
    f.write_text(f.read_text() + "../sub/rtl/xy_blk_sub.sv\n")
    r = run(good)
    assert r.findings == ["blk/dv/blk.sim.f: lists blk/sub/rtl/xy_blk_sub.sv more than once "
                          "after flattening",
                          "blk/rtl/blk.rtl.f: lists blk/sub/rtl/xy_blk_sub.sv more than once "
                          "after flattening"]


def test_diamond_include_is_not_a_duplicate(good):
    # The shared-block list reached through both the parent and the sub-IP.
    f = good / "blk/sub/rtl/sub.rtl.f"
    f.write_text("-F ../../../common/rtl/common_lib.rtl.f\n" + f.read_text())
    assert run(good).passed


def test_orphan_and_include_header(good):
    (good / "blk/rtl/xy_blk_extra.sv").write_text("")
    (good / "blk/rtl/include/sub").mkdir()
    (good / "blk/rtl/include/sub/x.svh").write_text("")  # reached through +incdir+
    (good / "common/rtl/common_spare.sv").write_text("")
    assert run(good).findings == [
        "blk/rtl/xy_blk_extra.sv: orphan: not reached by a filelist in blk/rtl/",
        "common/rtl/common_spare.sv: orphan: not reached by a filelist in common/rtl/",
    ]


def test_source_reached_only_from_another_rtl_dir_is_orphan(good):
    # Listed by the parent's filelist, but its own rtl/ has no list reaching it.
    (good / "blk/sub/rtl/xy_blk_sub2.sv").write_text("")
    f = good / "blk/rtl/blk.rtl.f"
    f.write_text(f.read_text() + "../sub/rtl/xy_blk_sub2.sv\n")
    assert run(good).findings == [
        "blk/sub/rtl/xy_blk_sub2.sv: orphan: not reached by a filelist in blk/sub/rtl/"]


def test_missing_dv_files_and_levels(good):
    (good / "blk/sub/dv/sub.dv.f").unlink()
    (good / "blk/sub/dv/regress/level0.yaml").unlink()
    r = run(good)
    assert "blk/sub: missing blk/sub/dv/sub.dv.f" in r.findings
    assert "blk/sub: missing blk/sub/dv/regress/level0.yaml" in r.findings
    assert any(f.startswith("blk/dv/blk.sim.f: does not resolve") for f in r.findings) is False
    assert any(f.startswith("blk/sub/dv/sub.sim.f: does not resolve") for f in r.findings)


def test_regress_duplicate_test_name(good):
    entry = "tests:\n  - {name: t, module: tests.t, top: xy_blk, timeout_s: 1}\n"
    (good / "blk/dv/regress/level0.yaml").write_text(entry)
    (good / "blk/dv/regress/level1.yaml").write_text(entry)
    r = run(good)
    assert len(r.findings) == 1 and "test 't' already defined in level0.yaml" in r.findings[0]


def test_ip_filter(good):
    (good / "common/rtl/common_spare.sv").write_text("")
    (good / "blk/sub/rtl/xy_blk_sub2.sv").write_text("")
    r = run(good, {"blk"})
    assert r.passed, r.findings  # common/ and the sub-IP are not selected
    assert r.checked.startswith("3 filelist(s), 1 IP(s)")
    assert len(run(good, {"blk/sub"}).findings) == 1
