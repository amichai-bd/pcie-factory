import json
import os
import shutil
import time
from pathlib import Path

import pytest

from build import cache as build_cache
from build import sim
from build.ip import discover, find_ip
from build.tests.test_sim import needs_sim


# ------------------------------------------------------------- location

def test_cache_dir_default_override_off_and_inside_repo(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    env = {"XDG_CACHE_HOME": str(tmp_path / "xdg")}
    assert build_cache.cache_dir(repo, env) == (tmp_path / "xdg/myrepo/build").resolve()
    env[build_cache.ENV] = str(tmp_path / "elsewhere")
    assert build_cache.cache_dir(repo, env) == (tmp_path / "elsewhere").resolve()
    env[build_cache.ENV] = "OFF"
    assert build_cache.cache_dir(repo, env) is None
    env[build_cache.ENV] = str(repo / "cache")
    with pytest.raises(build_cache.CacheError, match="inside the repository"):
        build_cache.cache_dir(repo, env)


def test_from_option_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv(build_cache.ENV, str(tmp_path / "c"))
    assert build_cache.from_option(tmp_path / "r", disabled=True) is None
    c = build_cache.from_option(tmp_path / "r")
    assert c.root == (tmp_path / "c").resolve() and (c.root / "builds").is_dir()


def test_show_writes_home_as_tilde():
    assert build_cache.show(Path.home() / "x/y") == "~/x/y"
    assert build_cache.show(Path("/opt/z")) == "/opt/z"


# ------------------------------------------------------------------ key

def _key(src_root: Path, verilator: Path, **kw) -> str:
    ip = find_ip(discover(src_root), "counter")
    spec = sim.BuildSpec(ip, "counter", **kw)
    from build.filelist import resolve
    flat = resolve(ip.sim_f)
    return sim.content_key(spec, flat.sources, flat.incdirs, flat.defines, verilator)


def test_key_same_across_worktrees_and_changes_with_every_input(fixture_src, tmp_path,
                                                                  monkeypatch):
    monkeypatch.setattr(sim, "tool_ident", lambda v: {"verilator": "v1"})
    fake = Path("/fake/verilator")
    other = tmp_path / "other/src"
    shutil.copytree(fixture_src, other)
    base = _key(fixture_src, fake)
    assert _key(other, fake) == base  # same content, another worktree
    assert _key(fixture_src, fake, params={"W": 4}) != base
    assert _key(fixture_src, fake, coverage=True) != base
    assert _key(fixture_src, fake, waves=True) != base
    rtl = next((fixture_src / "rtl/counter").glob("*.sv"))
    rtl.write_text(rtl.read_text() + "\n// changed\n")
    assert _key(fixture_src, fake) != base
    monkeypatch.setattr(sim, "tool_ident", lambda v: {"verilator": "v2"})
    assert _key(other, fake) != base


def test_key_changes_with_the_build_driver(fixture_src, tmp_path, monkeypatch):
    """A change to how the build is made (tools/build/*.py) is a new key,
    even when no setting, source or tool version changed."""
    real = sim.driver_files()  # the real driver is hashed
    assert Path(sim.__file__).resolve() in real and any(p.name == "filelist.py" for p in real)
    monkeypatch.setattr(sim, "tool_ident", lambda v: {"verilator": "v1"})
    driver = tmp_path / "driver"
    driver.mkdir()
    (driver / "sim.py").write_text("runner.build(a)\n")
    monkeypatch.setattr(sim, "driver_files", lambda: sorted(driver.glob("*.py")))
    base = _key(fixture_src, Path("/fake/verilator"))
    (driver / "sim.py").write_text("runner.build(a, timescale=(1, 1))\n")
    assert _key(fixture_src, Path("/fake/verilator")) != base


# ---------------------------------------------------------------- store

def test_store_is_atomic_and_keeps_the_first_writer(tmp_path):
    c = build_cache.Cache(tmp_path / "c")
    exe = tmp_path / "top"
    exe.write_text("binary-1")
    assert c.lookup("top", "k" * 64) is None
    entry = c.store("top", "k" * 64, [exe], {"root": "/a"})
    assert (entry / "top").read_text() == "binary-1"
    assert c.lookup("top", "k" * 64)["root"] == "/a"
    exe.write_text("binary-2")  # a concurrent writer of the same key
    assert c.store("top", "k" * 64, [exe], {"root": "/b"}) == entry
    assert (entry / "top").read_text() == "binary-1"
    assert not [p for p in (c.root / "builds").iterdir() if p.name.startswith(".tmp-")]


def test_partial_entry_is_not_a_hit(tmp_path):
    c = build_cache.Cache(tmp_path / "c")
    d = c.entry("top", "a" * 64)
    d.mkdir(parents=True)
    (d / "top").write_text("half")  # no meta.json: never finished
    assert c.lookup("top", "a" * 64) is None
    (d / build_cache.META).write_text(json.dumps({"key": "b" * 64, "schema": build_cache.SCHEMA}))
    assert c.lookup("top", "a" * 64) is None  # another key's meta


def test_prune_by_last_use(tmp_path):
    c = build_cache.Cache(tmp_path / "c")
    exe = tmp_path / "top"
    exe.write_text("x")
    old = c.store("top", "o" * 64, [exe], {})
    new = c.store("top", "n" * 64, [exe], {})
    c.record_pass("p" * 64, {"test": "t"})
    stale_tmp = c.root / "builds" / ".tmp-dead"
    stale_tmp.mkdir()
    long_ago = time.time() - 30 * 86400
    os.utime(old / build_cache.META, (long_ago, long_ago))
    os.utime(stale_tmp, (long_ago, long_ago))
    os.utime(c.root / "passes" / ("p" * 64 + ".json"), (long_ago, long_ago))
    assert c.prune() == 3
    assert not old.exists() and new.exists() and not stale_tmp.exists()
    assert c.pass_record("p" * 64) is None


def test_prune_by_size_drops_least_recently_used(tmp_path):
    c = build_cache.Cache(tmp_path / "c")
    exe = tmp_path / "top"
    exe.write_bytes(b"x" * 1000)
    old = c.store("top", "o" * 64, [exe], {})
    new = c.store("top", "n" * 64, [exe], {})
    t = time.time()
    os.utime(old / build_cache.META, (t - 100, t - 100))
    assert c.prune(max_bytes=1500) == 1
    assert not old.exists() and new.exists()
    assert not [p for p in (c.root / "builds").iterdir() if p.name.startswith(".tmp-")]


def test_pass_records(tmp_path):
    c = build_cache.Cache(tmp_path / "c")
    assert c.pass_record("q" * 64) is None
    c.record_pass("q" * 64, {"test": "t", "seed": 3})
    assert c.pass_record("q" * 64)["seed"] == 3


# ------------------------------------------------------- coverage paths

def test_rebase_coverage_rewrites_the_compiling_tree(tmp_path):
    dat = tmp_path / "coverage.dat"
    dat.write_text("# SystemC::Coverage-3\n"
                   "C '\x01t\x02line\x01f\x02/w1/src/rtl/a.sv\x01l\x0212\x01' 3\n")
    sim.rebase_coverage(dat, "/w1", "/w2")
    assert "\x02/w2/src/rtl/a.sv" in dat.read_text() and "/w1/" not in dat.read_text()
    assert sim.rebase_coverage(dat, "/w2", "/w2")  # same tree: untouched
    assert "\x02/w2/src/rtl/a.sv" in dat.read_text()
    assert not sim.rebase_coverage(dat, "/elsewhere", "/w3")  # nothing matched: reported


# ---------------------------------------------------------- cold builds

def test_compile_slots_fit_cpus_and_memory():
    gib = 2**30
    assert sim.compile_slots(cpus=16, mem=None) == 4        # 16 CPUs / make -j4
    assert sim.compile_slots(cpus=16, mem=14 * gib) == 1    # 7 compilers fit: one build
    assert sim.compile_slots(cpus=32, mem=64 * gib) == 8
    assert sim.compile_slots(cpus=4, mem=1 * gib) == 1      # never below one
    assert sim.compile_slots(cpus=2, mem=None) == 1         # make -j2 on two CPUs
    assert isinstance(sim.mem_available(), (int, type(None)))


# --------------------------------------------------------------- ccache

def test_ccache_env(tmp_path, monkeypatch):
    for k in ("OBJCACHE", "CCACHE_DIR", "CCACHE_BASEDIR", "CCACHE_NOHASHDIR",
              "CCACHE_SLOPPINESS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sim.shutil, "which", lambda name: "/usr/bin/ccache")
    sim._ccache_env({"ccache": True, "build_dir": str(tmp_path / "b")})
    assert os.environ["OBJCACHE"] == "ccache"
    assert os.environ["CCACHE_BASEDIR"] == str(tmp_path / "b")
    assert "CCACHE_DIR" not in os.environ  # ccache's own directory
    sim._ccache_env({"build_dir": str(tmp_path / "b")})  # cache off
    assert os.environ["OBJCACHE"] == ""
    monkeypatch.setattr(sim.shutil, "which", lambda name: None)
    sim._ccache_env({"ccache": True, "build_dir": str(tmp_path / "b")})
    assert os.environ["OBJCACHE"] == ""  # no ccache installed


# ------------------------------------------------------------- real sim

@needs_sim
def test_second_build_is_a_hit_from_another_worktree(fixture_src, tmp_path):
    verilator = sim.find_verilator()
    cache = build_cache.Cache(tmp_path / "cache")
    sim.set_cache(cache)
    other = tmp_path / "wt2/src"
    shutil.copytree(fixture_src, other)
    first = sim.build(sim.BuildSpec(find_ip(discover(fixture_src), "counter"), "counter"),
                      tmp_path / "w1", verilator)
    assert first.status == "pass" and not first.cached
    assert first.build_dir.parent == cache.root / "builds"
    assert sorted(p.name for p in first.log.parent.iterdir()) == ["build.log"]
    ip2 = find_ip(discover(other), "counter")
    spec2 = sim.BuildSpec(ip2, "counter")
    second = sim.build(spec2, tmp_path / "w2", verilator)
    assert second.status == "pass" and second.cached and second.build_dir == first.build_dir
    assert "build cache hit" in second.log.read_text()
    ok = sim.run_test(spec2, tmp_path / "w2", verilator, "counts", "tests.test_counter",
                      "counts", 5, 60, second)
    assert ok.status == "pass", ok.log.read_text()
