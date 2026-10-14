# RTL BKM

> Provenance: adapted from nand2anything `wiki/bkm/bkm_rtl.md` (commit
> `085ef4b`, same owner, no license file). Generic SystemVerilog rules kept;
> project macros, inventories and exceptions dropped.

## Scope

This page owns repository-wide SystemVerilog style for synthesizable RTL
under `src/**/rtl/`. An IP's `HAS.md` owns its behavior; the
[layout page](../project/layout.md) owns file placement. Simulation-only code
follows the [DV BKM](dv.md).

## SystemVerilog rules

- Synthesizable SystemVerilog-2017 that Verilator, Quartus Lite and the ORFS
  frontend all accept: `logic`, `always_comb`, `always_ff`, parameters,
  enums, packed structs, packed arrays and `generate` where they make intent
  clearer.
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
  connections. Interfaces only for a real shared contract.
- **No `ifdef` for behavior.** Product RTL has no `` `ifdef SYNTHESIS`` or
  simulation-only branches; configuration is by parameter.
- **No functions with side effects, no tasks** in RTL. Pure helper functions
  live in the owning package.
- Small modules with one purpose. Comments explain protocol, timing,
  invariants or tradeoffs, not syntax.

## Types and constants live in packages

- Every `typedef` (enum, struct, union, width alias) lives in a package
  (`<prefix><ip>_pkg.sv`), never inside a module.
- A constant or parameter default used by more than one module lives in the
  package; a `localparam` derived and used only inside one module stays
  local.
- Hierarchy: one package per IP for its contract; a sub-IP adds its own
  package only when it has types the parent does not need. Shared, IP-neutral
  types go in `src/common/rtl/common_*_pkg.sv` only when there is a real
  second consumer.
- Packages cannot see module parameters. A field whose width follows a module
  parameter stays a separately declared signal, not a packaged struct field.
- Packages come first in the filelist that owns them (see below).

## Naming

The prefix is declared once in [`src/README.md`](../../src/README.md); this
page refers to it as `<prefix>`.

| Item | Rule | Example shape |
| --- | --- | --- |
| Directory | Bare IP or sub-IP name | `src/<ip>/<subip>/` |
| Module, file | `<prefix><ip>[_<subip>][_<role>]`, one module per file, file named after the module | `<prefix><ip>_<subip>.sv` |
| Package | Module rule plus `_pkg` | `<prefix><ip>_pkg` |
| Shared block | `common_<name>` | `common_<name>.sv` |
| Type | `t_<name>` (struct, enum, alias) | `t_req` |
| Parameter, localparam, enum value | `UPPER_SNAKE` | `DATA_W` |
| Signal | `lower_snake` | `rd_valid` |
| Port suffix | `_i` input, `_o` output | `data_i` |
| Register / next state | `_q` / `_d` | `count_q`, `count_d` |
| Active-low | `_n` suffix (before `_i`/`_o`) | `rst_ni` |

## Reset and clocks

- Each module's HAS states clock, reset polarity, synchronous or asynchronous
  assertion, release behavior and every reset value. Default when the HAS is
  silent: one clock `clk_i`, synchronous active-high reset `rst_i`.
- Reset only state that needs a defined value; storage arrays are not reset.
- No generated or gated clocks in fabric; use clock enables. A second clock
  domain needs an owning HAS section, and every crossing uses a specified
  synchronizer or protocol.
- No synthesis pragmas or attributes that change simulated behavior.

## Assertions

- Checkers that only simulation needs go in bind modules listed in
  `dv/<ip>.dv.f`, never in the `.rtl.f` closure. Bind to documented ports and
  internal signals named in the HAS.
- Assertions are simulator-neutral SystemVerilog that Verilator supports
  (`--assert`): immediate assertions in `always_comb`/`always_ff`, or
  concurrent properties with an explicit clock and `disable iff` reset.
- Each assertion has a label and a message naming the invariant. It speeds
  localization; the test still owns the pass condition.

## Filelists

Rules from the [layout page](../project/layout.md#per-ip-files), restated
only as practice:

- `rtl/<ip>.rtl.f` (bare IP name, as the layout page fixes it) lists
  synthesizable sources only. It is the single source of truth for simulation, FPGA and
  ASIC; never duplicate a source list in a flow config.
- Plain Verilator `.f` syntax: one path per line, `//` comments, paths
  relative to the filelist, and only `-F`, `+incdir+` and `+define+`.
- Explicit order: packages, then leaves, then the IP top. No globs.
- Include a sub-IP or common block through its own filelist with `-F`, never
  by listing its files.
- Every `.sv` under `src/**/rtl/` appears in exactly one filelist; no
  duplicates after flattening. `./build check` enforces this.

## Lint and style checks

- Verilator `--lint-only -Wall` on every `.rtl.f` is clean. Treat every
  warning as an error.
- **Waivers** live only in the reviewed waiver file owned by the check tool
  (its SPEC names the path). Each waiver names file, line or signal, the
  exact warning and a one-line reason. No `/* verilator lint_off */` pragmas
  in RTL. A waiver is a reviewed change, never a convenience.
- The RTL style checker in `./build check` enforces the mechanically
  checkable rules above (for example typedef-in-module, `wire`/`reg`, missing
  `default_nettype`, logic in `always_ff`). Rules it cannot check stay review
  items. Its tool SPEC lists the enforced rules; it is created by the
  `./build check` issue (#7).

## Review checklist

- Widths, signedness and reset values match the HAS.
- No latch, no multiple drivers, no unsized constant.
- Types in packages; names follow the table.
- Filelist updated in order; no orphan or duplicate.
- Lint and style clean, or each waiver reviewed with a reason.
- HAS updated in the same PR when behavior or interfaces change
  ([wiki BKM](wiki.md#specs-and-code-stay-aligned)).
