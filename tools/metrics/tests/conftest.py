"""Fixtures for the metrics tests: a fake ``gh`` client and a synthetic git repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from metrics.config import load
from metrics.gh import GhError

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeGh:
    """Answers ``api``/``paginate`` from a dict of path prefix -> data (or an
    exception), and records every write."""

    def __init__(self, routes: dict, repo: str = "own/repo"):
        self.repo = repo
        self.routes = routes
        self.calls: list[tuple[str, str, list]] = []
        self.next_number = 100

    def _lookup(self, path: str):
        base = path.split("?")[0]
        for key in sorted(self.routes, key=len, reverse=True):
            if base == key or path.startswith(key + "?") or path == key:
                value = self.routes[key]
                if isinstance(value, Exception):
                    raise value
                return value
        raise GhError(f"no route for {path}", 404)

    def api(self, method, path, fields=None):
        self.calls.append((method, path, list(fields or [])))
        if method == "GET":
            return self._lookup(path)
        if method == "POST" and path.endswith("/issues"):
            self.next_number += 1
            return {"number": self.next_number}
        return {}

    def paginate(self, path, key=None, limit=5000):
        data = self._lookup(path)
        return list(data[key] if key else data)[:limit]

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def cfg():
    return load(REPO_ROOT / "cfg")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str, date: str,
           email: str = "t@example.invalid") -> str:
    for rel, text in files.items():
        p = repo / rel
        if text is None:
            p.unlink()
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    env_date = f"{date} +0000"
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", f"user.email={email}",
                    "commit", "-q", "-m", message, "--date", env_date],
                   check=True, env={"GIT_COMMITTER_DATE": env_date, "PATH": "/usr/bin:/bin",
                                    "HOME": str(repo)})
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A git repo with two commits on main and neutral IP names."""
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    commit(r, {
        "README.md": "# Title\n\nText.\n",
        "src/rtl/blk/blk.rtl.f": "// filelist\nxy_blk.sv\n",
        "src/rtl/blk/xy_blk.sv": "// header\nmodule xy_blk;\n/* block\n comment */\nendmodule\n\n",
        "tools/x/run.py": "# comment\nimport os\n\nprint(os.name)\n",
    }, "first", "2026-01-01T00:00:00")
    commit(r, {
        "src/rtl/blk_sub/blk_sub.rtl.f": "xy_blk_sub.sv\n",
        "src/rtl/blk_sub/xy_blk_sub.sv": "module xy_blk_sub; endmodule // tail\n",
        "src/dv/blk/tests/test_blk.py": "def test():\n    pass\n",
        "wiki/tracker/data/old.json": "{}\n",
        "logo.bin": "\0\1\2",
    }, "second", "2026-01-02T00:00:00")
    return r
