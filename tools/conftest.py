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


@pytest.fixture(autouse=True)
def private_build_cache(tmp_path_factory, monkeypatch):
    """Keep every test off the host build cache: $BUILD_CACHE points at a
    private directory, and the cache a CLI test turned on is turned off
    again afterwards (it is a module setting of build.sim)."""
    from build import sim

    monkeypatch.setenv("BUILD_CACHE", str(tmp_path_factory.mktemp("build-cache")))
    yield
    sim.set_cache(None)
