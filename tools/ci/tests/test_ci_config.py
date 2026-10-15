import pytest

from build.ip import REPO_ROOT
from ci.config import CiConfigError, load_rules

GOOD = """\
global:
  prefixes: [tools/, cfg/]
  files: [build]
  levels: [0]
shared_dv: [dv/common]
models: models
tools_tests: [tools]
vocabulary_tests:
  marker: vocabulary
  paths: [src/rtl/common/h.svh, src/x/]
"""


def write(tmp_path, text):
    (tmp_path / "ci.yaml").write_text(text)
    return tmp_path


def test_real_config_loads():
    r = load_rules(REPO_ROOT / "cfg")
    assert r.global_prefixes == ("tools/", "cfg/") and r.global_files == ("build",)
    assert r.global_levels == (0,)
    assert r.shared_dv == ("dv/common",) and r.models == "models"
    assert r.tools_tests == ("tools",)
    assert r.vocabulary_marker == "vocabulary"
    assert r.vocabulary_paths == ("src/rtl/common/common_macros.svh",
                                  "src/rtl/common/common_assertions.svh")


def test_is_vocabulary(tmp_path):
    r = load_rules(write(tmp_path, GOOD))
    assert r.is_vocabulary("src/rtl/common/h.svh") and r.is_vocabulary("src/x/y.sv")
    assert not r.is_vocabulary("src/rtl/common/h.sv") and not r.is_vocabulary("src/xy")


def test_is_global(tmp_path):
    r = load_rules(write(tmp_path, GOOD))
    assert r.is_global("tools/x.py") and r.is_global("cfg/a.yaml") and r.is_global("build")
    assert not r.is_global("builder") and not r.is_global("toolsx/a") and not r.is_global("src/a")


@pytest.mark.parametrize("text, msg", [
    ("", "top level must be a mapping"),
    ("global: [", "invalid YAML"),
    (GOOD.replace("models: models\n", ""), "missing \\['models'\\]"),
    (GOOD + "extra: 1\n", "unknown \\['extra'\\]"),
    (GOOD.replace("files: [build]", "file: [build]"), "global: must be a mapping"),
    (GOOD.replace("  levels: [0]\n", ""), "global: must be a mapping"),
    (GOOD.replace("levels: [0]", "levels: []"), "global.levels: expected a non-empty list"),
    (GOOD.replace("levels: [0]", "levels: [-1]"), "global.levels"),
    (GOOD.replace("levels: [0]", "levels: [true]"), "global.levels"),
    (GOOD.replace("levels: [0]", "levels: 0"), "global.levels"),
    (GOOD.replace("[tools/, cfg/]", "[tools, cfg/]"), "must end with '/'"),
    (GOOD.replace("[dv/common]", "[../x]"), "relative path inside the tree"),
    (GOOD.replace("[dv/common]", "dv/common"), "expected a list"),
    (GOOD.replace("models: models", "models: [models]"), "models: expected a string"),
    (GOOD.replace("models: models", "models: ../models"), "models: .*relative path inside the tree"),
    (GOOD + "arch: arch\n", "unknown \\['arch'\\]"),
    (GOOD.replace("tools_tests: [tools]", "tools_tests: [/abs]"), "relative path"),
    (GOOD.replace("  marker: vocabulary\n", ""), "vocabulary_tests: must be a mapping"),
    (GOOD.replace("marker: vocabulary", "marker: not a name"), "pytest marker name"),
    (GOOD.replace("[src/rtl/common/h.svh, src/x/]", "[../h]"), "relative path inside the tree"),
])
def test_malformed(tmp_path, text, msg):
    with pytest.raises(CiConfigError, match=msg):
        load_rules(write(tmp_path, text))


def test_missing_file(tmp_path):
    with pytest.raises(CiConfigError, match="cannot read"):
        load_rules(tmp_path)
