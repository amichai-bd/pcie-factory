import shutil
from pathlib import Path

import pytest

from build.ip import discover
from checks import wiki_mirror

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGE = "# Page\n"
KINDS = "(rtl/, dv/, arch/, fpga/, asic/)"


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
    # blk: arch, rtl, dv, fpga; blk_sub: rtl, dv
    assert r.checked == "6 required page(s), 6 wiki folder(s) under wiki/src/ (all IPs)"


def test_bad_fixture_fails():
    r = run(FIXTURES / "bad")
    assert r.findings == [
        "wiki/src/arch/blk/HAS.md: missing (required for IP blk)",
        "wiki/src/dv/blk/DV_PLAN.md: missing (required for IP blk)",
        "wiki/src/arch/broken/HAS.md: missing (required for IP broken)",
        "wiki/src/rtl/broken/MAS.md: missing (required for IP broken)",
        "wiki/src/dv/broken/DV_PLAN.md: missing (required for IP broken)",
        f"wiki/src/HAS.md: not in an IP folder <kind>/<ip>/ {KINDS}",
        "wiki/src/fpga/blk/: no source counterpart src/fpga/blk/",
        "wiki/src/rtl/old/: names no IP (no src/rtl/old/old.rtl.f)",
    ]


@pytest.mark.parametrize("page", ["rtl/blk/MAS.md", "dv/blk/DV_PLAN.md", "arch/blk/HAS.md",
                                  "fpga/blk/FPGA.md", "rtl/blk_sub/MAS.md",
                                  "dv/blk_sub/DV_PLAN.md"])
def test_each_required_page(good, page):
    (good / "wiki/src" / page).unlink()
    ip = page.split("/")[1]
    assert run(good).findings == [f"wiki/src/{page}: missing (required for IP {ip})"]


def test_asic_page_follows_the_src_folder(good):
    (good / "src/asic/blk").mkdir(parents=True)
    assert run(good).findings == ["wiki/src/asic/blk/ASIC.md: missing (required for IP blk)"]
    write(good / "wiki/src/asic/blk/ASIC.md")
    assert run(good).passed


def test_fpga_page_without_src_folder(good):
    shutil.rmtree(good / "src/fpga/blk")
    assert run(good).findings == ["wiki/src/fpga/blk/: no source counterpart src/fpga/blk/"]


def test_top_level_arch_without_targets_needs_no_src_counterpart(good):
    assert not (good / "src/arch/blk").exists()
    r = run(good)
    assert r.passed, r.findings
    # ...but anything below it still needs its source folder.
    write(good / "wiki/src/arch/blk/perf/notes.md")
    assert run(good).findings == ["wiki/src/arch/blk/perf/: no source counterpart "
                                  "src/arch/blk/perf/"]


def test_sub_ip_has_no_arch_page(good):
    write(good / "wiki/src/arch/blk_sub/HAS.md")
    assert run(good).findings == ["wiki/src/arch/blk_sub/: arch/ is for top-level IPs only "
                                  "(blk_sub is a sub-IP of blk)"]


def test_sub_ip_becomes_top_level_when_no_ip_reaches_it(good):
    f = good / "src/rtl/blk/blk.rtl.f"
    f.write_text(f.read_text().replace("-F ../blk_sub/blk_sub.rtl.f\n", ""))
    assert run(good).findings == [
        "wiki/src/arch/blk_sub/HAS.md: missing (required for IP blk_sub)"]


HAS_WITH_TARGETS = "# Blk HAS\n\n## Targets\n\n| Item | Target |\n| --- | --- |\n"


def add_arch(root: Path, model: bool = True, scenario: bool = True) -> None:
    arch = root / "src/arch/blk"
    (arch / "perf").mkdir(parents=True)
    if model:
        write(arch / "xy_blk_arch.py", "def perf(workload, seed):\n    return {}\n")
    if scenario:
        write(arch / "perf/full.yaml", "description: d\nseed: 1\nworkload: {}\n")


def test_has_targets_require_the_arch_model(good):
    write(good / "wiki/src/arch/blk/HAS.md", HAS_WITH_TARGETS)
    assert run(good).findings == [
        "src/arch/blk/: missing (wiki/src/arch/blk/HAS.md defines targets; "
        "the arch model reports against them)"]
    add_arch(good)
    r = run(good)
    assert r.passed, r.findings
    # Only a level-2 "Targets" heading counts.
    shutil.rmtree(good / "src/arch/blk")
    write(good / "wiki/src/arch/blk/HAS.md", "# Blk\n\n### Targets\n\nTargets: none.\n")
    assert run(good).passed


@pytest.mark.parametrize("text, targets", [
    ("## Targets\n", True),
    ("## Targets (draft)\n", True),
    ("##  targets and budgets\n", True),
    ("   ## Targets\n", True),
    ("### Targets\n", False),
    ("## Performance targets\n", False),
    ("## Targetsheet\n", False),
    ("    ## Targets\n", False),                       # indented code
    ("```\n## Targets\n```\n", False),                 # fenced code
    ("~~~md\n## Targets\n~~~\n", False),
    ("````\n```\n## Targets\n````\n", False),         # a shorter fence does not close
    ("```\nx\n```\n## Targets\n", True),              # after the fence closes
    ("```\n## Targets\n", False),                      # an unclosed fence runs to the end
])
def test_targets_heading(tmp_path, text, targets):
    write(tmp_path / "HAS.md", "# Blk HAS\n\n" + text)
    assert wiki_mirror.defines_targets(tmp_path / "HAS.md") is targets


def test_src_arch_needs_a_model_and_a_scenario(good):
    add_arch(good)
    assert run(good).passed  # without HAS targets, an arch/ is still checked
    (good / "src/arch/blk/xy_blk_arch.py").unlink()
    (good / "src/arch/blk/perf/full.yaml").unlink()
    write(good / "src/arch/blk/zz_blk_arch.py")  # not the exact <prefix><id>_arch.py
    assert run(good).findings == [
        "src/arch/blk/: no model xy_blk_arch.py",
        "src/arch/blk/: no perf scenario perf/*.yaml",
    ]
    write(good / "src/arch/blk/xy_blk_arch.py")
    write(good / "src/arch/blk/perf/full.yaml")
    assert run(good).passed


def test_arch_never_imports_dv_rtl_or_tools(good):
    add_arch(good)
    write(good / "src/dv/blk/models/xy_blk_model.py", "")
    write(good / "src/dv/blk/models/random.py", "")        # a stdlib name stays stdlib
    write(good / "src/arch/blk/xy_blk_helper.py", "")       # a sibling is allowed
    write(good / "src/dv/blk/models/xy_blk_helper.py", "")  # ...even when DV has the name
    write(good / "src/arch/blk/test_xy_blk_arch.py",
          "import random\nimport pytest\nimport xy_blk_helper\nimport xy_blk_arch\n")
    assert run(good).passed
    write(good / "src/arch/blk/xy_blk_arch.py",
          "import xy_blk_model\n"
          "from cocotb.triggers import Timer\n"
          "import build.ip\n"
          "import importlib\n"
          "m = importlib.import_module('checks.common')\n"
          "def perf(workload, seed):\n    return {}\n")
    rule = "arch never imports DV, RTL or tools code"
    assert run(good).findings == [
        f"src/arch/blk/xy_blk_arch.py:1: imports xy_blk_model (src/dv); {rule}",
        f"src/arch/blk/xy_blk_arch.py:2: imports cocotb (DV framework); {rule}",
        f"src/arch/blk/xy_blk_arch.py:3: imports build (tools); {rule}",
        f"src/arch/blk/xy_blk_arch.py:5: imports checks (tools); {rule}",
    ]
    # tools/ and src/ as packages, a tool folder without __init__.py and its modules
    write(good / "src/arch/blk/xy_blk_arch.py",
          "import tools.metrics\nfrom src.dv.blk import x\nimport wiki_site\nfrom tracker import render\n"
          "def perf(workload, seed):\n    return {}\n")
    assert run(good).findings == [
        f"src/arch/blk/xy_blk_arch.py:1: imports tools (tools); {rule}",
        f"src/arch/blk/xy_blk_arch.py:2: imports src (src); {rule}",
        f"src/arch/blk/xy_blk_arch.py:3: imports wiki_site (tools); {rule}",
        f"src/arch/blk/xy_blk_arch.py:4: imports tracker (tools); {rule}",
    ]
    write(good / "src/arch/blk/xy_blk_arch.py", "def perf(:\n")
    assert run(good).findings == ["src/arch/blk/xy_blk_arch.py:1: cannot parse for the import scan"]


def test_ip_first_layout_is_rejected(good):
    """The superseded IP-first wiki tree (``wiki/src/<ip>/<kind>/``)."""
    write(good / "wiki/src/blk/rtl/MAS.md")
    write(good / "wiki/src/blk/blk_sub/dv/DV_PLAN.md")
    write(good / "wiki/src/notes.md")
    assert run(good).findings == [
        f"wiki/src/notes.md: not in an IP folder <kind>/<ip>/ {KINDS}",
        f"wiki/src/blk/: not a kind folder {KINDS}",
    ]


def test_design_level_pages_are_allowed(good):
    write(good / "wiki/src/README.md")
    write(good / "wiki/src/roadmap.md")
    write(good / "wiki/src/decisions/0002-m1-grill.md")
    write(good / "wiki/src/decisions/0010-a-b-2.md")
    r = run(good)
    assert r.passed, r.findings
    assert r.checked.startswith("6 required page(s), 6 wiki folder(s)")


def test_other_design_level_files_are_rejected(good):
    write(good / "wiki/src/Roadmap.md")              # names are exact
    write(good / "wiki/src/notes.md")
    write(good / "wiki/src/decisions/README.md")     # not a record
    write(good / "wiki/src/decisions/2-short.md")    # four digits
    write(good / "wiki/src/decisions/0003-Upper.md")  # lower-case slug
    write(good / "wiki/src/decisions/0004-x.txt")
    write(good / "wiki/src/decisions/old/0001-x.md")  # flat folder
    write(good / "wiki/src/rtl/roadmap.md")          # only at the root
    write(good / "wiki/src/rtl/decisions/0001-x.md")
    assert run(good).findings == [
        f"wiki/src/Roadmap.md: not in an IP folder <kind>/<ip>/ {KINDS}",
        f"wiki/src/notes.md: not in an IP folder <kind>/<ip>/ {KINDS}",
        "wiki/src/decisions/old/: decisions/ holds no folders",
        "wiki/src/decisions/0003-Upper.md: not a decision record NNNN-<slug>.md",
        "wiki/src/decisions/0004-x.txt: not a decision record NNNN-<slug>.md",
        "wiki/src/decisions/2-short.md: not a decision record NNNN-<slug>.md",
        "wiki/src/decisions/README.md: not a decision record NNNN-<slug>.md",
        f"wiki/src/rtl/roadmap.md: not in an IP folder <kind>/<ip>/ {KINDS}",
        "wiki/src/rtl/decisions/: names no IP (no src/rtl/decisions/decisions.rtl.f)",
    ]
    # A filtered run looks only inside the chosen IPs' folders.
    assert run(good, {"blk"}).passed


def test_orphan_folders(good):
    write(good / "wiki/src/rtl/gone/MAS.md")        # IP removed from src
    write(good / "wiki/src/dv/blk_subx/DV_PLAN.md")  # misspelled sub-IP
    (good / "wiki/src/rtl/blk/empty").mkdir()       # an empty folder too
    write(good / "wiki/src/rtl/stray.md")           # a file directly in a kind folder
    assert run(good).findings == [
        "wiki/src/dv/blk_subx/: names no IP (no src/rtl/blk_subx/blk_subx.rtl.f)",
        f"wiki/src/rtl/stray.md: not in an IP folder <kind>/<ip>/ {KINDS}",
        "wiki/src/rtl/blk/empty/: no source counterpart src/rtl/blk/empty/",
        "wiki/src/rtl/gone/: names no IP (no src/rtl/gone/gone.rtl.f)",
    ]


def test_extra_pages_and_shared_folders(good):
    # More pages in an IP folder, or below one with a counterpart, are fine.
    write(good / "wiki/src/rtl/blk/NOTES.md")
    write(good / "wiki/src/dv/blk/regress/README.md")
    assert run(good).passed
    # rtl/common/ is not an IP: a source folder exists, but no page fits.
    write(good / "wiki/src/rtl/common/common.md")
    assert run(good).findings == [
        "wiki/src/rtl/common/: names no IP (no src/rtl/common/common.rtl.f)"]


def test_hidden_entries_are_skipped(good):
    write(good / "wiki/src/.cache/x.md")
    write(good / "wiki/src/rtl/blk/.DS_Store", "")
    assert run(good).passed


def test_ip_filter(good):
    (good / "wiki/src/rtl/blk_sub/MAS.md").unlink()
    write(good / "wiki/src/dv/blk_sub/stray/x.md")
    write(good / "wiki/src/rtl/gone/MAS.md")
    r = run(good, {"blk"})
    assert r.passed, r.findings
    assert r.checked.endswith("(--ip blk)")
    assert run(good, {"blk_sub"}).findings == [
        "wiki/src/rtl/blk_sub/MAS.md: missing (required for IP blk_sub)",
        "wiki/src/dv/blk_sub/stray/: no source counterpart src/dv/blk_sub/stray/",
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
