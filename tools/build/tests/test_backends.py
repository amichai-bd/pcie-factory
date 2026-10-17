"""build.backends: backend-list discovery and source substitution."""

from __future__ import annotations

from pathlib import Path

import pytest

from build import backends
from build.filelist import resolve


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def tree(tmp_path):
    """A shared block common_x with a portable file and an 'alt' backend,
    and an IP filelist that uses the block."""
    ram = tmp_path / "rtl" / "common" / "ram"
    write(tmp_path / "rtl" / "common" / "inc" / "h.svh", "")
    write(ram / "common_x.sv", "module common_x; endmodule\n")
    write(ram / "common_x.rtl.f", "common_x.sv\n")
    write(ram / "common_x_alt.sv", "// module not_this;\nmodule common_x; endmodule\n")
    write(ram / "common_x.alt.f", "+incdir+../inc\n+define+ALT_ONLY\ncommon_x_alt.sv\n")
    write(tmp_path / "rtl" / "ip" / "ip_top.sv", "module ip_top; endmodule\n")
    write(tmp_path / "rtl" / "ip" / "ip.rtl.f", "-F ../common/ram/common_x.rtl.f\nip_top.sv\n")
    return tmp_path


def test_parse_list_name():
    b = backends.parse_list_name(Path("/s/rtl/common/ram/common_x.sky130.f"))
    assert (b.block, b.backend) == ("common_x", "sky130")
    assert b.source == Path("/s/rtl/common/ram/common_x_sky130.sv")
    assert backends.parse_list_name(Path("common_x.rtl.f")) is None
    assert backends.parse_list_name(Path("common_x.f")) is None
    assert backends.parse_list_name(Path("common_x.Bad.f")) is None


def test_backend_lists_are_found_in_the_shared_folder_and_groups(tree):
    write(tree / "rtl" / "common" / "common_y.alt.f", "")
    write(tree / "rtl" / "ip" / "ip.alt.f", "")   # not shared: not a backend list
    found = [(b.block, b.backend) for b in backends.backend_lists(tree)]
    assert found == [("common_y", "alt"), ("common_x", "alt")]   # by path


def test_declared_modules_ignore_comments(tree):
    assert backends.declared_modules(tree / "rtl/common/ram/common_x_alt.sv") == ["common_x"]


def test_apply_replaces_in_place_and_merges_dirs_and_defines(tree):
    flat = resolve(tree / "rtl/ip/ip.rtl.f")
    sel = write(tree / "asic/ip/sky.backends.f", "-F ../../rtl/common/ram/common_x.alt.f\n")
    out, pairs = backends.apply(flat, sel)
    ram = (tree / "rtl/common/ram").resolve()
    assert pairs == [(ram / "common_x.sv", ram / "common_x_alt.sv")]
    assert [p.name for p in out.sources] == ["common_x_alt.sv", "ip_top.sv"]
    assert (tree / "rtl/common/inc").resolve() in out.incdirs
    assert out.defines == {"ALT_ONLY": None}
    # Deterministic.
    assert backends.apply(flat, sel)[0].sources == out.sources


def test_apply_skips_sources_the_ip_already_has(tree):
    flat = resolve(tree / "rtl/ip/ip.rtl.f")
    sel = write(tree / "sel.f", "rtl/common/ram/common_x.sv\nrtl/common/ram/common_x_alt.sv\n")
    out, pairs = backends.apply(flat, sel)
    assert len(pairs) == 1 and [p.name for p in out.sources] == ["common_x_alt.sv", "ip_top.sv"]


def test_two_backends_of_one_module_are_an_error(tree):
    write(tree / "rtl/common/ram/common_x_b.sv", "module common_x; endmodule\n")
    sel = write(tree / "sel.f", "rtl/common/ram/common_x_alt.sv\nrtl/common/ram/common_x_b.sv\n")
    with pytest.raises(backends.BackendError, match="two backends of module common_x"):
        backends.apply(resolve(tree / "rtl/ip/ip.rtl.f"), sel)


def test_backend_of_a_module_the_ip_lacks_is_an_error(tree):
    write(tree / "rtl/common/ram/other.sv", "module other; endmodule\n")
    sel = write(tree / "sel.f", "rtl/common/ram/other.sv\n")
    with pytest.raises(backends.BackendError, match="module other, which 0 source"):
        backends.apply(resolve(tree / "rtl/ip/ip.rtl.f"), sel)


def test_backend_with_two_modules_is_an_error(tree):
    write(tree / "rtl/common/ram/two.sv", "module common_x; endmodule\nmodule z; endmodule\n")
    sel = write(tree / "sel.f", "rtl/common/ram/two.sv\n")
    with pytest.raises(backends.BackendError, match="exactly one module, found 2"):
        backends.apply(resolve(tree / "rtl/ip/ip.rtl.f"), sel)


def test_unresolvable_backend_list_is_an_error(tree):
    sel = write(tree / "sel.f", "-F nope.f\n")
    with pytest.raises(backends.BackendError, match="nope.f"):
        backends.apply(resolve(tree / "rtl/ip/ip.rtl.f"), sel)


def test_conflicting_define_is_an_error(tree):
    write(tree / "rtl/ip/ip.rtl.f", "+define+ALT_ONLY=1\n-F ../common/ram/common_x.rtl.f\nip_top.sv\n")
    sel = write(tree / "sel.f", "-F rtl/common/ram/common_x.alt.f\n")
    with pytest.raises(backends.BackendError, match="define ALT_ONLY conflicts"):
        backends.apply(resolve(tree / "rtl/ip/ip.rtl.f"), sel)
