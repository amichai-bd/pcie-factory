"""Machine pre-check of a pull request's recorded evidence (``./build pr check``).

Reads a PR body, head SHA, changed files and labels (GitHub REST through
``gh api``, or a local file for tests) and reports the evidence the
pr-creation skill requires that is missing or malformed. It never writes to
GitHub and never judges evidence quality. See wiki/tools/pr/SPEC.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from labels import core as labels
from regress.schema import load_budgets

_PER_PAGE = 100
_SHA = re.compile(r"^[0-9a-f]{40}$")


class PrError(Exception):
    """Configuration, input or GitHub error (exit 2)."""


@dataclass(frozen=True)
class VersionRule:
    when: str
    need: tuple[str, ...]


@dataclass(frozen=True)
class Rules:
    mutation_paths: tuple[str, ...]
    seeded_commands: tuple[str, ...]
    versions: tuple[VersionRule, ...]
    budgets: dict  # level -> CPU budget in seconds (cfg/levels.yaml)


@dataclass(frozen=True)
class Pr:
    number: int
    body: str
    head_sha: str
    files: tuple[str, ...] | None  # None: unknown (file mode without --files)
    labels: tuple[str, ...] | None  # None: unknown (file mode without --labels)


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str


# --- configuration ------------------------------------------------------------

def _str_list(path: Path, key: str, value) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or \
            not all(isinstance(v, str) and v.strip() for v in value):
        raise PrError(f"{path}: {key} must be a non-empty list of strings")
    return tuple(value)


def load_rules(cfg_dir: Path) -> Rules:
    path = Path(cfg_dir) / "pr.yaml"
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise PrError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise PrError(f"{path}: invalid YAML: {exc}") from exc
    keys = {"mutation_paths", "seeded_commands", "versions"}
    if not isinstance(data, dict) or set(data) != keys:
        raise PrError(f"{path}: top level must be a mapping with exactly the keys "
                      f"{', '.join(sorted(keys))}")
    versions = []
    if not isinstance(data["versions"], list) or not data["versions"]:
        raise PrError(f"{path}: versions must be a non-empty list")
    for i, item in enumerate(data["versions"]):
        if not isinstance(item, dict) or set(item) != {"when", "need"} \
                or not isinstance(item["when"], str) or not item["when"].strip():
            raise PrError(f"{path}: versions[{i}] must have a non-empty 'when' and 'need'")
        versions.append(VersionRule(item["when"], _str_list(path, f"versions[{i}].need",
                                                             item["need"])))
    budgets = {lv: b.budget_s for lv, b in load_budgets(Path(cfg_dir)).items()}
    return Rules(_str_list(path, "mutation_paths", data["mutation_paths"]),
                 _str_list(path, "seeded_commands", data["seeded_commands"]),
                 tuple(versions), budgets)


# --- inputs -------------------------------------------------------------------

def fetch(gh: labels.Gh, number: int) -> Pr:
    """Read a PR's body, head SHA, changed files and labels (GET only).

    Files the PR removes are left out: a deleted file has nothing to mutate.
    """
    data = gh.api("GET", f"repos/{gh.repo}/pulls/{number}")
    files, page = [], 1
    while True:
        batch = gh.api("GET", f"repos/{gh.repo}/pulls/{number}/files"
                              f"?per_page={_PER_PAGE}&page={page}")
        files += [f["filename"] for f in batch if f.get("status") != "removed"]
        if len(batch) < _PER_PAGE:
            break
        page += 1
    return Pr(number, data.get("body") or "", data["head"]["sha"], tuple(files),
              tuple(x["name"] for x in data.get("labels", [])))


def from_file(number: int, body_file: Path, head: str, files: list[str] | None,
              label_names: list[str] | None) -> Pr:
    try:
        body = Path(body_file).read_text()
    except OSError as exc:
        raise PrError(f"cannot read {body_file}: {exc}") from exc
    if not _SHA.match(head or ""):
        raise PrError(f"--head must be a full 40-character lower-case SHA, got {head!r}")
    return Pr(number, body, head, None if files is None else tuple(files),
              None if label_names is None else tuple(label_names))


# --- Markdown parsing -----------------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_INLINE_CODE = re.compile(r"(`+)(.+?)\1", re.S)
_SEPARATOR_CELL = re.compile(r"^:?-+:?$")


def _prose(body: str) -> str:
    """The body without HTML comments and with fenced code blocks blanked."""
    out, fenced = [], False
    for line in _COMMENT.sub("", body.replace("\r\n", "\n")).split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else line)
    return "\n".join(out)


def section(text: str, title: str) -> str | None:
    """The text under the level-2 heading `## <title>` (case-insensitive)."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if re.match(rf"^##\s+{re.escape(title)}\s*$", line, re.I):
            end = next((j for j in range(i + 1, len(lines))
                        if re.match(r"^#{1,2}\s", lines[j])), len(lines))
            return "\n".join(lines[i + 1:end])
    return None


def cells(line: str) -> list[str]:
    """Split a table row on '|' outside code spans; '\\|' is a literal bar."""
    s = line.strip()
    s = s[1:] if s.startswith("|") else s
    out, cur, i, tick = [], "", 0, ""
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "|"
            i += 2
            continue
        if ch == "`":
            run = re.match(r"`+", s[i:]).group(0)
            tick = "" if tick == run else (tick or run)
            cur += run
            i += len(run)
            continue
        if ch == "|" and not tick:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    if cur.strip():
        out.append(cur.strip())
    return out


@dataclass(frozen=True)
class Table:
    header: list[str]
    separator: bool  # the line after the header is a separator row
    rows: list[list[str]]


def tables(text: str) -> list[Table]:
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block.append(cells(lines[i]))
            i += 1
        sep = len(block) > 1 and bool(block[1]) and \
            all(_SEPARATOR_CELL.match(c.replace(" ", "")) for c in block[1])
        out.append(Table(block[0], sep, block[2:] if sep else block[1:]))
    return out


def _code_spans(cell: str) -> list[str]:
    return [m.group(2).strip() for m in _INLINE_CODE.finditer(cell)]


# --- rules ------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    commands: tuple[str, ...]  # code spans of the Command cell
    command_cell: str
    result: str


def _command_rows(validation: str) -> tuple[Table | None, list[Row]]:
    table = next((t for t in tables(validation)
                  if t.header and t.header[0].strip().lower() == "command"), None)
    if table is None:
        return None, []
    lower = [h.strip().lower() for h in table.header]
    col = lower.index("result") if "result" in lower else len(lower) - 1
    rows = [Row(tuple(_code_spans(r[0]) if r else ()), r[0] if r else "",
                r[col] if col < len(r) else "") for r in table.rows]
    return table, rows


def rule_head_sha(pr: Pr, validation: str, rows, rules) -> list[Finding]:
    m = re.search(r"\bat head\s+`?([0-9a-fA-F]{7,40})\b`?", validation, re.I)
    if m is None:
        return [Finding("head-sha", "the Validation section has no 'At head <sha>' line")]
    sha = m.group(1).lower()
    if len(sha) != 40:
        return [Finding("head-sha", f"'At head {sha}' must cite the full 40-character SHA")]
    if sha != pr.head_sha:
        return [Finding("head-sha", f"'At head {sha}' does not match the PR head {pr.head_sha}")]
    return []


def rule_commands(pr: Pr, validation: str, rows, rules) -> list[Finding]:
    table, _ = _command_rows(validation)
    if table is None:
        return [Finding("commands", "the Validation section has no '| Command | Result |' table")]
    out = []
    if not table.separator:
        out.append(Finding("commands", "the command table has no separator row"))
    if not table.rows:
        out.append(Finding("commands", "the command table has no rows"))
    for r in rows:
        if not r.commands:
            out.append(Finding("commands", f"row {r.command_cell[:60]!r}: the command is not "
                                           "quoted exactly in backticks"))
        if not r.result.strip():
            out.append(Finding("commands", f"row {r.command_cell[:60]!r}: no result"))
    return out


def _run(rows: list[Row], needle: str) -> bool:
    return any(needle in c for r in rows for c in r.commands)


def rule_versions(pr: Pr, validation: str, rows, rules) -> list[Finding]:
    need = []
    for v in rules.versions:
        if _run(rows, v.when):
            need += [n for n in v.need if n not in need]
    return [Finding("versions", f"no {name} version stated ('{name} <x.y>')")
            for name in need
            if not re.search(rf"(?<![\w-]){re.escape(name)}\s+v?\d+(\.\d+)+", validation, re.I)]


def rule_seeds(pr: Pr, validation: str, rows, rules) -> list[Finding]:
    if not any(_run(rows, s) for s in rules.seeded_commands):
        return []
    for line in validation.split("\n"):
        if re.search(r"\bseeds?\b", line, re.I) and re.search(r"\d|crc32|#seeds", line, re.I):
            return []
    return [Finding("seeds", "seeded tests ran but no line states the seeds "
                             "(numbers, or the crc32 seed rule)")]


_LEVEL = re.compile(r"\b(?:L|level\s?)(\d)\b\s*:?\s*(\d+)\s*/\s*(\d+)", re.I)
_CPU = re.compile(r"\bCPU\s*:?\s*(\d+(?:\.\d+)?)\s*s\b", re.I)


def rule_levels(pr: Pr, validation: str, rows, rules) -> list[Finding]:
    out = []
    for r in rows:
        cmd = next((c for c in r.commands if "./build regress" in c), None)
        if cmd is None:
            continue
        hits = list(_LEVEL.finditer(r.result))
        for level in sorted({int(x) for x in re.findall(r"--level[ =](\d)", cmd)}):
            idx = next((i for i, h in enumerate(hits) if int(h.group(1)) == level), None)
            if idx is None:
                out.append(Finding("levels", f"`{cmd}`: no 'L{level} <passed>/<total>' result"))
                continue
            end = hits[idx + 1].start() if idx + 1 < len(hits) else len(r.result)
            cpu = _CPU.search(r.result, hits[idx].end(), end)
            if cpu is None:
                out.append(Finding("levels", f"`{cmd}`: no 'CPU <s> s' after L{level}"))
                continue
            budget = rules.budgets.get(level)
            if budget is not None and float(cpu.group(1)) > budget and not re.search(
                    rf"\bL{level} budget\s*:", pr.body, re.I):
                out.append(Finding("budget", f"`{cmd}`: L{level} CPU {cpu.group(1)} s is over "
                                             f"its {budget:g} s budget with no "
                                             f"'L{level} budget:' explanation"))
    return out


def rule_mutants(pr: Pr, validation: str, rows, rules) -> list[Finding]:
    if pr.files is None or not any(f.startswith(p) for f in pr.files
                                   for p in rules.mutation_paths):
        return []
    table = next((t for t in tables(validation)
                  if any("mutant" in h.lower() for h in t.header)), None)
    where = ", ".join(rules.mutation_paths)
    if table is None:
        return [Finding("mutants", f"the PR changes {where} but the Validation section has "
                                   "no mutation table (a header with a 'Mutant' column)")]
    if not table.separator:
        return [Finding("mutants", "the mutation table has no separator row")]
    if not table.rows:
        return [Finding("mutants", "the mutation table has no rows")]
    return []


def rule_not_run(pr: Pr, validation: str, rows, rules) -> list[Finding]:
    lines = validation.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^###\s+Not run\s*$", line, re.I):
            rest = []
            for nxt in lines[i + 1:]:
                if re.match(r"^#{1,3}\s", nxt):
                    break
                rest.append(nxt)
            if not "".join(rest).strip():
                return [Finding("not-run", "the '### Not run' list is empty "
                                           "(write 'None.' if nothing was cut)")]
            return []
    return [Finding("not-run", "the Validation section has no '### Not run' subsection")]


_CLOSING = re.compile(r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b\s*:?\s+#(\d+)\b", re.I)
_PART_OF = re.compile(r"\bpart of\s+(?:(?:the\s+)?epic\s+)?#(\d+)\b", re.I)


def rule_keywords(pr: Pr, prose: str) -> list[Finding]:
    text = _INLINE_CODE.sub("", prose)
    closes = sorted({int(m.group(2)) for m in _CLOSING.finditer(text)})
    part_of = sorted({int(m.group(1)) for m in _PART_OF.finditer(text)})
    out = [Finding("keywords", f"#{n} is 'Part of' and also next to a closing keyword "
                               "(close/fix/resolve), so merging would close it")
           for n in closes if n in part_of]
    if len(closes) > 1:
        out.append(Finding("keywords", "closing keywords name more than one issue: "
                                       + ", ".join(f"#{n}" for n in closes)))
    if not closes and not part_of:
        out.append(Finding("keywords", "no 'Closes #<n>' (or 'Part of #<n>' for a partial PR)"))
    return out


RULES = (rule_head_sha, rule_commands, rule_versions, rule_seeds, rule_levels,
         rule_mutants, rule_not_run)


def check(pr: Pr, rules: Rules, standard: labels.Standard | None) -> tuple[list[Finding],
                                                                           list[str]]:
    """All findings for one PR, and notes on rules that could not be applied."""
    prose = _prose(pr.body)
    findings, notes = [], []
    validation = section(prose, "Validation")
    if validation is None:
        findings.append(Finding("validation", "the body has no '## Validation' section"))
    else:
        _, rows = _command_rows(validation)
        for rule in RULES:
            findings += rule(pr, validation, rows, rules)
    if pr.files is None:
        notes.append("mutants: changed files unknown (no --files); not checked")
    findings += rule_keywords(pr, prose)
    if pr.labels is None or standard is None:
        notes.append("labels: PR labels unknown (no --labels); not checked")
    else:
        errors, _ = labels.check(standard, list(pr.labels))
        findings += [Finding("labels", e) for e in errors]
    return findings, notes


def report(pr: Pr, findings: list[Finding], notes: list[str], out) -> int:
    print(f"pr check: #{pr.number} at {pr.head_sha}", file=out)
    for n in notes:
        print(f"note: #{pr.number}: {n}", file=out)
    for f in findings:
        print(f"error: #{pr.number}: [{f.rule}] {f.message}", file=out)
    print(f"#{pr.number}: evidence {'FAIL' if findings else 'OK'}", file=out)
    return 1 if findings else 0
