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
    assert "rtl/blk/blk.rtl.f: lists rtl/blk/xy_blk.sv more than once" in text
    assert "rtl/broken/broken.rtl.f: does not resolve" in text
    assert "source not found: xy_broken.sv" in text
    assert "blk: missing dv/blk/regress/level2.yaml" in text
    assert "level1.yaml: tests[0]: a test entry must be a mapping" in text
    assert "rtl/blk/xy_blk_old.sv: orphan" in text


def test_duplicate_across_filelists(good):
    # The sub-IP's source listed again by the parent: reached twice.
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text(f.read_text() + "../blk_sub/xy_blk_sub.sv\n")
    r = run(good)
    assert r.findings == ["dv/blk/blk.sim.f: lists rtl/blk_sub/xy_blk_sub.sv more than once "
                          "after flattening",
                          "rtl/blk/blk.rtl.f: lists rtl/blk_sub/xy_blk_sub.sv more than once "
                          "after flattening"]


def test_diamond_include_is_not_a_duplicate(good):
    # The shared-block list reached through both the parent and the sub-IP.
    f = good / "rtl/blk_sub/blk_sub.rtl.f"
    f.write_text("-F ../common/common_lib.rtl.f\n" + f.read_text())
    assert run(good).passed


def test_include_cycle_makes_every_ip_in_it_top_level_and_fails(good):
    # The sub-IP includes its parent back: neither filelist resolves, so
    # discovery sees no reach (both IPs are top-level) and the check fails.
    f = good / "rtl/blk_sub/blk_sub.rtl.f"
    f.write_text("-F ../blk/blk.rtl.f\n" + f.read_text())
    ips = discover(good)
    assert all(ip.top_level for ip in ips)
    r = filelists.run(good, ips, None)
    assert not r.passed
    text = "\n".join(r.findings)
    for ip in ("blk", "blk_sub"):
        assert f"rtl/{ip}/{ip}.rtl.f: does not resolve" in text
    assert "cycle" in text


def test_orphan_and_include_header(good):
    (good / "rtl/blk/xy_blk_extra.sv").write_text("")
    (good / "rtl/blk/include/sub").mkdir()
    (good / "rtl/blk/include/sub/x.svh").write_text("")  # reached through +incdir+
    (good / "rtl/common/common_spare.sv").write_text("")
    assert run(good).findings == [
        "rtl/blk/xy_blk_extra.sv: orphan: not reached by a filelist in rtl/blk/",
        "rtl/common/common_spare.sv: orphan: not reached by a filelist in rtl/common/",
    ]


def test_source_reached_only_from_another_rtl_dir_is_orphan(good):
    # Listed by the parent's filelist, but its own folder has no list reaching it.
    (good / "rtl/blk_sub/xy_blk_sub2.sv").write_text("")
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text(f.read_text() + "../blk_sub/xy_blk_sub2.sv\n")
    assert run(good).findings == [
        "rtl/blk_sub/xy_blk_sub2.sv: orphan: not reached by a filelist in rtl/blk_sub/"]


def test_missing_dv_files_and_levels(good):
    (good / "dv/blk_sub/blk_sub.dv.f").unlink()
    (good / "dv/blk_sub/regress/level0.yaml").unlink()
    r = run(good)
    assert "blk_sub: missing dv/blk_sub/blk_sub.dv.f" in r.findings
    assert "blk_sub: missing dv/blk_sub/regress/level0.yaml" in r.findings
    assert any(f.startswith("dv/blk/blk.sim.f: does not resolve") for f in r.findings) is False
    assert any(f.startswith("dv/blk_sub/blk_sub.sim.f: does not resolve") for f in r.findings)


def test_regress_duplicate_test_name(good):
    entry = "tests:\n  - {name: t, module: tests.t, top: xy_blk, timeout_s: 1}\n"
    (good / "dv/blk/regress/level0.yaml").write_text(entry)
    (good / "dv/blk/regress/level1.yaml").write_text(entry)
    r = run(good)
    assert len(r.findings) == 1 and "test 't' already defined in level0.yaml" in r.findings[0]


def test_ip_filter(good):
    (good / "rtl/common/common_spare.sv").write_text("")
    (good / "rtl/blk_sub/xy_blk_sub2.sv").write_text("")
    r = run(good, {"blk"})
    assert r.passed, r.findings  # common/ and the sub-IP are not selected
    assert r.checked.startswith("3 filelist(s), 1 IP(s)")
    assert len(run(good, {"blk_sub"}).findings) == 1


VLT = "`verilator_config\nlint_off -rule UNUSEDSIGNAL\n"
V_FILE = "module xy_blk_v (a, y);\n  input a; output y;\n  wire a;\n  assign y = a;\nendmodule\n"


@pytest.mark.parametrize("name, text", [("waive.vlt", VLT), ("xy_blk_v.v", V_FILE)])
def test_non_sv_source_in_rtl_closure_fails(good, name, text):
    # A .vlt would waive warnings outside the reviewed waiver file; a .v
    # would escape the style check. Both fail even when listed.
    (good / "rtl/blk" / name).write_text(text)
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text(f.read_text() + name + "\n")
    r = run(good)
    assert f"rtl/blk/blk.rtl.f: source rtl/blk/{name} is not .sv/.svh " \
           "(synthesizable closures hold SystemVerilog only)" in r.findings
    assert f"dv/blk/blk.sim.f: source rtl/blk/{name} is not .sv/.svh " \
           "(synthesizable closures hold SystemVerilog only)" not in r.findings  # dv is free
    assert f"rtl/blk/{name}: file type not allowed under rtl/ (only .sv, .svh, .f, .md)" \
        in r.findings


def test_non_sv_source_reached_from_outside_rtl_fails(good):
    (good / "dv/blk/misc").mkdir()
    (good / "dv/blk/misc/waive.vlt").write_text(VLT)
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text(f.read_text() + "../../dv/blk/misc/waive.vlt\n")
    assert run(good).findings == [
        "rtl/blk/blk.rtl.f: source dv/blk/misc/waive.vlt is not below rtl/ "
        "(a synthesizable closure lists only rtl/ files)",
        "rtl/blk/blk.rtl.f: source dv/blk/misc/waive.vlt is not .sv/.svh "
        "(synthesizable closures hold SystemVerilog only)"]


def test_foreign_files_under_rtl_and_fpga_fail_even_unlisted(good):
    (good / "fpga/blk/waive.vlt").write_text(VLT)
    (good / "rtl/common/common_old.v").write_text(V_FILE)
    (good / "fpga/blk/xy_blk.sdc").write_text("")  # constraints are fine
    (good / "rtl/common/README.md").write_text("notes\n")  # docs are allowed
    assert run(good).findings == [
        "rtl/common/common_old.v: file type not allowed under rtl/ (only .sv, .svh, .f, .md)",
        "fpga/blk/waive.vlt: .vlt file not allowed under fpga/ "
        "(SystemVerilog .sv/.svh only; waivers go in the waiver file)",
    ]


WAIVE_TXT = "`verilator_config\nlint_off -rule UNUSEDSIGNAL\n"


def test_include_route_any_extension_under_rtl_fails(good):
    # `include "w.txt" from an incdir under rtl/: the allowlist rejects it.
    (good / "rtl/blk/include/w.txt").write_text(WAIVE_TXT)
    assert run(good).findings == [
        "rtl/blk/include/w.txt: file type not allowed under rtl/ (only .sv, .svh, .f, .md)"]


def test_incdir_outside_rtl_fails(good):
    # `include "w.txt" through +incdir+ ../dv: the include dir itself fails.
    (good / "dv/blk/w.txt").write_text("// verilator lint_off UNUSEDSIGNAL\n")
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text("+incdir+../../dv/blk\n" + f.read_text())
    msg = ("rtl/blk/blk.rtl.f: +incdir+ dv/blk is not below rtl/ "
           "(a synthesizable closure includes only from rtl/)")
    assert run(good).findings == [msg]  # the dv/ sim closure may include from dv/


def test_incdir_outside_src_root_fails(good, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text(f"+incdir+{tmp_path / 'elsewhere'}\n" + f.read_text())
    assert any("+incdir+" in x and "elsewhere is not below rtl/" in x
               for x in run(good).findings)


def test_listed_source_outside_rtl_fails(good):
    # Route (b): a .svh listed from dv/ passes the suffix rule but is never
    # style-checked; it must sit below rtl/.
    (good / "dv/blk/xy_blk_cfg.svh").write_text("`verilator_config\n")
    f = good / "rtl/blk/blk.rtl.f"
    f.write_text(f.read_text() + "../../dv/blk/xy_blk_cfg.svh\n")
    assert run(good).findings == [
        "rtl/blk/blk.rtl.f: source dv/blk/xy_blk_cfg.svh is not below rtl/ "
        "(a synthesizable closure lists only rtl/ files)"]



def test_bad_coverage_floor_is_a_finding(good):
    ip = sorted(p.parent.parent.name for p in good.glob("dv/*/regress/level2.yaml"))[0]
    level = good / f"dv/{ip}/regress/level2.yaml"
    level.write_text(level.read_text() + "coverage_floors: {line: 120}\n")
    r = run(good)
    assert not r.passed
    assert any("coverage_floors: line must be a percentage" in str(f) for f in r.findings), r.findings


def test_cycle_between_top_level_ips_fails(tmp_path):
    src = tmp_path / "src"
    shutil.copytree(FIXTURES / "integration/src", src)
    b = src / "rtl/b/b.rtl.f"
    b.write_text("-F ../sys/sys.rtl.f\n" + b.read_text())
    text = "\n".join(run(src).findings)
    assert "rtl/b/b.rtl.f: does not resolve" in text and "-F cycle" in text
