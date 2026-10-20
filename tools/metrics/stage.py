"""Stage a nightly run's data files on the data branch, losing no record
(wiki/tools/metrics/SPEC.md#stage).

The open data PR into the base, when there is one, is continued: this run's
record is committed on top of its head, so a run that publishes before the
previous run's PR merged keeps both records. Any other open data PR is
carried: its records are copied in and it is closed by the caller once the
branch is pushed. The series (`commits.jsonl`) is merged by commit, so it
holds each commit once and never loses a line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import guard
from .config import Config, MetricsError
from .tree import git

_REF = re.compile(r"^[A-Za-z0-9._/-]+$")


def _ref(name) -> str:
    if not isinstance(name, str) or not _REF.match(name) or name.startswith("-") or ".." in name:
        raise MetricsError(f"invalid branch name {name!r}")
    return name


def open_data_prs(gh, base: str, cfg: Config) -> list[tuple[int, str]]:
    """(number, head branch) of the open data PRs into ``base`` from this
    repository, oldest first."""
    out = []
    for p in gh.paginate(f"repos/{gh.repo}/pulls?state=open&base={base}"):
        head = p.get("head") or {}
        ref, repo = head.get("ref"), (head.get("repo") or {}).get("full_name")
        if isinstance(ref, str) and ref.startswith(cfg.branch_prefix) and repo == gh.repo:
            out.append((int(p["number"]), _ref(ref)))
    return sorted(out)


def merge_series(*sources: list[dict]) -> list[dict]:
    """One line per commit from every source; a later source wins on the same
    commit. Ordered by first-parent position (`index`), then date."""
    by_sha = {}
    for lines in sources:
        for x in lines:
            by_sha[x["sha"]] = x
    big = float("inf")
    return sorted(by_sha.values(), key=lambda x: (x["index"] if isinstance(x.get("index"), int) else big,
                                                   str(x.get("date", "")), x["sha"]))


def _series(text: str) -> list[dict]:
    lines = []
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MetricsError(f"{guard.SERIES}:{n}: invalid series line: {exc}") from None
        if not isinstance(obj, dict) or not isinstance(obj.get("sha"), str):
            raise MetricsError(f"{guard.SERIES}:{n}: invalid series line: no sha")
        lines.append(obj)
    return lines


def _dumps(lines: list[dict]) -> str:
    return "".join(json.dumps(x, sort_keys=True, separators=(",", ":")) + "\n" for x in lines)


class Staging:
    """The data directory of the staged branch's worktree."""

    def __init__(self, dest: Path):
        self.dest = dest
        f = dest / guard.SERIES
        self.series = _series(f.read_text(encoding="utf-8")) if f.is_file() else []
        self.records: list[str] = []

    def add_record(self, name: str, data: bytes) -> None:
        if not guard.RECORD.match(name):
            raise MetricsError(f"{name}: not a run record name")
        f = self.dest / name
        if f.exists():
            if f.read_bytes() != data:
                raise MetricsError(f"{name}: differs from the record already on the branch")
            return
        f.write_bytes(data)
        self.records.append(name)

    def add_series(self, text: str) -> None:
        self.series = merge_series(self.series, _series(text))

    def write(self) -> None:
        if self.series:
            (self.dest / guard.SERIES).write_text(_dumps(self.series), encoding="utf-8")


def stage(repo: Path, gh, cfg: Config, base: str, branch: str, data: Path, worktree: Path,
          message: str, log=print) -> dict:
    """Build the data branch in ``worktree`` and commit it. Returns the
    outputs: branch, mode (new|continue), number (the continued PR), carried
    (PR numbers to close), changed (true|false) and sha."""
    base, branch = _ref(base), _ref(branch)
    prs = open_data_prs(gh, base, cfg)

    def fetch(name):
        git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{name}:refs/remotes/origin/{name}")
        return f"origin/{name}"

    base_rev = fetch(base)
    for number, ref in prs:
        passed, lines, _ = guard.run(repo, ref, base_rev, fetch(ref), cfg)
        if not passed:
            raise MetricsError(f"open data PR #{number} ({ref}) fails the data guard; fix or close it "
                               "by hand:\n" + "\n".join(lines))
    target = prs[-1] if prs else None
    carried = prs[:-1]
    if target:
        branch, start, mode = target[1], f"origin/{target[1]}", "continue"
        log(f"[metrics] stage: continuing data PR #{target[0]} ({branch})")
    else:
        start, mode = base_rev, "new"
        log(f"[metrics] stage: no open data PR into {base}; new branch {branch} from {base_rev}")
    if worktree.exists():  # an earlier attempt's worktree
        git(repo, "worktree", "remove", "--force", str(worktree))
    git(repo, "worktree", "prune")
    git(repo, "worktree", "add", "--quiet", "-B", branch, str(worktree), start)
    dest = worktree / cfg.data_dir
    dest.mkdir(parents=True, exist_ok=True)
    staged = Staging(dest)
    prefix = cfg.data_dir.rstrip("/") + "/"
    for number, ref in carried:
        for status, path in guard.changes(repo, base_rev, f"origin/{ref}"):
            name = path[len(prefix):]
            if status == "D" or not path.startswith(prefix):
                continue
            blob = git(repo, "show", f"origin/{ref}:{path}")
            if name == guard.SERIES:
                staged.add_series(blob.decode("utf-8"))
            else:
                staged.add_record(name, blob)
        log(f"[metrics] stage: carried the data of #{number} ({ref})")
    for f in sorted(data.iterdir()):
        if f.name == guard.SERIES:
            staged.add_series(f.read_text(encoding="utf-8"))
        elif f.is_file():
            staged.add_record(f.name, f.read_bytes())
    staged.write()
    git(worktree, "add", "--", cfg.data_dir)
    out = {"branch": branch, "mode": mode, "number": str(target[0]) if target else "",
           "carried": " ".join(str(n) for n, _ in carried), "changed": "false", "sha": ""}
    diff = git(worktree, "diff", "--cached", "--name-only").decode().split()
    if not diff:
        log(f"[metrics] stage: no data change against {start}; nothing to publish")
        return out
    git(worktree, "commit", "--quiet", "-m", message)
    out["changed"] = "true"
    out["sha"] = git(worktree, "rev-parse", "HEAD").decode().strip()
    log(f"[metrics] stage: {branch} at {out['sha'][:12]}: {', '.join(staged.records) or 'no new record'}"
        f", {len(staged.series)} series lines")
    return out
