# RTL BKM

> Provenance: adapted from nand2anything `wiki/bkm/bkm_rtl.md` (commit
> `085ef4b`), adapted with the owner's permission. Its register-macro style,
> assertion vocabulary, package rules and named-operand rule are kept under
> a neutral `COMMON_` prefix; project macros, inventories and exceptions are
> dropped.

## Scope

This page owns repository-wide SystemVerilog style for synthesizable RTL
under `src/rtl/` (and board wrappers under `src/fpga/`). An IP's
`arch/<ip>/HAS.md` owns its behavior and interfaces (a sub-IP's
`rtl/<ip>/MAS.md` does, as it has no HAS), its `rtl/<ip>/MAS.md` owns its
micro-architecture ([wiki BKM](wiki.md#page-set)), and the
[layout page](../project/layout.md) owns file placement. Simulation-only
code follows the [DV BKM](dv.md).

## Design rules

Four rules come first. `./build check` enforces each one on every design
file (every `.sv` below `rtl/` and `fpga/`); the only files exempt are the
two vocabulary headers, `src/rtl/common/common_macros.svh` and
`src/rtl/common/common_assertions.svh`
([the RTL vocabulary](#the-rtl-vocabulary)).

1. **No nonblocking assignment and no `always_ff` outside the vocabulary.**
   Every register, register array and memory port is one call of a
   `COMMON_DFF*` or `COMMON_MEM*` macro; next state is computed with
   blocking logic in `always_comb` or `assign`, and `always_comb` is the
   only process keyword a design file writes
   ([registers and memories](#registers-and-memories)). No approved cell
   needs an exemption: the crossing cells use the macros too. No file
   declares a clocking block (`default clocking` would let
   `assign q = $past(d);` simulate as a register outside the macros).
   Rules `raw-always-ff`, `nonblocking-assignment`, `clocking-block`.
2. **No assertion written directly.** No `assert`, `assume`, `cover`,
   `property`, `sequence`, `$fatal`, `$error`, `$warning`, `$info`,
   `initial` or `final` in a design file, and no other system task either
   (`$display`, `$write`, `$finish`, `$stop`, `$monitor`, `$strobe`,
   `$readmem*`, `$random`, ...): an `if (bad) $display(...); $finish;` is a
   hand-written assertion that synthesis silently drops. Only pure system
   functions are allowed (`$bits`, `$clog2`, `$signed`, `$unsigned`,
   `$size`, `$low`, `$high`, `$left`, `$right`, `$countones`, `$onehot*`,
   `$isunknown`); the sampled-value functions (`$past`, `$stable`, `$rose`,
   `$fell`, `$changed`, `$sampled`) are allowed only inside the arguments
   of a `COMMON_ASSERT*` macro. Use the `COMMON_ASSERT*` macros, including
   `COMMON_ASSERT_PARAM` for parameter checks ([assertions](#assertions)).
   Rules `raw-assertion`, `raw-initial`, `system-task`, `sampled-value`.
3. **No conditional compilation in a design file.** No `` `ifdef``,
   `` `ifndef``, `` `elsif`` or `` `else`` in a `.sv`, not even an include
   guard. Flow selection (synthesis guards, Verilator lint sinks, assertion
   compile-out) lives only inside the vocabulary headers, and so does every
   macro: no `` `define`` or `` `undef`` in a design file either
   ([no conditional compilation](#no-conditional-compilation)). Rules
   `conditional-compilation`, `local-define`.
4. **Types and shared constants live in packages and are named
   explicitly.** Every type, `typedef` or not (every `enum`, `struct` and
   width alias: an FSM's state enum is `pkg::t_state` in the package, never
   an anonymous `enum` in the module), and every constant used by more than
   one module lives in a package, and RTL references it
   as `pkg_name::item`. No `import` at all, in a module, a port list or a
   package ([packages](#types-and-constants-live-in-packages)). Rules
   `package-import`, `typedef-outside-package`.

## The RTL vocabulary

`src/rtl/common/common_macros.svh` defines the register, memory, crossing
and unused-bit forms and includes `common_assertions.svh`, which defines
the assertion forms. These two named files are the shared vocabulary and
the only files exempt from the design rules; the checker lists them by
name, so adding a header is a reviewed change to that list (a new `.svh`
anywhere, `rtl/common/` included, gets no exemption). Together they form an
include-only shared block (`common_macros.rtl.f` holds only `+incdir+.`;
its implementation notes are in its
[MAS](../src/rtl/common/common_macros.md)). Every form expands to exactly
one process, instance or declaration group; none hides an event
expression, several unrelated registers or a synchronizer chain. A new form
enters the headers only for a repeated, narrowly specified idiom, with a
test in the [macro DV plan](../src/dv/common/common_macros.md). Design
files call only the function-like forms; the headers' object-like macros
(their include guards) are theirs alone (rule `object-macro`).

### Includes

- A design file that calls a vocabulary macro includes the vocabulary
  itself, after `` `default_nettype none``:
  `` `include "common_macros.svh"`` (rule `vocabulary-include`).
- `` `include`` names only a vocabulary header, by bare file name; the
  include directory comes from the filelist (rule `include-path`). There
  are no IP header files: constants and types go in packages.
- A filelist whose sources use the vocabulary includes
  `-F <path>/common/common_macros.rtl.f` (a crossing macro also needs the
  cell's own filelist).

### Registers and memories

Argument order is normative: destination, next value, clock, then enable,
then reset, then reset value. `RESET` is active high and wins over
`ENABLE`; reset values are typed constants or literals.

| Form | Behavior at `posedge CLK` |
| --- | --- |
| `` `COMMON_DFF(Q, D, CLK)`` | `Q <= D`. |
| `` `COMMON_DFF_EN(Q, D, CLK, ENABLE)`` | `Q <= D` when `ENABLE`. |
| `` `COMMON_DFF_RST(Q, D, CLK, RESET)`` | `Q <= '0` when `RESET`, else `D`. |
| `` `COMMON_DFF_RST_VAL(Q, D, CLK, RESET, VALUE)`` | `Q <= VALUE` when `RESET`, else `D`. |
| `` `COMMON_DFF_RST_EN(Q, D, CLK, ENABLE, RESET, VALUE)`` | `VALUE` when `RESET`, else `D` when `ENABLE`. |
| `` `COMMON_DFF_ARST_VAL(Q, D, CLK, RESET, VALUE)`` | Asynchronous assertion (`posedge RESET` loads `VALUE` at once), else `D`. Only where the owning spec requires it. |
| `` `COMMON_DFF_ARST_EN(Q, D, CLK, ENABLE, RESET, VALUE)`` | Asynchronous `VALUE`, else `D` when `ENABLE`. |
| `` `COMMON_DFF_ARRAY_EN(MEM, NEXT_MEM, CLK, ENABLE)`` | Every element of a flop array copies the whole next-state array when `ENABLE`. |
| `` `COMMON_DFF_ARRAY_RST_EN(MEM, NEXT_MEM, CLK, ENABLE, RESET)`` | The same; `RESET` clears every element. |
| `` `COMMON_MEM_WRITE(MEM, CLK, WRITE_ENABLE, ADDRESS, DATA)`` | Inferred-RAM write port: `MEM[ADDRESS] <= DATA`. |
| `` `COMMON_MEM_WRITE_LANES(MEM, CLK, WRITE_ENABLE, ADDRESS, DATA, LANE_ENABLE, LANE_WIDTH)`` | Writes lane `l` (`LANE_WIDTH` bits) when `LANE_ENABLE[l]`. |
| `` `COMMON_MEM_READ(Q, MEM, CLK, ADDRESS)`` | Registered read port: `Q <= MEM[ADDRESS]`. |
| `` `COMMON_MEM_READ_EN(Q, MEM, CLK, READ_ENABLE, ADDRESS)`` | The same when `READ_ENABLE`. |
| `` `COMMON_UNUSED_BITS(NAME, BITS)`` | Lint-only: under Verilator declares `logic NAME = ^(BITS)` so deliberately unused bits are observed without a waiver; synthesis sees nothing. `NAME` starts with `unused_` and has no reader. |

- Group a state machine's registers into one packed struct with a reset
  constant from the package when that clarifies ownership and reset.
- Reset only state that needs a defined value; data qualified by a valid
  bit uses `COMMON_DFF_EN`, and storage arrays are not reset.
- A memory has one `COMMON_MEM_WRITE*` and one `COMMON_MEM_READ*` (or a
  combinational index) per port. Reads and writes live in separate
  processes, so a same-edge same-address read returns the old data. Never
  build a memory from the register-array forms or a whole-array next state:
  that defeats RAM inference.
- **Named macro operands.** Pass signal names, typed constants or literals
  to the non-assertion macros, never expressions: declare the term with its
  own `assign` first (`assign waddr = wbin_q[ADDR_W-1:0];`). Names state
  purpose, stage and polarity. Assertion macros keep inline properties and
  `COMMON_UNUSED_BITS` takes an explicit slice or concatenation. This rule
  is a review item; the checker does not enforce it.
- A conversion to the vocabulary keeps cycle, reset priority, enable and
  old-value semantics; the block's focused test is the proof, not token
  comparison.

### Assertions

All assertions come from `common_assertions.svh`. Each failure is `$fatal`
with the label, so a failing simulation stops and the test fails. Every
form but `COMMON_ASSERT_PARAM` is empty when `SYNTHESIS` is defined, which
only the FPGA and ASIC flows do ([FPGA](../tools/fpga/SPEC.md),
[ASIC](../tools/asic/SPEC.md)); simulation builds pass `--assert`
([build SPEC](../tools/build/SPEC.md#simulation-build-sim)).

| Form | Check |
| --- | --- |
| `` `COMMON_ASSERT(NAME, CLK, RESET, PROPERTY)`` | Concurrent property at `posedge CLK`, `disable iff (RESET)`. |
| `` `COMMON_ASSERT_NO_RESET(NAME, CLK, PROPERTY)`` | The same with no reset disable; only for a property that holds from the first edge. |
| `` `COMMON_ASSERT_NEVER(NAME, CLK, RESET, CONDITION)`` | `CONDITION` is never true outside reset. |
| `` `COMMON_ASSERT_KNOWN(NAME, CLK, RESET, SIGNAL)`` | `!$isunknown(SIGNAL)` (meaningful in four-state simulators only). |
| `` `COMMON_ASSERT_STABLE_WHEN(NAME, CLK, RESET, HOLD, SIGNAL)`` | `HOLD` at one edge keeps `SIGNAL` unchanged at the next; `HOLD` is sampled by a flop `NAME_hold_q` (assertion state only). |
| `` `COMMON_ASSERT_CLOCKED(NAME, CLK, RESET, CONDITION)`` | Immediate check at `posedge CLK` while `!RESET`, in the active region. |
| `` `COMMON_ASSERT_CLOCKED_WHEN(NAME, CLK, RESET, GUARD, CONDITION)`` | The same under a nested `if (GUARD)` (not `!GUARD \|\| CONDITION`, which differs for X). |
| `` `COMMON_ASSERT_PARAM(LABEL, CONDITION, MESSAGE)`` | Module scope, every flow: a false `CONDITION` elaborates block `LABEL`. In the simulation view it holds an elaboration-time `$fatal`, so `./build check` lint and the simulation build reject the parameters; under `SYNTHESIS` it holds a time-zero `initial $fatal`, which synthesis tools accept but ignore (lint is the gate). |

- Assertions sit in the module whose invariant they state, on signals its
  MAS names, each with a label naming the invariant. They speed fault
  localization; the test still owns the pass condition.
- Checkers that need testbench state stay in bind modules listed in
  `dv/<ip>/<ip>.dv.f`, never in the `.rtl.f` closure; DV code may write
  raw assertions ([DV BKM](dv.md)).
- A signal read only by an assertion is unused under `SYNTHESIS`; the lint
  check runs the simulation view, where it has its reader.

### No conditional compilation

Design files never select code by macro: configuration is by parameter,
and the only flow selection (`SYNTHESIS` for assertion compile-out,
`VERILATOR` for `COMMON_UNUSED_BITS`) is inside the vocabulary headers,
which alone may test those two macros (rule `sim-ifdef` elsewhere). An
include file other than the headers may hold only an include guard, and
then only on a macro the RTL closure defines (rule `external-ifdef`): a
macro that only a vendor or tool flow defines would hide code from lint
and simulation.

## SystemVerilog rules

- Synthesizable SystemVerilog-2017 that Verilator, Quartus Lite and the ORFS
  frontend all accept: `logic`, `always_comb`, parameters, enums, packed
  structs, packed arrays and `generate` where they make intent clearer.
  SystemVerilog interfaces, modports and unions stay out of the `.rtl.f`
  closure until a flow issue proves all three tools handle them; DV code
  may use them.
- **Explicit widths.** Size every constant (`8'd0`, `'0`, `WIDTH'(x)`), make
  extension, truncation and signedness visible, and never rely on implicit
  32-bit integer context.
- **Total combinational logic.** Every `always_comb` assigns a default to
  every output before any branch; every `case` has a `default`. No latches,
  no combinational loops.
- **One owner per signal.** Each variable is driven by exactly one `assign`,
  one `always_comb` or one vocabulary macro.
- **Nets.** Use `logic` with a separate `assign`; no `wire`, `reg`, implicit
  nets (`` `default_nettype none`` at the top of every `.sv`, restored to
  `wire` at the end) or declaration-time assignment.
- **Ports.** ANSI ports with explicit direction, type and width. Named
  connections (`.port(sig)`) on every instance; no `.*`, no positional
  connections.
- **Generate blocks** carry a `g_<name>` label.
- **Macros** are only the vocabulary's: a design file defines none.
- **No functions with side effects, no tasks** in RTL. Pure helper functions
  live in the owning package.
- Small modules with one purpose. Comments explain protocol, timing,
  invariants or tradeoffs, not syntax.

## Types and constants live in packages

- Every type (enum, packed struct, width alias) is a `typedef` in a package
  (`<prefix><ip>_pkg.sv`), never declared inside a module, anonymous or
  not.
- A constant used by more than one module lives in the package; a
  `localparam` derived and used only inside one module stays local. A module
  `parameter` stays a parameter; when its default is shared, the default is
  a package constant (`parameter int W = <prefix><ip>_pkg::W`).
- **Explicit references.** RTL names every package item as
  `pkg_name::item` (`<prefix><ip>_pkg::t_req` in a port list,
  `<prefix><ip>_pkg::SEED` in a reset value). No `import`, wildcard or named: a reader sees where
  each name comes from, and two packages never collide.
- Hierarchy: one package per IP for its contract; a sub-IP adds its own
  package only when it has types the parent does not need. Shared, IP-neutral
  types go in `src/rtl/common/common_*_pkg.sv` only when there is a real
  second consumer (none yet: no common block needs one).
- Package dependencies point down the hierarchy: a parent may use a
  sub-IP's package, and a sub-IP never uses its parent's, so the sub-IP's
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
| Vocabulary macro | `COMMON_<FORM>` | `COMMON_DFF_RST` |
| Type | `t_<name>` (struct, enum, alias) | `t_req` |
| Parameter, localparam, enum value | `UPPER_SNAKE` | `DATA_W` |
| Signal | `lower_snake` | `rd_valid` |
| Port suffix | `_i` input, `_o` output | `data_i` |
| Register / next state | `_q` / `_d` | `count_q`, `count_d` |
| Active-low | `n` directly before the direction letter on ports; `_n` on internal signals | `rst_ni`, `cs_no`, `busy_n` |
| Instance, generate block, assertion | `u_<name>`, `g_<name>`, `a_<name>` | `u_sync`, `g_lanes`, `a_no_overflow` |

## Reset and clocks

- Each IP's spec (HAS, or MAS for a sub-IP) states clock, reset polarity,
  synchronous or asynchronous assertion, release behavior and every reset
  value. Default when the spec is silent: one clock `clk_i`, synchronous active-high reset `rst_i`.
- An asynchronously asserted reset comes only from a
  `` `COMMON_RESET_SYNC`` ([`common_cdc_rst`](#clock-domain-crossings)) in
  its domain, never from a raw reset input of another domain. Each flop
  that takes it is a `` `COMMON_DFF_ARST_VAL`` or `` `COMMON_DFF_ARST_EN``,
  so it also enters reset with no clock edge.
- Reset only state that needs a defined value; storage arrays are not reset.
- No generated or gated clocks in fabric; use clock enables. A second clock
  domain needs an owning MAS section, and every crossing uses a crossing
  cell ([below](#clock-domain-crossings)).
- No synthesis pragmas or attributes that change simulated behavior.

## Clock-domain crossings

A crossing is any path from a flop on one clock to a flop on another clock
that is asynchronous or unrelated to it. Crossings may only use the shared
crossing cells in `src/rtl/common/`; each MAS owns its cell's rules
(spacing, Gray coding, reset). Outside `src/rtl/common/` a cell that a
vocabulary macro wraps is instantiated only through that macro (rule
`cdc-cell-macro`), so RTL uses one vocabulary while every crossing stays a
cell instance the CDC check recognizes:

| Crossing | Macro | Cell |
| --- | --- | --- |
| A level, or a Gray-coded vector, from a source flop; reset to `'0` | `` `COMMON_METAFLOP(INST, Q, D, DST_CLK, DST_RST)`` | [`common_cdc_sync2`](../src/rtl/common/common_cdc_sync2.md) |
| The same, reset to `VALUE` | `` `COMMON_METAFLOP_VAL(INST, Q, D, DST_CLK, DST_RST, VALUE)`` | `common_cdc_sync2` (`RESET_VALUE`) |
| An event: one source cycle becomes one destination cycle | `` `COMMON_PULSE_SYNC(INST, Q, D, SRC_CLK, SRC_RST, DST_CLK, DST_RST)`` | [`common_cdc_pulse`](../src/rtl/common/common_cdc_pulse.md) |
| A reset: asynchronous assertion, synchronous release after two `DST_CLK` edges | `` `COMMON_RESET_SYNC(INST, Q, D, DST_CLK)`` | [`common_cdc_rst`](../src/rtl/common/common_cdc_rst.md) |
| Data, or a burst of events, with flow control | none yet: instantiate inside `src/rtl/common/` only | [`common_cdc_fifo`](../src/rtl/common/common_cdc_fifo.md) |

`INST` names the instance, the width follows `Q`, and the caller's filelist
includes the cell's filelist with `-F`.

- No hand-built synchronizer, and no signal that fans out to two cells and
  reconverges in the destination domain. A cell with `src=async` (no
  source clock) takes its source inputs straight from a launching flop
  (or, for a reset, a reset input pin), with no logic between; a cell with a source clock samples them with its
  own source flops, so ordinary source-domain logic may drive them. A crossing no cell fits needs a new
  `common_cdc_<name>` cell with its MAS and DV plan first.
- Each domain has its own reset, synchronous to its clock, or, where its
  spec requires asynchronous assertion, from `common_cdc_rst`, which
  releases it synchronously. A cell's MAS says how its two resets must
  overlap.
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
  the `src=async` rule and the port-domain annotations. It reads the
  elaborated netlist, so a cell behind a macro is the same cell instance.
  A reset is data to it: a reset, synchronous or asynchronous, from
  another domain's flop or from an input port annotated with another
  domain is reported as a crossing. It does not check reconvergence,
  glitches, reset assertion and release ordering, or anything sampled from
  an input port without an annotation; review owns those.

## Filelists

The [layout page](../project/layout.md#per-ip-files) owns the filelist
format and names. Practice on top of it:

- Never duplicate a source list in a flow config; flows consume
  `rtl/<ip>/<ip>.rtl.f`.
- Order: the vocabulary block, packages, then leaves, then the IP top.
- Include a sub-IP or common block through its own filelist with `-F`, never
  by listing its files.
- Every `.sv` under `src/rtl/` is listed by exactly one filelist;
  `./build check` reports orphans and duplicates.

## Lint and style checks

- Verilator `--lint-only -Wall` on every `.rtl.f` is clean. Treat every
  warning as an error.
- **Waivers** live only in the reviewed waiver file owned by the check tool
  (its SPEC names the path). Each waiver names file, line or signal, the
  exact warning and a one-line reason. No `/* verilator lint_off */` pragmas
  in RTL. A waiver is a reviewed change, never a convenience; deliberately
  unused bits use `COMMON_UNUSED_BITS` instead.
- The RTL style checker in `./build check` enforces the design rules above
  and the other mechanically checkable rules (typedef-in-module,
  `wire`/`reg`, missing `default_nettype`, port suffixes, includes). Rules
  it cannot check (named macro operands, widths, latches, one driver) stay
  review items. The [checks SPEC](../tools/checks/SPEC.md#style-check)
  lists the enforced rules.

## Review checklist

- Widths, signedness and reset values match the HAS (or sub-IP MAS).
- No latch, no multiple drivers, no unsized constant.
- Every register, memory port and assertion is a vocabulary macro with
  named operands.
- Every clock-domain crossing goes through a crossing macro or cell
  ([crossings](#clock-domain-crossings)).
- Types in packages, referenced as `pkg::item`; names follow the table.
- Filelist updated in order; no orphan or duplicate.
- Lint and style clean, or each waiver reviewed with a reason.
- HAS updated in the same PR when behavior or interfaces change, MAS when
  the structure, pipeline or timing does
  ([wiki BKM](wiki.md#specs-and-code-stay-aligned)).
