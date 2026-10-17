"""Source backends (build.backends): configuration, naming and selection."""

from pathlib import Path

import pytest

from build import backends
from build.backends import Backend, BackendError
from build.ip import REPO_ROOT

FPGA = Backend("fpga", "alt", ("dv/common/tb/prim.sv",))
ASIC = Backend("asic", "macro", ())
KNOWN = {"fpga": FPGA, "asic": ASIC}


def touch(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_load_repo_config():
    known = backends.load(REPO_ROOT / "cfg")
    assert known["fpga"].suffix  # the FPGA flow has a backend
    for b in known.values():  # every stand-in exists in the repository
        assert backends.dv_models(b, REPO_ROOT / "src")


def test_load_absent_is_empty(tmp_path):
    assert backends.load(tmp_path) == {}


@pytest.mark.parametrize("text, message", [
    ("[a]\n", "mapping"),
    ("fpga: alt\n", "expected keys"),
    ("fpga: {suffix: Alt}\n", "lowercase word"),
    ("fpga: {suffix: alt, extra: 1}\n", "expected keys"),
    ("fpga: {suffix: alt, dv_models: [rtl/x.sv]}\n", "below dv/"),
    ("fpga: {suffix: alt, dv_models: [dv/../rtl/x.sv]}\n", "below dv/"),
    ("fpga: {suffix: alt}\nasic: {suffix: alt}\n", "share one backend suffix"),
    ("Fpga-x: {suffix: alt}\n", "plain name"),
    ("fpga: [\n", "cannot read"),
])
def test_load_errors(tmp_path, text, message):
    (tmp_path / "backends.yaml").write_text(text)
    with pytest.raises(BackendError, match=message):
        backends.load(tmp_path)


def test_portable_of_needs_the_portable_file(tmp_path):
    alt = touch(tmp_path / "blk_alt.sv")
    assert backends.portable_of(alt, KNOWN) is None  # no blk.sv beside it
    portable = touch(tmp_path / "blk.sv")
    assert backends.portable_of(alt, KNOWN) == (portable, FPGA)
    assert backends.portable_of(portable, KNOWN) is None
    # Another suffix, a header and a bare suffix are not backend files.
    assert backends.portable_of(touch(tmp_path / "blk_held.sv"), KNOWN) is None
    assert backends.portable_of(touch(tmp_path / "blk_alt.svh"), KNOWN) is None
    assert backends.portable_of(touch(tmp_path / "_alt.sv"), KNOWN) is None


def test_select_replaces_in_place(tmp_path):
    a, b, c = (touch(tmp_path / n) for n in ("a.sv", "b.sv", "c.sv"))
    b_alt = touch(tmp_path / "b_alt.sv")
    touch(tmp_path / "c_macro.sv")
    assert backends.select([a, b, c], FPGA, KNOWN) == [a, b_alt.resolve(), c]
    assert backends.select([a, b, c], ASIC, KNOWN) == [a, b, (tmp_path / "c_macro.sv").resolve()]
    assert backends.select([a, b, c], None, KNOWN) == [a, b, c]


def test_select_rejects_a_listed_backend(tmp_path):
    b = touch(tmp_path / "b.sv")
    b_alt = touch(tmp_path / "b_alt.sv")
    for chosen in (FPGA, ASIC, None):  # two implementations of one module
        with pytest.raises(BackendError, match="lists a backend file"):
            backends.select([b, b_alt], chosen, KNOWN)


def test_dv_models(tmp_path):
    with pytest.raises(BackendError, match="dv model dv/common/tb/prim.sv not found"):
        backends.dv_models(FPGA, tmp_path)
    prim = touch(tmp_path / "dv/common/tb/prim.sv")
    assert backends.dv_models(FPGA, tmp_path) == [prim.resolve()]
