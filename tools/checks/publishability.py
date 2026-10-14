"""Publishability check (wiki BKM, "Publishability").

The repository and its wiki site are public. This check scans every file
git lists (tracked, plus untracked files that are not ignored), in every
tree, for text that must not be published: private keys, access tokens,
credentials in URLs, IP addresses, home-directory paths and personal email
addresses. File paths are scanned as well as contents; binary files (a NUL
byte) are skipped, and a symbolic link is not followed: its target string is
scanned instead.

The rules are fixed here. Values that identify nobody are not findings:
loopback, unspecified and documentation addresses (RFC 5737, RFC 3849),
email addresses at reserved domains (RFC 2606, RFC 6761) and no-reply
addresses. Anything else is exempt only through a reviewed entry in the
``publishability.allow`` list of ``cfg/checks.yaml``: a rule, the exact
matched text, an optional path glob and a reason. An entry that matches
nothing is stale and is a finding.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

from checks.common import CheckConfigError, CheckResult

CONFIG = "cfg/checks.yaml"
_ENTRY_KEYS = {"rule", "match", "path", "reason"}

# rule -> pattern. Group "v", when present, is the reported value.
RULES: dict[str, re.Pattern] = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    "token": re.compile(
        r"(?<![\w-])(?:"
        r"gh[pousr]_[A-Za-z0-9]{36,}"            # GitHub classic tokens
        r"|github_pat_[A-Za-z0-9_]{22,}"         # GitHub fine-grained tokens
        r"|(?:AKIA|ASIA)[0-9A-Z]{16}"            # AWS access key ids
        r"|AIza[0-9A-Za-z_-]{35}"                # Google API keys
        r"|xox[abposr]-[A-Za-z0-9-]{10,}"        # Slack tokens
        r"|sk-[A-Za-z0-9_-]{32,}"                # sk- secret keys
        r"|glpat-[A-Za-z0-9_-]{20,}"             # GitLab tokens
        r"|npm_[A-Za-z0-9]{36}"                  # npm tokens
        r")(?![\w-])"),
    "url-credentials": re.compile(
        r"(?<![\w+.-])[A-Za-z][A-Za-z0-9+.-]*://(?P<v>[^\s/:@'\"<>]+:[^\s/@'\"<>]+)@"),
    "ipv4": re.compile(r"(?<![\w.])(?P<v>\d{1,3}(?:\.\d{1,3}){3})(?![\w]|\.\d)"),
    "ipv6": re.compile(r"(?<![\w:.])(?P<v>(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4})(?![\w:])"),
    "home-path": re.compile(r"(?<![\w.-])(?P<v>/(?:home|Users)/[A-Za-z0-9._-]+)"),
    "email": re.compile(
        # The lookbehind holds every local-part character, so a match starts
        # only once per run: otherwise a long run backtracks quadratically.
        r"(?<![\w.%+-])(?P<v>[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![\w-])"),
}

# Networks that identify no machine: loopback, unspecified and documentation.
_SAFE_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "0.0.0.0/32", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
    "::1/128", "::/128", "2001:db8::/32")]
# Reserved second-level and top-level domains (RFC 2606, RFC 6761).
_RESERVED_DOMAIN = re.compile(
    r"(?:^|\.)(?:example\.(?:com|net|org)|example|invalid|test|localhost)$", re.IGNORECASE)
_NOREPLY_LOCAL = re.compile(r"^no-?reply(?:[+._-].*)?$|^.*\+noreply$", re.IGNORECASE)
_NOREPLY_DOMAIN = re.compile(r"(?:^|\.)noreply\.[A-Za-z0-9.-]+$", re.IGNORECASE)


@dataclass(frozen=True)
class Allow:
    rule: str
    match: str
    path: str | None
    reason: str


def load_config(repo_root: Path, path: str = CONFIG) -> tuple[Allow, ...]:
    """The ``publishability.allow`` entries of ``cfg/checks.yaml``."""
    p = Path(repo_root) / path
    try:
        data = yaml.safe_load(p.read_text())
    except OSError as exc:
        raise CheckConfigError(f"{p}: {exc.strerror}") from None
    except yaml.YAMLError as exc:
        raise CheckConfigError(f"{p}: invalid YAML: {exc}") from None
    sec = data.get("publishability") if isinstance(data, dict) else None
    if not isinstance(sec, dict) or set(sec) != {"allow"}:
        raise CheckConfigError(f"{p}: 'publishability' must be a mapping with only 'allow'")
    entries = sec["allow"]
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise CheckConfigError(f"{p}: publishability.allow must be a list")
    out = []
    for i, e in enumerate(entries):
        where = f"{p}: publishability.allow[{i}]"
        if not isinstance(e, dict) or not {"rule", "match", "reason"} <= set(e) <= _ENTRY_KEYS:
            raise CheckConfigError(f"{where}: needs 'rule', 'match' and 'reason', "
                                   "and optionally 'path', only")
        if e["rule"] not in RULES:
            raise CheckConfigError(f"{where}: unknown rule {e['rule']!r} "
                                   f"(one of {', '.join(RULES)})")
        for k in ("match", "reason", "path"):
            if k in e and (not isinstance(e[k], str) or not e[k].strip()):
                raise CheckConfigError(f"{where}: '{k}' must be a non-empty string")
        out.append(Allow(e["rule"], e["match"], e.get("path"), e["reason"].strip()))
    return tuple(out)


def _harmless(rule: str, value: str) -> bool:
    """True for a match that identifies nobody, or is not an address at all."""
    if rule in ("ipv4", "ipv6"):
        try:
            addr = ipaddress.ip_address(value)
        except ValueError:
            return True  # 999.1.1.1, a time such as 12:30:45, a MAC address
        if rule == "ipv6":
            groups = [g for g in value.split(":") if g]
            # Slice and scope syntax (`1::2`, `a::b`) is not an address.
            if len(groups) < 2 or (len(groups) < 3 and max(len(g) for g in groups) < 3):
                return True
        return any(addr in n for n in _SAFE_NETS if n.version == addr.version)
    if rule == "email":
        local, domain = value.rsplit("@", 1)
        return bool(_RESERVED_DOMAIN.search(domain) or _NOREPLY_LOCAL.match(local)
                    or _NOREPLY_DOMAIN.search(domain))
    return False


def scan_text(text: str) -> list[tuple[int, str, str]]:
    """(line number, rule, matched value) for every finding in ``text``."""
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        creds = [m.span() for m in RULES["url-credentials"].finditer(line)]
        for rule, rx in RULES.items():
            for m in rx.finditer(line):
                value = m.group("v") if "v" in rx.groupindex else m.group()
                # `user:password@host` is one url-credentials finding, not an email too.
                if rule == "email" and any(a <= m.start() < b for a, b in creds):
                    continue
                if not _harmless(rule, value):
                    hits.append((n, rule, value))
    return hits


def _allowed(entry: Allow, rule: str, value: str, path: str) -> bool:
    return (entry.rule == rule and entry.match == value
            and (entry.path is None or fnmatchcase(path, entry.path)))


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise CheckConfigError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def run(repo_root: Path, config_path: str = CONFIG) -> CheckResult:
    allow = load_config(repo_root, config_path)
    files = sorted({f for f in _git(repo_root, "ls-files", "-z", "--cached", "--others",
                                    "--exclude-standard").split("\0") if f})
    used: set[int] = set()
    result = CheckResult("publishability")

    def report(path: str, rule: str, value: str, at: str) -> None:
        if path == config_path:
            # The config may name every allowed value (the review record);
            # that mention is not a use, so it never keeps an entry alive.
            if not any(e.rule == rule and e.match == value for e in allow):
                result.findings.append(f"{at}: {rule}: {value!r}")
            return
        hit = [i for i, e in enumerate(allow) if _allowed(e, rule, value, path)]
        if hit:
            used.update(hit)
        else:
            result.findings.append(f"{at}: {rule}: {value!r}")

    for f in files:
        for _, rule, value in scan_text(f):
            report(f, rule, value, f"{f} (path)")
        p = Path(repo_root) / f
        if p.is_symlink():  # never follow a link out of the repository: scan its target string
            data = os.readlink(p).encode()
        elif p.is_file():
            data = p.read_bytes()
        else:  # deleted in the working tree, or a submodule
            continue
        if b"\0" in data:
            continue
        for n, rule, value in scan_text(data.decode("utf-8", errors="replace")):
            report(f, rule, value, f"{f}:{n}")
    for i, e in enumerate(allow):
        if i not in used:
            result.findings.append(
                f"{config_path}: publishability.allow[{i}] ({e.rule} {e.match!r}"
                + (f" in {e.path}" if e.path else "") + "): stale, matches nothing")
    result.checked = (f"{len(files)} file(s) (tracked and untracked, not ignored); "
                      f"{len(RULES)} rule(s); {len(allow)} allow entry(ies)")
    return result
