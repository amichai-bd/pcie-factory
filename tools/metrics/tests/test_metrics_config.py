from pathlib import Path

import pytest
import yaml

from metrics.config import MetricsError, load, match

from conftest import REPO_ROOT


def _write(tmp_path: Path, change) -> Path:
    raw = yaml.safe_load((REPO_ROOT / "cfg" / "metrics.yaml").read_text())
    change(raw)
    (tmp_path / "metrics.yaml").write_text(yaml.safe_dump(raw))
    return tmp_path


def test_repo_config_loads(cfg):
    assert cfg.data_dir == "wiki/tracker/data"
    assert cfg.branch_prefix == "nightly/"
    assert cfg.windows == (1, 7, 30)
    assert "rtl_sv" in cfg.kinds


@pytest.mark.parametrize("change, msg", [
    (lambda r: r.pop("kinds"), "missing keys: kinds"),
    (lambda r: r.update(extra=1), "unknown keys: extra"),
    (lambda r: r.update(windows=[0]), "windows"),
    (lambda r: r.update(slowest="x"), "slowest"),
    (lambda r: r.update(data_dir="/abs"), "data_dir"),
    (lambda r: r.update(data_dir="a/../b"), "data_dir"),
    (lambda r: r.update(kinds={"Bad Kind": ["x"]}), "bad kind name"),
    (lambda r: r.update(line_comments={"py": ["#"]}), "must start with"),
    (lambda r: r.update(fpga_fields=["a..b"]), "bad field path"),
])
def test_bad_config(tmp_path, change, msg):
    with pytest.raises(MetricsError, match=msg):
        load(_write(tmp_path, change))


def test_invalid_yaml(tmp_path):
    (tmp_path / "metrics.yaml").write_text("a: [\n")
    with pytest.raises(MetricsError, match="invalid YAML"):
        load(tmp_path)


def test_missing_file(tmp_path):
    with pytest.raises(MetricsError, match="cannot read"):
        load(tmp_path)


@pytest.mark.parametrize("path, pattern, hit", [
    ("src/rtl/a/x.sv", "src/rtl/**/*.sv", True),
    ("src/rtl/a_b/inc/x.sv", "src/rtl/**/*.sv", True),
    ("src/dv/a/x.sv", "src/rtl/**/*.sv", False),
    ("src/fpga/a/b.sdc", "src/**/*.sdc", True),
    ("README.md", "*.md", True),
    ("wiki/README.md", "*.md", False),
    ("wiki/tracker/data/2026-01-01.json", "wiki/tracker/data/**", True),
    (".github/workflows/ci.yml", ".github/workflows/*.yml", True),
])
def test_glob(path, pattern, hit):
    assert match(path, [pattern]) is hit
