"""build.logscrub: what a published job-log line keeps and loses."""

from pathlib import Path

from build.logscrub import host_names, scrub

ROOT = Path("/var/lib/ci/work/checkout")
WORK = Path("/scratch/ci/w")
ROOTS = {ROOT: "", WORK: "<workdir>/"}


def s(text: str, names: list[str] | None = None, **kw) -> str:
    return scrub(text, ROOTS, names or [], **kw)


def test_roots_relative_longest_first():
    nested = {ROOT: "", ROOT / "workdir": "<w>/"}
    assert scrub(f"{ROOT}/workdir/a/b.log {ROOT}/src/x.sv", nested, []) == "<w>/a/b.log src/x.sv"
    assert s(f"log: {WORK}/ip/fpga/logs/map.log") == "log: <workdir>/ip/fpga/logs/map.log"
    assert s(f"cd {ROOT}") == "cd ."
    assert s(f"in {WORK}, done") == "in <workdir>, done"


def test_other_absolute_paths_cut_to_file_name():
    assert s("run /opt/vendor/bin/tool_sh -t x.tcl") == "run .../tool_sh -t x.tcl"
    assert s('File: "/srv/a/b.sv" Line: 3') == 'File: ".../b.sv" Line: 3'
    assert s("dir /srv/a/") == "dir .../a"


def test_relative_paths_and_placeholders_kept():
    assert s("src/rtl/a.sv and ./x/y and ../z") == "src/rtl/a.sv and ./x/y and ../z"
    assert s("<repo>/src/a.sv <workdir>/x") == "<repo>/src/a.sv <workdir>/x"


def test_home_relative_paths():
    assert s("see ~/proj/x.sv and ~someone/y/z.v") == "see .../x.sv and .../z.v"


def test_windows_and_unc_paths():
    assert s(r"C:\intelFPGA_lite\25.1\quartus\bin64\quartus_sh.exe ok") == r"...\quartus_sh.exe ok"
    assert s("D:/work/proj/top.sv") == r"...\top.sv"
    assert s(r"at \\fileserver\share\dir\top.qsf") == r"at ...\top.qsf"


def test_names_whole_words():
    assert s("ci-user ci-users xci-user", ["ci-user"]) == "<name> ci-users xci-user"
    assert s("written by ci-user on build-host-07", ["build-host-07", "ci-user"]) == \
        "written by <name> on <name>"


def test_addresses():
    assert s("mail someone@example.org now") == "mail <email> now"
    assert s("license 27000@licserver;1800@lic2 down") == "license <host>;<host> down"
    assert s("from user@laptop") == "from <host>"
    assert s("ip 192.0.2.7 v6 2001:db8::1 and 2001:db8:0:0:0:0:0:2") == "ip <ip> v6 <ip> and <ip>"
    assert s("2001:db8::5%eth0") == "<ip>%eth0"
    assert s("mac 00:1a:2b:3c:4d:5e or 00-1A-2B-3C-4D-5E") == "mac <mac> or <mac>"


def test_not_addresses():
    assert s("at 12:34:56 on 2026-10-20") == "at 12:34:56 on 2026-10-20"
    assert s("top:u_top|blk:u_blk|ram:u_ram") == "top:u_top|blk:u_blk|ram:u_ram"
    assert s("always @(posedge clk)") == "always @(posedge clk)"
    assert s("::quartus::project") == "::quartus::project"


def test_known_limit_path_with_spaces():
    # Only the part before the first space is recognized as a path.
    assert s("/opt/My Tools/bin/x") == ".../My Tools/bin/x"


def test_collapse_and_limit():
    assert s("a  b\tc") == "a  b\tc"
    assert s("a  b\tc", collapse=True) == "a b c"
    assert s("x" * 200, limit=10) == "xxxxxxx..."
    assert s("short", limit=10) == "short"


def test_host_names_longest_first():
    names = host_names()
    assert names == sorted(names, key=len, reverse=True)
    assert all(len(n) > 1 for n in names)
