from pathlib import Path

import pytest

from build.ip import DiscoveryError, discover, find_ip, make_context, owner_ip


def make_ip(root: Path, rel: str) -> None:
    d = root / rel
    (d / "rtl").mkdir(parents=True, exist_ok=True)
    (d / "rtl" / f"{d.name}.rtl.f").write_text("")


def test_discover_nested(tmp_path):
    make_ip(tmp_path, "a")
    make_ip(tmp_path, "a/b")
    make_ip(tmp_path, "a/b/c")
    make_ip(tmp_path, "z")
    (tmp_path / "common/rtl").mkdir(parents=True)  # no common.rtl.f -> not an IP
    (tmp_path / "x/rtl").mkdir(parents=True)
    (tmp_path / "x/rtl/other.rtl.f").write_text("")  # name mismatch -> not an IP
    ips = discover(tmp_path)
    assert [ip.id for ip in ips] == ["a", "a/b", "a/b/c", "z"]
    assert find_ip(ips, "a/b").name == "b"
    assert ips[1].sim_f == tmp_path / "a/b/dv/b.sim.f"


def test_discover_fixture(fixture_src):
    assert [ip.id for ip in discover(fixture_src)] == ["counter", "counter/inc"]


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


def test_owner_is_innermost(tmp_path):
    make_ip(tmp_path, "a")
    make_ip(tmp_path, "a/b")
    ips = discover(tmp_path)
    assert owner_ip(ips, tmp_path / "a/b/rtl/b.sv").id == "a/b"
    assert owner_ip(ips, tmp_path / "a/rtl/a.sv").id == "a"
    assert owner_ip(ips, tmp_path / "README.md") is None


def test_context_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BUILD_SRC_ROOT", str(tmp_path / "s"))
    monkeypatch.setenv("BUILD_WORKDIR", str(tmp_path / "w"))
    ctx = make_context()
    assert ctx.src_root == tmp_path / "s" and ctx.workdir == tmp_path / "w"
    ctx = make_context(str(tmp_path / "s2"), None)
    assert ctx.src_root == tmp_path / "s2"
    assert (ctx.cfg_dir / "levels.yaml").is_file()
