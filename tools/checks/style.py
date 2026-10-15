"""RTL style checker: the mechanically checkable rules of wiki/bkm/rtl.md.

A lexical checker, not a parser: it blanks comments and strings, then looks
for constructs with regular expressions and a small bracket-aware scan of
module headers. Rules it cannot check reliably stay review items.

Provenance: the comment/string stripper, the package-span test, the
net-keyword, SYNTHESIS-guard, module-function and pragma-comment rules are
adapted from nand2anything tools/style/rtl_style.py (commit 085ef4b, same
owner, no license file). Project inventories, macros and exceptions were
dropped; the file-name, port-suffix, nettype, always, interface/union and
wildcard-connection and external-ifdef rules are new. The vocabulary rules
(raw-always-ff, raw-assertion, raw-initial: constructs reserved for the
shared macro headers) follow the same tool's clocked-process, raw-assert and
raw-initial rules, and nonblocking-assignment ports its scan;
system-task, sampled-value, assert-label, clocking-block, object-macro,
local-define, conditional-compilation, package-import, include-path,
vocabulary-include and cdc-cell-macro are new.

The vocabulary headers are the files named in ``VOCABULARY_HEADERS``,
directly in ``rtl/common/`` (the RTL BKM's shared vocabulary). They alone
may hold ``always_ff``, ``<=``, assertions, system tasks, ``initial``
blocks and ``SYNTHESIS``/``VERILATOR`` conditionals; RTL reaches them with
a bare-name `` `include``. Any other ``.svh`` gets no exemption. Elsewhere
the sampled-value functions are legal only inside `` `COMMON_ASSERT*``
arguments, whose first argument (the NAME or LABEL the macro pastes as a
bare token) must be a single identifier, and no file, header or not, may
declare a clocking block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
    "plain-always": ("systemverilog-rules",
                     "no always (including always @*) and no always_latch; always_comb is the only "
                     "process keyword in design RTL (always_ff only inside the vocabulary headers, "
                     "see raw-always-ff)"),
    "interface-union": ("systemverilog-rules", "no interface, modport or union in synthesizable RTL"),
    "wildcard-connection": ("systemverilog-rules", "named connections only; no .*"),
    "sim-ifdef": ("no-conditional-compilation",
                  "no `ifdef/`ifndef/`elsif on SYNTHESIS, SIMULATION or VERILATOR "
                  "(the vocabulary headers may use SYNTHESIS and VERILATOR)"),
    "external-ifdef": ("no-conditional-compilation",
                       "no `ifdef/`ifndef/`elsif on a macro the RTL closure does not `define "
                       "(vendor or tool macros); include guards pass"),
    "task-function": ("systemverilog-rules", "no tasks; functions only inside a package"),
    "typedef-outside-package": ("types-and-constants-live-in-packages",
                                "typedef, enum and struct declarations only inside a package"),
    "file-name": ("naming", "one module or package per .sv, named after the file, "
                  "with the owner's prefix; packages end in _pkg"),
    "port-suffix": ("naming", "ANSI ports with direction; _i/_o/_io suffix (n before it for active-low)"),
    "verilator-config": ("lint-and-style-checks",
                         "no `verilator_config blocks (lint_off and friends outside the waiver file)"),
    "tool-pragma": ("lint-and-style-checks",
                    "no tool directive comments (verilator lint_off, synthesis translate_off, ...)"),
    "nonblocking-assignment": ("registers-and-memories",
                               "no nonblocking assignment (<=) outside the vocabulary headers"),
    "conditional-compilation": ("no-conditional-compilation",
                                "no `ifdef/`ifndef/`elsif/`else in a .sv file (only the vocabulary "
                                "headers select by flow; include guards only in .svh files)"),
    "raw-always-ff": ("registers-and-memories",
                      "no always_ff outside the vocabulary headers (use a COMMON_DFF* or COMMON_MEM* macro)"),
    "raw-assertion": ("assertions",
                      "no assert, assume, cover, property, sequence or $fatal/$error/$warning/$info "
                      "outside the vocabulary headers (use a COMMON_ASSERT* macro)"),
    "raw-initial": ("assertions", "no initial or final block outside the vocabulary headers"),
    "system-task": ("assertions",
                    "no system task or function ($display, $finish, $readmemh, ...) outside the "
                    "vocabulary headers, except pure functions ($bits, $clog2, $signed, ...) and "
                    "the sampled-value functions (see sampled-value)"),
    "sampled-value": ("assertions",
                      "$past, $stable, $rose, $fell, $changed and $sampled only inside the "
                      "arguments of a COMMON_ASSERT* vocabulary macro (elsewhere $past is a "
                      "register outside the COMMON_DFF* macros)"),
    "assert-label": ("assertions",
                     "the first argument (NAME or LABEL) of a COMMON_ASSERT* call is a single "
                     "identifier: the macro pastes it as a bare token, so anything else is "
                     "design text outside the assertion"),
    "clocking-block": ("registers-and-memories",
                       "no clocking block (clocking, default clocking, global clocking) in any "
                       "checked file: it gives sampled-value functions an implicit clock"),
    "local-define": ("no-conditional-compilation",
                     "no `define or `undef in a .sv (macros live only in the vocabulary headers)"),
    "package-import": ("types-and-constants-live-in-packages",
                       "no import; name package items explicitly as <pkg>::<name>"),
    "cdc-cell-macro": ("clock-domain-crossings",
                       "outside rtl/common/, a crossing cell a vocabulary macro wraps is "
                       "instantiated only through that macro (COMMON_METAFLOP, ...)"),
    "object-macro": ("the-rtl-vocabulary",
                     "outside the vocabulary headers, no use of an object-like vocabulary macro "
                     "(an include guard such as COMMON_MACROS_SVH); only the function-like "
                     "forms are vocabulary"),
    "include-path": ("includes", "`include names a vocabulary header by its bare file name"),
    "vocabulary-include": ("includes",
                           "a file that uses a vocabulary macro `includes the vocabulary itself"),
    "bare-generate": ("systemverilog-rules",
                      "a module-level for, if or case (a generate construct) sits inside "
                      "generate ... endgenerate (Quartus Lite rejects a bare generate region)"),
    "inline-genvar": ("systemverilog-rules",
                      "no genvar declared in a for header; declare it at module level "
                      "(Quartus Lite rejects for (genvar i ...))"),
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
# The guards the vocabulary headers may use: SYNTHESIS (defined by the
# synthesis flows) omits simulation-only forms; VERILATOR keeps lint-only
# sinks out of synthesis.
_HEADER_GUARDS = ("SYNTHESIS", "VERILATOR")
# `ifdef NAME, or a parenthesized macro expression (`ifdef (A && !B)). The
# name or "(" must be on the directive's line; macro names may contain $.
_COND_RE = re.compile(r"`(ifdef|ifndef|elsif)\b[ \t]*(\(|[A-Za-z_][\w$]*)?")
_DEFINE_RE = re.compile(r"`define\s+([A-Za-z_][\w$]*)")
# A function-like macro: the parameter list's "(" follows the name directly
# (IEEE 1800 22.5.1); with a space between, the "(" is part of the body.
_FUNCTION_DEFINE_RE = re.compile(r"`define\s+([A-Za-z_][\w$]*)\(")
_INCLUDE_RE = re.compile(r"`include\b[ \t]*(\"[^\"\n]*\"|<[^>\n]*>)?")
_ASSERTION_RE = re.compile(r"\b(assert|assume|cover|restrict|expect|property|sequence)\b"
                           r"|\$(fatal|error|warning|info)\b")
_SYSTEM_RE = re.compile(r"(?<![\w$])\$[A-Za-z_][\w$]*")
# Pure system functions design RTL may call anywhere: constant and width
# functions, conversions and bit counts. Every other system task or function
# ($display, $finish, $stop, $readmemh, $random, ...) is a finding outside
# the headers, except the sampled-value functions below.
PURE_SYSTEM_FUNCTIONS = frozenset({
    "$bits", "$clog2", "$signed", "$unsigned", "$size", "$low", "$high", "$left", "$right",
    "$increment", "$dimensions", "$unpacked_dimensions", "$countones", "$countbits",
    "$onehot", "$onehot0", "$isunknown",
})
# Sampled-value functions: legal only inside the arguments of an assertion
# macro (rule sampled-value). Outside one, with a default clocking block in
# scope, `assign r = $past(d);` is a register that bypasses the DFF macros.
# The _gclk forms need global clocking and stay system-task findings.
SAMPLED_VALUE_FUNCTIONS = frozenset({"$past", "$stable", "$rose", "$fell", "$changed",
                                     "$sampled"})
# Vocabulary macros whose arguments may hold sampled-value functions: the
# assertion forms, by name prefix, and only function-like macros a header
# defines (an object-like one, such as the COMMON_ASSERTIONS_SVH guard,
# takes no arguments, so a "(" after it is plain design text).
ASSERT_MACRO_PREFIX = COMMON_PREFIX.upper() + "ASSERT"
# A `$` is an identifier character, so `clocking$x` is a name, not the keyword.
_CLOCKING_RE = re.compile(r"(?<![\w$])clocking(?![\w$])")
# The vocabulary headers, by file name directly in rtl/common/. Adding one
# is a reviewed change: a listed header is exempt from the design rules.
VOCABULARY_HEADERS = ("common_macros.svh", "common_assertions.svh")


@dataclass(frozen=True)
class Vocabulary:
    """The shared vocabulary headers: ``VOCABULARY_HEADERS`` found in
    ``rtl/common/`` (paths relative to the source root), their file names, the macros they define, the
    shared cells (``rtl/common/[<group>/]<cell>.sv``) their macros instantiate, and
    the function-like macros among ``macros`` (defined with a parameter
    list; the rest are object-like, such as the include guards)."""

    headers: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()
    macros: frozenset[str] = frozenset()
    cells: frozenset[str] = frozenset()
    functions: frozenset[str] = frozenset()


def vocabulary(src_root: Path) -> Vocabulary:
    """Read the vocabulary headers directly in ``<src_root>/rtl/common/``."""
    d = Path(src_root) / "rtl" / SHARED_DIR
    files = [d / name for name in VOCABULARY_HEADERS if (d / name).is_file()]
    macros: set[str] = set()
    functions: set[str] = set()
    cells: set[str] = set()
    # Shared cell sources, directly in rtl/common/ or in a group folder.
    shared_sv = {p.stem for p in (*d.glob("*.sv"), *d.glob("*/*.sv")) if p.is_file()}
    for f in files:
        text = strip_comments_and_strings(f.read_text(errors="replace"))
        macros |= defined_macros(text)
        functions |= set(_FUNCTION_DEFINE_RE.findall(text))
        cells |= {name for name in _IDENT.findall(text) if name in shared_sv}
    return Vocabulary(frozenset(f"rtl/{SHARED_DIR}/{f.name}" for f in files),
                      frozenset(f.name for f in files), frozenset(macros), frozenset(cells),
                      frozenset(functions))


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


def ifdef_findings(relpath: str, text: str, defined: frozenset[str] | set[str] = frozenset(),
                   allow_sim: bool = False) -> list[str]:
    """``sim-ifdef`` and ``external-ifdef`` findings in comment-stripped
    ``text``. A macro is internal when this file or ``defined`` (the rest of
    its RTL closure: `` `define``s and filelist ``+define+``) defines it, so
    an include guard passes; any other macro can only come from a tool or
    vendor flow and would hide code from lint. ``allow_sim`` (the
    vocabulary headers only) permits the ``SYNTHESIS``/``VERILATOR`` guards.

    Lexical limitation, failing closed: the macro name (or the opening
    parenthesis of an expression) must be on the directive's line. A name on
    a later line, ``SYNTHESIS`` included, is reported as an
    ``external-ifdef`` directive without a macro name."""
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
                if not (allow_sim and name in _HEADER_GUARDS):
                    out.append(f"{relpath}:{line}: sim-ifdef: `{directive} {name}")
            elif name not in known:
                out.append(f"{relpath}:{line}: external-ifdef: `{directive} {name} "
                           "is not `define'd by the RTL closure")
    return out


def directive_findings(relpath: str, raw: str, defined: frozenset[str] | set[str] = frozenset(),
                       header: bool = False, conditionals: bool = True) -> list[str]:
    """Findings that matter for files that can be `` `include``d whatever
    their suffix: ``tool-pragma``, ``verilator-config``, and the two
    conditional-compilation rules (``sim-ifdef``, ``external-ifdef``).
    ``header`` marks a vocabulary header (its guards pass ``sim-ifdef``);
    ``conditionals=False`` leaves conditionals to ``conditional-compilation``
    (design ``.sv`` files)."""
    text = strip_comments_and_strings(raw)
    out = ifdef_findings(relpath, text, defined, allow_sim=header) if conditionals else []
    for m in _COMMENT_RE.finditer(raw):
        body = (m.group("line") if m.group("line") is not None else m.group("block")).strip()
        if _PRAGMA_RE.match(body):
            out.append(f"{relpath}:{line_of(raw, m.start())}: tool-pragma: {body[:60]}")
    for m in re.finditer(r"`verilator_config\b", text):
        out.append(f"{relpath}:{line_of(text, m.start())}: verilator-config: {m.group()}")
    return out


_CONCAT_LVALUE = re.compile(r"^\{[^{}]*\}$")
_LVALUE = re.compile(r"^[A-Za-z_][\w$]*(?:\s*(?:\.[A-Za-z_][\w$]*|"
                     r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]))*$")


def find_nonblocking(text: str) -> list[int]:
    """Indices of ``<=`` that are nonblocking assignments, not comparisons,
    in comment-stripped ``text``: ``<=`` at parenthesis/brace depth zero
    whose text back to the previous statement boundary (``;``, ``,``,
    case-label ``:``, ``begin``, ``end``, ``else``, ``)``) is an lvalue or
    a concatenation of lvalues. ``if (a <= b)``, ``i <= n`` in a ``for``
    header, ``assign x = a <= b`` and ternaries are not flagged.

    Provenance: adapted from nand2anything tools/style/rtl_style.py
    ``find_nonblocking`` (commit 085ef4b), adapted with the owner's
    permission."""
    hits = []
    depth = brackets = boundary = 0
    concat_starts: list[int] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "(":
            depth += 1
            boundary = i + 1
        elif c == ")":
            depth = max(depth - 1, 0)
            boundary = i + 1
        elif c == "{":
            if depth == 0:
                concat_starts.append(i)
            depth += 1
        elif c == "}":
            depth = max(depth - 1, 0)
            if depth == 0 and concat_starts:
                boundary = concat_starts.pop()
            elif depth == 0:
                boundary = i + 1
        elif c == "[":
            brackets += 1
        elif c == "]":
            brackets = max(brackets - 1, 0)
        elif c in ";," or (c == ":" and brackets == 0 and depth == 0):
            boundary = i + 1
        elif text.startswith("<=", i):
            if depth == 0 and (i + 2 >= n or text[i + 2] != "="):
                prefix = text[boundary:i].strip()
                prefix = re.sub(r"^(?:begin|end|else)\b", "", prefix).strip()
                if prefix and (_LVALUE.match(prefix) or _CONCAT_LVALUE.match(prefix)):
                    hits.append(i)
            i += 2
            continue
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            if text[i:j] in ("begin", "end", "else", "always_comb", "always_latch", "always",
                             "always_ff", "initial", "final"):
                boundary = j
            i = j
            continue
        i += 1
    return hits


_TOKEN_RE = re.compile(r"`?[A-Za-z_][\w$]*|\S")
_OPEN = {"(": ")", "[": "]", "{": "}"}
_PROCESSES = frozenset({"always", "always_comb", "always_ff", "always_latch", "initial", "final"})
_ROUTINES = {"function": "endfunction", "task": "endtask"}
_CASES = frozenset({"case", "casez", "casex"})


def bare_generate(text: str) -> list[int]:
    """Indices of module-level ``for``, ``if`` and ``case`` keywords (generate
    constructs) outside a ``generate ... endgenerate`` region, in
    comment-stripped ``text``. A token scan, not a parser: bracket groups
    (expressions, port lists, macro arguments) are skipped whole; process
    bodies (``always*``, ``initial``, ``final``) and functions or tasks are
    skipped, so their ``for``/``if``/``case`` statements are procedural. Only
    the outermost construct of a bare region is reported."""
    toks = [(m.group(), m.start()) for m in _TOKEN_RE.finditer(text)]
    n = len(toks)

    def group(k: int) -> int:
        """Index just past the bracket group opening at token ``k``."""
        stack = []
        while k < n:
            t = toks[k][0]
            if t in _OPEN:
                stack.append(_OPEN[t])
            elif stack and t == stack[-1]:
                stack.pop()
                if not stack:
                    return k + 1
            k += 1
        return n

    def until(k: int, opens: frozenset[str] | set[str], close: str) -> int:
        """Index just past the ``close`` that matches the opener at ``k``."""
        depth = 0
        while k < n:
            t = toks[k][0]
            if t in _OPEN:
                k = group(k)
                continue
            if t in opens:
                depth += 1
            elif t == close:
                depth -= 1
                if depth == 0:
                    k += 1
                    if k + 1 < n and toks[k][0] == ":":  # end : label
                        k += 2
                    return k
            k += 1
        return n

    def statement(k: int) -> int:
        """Index just past the statement (or generate item) at token ``k``."""
        while k < n and toks[k][0] in ("@", "#"):  # timing control
            k += 1
            if k < n and toks[k][0] in _OPEN:
                k = group(k)
            elif k < n:
                k += 1
        if k >= n:
            return n
        t = toks[k][0]
        if t in ("unique", "unique0", "priority"):
            return statement(k + 1)
        if t == "begin":
            return until(k, {"begin"}, "end")
        if t in _CASES:
            return until(k, _CASES, "endcase")
        if t in ("if", "for", "while", "repeat", "foreach"):
            k += 1
            if k < n and toks[k][0] == "(":
                k = group(k)
            k = statement(k)
            if t == "if" and k < n and toks[k][0] == "else":
                k = statement(k + 1)
            return k
        while k < n and toks[k][0] != ";":
            k = group(k) if toks[k][0] in _OPEN else k + 1
        return k + 1

    hits: list[int] = []
    module = False
    regions = 0
    k = 0
    while k < n:
        t, pos = toks[k]
        if t in _OPEN:
            k = group(k)
            continue
        if t in ("module", "macromodule"):
            module = True
        elif t == "endmodule":
            module = False
        elif t == "generate":
            regions += 1
        elif t == "endgenerate":
            regions = max(regions - 1, 0)
        elif module and regions == 0:
            if t in _PROCESSES:
                k = statement(k + 1)
                continue
            if t in _ROUTINES:
                k = until(k, {t}, _ROUTINES[t])
                continue
            if t in ("for", "if") or t in _CASES:
                hits.append(pos)
                k = statement(k)
                continue
        k += 1
    return hits


def assert_argument_spans(text: str, vocab: Vocabulary) -> list[tuple[int, int]]:
    """Spans of the parenthesized argument lists of `` `COMMON_ASSERT*``
    calls in comment-stripped ``text``, for function-like macros a
    vocabulary header defines."""
    spans = []
    for m in re.finditer(r"`([A-Za-z_][\w$]*)\s*\(", text):
        if m.group(1).startswith(ASSERT_MACRO_PREFIX) and m.group(1) in vocab.functions:
            start = m.end() - 1
            spans.append((start, _balanced(text, start)))
    return spans


def vocabulary_findings(relpath: str, text: str, vocab: Vocabulary) -> list[str]:
    """The vocabulary rules on comment-stripped ``text``: constructs
    reserved for the vocabulary headers, ``import``, and the include
    rules. Strings are blanked, so `` `include`` paths are read from the
    raw-length text through the string's span."""
    header = relpath in vocab.headers
    out: list[str] = []

    def add(rule: str, index: int, detail: str) -> None:
        out.append(f"{relpath}:{line_of(text, index)}: {rule}: {detail.strip()}")

    if not header:
        for m in re.finditer(r"\balways_ff\b", text):
            add("raw-always-ff", m.start(), m.group())
        for index in find_nonblocking(text):
            add("nonblocking-assignment", index, "<=")
        for m in _ASSERTION_RE.finditer(text):
            add("raw-assertion", m.start(), m.group())
        for m in re.finditer(r"\b(initial|final)\b", text):
            add("raw-initial", m.start(), m.group())
        asserts = assert_argument_spans(text, vocab)
        for start, end in asserts:
            offset, first = _split_top(text, start + 1, max(start + 1, end - 1))[0]
            if not _IDENT.fullmatch(first.strip()):
                add("assert-label", offset + len(first) - len(first.lstrip()),
                    f"{ASSERT_MACRO_PREFIX}* first argument {' '.join(first.split())!r} "
                    "is not a single identifier")
        for m in _SYSTEM_RE.finditer(text):
            if m.group() in SAMPLED_VALUE_FUNCTIONS:
                if not _in(m.start(), asserts):
                    add("sampled-value", m.start(),
                        f"{m.group()} outside a {ASSERT_MACRO_PREFIX}* macro argument")
            elif m.group() not in PURE_SYSTEM_FUNCTIONS and not _ASSERTION_RE.fullmatch(m.group()):
                add("system-task", m.start(), m.group())
        for m in re.finditer(r"`([A-Za-z_][\w$]*)", text):
            if m.group(1) in vocab.macros and m.group(1) not in vocab.functions:
                add("object-macro", m.start(), f"`{m.group(1)} is an object-like vocabulary "
                    "macro (an include guard), not a vocabulary form")
    for m in _CLOCKING_RE.finditer(text):
        add("clocking-block", m.start(), m.group())
    for m in re.finditer(r"\bimport\b", text):
        add("package-import", m.start(), "import (name items as <pkg>::<name>)")
    if not relpath.startswith(f"rtl/{SHARED_DIR}/"):
        for m in re.finditer(r"\b[A-Za-z_]\w*\b", text):
            if m.group() in vocab.cells and text[m.start() - 1:m.start()] != "`":
                add("cdc-cell-macro", m.start(), f"{m.group()} instantiated directly "
                    "(use its vocabulary macro)")
    return out


def include_findings(relpath: str, raw: str, text: str, vocab: Vocabulary) -> list[str]:
    """``include-path`` and ``vocabulary-include``: every `` `include``
    names a vocabulary header by its bare file name, and a file that uses a
    vocabulary macro includes one itself (headers are exempt)."""
    out: list[str] = []
    included = False
    for m in _INCLUDE_RE.finditer(text):
        line = line_of(text, m.start())
        target = raw[m.start(1):m.end(1)] if m.group(1) else ""
        name = target[1:-1]
        if not target.startswith('"') or name not in vocab.names:
            shown = target or "(no file name)"
            out.append(f"{relpath}:{line}: include-path: `include {shown} is not a vocabulary "
                       f"header named by its bare file name"
                       + (f" ({', '.join(sorted(vocab.names))})" if vocab.names else ""))
        else:
            included = True
    if relpath not in vocab.headers and not included and vocab.macros:
        for m in re.finditer(r"`([A-Za-z_][\w$]*)", text):
            if m.group(1) in vocab.macros:
                out.append(f"{relpath}:{line_of(text, m.start())}: vocabulary-include: "
                           f"uses `{m.group(1)} without `include of a vocabulary header")
                break
    return out


def check_text(relpath: str, raw: str, expected_prefix: str | None,
               defined: frozenset[str] | set[str] = frozenset(),
               vocab: Vocabulary = Vocabulary()) -> list[str]:
    """Apply every rule to one file; ``expected_prefix`` is the name prefix
    a unit in this file must start with (None: no prefix declared);
    ``defined`` holds the macros the rest of its RTL closure defines;
    ``vocab`` describes the vocabulary headers (``relpath`` is relative to
    the source root, so a header is recognized by its path)."""
    text = strip_comments_and_strings(raw)
    path = Path(relpath)
    out: list[str] = []

    def add(rule: str, index: int, detail: str) -> None:
        out.append(f"{relpath}:{line_of(text, index)}: {rule}: {detail.strip()}")

    header = relpath in vocab.headers
    design = path.suffix == ".sv" and not header
    out += directive_findings(relpath, raw, defined, header=header, conditionals=not design)
    if design:
        for m in re.finditer(r"`(ifdef|ifndef|elsif|else)\b", text):
            add("conditional-compilation", m.start(), m.group())
        for m in re.finditer(r"`(define|undef|undefineall)\b", text):
            add("local-define", m.start(), m.group())
    out += vocabulary_findings(relpath, text, vocab)
    out += include_findings(relpath, raw, text, vocab)

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
    for m in re.finditer(r"\bfor\s*\(\s*genvar\b", body):
        add("inline-genvar", m.start(), "for (genvar ...): declare the genvar at module level")
    for index in bare_generate(body):
        add("bare-generate", index, f"module-level {_IDENT.match(body, index).group()} "
            "outside generate ... endgenerate")
    packages = _package_spans(body)
    for m in re.finditer(r"\b(task|function)\b", body):
        if m.group(1) == "task" or not _in(m.start(), packages):
            add("task-function", m.start(), m.group())
    for m in re.finditer(r"\b(typedef|enum|struct)\b", body):
        if not _in(m.start(), packages):
            add("typedef-outside-package", m.start(), m.group())

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
    vocab = vocabulary(src_root)
    for f in files:
        exp = expected_prefix(f, src_root, ips, prefix)
        result.findings += check_text(rel(f, src_root), f.read_text(errors="replace"), exp,
                                      _defined_for(f, closures, by_fpga), vocab)
    for f in extra:
        text = _text(f)
        if text is not None:  # binary files cannot carry directives
            result.findings += directive_findings(rel(f, src_root), text,
                                                  _defined_for(f, closures, by_fpga),
                                                  header=rel(f, src_root) in vocab.headers)
    return result
