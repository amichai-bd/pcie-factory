"""RTL style checker: the mechanically checkable rules of wiki/bkm/rtl.md.

A lexical checker, not a parser: it blanks comments and strings, then looks
for constructs with regular expressions and a small bracket-aware scan of
module headers. Rules it cannot check reliably stay review items.

Provenance: the comment/string stripper, the package-span test, the
net-keyword, SYNTHESIS-guard, module-function and pragma-comment rules are
adapted from nand2anything tools/style/rtl_style.py (commit 085ef4b, same
owner, no license file). Project inventories, macros and exceptions were
dropped; the file-name, port-suffix, nettype, always, interface/union and
wildcard-connection and external-ifdef rules are new.
"""

from __future__ import annotations

import re
from pathlib import Path

from build.filelist import FilelistError, Resolved, resolve
from build.ip import SHARED_DIR, Ip, locate, owner_ip
from checks import filelists
from checks.common import (
    COMMON_PREFIX,
    CheckResult,
    in_kind,
    kind_files,
    line_of,
    read_prefix,
    rel,
    selected,
    strip_comments_and_strings,
)

# rule -> (RTL BKM anchor, what it enforces)
RULES = {
    "default-nettype": ("systemverilog-rules",
                        "`.sv` starts with `default_nettype none and ends with `default_nettype wire"),
    "net-keyword": ("systemverilog-rules", "no wire/reg/tri nets; use logic"),
    "plain-always": ("systemverilog-rules", "only always_comb and always_ff (no always, always_latch)"),
    "interface-union": ("systemverilog-rules", "no interface, modport or union in synthesizable RTL"),
    "wildcard-connection": ("systemverilog-rules", "named connections only; no .*"),
    "sim-ifdef": ("systemverilog-rules", "no `ifdef/`ifndef/`elsif on SYNTHESIS, SIMULATION or VERILATOR"),
    "external-ifdef": ("systemverilog-rules",
                       "no `ifdef/`ifndef/`elsif on a macro the RTL closure does not `define "
                       "(vendor or tool macros); include guards pass"),
    "task-function": ("systemverilog-rules", "no tasks; functions only inside a package"),
    "typedef-outside-package": ("types-and-constants-live-in-packages",
                                "typedefs only inside a package"),
    "file-name": ("naming", "one module or package per .sv, named after the file, "
                  "with the owner's prefix; packages end in _pkg"),
    "port-suffix": ("naming", "ANSI ports with direction; _i/_o/_io suffix (n before it for active-low)"),
    "verilator-config": ("lint-and-style-checks",
                         "no `verilator_config blocks (lint_off and friends outside the waiver file)"),
    "tool-pragma": ("lint-and-style-checks",
                    "no tool directive comments (verilator lint_off, synthesis translate_off, ...)"),
}

# A tool directive comment: a tool keyword followed by a directive word.
# Prose that merely starts with the keyword ("Synthesis infers a flop") is
# not one. Directive words adapted from nand2anything rtl_style.py
# PRAGMA_DIRECTIVES (commit 085ef4b); prefixes cover families (lint_off,
# lint_on, translate_off, syn_keep, ...).
_PRAGMA_RE = re.compile(
    r"^(?:verilator|synthesis|synopsys|pragma|altera)\s*:?\s+"
    r"(?:lint_|translate_|syn_|public|coverage_|tracing_|message_|unroll_|timing_|"
    r"no_inline|inline|dont_|protect|keep\b|preserve\b|noprune\b|ramstyle\b|"
    r"romstyle\b|full_case\b|parallel_case\b|read_comments_as_hdl\b|attribute\b|"
    r"multstyle\b|maxfan\b|direct_enable\b|useioff\b|split_var\b|"
    r"isolate_assignments\b|hier_block\b|clocker\b|no_clocker\b|clock_enable\b|"
    r"forceable\b|sformat\b|sc_bv\b|tag\b|altera_attribute\b)",
    re.IGNORECASE)
_COMMENT_RE = re.compile(r"//(?P<line>[^\n]*)|/\*(?P<block>.*?)\*/", re.DOTALL)
_NETTYPE_RE = re.compile(r"`default_nettype\s+(\w+)")
_UNIT_RE = re.compile(r"\b(module|package|interface|program|class)\s+(?:(?:automatic|static)\s+)?(\w+)")
_SUFFIX = {"input": re.compile(r"_n?i$"), "output": re.compile(r"_n?o$"),
           "inout": re.compile(r"_n?io$")}
_IDENT = re.compile(r"[A-Za-z_][\w$]*")
# Tool macros the sim-ifdef rule owns; external-ifdef leaves them to it.
_SIM_MACROS = ("SYNTHESIS", "SIMULATION", "VERILATOR")
# `ifdef NAME, or a parenthesized macro expression (`ifdef (A && !B)).
_COND_RE = re.compile(r"`(ifdef|ifndef|elsif)\b[ \t]*(\(|[A-Za-z_]\w*)?")
_DEFINE_RE = re.compile(r"`define\s+([A-Za-z_]\w*)")


def _package_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for m in re.finditer(r"\bpackage\b", text):
        end = text.find("endpackage", m.end())
        spans.append((m.start(), len(text) if end < 0 else end))
    return spans


def _in(index: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= index < b for a, b in spans)


def _balanced(text: str, start: int) -> int:
    """Index just past the bracket group opening at ``text[start]``."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] in "([{":
            depth += 1
        elif text[i] in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def _split_top(text: str, start: int, end: int) -> list[tuple[int, str]]:
    """Split ``text[start:end]`` at depth-0 commas -> (offset, piece)."""
    parts, depth, s = [], 0, start
    for i in range(start, end):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            parts.append((s, text[s:i]))
            s = i + 1
    parts.append((s, text[s:end]))
    return parts


def _port_name(decl: str) -> tuple[int, str] | None:
    """(offset, name) of the declared identifier: the last depth-0
    identifier before any default value."""
    depth, best, i = 0, None, 0
    while i < len(decl):
        c = decl[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "=" and depth == 0:
            break
        elif (c.isalpha() or c == "_") and depth == 0:
            m = _IDENT.match(decl, i)
            if decl[i - 1:i] not in ("$", "'", "`"):
                best = (i, m.group())
            i = m.end()
            continue
        i += 1
    return best


def module_ports(text: str):
    """Yield (index, direction or None, name) for each ANSI port of every
    module in comment-stripped ``text``."""
    for m in re.finditer(r"\bmodule\s+(?:(?:automatic|static)\s+)?\w+", text):
        i = m.end()
        while True:
            while i < len(text) and text[i].isspace():
                i += 1
            if re.match(r"import\b", text[i:i + 7]):
                i = text.find(";", i) + 1 or len(text)
                continue
            break
        if text.startswith("#", i):
            p = text.find("(", i)
            i = _balanced(text, p) if p >= 0 else len(text)
            while i < len(text) and text[i].isspace():
                i += 1
        if not text.startswith("(", i):
            continue
        end = _balanced(text, i) - 1
        direction = None
        for off, piece in _split_top(text, i + 1, end):
            if not piece.strip():
                continue
            d = re.match(r"\s*(input|output|inout|ref)\b", piece)
            if d:
                direction = d.group(1)
            named = _port_name(piece)
            if named is None:
                continue
            yield off + named[0], direction, named[1]


def defined_macros(text: str) -> set[str]:
    """Names `` `define``d in comment-stripped ``text``."""
    return set(_DEFINE_RE.findall(text))


def ifdef_findings(relpath: str, text: str, defined: frozenset[str] | set[str] = frozenset()
                   ) -> list[str]:
    """``sim-ifdef`` and ``external-ifdef`` findings in comment-stripped
    ``text``. A macro is internal when this file or ``defined`` (the rest of
    its RTL closure: `` `define``s and filelist ``+define+``) defines it, so
    an include guard passes; any other macro can only come from a tool or
    vendor flow and would hide code from lint."""
    known = defined_macros(text) | set(defined)
    out = []
    for m in _COND_RE.finditer(text):
        directive, operand = m.group(1), m.group(2) or ""
        if operand == "(":
            operand = text[m.start(2):_balanced(text, m.start(2))]
        line = line_of(text, m.start())
        names = _IDENT.findall(operand)
        if not names:
            out.append(f"{relpath}:{line}: external-ifdef: `{directive} without a macro name")
        for name in names:
            if name in _SIM_MACROS:
                out.append(f"{relpath}:{line}: sim-ifdef: `{directive} {name}")
            elif name not in known:
                out.append(f"{relpath}:{line}: external-ifdef: `{directive} {name} "
                           "is not `define'd by the RTL closure")
    return out


def directive_findings(relpath: str, raw: str, defined: frozenset[str] | set[str] = frozenset()
                       ) -> list[str]:
    """Findings that matter for files that can be `` `include``d whatever
    their suffix: ``tool-pragma``, ``verilator-config``, and the two
    conditional-compilation rules (``sim-ifdef``, ``external-ifdef``)."""
    text = strip_comments_and_strings(raw)
    out = ifdef_findings(relpath, text, defined)
    for m in _COMMENT_RE.finditer(raw):
        body = (m.group("line") if m.group("line") is not None else m.group("block")).strip()
        if _PRAGMA_RE.match(body):
            out.append(f"{relpath}:{line_of(raw, m.start())}: tool-pragma: {body[:60]}")
    for m in re.finditer(r"`verilator_config\b", text):
        out.append(f"{relpath}:{line_of(text, m.start())}: verilator-config: {m.group()}")
    return out


def check_text(relpath: str, raw: str, expected_prefix: str | None,
               defined: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Apply every rule to one file; ``expected_prefix`` is the name prefix
    a unit in this file must start with (None: no prefix declared);
    ``defined`` holds the macros the rest of its RTL closure defines."""
    text = strip_comments_and_strings(raw)
    path = Path(relpath)
    out: list[str] = []

    def add(rule: str, index: int, detail: str) -> None:
        out.append(f"{relpath}:{line_of(text, index)}: {rule}: {detail.strip()}")

    out += directive_findings(relpath, raw, defined)

    if path.suffix == ".sv":
        tokens = text.split()
        if tokens[:2] != ["`default_nettype", "none"]:
            add("default-nettype", 0, "first statement must be `default_nettype none")
        if tokens[-2:] != ["`default_nettype", "wire"]:
            add("default-nettype", len(text.rstrip()),
                "last statement must be `default_nettype wire")
    body = _NETTYPE_RE.sub(lambda m: " " * len(m.group()), text)

    for m in re.finditer(r"(?<![\w$.`])(wire|reg|tri|tri0|tri1|wand|wor|uwire)\b", body):
        add("net-keyword", m.start(), m.group())
    for m in re.finditer(r"\balways(?:_latch)?\b", body):
        add("plain-always", m.start(), m.group())
    for m in re.finditer(r"\b(interface|modport|union)\b", body):
        add("interface-union", m.start(), m.group())
    for m in re.finditer(r"\.\s*\*", body):
        add("wildcard-connection", m.start(), ".*")
    packages = _package_spans(body)
    for m in re.finditer(r"\b(task|function)\b", body):
        if m.group(1) == "task" or not _in(m.start(), packages):
            add("task-function", m.start(), m.group())
    for m in re.finditer(r"\btypedef\b", body):
        if not _in(m.start(), packages):
            add("typedef-outside-package", m.start(), "typedef")

    if path.suffix == ".sv":
        units = list(_UNIT_RE.finditer(body))
        if len(units) != 1:
            add("file-name", 0, f"expected exactly one module or package, found {len(units)}")
        for u in units:
            kind, name = u.group(1), u.group(2)
            if name != path.stem:
                add("file-name", u.start(), f"{kind} {name} in file {path.name}")
            if kind == "package" and not name.endswith("_pkg"):
                add("file-name", u.start(), f"package {name} must end in _pkg")
            if kind == "module" and name.endswith("_pkg"):
                add("file-name", u.start(), f"module {name} must not end in _pkg")
            if expected_prefix is None:
                add("file-name", u.start(), "src/README.md declares no module prefix")
            elif not (name == expected_prefix.rstrip("_")
                      or name.startswith(expected_prefix if expected_prefix.endswith("_")
                                         else expected_prefix + "_")):
                add("file-name", u.start(), f"{name} must start with {expected_prefix}")

    for index, direction, name in module_ports(body):
        if direction is None:
            add("port-suffix", index, f"{name}: no direction (use ANSI ports)")
        elif direction in _SUFFIX and not _SUFFIX[direction].search(name):
            add("port-suffix", index, f"{direction} {name}: suffix must match {_SUFFIX[direction].pattern}")
        elif direction == "ref":
            add("port-suffix", index, f"ref port {name} is not allowed")
    return sorted(set(out), key=lambda s: (s.split(":")[0], int(s.split(":")[1]), s))


def expected_prefix(path: Path, src_root: Path, ips: list[Ip], prefix: str | None) -> str | None:
    """``common_`` under ``<kind>/common/``, else ``<prefix><ip>`` for the
    owning IP (``<prefix>`` alone when no IP owns the file)."""
    if locate(src_root, path)[1] == SHARED_DIR:
        return COMMON_PREFIX
    if prefix is None:
        return None
    owner = owner_ip(ips, path)
    return prefix + owner.id if owner is not None else prefix


def include_scope(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> list[Path]:
    """Every file, of any type, that an ``rtl/`` closure could `` `include``
    (everything below its include directories inside the source root), plus
    every file below an ``fpga/`` directory."""
    found: set[Path] = set()
    for f in filelists.all_filelists(src_root):
        if not in_kind(f, src_root, ("rtl",)) or not selected(f, ips, ip_ids):
            continue
        try:
            dirs = resolve(f).incdirs
        except FilelistError:
            continue  # the filelist check reports it
        for d in dirs:
            if d == src_root or src_root in d.parents:
                found.update(p for p in d.rglob("*") if p.is_file())
    found.update(p for p in kind_files(src_root, ("fpga",), None) if selected(p, ips, ip_ids))
    return sorted(found)


def _text(path: Path) -> str | None:
    """Decoded contents, or None for a binary file (a NUL byte)."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return None if b"\0" in data else data.decode(errors="replace")


def closure_macros(src_root: Path, ips: list[Ip]) -> tuple[list[tuple[Resolved, set[str]]],
                                                            dict[Path, set[str]]]:
    """Macros each ``rtl/`` filelist closure defines: `` `define``s in its
    sources and in every file below its include directories (inside the
    source root), plus its ``+define+`` names. Returns (closure, macros)
    pairs, and the macros of each IP's own ``.rtl.f`` keyed by the IP's
    ``fpga/`` directory (a board wrapper is linted with those sources)."""
    cache: dict[Path, set[str]] = {}

    def macros(f: Path) -> set[str]:
        f = f.resolve()
        if f not in cache:
            text = _text(f)
            cache[f] = defined_macros(strip_comments_and_strings(text)) if text else set()
        return cache[f]

    closures = []
    by_fpga: dict[Path, set[str]] = {}
    for f in filelists.all_filelists(src_root):
        if not in_kind(f, src_root, ("rtl",)):
            continue
        try:
            flat = resolve(f)
        except FilelistError:
            continue  # the filelist check reports it
        names = set(flat.defines)
        files = set(flat.sources)
        for d in flat.incdirs:
            if d == src_root.resolve() or src_root.resolve() in d.parents:
                files.update(p for p in d.rglob("*") if p.is_file())
        for p in files:
            names |= macros(p)
        closures.append((flat, names))
        for ip in ips:
            if ip.rtl_f.resolve() == f.resolve():
                by_fpga[ip.dir("fpga").resolve()] = names
    return closures, by_fpga


def _defined_for(path: Path, closures, by_fpga) -> set[str]:
    """Union of the macros of every closure that reaches ``path``."""
    path = path.resolve()
    out: set[str] = set()
    for flat, names in closures:
        if flat.reaches(path):
            out |= names
    for d, names in by_fpga.items():
        if d in path.parents:
            out |= names
    return out


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> CheckResult:
    prefix = read_prefix(src_root)
    files = [f for f in kind_files(src_root, ("rtl", "fpga")) if selected(f, ips, ip_ids)]
    extra = [f for f in include_scope(src_root, ips, ip_ids) if f not in set(files)]
    result = CheckResult("style", f"{len(files)} RTL file(s), {len(RULES)} rule(s); "
                         f"{len(extra)} other includable or fpga/ file(s) for directives")
    closures, by_fpga = closure_macros(src_root, ips)
    for f in files:
        exp = expected_prefix(f, src_root, ips, prefix)
        result.findings += check_text(rel(f, src_root), f.read_text(errors="replace"), exp,
                                      _defined_for(f, closures, by_fpga))
    for f in extra:
        text = _text(f)
        if text is not None:  # binary files cannot carry directives
            result.findings += directive_findings(rel(f, src_root), text,
                                                  _defined_for(f, closures, by_fpga))
    return result
