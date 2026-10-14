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
| Host | The Omarchy CI host only; version recorded in `cfg/host.yaml` (Quartus Prime Lite 25.1std.0) |
| When | Nightly L2, or on demand with `./build fpga` |
| Command | `./build fpga --ip <ip>` ([fpga tool spec](../tools/fpga/SPEC.md)) |

Quartus never runs in `ci-gate` or in cloud sessions. Its unit tests (project
generation, report parsing) use hand-made fixture reports and run without
Quartus. In a cloud session, `./build fpga --ip <ip> --dry-run` validates the
inputs and generates the project scripts. On demand on the Omarchy host,
the `fpga` workflow (manual dispatch only) compiles and uploads only
`metrics.json` and `worst_path.txt`; the Quartus reports and logs stay in
`workdir/` on the host.

## Inputs

The flow consumes the IP's `rtl/<ip>.rtl.f`, flattened by the filelist
resolver, plus only what is in `src/<ip>/fpga/`:

| File | Holds |
| --- | --- |
| `<prefix><ip>.sdc` (exactly one `.sdc`) | Clocks, generated clocks, I/O delays, false and multicycle paths, each with a comment giving the reason |
| `de2_115.qsf.tcl` | Family, device, pin assignments, I/O standards, drive settings, project settings; pins cite the board manual table |
| Board wrapper `<prefix><ip>_de2_115.sv` (exactly one `.sv`) | Binds the IP's ports to board pins: clock, reset, LEDs, switches, connectors. Its file stem is the Quartus top-level module. |

- `fpga/` exists only for an IP that is a synthesis target; a sub-IP gets one
  only if it is built on its own.
- The board wrapper is synthesizable RTL under the [RTL BKM](rtl.md) but is
  not in `<ip>.rtl.f`: the FPGA flow adds it, so simulation and ASIC never
  see board policy. It is linted with Verilator `-Wall` together with
  `<ip>.rtl.f`, as part of the FPGA inputs: `./build fpga` runs that lint
  first (the `lint` step) whenever Verilator is available.
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
  through `<ip>.rtl.f` and the wrapper.
- Vendor IP (PLLs, memories) is avoided while behavioral RTL suffices. When
  needed, it sits behind a module boundary with a synthesizable simulation
  model, and its generator, parameters and license are in the IP's HAS.

## Layers

| Layer | Owns | Where |
| --- | --- | --- |
| IP RTL | Function; no board choices | `src/<ip>/rtl/` |
| Board wrapper | Pin binding, board clock and reset conditioning | `src/<ip>/fpga/` |
| Constraints | Timing and placement intent | `src/<ip>/fpga/` |
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
| Fmax and restricted Fmax per clock | Timing analyzer, worst over the slow models |
| Worst setup, hold, recovery and removal slack | Timing analyzer, worst over all models |
| Unconstrained paths | Timing analyzer "Unconstrained Paths Summary" |
| Critical path | Timing analyzer, worst setup path (from, to, clocks, slack) |
| Warning count per step | Each Quartus step's closing message |

Nightly collects these per IP into the tracker
([DevOps BKM](devops.md#metrics)).

## Known Quartus warnings

Warnings are reviewed, not ignored. These are expected with Quartus Lite on
this board and need no action; any other warning is a finding:

| ID | Warning | Why it is accepted |
| --- | --- | --- |
| 292013 | LogicLock is only available with a subscription license | Lite edition; the flow uses no LogicLock regions. |
| 169177 | Pins must meet requirements for 3.3, 3.0 and 2.5 V interfaces | Reported for the 3.3-V LVTTL `CLOCK_50` input that the board wires that way (AN 447). |

## Review checklist

- Inputs limited to `<ip>.rtl.f`, the wrapper, SDC and QSF Tcl.
- Every SDC exception has a reason.
- Timing met on every clock, or the PR states the gap and the owning issue.
- Quartus warnings are the known ones above, or the PR explains each new one.
- No generated output committed.
