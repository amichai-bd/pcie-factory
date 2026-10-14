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
| Host | The Omarchy CI host only; version pinned in `cfg/host.yaml` |
| When | Nightly L2, or on demand with `./build fpga` |

Quartus never runs in `ci-gate` or in cloud sessions. Its unit tests (project
generation, report parsing) use fixture reports and run without Quartus.

## Inputs

The flow consumes the IP's `rtl/<ip>.rtl.f`, flattened by the filelist
resolver, plus only what is in `src/<ip>/fpga/`:

| File | Holds |
| --- | --- |
| `<ip>.sdc` | Clocks, generated clocks, I/O delays, false and multicycle paths, each with a comment giving the reason |
| `de2_115.qsf.tcl` | Device, pin assignments, I/O standards, project settings |
| Board wrapper (`<prefix><ip>_de2_115.sv`) | Binds the IP's ports to board pins: clock, reset, LEDs, switches, connectors |

- `fpga/` exists only for an IP that is a synthesis target; a sub-IP gets one
  only if it is built on its own.
- The board wrapper is synthesizable RTL under the [RTL BKM](rtl.md) but is
  not in `<ip>.rtl.f`: the FPGA flow adds it, so simulation and ASIC never
  see board policy. It is linted with Verilator `-Wall` together with
  `<ip>.rtl.f`, as part of the FPGA inputs; the
  [checks SPEC](../tools/checks/SPEC.md#lint-check) owns how, and the fpga
  tool (#13) consumes the same inputs.
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

`./build fpga` writes `metrics.json` (the fpga tool SPEC, issue #13, owns
the schema) with at least:

| Metric | Source |
| --- | --- |
| Logic elements, registers | Fitter resource summary |
| M9K blocks, embedded multipliers (DSP) | Fitter resource summary |
| Fmax per clock | Timing analyzer, slow model |
| Worst setup and hold slack | Timing analyzer |
| Critical path | Timing analyzer, worst setup path |

Nightly collects these per IP into the tracker
([DevOps BKM](devops.md#metrics)).

## Review checklist

- Inputs limited to `<ip>.rtl.f`, the wrapper, SDC and QSF Tcl.
- Every SDC exception has a reason.
- Timing met on every clock, or the PR states the gap and the owning issue.
- No generated output committed.
