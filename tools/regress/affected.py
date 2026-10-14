"""``--affected <base>``: which IPs a change touches."""

from __future__ import annotations

import subprocess
from pathlib import Path

from build.filelist import FilelistError, resolve
from build.ip import Ip, owner_ip

# Paths (relative to the repository root) whose change selects every IP.
GLOBAL_PREFIXES = ("tools/", "cfg/")
GLOBAL_FILES = ("build",)
# Shared DV helpers under the source root; a change there selects every IP.
SHARED_DV = "common/dv"


class GitError(Exception):
    pass


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def changed_files(base: str, start: Path) -> tuple[Path, list[str]]:
    """Return (repo root, changed paths relative to it) between the merge base
    of ``base`` and HEAD, including uncommitted and untracked files."""
    repo = Path(_git(start, "rev-parse", "--show-toplevel").strip())
    merge_base = _git(repo, "merge-base", base, "HEAD").strip()
    diff = _git(repo, "diff", "-z", "--no-renames", "--name-only", merge_base)
    untracked = _git(repo, "ls-files", "-z", "--others", "--exclude-standard")
    names = {n for n in (diff + untracked).split("\0") if n}
    return repo, sorted(names)


def select_affected(
    ips: list[Ip], repo: Path, changed: list[str], src_root: Path | None = None
) -> dict[str, str]:
    """Map affected IP id -> reason. Every IP is affected when a global path
    changed, or a file under ``<src_root>/common/dv/`` (shared DV Python,
    which no filelist reaches). Otherwise an IP is affected when a changed
    file lies in its own directory (not in a sub-IP's) or is reached by its
    ``dv/<name>.sim.f``."""
    for rel in changed:
        if rel in GLOBAL_FILES or rel.startswith(GLOBAL_PREFIXES):
            return {ip.id: f"global change: {rel}" for ip in ips}
    paths = [(repo / rel).resolve() for rel in changed]
    if src_root is not None:
        shared = (Path(src_root) / SHARED_DV).resolve()
        for rel, path in zip(changed, paths):
            if shared in path.parents:
                return {ip.id: f"shared DV change: {rel}" for ip in ips}
    reasons: dict[str, str] = {}
    for rel, path in zip(changed, paths):
        owner = owner_ip(ips, path)
        if owner is not None:
            reasons.setdefault(owner.id, f"changed: {rel}")
    for ip in ips:
        if ip.id in reasons or not ip.sim_f.is_file():
            continue
        try:
            flat = resolve(ip.sim_f)
        except FilelistError:
            # A broken filelist is a reason to run the IP: sim will report it.
            reasons[ip.id] = "sim filelist does not resolve"
            continue
        for rel, path in zip(changed, paths):
            if flat.reaches(path):
                reasons[ip.id] = f"reaches: {rel}"
                break
    return dict(sorted(reasons.items()))
