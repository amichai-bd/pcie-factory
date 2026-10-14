import pytest

from build.ip import REPO_ROOT
from ci.config import CiConfigError, load_rules

GOOD = """\
global:
  prefixes: [tools/, cfg/]
  files: [build]
shared_dv: [common/dv]
models: dv/models
arch: arch
tools_tests: [tools]
"""


def write(tmp_path, text):
    (tmp_path / "ci.yaml").write_text(text)
    return tmp_path


def test_real_config_loads():
    r = load_rules(REPO_ROOT / "cfg")
    assert r.global_prefixes == ("tools/", "cfg/") and r.global_files == ("build",)
    assert r.shared_dv == ("common/dv",) and r.models == "dv/models" and r.arch == "arch"
    assert r.tools_tests == ("tools",)


def test_is_global(tmp_path):
    r = load_rules(write(tmp_path, GOOD))
    assert r.is_global("tools/x.py") and r.is_global("cfg/a.yaml") and r.is_global("build")
    assert not r.is_global("builder") and not r.is_global("toolsx/a") and not r.is_global("src/a")


@pytest.mark.parametrize("text, msg", [
    ("", "top level must be a mapping"),
    ("global: [", "invalid YAML"),
    (GOOD.replace("models: dv/models\n", ""), "missing \\['models'\\]"),
    (GOOD + "extra: 1\n", "unknown \\['extra'\\]"),
    (GOOD.replace("files: [build]", "file: [build]"), "global: must be a mapping"),
    (GOOD.replace("[tools/, cfg/]", "[tools, cfg/]"), "must end with '/'"),
    (GOOD.replace("[common/dv]", "[../x]"), "relative path inside the tree"),
    (GOOD.replace("[common/dv]", "common/dv"), "expected a list"),
    (GOOD.replace("models: dv/models", "models: [dv/models]"), "models: expected a string"),
    (GOOD.replace("arch: arch\n", ""), "missing \\['arch'\\]"),
    (GOOD.replace("arch: arch", "arch: 1"), "arch: expected a string"),
    (GOOD.replace("arch: arch", "arch: ../arch"), "arch: .*relative path inside the tree"),
    (GOOD.replace("tools_tests: [tools]", "tools_tests: [/abs]"), "relative path"),
])
def test_malformed(tmp_path, text, msg):
    with pytest.raises(CiConfigError, match=msg):
        load_rules(write(tmp_path, text))


def test_missing_file(tmp_path):
    with pytest.raises(CiConfigError, match="cannot read"):
        load_rules(tmp_path)
