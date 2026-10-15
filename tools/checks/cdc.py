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
CELL_PREFIX = COMMON_PREFIX + "cdc_"
ASYNC = "async"
SIDES = ("src", "dst")

# A comment line that starts with ``cdc-cell`` is a directive; it must then
# match the grammar exactly.
_DIRECTIVE_LINE_RE = re.compile(r"^\s*//\s*cdc-cell\b")
_DIRECTIVE_RE = re.compile(r"^\s*//\s*cdc-cell:\s*src=(?P<src>[A-Za-z_]\w*)"
                           r"\s+dst=(?P<dst>[A-Za-z_]\w*)\s*$")
_MODULE_LINE_RE = re.compile(r"^\s*module\s+(?:(?:automatic|static)\s+)?(?P<name>\w+)")

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

    def file_of(self, loc: str) -> Path | None:
        f = self.files.get(loc.split(",", 1)[0]) if loc else None
        return Path(f).resolve() if f and not f.startswith("<") else None

    def where(self, loc: str) -> str:
        """``file:line`` relative to the source root, from a node's loc."""
        path = self.file_of(loc)
        try:
            line = int(loc.split(",")[1].split(":")[0])
        except (IndexError, ValueError):
            line = 0
        return f"{rel(path, self.src_root)}:{line}" if path else "?"

    def model(self, addr: str) -> Module:
        if addr not in self._models:
            self._models[addr] = _build_model(self, self.modules[addr])
        return self._models[addr]


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
                                            d if d in ("INPUT", "OUTPUT", "INOUT") else None)
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
    sinks: list[Sink] = field(default_factory=list)
    inputs: dict[Node, str] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)

    def name(self, n: Node) -> str:
        return self.names.get(n, "?")

    def edge(self, dst: Node, src: Node, kind: int) -> None:
        if dst != src:
            self.comb_in.setdefault(dst, []).append((src, kind))


def flatten(nl: Netlist, cells: dict[str, Directive]) -> Design:
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
            if not path and v.direction in ("INPUT", "INOUT"):
                d.inputs[(path, a)] = v.name
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
        return name

    def source_domain(self, n: Node) -> str | None:
        d = self.d
        clock = d.flop_clock.get(n) if n in d.flops else d.pseudo.get(n)
        return self.domain(clock) if clock is not None else None

    def _propagate(self) -> dict[Node, dict[Node, int]]:
        """Every flop and cell output reaching each node through
        combinational logic, with the smallest edge kind on the way."""
        d = self.d
        out: dict[Node, list[tuple[Node, int]]] = {}
        for dst, ins in d.comb_in.items():
            for src, k in ins:
                out.setdefault(src, []).append((dst, k))
        labels: dict[Node, dict[Node, int]] = {}
        work = [s for s in list(d.flops) + list(d.pseudo)]
        for s in work:
            labels.setdefault(s, {})[s] = ALIAS
        while work:
            u = work.pop()
            lu = labels[u]
            for w, k in out.get(u, ()):
                if w in d.flops or w in d.pseudo:
                    continue
                lw = labels.setdefault(w, {})
                changed = False
                for s, ks in lu.items():
                    nk = max(k, ks)
                    if nk < lw.get(s, LOGIC + 1):
                        lw[s] = nk
                        changed = True
                if changed:
                    work.append(w)
        return labels

    def sources(self, deps: dict[Node, int]) -> dict[Node, int]:
        acc: dict[Node, int] = {}
        for n, k in deps.items():
            for s, ks in self.labels.get(n, {}).items():
                nk = max(k, ks)
                if nk < acc.get(s, LOGIC + 1):
                    acc[s] = nk
        return acc

    def crossings(self) -> list[str]:
        d, out = self.d, []
        for s in d.sinks:
            if s.clock is None:
                out += self._async_input(s)
                continue
            dom = self.domain(s.clock)
            for src, k in sorted(self.sources(s.deps).items(), key=lambda x: d.name(x[0])):
                sdom = self.source_domain(src)
                if sdom is None or sdom == dom:
                    continue
                how = "through combinational logic" if k == LOGIC else "directly"
                out.append(f"{s.desc} (clock {dom}, {s.where}) samples "
                           f"{'flop' if src in d.flops else 'cell output'} {d.name(src)} "
                           f"(clock {sdom}, {d.nl.where(d.locs.get(src, ''))}) {how}, "
                           "with no crossing cell between them")
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


def analyze(nl: Netlist, directives: dict[tuple[Path, str], Directive],
            checked_cells: dict[str, list[str]]) -> tuple[list[str], int]:
    """Findings for one netlist and its count of flops. ``checked_cells``
    caches each cell module's port findings by name."""
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
    own = approved.pop(nl.top["addr"], None) if nl.top is not None else None
    design = flatten(nl, approved)  # a cell analyzed on its own is looked inside
    analysis = Analysis(design)
    findings += design.findings
    if own is not None:  # its flops run on the clocks its directive names
        for node, clock in sorted(design.flop_clock.items(), key=lambda x: design.name(x[0])):
            dom = analysis.domain(clock) if clock else "?"
            if dom not in (own.src, own.dst):
                findings.append(f"{rel(own.file, nl.src_root)}:{own.line}: crossing cell "
                                f"{own.module}: flop {design.name(node)} runs on {dom}, "
                                "not on a clock its cdc-cell directive names")
    findings += analysis.crossings()
    return findings, len(design.flops)


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
    headers_only = 0
    for label, rtl_f, top in lists:
        try:
            flat = resolve(rtl_f)
        except FilelistError as exc:
            result.findings.append(f"{label}: rtl filelist does not resolve: {exc}")
            continue
        if not flat.sources:  # an include-only shared block holds no flops
            headers_only += 1
            continue
        plan.append(Target(label, flat, top if top in declared_modules(flat.sources) else None))
    files = list(dict.fromkeys(s.resolve() for t in plan for s in t.flat.sources))
    directives, found = scan_directives(files, src_root)
    result.findings += found
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
                    continue
                if proc.returncode != 0:
                    err = [ln for ln in (proc.stdout + proc.stderr).splitlines()
                           if ln.startswith("%Error")]
                    result.findings.append(f"{t.label}: verilator --json-only exited "
                                           f"{proc.returncode}: {(err or [''])[0]}")
                    continue
                try:
                    tree = json.loads((out / "tree.json").read_text())
                    meta = json.loads((out / "meta.json").read_text())
                except (OSError, ValueError) as exc:
                    result.findings.append(f"{t.label}: unreadable JSON netlist: {exc}")
                    continue
            found, n = analyze(Netlist(tree, meta, src_root), directives, checked_cells)
            flops += n
            result.findings += [f"{t.label}: {x}" for x in found]
    result.checked = (f"{len(tops)} top-level IP(s), "
                      f"{len(lists) - len(tops) - headers_only} shared block(s), "
                      f"{len(plan)} Verilator --json-only run(s), {flops} flop(s), "
                      f"{len(directives)} cdc-cell directive(s)"
                      + (f", {headers_only} include-only block(s) skipped" if headers_only else ""))
    return result
