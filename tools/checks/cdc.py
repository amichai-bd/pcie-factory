"""Structural clock-domain-crossing (CDC) check on Verilator's JSON netlist.

For each top-level IP (and, without ``--ip``, each shared block) Verilator
writes its elaborated netlist with ``--json-only``. The netlist is flattened
per instance into a variable-level graph of flops and combinational
assignments. A flop's domain is its clock, followed through plain wires and
ports up to a clock input of the analyzed top. A flop whose data input
depends, directly or through combinational logic, on a flop of another
domain is a finding, unless the path crosses an approved crossing cell: a
``common_cdc_*`` module in ``rtl/common/`` with a valid ``cdc-cell``
directive, which the check treats as a black box whose ``src_*`` ports
belong to its source clock and ``dst_*`` ports to its destination clock.
An input port of the analyzed top that carries a ``cdc-domain`` annotation
is a source in the domain of the clock input it names.
The SPEC (wiki/tools/checks/SPEC.md, "CDC check") owns the rule, its
assumptions and its limits.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from build import sim
from build.filelist import FilelistError, Resolved, resolve
from build.ip import REPO_ROOT, SHARED_DIR, Ip, block_name, shared_blocks
from checks.common import COMMON_PREFIX, CheckResult, read_prefix, rel
from checks.lint import declared_modules

CDC_TIMEOUT_S = 600.0
CO_SOURCES = 4   # other sources of the same domain listed in one finding
CELL_PREFIX = COMMON_PREFIX + "cdc_"
ASYNC = "async"
SIDES = ("src", "dst")

# A comment line that starts with ``cdc-cell`` is a directive; it must then
# match the grammar exactly.
_DIRECTIVE_LINE_RE = re.compile(r"^\s*//\s*cdc-cell\b")
_DIRECTIVE_RE = re.compile(r"^\s*//\s*cdc-cell:\s*src=(?P<src>[A-Za-z_]\w*)"
                           r"\s+dst=(?P<dst>[A-Za-z_]\w*)\s*$")
_MODULE_LINE_RE = re.compile(r"^\s*module\s+(?:(?:automatic|static)\s+)?(?P<name>\w+)")
# A port-domain annotation: the trailing comment of an input port declaration.
_DOMAIN_LINE_RE = re.compile(r"//\s*cdc-domain\b")
_DOMAIN_RE = re.compile(r"^//\s*cdc-domain:\s*(?P<clock>[A-Za-z_]\w*)\s*$")
_INPUT_LINE_RE = re.compile(r"^\s*input\b")

# Edge kinds, ordered: a path's kind is the largest kind on it.
ALIAS, WIRE, LOGIC = 0, 1, 2
# Node types that only route bits (no logic), given constant indices.
_PURE_TYPES = {"VARREF", "CONST", "CONCAT", "EXTEND", "EXTENDS", "REPLICATE", "SEL",
               "ARRAYSEL", "STRUCTSEL", "WORDSEL"}
_ASSIGN_TYPES = {"ASSIGN", "ASSIGNDLY", "ASSIGNW", "ASSIGNFORCE"}
# Children that hold statements; every other child of a control statement
# (IF, CASE, CASEITEM, WHILE, ...) is a condition.
_STMT_KEYS = {"stmtsp", "thensp", "elsesp", "itemsp", "bodysp", "incsp", "precondsp"}
_CLOCK_EDGES = {"POS", "NEG", "BOTH"}
_CONST_VAR_TYPES = {"GPARAM", "LPARAM", "GENVAR"}


# --------------------------------------------------------------------------
# cdc-cell directives (source text)


@dataclass(frozen=True)
class Directive:
    module: str
    src: str   # a clock port name, or ASYNC
    dst: str
    file: Path  # resolved
    line: int


def scan_directives(files: list[Path], src_root: Path
                    ) -> tuple[dict[tuple[Path, str], Directive], list[str]]:
    """Read every ``cdc-cell`` directive in ``files``. Returns the valid
    directives keyed by (resolved file, module name) and the findings:
    malformed or misplaced directives, directives on modules that are not
    crossing cells, and crossing cells with no directive."""
    found: dict[tuple[Path, str], Directive] = {}
    findings: list[str] = []
    common = (Path(src_root) / "rtl" / SHARED_DIR).resolve()
    for path in files:
        path = Path(path).resolve()
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        where = rel(path, src_root)
        in_common = path.parent == common
        directive_lines: set[int] = set()
        for i, line in enumerate(lines):
            if not _DIRECTIVE_LINE_RE.match(line):
                continue
            n = i + 1
            m = _DIRECTIVE_RE.match(line)
            if m is None:
                findings.append(f"{where}:{n}: malformed cdc-cell directive (expected "
                                "'// cdc-cell: src=<clock port|async> dst=<clock port>')")
                continue
            mod = _MODULE_LINE_RE.match(lines[i + 1]) if i + 1 < len(lines) else None
            if mod is None:
                findings.append(f"{where}:{n}: cdc-cell directive is not on the line "
                                "directly above a module line")
                continue
            name = mod.group("name")
            if not (in_common and name.startswith(CELL_PREFIX)):
                findings.append(f"{where}:{n}: cdc-cell directive on {name}: only "
                                f"{CELL_PREFIX}* modules in rtl/{SHARED_DIR}/ carry one")
                continue
            if m.group("src") == m.group("dst"):
                findings.append(f"{where}:{n}: malformed cdc-cell directive on {name}: "
                                "src and dst name the same port")
                continue
            directive_lines.add(i + 1)
            found[(path, name)] = Directive(name, m.group("src"), m.group("dst"), path, n)
        if not in_common:
            continue
        for i, line in enumerate(lines):
            mod = _MODULE_LINE_RE.match(line)
            if (mod and mod.group("name").startswith(CELL_PREFIX)
                    and i not in directive_lines
                    and not (i > 0 and _DIRECTIVE_LINE_RE.match(lines[i - 1]))):
                findings.append(f"{where}:{i + 1}: crossing cell {mod.group('name')} has no "
                                "cdc-cell directive on the line above its module line")
    return found, findings


@dataclass(frozen=True)
class PortDomain:
    clock: str   # a clock input port of the same module
    file: Path   # resolved
    line: int


def scan_port_domains(files: list[Path], src_root: Path
                      ) -> tuple[dict[tuple[Path, int], PortDomain], list[str]]:
    """Read every ``cdc-domain`` annotation in ``files``. Returns the
    well-formed ones keyed by (resolved file, line) and the findings for
    malformed or misplaced ones."""
    found: dict[tuple[Path, int], PortDomain] = {}
    findings: list[str] = []
    for path in files:
        path = Path(path).resolve()
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        where = rel(path, src_root)
        for i, line in enumerate(lines):
            hit = _DOMAIN_LINE_RE.search(line)
            if hit is None:
                continue
            n = i + 1
            m = _DOMAIN_RE.match(line[hit.start():])
            if m is None:
                findings.append(f"{where}:{n}: malformed cdc-domain annotation (expected "
                                "'// cdc-domain: <clock input port>' ending the line)")
            elif not _INPUT_LINE_RE.match(line):
                findings.append(f"{where}:{n}: cdc-domain annotation is not on an input port "
                                "declaration line")
            else:
                found[(path, n)] = PortDomain(m.group("clock"), path, n)
    return found, findings


# --------------------------------------------------------------------------
# Netlist model (one module)


def _children(node: dict, keys: set[str] | None = None, skip: set[str] | None = None):
    for k, v in node.items():
        if (keys is not None and k not in keys) or (skip is not None and k in skip):
            continue
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict) and "type" in x:
                    yield x
        elif isinstance(v, dict) and "type" in v:
            yield v


def _walk(node: dict):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(_children(n))


def _pure(node: dict | None) -> bool:
    """True when ``node`` only routes bits: references, constants, constant
    selects, concatenations and extensions."""
    if node is None:
        return True
    t = node["type"]
    if t not in _PURE_TYPES:
        return False
    if t in ("SEL", "WORDSEL"):
        return _pure(_one(node, "fromp")) and all(
            c["type"] == "CONST" for c in _children(node, skip={"fromp"}))
    if t == "ARRAYSEL":
        return _pure(_one(node, "fromp")) and all(
            c["type"] == "CONST" for c in _children(node, skip={"fromp"}))
    if t == "REPLICATE":
        return _pure(_one(node, "srcp")) and all(
            c["type"] == "CONST" for c in _children(node, skip={"srcp"}))
    return all(_pure(c) for c in _children(node))


def _one(node: dict, key: str) -> dict | None:
    v = node.get(key)
    if isinstance(v, list):
        return v[0] if v and isinstance(v[0], dict) else None
    return v if isinstance(v, dict) else None


@dataclass
class VarInfo:
    name: str          # scoped within the module (generate blocks)
    loc: str
    direction: str | None  # INPUT/OUTPUT/INOUT for ports, else None
    memory: bool = False   # an unpacked array


@dataclass
class Flop:
    target: str
    clock: str | None   # var addr of the clock, None when it cannot be told
    deps: dict[str, int]
    loc: str
    error: str = ""


@dataclass
class Pin:
    port: str           # var addr in the instantiated module
    reads: dict[str, int]
    writes: dict[str, int]
    bare: str | None    # var addr when the expression is one whole variable


@dataclass
class CellRef:
    name: str
    modp: str
    loc: str
    pins: list[Pin]


@dataclass
class Module:
    node: dict
    vars: dict[str, VarInfo] = field(default_factory=dict)
    comb: list[tuple[str, str, int]] = field(default_factory=list)  # (target, dep, kind)
    flops: list[Flop] = field(default_factory=list)
    cells: list[CellRef] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


class Netlist:
    """Verilator's ``--json-only`` output (tree and meta)."""

    def __init__(self, tree: dict, meta: dict, src_root: Path):
        self.src_root = Path(src_root)
        self.files = {k: v.get("realpath") or v.get("filename")
                      for k, v in (meta.get("files") or {}).items()}
        self.modules = {m["addr"]: m for m in tree.get("modulesp", [])}
        self.funcs: dict[str, dict] = {}
        for m in self.modules.values():
            for n in _walk(m):
                if n["type"] in ("FUNC", "TASK"):
                    self.funcs[n["addr"]] = n
        tops = [m for m in tree.get("modulesp", [])
                if m["type"] == "MODULE" and m.get("level") == 1]
        self.top = tops[0] if tops else None
        self._models: dict[str, Module] = {}
        self.unpacked = {n["addr"] for n in _walk(tree) if n["type"] == "UNPACKARRAYDTYPE"}

    def file_of(self, loc: str) -> Path | None:
        f = self.files.get(loc.split(",", 1)[0]) if loc else None
        return Path(f).resolve() if f and not f.startswith("<") else None

    def where(self, loc: str) -> str:
        """``file:line`` relative to the source root, from a node's loc."""
        path = self.file_of(loc)
        return f"{rel(path, self.src_root)}:{_line(loc)}" if path else "?"

    def model(self, addr: str) -> Module:
        if addr not in self._models:
            self._models[addr] = _build_model(self, self.modules[addr])
        return self._models[addr]


def _line(loc: str) -> int:
    try:
        return int(loc.split(",")[1].split(":")[0])
    except (IndexError, ValueError):
        return 0


def _build_model(nl: Netlist, mod: dict) -> Module:
    m = Module(mod)
    func_reads: dict[str, set[str]] = {}

    def register(items, scope: str) -> None:
        for n in items:
            t = n["type"]
            if t == "VAR":
                if n.get("varType") in _CONST_VAR_TYPES or n.get("isParam"):
                    continue
                d = n.get("direction")
                m.vars[n["addr"]] = VarInfo(scope + n["name"], n.get("loc", ""),
                                            d if d in ("INPUT", "OUTPUT", "INOUT") else None,
                                            n.get("dtypep") in nl.unpacked)
            elif t in ("FUNC", "TASK"):
                continue  # locals of a function are not module state
            elif t == "CELL":
                continue
            elif t in ("GENBLOCK", "BEGIN"):
                sub = scope + n["name"] + "." if n.get("name") else scope
                register(_children(n), sub)
            else:
                register(_children(n), scope)

    register(_children(mod), "")

    def reads(node: dict | None, out: dict[str, int], kind: int) -> None:
        if node is None:
            return
        for n in _walk(node):
            t = n["type"]
            if t == "VARREF" and n.get("access") in ("RD", "RW"):
                if n["varp"] in m.vars:
                    out[n["varp"]] = min(out.get(n["varp"], LOGIC), kind)
            elif t in ("VARXREF",):
                m.unsupported.append(f"hierarchical reference {n.get('name')} at "
                                     f"{nl.where(n.get('loc', ''))}")
            elif t in ("FUNCREF", "TASKREF"):
                for v in body_reads(n.get("taskp", "")):
                    out[v] = LOGIC

    def body_reads(faddr: str) -> set[str]:
        if faddr in func_reads:
            return func_reads[faddr]
        func_reads[faddr] = set()  # recursion guard
        f = nl.funcs.get(faddr)
        acc: dict[str, int] = {}
        if f is not None:
            for c in _children(f):
                reads(c, acc, LOGIC)
        func_reads[faddr] = set(acc)
        return func_reads[faddr]

    def writes(node: dict | None) -> tuple[list[str], dict[str, int]]:
        """Targets written by an lvalue, and the variables it reads (indices)."""
        tg: list[str] = []
        rd: dict[str, int] = {}
        if node is None:
            return tg, rd
        for n in _walk(node):
            if n["type"] == "VARREF":
                if n.get("access") in ("WR", "RW") and n["varp"] in m.vars:
                    tg.append(n["varp"])
                if n.get("access") in ("RD", "RW") and n["varp"] in m.vars:
                    rd[n["varp"]] = LOGIC
            elif n["type"] == "VARXREF":
                m.unsupported.append(f"hierarchical reference {n.get('name')} at "
                                     f"{nl.where(n.get('loc', ''))}")
        return tg, rd

    def assignment(n: dict, conds: dict[str, int], clocked: bool):
        rhs, lhs = _one(n, "rhsp"), _one(n, "lhsp")
        tg, idx = writes(lhs)
        if rhs is not None and rhs["type"] == "VARREF" and lhs is not None \
                and lhs["type"] == "VARREF":
            kind = ALIAS
        elif _pure(rhs) and _pure(lhs):
            kind = WIRE
        else:
            kind = LOGIC
        if conds and not clocked:
            kind = LOGIC  # a mux
        deps: dict[str, int] = {}
        reads(rhs, deps, kind)
        for v, k in list(idx.items()) + list(conds.items()):
            deps[v] = min(deps.get(v, LOGIC), k)
        return tg, deps

    def statements(items, conds: dict[str, int], clocked: bool, flop_out, comb_out) -> None:
        for n in items:
            t = n["type"]
            if t in _ASSIGN_TYPES:
                tg, deps = assignment(n, conds, clocked)
                sink = flop_out if (clocked and t == "ASSIGNDLY") else comb_out
                for v in tg:
                    acc = sink.setdefault(v, {})
                    for d, k in deps.items():
                        acc[d] = min(acc.get(d, LOGIC), k)
            elif t == "BEGIN":
                statements(_children(n, _STMT_KEYS), conds, clocked, flop_out, comb_out)
            else:
                c = dict(conds)
                for ch in _children(n, skip=_STMT_KEYS):
                    reads(ch, c, LOGIC)
                if t == "LOOP":  # its exit tests sit among its statements
                    for ch in _children(n, _STMT_KEYS):
                        if ch["type"] == "LOOPTEST":
                            for g in _children(ch):
                                reads(g, c, LOGIC)
                statements(_children(n, _STMT_KEYS), c, clocked, flop_out, comb_out)

    def always(n: dict) -> None:
        sens: list[str] = []
        for st in _children(n, {"sentreep"}):
            for item in _children(st, {"sensesp"}):
                if item.get("edgeType") in _CLOCK_EDGES:
                    ref = _one(item, "sensp")
                    if ref is not None and ref["type"] == "VARREF" and ref["varp"] in m.vars:
                        sens.append(ref["varp"])
                    else:
                        sens.append("")
        flop_out: dict[str, dict[str, int]] = {}
        comb_out: dict[str, dict[str, int]] = {}
        clocked = bool(sens)
        statements(_children(n, {"stmtsp"}), {}, clocked, flop_out, comb_out)
        for tg, deps in comb_out.items():
            for d, k in deps.items():
                if d != tg:
                    m.comb.append((tg, d, k if not clocked else LOGIC))
        if not clocked:
            return
        clock, error = None, ""
        if "" in sens:
            error = "its sensitivity list holds an expression, not a signal"
        elif len(sens) == 1:
            clock = sens[0]
        else:
            body: dict[str, int] = {}
            for ch in _children(n, {"stmtsp"}):
                reads(ch, body, LOGIC)
            cands = [s for s in dict.fromkeys(sens) if s not in body]
            if len(cands) == 1:
                clock = cands[0]
            else:
                error = "its clock cannot be told from its sensitivity list"
        for tg, deps in flop_out.items():
            m.flops.append(Flop(tg, clock, deps, n.get("loc", ""), error))

    def cell(n: dict, scope: str) -> None:
        sub = nl.modules.get(n.get("modp", ""))
        if sub is None or sub["type"] != "MODULE":
            m.unsupported.append(f"instance {scope}{n['name']} of a "
                                 f"{(sub or {}).get('type', 'missing').lower()} at "
                                 f"{nl.where(n.get('loc', ''))}")
            return
        pins = []
        for p in _children(n, {"pinsp"}):
            expr = _one(p, "exprp")
            if expr is None or not p.get("modVarp") or p["modVarp"] == "UNLINKED":
                continue
            kind = ALIAS if expr["type"] == "VARREF" else WIRE if _pure(expr) else LOGIC
            rd: dict[str, int] = {}
            reads(expr, rd, kind)
            tg, idx = writes(expr)
            for v, k in idx.items():
                rd[v] = min(rd.get(v, LOGIC), k)
            bare = expr["varp"] if expr["type"] == "VARREF" and expr["varp"] in m.vars else None
            pins.append(Pin(p["modVarp"], rd, {v: kind for v in tg}, bare))
        m.cells.append(CellRef(scope + n["name"], sub["addr"], n.get("loc", ""), pins))

    def items(nodes, scope: str) -> None:
        for n in nodes:
            t = n["type"]
            if t == "ALWAYS":
                always(n)
            elif t == "CELL":
                cell(n, scope)
            elif t in ("GENBLOCK", "BEGIN"):
                items(_children(n), scope + n["name"] + "." if n.get("name") else scope)
            elif t in ("ASSIGNW", "ASSIGNALIAS"):
                tg, deps = assignment(n, {}, False)
                for v in tg:
                    for d, k in deps.items():
                        if d != v:
                            m.comb.append((v, d, k))

    items(_children(mod), "")
    return m


# --------------------------------------------------------------------------
# Approved cells: directive plus port rules


def port_findings(model: Module, d: Directive) -> list[str]:
    """Port rules of an approved cell: every port is ``src_*`` or ``dst_*``,
    and the directive's clocks name input ports on their side."""
    out = []
    ports = {v.name: v for v in model.vars.values() if v.direction}
    for name in sorted(ports):
        if not name.startswith(("src_", "dst_")):
            out.append(f"port {name} is neither src_* nor dst_*")
    for side, clk in (("src", d.src), ("dst", d.dst)):
        if side == "src" and clk == ASYNC:
            continue
        if not clk.startswith(side + "_"):
            out.append(f"{side}={clk} does not name a {side}_* port")
        elif clk not in ports or ports[clk].direction != "INPUT":
            out.append(f"{side}={clk} is not an input port")
    if d.src == ASYNC:
        for name, v in sorted(ports.items()):
            if name.startswith("src_") and v.direction != "INPUT":
                out.append(f"src=async cell has a source-side output {name}")
    return out


# --------------------------------------------------------------------------
# Flattening and analysis (one netlist)

Node = tuple[tuple[str, ...], str]   # (instance path, var addr)


@dataclass
class Sink:
    desc: str
    where: str
    clock: Node | None      # None: a src=async cell input
    deps: dict[Node, int]


@dataclass
class Design:
    nl: Netlist
    cells: dict[str, Directive]                     # approved module addr -> directive
    names: dict[Node, str] = field(default_factory=dict)
    locs: dict[Node, str] = field(default_factory=dict)
    comb_in: dict[Node, list[tuple[Node, int]]] = field(default_factory=dict)
    flops: dict[Node, Flop] = field(default_factory=dict)
    flop_clock: dict[Node, Node | None] = field(default_factory=dict)
    pseudo: dict[Node, Node | None] = field(default_factory=dict)  # cell output -> clock
    port_clock: dict[Node, Node] = field(default_factory=dict)  # annotated top input -> clock
    sinks: list[Sink] = field(default_factory=list)
    inputs: dict[Node, str] = field(default_factory=dict)
    inouts: set[Node] = field(default_factory=set)
    memories: set[Node] = field(default_factory=set)
    findings: list[str] = field(default_factory=list)

    def name(self, n: Node) -> str:
        return self.names.get(n, "?")

    def edge(self, dst: Node, src: Node, kind: int) -> None:
        if dst != src:
            self.comb_in.setdefault(dst, []).append((src, kind))


def flatten(nl: Netlist, cells: dict[str, Directive],
            port_clocks: dict[str, str] | None = None) -> Design:
    """The instance-flattened graph. ``port_clocks`` maps an annotated input
    port of the top (var addr) to its clock input port (var addr)."""
    port_clocks = port_clocks or {}
    d = Design(nl, cells)
    top = nl.top
    if top is None:
        d.findings.append("the netlist has no top module")
        return d
    stack = [((), top["addr"])]
    while stack:
        path, addr = stack.pop()
        m = nl.model(addr)
        prefix = ".".join(path) + "." if path else ""
        for a, v in m.vars.items():
            d.names[(path, a)] = prefix + v.name
            d.locs[(path, a)] = v.loc
            if v.memory:
                d.memories.add((path, a))
            if not path and v.direction == "INOUT":
                d.inouts.add((path, a))
            if not path and v.direction in ("INPUT", "INOUT"):
                d.inputs[(path, a)] = v.name
                if a in port_clocks:
                    d.port_clock[(path, a)] = (path, port_clocks[a])
        d.findings += m.unsupported
        for tg, dep, k in m.comb:
            d.edge((path, tg), (path, dep), k)
        for f in m.flops:
            node = (path, f.target)
            if f.error:
                d.findings.append(f"cannot tell the clock of flop {d.name(node)} "
                                  f"({nl.where(f.loc)}): {f.error}")
                continue
            d.flops[node] = f
            d.flop_clock[node] = (path, f.clock) if f.clock else None
            d.sinks.append(Sink(f"flop {d.name(node)}", nl.where(d.locs[node]), (path, f.clock),
                                {(path, x): k for x, k in f.deps.items()}))
        for c in m.cells:
            sub_path = path + (c.name,)
            sub = nl.model(c.modp)
            if c.modp in cells:
                _black_box(d, path, prefix, c, sub, cells[c.modp])
                continue
            for p in c.pins:
                port = (sub_path, p.port)
                info = sub.vars.get(p.port)
                direction = info.direction if info else None
                if direction in ("INPUT", "INOUT"):
                    for r, k in p.reads.items():
                        d.edge(port, (path, r), k)
                if direction in ("OUTPUT", "INOUT"):
                    for w, k in p.writes.items():
                        d.edge((path, w), port, k)
            stack.append((sub_path, c.modp))
    return d


def _black_box(d: Design, path, prefix: str, c: CellRef, sub: Module, dv: Directive) -> None:
    """An approved cell instance: outputs are sources in their side's clock,
    inputs are sinks in theirs (or src=async sinks)."""
    by_name = {v.name: a for a, v in sub.vars.items()}
    pins = {p.port: p for p in c.pins}
    where = d.nl.where(c.loc)
    clocks: dict[str, Node | None] = {"src": None, "dst": None}
    for side, port in (("src", dv.src), ("dst", dv.dst)):
        if port == ASYNC:
            continue
        p = pins.get(by_name.get(port, ""))
        if p is None or p.bare is None:
            d.findings.append(f"cell {prefix}{c.name} ({where}): clock port {port} must "
                              "connect to a plain signal")
            continue
        clocks[side] = (path, p.bare)
    clock_ports = {by_name.get(dv.src), by_name.get(dv.dst)}
    for p in c.pins:
        info = sub.vars.get(p.port)
        if info is None or p.port in clock_ports:
            continue
        side = info.name.split("_", 1)[0]
        desc = f"{prefix}{c.name}.{info.name}"
        if info.direction == "OUTPUT":
            node = (path + (c.name,), p.port)
            d.names[node] = desc
            d.locs[node] = c.loc
            d.pseudo[node] = clocks.get(side)
            for w, k in p.writes.items():
                d.edge((path, w), node, k)
        elif info.direction == "INPUT":
            if side == "src" and dv.src == ASYNC:
                clock = None
            else:
                clock = clocks.get(side)
                if clock is None:
                    continue  # its clock pin is already a finding
            d.sinks.append(Sink(f"cell input {desc}", where, clock,
                                {(path, r): k for r, k in p.reads.items()}))


class Analysis:
    def __init__(self, d: Design):
        self.d = d
        self._domains: dict[Node, str] = {}
        self._roots: dict[Node, Node] = {}   # clock signal -> where its domain ends
        self.labels = self._propagate()

    def domain(self, clock: Node) -> str:
        """The clock domain of a clock signal: the top-level input it comes
        from through plain wires and ports, else the signal itself."""
        if clock in self._domains:
            return self._domains[clock]
        d, n, seen = self.d, clock, set()
        while n not in d.inputs and n not in d.flops and n not in d.pseudo and n not in seen:
            seen.add(n)
            ins = d.comb_in.get(n, [])
            if len(ins) != 1 or ins[0][1] != ALIAS:
                break
            n = ins[0][0]
        if n in d.inputs:
            name = d.inputs[n]
        elif n in d.flops or n in d.pseudo:
            name = f"{d.name(n)} (generated)"
        else:
            name = f"{d.name(n)} (derived)"
        self._domains[clock] = name
        self._roots[clock] = n
        return name

    def clock_inputs(self) -> list[Node]:
        """Top input ports that clock a flop or an approved cell."""
        d = self.d
        roots = set()
        for c in list(d.flop_clock.values()) + [s.clock for s in d.sinks]:
            if c is not None:
                self.domain(c)
                roots.add(self._roots[c])
        return sorted((n for n in roots if n in d.inputs), key=d.name)

    def unannotated_inputs(self) -> list[str]:
        """With more than one clock input, every other input port of the
        top (not an inout) needs a cdc-domain annotation."""
        d = self.d
        clocks = self.clock_inputs()
        if len(clocks) < 2:
            return []
        names = ", ".join(d.inputs[c] for c in clocks)
        return [f"input port {d.inputs[n]} ({d.nl.where(d.locs.get(n, ''))}) has no "
                f"cdc-domain annotation, and the top has {len(clocks)} clock inputs ({names})"
                for n in sorted(d.inputs, key=d.name)
                if n not in clocks and n not in d.port_clock and n not in d.inouts]

    def is_source(self, n: Node) -> bool:
        d = self.d
        return n in d.flops or n in d.pseudo or n in d.port_clock

    def source_domain(self, n: Node) -> str | None:
        d = self.d
        if n in d.flops:
            clock = d.flop_clock.get(n)
        elif n in d.pseudo:
            clock = d.pseudo[n]
        else:
            clock = d.port_clock.get(n)
        return self.domain(clock) if clock is not None else None

    def source_kind(self, n: Node) -> str:
        d = self.d
        return "flop" if n in d.flops else "cell output" if n in d.pseudo else "input port"

    def _propagate(self) -> dict[Node, dict[str, tuple[int, Node]]]:
        """The source domains reaching each node through combinational
        logic: per domain, the smallest edge kind on the way and one source
        (the first to reach the node with that kind) to name in a finding.
        A label only ever lowers its kind, so each node is revisited at most
        three times per domain: about O(edges x domains)."""
        d = self.d
        out: dict[Node, list[tuple[Node, int]]] = {}
        for dst, ins in d.comb_in.items():
            for src, k in ins:
                out.setdefault(src, []).append((dst, k))
        labels: dict[Node, dict[str, tuple[int, Node]]] = {}
        work = []
        for s in list(d.flops) + list(d.pseudo) + list(d.port_clock):
            dom = self.source_domain(s)
            if dom is not None:
                labels[s] = {dom: (ALIAS, s)}
                work.append(s)
        while work:
            u = work.pop()
            lu = labels[u]
            for w, k in out.get(u, ()):
                if self.is_source(w):
                    continue
                lw = labels.setdefault(w, {})
                changed = False
                for dom, (ks, rep) in lu.items():
                    nk = max(k, ks)
                    if nk < lw.get(dom, (LOGIC + 1,))[0]:
                        lw[dom] = (nk, rep)
                        changed = True
                if changed:
                    work.append(w)
        return labels

    def sources(self, deps: dict[Node, int]) -> dict[str, tuple[int, Node]]:
        """Source domains reaching a sink's data input, as in ``labels``.
        Among equal kinds the source with the smallest name is kept, so the
        named source does not depend on the order of ``deps``."""
        acc: dict[str, tuple[int, Node]] = {}
        d = self.d
        for n, k in deps.items():
            for dom, (ks, rep) in self.labels.get(n, {}).items():
                cand = (max(k, ks), rep)
                cur = acc.get(dom)
                if cur is None or (cand[0], d.name(cand[1])) < (cur[0], d.name(cur[1])):
                    acc[dom] = cand
        return acc

    def all_sources(self, deps, dom: str | None = None, cap: int = 0) -> list[Node]:
        """The sources reaching ``deps`` through combinational edges, found
        by a backward walk (only run for findings): all of them, or those
        in ``dom``; at most ``cap`` when it is set."""
        d, found, seen = self.d, [], set()
        stack = list(deps)
        while stack and not (cap and len(found) >= cap):
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if self.is_source(n):
                if dom is None or self.source_domain(n) == dom:
                    found.append(n)
                continue
            stack.extend(u for u, _ in d.comb_in.get(n, ()))
        return sorted(found, key=d.name)

    def describe(self, n: Node) -> str:
        d = self.d
        return f"{self.source_kind(n)} {d.name(n)} ({d.nl.where(d.locs.get(n, ''))})"

    def crossings(self) -> list[str]:
        """One finding per sink and foreign source domain; it names one
        source and lists the other sources of that domain (up to
        ``CO_SOURCES``)."""
        d, out = self.d, []
        for s in d.sinks:
            if s.clock is None:
                out += self._async_input(s)
                continue
            dom = self.domain(s.clock)
            for sdom, (k, src) in sorted(self.sources(s.deps).items()):
                if sdom == dom:
                    continue
                how = "through combinational logic" if k == LOGIC else "directly"
                others = [n for n in self.all_sources(s.deps, sdom, CO_SOURCES + 2)
                          if n != src][:CO_SOURCES + 1]
                also = ""
                if others:
                    more = ", and more" if len(others) > CO_SOURCES else ""
                    also = (f"; also from {sdom}: "
                            + ", ".join(self.describe(n) for n in others[:CO_SOURCES]) + more)
                out.append(f"{s.desc} (clock {dom}, {s.where}) samples "
                           f"{self.source_kind(src)} {d.name(src)} "
                           f"(clock {sdom}, {d.nl.where(d.locs.get(src, ''))}) {how}, "
                           f"with no crossing cell between them{also}")
        return out

    def _async_input(self, s: Sink) -> list[str]:
        """A src=async input must come straight from a flop: only wires
        between them."""
        d = self.d
        bad: list[str] = []
        seen: set[Node] = set()
        stack = []
        for n, k in s.deps.items():
            if k == LOGIC:
                bad.append("its connection expression")
            stack.append(n)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n in d.flops or n in d.pseudo or n in d.inputs:
                continue
            for u, k in d.comb_in.get(n, ()):
                if k == LOGIC:
                    bad.append(d.name(n))
                else:
                    stack.append(u)
        if not bad:
            return []
        at = ", ".join(sorted(set(bad)))
        return [f"{s.desc} ({s.where}) has combinational logic between it and its launching "
                f"flop (at {at}); a src=async input must come straight from a flop"]


# --------------------------------------------------------------------------
# Verilator and targets


@dataclass(frozen=True)
class Target:
    label: str
    flat: Resolved
    top: str | None


def pinned_version() -> str | None:
    try:
        data = yaml.safe_load((REPO_ROOT / "cfg" / "host.yaml").read_text()) or {}
        return str(data["verilator"]["version"])
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return None


def verilator_version(verilator: Path) -> str | None:
    try:
        out = subprocess.run([str(verilator), "--version"], capture_output=True, text=True,
                             timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.match(r"Verilator\s+(\S+)", out.strip())
    return m.group(1) if m else None


def json_command(verilator: Path, flat: Resolved, top: str | None, out_dir: Path) -> list[str]:
    cmd = [str(verilator), "--json-only", "-Wno-fatal", "--Mdir", str(out_dir),
           "--json-only-output", str(out_dir / "tree.json"),
           "--json-only-meta-output", str(out_dir / "meta.json")]
    if top:
        cmd += ["--top-module", top]
    cmd += [f"+incdir+{d}" for d in flat.incdirs]
    cmd += [f"-D{k}={'' if v is None else v}" for k, v in flat.defines.items()]
    return cmd + [str(s) for s in flat.sources]


def select(ips: list[Ip], ip_ids: set[str] | None) -> list[Ip]:
    """Top-level IPs to analyze: all of them, or those among the selected
    IPs and their ancestors (a sub-IP's domains come from its top)."""
    by_id = {ip.id: ip for ip in ips}
    if ip_ids is None:
        return [ip for ip in ips if ip.top_level]
    wanted = set()
    for i in ip_ids:
        ip = by_id.get(i)
        if ip is not None:
            wanted |= {i, *ip.ancestors}
    return [ip for ip in ips if ip.id in wanted and ip.top_level]


def port_domain_findings(nl: Netlist, domains: dict[tuple[Path, int], PortDomain],
                         used: set[tuple[Path, int]]) -> tuple[list[str], dict[str, str]]:
    """Check each annotated input port of every module in ``nl`` (its clock
    must be another input port of the same module) and record the
    annotations seen in ``used``; a broken annotation is reported the first
    time it is seen. Returns the findings and, for the top, the
    annotated input ports mapped to their clock ports (var addrs)."""
    findings: list[str] = []
    top: dict[str, str] = {}
    for addr, mod in nl.modules.items():
        if mod["type"] != "MODULE":
            continue
        model = nl.model(addr)
        ports = {v.name: a for a, v in model.vars.items() if v.direction}
        for a, v in model.vars.items():
            f = nl.file_of(v.loc) if v.direction == "INPUT" else None
            pd = domains.get((f, _line(v.loc))) if f else None
            if pd is None:
                continue
            first = (pd.file, pd.line) not in used
            used.add((pd.file, pd.line))
            name = mod.get("origName", mod["name"])
            ca = ports.get(pd.clock)
            bad = ("names the port itself" if pd.clock == v.name else
                   f"{pd.clock} is not an input port of {name}"
                   if ca is None or model.vars[ca].direction != "INPUT" else "")
            if bad and first:
                findings.append(f"{rel(pd.file, nl.src_root)}:{pd.line}: cdc-domain annotation "
                                f"on {name}.{v.name}: {bad}")
            elif not bad and nl.top is not None and addr == nl.top["addr"]:
                top[a] = ca
    return findings, top


def pass_through_findings(design: Design, own: Directive) -> list[str]:
    """A crossing cell analyzed on its own: no output may have a purely
    combinational path from an input of the other side, or a cell that is
    only wires would hide the crossing it claims to make."""
    d, out = design, []
    head = f"{rel(own.file, d.nl.src_root)}:{own.line}: crossing cell {own.module}"
    top = d.nl.model(d.nl.top["addr"])
    outputs = sorted((v.name, ((), a)) for a, v in top.vars.items() if v.direction == "OUTPUT")
    for name, o in outputs:
        side = name.split("_", 1)[0]
        stack, seen, hits = [o], set(), set()
        while stack:
            n = stack.pop()
            if n in seen or n in d.flops or n in d.pseudo:
                continue
            seen.add(n)
            for u, _ in d.comb_in.get(n, ()):
                if u in d.inputs and d.inputs[u].split("_", 1)[0] != side:
                    hits.add(d.inputs[u])
                stack.append(u)
        out += [f"{head}: output {name} has a combinational path from input {i}, so the "
                "cell would hide a crossing" for i in sorted(hits)]
    return out


def output_domain_findings(analysis: Analysis, own: Directive) -> list[str]:
    """A crossing cell analyzed on its own: every source reaching a side's
    output is in that side's clock domain. A memory written in the other
    domain may be read, when the read also depends on the output's own
    domain (a dual-clock memory read at an address of the reading side)."""
    d, out = analysis.d, []
    head = f"{rel(own.file, d.nl.src_root)}:{own.line}: crossing cell {own.module}"
    top = d.nl.model(d.nl.top["addr"])
    clocks = {v.name: ((), a) for a, v in top.vars.items() if v.direction == "INPUT"}
    for a, v in sorted(top.vars.items(), key=lambda x: x[1].name):
        side = v.name.split("_", 1)[0]
        port = own.src if side == "src" else own.dst if side == "dst" else ASYNC
        if v.direction != "OUTPUT" or port == ASYNC or port not in clocks:
            continue
        want = analysis.domain(clocks[port])
        srcs = analysis.all_sources([((), a)])
        doms = {n: analysis.source_domain(n) for n in srcs}
        foreign = [n for n in srcs if doms[n] != want]
        if foreign and all(n in d.memories for n in foreign) and want in doms.values():
            continue
        out += [f"{head}: output {v.name} is driven by {analysis.describe(n)} on clock "
                f"{doms[n]}, not by its own side's clock {want}" for n in foreign]
    return out


def analyze(nl: Netlist, directives: dict[tuple[Path, str], Directive],
            checked_cells: dict[str, list[str]],
            domains: dict[tuple[Path, int], PortDomain] | None = None,
            used: set[tuple[Path, int]] | None = None) -> tuple[list[str], int]:
    """Findings for one netlist, each once, and its count of flops.
    ``checked_cells`` caches each cell module's port findings by name;
    ``domains`` are the port-domain annotations, and ``used`` collects those
    that annotate an elaborated input port."""
    approved: dict[str, Directive] = {}
    findings: list[str] = []
    for addr, mod in nl.modules.items():
        if mod["type"] != "MODULE":
            continue
        name = mod.get("origName", mod["name"])
        f = nl.file_of(mod.get("loc", ""))
        dv = directives.get((f, name)) if f else None
        if dv is None:
            continue
        if name not in checked_cells:
            checked_cells[name] = [f"{rel(dv.file, nl.src_root)}:{dv.line}: crossing cell "
                                   f"{name}: {x}" for x in port_findings(nl.model(addr), dv)]
            findings += checked_cells[name]
        if not checked_cells[name]:
            approved[addr] = dv
    found, port_clocks = port_domain_findings(nl, domains or {},
                                              used if used is not None else set())
    findings += found
    own = approved.pop(nl.top["addr"], None) if nl.top is not None else None
    design = flatten(nl, approved, port_clocks)  # a cell analyzed on its own is looked inside
    analysis = Analysis(design)
    findings += design.findings
    if own is not None:  # its flops run on the clocks its directive names
        for node, clock in sorted(design.flop_clock.items(), key=lambda x: design.name(x[0])):
            dom = analysis.domain(clock) if clock else "?"
            if dom not in (own.src, own.dst):
                findings.append(f"{rel(own.file, nl.src_root)}:{own.line}: crossing cell "
                                f"{own.module}: flop {design.name(node)} runs on {dom}, "
                                "not on a clock its cdc-cell directive names")
        findings += pass_through_findings(design, own)
        findings += output_domain_findings(analysis, own)
    else:  # a crossing cell's ports have their domains from src_*/dst_*
        findings += analysis.unannotated_inputs()
    findings += analysis.crossings()
    return list(dict.fromkeys(findings)), len(design.flops)


def run(src_root: Path, ips: list[Ip], ip_ids: set[str] | None) -> CheckResult:
    src_root = Path(src_root)
    result = CheckResult("cdc")
    prefix = read_prefix(src_root)
    tops = select(ips, ip_ids)
    lists: list[tuple[str, Path, str | None]] = [(ip.id, ip.rtl_f, (prefix or "") + ip.id)
                                                 for ip in tops]
    if ip_ids is None:
        lists += [(f"common/{block_name(f)}", f, block_name(f)) for f in shared_blocks(src_root)]
    plan: list[Target] = []
    for label, rtl_f, top in lists:
        try:
            flat = resolve(rtl_f)
        except FilelistError as exc:
            result.findings.append(f"{label}: rtl filelist does not resolve: {exc}")
            continue
        plan.append(Target(label, flat, top if top in declared_modules(flat.sources) else None))
    files = list(dict.fromkeys(s.resolve() for t in plan for s in t.flat.sources))
    directives, found = scan_directives(files, src_root)
    result.findings += found
    domains, found = scan_port_domains(files, src_root)
    result.findings += found
    used: set[tuple[Path, int]] = set()
    complete = True
    flops = 0
    if plan:
        verilator = sim.find_verilator()
        if verilator is None:
            raise sim.SimError(sim.not_found_message())
        pinned, have = pinned_version(), verilator_version(verilator)
        if pinned is None or have != pinned:
            result.findings.append(
                f"Verilator {have or '(unknown version)'} found, but the JSON netlist reader "
                f"is tied to the pinned version {pinned or '(none)'} in cfg/host.yaml")
            plan = []
            complete = False
        checked_cells: dict[str, list[str]] = {}
        for t in plan:
            print(f"[cdc] {t.label}: top={t.top or '(verilator picks)'}", flush=True)
            with tempfile.TemporaryDirectory(prefix="cdc-") as tmp:
                out = Path(tmp)
                try:
                    proc = subprocess.run(json_command(verilator, t.flat, t.top, out),
                                          capture_output=True, text=True, timeout=CDC_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    result.findings.append(f"{t.label}: verilator --json-only timed out after "
                                           f"{CDC_TIMEOUT_S:.0f}s")
                    complete = False
                    continue
                if proc.returncode != 0:
                    err = [ln for ln in (proc.stdout + proc.stderr).splitlines()
                           if ln.startswith("%Error")]
                    result.findings.append(f"{t.label}: verilator --json-only exited "
                                           f"{proc.returncode}: {(err or [''])[0]}")
                    complete = False
                    continue
                try:
                    tree = json.loads((out / "tree.json").read_text())
                    meta = json.loads((out / "meta.json").read_text())
                except (OSError, ValueError) as exc:
                    result.findings.append(f"{t.label}: unreadable JSON netlist: {exc}")
                    complete = False
                    continue
            found, n = analyze(Netlist(tree, meta, src_root), directives, checked_cells,
                               domains, used)
            flops += n
            result.findings += [f"{t.label}: {x}" for x in found]
    if complete:  # an annotation no analyzed module declares as an input port
        result.findings += [f"{rel(f, src_root)}:{n}: cdc-domain annotation does not annotate "
                            "an input port of an analyzed module"
                            for f, n in sorted(set(domains) - used)]
    result.findings = list(dict.fromkeys(result.findings))
    result.checked = (f"{len(tops)} top-level IP(s), {len(lists) - len(tops)} shared block(s), "
                      f"{len(plan)} Verilator --json-only run(s), {flops} flop(s), "
                      f"{len(directives)} cdc-cell directive(s), "
                      f"{len(domains)} cdc-domain annotation(s)")
    return result
