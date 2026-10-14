# PD BKM

> Provenance: adapted from the nand2anything ASIC flow spec
> (`wiki/tools/asic/SPEC.md`, commit `085ef4b`, same owner, no license file).
> The ORFS input split, output boundary and summary-metrics idea kept; its
> platforms, pins, study clocks and design specifics dropped.

## Target

| Item | Value |
| --- | --- |
| Flow | OpenROAD-flow-scripts (ORFS): synthesis, floorplan, place, CTS, route |
| Platform | `sky130hd` |
| Host | The Omarchy CI host; ORFS and tool versions pinned in `cfg/host.yaml` |
| When | Nightly L2, or on demand with `./build asic` |

The flow is an estimation and quality signal for the RTL, not a tape-out. It
never runs in `ci-gate`. Its unit tests (config generation, report parsing)
use fixture reports and run without ORFS.

## Inputs

The flow consumes the IP's `rtl/<ip>.rtl.f`, flattened by the filelist
resolver, plus only what is in `src/<ip>/asic/`:

| File | Holds |
| --- | --- |
| `<ip>.sdc` | Clock period and uncertainty, input and output delays, each exception with a reason |
| `sky130hd.mk` | ORFS design config: `DESIGN_NAME` (the IP top), platform, utilization or die/core area, placement density and other knobs; never a source list |

- `asic/` exists only for an IP that is a synthesis target; a sub-IP gets one
  only if it is hardened on its own.
- The flow tool generates the ORFS `VERILOG_FILES`, include directories and
  defines from the flattened filelist; a hand-written source list in
  `sky130hd.mk` is a review finding.
- The SDC clock comes from the IP's HAS timing section. A different study
  clock is a run option, never an edit to the HAS target.
- Keep RTL portable: no vendor primitives in `<ip>.rtl.f`; memories stay
  behavioral until a macro is specified in the HAS.

## Generated output

ORFS logs, reports, results (netlists, DEF, ODB, GDS) and `metrics.json` go
under `workdir/` only and are never committed. Each run starts from a clean
work directory so stale results never mask a failure.

## Acceptance ladder

1. Verilator behavior tests pass for the IP ([DV BKM](dv.md)).
2. Lint clean ([RTL BKM](rtl.md#lint-and-style-checks)); synthesis warnings
   reviewed (latches, undriven or removed logic are failures).
3. Flow completes through route with the reported metrics.
4. Timing: WNS ≥ 0 at the HAS clock, or the gap is stated with its owning
   issue.

Synthesis removing logic that the HAS requires is a bug, even when the flow
passes; check the cell count against expectation.

## Metrics

`./build asic` writes `metrics.json` (the asic tool SPEC, issue #14, owns the
schema) with at least:

| Metric | Meaning |
| --- | --- |
| WNS, TNS | Worst and total negative setup slack (ns) at the SDC clock |
| Area | Standard-cell area (µm²), plus core area and utilization |
| Cells | Standard-cell instance count |
| Max fanout | Largest net fanout after placement |
| Logic depth | Longest combinational cone between sequential points |
| Worst path | Start and end point of the worst setup path |
| Toolchain | ORFS and tool versions, source revision |

Nightly collects these per IP into the tracker
([DevOps BKM](devops.md#metrics)).

## Review checklist

- Inputs limited to `<ip>.rtl.f`, `<ip>.sdc` and `sky130hd.mk`.
- SDC clock matches the HAS; every exception has a reason.
- Metrics reported with the toolchain versions; WNS gap explained.
- No generated output committed.
