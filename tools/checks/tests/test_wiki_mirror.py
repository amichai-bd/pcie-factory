import shutil
from pathlib import Path

import pytest

from build.ip import discover
from checks import wiki_mirror

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGE = "# Page\n"


@pytest.fixture
def good(tmp_path) -> Path:
    """A copy of the good fixture: <tmp>/src and its mirror <tmp>/wiki/src."""
    shutil.copytree(FIXTURES / "good/src", tmp_path / "src")
    shutil.copytree(FIXTURES / "good/wiki", tmp_path / "wiki")
    return tmp_path


def run(root: Path, ip_ids=None):
    src = root / "src"
    return wiki_mirror.run(src, discover(src), ip_ids)


def write(path: Path, text: str = PAGE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_good_fixture_passes(good):
    r = run(good)
    assert r.passed, r.findings
    # blk: arch, rtl, dv, fpga; blk/sub: rtl, dv
    assert r.checked == "6 required page(s), 8 wiki folder(s) under wiki/src/ (all IPs)"


def test_bad_fixture_fails():
    r = run(FIXTURES / "bad")
    assert r.findings == [
        "wiki/src/blk/arch/HAS.md: missing (required for IP blk)",
        "wiki/src/blk/dv/DV_PLAN.md: missing (required for IP blk)",
        "wiki/src/broken/arch/HAS.md: missing (required for IP broken)",
        "wiki/src/broken/rtl/MAS.md: missing (required for IP broken)",
        "wiki/src/broken/dv/DV_PLAN.md: missing (required for IP broken)",
        "wiki/src/blk/HAS.md: not in an arch/, rtl/, dv/, fpga/ or asic/ folder of an IP",
        "wiki/src/blk/fpga/: no source counterpart src/blk/fpga/",
        "wiki/src/blk/old/: no source counterpart src/blk/old/",
    ]


@pytest.mark.parametrize("page", ["blk/rtl/MAS.md", "blk/dv/DV_PLAN.md", "blk/arch/HAS.md",
                                  "blk/fpga/FPGA.md", "blk/sub/rtl/MAS.md",
                                  "blk/sub/dv/DV_PLAN.md"])
def test_each_required_page(good, page):
    (good / "wiki/src" / page).unlink()
    ip = page.rsplit("/", 2)[0]
    assert run(good).findings == [f"wiki/src/{page}: missing (required for IP {ip})"]


def test_asic_page_follows_the_src_folder(good):
    (good / "src/blk/asic").mkdir()
    assert run(good).findings == ["wiki/src/blk/asic/ASIC.md: missing (required for IP blk)"]
    write(good / "wiki/src/blk/asic/ASIC.md")
    assert run(good).passed


def test_fpga_page_without_src_folder(good):
    shutil.rmtree(good / "src/blk/fpga")
    assert run(good).findings == ["wiki/src/blk/fpga/: no source counterpart src/blk/fpga/"]


def test_top_level_arch_without_targets_needs_no_src_counterpart(good):
    assert not (good / "src/blk/arch").exists()
    r = run(good)
    assert r.passed, r.findings
    # ...but anything below arch/ still needs its source folder.
    write(good / "wiki/src/blk/arch/perf/notes.md")
    assert run(good).findings == ["wiki/src/blk/arch/perf/: no source counterpart "
                                  "src/blk/arch/perf/"]


def test_sub_ip_has_no_arch(good):
    write(good / "wiki/src/blk/sub/arch/HAS.md")
    assert run(good).findings == ["wiki/src/blk/sub/arch/: arch/ is for top-level IPs only "
                                  "(blk/sub is a sub-IP)"]
    # Even with a source folder, which is a finding of its own.
    (good / "src/blk/sub/arch").mkdir()
    assert run(good).findings == [
        "src/blk/sub/arch/: arch/ is for top-level IPs only (blk/sub is a sub-IP)",
        "wiki/src/blk/sub/arch/: arch/ is for top-level IPs only (blk/sub is a sub-IP)"]


def test_src_arch_under_a_sub_ip(good):
    (good / "src/blk/sub/arch").mkdir()
    assert run(good).findings == [
        "src/blk/sub/arch/: arch/ is for top-level IPs only (blk/sub is a sub-IP)"]
    assert run(good, {"blk"}).passed  # reported with the sub-IP


HAS_WITH_TARGETS = "# Blk HAS\n\n## Targets\n\n| Item | Target |\n| --- | --- |\n"


def add_arch(root: Path, model: bool = True, scenario: bool = True) -> None:
    arch = root / "src/blk/arch"
    (arch / "perf").mkdir(parents=True)
    if model:
        write(arch / "xy_blk_arch.py", "def perf(workload, seed):\n    return {}\n")
    if scenario:
        write(arch / "perf/full.yaml", "description: d\nseed: 1\nworkload: {}\n")


def test_has_targets_require_the_arch_model(good):
    write(good / "wiki/src/blk/arch/HAS.md", HAS_WITH_TARGETS)
    assert run(good).findings == [
        "src/blk/arch/: missing (wiki/src/blk/arch/HAS.md defines targets; "
        "the arch model reports against them)"]
    add_arch(good)
    r = run(good)
    assert r.passed, r.findings
    # Only a level-2 "Targets" heading counts.
    shutil.rmtree(good / "src/blk/arch")
    write(good / "wiki/src/blk/arch/HAS.md", "# Blk\n\n### Targets\n\nTargets: none.\n")
    assert run(good).passed


def test_src_arch_needs_a_model_and_a_scenario(good):
    add_arch(good)
    assert run(good).passed  # without HAS targets, an arch/ is still checked
    (good / "src/blk/arch/xy_blk_arch.py").unlink()
    (good / "src/blk/arch/perf/full.yaml").unlink()
    assert run(good).findings == [
        "src/blk/arch/: no single model <prefix>blk_arch.py",
        "src/blk/arch/: no perf scenario perf/*.yaml",
    ]
    write(good / "src/blk/arch/xy_blk_arch.py")
    write(good / "src/blk/arch/zz_blk_arch.py")
    write(good / "src/blk/arch/perf/full.yaml")
    assert run(good).findings == ["src/blk/arch/: no single model <prefix>blk_arch.py"]


def test_old_flat_layout_is_rejected(good):
    write(good / "wiki/src/blk/MAS.md")
    write(good / "wiki/src/blk/sub/DV_PLAN.md")
    write(good / "wiki/src/notes.md")
    assert run(good).findings == [
        "wiki/src/notes.md: not in an arch/, rtl/, dv/, fpga/ or asic/ folder of an IP",
        "wiki/src/blk/MAS.md: not in an arch/, rtl/, dv/, fpga/ or asic/ folder of an IP",
        "wiki/src/blk/sub/DV_PLAN.md: not in an arch/, rtl/, dv/, fpga/ or asic/ folder of an IP",
    ]


def test_orphan_folders(good):
    write(good / "wiki/src/gone/rtl/MAS.md")       # IP removed from src
    write(good / "wiki/src/blk/subx/rtl/MAS.md")   # misspelled sub-IP
    (good / "wiki/src/blk/rtl/empty").mkdir()      # an empty folder too
    assert run(good).findings == [
        "wiki/src/blk/rtl/empty/: no source counterpart src/blk/rtl/empty/",
        "wiki/src/blk/subx/: no source counterpart src/blk/subx/",
        "wiki/src/gone/: no source counterpart src/gone/",
    ]


def test_extra_pages_in_mirror_folders_and_non_ip_source_folders(good):
    # More pages in a mirror folder, or below one with a counterpart, are fine.
    write(good / "wiki/src/blk/rtl/NOTES.md")
    write(good / "wiki/src/blk/dv/regress/README.md")
    assert run(good).passed
    # src/common/ is not an IP: a counterpart folder exists, but no page fits.
    write(good / "wiki/src/common/rtl/common.md")
    assert run(good).findings == [
        "wiki/src/common/rtl/common.md: not in an arch/, rtl/, dv/, fpga/ or asic/ folder of an IP"]


def test_hidden_entries_are_skipped(good):
    write(good / "wiki/src/.cache/x.md")
    write(good / "wiki/src/blk/.DS_Store", "")
    assert run(good).passed


def test_ip_filter(good):
    (good / "wiki/src/blk/sub/rtl/MAS.md").unlink()
    write(good / "wiki/src/blk/sub/stray.md")
    write(good / "wiki/src/gone/rtl/MAS.md")
    r = run(good, {"blk"})
    assert r.passed, r.findings
    assert r.checked.endswith("(--ip blk)")
    assert run(good, {"blk/sub"}).findings == [
        "wiki/src/blk/sub/rtl/MAS.md: missing (required for IP blk/sub)",
        "wiki/src/blk/sub/stray.md: not in an arch/, rtl/, dv/, fpga/ or asic/ folder of an IP",
    ]
    assert len(run(good).findings) == 3  # unfiltered: also the orphan IP folder


def test_no_wiki_tree(tmp_path):
    shutil.copytree(FIXTURES / "good/src", tmp_path / "src")
    r = run(tmp_path)
    assert len(r.findings) == 6 and all("missing" in f for f in r.findings)
    (tmp_path / "empty/src").mkdir(parents=True)
    r = run(tmp_path / "empty")
    assert r.passed and r.checked.startswith("0 required page(s), 0 wiki folder(s)")


def test_wiki_root_is_the_sibling_tree(tmp_path):
    assert wiki_mirror.wiki_root(tmp_path / "src") == (tmp_path / "wiki/src").resolve()
