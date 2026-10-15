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
| Host | The Omarchy CI host, through the pinned `openroad/orfs` Docker image recorded in `cfg/host.yaml` (`hosts.omarchy.orfs_image`) |
| When | Nightly L2 or on demand only: the `asic` workflow (`workflow_dispatch`, which nightly calls) or `./build asic --ip <id>` on the host; never in `ci-gate` or on pull requests |

The flow is an estimation and quality signal for the RTL, not a tape-out. It
never runs in `ci-gate`. Its unit tests (config generation, report parsing)
use fixture reports and run without ORFS.

## Inputs

The flow consumes the IP's `rtl/<ip>/<ip>.rtl.f`, flattened by the filelist
resolver, plus only what is in `src/asic/<ip>/`:

| File | Holds |
| --- | --- |
| `<prefix><ip>.sdc` (exactly one `.sdc`) | Clock period and uncertainty, input and output delays, each exception with a reason |
| `sky130hd.mk` | ORFS design config: `DESIGN_NAME` (the IP top), platform, utilization or die/core area, placement density and other knobs; never a source list |

- `src/asic/<ip>/` exists only for a top-level IP that is a synthesis
  target; a sub-IP never has one (the layout check rejects it).
- The IP's ASIC page, `wiki/src/asic/<ip>/ASIC.md`, states the clock
  target, the constraints and flow knobs, and how to run the flow.
- The flow tool generates the ORFS `VERILOG_FILES`, include directories and
  defines from the flattened filelist, plus `PLATFORM`, `SDC_FILE` and
  `DESIGN_NICKNAME`; `sky130hd.mk` setting any of them is an error
  ([asic spec](../tools/asic/SPEC.md#inputs)).
- RTL with packages, typed parameters or other SystemVerilog beyond Yosys's
  own reader sets `SYNTH_HDL_FRONTEND = slang` in `sky130hd.mk`, so ORFS reads
  it with the slang frontend built into its Yosys.
- The SDC clock comes from the clock target on the IP's ASIC page
  (`wiki/src/asic/<ip>/ASIC.md`), which meets the HAS performance targets. A
  different study clock is a run option, never an edit to that target.
- Keep RTL portable: no vendor primitives in `<ip>.rtl.f`; memories stay
  behavioral until a macro is specified in the MAS.

## Generated output

ORFS logs, reports, results (netlists, DEF, ODB, GDS) and `metrics.json` go
under `workdir/<ip>/asic/` only and are never committed. Each run starts from
a clean work directory so stale results never mask a failure. The `asic`
workflow publishes only `metrics.json` and the tool's `worst_path.txt`
summary as its `asic-metrics` artifact, never raw ORFS reports or logs.

## Acceptance ladder

1. Verilator behavior tests pass for the IP ([DV BKM](dv.md)).
2. Lint clean ([RTL BKM](rtl.md#lint-and-style-checks)); synthesis warnings
   reviewed (latches, undriven or removed logic are failures).
3. Flow completes through route with the reported metrics.
4. Timing: WNS ≥ 0 at the ASIC page's clock target, or the gap is stated
   with its owning issue.

Synthesis removing logic that the MAS requires is a bug, even when the flow
passes; check the cell count against expectation.

## Metrics

`./build asic` writes `metrics.json`; the
[asic spec](../tools/asic/SPEC.md#metricsjson-schema-1) owns the schema and
how each value is derived. It holds at least:

| Metric | Meaning |
| --- | --- |
| WNS, TNS | Worst and total negative setup slack (ns), over all SDC clocks and per clock |
| Area | Standard-cell area (µm²), plus core and die area and utilization (fraction of core) |
| Cells | Standard-cell instances after route (tap, clock and repair buffers included, fill excluded), and the synthesis count |
| Max fanout | Largest data-net fanout in the final design (clock nets excluded) |
| Logic depth | Most cells on any register/port-to-register/port path in the final design |
| Worst path | Start and end point, slack and cell count of the worst setup path |
| Toolchain | Runner, ORFS image and commit, Yosys version, source revision |

Nightly collects these per IP into the tracker
([DevOps BKM](devops.md#metrics)).

## Review checklist

- Inputs limited to `<ip>.rtl.f`, one `<prefix><ip>.sdc` and `sky130hd.mk`.
- SDC clock matches the ASIC page; every exception has a reason.
- Metrics reported with the toolchain versions; WNS gap explained.
- No generated output committed.
