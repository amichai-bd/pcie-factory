"""Struct-typed parameters selected in constant expressions.

The style rule ``struct-param-select`` (owner: the RTL BKM, "Struct
localparams"; the FPGA BKM, "Known Quartus limits"). Quartus Prime Lite
rejects a member select of a struct-typed localparam or parameter inside a
constant expression (``Error (10742)``, constant expression cannot contain a
hierarchical identifier; then ``Error (10734)``, not a constant), although
Verilator accepts it.

A lexical scan on comment- and string-stripped text, in two steps:

1. ``collect`` builds a small symbol table from every RTL file: the struct
   types each package declares (``typedef struct ... } t_x;`` and plain
   aliases of them, ``typedef t_x t_y;``), then the package-level
   localparams and parameters declared with one of those types.
2. ``struct_param_selects`` finds, in one file, each member select
   ``NAME.member`` (or ``pkg::NAME.member``) of a struct-typed parameter that
   sits in a constant context: a localparam or parameter initialiser, a
   parameter value of an instance (``#(.P(...))``), a
   `` `COMMON_ASSERT_PARAM`` argument, the header of a module-level
   ``if``/``for``/``case`` or a generate ``case`` item label (a generate
   construct), a range (a ``[...]`` with more top-level ``:`` than ``?``, so
   ``+:``, ``-:`` and ``a:b`` but not a ``c ? x : y`` index), an unpacked
   dimension or size of a declaration (``logic x [N]``), a replication count
   (``{N{...}}``) or a size cast (``(N)'(...)``, or a select written
   directly before ``'(``).

A bare ``NAME`` resolves to the enclosing package's parameter, or to a
module's own struct-typed parameter (declared with a ``pkg::type``); inside a
function, a name the function declares (an argument or a local) shadows it.
A member select in ordinary logic is out of scope: Quartus accepts it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Vocabulary macros whose arguments are constant expressions (a generate
# condition under SYNTHESIS).
PARAM_MACROS = ("COMMON_ASSERT_PARAM",)

_ID = r"[A-Za-z_][\w$]*"
_PACKAGE_RE = re.compile(rf"\bpackage\s+(?:(?:automatic|static)\s+)?({_ID})")
_MODULE_RE = re.compile(rf"\b(?:module|macromodule)\s+(?:(?:automatic|static)\s+)?({_ID})")
_TYPEDEF_RE = re.compile(r"\btypedef\b")
_STRUCT_RE = re.compile(r"\btypedef\s+struct\b[^{;]*\{")
_ALIAS_RE = re.compile(rf"\btypedef\s+((?:{_ID}\s*::\s*)?{_ID})\s+({_ID})\s*;")
_DECL_RE = re.compile(r"\b(localparam|parameter)\b")
_TYPE_RE = re.compile(rf"^(?:({_ID})\s*::\s*)?({_ID})$")
_SELECT_RE = re.compile(rf"(?<![\w$.`'])(?:({_ID})\s*::\s*)?({_ID})\s*\.\s*({_ID})(?![\w$])(?!\s*\()")
_FUNCTION_RE = re.compile(r"\bfunction\b")
_NOT_TYPES = frozenset({
    "return", "else", "begin", "end", "if", "case", "casez", "casex", "for", "while",
    "repeat", "foreach", "do", "assign", "unique", "unique0", "priority", "default",
})
_LOCAL_RE = re.compile(
    rf"(?:^|(?<=[;\n])|(?<=\bbegin))\s*(?:(?:automatic|static|const|var)\s+)*"
    rf"((?:{_ID}\s*::\s*)?{_ID})(?:\s+(?:signed|unsigned))?(?:\s*\[[^\]]*\])*\s+"
    rf"({_ID})\s*(?:\[[^\]]*\]\s*)*(?=[=;,])")


@dataclass(frozen=True)
class StructParams:
    """``types``: struct types, ``pkg::t``; ``others``: every other type a
    package declares; ``params``: package-level parameters that are struct
    typed or whose type cannot be resolved, ``pkg::NAME``; ``unresolved``:
    those of ``params`` whose type cannot be resolved (the rule fails closed
    on them); ``packages``: the packages seen (a ``pkg::NAME`` of any other
    package fails closed too)."""
    types: frozenset[str] = frozenset()
    params: frozenset[str] = frozenset()
    others: frozenset[str] = frozenset()
    unresolved: frozenset[str] = frozenset()
    packages: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Decl:
    name: str
    type: str  # the declared type text, "" when none
    init: tuple[int, int] | None  # span of the initialiser


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


def _units(text: str, rx: re.Pattern, end_word: str) -> list[tuple[str, int, int]]:
    out = []
    for m in rx.finditer(text):
        end = re.compile(rf"\b{end_word}\b").search(text, m.end())
        out.append((m.group(1), m.start(), end.start() if end else len(text)))
    return out


def packages(text: str) -> list[tuple[str, int, int]]:
    """(name, start, end) of each ``package ... endpackage``."""
    return _units(text, _PACKAGE_RE, "endpackage")


def modules(text: str) -> list[tuple[str, int, int]]:
    """(name, start, end) of each ``module ... endmodule``."""
    return _units(text, _MODULE_RE, "endmodule")


def _top_equals(piece: str) -> int:
    """Index of the first depth-0 ``=`` that is an assignment, or -1."""
    depth = 0
    for i, c in enumerate(piece):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif (c == "=" and depth == 0 and piece[i + 1:i + 2] != "="
              and piece[i - 1:i] not in ("=", "!", "<", ">")):
            return i
    return -1


def declarations(text: str, start: int = 0, end: int | None = None) -> list[Decl]:
    """Every localparam and parameter declared in ``text[start:end]``: a
    statement up to its ``;`` (or the ``)`` closing a parameter port list),
    split at depth-0 commas; a declarator without a type takes the previous
    one's (``localparam t_x A = 1, B = 2;``). Type parameters are skipped."""
    end = len(text) if end is None else end
    out: list[Decl] = []
    for m in _DECL_RE.finditer(text, start, end):
        depth, j = 0, m.end()
        while j < end:
            c = text[j]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c == ";" and depth == 0:
                break
            j += 1
        prev = ""
        depth, s = 0, m.end()
        pieces = []
        for i in range(m.end(), j + 1):
            c = text[i] if i < j else ","
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == "," and depth == 0:
                pieces.append((s, text[s:i]))
                s = i + 1
        for off, piece in pieces:
            if _DECL_RE.match(piece.strip()) and off != m.end():
                break  # the next keyword's own statement
            eq = _top_equals(piece)
            head = piece[:eq] if eq >= 0 else piece
            head = re.sub(r"^\s*(?:localparam|parameter)\b", "", head)
            head = re.sub(r"(?:\s*\[[^\[\]]*\])+\s*$", "", head).strip()
            if head.startswith("type") and re.match(r"type\b", head):
                prev = ""
                continue
            n = re.search(rf"({_ID})$", head)
            if not n:
                continue
            typ = re.sub(r"\s+", " ", head[:n.start()]).strip() or prev
            prev = typ
            init = (off + eq + 1, off + len(piece)) if eq >= 0 else None
            out.append(Decl(n.group(1), typ, init))
    return out


_BUILTIN_RE = re.compile(r"(?:logic|bit|reg|int|integer|byte|shortint|longint|real|realtime|"
                         r"shortreal|string|time|signed|unsigned)\b|\[")


def kind(typ: str, package: str | None, types: frozenset[str] | set[str],
         others: frozenset[str] | set[str]) -> str | None:
    """``"struct"`` when ``typ`` names a struct type, ``"unresolved"`` when
    it names no type the table knows (or is absent: an untyped parameter),
    else None (a built-in type, a packed range, a known non-struct type or
    an array of a named type)."""
    typ = typ.strip()
    if not typ:
        return "unresolved"
    if _BUILTIN_RE.match(typ) or "[" in typ:
        return None
    t = _TYPE_RE.match(re.sub(r"\s+", "", typ))
    if not t:
        return "unresolved"
    q = f"{t.group(1)}::{t.group(2)}" if t.group(1) else (
        f"{package}::{t.group(2)}" if package else t.group(2))
    if q in types:
        return "struct"
    return None if q in others else "unresolved"


def collect(texts: list[str]) -> StructParams:
    """The symbol table from comment- and string-stripped RTL ``texts``."""
    types: set[str] = set()
    declared: set[str] = set()
    aliases: dict[str, str] = {}
    pkgs = [(text, p) for text in texts for p in packages(text)]
    for text, (name, a, b) in pkgs:
        for m in _TYPEDEF_RE.finditer(text, a, b):
            semi, depth, k = -1, 0, m.end()
            while k < b:
                if text[k] in "([{":
                    depth += 1
                elif text[k] in ")]}":
                    depth -= 1
                elif text[k] == ";" and depth == 0:
                    semi = k
                    break
                k += 1
            n = re.search(rf"({_ID})\s*(?:\[[^\]]*\]\s*)*$", text[m.end():semi]) if semi > 0 else None
            if n:
                declared.add(f"{name}::{n.group(1)}")
        for m in _STRUCT_RE.finditer(text, a, b):
            close = _balanced(text, m.end() - 1)
            n = re.match(rf"\s*({_ID})\s*;", text[close:b])
            if n:
                types.add(f"{name}::{n.group(1)}")
        for m in _ALIAS_RE.finditer(text, a, b):
            src = re.sub(r"\s+", "", m.group(1))
            aliases[f"{name}::{m.group(2)}"] = src if "::" in src else f"{name}::{src}"
    changed = True
    while changed:
        changed = False
        for alias, src in aliases.items():
            if src in types and alias not in types:
                types.add(alias)
                changed = True
    others = declared - types
    params, unresolved = set(), set()
    for text, (name, a, b) in pkgs:
        for d in declarations(text, a, b):
            k = kind(d.type, name, types, others)
            if k:
                params.add(f"{name}::{d.name}")
            if k == "unresolved":
                unresolved.add(f"{name}::{d.name}")
    return StructParams(frozenset(types), frozenset(params), frozenset(others),
                        frozenset(unresolved), frozenset(p[0] for _, p in pkgs))


# --- constant contexts --------------------------------------------------------

_TOKEN_RE = re.compile(r"`?[A-Za-z_][\w$]*|\S")
_OPEN = {"(": ")", "[": "]", "{": "}"}
_PROCESSES = frozenset({"always", "always_comb", "always_ff", "always_latch", "initial", "final"})
_ROUTINES = {"function": "endfunction", "task": "endtask"}
_CASES = frozenset({"case", "casez", "casex"})


def generate_headers(text: str) -> list[tuple[int, int, str]]:
    """(start, end, context) of the parenthesized header of every
    module-level ``if``, ``for`` and ``case`` (a generate construct, inside a
    ``generate`` region or not, at any nesting depth), and of each generate
    ``case`` item label (``default`` excepted), skipping process bodies and
    routines."""
    toks = [(m.group(), m.start()) for m in _TOKEN_RE.finditer(text)]
    n = len(toks)

    def group(k: int) -> int:
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

    def until(k: int, opens: set[str] | frozenset[str], close: str) -> int:
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
                    if k + 1 < n and toks[k][0] == ":" and toks[k + 1][0] != ":":
                        k += 2  # end : label
                    return k
            k += 1
        return n

    def labels(j: int) -> None:
        """Record the item labels of the generate case whose body starts at
        token ``j``."""
        while j < n and toks[j][0] != "endcase":
            start = j
            while j < n:
                t = toks[j][0]
                if t in _OPEN:
                    j = group(j)
                    continue
                if t == "endcase":
                    return
                if t == ":":
                    if j + 1 < n and toks[j + 1][0] == ":" and toks[j + 1][1] == toks[j][1] + 1:
                        j += 2  # pkg::NAME
                        continue
                    break
                j += 1
            if j >= n:
                return
            if toks[start][0] != "default":
                spans.append((toks[start][1], toks[j][1], "generate case label"))
            j += 1
            if j < n and toks[j][0] == "begin":
                j = until(j, {"begin"}, "end")
            else:
                j = statement(j)

    def statement(k: int) -> int:
        while k < n and toks[k][0] in ("@", "#"):
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

    spans = []
    module = False
    k = 0
    while k < n:
        t = toks[k][0]
        if t in _OPEN:
            k = group(k)
            continue
        if t in ("module", "macromodule"):
            module = True
        elif t == "endmodule":
            module = False
        elif module and t in _PROCESSES:
            k = statement(k + 1)
            continue
        elif module and t in _ROUTINES:
            k = until(k, {t}, _ROUTINES[t])
            continue
        elif module and (t in ("if", "for") or t in _CASES):
            if k + 1 < n and toks[k + 1][0] == "(":
                e = group(k + 1)
                spans.append((toks[k + 1][1], toks[e - 1][1] + 1, "generate condition"))
                if t in _CASES:
                    labels(e)
                k = e
                continue
        k += 1
    return spans


def _is_range(group: str) -> bool:
    """Whether the bracket group ``[...]`` is a range: more depth-1 ``:``
    (``::`` excepted) than ``?``, so ``a:b``, ``+:`` and ``-:`` count and
    a conditional index ``c ? x : y`` does not."""
    group = group.replace("::", "  ")
    depth, colons, questions = 0, 0, 0
    for c in group:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 1 and c == ":":
            colons += 1
        elif depth == 1 and c == "?":
            questions += 1
    return colons > questions


# Words that can stand before a name and its "[" without declaring it.
_NOT_DECL = _NOT_TYPES | frozenset({
    "force", "release", "deassign", "wait", "disable", "posedge", "negedge", "or", "and",
    "not", "inside", "dist", "with", "iff", "generate", "endgenerate", "endcase", "module",
    "package", "endmodule", "endpackage", "endfunction", "function", "task", "typedef",
    "genvar", "automatic", "static",
})
_DIM_RE = re.compile(rf"(?<![\w$.`'])({_ID}(?:\s*::\s*{_ID})?)(?:\s*\[[^\[\]]*\])*\s+{_ID}\s*(?=[\[,=;])")


def unpacked_dimensions(text: str) -> list[tuple[int, int, str]]:
    """Dimensions of every declarator, including comma continuations.

    Initialisers can contain indexing and nested comma expressions; skip
    them without treating their brackets as declaration dimensions.
    """
    spans = []
    for m in _DIM_RE.finditer(text):
        if m.group(1).split(":")[0].strip() in _NOT_DECL:
            continue
        k = m.end()
        while k < len(text):
            if text[k].isspace():
                k += 1
            elif text[k] == "[":
                end = _balanced(text, k)
                spans.append((k, end, "dimension"))
                k = end
            elif text[k] == "=":
                k += 1
                while k < len(text) and text[k] not in ",;":
                    k = _balanced(text, k) if text[k] in "([{" else k + 1
            elif text[k] == ",":
                name = re.match(rf"\s*{_ID}\s*", text[k + 1:])
                if not name:
                    break
                k += 1 + name.end()
            else:
                break
    return spans


def constant_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, context) of every constant context in ``text``."""
    spans = [(d.init[0], d.init[1], "initialiser") for d in declarations(text) if d.init]
    for m in re.finditer(rf"`({_ID})\s*\(", text):
        if m.group(1) in PARAM_MACROS:
            spans.append((m.end() - 1, _balanced(text, m.end() - 1), m.group(1)))
    for m in re.finditer(r"#\s*\(", text):
        spans.append((m.end() - 1, _balanced(text, m.end() - 1), "parameter value"))
    spans += generate_headers(text)
    for m in re.finditer(r"\[", text):
        end = _balanced(text, m.start())
        if _is_range(text[m.start():end]):
            spans.append((m.start(), end, "range"))
    spans += unpacked_dimensions(text)
    for m in re.finditer(r"(?<!')\{", text):
        end = _balanced(text, m.start())
        inner = re.match(r"\{([^,{}]+)\{", text[m.start():end])
        if inner and inner.group(1).strip():
            spans.append((m.start() + 1, m.start() + 1 + len(inner.group(1)), "replication"))
    for m in re.finditer(r"\)\s*'\s*\(", text):
        depth = 0
        for i in range(m.start(), -1, -1):
            if text[i] == ")":
                depth += 1
            elif text[i] == "(":
                depth -= 1
                if depth == 0:
                    spans.append((i, m.start() + 1, "size cast"))
                    break
    return spans


def _function_scopes(text: str) -> list[tuple[int, int, set[str]]]:
    """(start, end, declared names) of each function: its arguments and the
    names its body declares."""
    out = []
    for m in _FUNCTION_RE.finditer(text):
        end = re.compile(r"\bendfunction\b").search(text, m.end())
        stop = end.start() if end else len(text)
        names: set[str] = set()
        semi = text.find(";", m.end(), stop)
        header = text[m.end():semi if semi >= 0 else stop]
        paren = header.find("(")
        if paren >= 0:
            args = header[paren + 1:_balanced(header, paren) - 1]
            for piece in re.split(r",(?![^()\[\]{}]*[)\]}])", args):
                piece = piece.split("=")[0]
                piece = re.sub(r"(?:\s*\[[^\[\]]*\])+\s*$", "", piece).strip()
                n = re.search(rf"({_ID})$", piece)
                if n:
                    names.add(n.group(1))
        body = text[semi + 1 if semi >= 0 else stop:stop]
        for d in _LOCAL_RE.finditer(body):
            if d.group(1) not in _NOT_TYPES:
                names.add(d.group(2))
        out.append((m.start(), stop, names))
    return out


def struct_param_selects(text: str, table: StructParams) -> list[tuple[int, str, str]]:
    """(index, select, context) for each member select of a struct-typed
    parameter inside a constant context of comment-stripped ``text``."""
    pkgs = packages(text)
    local: dict[str, str] = {}
    for _, a, b in modules(text):
        for d in declarations(text, a, b):
            k = kind(d.type, None, table.types, table.others)
            if k:
                local[d.name] = k
    spans = constant_spans(text)
    scopes = _function_scopes(text)
    out = []
    for m in _SELECT_RE.finditer(text):
        pkg, name = m.group(1), m.group(2)
        if pkg:
            q = f"{pkg}::{name}"
            unknown = pkg not in table.packages
            hit, closed = q in table.params or unknown, q in table.unresolved or unknown
        else:
            if any(a <= m.start() < b and name in names for a, b, names in scopes):
                continue  # a function argument or local
            owner = next((p for p, a, b in pkgs if a <= m.start() < b), None)
            if owner:
                q = f"{owner}::{name}"
                hit, closed = q in table.params, q in table.unresolved
            else:
                hit, closed = name in local, local.get(name) == "unresolved"
        if not hit:
            continue
        ctx = next((c for a, b, c in spans if a <= m.start() < b), None)
        if not ctx and re.match(r"\s*'\s*\(", text[m.end():]):
            ctx = "size cast"
        if ctx:
            ctx += "; its type is not resolved, so the rule fails closed" if closed else ""
            out.append((m.start(), re.sub(r"\s+", "", m.group()), ctx))
    return out
