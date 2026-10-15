"""Wiki mirror check: ``wiki/src/`` mirrors the kind-first source tree.

The wiki tree is ``<src_root>/../wiki/src`` (in the repository, ``src/`` and
``wiki/src/`` are siblings under the root). Rules, owned by the wiki BKM's
page set:

- every IP has ``rtl/<id>/MAS.md`` and ``dv/<id>/DV_PLAN.md``;
- every top-level IP has ``arch/<id>/HAS.md``; no sub-IP has ``arch/<id>/``;
- an IP with ``src/fpga/<id>/`` has ``fpga/<id>/FPGA.md``, one with
  ``src/asic/<id>/`` has ``asic/<id>/ASIC.md`` (and, by the next rule, an IP
  without them has neither);
- every folder under the wiki tree, other than the flat ``decisions/``, is
  a kind folder or below one, names an IP directly below its kind folder,
  and has a source counterpart (``src/<kind>/<id>/...``), except a
  top-level IP's ``arch/<id>/`` while its HAS defines no targets;
- every file sits in an IP folder ``<kind>/<id>/`` or below one, except
  the design-level pages: ``README.md``, ``roadmap.md`` and ``references.md``
  at the root and
  decision records ``decisions/NNNN-<slug>.md``;
- the shared folders ``rtl/common/`` and ``dv/common/`` hold one page
  ``<block>.md`` per shared block, in the block's group folder when it has
  one (``src/rtl/common/[<group>/]<block>.rtl.f`` gives
  ``<kind>/common/[<group>/]<block>.md``): its MAS under ``rtl/``, its DV
  plan under ``dv/``, and nothing else;
- a top-level IP whose ``arch/<id>/HAS.md`` has a ``## Targets`` section
  (any level-2 heading starting with the word ``Targets``, outside fenced
  code) has ``src/arch/<id>/`` with one model ``<prefix><id>_arch.py`` and
  at least one ``perf/*.yaml`` scenario (the arch BKM);
- no Python file under ``src/arch/<id>/`` imports DV, RTL or tools code
  (``arch.imports``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from arch import imports
from arch.perf import ARCH_DIR, PERF_DIR, ArchError, find_model, model_name
from build.ip import KINDS, SHARED_DIR, Ip, block_dir, block_name, shared_blocks
from checks.common import CheckResult

# The page each IP folder under a kind holds.
PAGES = {"arch": "HAS.md", "rtl": "MAS.md", "dv": "DV_PLAN.md",
         "fpga": "FPGA.md", "asic": "ASIC.md"}

# Design-level pages outside the IP folders (the wiki BKM's page set).
ROOT_PAGES = ("README.md", "roadmap.md", "references.md")
DECISIONS = "decisions"
_DECISION = re.compile(r"\A[0-9]{4}-[a-z0-9]+(-[a-z0-9]+)*\.md\Z")


def wiki_root(src_root: Path) -> Path:
    """The wiki tree that mirrors ``src_root``."""
    return Path(src_root).resolve().parent / "wiki" / "src"


# Kinds whose wiki folder holds the shared blocks' pages, one per block.
SHARED_KINDS = ("rtl", "dv")


# A HAS defines performance targets in a level-2 section whose heading
# starts with the word "Targets" ("## Targets", "## Targets (draft)").
_TARGETS = re.compile(r"^ {0,3}##[ \t]+Targets\b", re.I)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def defines_targets(has: Path) -> bool:
    """True when ``has`` has a Targets heading outside fenced code blocks."""
    try:
        lines = has.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    fence = None  # the open fence's character and length
    for line in lines:
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
            elif _TARGETS.match(line):
                return True
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                and not line[m.end():].strip():
            fence = None
    return False


def arch_findings(ip: Ip, wiki: Path, label) -> list[str]:
    """Source-side arch/ rules for one top-level IP (the layout check
    rejects arch/ for a sub-IP)."""
    if not ip.top_level:
        return []
    src_arch = ip.dir(ARCH_DIR)
    has = wiki / ARCH_DIR / ip.id / PAGES[ARCH_DIR]
    if not src_arch.is_dir():
        return ([f"{label(src_arch)}/: missing ({label(has)} defines targets; "
                 f"the arch model reports against them)"] if defines_targets(has) else [])
    found = []
    try:
        find_model(ip)
    except ArchError:
        try:
            name = model_name(ip)
        except ArchError:
            name = f"<prefix>{ip.id}_{ARCH_DIR}.py"
        found.append(f"{label(src_arch)}/: no model {name}")
    if not any((src_arch / PERF_DIR).glob("*.yaml")):
        found.append(f"{label(src_arch)}/: no perf scenario {PERF_DIR}/*.yaml")
    for f in imports.scan(ip):
        if f.origin == "syntax":
            found.append(f"{label(f.path)}:{f.line}: cannot parse for the import scan")
        else:
            found.append(f"{label(f.path)}:{f.line}: imports {f.module} ({f.origin}); "
                         f"arch never imports DV, RTL or tools code")
    return found


def required_pages(ip: Ip) -> list[str]:
    """Pages ``ip`` needs, relative to the wiki tree."""
    kinds = (["arch"] if ip.top_level else []) + ["rtl", "dv"]
    kinds += [k for k in ("fpga", "asic") if ip.dir(k).is_dir()]
    return [f"{k}/{ip.id}/{PAGES[k]}" for k in kinds]


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> CheckResult:
    src_root = Path(src_root).resolve()
    wiki = wiki_root(src_root)
    label = lambda p: Path(os.path.relpath(p, src_root.parent)).as_posix()  # noqa: E731
    by_id = {ip.id: ip for ip in ips}
    result = CheckResult("wiki")
    add = result.findings.append
    pages = folders = 0

    def chosen(ip_id: str | None) -> bool:
        return ip_ids is None or ip_id in ip_ids

    for ip in ips:
        if not chosen(ip.id):
            continue
        for page in required_pages(ip):
            pages += 1
            if not (wiki / page).is_file():
                add(f"{label(wiki / page)}: missing (required for IP {ip.id})")
        result.findings += arch_findings(ip, wiki, label)
    # Each block's page path below <kind>/common/: [<group>/]<block>.md.
    blocks = {f"{block_dir(f)}{block_name(f)}.md": f for f in shared_blocks(src_root)}
    groups = {block_dir(f).rstrip("/") for f in blocks.values()} - {""}
    if ip_ids is None:
        for b, f in sorted(blocks.items()):
            for kind in SHARED_KINDS:
                pages += 1
                page = wiki / kind / SHARED_DIR / b
                if not page.is_file():
                    add(f"{label(page)}: missing (required for shared block {block_name(f)})")

    kinds = ", ".join(k + "/" for k in KINDS)
    for dirpath, dirs, files in os.walk(wiki) if wiki.is_dir() else ():
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        files = sorted(f for f in files if not f.startswith("."))
        here = Path(dirpath)
        rel = here.relative_to(wiki)
        parts = rel.parts
        ip_id = parts[1] if len(parts) > 1 else None
        if len(parts) > 1 and not chosen(ip_id):
            dirs[:] = []
            continue
        if ip_ids is not None and len(parts) < 2:
            continue  # a filtered run looks only inside the chosen IPs' folders
        if parts == (DECISIONS,):
            for d in dirs:
                add(f"{label(here / d)}/: {DECISIONS}/ holds no folders")
            for f in files:
                if not _DECISION.match(f):
                    add(f"{label(here / f)}: not a decision record NNNN-<slug>.md")
            dirs[:] = []
            continue
        if len(parts) == 1 and parts[0] not in KINDS:
            add(f"{label(here)}/: not a kind folder ({kinds})")
            dirs[:] = []
            continue
        if len(parts) < 2:
            for f in files:
                if not parts and f in ROOT_PAGES:
                    continue
                add(f"{label(here / f)}: not in an IP folder <kind>/<ip>/ ({kinds})")
            continue
        if len(parts) in (2, 3) and ip_id == SHARED_DIR and parts[0] in SHARED_KINDS:
            folders += 1
            sub = "/".join(parts[2:])
            where = f"{parts[0]}/{SHARED_DIR}/" + (sub + "/" if sub else "")
            for d in dirs:
                if sub or d not in groups:
                    add(f"{label(here / d)}/: {where} holds only block pages"
                        + ("" if sub else " and the group folders of rtl/common/"))
            dirs[:] = [] if sub else [d for d in dirs if d in groups]
            for f in files:
                page = (sub + "/" if sub else "") + f
                if page not in blocks:
                    add(f"{label(here / f)}: names no shared block "
                        f"(no {label(src_root / 'rtl' / SHARED_DIR / page[:-3])}.rtl.f)")
            continue
        if len(parts) == 2:
            folders += 1
            ip = by_id.get(ip_id)
            if ip is None:
                add(f"{label(here)}/: names no IP (no {label(src_root / 'rtl' / ip_id)}/"
                    f"{ip_id}.rtl.f)")
                dirs[:] = []
                continue
            if parts[0] == ARCH_DIR:
                if not ip.top_level:
                    add(f"{label(here)}/: arch/ is for top-level IPs only "
                        f"({ip.id} is a sub-IP of {', '.join(ip.parents)})")
                    dirs[:] = []
                # A top-level IP's HAS may precede its arch model; the HAS
                # targets rule above requires the source once targets exist.
                continue
        else:
            folders += 1
        if not (src_root / rel).is_dir():
            add(f"{label(here)}/: no source counterpart {label(src_root / rel)}/")
            dirs[:] = []

    scope = f"--ip {' '.join(sorted(ip_ids))}" if ip_ids is not None else "all IPs"
    result.checked = (f"{pages} required page(s), {folders} wiki folder(s) "
                      f"under {label(wiki)}/ ({scope})")
    return result
