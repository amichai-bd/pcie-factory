from pathlib import Path

import pytest

from build.filelist import FilelistError, resolve


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def tree(tmp_path):
    for name in ("a.sv", "b.sv", "c.sv", "sub/d.sv", "inc/defs.svh"):
        write(tmp_path / name, "")
    return tmp_path


def test_order_dedup_incdirs_defines(tree):
    write(tree / "sub/sub.f", "d.sv\n../a.sv\n+define+SUB=2\n")
    top = write(
        tree / "top.f",
        "// header comment\n"
        "\n"
        "+incdir+inc\n"
        "+define+FOO\n"
        "+define+BAR=1\n"
        "a.sv  // trailing comment\n"
        "-F sub/sub.f\n"
        "b.sv\n"
        "a.sv\n"
        "+incdir+inc\n",
    )
    r = resolve(top)
    assert r.sources == [tree / "a.sv", tree / "sub/d.sv", tree / "b.sv"]
    assert r.incdirs == [tree / "inc"]
    assert r.defines == {"FOO": None, "BAR": "1", "SUB": "2"}
    assert r.filelists == [top, tree / "sub/sub.f"]


def test_diamond_include_is_not_a_cycle(tree):
    write(tree / "leaf.f", "c.sv\n")
    write(tree / "l.f", "-F leaf.f\na.sv\n")
    write(tree / "r.f", "-F leaf.f\nb.sv\n")
    r = resolve(write(tree / "top.f", "-F l.f\n-F r.f\n"))
    assert r.sources == [tree / "c.sv", tree / "a.sv", tree / "b.sv"]


def test_cycle_detected(tree):
    write(tree / "x.f", "-F y.f\n")
    write(tree / "y.f", "-F x.f\n")
    with pytest.raises(FilelistError, match="cycle: .*x.f -> .*y.f -> .*x.f"):
        resolve(tree / "x.f")


def test_self_include_detected(tree):
    with pytest.raises(FilelistError, match="cycle"):
        resolve(write(tree / "s.f", "-F s.f\n"))


@pytest.mark.parametrize(
    "line, message",
    [
        ("*.sv", "globs are not allowed"),
        ("rtl/?.sv", "globs are not allowed"),
        ("-f other.f", "unsupported option '-f'"),
        ("-v lib.v", "unsupported option '-v'"),
        ("+libext+.sv", "unsupported option"),
        ("-F", "exactly one path"),
        ("-F a.f b.f", "exactly one path"),
        ("a.sv b.sv", "one entry per line"),
        ("$ROOT/a.sv", "environment variables"),
        ("+incdir+", "exactly one directory"),
        ("+incdir+a+b", "exactly one directory"),
        ("+define+1BAD", "NAME"),
        ("+define+A+B", "NAME"),
        ("missing.sv", "source not found"),
        ("+incdir+nodir", "include directory not found"),
        ("-F nofile.f", "filelist not found"),
    ],
)
def test_errors_name_file_and_line(tree, line, message):
    f = write(tree / "bad.f", "a.sv\n// ok\n" + line + "\n")
    with pytest.raises(FilelistError) as exc:
        resolve(f)
    assert f"{f}:3" in str(exc.value)
    assert message in str(exc.value)


def test_conflicting_define(tree):
    f = write(tree / "d.f", "+define+W=1\n+define+W=1\n+define+W=2\n")
    with pytest.raises(FilelistError, match=r"d.f:3: define W conflicts"):
        resolve(f)


def test_missing_root():
    with pytest.raises(FilelistError, match="filelist not found"):
        resolve("/nonexistent/x.f")


def test_reaches(tree):
    write(tree / "sub/sub.f", "d.sv\n")
    top = write(tree / "top.f", "+incdir+inc\n-F sub/sub.f\na.sv\n")
    r = resolve(top)
    assert r.reaches(tree / "a.sv")
    assert r.reaches(tree / "sub/d.sv")
    assert r.reaches(tree / "sub/sub.f")
    assert r.reaches(tree / "inc/defs.svh")
    assert r.reaches(tree / "inc/new_header.svh")  # may not exist yet
    assert not r.reaches(tree / "b.sv")


def test_fixture_filelists(fixture_src):
    r = resolve(fixture_src / "counter/dv/counter.sim.f")
    assert [p.name for p in r.sources] == ["inc.sv", "counter.sv"]
    assert r.defines == {"COUNTER_FIXTURE": None}
    assert [p.name for p in r.incdirs] == ["include"]
