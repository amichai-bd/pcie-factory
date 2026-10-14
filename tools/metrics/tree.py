"""Code statistics computed from a git tree (a commit, or HEAD).

Everything is read with ``git ls-tree`` and ``git cat-file --batch``, so the
same code serves the nightly record (HEAD) and the per-commit series (any
main commit), and the result never depends on untracked or generated files.
Line counts are cached by blob id, so a backfill reads each blob once.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .config import Config, MetricsError, match

_NON_IP_DIRS = {"rtl", "dv", "fpga", "asic"}
NO_EXT = "(none)"
ROOT_DIR = "."


@dataclass(frozen=True)
class FileStat:
    path: str
    bytes: int
    lines: int  # non-blank lines
    code: int  # non-blank, non-comment lines


def git(repo: Path, *args: str, input: bytes | None = None) -> bytes:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], input=input,
                              capture_output=True)
    except OSError as exc:
        raise MetricsError(f"cannot run git: {exc}") from exc
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip().splitlines()
        raise MetricsError(f"git {args[0]} failed: {msg[-1] if msg else 'no output'}")
    return proc.stdout


def count_lines(text: str, ext: str, cfg: Config) -> tuple[int, int]:
    """(non-blank lines, code lines) of one text file."""
    prefixes = cfg.line_comments.get(ext, ())
    block = ext in cfg.block_c
    lines = code = 0
    in_block = False
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        lines += 1
        if block:
            rest = ""
            while s:
                if in_block:
                    end = s.find("*/")
                    if end < 0:
                        s = ""
                        break
                    in_block = False
                    s = s[end + 2:].strip()
                else:
                    start = s.find("/*")
                    line_c = min((s.find(p) for p in prefixes if p in s), default=-1)
                    if start < 0 or (0 <= line_c < start):
                        rest += s if line_c < 0 else s[:line_c]
                        s = ""
                    else:
                        rest += s[:start]
                        in_block = True
                        s = s[start + 2:]
            if rest.strip():
                code += 1
        elif not any(s.startswith(p) for p in prefixes):
            code += 1
    return lines, code


class TreeReader:
    """Reads trees of one repository and caches per-blob line counts."""

    def __init__(self, repo: Path, cfg: Config):
        self.repo = Path(repo)
        self.cfg = cfg
        self._cache: dict[tuple[str, str], tuple[int, int]] = {}

    def files(self, rev: str) -> list[FileStat]:
        out = git(self.repo, "ls-tree", "-r", "-l", "-z", rev)
        entries = []
        for rec in out.split(b"\0"):
            if not rec:
                continue
            meta, _, path = rec.partition(b"\t")
            mode, kind, oid, size = meta.split()
            p = path.decode("utf-8", errors="replace")
            if kind != b"blob" or mode == b"120000" or match(p, self.cfg.exclude):
                continue
            entries.append((p, oid.decode(), int(size)))
        todo = sorted({(oid, PurePosixPath(p).suffix) for p, oid, _ in entries}
                      - set(self._cache))
        if todo:
            self._read_blobs(todo)
        return [FileStat(p, size, *self._cache[(oid, PurePosixPath(p).suffix)])
                for p, oid, size in sorted(entries)]

    def _read_blobs(self, todo: list[tuple[str, str]]) -> None:
        oids = sorted({oid for oid, _ in todo})
        data = git(self.repo, "cat-file", "--batch", input="".join(o + "\n" for o in oids).encode())
        blobs, pos = {}, 0
        for oid in oids:
            nl = data.index(b"\n", pos)
            header = data[pos:nl].split()
            if len(header) < 3:
                raise MetricsError(f"git cat-file: unexpected header for {oid}")
            size = int(header[2])
            blobs[oid] = data[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
        for oid, ext in todo:
            body = blobs[oid]
            if b"\0" in body[:8192]:
                self._cache[(oid, ext)] = (0, 0)  # binary
            else:
                self._cache[(oid, ext)] = count_lines(body.decode("utf-8", errors="replace"),
                                                      ext, self.cfg)

    def stats(self, rev: str) -> tuple[dict, list[FileStat]]:
        files = self.files(rev)
        return code_stats(files, self.cfg), files


def discover_ips(paths: list[str], src_root: str) -> list[str]:
    """IP ids (relative to ``src_root``) found in a list of tree paths."""
    prefix = src_root.rstrip("/") + "/"
    ips = set()
    for p in paths:
        if not p.startswith(prefix):
            continue
        parts = p[len(prefix):].split("/")
        # <ip...>/rtl/<name>.rtl.f
        if len(parts) >= 3 and parts[-2] == "rtl" and parts[-1] == f"{parts[-3]}.rtl.f":
            ip_parts = parts[:-2]
            if any(x in _NON_IP_DIRS or x.startswith(".") for x in ip_parts):
                continue
            ips.add("/".join(ip_parts))
    return sorted(ips)


def owner(path: str, ips: list[str], src_root: str) -> str | None:
    """The innermost IP whose directory holds ``path``."""
    prefix = src_root.rstrip("/") + "/"
    if not path.startswith(prefix):
        return None
    rel = path[len(prefix):]
    best = None
    for ip in ips:
        if rel.startswith(ip + "/") and (best is None or len(ip) > len(best)):
            best = ip
    return best


def _bucket() -> dict:
    return {"files": 0, "lines": 0, "code": 0}


def _add(b: dict, f: FileStat) -> None:
    b["files"] += 1
    b["lines"] += f.lines
    b["code"] += f.code


def code_stats(files: list[FileStat], cfg: Config) -> dict:
    """Aggregate file stats by kind, IP, extension and top-level directory."""
    ips = discover_ips([f.path for f in files], cfg.src_root)
    total = _bucket()
    total["bytes"] = 0
    kinds = {k: _bucket() for k in cfg.kinds}
    per_ip = {ip: {**_bucket(), "kinds": {}} for ip in ips}
    by_ext: dict[str, dict] = {}
    by_dir: dict[str, dict] = {}
    for f in files:
        _add(total, f)
        total["bytes"] += f.bytes
        ext = PurePosixPath(f.path).suffix or NO_EXT
        _add(by_ext.setdefault(ext, _bucket()), f)
        top = f.path.split("/", 1)[0] if "/" in f.path else ROOT_DIR
        _add(by_dir.setdefault(top, _bucket()), f)
        ip = owner(f.path, ips, cfg.src_root)
        if ip is not None:
            _add(per_ip[ip], f)
        for kind, patterns in cfg.kinds.items():
            if match(f.path, patterns):
                _add(kinds[kind], f)
                if ip is not None:
                    _add(per_ip[ip]["kinds"].setdefault(kind, _bucket()), f)
    return {
        "total": total,
        "kinds": kinds,
        "ips": per_ip,
        "by_ext": dict(sorted(by_ext.items())),
        "by_dir": dict(sorted(by_dir.items())),
    }
