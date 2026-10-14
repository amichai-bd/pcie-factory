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
  simulation-only branches; configuration is by parameter.
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
  domain needs an owning MAS section, and every crossing uses a specified
  synchronizer or protocol.
- No synthesis pragmas or attributes that change simulated behavior.

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
- Types in packages; names follow the table.
- Filelist updated in order; no orphan or duplicate.
- Lint and style clean, or each waiver reviewed with a reason.
- HAS updated in the same PR when behavior or interfaces change, MAS when
  the structure, pipeline or timing does
  ([wiki BKM](wiki.md#specs-and-code-stay-aligned)).
