"""Shared pytest fixtures for tools/ unit tests."""

import shutil
from pathlib import Path

import pytest

FIXTURE_SRC = Path(__file__).resolve().parent / "build" / "tests" / "fixtures" / "src"


@pytest.fixture
def fixture_src(tmp_path) -> Path:
    """A private, writable copy of the fixture source tree."""
    dst = tmp_path / "src"
    shutil.copytree(FIXTURE_SRC, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst
