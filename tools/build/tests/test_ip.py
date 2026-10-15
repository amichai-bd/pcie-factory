from pathlib import Path

import pytest

from build.ip import (DiscoveryError, block_name, discover, find_ip, locate, make_context,
                      owner_ip, shared_blocks, shared_unit, units)


def make_ip(root: Path, ip: str, reaches: tuple[str, ...] = ()) -> None:
    """``rtl/<ip>/<ip>.rtl.f`` including each IP in ``reaches`` with -F."""
    d = root / "rtl" / ip
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ip}.rtl.f").write_text("".join(f"-F ../{r}/{r}.rtl.f\n" for r in reaches))


def test_discover_flat(tmp_path):
    make_ip(tmp_path, "a_b")
    make_ip(tmp_path, "a", ("a_b",))
    make_ip(tmp_path, "z")
    (tmp_path / "rtl/common").mkdir(parents=True)  # shared blocks, never an IP
    (tmp_path / "rtl/common/common.rtl.f").write_text("")
    (tmp_path / "rtl/x").mkdir(parents=True)
    (tmp_path / "rtl/x/other.rtl.f").write_text("")  # name mismatch -> not an IP
    (tmp_path / "dv/y").mkdir(parents=True)  # no rtl/y/ -> not an IP
    ips = discover(tmp_path)
    assert [ip.id for ip in ips] == ["a", "a_b", "z"]
    sub = find_ip(ips, "a_b")
    assert sub.rtl_f == tmp_path / "rtl/a_b/a_b.rtl.f"
    assert sub.sim_f == tmp_path / "dv/a_b/a_b.sim.f"
    assert sub.dv_f == tmp_path / "dv/a_b/a_b.dv.f"
    assert sub.level_file(1) == tmp_path / "dv/a_b/regress/level1.yaml"
    assert sub.dir("fpga") == tmp_path / "fpga/a_b"


def test_top_level_versus_sub_ip(tmp_path):
    # a reaches a_b directly and a_b_c through a_b; d reaches a_b_c too.
    make_ip(tmp_path, "a_b_c")
    make_ip(tmp_path, "a_b", ("a_b_c",))
    make_ip(tmp_path, "a", ("a_b",))
    make_ip(tmp_path, "d", ("a_b_c",))
    ips = {ip.id: ip for ip in discover(tmp_path)}
    assert ips["a"].top_level and ips["d"].top_level
    assert ips["a_b"].ancestors == ("a",) and not ips["a_b"].top_level
    assert ips["a_b_c"].ancestors == ("a", "a_b", "d")
    assert ips["a_b_c"].parents == ("a", "a_b") and not ips["a_b_c"].top_level


def test_integrated_ips_stay_top_level(tmp_path):
    # sys integrates a and b; a reaches its sub-IP a_x; a_y is reached only
    # by sys, so its prefix alone does not make it a sub-IP of a (the layout
    # check reports that unconfirmed prefix).
    make_ip(tmp_path, "a_x")
    make_ip(tmp_path, "a_y")
    make_ip(tmp_path, "a", ("a_x",))
    make_ip(tmp_path, "b")
    make_ip(tmp_path, "sys", ("a", "b", "a_y"))
    ips = {ip.id: ip for ip in discover(tmp_path)}
    for top in ("sys", "a", "b", "a_y"):
        assert ips[top].top_level and ips[top].parents == (), top
    assert ips["a"].ancestors == ("sys",) and ips["b"].ancestors == ("sys",)
    assert ips["a_x"].ancestors == ("a", "sys") and ips["a_x"].parents == ("a",)
    assert not ips["a_x"].top_level


def test_broken_filelist_reaches_nothing(tmp_path):
    make_ip(tmp_path, "a_b")
    make_ip(tmp_path, "a", ("a_b", "missing"))
    ips = {ip.id: ip for ip in discover(tmp_path)}
    assert ips["a_b"].top_level  # the filelist check reports the broken -F


def test_include_cycle_makes_every_ip_in_it_top_level(tmp_path):
    # a_b and a_b_c include each other: neither resolves, so neither reaches
    # the other and both are top-level (the filelist check reports the cycle).
    make_ip(tmp_path, "a_b", ("a_b_c",))
    make_ip(tmp_path, "a_b_c", ("a_b",))
    assert all(ip.top_level for ip in discover(tmp_path))


def test_discover_fixture(fixture_src):
    ips = discover(fixture_src)
    assert [ip.id for ip in ips] == ["counter", "counter_inc"]
    assert [ip.top_level for ip in ips] == [True, False]


def test_unknown_ip(tmp_path):
    make_ip(tmp_path, "a")
    with pytest.raises(DiscoveryError, match="unknown IP 'b' \\(known: a\\)"):
        find_ip(discover(tmp_path), "b")


def test_reserved_ip_id(tmp_path):
    make_ip(tmp_path, "regress")
    with pytest.raises(DiscoveryError, match="reserved"):
        discover(tmp_path)


def test_missing_root(tmp_path):
    with pytest.raises(DiscoveryError, match="source root not found"):
        discover(tmp_path / "nope")


def test_owner_by_kind_folder(tmp_path):
    make_ip(tmp_path, "a")
    make_ip(tmp_path, "a_b")
    ips = discover(tmp_path)
    assert owner_ip(ips, tmp_path / "rtl/a_b/x.sv").id == "a_b"
    assert owner_ip(ips, tmp_path / "dv/a/tests/t.py").id == "a"
    assert owner_ip(ips, tmp_path / "fpga/a/a.sdc").id == "a"
    assert owner_ip(ips, tmp_path / "rtl/a_bc/x.sv") is None
    assert owner_ip(ips, tmp_path / "rtl/common/c.sv") is None
    assert owner_ip(ips, tmp_path / "README.md") is None


def test_shared_unit_and_blocks(tmp_path):
    make_ip(tmp_path, "a")
    common = tmp_path / "rtl/common"
    common.mkdir(parents=True)
    (common / "common_y.rtl.f").write_text("")
    (common / "common_x.rtl.f").write_text("")
    (common / "notes.md").write_text("")
    assert shared_blocks(tmp_path) == [common / "common_x.rtl.f", common / "common_y.rtl.f"]
    assert block_name(common / "common_x.rtl.f") == "common_x"
    assert shared_blocks(tmp_path / "nope") == []
    # No dv/common/common.sim.f: no shared unit, units() is discover().
    assert shared_unit(tmp_path) is None
    assert [ip.id for ip in units(tmp_path)] == ["a"]
    (tmp_path / "dv/common").mkdir(parents=True)
    (tmp_path / "dv/common/common.sim.f").write_text("")
    unit = shared_unit(tmp_path)
    assert unit.id == "common" and unit.shared and not unit.top_level
    assert unit.sim_f == tmp_path / "dv/common/common.sim.f"
    assert unit.level_file(0) == tmp_path / "dv/common/regress/level0.yaml"
    assert [ip.id for ip in units(tmp_path)] == ["a", "common"]
    assert [ip.id for ip in discover(tmp_path)] == ["a"]  # never an IP
    assert owner_ip(units(tmp_path), common / "common_x.sv").id == "common"


def test_shared_unit_is_in_no_ip_relation(tmp_path):
    """The shared unit is never a parent, ancestor or sub-IP, even when IPs
    include its blocks with -F."""
    common = tmp_path / "rtl/common"
    common.mkdir(parents=True)
    (common / "common_x.rtl.f").write_text("")
    (tmp_path / "dv/common").mkdir(parents=True)
    (tmp_path / "dv/common/common.sim.f").write_text("-F ../../rtl/common/common_x.rtl.f\n")
    make_ip(tmp_path, "a_b")
    make_ip(tmp_path, "a", ("a_b",))
    (tmp_path / "rtl/a/a.rtl.f").write_text("-F ../common/common_x.rtl.f\n-F ../a_b/a_b.rtl.f\n")
    got = {ip.id: ip for ip in units(tmp_path)}
    assert got["common"].ancestors == () and got["common"].parents == ()
    assert not got["common"].top_level
    assert got["a"].top_level and got["a"].ancestors == ()
    assert got["a_b"].parents == ("a",)


def test_locate(tmp_path):
    assert locate(tmp_path, tmp_path / "rtl/a/x.sv") == ("rtl", "a")
    assert locate(tmp_path, tmp_path / "dv") == ("dv", None)
    assert locate(tmp_path, tmp_path / "a/rtl/x.sv") == (None, None)
    assert locate(tmp_path, tmp_path.parent / "x") == (None, None)


def test_context_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BUILD_SRC_ROOT", str(tmp_path / "s"))
    monkeypatch.setenv("BUILD_WORKDIR", str(tmp_path / "w"))
    ctx = make_context()
    assert ctx.src_root == tmp_path / "s" and ctx.workdir == tmp_path / "w"
    ctx = make_context(str(tmp_path / "s2"), None)
    assert ctx.src_root == tmp_path / "s2"
    assert (ctx.cfg_dir / "levels.yaml").is_file()
