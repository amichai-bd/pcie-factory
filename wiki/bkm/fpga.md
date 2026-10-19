# FPGA BKM

> Provenance: adapted from nand2anything `wiki/bkm/bkm_fpga.md` (commit
> `085ef4b`, same owner, no license file). Layering, source/generated
> boundary and acceptance ladder kept; its board, clock default and host
> routing replaced by this repository's decisions.

## Target

| Item | Value |
| --- | --- |
| Tool | Intel Quartus Prime Lite |
| Board | Terasic DE2-115 |
| Device | Cyclone IV E `EP4CE115F29C7` |
| Host | Self-hosted runners with Quartus; version recorded in `cfg/host.yaml` (Quartus Prime Lite 25.1std.0) |
| When | Analysis and synthesis only (`--synth-only`) for affected targets in required CI; the full flow (fit, timing) in nightly L2 or on demand with `./build fpga` ([levels](devops.md#levels-and-budgets)) |
| Command | `./build fpga --ip <ip>` ([fpga tool spec](../tools/fpga/SPEC.md)) |

Required [CI vendor checks](../tools/ci/SPEC.md#build-ci-vendor) run Quartus
analysis and synthesis only (`./build fpga --synth-only`) in parallel with
simulation; the fit and timing acceptance runs in the nightly
([where each check runs](devops.md#levels-and-budgets)).
Its unit tests (project generation, report parsing) use hand-made fixture
reports and run without Quartus. In a cloud session, `./build fpga --ip <ip> --dry-run` validates the
inputs and generates the project scripts. On demand on a configured host,
the `fpga` workflow (manual dispatch only) compiles and uploads only
`metrics.json` and `worst_path.txt`; the Quartus reports and logs stay in
`workdir/` on the host.

## Inputs

The flow consumes the IP's `rtl/<ip>/<ip>.rtl.f`, flattened by the filelist
resolver, plus only what is in `src/fpga/<ip>/`:

| File | Holds |
| --- | --- |
| `<prefix><ip>.sdc` (exactly one `.sdc`) | Clocks, generated clocks, I/O delays, false and multicycle paths, each with a comment giving the reason |
| `de2_115.qsf.tcl` | Family, device, pin assignments, I/O standards, drive settings, project settings; pins cite the board manual table |
| Board wrapper `<prefix><ip>_de2_115.sv` (exactly one `.sv`) | Binds the IP's ports to board pins: clock, reset, LEDs, switches, connectors. Its file stem is the Quartus top-level module. |

- `src/fpga/<ip>/` exists only for a top-level IP that is a synthesis
  target; a sub-IP never has one (the layout check rejects it).
- The IP's FPGA page, `wiki/src/fpga/<ip>/FPGA.md`, documents the board
  wrapper (pins, reset, I/O use), the constraints and how to run the flow.
- The board wrapper is synthesizable RTL under the [RTL BKM](rtl.md) but is
  not in `<ip>.rtl.f`: the FPGA flow adds it, so simulation and ASIC never
  see board policy. It is linted with Verilator `-Wall` together with
  `<ip>.rtl.f`, as part of the FPGA inputs: `./build check` lints it (the
  [checks SPEC](../tools/checks/SPEC.md#lint-check) owns how), and
  `./build fpga` runs that lint first (the `lint` step) whenever Verilator
  is available.
- Wrapper ports follow the RTL BKM names (`clk_50_i`, `key_ni`, `sw_i`,
  `ledr_o`); a comment gives the board manual's signal name. Declare only the
  board pins the wrapper uses, so `-Wall` has no unused-signal warnings.
- Every board input is asynchronous to the board clock: pass it through a
  two-flop synchronizer before use, and give it a `set_false_path` with that
  reason in the SDC. Static outputs (LEDs) get a `set_false_path -to` too, so
  the timing report has no unconstrained I/O.
- Push buttons are active low; derive a synchronous active-high reset and
  one-cycle press pulses from their synchronized levels.
- Reset the design after configuration with a power-on reset (a short
  counter that holds reset until it saturates) rather than relying on a
  button press; the board file sets `ALLOW_POWER_UP_DONT_CARE OFF` so that
  counter starts at 0.
- No duplicate source list: the flow never names RTL files other than
  through `<ip>.rtl.f` and the wrapper; the only substitution is a shared
  block's Intel backend (below).
- Vendor IP (PLLs, memories) is avoided while behavioral RTL suffices. When
  needed, it sits behind a module boundary with a synthesizable simulation
  model, and its generator, parameters and license are in the IP's FPGA
  page (`wiki/src/fpga/<ip>/FPGA.md`).

## Intel backends

A shared block whose inferred form is not good enough on the board (a RAM
that must land in M9K blocks with exact read-during-write behavior) gets an
Intel [source backend](../project/layout.md#source-backends):
`<block>_intel.sv` beside the portable `<block>.sv`, the same module built
from a vendor primitive (`altsyncram`). `cfg/backends.yaml` gives the flow
the `intel` suffix, and `./build fpga` puts the backend file in place of the
portable one in the Quartus source list; simulation, `./build check` lint
and the ASIC flow keep the portable file.

- One module name in two files, no `` `ifdef``, no `ramstyle` attribute, no
  vendor primitive in IP RTL: the IP instantiates the shared block only.
- The backend requests exactly the shape the block's MAS lists; the device
  family defaults to this board's (Cyclone IV E), and another Intel board
  overrides it from its board settings file.
- Verilator sees a backend only through the DV-only primitive stub in
  `dv_models`: a lint test with the expected messages named, stub tests
  that reject every parameter outside the shape, and the block's contract
  tests run with `backend: fpga`. The stub is never a vendor model and
  never enters a Quartus or product source list.
- Simulation never proves the vendor mapping. Each consuming target's fit
  records it: the per-RAM rows of `metrics.json` (`resources.rams`: mode,
  clock mode, geometry, block count, mixed-port read-during-write mode),
  reported in the PR and in the block's MAS.

## Layers

| Layer | Owns | Where |
| --- | --- | --- |
| IP RTL | Function; no board choices | `src/rtl/<ip>/` |
| Board wrapper | Pin binding, board clock and reset conditioning | `src/fpga/<ip>/` |
| Constraints | Timing and placement intent | `src/fpga/<ip>/` |
| Flow | Project generation, compile, report parsing | `tools/` (fpga tool SPEC) |

## Generated output

The Quartus project, databases, reports, `.sof` and logs go under
`workdir/` only and are never committed. Generated HDL that must become
source needs its own issue and review.

## Acceptance ladder

FPGA evidence is reported as distinct stages; never claim a later stage from
an earlier one:

1. Verilator behavior tests pass for the IP ([DV BKM](dv.md)).
2. Structural check: wrapper lint-clean with the IP; ports, filelist and
   constraints resolve.
3. Quartus analysis, fit and timing: warnings reviewed, every clock meets
   its constraint (no negative setup or hold slack).
4. Programming and observed board behavior, only when an issue asks for it
   and the captain has authorized physical access.

Quartus is not a simulator: simulation never depends on Quartus libraries.

## Metrics

`./build fpga` writes `workdir/<ip>/fpga/metrics.json` (the
[fpga tool spec](../tools/fpga/SPEC.md#metrics) owns the schema) with at
least:

| Metric | Source |
| --- | --- |
| Logic elements, registers, memory bits | Fitter summary |
| M9K blocks, embedded multipliers (DSP) | Fitter resource usage summary |
| Per RAM: mode, clock mode, depth, width, M9K blocks, mixed-port read-during-write mode | Fitter RAM Summary |
| Fmax and restricted Fmax per clock | Timing analyzer, worst over the slow models |
| Worst setup, hold, recovery and removal slack | Timing analyzer, worst over all models |
| Unconstrained paths | Timing analyzer "Unconstrained Paths Summary" |
| Critical path | Timing analyzer, worst setup path (from, to, clocks, slack) |
| Warning count per step | Each Quartus step's closing message |
| Warning IDs per step, with counts | Each step log's `Warning (<id>):` messages |

Nightly collects these per IP into the tracker
([DevOps BKM](devops.md#metrics)).

## Known Quartus limits

Quartus Prime Lite reads the RTL as SystemVerilog-2005
(`VERILOG_INPUT_VERSION SYSTEMVERILOG_2005`) and rejects some constructs
that Verilator accepts. Each one found is a [style-check](../tools/checks/SPEC.md#style-check)
rule, so `./build check` rejects it before Quartus runs; the
[RTL BKM](rtl.md#systemverilog-rules) gives the portable form.

| Construct | Quartus error | Portable form | Style rule |
| --- | --- | --- | --- |
| Module-level `for`, `if` or `case` without `generate ... endgenerate` | 10170 syntax error, expecting `endmodule` | Wrap it in `generate ... endgenerate` (labels and hierarchy unchanged) | `bare-generate` |
| `for (genvar i = 0; ...)` | 10170, "genvar" is a reserved keyword | `genvar i;` at module level, then `for (i = 0; ...)` | `inline-genvar` |
| Pattern key that is also a name in scope (`'{sym0: ...}` beside a net `sym0`) | 10734, "is not a constant" | Rename the local | `pattern-key-name` |
| Assignment pattern as a `?:` or comparison operand | 10207 "can't resolve reference to object", 10901 | A package constant or a named signal | `pattern-context` |
| `$countones` (and the other bit-vector functions) in logic | 10174, not supported for synthesis | A package function; the system functions stay in assertion arguments | `system-task` |
| A member select named like a built-in method (`h.len`, `x.size`, ...) inside a function | 10049, value must not be assigned to nonvariable `len`; then 10903 and 12152 at the caller | Rename the struct member (`length`); the same select at module level is accepted | `method-member-name` |
| A package constant as an index inside a port connection (`.p_i (s.f[pkg::C])`) under `` `default_nettype none`` | 10162, can't declare implicit net | A named net plus `assign` (`assign f_c = s.f[pkg::C];`, then `.p_i (f_c)`) | `port-index-constant` |

## Known Quartus warnings

Warnings are reviewed, not ignored. These are expected with Quartus Lite on
this board and need no action; any other warning is a finding. The IDs are
mirrored in `known_warnings` in `cfg/fpga.yaml`, and a unit test keeps that
list equal to this table, so add or remove a row and its ID together:

| ID | Warning | Why it is accepted |
| --- | --- | --- |
| 292013 | LogicLock is only available with a subscription license | Lite edition; the flow uses no LogicLock regions. |
| 169177 | Pins must meet requirements for 3.3, 3.0 and 2.5 V interfaces | Reported for 3.3-V LVTTL clock inputs that the board wires that way (`CLOCK_50`, `SMA_CLKIN`, `HSMC_CLKIN0` with JP6 at its default) (AN 447). |
| 10036 | Object assigned a value but never read | A signal read only inside `COMMON_ASSERT*` arguments, which `SYNTHESIS` compiles out ([assertions](rtl.md#assertions)); Verilator lint, which sees the assertions, still reports a signal that nothing reads. |
| 276020 | Inferred RAM node from synchronous design logic; pass-through logic added to match the read-during-write behavior | Quartus maps a register array to block RAM and adds bypass logic so that a read of the word being written returns what the RTL returns: behavior is preserved, and the added logic is inside the timing and resource results. A memory that must be block RAM with a defined collision uses the explicit backend instead ([layout, source backends](../project/layout.md#source-backends)). |

`./build fpga` records each step's warning IDs with their counts in
`metrics.json` and, after a passing run, prints per step how many warnings
are known and how many are unknown, then one line per unknown ID
([fpga tool spec](../tools/fpga/SPEC.md#warnings)). That line is the only log
content published besides a failure excerpt: the ID and its first message,
with paths made relative to the checkout (other absolute paths cut to the
file name), user and host names, e-mail and IP addresses replaced, and the
text cut to 160 characters. The logs themselves stay on the host
([publishability](wiki.md#publishability)).

## Review checklist

- Inputs limited to `<ip>.rtl.f` (with the shared blocks' Intel backends
  selected), the wrapper, SDC and QSF Tcl.
- Each RAM a consuming target fits is in the expected mode with the expected
  M9K count, as the block's MAS records.
- Every SDC exception has a reason.
- Timing met on every clock, or the PR states the gap and the owning issue.
  PR CI runs synthesis only, so a change that can move timing or fit records
  a local `./build fpga --ip <id>` run; otherwise the next nightly shows it.
- Quartus warnings are the known ones above, or the PR explains each new one
  (each unknown synthesis ID is printed in the `ci-fpga` job log; fitter and
  timing IDs in the nightly's).
- No generated output committed.
