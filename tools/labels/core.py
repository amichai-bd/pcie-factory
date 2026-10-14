"""GitHub label standard: load cfg/labels.yaml, sync it, check issues.

GitHub is reached only through the ``gh`` CLI (``gh api``, REST), via the
``Gh`` class, so tests can substitute a fake. See wiki/tools/labels/SPEC.md.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import yaml

from build.ip import discover

RULES = ("exactly-one", "at-least-one", "at-most-one", "any")
DERIVED = ("ip",)
_COLOR = re.compile(r"^[0-9a-fA-F]{6}$")
_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MAX_DESC = 100  # GitHub's limit for a label description
_PER_PAGE = 100
ENV_REPO = "GH_REPO"


class LabelsError(Exception):
    """Configuration, repository or GitHub error (exit 2)."""


@dataclass(frozen=True)
class Label:
    name: str
    color: str  # lower-case hex, no '#'
    description: str


@dataclass(frozen=True)
class Dimension:
    name: str
    prefix: str
    rule: str
    labels: tuple[Label, ...]


@dataclass(frozen=True)
class Standard:
    dimensions: tuple[Dimension, ...]

    @property
    def labels(self) -> list[Label]:
        return [lab for d in self.dimensions for lab in d.labels]

    def dimension_of(self, name: str) -> Dimension | None:
        """The dimension a label name belongs to: the longest matching prefix."""
        best = None
        for d in self.dimensions:
            if name.startswith(d.prefix) and (best is None or len(d.prefix) > len(best.prefix)):
                best = d
        if best is not None and best.prefix == "":
            # Unprefixed dimensions only own the labels they list.
            return best if any(lab.name == name for lab in best.labels) else None
        return best


# --- configuration ----------------------------------------------------------

def _label(where: str, name: str, color, description) -> Label:
    if not isinstance(color, str) or not _COLOR.match(color):
        raise LabelsError(f"{where}: color must be 6 hex digits without '#', got {color!r}")
    if not isinstance(description, str) or not description.strip():
        raise LabelsError(f"{where}: description must be a non-empty string")
    if len(description) > _MAX_DESC:
        raise LabelsError(f"{where}: description longer than {_MAX_DESC} characters")
    if not name or name != name.strip() or "," in name:
        raise LabelsError(f"{where}: invalid label name {name!r}")
    return Label(name, color.lower(), description)


def load(path: Path, ip_ids: list[str]) -> Standard:
    """Parse and validate the label config; ``ip_ids`` feed derived dimensions."""
    try:
        data = yaml.safe_load(Path(path).read_text())
    except OSError as exc:
        raise LabelsError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise LabelsError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"dimensions"} \
            or not isinstance(data["dimensions"], dict) or not data["dimensions"]:
        raise LabelsError(f"{path}: top level must be a mapping with one key, "
                          "'dimensions', holding a non-empty mapping")
    dims = []
    seen: dict[str, str] = {}
    prefixes: set[str] = set()
    for dname, spec in data["dimensions"].items():
        where = f"{path}: dimensions.{dname}"
        if not isinstance(spec, dict):
            raise LabelsError(f"{where}: must be a mapping")
        derived = spec.get("derived")
        allowed = {"prefix", "rule", "derived", "color", "description"} if derived \
            else {"prefix", "rule", "labels"}
        extra = set(spec) - allowed
        if extra:
            raise LabelsError(f"{where}: unknown keys {sorted(extra)}")
        prefix, rule = spec.get("prefix"), spec.get("rule")
        if not isinstance(prefix, str) or " " in prefix:
            raise LabelsError(f"{where}: prefix must be a string without spaces")
        if prefix in prefixes:
            raise LabelsError(f"{where}: duplicate prefix {prefix!r}")
        prefixes.add(prefix)
        if rule not in RULES:
            raise LabelsError(f"{where}: rule must be one of {', '.join(RULES)}")
        if derived is not None:
            if derived not in DERIVED:
                raise LabelsError(f"{where}: derived must be one of {', '.join(DERIVED)}")
            if not prefix:
                raise LabelsError(f"{where}: a derived dimension needs a prefix")
            template = spec.get("description")
            if not isinstance(template, str) or "{id}" not in template:
                raise LabelsError(f"{where}: description must be a string containing '{{id}}'")
            items = [(i, spec.get("color"), template.replace("{id}", i)) for i in ip_ids]
        else:
            raw = spec.get("labels")
            if not isinstance(raw, dict) or not raw:
                raise LabelsError(f"{where}: labels must be a non-empty mapping")
            items = []
            for value, lab in raw.items():
                if not isinstance(lab, dict) or set(lab) != {"color", "description"}:
                    raise LabelsError(f"{where}.labels.{value}: must be a mapping with "
                                      "exactly 'color' and 'description'")
                items.append((str(value), lab["color"], lab["description"]))
        labels = []
        for value, color, desc in items:
            name = prefix + value
            lab = _label(f"{where}.labels.{value}", name, color, desc)
            if name.lower() in seen:
                raise LabelsError(f"{where}: label {name!r} already defined in "
                                  f"dimensions.{seen[name.lower()]}")
            seen[name.lower()] = dname
            labels.append(lab)
        dims.append(Dimension(dname, prefix, rule, tuple(labels)))
    for d in dims:  # an unprefixed label must not look like a prefixed one
        if d.prefix == "":
            for lab in d.labels:
                for other in dims:
                    if other.prefix and lab.name.startswith(other.prefix):
                        raise LabelsError(f"{path}: label {lab.name!r} in dimensions.{d.name} "
                                          f"uses the prefix of dimensions.{other.name}")
    return Standard(tuple(dims))


def load_repo_standard(cfg_dir: Path, src_root: Path) -> Standard:
    ip_ids = [ip.id for ip in discover(src_root)] if Path(src_root).is_dir() else []
    return load(Path(cfg_dir) / "labels.yaml", ip_ids)


# --- GitHub access ------------------------------------------------------------

def slug_from_url(url: str) -> str | None:
    """``owner/name`` from a git remote URL (https, ssh or a proxy path)."""
    url = url.strip()
    m = re.match(r"^[^@/]+@[^:/]+:(.+)$", url)  # scp-like ssh: git@host:o/r.git
    path = m.group(1) if m else re.sub(r"^[a-z+]+://[^/]+/", "", url)
    parts = [p for p in path.rstrip("/").removesuffix(".git").split("/") if p]
    if len(parts) < 2:
        return None
    slug = "/".join(parts[-2:])
    return slug if _SLUG.match(slug) else None


def resolve_repo(explicit: str | None, repo_root: Path) -> str:
    """``--repo``, then ``$GH_REPO``, then the ``origin`` remote."""
    slug = explicit or os.environ.get(ENV_REPO)
    if slug:
        if not _SLUG.match(slug):
            raise LabelsError(f"repository must be OWNER/NAME, got {slug!r}")
        return slug
    try:
        url = subprocess.run(["git", "-C", str(repo_root), "remote", "get-url", "origin"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LabelsError("no --repo or $GH_REPO and no git remote 'origin'") from exc
    slug = slug_from_url(url)
    if slug is None:
        raise LabelsError(f"cannot derive OWNER/NAME from remote {url.strip()!r}; use --repo")
    return slug


class Gh:
    """Minimal REST client over ``gh api``."""

    def __init__(self, repo: str, exe: str = "gh"):
        self.repo = repo
        self.exe = exe

    def api(self, method: str, path: str, fields: dict[str, str] | None = None):
        cmd = [self.exe, "api", "-X", method, "-H", "Accept: application/vnd.github+json", path]
        for key, value in (fields or {}).items():
            cmd += ["-f", f"{key}={value}"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as exc:
            raise LabelsError(f"cannot run {self.exe}: {exc}") from exc
        if proc.returncode != 0:
            msg = (proc.stderr.strip() or proc.stdout.strip()).splitlines()
            raise LabelsError(f"gh api {method} {path} failed: {msg[-1] if msg else 'no output'}")
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def _label_path(self, name: str) -> str:
        return f"repos/{self.repo}/labels/{quote(name, safe='')}"

    def list_labels(self) -> list[Label]:
        out, page = [], 1
        while True:
            batch = self.api("GET", f"repos/{self.repo}/labels?per_page={_PER_PAGE}&page={page}")
            out += [Label(x["name"], x["color"].lower(), x.get("description") or "")
                    for x in batch]
            if len(batch) < _PER_PAGE:
                return out
            page += 1

    def create_label(self, lab: Label) -> None:
        self.api("POST", f"repos/{self.repo}/labels",
                 {"name": lab.name, "color": lab.color, "description": lab.description})

    def update_label(self, current: str, lab: Label) -> None:
        self.api("PATCH", self._label_path(current),
                 {"new_name": lab.name, "color": lab.color, "description": lab.description})

    def delete_label(self, name: str) -> None:
        self.api("DELETE", self._label_path(name))

    def issue_labels(self, number: int) -> list[str]:
        """Labels of an issue or pull request (they share the number space)."""
        data = self.api("GET", f"repos/{self.repo}/issues/{number}")
        return [x["name"] for x in data["labels"]]


# --- sync ---------------------------------------------------------------------

@dataclass(frozen=True)
class Change:
    action: str  # create | update | delete | extra
    name: str  # the current name on GitHub (create: the new name)
    label: Label | None  # desired label (None for delete/extra)
    detail: str

    def line(self) -> str:
        sign = {"create": "+", "update": "~", "delete": "-", "extra": "?"}[self.action]
        return f"{sign} {self.name}: {self.detail}"


def plan(standard: Standard, existing: list[Label], prune: bool) -> list[Change]:
    """The changes that make GitHub match the standard, in a stable order."""
    current = {lab.name.lower(): lab for lab in existing}
    wanted = {lab.name.lower() for lab in standard.labels}
    changes = []
    for lab in sorted(standard.labels, key=lambda x: x.name.lower()):
        cur = current.get(lab.name.lower())
        if cur is None:
            changes.append(Change("create", lab.name, lab,
                                  f"color {lab.color}, {lab.description!r}"))
            continue
        diffs = []
        if cur.name != lab.name:
            diffs.append(f"name -> {lab.name!r}")
        if cur.color != lab.color:
            diffs.append(f"color {cur.color} -> {lab.color}")
        if cur.description != lab.description:
            diffs.append(f"description {cur.description!r} -> {lab.description!r}")
        if diffs:
            changes.append(Change("update", cur.name, lab, "; ".join(diffs)))
    for key in sorted(set(current) - wanted):
        cur = current[key]
        if prune:
            changes.append(Change("delete", cur.name, None, "not in the config"))
        else:
            changes.append(Change("extra", cur.name, None,
                                  "not in the config (kept; --prune deletes)"))
    return changes


def sync(standard: Standard, gh: Gh, dry_run: bool, prune: bool, out) -> int:
    changes = plan(standard, gh.list_labels(), prune)
    mode = "dry run" if dry_run else "sync"
    print(f"labels {mode}: {gh.repo} ({len(standard.labels)} labels in the config)", file=out)
    for c in changes:
        print(c.line(), file=out)
    applied = [c for c in changes if c.action != "extra"]
    if not dry_run:
        for c in applied:
            if c.action == "create":
                gh.create_label(c.label)
            elif c.action == "update":
                gh.update_label(c.name, c.label)
            else:
                gh.delete_label(c.name)
    counts = {a: sum(c.action == a for c in changes)
              for a in ("create", "update", "delete", "extra")}
    verb = "would apply" if dry_run else "applied"
    print(f"{verb}: {counts['create']} create, {counts['update']} update, "
          f"{counts['delete']} delete; {counts['extra']} extra kept", file=out)
    return 0


# --- check --------------------------------------------------------------------

def check(standard: Standard, names: list[str]) -> tuple[list[str], list[str]]:
    """Apply the dimension rules to one item's labels: (errors, warnings)."""
    known = {lab.name for lab in standard.labels}
    errors, warnings = [], []
    per_dim: dict[str, list[str]] = {d.name: [] for d in standard.dimensions}
    for name in sorted(names):
        dim = standard.dimension_of(name)
        if dim is None:
            warnings.append(f"label {name!r} is not part of the standard")
            continue
        if name not in known:
            errors.append(f"label {name!r} is not a known {dim.name} value")
            continue
        per_dim[dim.name].append(name)
    for d in standard.dimensions:
        got = per_dim[d.name]
        what = f"{d.prefix}*" if d.prefix else d.name
        if d.rule == "exactly-one" and len(got) != 1:
            errors.append(f"needs exactly one {what} label, has {len(got)}"
                          + (f" ({', '.join(got)})" if got else ""))
        elif d.rule == "at-least-one" and not got:
            errors.append(f"needs at least one {what} label")
        elif d.rule == "at-most-one" and len(got) > 1:
            errors.append(f"allows at most one {what} label, has {', '.join(got)}")
    return errors, warnings
