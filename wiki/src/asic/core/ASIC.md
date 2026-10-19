# pcie_core ASIC

`pcie_core` (`pcie_tl` and `pcie_cfg` over `pcie_link`) is hardened on
sky130hd by the [ASIC flow](../../../tools/asic/SPEC.md) as an estimation
and quality signal, following the [PD BKM](../../../bkm/pd.md) and the
[`link` ASIC page](../link/ASIC.md). The flow reads `core.rtl.f` (the
`link`, `tl` and `cfg` filelists, then the stack top) and only the files
below.

## Clock target

The [HAS clock target](../../arch/core/HAS.md#clocks): `clk_i` and
`ser_rx_clk_i` both at 125 MHz (8 ns), as the periods in
[`pcie_core.sdc`](../../../../src/asic/core/pcie_core.sdc). These are the
default and nightly clocks, and 125 MHz is reported nightly; its closure
is future timing work,
[#235](https://github.com/amichai-bd/pcie-factory/issues/235).

The **M3 gate is the 100 MHz (10 ns) checkpoint on both domains**, an
explicit clock study (goal item 8 of the
[M3 record](../../decisions/0004-m3-grill.md#goal), after M1 Q27). If it is
missed, the achieved frequency and the worst paths are recorded here and
100 MHz closure becomes a follow-up, as captain decision 3 of 2026-10-17
set for `link` ([M2 record, Updates](../../decisions/0003-m2-grill.md#updates);
[Timing status](#timing-status)).

| SDC clock | Port | Period | Runs |
| --- | --- | --- | --- |
| `clk` | `clk_i` | 8 ns | `tl`, `cfg`, all of the `dll`; the MAC, PIPE, the PCS transmit path and the elastic buffer's read side. |
| `rx_clk` | `ser_rx_clk_i` | 8 ns | The PCS receive front end: the aligner and the elastic buffer's write side. |

## Constraints

The [`link` constraints](../link/ASIC.md#constraints), applied at the
`core` boundary; `tl` and `cfg` add no clock and no crossing
([HAS, Clocks](../../arch/core/HAS.md#clocks)):

- Clock uncertainty 0.25 ns on both clocks before clock-tree synthesis.
- I/O budget: each input arrives, and each output is needed, at 20% of the
  period of the clock its port is on. Per the
  [HAS interfaces](../../arch/core/HAS.md#interfaces) and the `cdc-domain`
  annotations of `pcie_core.sv`, `ser_rx_data_i` and `ser_rx_elec_idle_i`
  are on `rx_clk`; every other input and every output is on `clk`.
- **Clock relationship: asynchronous.** Every crossing is inside
  `pcie_pcs`, through an approved `common_cdc_*` cell, so the SDC replaces
  the same-edge checks between the clocks with the `link` exceptions: a
  `set_max_delay -ignore_clock_latency` of one capturing period on every
  path between the clocks, both directions, and `set_false_path -hold`
  between them, both directions (reasons on the
  [`link` page](../link/ASIC.md#constraints)). The guard against an
  unsynchronized crossing is `./build check` (Verilator lint and the
  structural CDC check).

## Flow configuration

`DESIGN_NAME` is `pcie_core`, at its default parameters (the default
credits, `MSIX_TABLE_SIZE` 8, `BAR0_ADDR_W` 12; `TIMEOUT_DIV`,
`ERR_TIMEOUT_DIV` and `CPL_TIMEOUT_DIV` 1, as the
[HAS](../../arch/core/HAS.md#parameters) states for ASIC builds). The RTL
is read with the slang SystemVerilog frontend
(`SYNTH_HDL_FRONTEND = slang`). The core is sized from the instance area,
macros included, at 40% utilization (30% gave an 8.5 mm² core whose
detailed route ran out of memory), with placement-density headroom for the
two clock trees and resizing buffers.

**Lighter flow.** `core` is the largest target, and its detailed route
needs more than the 6 GB container that `link` fits in. So
`sky130hd.mk` cuts repair work (captain decisions of 2026-10-18 and
2026-10-19 on [#296](https://github.com/amichai-bd/pcie-factory/issues/296#issuecomment-6260250587)):

- Timing repair covers ORFS's default share of the failing endpoints
  (`TNS_END_PERCENT` at its default), not all of them as for `link`.
- `SKIP_INCREMENTAL_REPAIR` and `SKIP_LAST_GASP` skip the repair pass
  inside global route and the last-gasp repair before detailed route.

These passes trade run time and memory for slack, so the final timing can
be slightly worse than a full flow would give. The container limit is
8 GB, and the host runs one ORFS job at a time.

**Memories.** Every RAM is a
[`common_sdp_ram`](../../rtl/common/ram/common_sdp_ram.md), and the ASIC
flow selects its [sky130 backend](../../rtl/common/ram/common_sdp_ram.md#sky130-backend)
(`cfg/backends.yaml`). The backend puts a one-lane RAM of at least
8192 bits on SRAM macros and keeps the others on flops:

| RAM | Geometry | Implementation |
| --- | --- | --- |
| `dll` replay buffer | 256 × 32 | One `sky130_sram_1rw1r_64x256_8` macro, as in `link`. |
| `dll` receive buffer | 512 × 34 | Two macros, one per row, as in `link`. |
| `tl` requester-completion data queue (`rc`) | 256 × 32 | One macro (8192 bits). |
| `tl` receive posted data queue | 128 × 32 | Flops (4096 bits). |
| `tl` completion data and transmit posted data queues | 64 × 32 each | Flops. |
| `tl` header and descriptor queues (receive posted, non-posted to `cq` and to `cfg`; requester completions; transmit non-posted; completion descriptors) | 4 to 32 entries of 21 to 127 bits | Flops (about 3.6 kbit in all). |
| `cfg` MSI-X table | 8 × 96, 12 lanes | Flops (768 bits, lane enables). |

The four macros are about 1.68 mm² together; about 12.5 kbit of RAM is on
flops. `sky130hd.mk` adds the
macro's LEF, Liberty and GDS views from the image's `sky130ram` platform
folder. **Power limitation**, as in `link`: the macros' `vdd` and `gnd`
pins are small met3 pads that the platform's met4/met5 grid does not
reach, so [`pdn.tcl`](../../../../src/asic/core/pdn.tcl) is the platform's
standard-cell grid without its macro grids, and the power figures and
IR-drop check cover the standard cells only
([#320](https://github.com/amichai-bd/pcie-factory/issues/320)). The macro
placer places the macros. No inferred memory is above ORFS's default
`SYNTH_MEMORY_MAX_BITS`, so the knob keeps its default.

## Running

`./build asic --ip core` on the CI host, or `--dry-run` anywhere to write
the generated config. Nightly runs it with every IP that has
`src/asic/<ip>/`. Results, including the setup WNS and TNS of each clock
(`timing.clocks.clk` and `timing.clocks.rx_clk`), area, cells, max fanout,
logic depth, power and peak memory, are in `metrics.json` and, from the
nightly run, on the tracker's `core` page.

The M3 gate runs with `./build asic --ip core --study-clock-period 10` (the
`asic` workflow's `study_clock_period` input). Its results are isolated in
`workdir/core/asic-study/10ns/`, with both clocks at 10 ns, I/O delays at
2 ns and crossing max delays at 10 ns; uncertainty and the hold exceptions
are unchanged. The gate requires the flow to complete with nonnegative
hold slack and zero route DRCs; nonnegative setup slack on both clocks
meets 100 MHz. Study results never replace the nightly 125 MHz
measurements.

## Timing status

**Interim: no run has completed detailed route yet**, so there is no
routed timing, final area or route DRC count, and the M3 gate is not yet
judged. Runs by the `asic` workflow on the WSL runners:

| Run | Inputs | Outcome | Last timing (global route) | Cells (synthesis) | Std-cell area | Peak memory |
| --- | --- | --- | --- | --- | --- | --- |
| 8 ns (run 39141818708, at `bf78951`) | 30% utilization, `TNS_END_PERCENT` 100, 6 GB container | Detailed route killed at the 6 GB limit | WNS −7.47 ns, TNS −21579 ns | 156616 (54847) | 1.155 mm² | 6125 MB, detailed route |
| 10 ns study (run 39163877374, at `65b7411`) | 40% utilization, default `TNS_END_PERCENT`, 10 GB container | Stopped at the 7200 s budget in detailed route (104 violations after the first optimization pass, 5.4 to 6.2 GB) | WNS −1.50 ns, TNS −309.6 ns; hold +0.093 ns | 125437 (55343) | 1.083 mm² | above 6 GB, detailed route |
| 10 ns study (run 39172848187, at `65b7411`, 8400 s budget) | as the row above | The runner lost contact with GitHub after 2 h 7 min; no result | none | none | none | none |

The second row includes the `tl`
registered transmit stage of
[#295](https://github.com/amichai-bd/pcie-factory/issues/295). Its core is
6.38 mm² (utilization 0.43), and its power estimate (0.276 W) covers the
standard cells only. At global route, `core` misses 100 MHz by about
1.5 ns (about 87 MHz). Timing usually changes a little after detailed
route, so this is an estimate, not the gate result. Per-clock slacks and
worst paths come only from a finished run (`timing.clocks`).

The second and third rows ran with a 10 GB container and the
repair passes on; the third run lost the host. The final inputs add the
[lighter flow](#flow-configuration) and an 8 GB limit.

## Files

| File | Holds |
| --- | --- |
| [`pcie_core.sdc`](../../../../src/asic/core/pcie_core.sdc) | The two clocks, the study opt-in, uncertainty, I/O delays and the crossing exceptions. |
| [`sky130hd.mk`](../../../../src/asic/core/sky130hd.mk) | ORFS design knobs and the SRAM macro views; never a source list. |
| [`pdn.tcl`](../../../../src/asic/core/pdn.tcl) | The power grid: the platform's standard-cell grid. |
