"""``--affected <base>``: which IPs a change touches.

The rules' facts (global paths, shared DV directories, the models directory)
come from ``cfg/ci.yaml``; see wiki/tools/ci/SPEC.md."""

from __future__ import annotations

import subprocess
from pathlib import Path

from build.filelist import FilelistError, resolve
from build.ip import Ip, owner_ip
from ci.config import CiRules
from regress.deps import Imports


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
    ips: list[Ip], repo: Path, changed: list[str], src_root: Path, rules: CiRules
) -> dict[str, str]:
    """Map affected IP id -> reason (see ``select_affected_levels``); the
    reason never mentions levels, which only ``regress`` applies."""
    return {k: reason for k, (reason, _) in
            select_affected_levels(ips, repo, changed, src_root, rules).items()}


def select_affected_levels(
    ips: list[Ip], repo: Path, changed: list[str], src_root: Path, rules: CiRules
) -> dict[str, tuple[str, tuple[int, ...] | None]]:
    """Map affected IP id -> (reason, levels). ``levels`` is None (every
    requested level) for an IP that a design or DV change selects, and
    ``rules.global_levels`` for an IP selected only because a global path
    (tools, configuration, the launcher) changed: such a change is proved by
    the tools' unit tests and a smoke level of every IP, not by every IP's
    full set. An IP whose DV or arch Python imports a changed file (an
    import-root module, or any module of another IP's DV or arch folder; see
    ``regress.deps``) is selected at every level, global change or not."""
    globals_ = [rel for rel in changed if rules.is_global(rel)]
    hit = _select_design(ips, repo, [rel for rel in changed if not rules.is_global(rel)],
                         src_root, rules)
    out: dict[str, tuple[str, tuple[int, ...] | None]] = {k: (v, None) for k, v in hit.items()}
    if changed:
        graph = Imports(ips, (repo / rules.import_root).resolve())
        files = [((repo / rel).resolve(), rel) for rel in changed]
        names = [(graph.module_name(p), rel) for p, rel in files]
        for ip in ips:
            if ip.id in out:
                continue
            reached, imported = graph.reach(ip)
            hits = sorted({rel for p, rel in files if p in reached}
                          | {rel for n, rel in names if n is not None and n in imported})
            if hits:
                out[ip.id] = (f"imports: {hits[0]}", None)
    if globals_:
        for ip in ips:
            out.setdefault(ip.id, (f"global change: {globals_[0]}", rules.global_levels))
    return dict(sorted(out.items()))


def _select_design(
    ips: list[Ip], repo: Path, changed: list[str], src_root: Path, rules: CiRules
) -> dict[str, str]:
    """Map affected IP id -> reason for non-global changes. Every IP is
    affected by a change under a shared DV directory (Python no filelist
    reaches). Otherwise an IP is affected when a changed file lies in one of
    its own kind folders, lies in the models directory of an IP its rtl
    filelist reaches (a sub-IP, or a top-level IP it integrates), or is
    reached by its ``<name>.sim.f``."""
    paths = [(repo / rel).resolve() for rel in changed]
    shared = [(Path(src_root) / d).resolve() for d in rules.shared_dv]
    for rel, path in zip(changed, paths):
        if any(s in path.parents for s in shared):
            return {ip.id: f"shared DV change: {rel}" for ip in ips}
    reasons: dict[str, str] = {}
    for rel, path in zip(changed, paths):
        owner = owner_ip(ips, path)
        if owner is not None:
            reasons.setdefault(owner.id, f"changed: {rel}")
    # Parent and integrating models import reached IPs' models by path,
    # which no filelist shows.
    for rel, path in zip(changed, paths):
        for sub in ips:
            if (sub.dv_dir / rules.models) in path.parents:
                for ancestor in sub.ancestors:
                    kind = "sub-IP" if ancestor in sub.parents else "integrated IP"
                    reasons.setdefault(ancestor, f"{kind} model: {rel}")
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
