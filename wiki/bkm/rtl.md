# RTL BKM

> Provenance: adapted from nand2anything `wiki/bkm/bkm_rtl.md` (commit
> `085ef4b`, same owner, no license file). Generic SystemVerilog rules kept;
> project macros, inventories and exceptions dropped.

## Scope

This page owns repository-wide SystemVerilog style for synthesizable RTL
under `src/rtl/`. An IP's `arch/<ip>/HAS.md` owns its behavior and
interfaces (a sub-IP's `rtl/<ip>/MAS.md` does, as it has no HAS), its
`rtl/<ip>/MAS.md` owns its micro-architecture ([wiki BKM](wiki.md#page-set)), and
the [layout page](../project/layout.md) owns file placement. Simulation-only code
follows the [DV BKM](dv.md).

## SystemVerilog rules

- Synthesizable SystemVerilog-2017 that Verilator, Quartus Lite and the ORFS
  frontend all accept: `logic`, `always_comb`, `always_ff`, parameters,
  enums, packed structs, packed arrays and `generate` where they make intent
  clearer. SystemVerilog interfaces, modports and unions stay out of the
  `.rtl.f` closure until a flow issue proves all three tools handle them;
  DV code may use them.
- **Explicit widths.** Size every constant (`8'd0`, `'0`, `WIDTH'(x)`), make
  extension, truncation and signedness visible, and never rely on implicit
  32-bit integer context.
- **Total combinational logic.** Every `always_comb` assigns a default to
  every output before any branch; every `case` has a `default`. No latches,
  no combinational loops.
- **One owner per signal.** Each variable is driven by exactly one `assign`
  or one process.
- **Registers.** Only in `always_ff`, only nonblocking `<=`, only the
  register update: compute next state (`foo_d`) in `always_comb`, register it
  as `foo_q`. No logic beyond reset and enable selection in `always_ff`.
- **Nets.** Use `logic` with a separate `assign`; no `wire`, `reg`, implicit
  nets (`` `default_nettype none `` at the top of every file, restored to
  `wire` at the end) or declaration-time assignment.
- **Ports.** ANSI ports with explicit direction, type and width. Named
  connections (`.port(sig)`) on every instance; no `.*`, no positional
  connections.
- **No `ifdef` for behavior.** Product RTL has no `` `ifdef SYNTHESIS`` or
  simulation-only branches; configuration is by parameter. Never condition
  on a macro the RTL does not `` `define`` itself (a vendor or tool macro
  such as `ALTERA_RESERVED_QIS`): that code is hidden from lint and
  simulation while another flow still compiles it. Include guards are the
  expected use.
- **No functions with side effects, no tasks** in RTL. Pure helper functions
  live in the owning package.
- Small modules with one purpose. Comments explain protocol, timing,
  invariants or tradeoffs, not syntax.

## Types and constants live in packages

- Every `typedef` (enum, packed struct, width alias) lives in a package
  (`<prefix><ip>_pkg.sv`), never inside a module.
- A constant or parameter default used by more than one module lives in the
  package; a `localparam` derived and used only inside one module stays
  local.
- Hierarchy: one package per IP for its contract; a sub-IP adds its own
  package only when it has types the parent does not need. Shared, IP-neutral
  types go in `src/rtl/common/common_*_pkg.sv` only when there is a real
  second consumer.
- Package dependencies point down the hierarchy: a parent may import a
  sub-IP's package, and a sub-IP never imports its parent's, so the sub-IP's
  filelist stays standalone.
- Packages cannot see module parameters. A field whose width follows a module
  parameter stays a separately declared signal, not a packaged struct field.
- Packages come first in the filelist that owns them (see below).

## Naming

The prefix is declared once in [`src/README.md`](../../src/README.md); this
page refers to it as `<prefix>`.

| Item | Rule | Example shape |
| --- | --- | --- |
| Folder | IP id; a sub-IP's id is a parent's id plus `_<sub>`, a flat sibling under each kind | `src/rtl/<ip>/`, `src/rtl/<ip>_<sub>/` |
| Module, file | `<prefix><id>[_<role>]`, one module per file, file named after the module | `<prefix><ip>_<sub>.sv` |
| Package | Module rule plus `_pkg` | `<prefix><ip>_pkg` |
| Shared block | `common_<name>` | `common_<name>.sv` |
| Type | `t_<name>` (struct, enum, alias) | `t_req` |
| Parameter, localparam, enum value | `UPPER_SNAKE` | `DATA_W` |
| Signal | `lower_snake` | `rd_valid` |
| Port suffix | `_i` input, `_o` output | `data_i` |
| Register / next state | `_q` / `_d` | `count_q`, `count_d` |
| Active-low | `n` directly before the direction letter on ports; `_n` on internal signals | `rst_ni`, `cs_no`, `busy_n` |

## Reset and clocks

- Each IP's spec (HAS, or MAS for a sub-IP) states clock, reset polarity,
  synchronous or asynchronous assertion, release behavior and every reset
  value. Default when the spec is silent: one clock `clk_i`, synchronous active-high reset `rst_i`.
- Reset only state that needs a defined value; storage arrays are not reset.
- No generated or gated clocks in fabric; use clock enables. A second clock
  domain needs an owning MAS section, and every crossing uses a crossing
  cell ([below](#clock-domain-crossings)).
- No synthesis pragmas or attributes that change simulated behavior.

## Clock-domain crossings

A crossing is any path from a flop on one clock to a flop on another clock
that is asynchronous or unrelated to it. Crossings may only use the shared
crossing cells in `src/rtl/common/`; each MAS owns its cell's rules
(spacing, Gray coding, reset):

| Crossing | Cell |
| --- | --- |
| A level, or a Gray-coded vector, from a source flop | [`common_cdc_sync2`](../src/rtl/common/common_cdc_sync2.md) |
| An event: one source cycle becomes one destination cycle | [`common_cdc_pulse`](../src/rtl/common/common_cdc_pulse.md) |
| Data, or a burst of events, with flow control | [`common_cdc_fifo`](../src/rtl/common/common_cdc_fifo.md) |

- No hand-built synchronizer, and no signal that fans out to two cells and
  reconverges in the destination domain. A cell with `src=async` (no
  source clock) takes its source inputs straight from a launching flop,
  with no logic between; a cell with a source clock samples them with its
  own source flops, so ordinary source-domain logic may drive them. A crossing no cell fits needs a new
  `common_cdc_<name>` cell with its MAS and DV plan first.
- Each domain has its own reset, synchronous to its clock. A cell's MAS
  says how its two resets must overlap.
- **Declaration.** A crossing cell declares its clocks in a comment on the
  line directly above its `module` line:

  ```systemverilog
  // cdc-cell: src=<source clock port | async> dst=<destination clock port>
  module common_cdc_<name> ...
  ```

  `src` names the cell's source clock input, or is `async` when the cell has
  no source clock (its source inputs must come straight from a flop in the
  source domain). `dst` names its destination clock input. Port names give
  every other port's domain: `src_*` ports belong to the source domain and
  `dst_*` ports to the destination domain. Only `common_cdc_*` modules in
  `src/rtl/common/` carry the directive. A cell has no combinational path
  from an input on one side to an output on the other, and each output is
  driven only from its own side's domain. The one exception is a
  dual-clock memory read: the other-domain sources are memories of two or
  more entries, and every own-side source reaching the output depends,
  through own-side flops, on a crossing cell inside the cell (a
  synchronized pointer). A pointer that is synchronized but does not
  really guard the read is left to the cell's MAS and review.
- **Port domains.** An input port declares the clock it is synchronous to
  with a comment ending its declaration line, naming another input port of
  the same module:

  ```systemverilog
  input  logic        clk_i,
  input  logic        rst_i,        // cdc-domain: clk_i
  input  logic        aux_clk_i,
  input  logic [19:0] aux_data_i,   // cdc-domain: aux_clk_i
  ```

  An IP or shared block with more than one clock input annotates every
  input port that is not a clock, as its HAS or MAS port table gives it
  (the `Clock` column), so the check sees a port sampled in the wrong
  domain. The check enforces this on each analyzed top (a top-level IP or
  a shared block) with two or more
  [clock inputs](../tools/checks/SPEC.md#cdc-check), counted as the
  check defines them: a clock that reaches flops only through a gate or
  an ICG is not counted, and a sub-IP is checked only through its top, so
  review owns those cases. `inout` ports are exempt, and so are
  `common_cdc_*` cells, whose `src_*`/`dst_*` prefixes and directive give
  the domains. A reset that only feeds synchronizers is annotated with
  the domain that also uses it directly, else the core clock. Name a second
  domain's ports with one prefix, its clock `<prefix>_clk_i` and the rest
  `<prefix>_<name>_i`, so the grouping is visible without the comment; the
  comment, not the name, is what the check reads. An integrator annotates
  its own input ports: an annotation counts only where its module is the
  analyzed top. A single-clock IP may leave its ports unannotated; a port
  without an annotation has no domain. A reset entering another domain
  goes through a crossing cell like any other signal; a reset
  synchronizer (asynchronous assertion, synchronous release) is a
  `src=async` cell whose `src_*` reset input drives the asynchronous
  reset of its `dst_clk_i` flops.
- **Check.** `./build check` runs the structural
  [CDC check](../tools/checks/SPEC.md#cdc-check): it enforces the directive,
  the port prefixes, pass-through-free cells, the cell-only crossing rule,
  the `src=async` rule and the port-domain annotations. A reset is data to
  it: a reset, synchronous or asynchronous, from another domain's flop or
  from an input port annotated with another domain is reported as a
  crossing. It does not check reconvergence, glitches, reset assertion
  and release ordering, or anything sampled from an input port without an
  annotation; review owns those.

## Assertions

- Checkers that only simulation needs go in bind modules listed in
  `dv/<ip>/<ip>.dv.f`, never in the `.rtl.f` closure. Bind to documented ports and
  internal signals named in the MAS.
- Assertions are simulator-neutral SystemVerilog that Verilator supports
  (`--assert`): immediate assertions in `always_comb`/`always_ff`, or
  concurrent properties with an explicit clock and `disable iff` reset.
- Each assertion has a label and a message naming the invariant. It speeds
  localization; the test still owns the pass condition.

## Filelists

The [layout page](../project/layout.md#per-ip-files) owns the filelist
format and names. Practice on top of it:

- Never duplicate a source list in a flow config; flows consume
  `rtl/<ip>/<ip>.rtl.f`.
- Order packages first, then leaves, then the IP top.
- Include a sub-IP or common block through its own filelist with `-F`, never
  by listing its files.
- Every `.sv` under `src/rtl/` belongs to exactly one filelist;
  `./build check` reports orphans and duplicates.

## Lint and style checks

- Verilator `--lint-only -Wall` on every `.rtl.f` is clean. Treat every
  warning as an error.
- **Waivers** live only in the reviewed waiver file owned by the check tool
  (its SPEC names the path). Each waiver names file, line or signal, the
  exact warning and a one-line reason. No `/* verilator lint_off */` pragmas
  in RTL. A waiver is a reviewed change, never a convenience.
- The RTL style checker in `./build check` enforces the mechanically
  checkable rules above (for example typedef-in-module, `wire`/`reg`, missing
  `default_nettype`, port suffixes). Rules it cannot check (such as logic in
  `always_ff`) stay review items. The
  [checks SPEC](../tools/checks/SPEC.md#style-check) lists the enforced rules.

## Review checklist

- Widths, signedness and reset values match the HAS (or sub-IP MAS).
- No latch, no multiple drivers, no unsized constant.
- Every clock-domain crossing goes through a crossing cell
  ([crossings](#clock-domain-crossings)).
- Types in packages; names follow the table.
- Filelist updated in order; no orphan or duplicate.
- Lint and style clean, or each waiver reviewed with a reason.
- HAS updated in the same PR when behavior or interfaces change, MAS when
  the structure, pipeline or timing does
  ([wiki BKM](wiki.md#specs-and-code-stay-aligned)).
