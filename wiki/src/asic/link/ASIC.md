# pcie_link ASIC

`pcie_link` (`pcie_dll` over `pcie_phy`) is hardened on sky130hd by the
[ASIC flow](../../../tools/asic/SPEC.md) as an estimation and quality
signal, following the [PD BKM](../../../bkm/pd.md) and the
[`phy` ASIC page](../phy/ASIC.md). The flow reads `link.rtl.f` (the `dll`
and `phy` filelists, then the stack top) and only the files below.

## Clock target

The [HAS clock target](../../arch/link/HAS.md#targets): `clk_i` and
`ser_rx_clk_i` both at 125 MHz (8 ns), as the periods in
[`pcie_link.sdc`](../../../../src/asic/link/pcie_link.sdc). These are the
default and nightly clocks, and 125 MHz is reported nightly.

The **M2 gate is the 100 MHz (10 ns) checkpoint on both domains**, an
explicit clock study (M2 Q10 in the [M2 record](../../decisions/0003-m2-grill.md#decisions),
after M1 Q27 and Q28): `link` contains `phy`, which does not yet meet
125 MHz ([`phy` timing status](../phy/ASIC.md#timing-status)). 125 MHz
closure is future timing work,
[#235](https://github.com/amichai-bd/pcie-factory/issues/235), and does not
block M2. Captain decision 3 of 2026-10-17 amends Q10 for the case where
`link` still misses 100 MHz with the SRAM macros: the achieved frequency is
recorded, M2 closes, and 100 MHz closure is a follow-up,
[#319](https://github.com/amichai-bd/pcie-factory/issues/319)
([M2 record, Updates](../../decisions/0003-m2-grill.md#updates);
[Timing status](#timing-status)).

| SDC clock | Port | Period | Runs |
| --- | --- | --- | --- |
| `clk` | `clk_i` | 8 ns | All of the `dll`; the MAC, PIPE, the PCS transmit path and the elastic buffer's read side. |
| `rx_clk` | `ser_rx_clk_i` | 8 ns | The PCS receive front end: the aligner and the elastic buffer's write side. |

## Constraints

The constraints are the [`phy` ones](../phy/ASIC.md#constraints), applied
at the `link` boundary; the `dll` adds no clock and no crossing
([HAS, Clocks and resets](../../arch/link/HAS.md#clocks-and-resets)):

- Clock uncertainty 0.25 ns on both clocks before clock-tree synthesis.
- I/O budget: each input arrives, and each output is needed, at 20% of the
  period of the clock its port is on; the neighbouring logic is assumed to
  use the rest. Per the [HAS interfaces](../../arch/link/HAS.md#interfaces)
  and the `cdc-domain` annotations of `pcie_link.sv`, `ser_rx_data_i` and
  `ser_rx_elec_idle_i` are on `rx_clk`; every other input and every output
  is on `clk`.
- **Clock relationship: asynchronous.** The two clocks come from different
  references within ±600 ppm of each other. Every crossing is inside
  `pcie_pcs`, through an approved `common_cdc_*` cell
  ([`link` MAS, Timing](../../rtl/link/MAS.md#timing)), so the SDC
  replaces the same-edge checks between the clocks with two exceptions:

  | Exception | Reason |
  | --- | --- |
  | `set_max_delay -ignore_clock_latency` of one period (8 ns) on every path from `clk` to `rx_clk` and from `rx_clk` to `clk` | Bounds each crossing to one cycle of the capturing clock: the skew between the bits of a synchronized Gray pointer, the elastic-buffer memory read that the pointer guards, and the RX reset's asynchronous assertion from `rst_i`. `-ignore_clock_latency` (an OpenSTA option, as in SDC 2.1) leaves the clock trees out, so the bound is on the data path alone. |
  | `set_false_path -hold` between the clocks, both directions | With no phase relationship a hold check has no meaning; the synchronizers resolve the arrival. |

  The exceptions are per clock pair, so they would also bound an
  unsynchronized crossing without flagging it; the guard is `./build
  check` (Verilator lint and the structural CDC check).
- **The `dll` combinational path.** `ltssm_state_o` → `pkt_tx_valid` →
  `phy` ([`link` MAS, Timing](../../rtl/link/MAS.md#timing)) runs from the
  `mac` LTSSM state register's decode through the `dll` replay-rollover
  logic and back into the `mac`'s packet-stream registers in one `clk`
  cycle. It is an ordinary `clk` path, with no exception.

## Flow configuration

`DESIGN_NAME` is `pcie_link`, at its default parameters (the default
credits; `TIMEOUT_DIV` and `ERR_TIMEOUT_DIV` 1, as the
[HAS](../../arch/link/HAS.md#parameters) states for ASIC builds). The RTL
is read with the slang SystemVerilog frontend
(`SYNTH_HDL_FRONTEND = slang`). The core is sized from the cell area at 30%
utilization, with placement-density headroom for the two clock trees and
resizing buffers, as for `phy`.

**Memories.** The `dll` replay buffer (256 × 32 bits) and receive buffer
(512 × 34 bits at the default credits) are
[`common_sdp_ram`](../../rtl/common/ram/common_sdp_ram.md) instances.
The ASIC flow selects the RAM's
[sky130 backend](../../rtl/common/ram/common_sdp_ram.md#sky130-backend)
(`cfg/backends.yaml`)
([#279](https://github.com/amichai-bd/pcie-factory/issues/279)), so they
are three `sky130_sram_1rw1r_64x256_8` SRAM macros (one for the replay
buffer, two deep for the receive buffer), about 1.26 mm² together, instead
of about 25 kbit of flops. Simulation and the FPGA flow keep the portable
RAM, with the same cycle behavior. `sky130hd.mk` adds the macro's LEF,
Liberty and GDS views from the image's `sky130ram` platform folder.
**Power limitation:** the macros' `vdd` and `gnd` pins are only small met3
pads spread over the array (no rings or straps), which the platform's
met4/met5 grid does not reach and its pin patterns do not name, so the
macros stay off the power grid:
[`pdn.tcl`](../../../../src/asic/link/pdn.tcl) is the platform's
standard-cell grid without its macro grids (which pdngen cannot build for
macros on no power net), and the flow's power figures and IR-drop check
cover the standard cells only. A first attempt with met4 straps over each
macro shorted VDD vias onto `gnd` pads (`asic` run 39069113585, IR check);
aligning straps to each net's pad columns, per macro orientation, is
[#320](https://github.com/amichai-bd/pcie-factory/issues/320). Timing and area are unaffected. The macro placer
places the macros. No inferred memory remains above ORFS's default
`SYNTH_MEMORY_MAX_BITS`, so the knob keeps its default.

The macro drives its read data after the falling edge, so the logic after
each buffer's read port gets about half a period minus 0.5 ns
(the backend's timing note).

## Running

`./build asic --ip link` on the CI host, or `--dry-run` anywhere to write
the generated config. Nightly runs it with every IP that has
`src/asic/<ip>/`. Results, including the setup WNS and TNS of each clock
(`timing.clocks.clk` and `timing.clocks.rx_clk`), area, cells, max fanout
and logic depth, are in `metrics.json` and, from the nightly run, on the
tracker's `link` page.

The M2 gate runs with `./build asic --ip link --study-clock-period 10`. Its
results are isolated in `workdir/link/asic-study/10ns/`, with both clocks
at 10 ns, I/O delays at 2 ns and crossing max delays at 10 ns; uncertainty
stays 0.25 ns and the hold exceptions are unchanged. The gate requires the
flow to complete with nonnegative hold slack and zero route DRCs, and
records the setup slack of both clocks. Nonnegative setup slack on both
clocks meets 100 MHz; otherwise, under captain decision 3 (which amends
Q10), the achieved frequency is recorded and the gap is owned by
[#319](https://github.com/amichai-bd/pcie-factory/issues/319). Inspect the
final `6_final.sdc` as well as `metrics.json`. Study results never replace
the nightly 125 MHz measurements.

## Timing status

Measured with the SRAM macros
([#279](https://github.com/amichai-bd/pcie-factory/issues/279)) by manual
`asic` workflow runs, both through route with 0 route DRCs and nonnegative
hold slack. `link` does **not** meet 100 MHz: the achieved frequency is
about 87 MHz (10 ns minus the worst `clk` slack). Under captain decision 3
this is recorded and M2 is not blocked; 100 MHz closure is
[#319](https://github.com/amichai-bd/pcie-factory/issues/319) and 125 MHz
closure stays with [#235](https://github.com/amichai-bd/pcie-factory/issues/235).

| Run | `clk` WS / TNS | `rx_clk` WS | Hold WS | Std-cell area | Cells (synthesis) | Logic depth | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 10 ns study (run 39080937561, at `1e4f647`) | −1.428 / −30.76 ns | +3.799 ns | +0.079 ns | 0.260 mm² | 66094 (17467) | 55 | 3010 MB, detailed route |
| 8 ns, the SDC target (run 39099181940, at `87c0f04`) | −1.864 / −199.70 ns | +1.362 ns | +0.031 ns | 0.269 mm² | 66679 (17467) | 59 | 2381 MB, detailed route |

The core is 4.75 mm² (utilization 0.32) in both; the macros add about
1.26 mm². Power, 0.101 W at 10 ns and 0.128 W at 8 ns, covers the standard
cells only (macros unpowered,
[#320](https://github.com/amichai-bd/pcie-factory/issues/320)). The 10 ns
run had the earlier 3 GB container limit (2% margin); the 8 ns run had the
6 GB limit of captain decision 2.

**Worst `clk` paths** (`timing.clocks.clk.worst_paths`, five per clock):

- **10 ns:** all five start at a receive-buffer macro's read data
  (`u_dll.u_rx_buf` row 0 `dout1`) and end at `tlp_rx_*_o` output ports,
  −1.428 to −1.304 ns (worst `dout1[60]` → `tlp_rx_data_o[1]`, 10 cells).
  The macro drives its data after the falling edge (arrival 6.85 ns), so
  the row and hold muxes and output buffers have about half a period
  minus the 20% output delay.
- **8 ns:** all five start at the `dll` transmit state register
  `u_dll.u_tlp_tx.sm_q[26]` and end at `u_dll.u_tlp_tx.st_q` bits or the
  `tlp_tx_ready_o` port, −1.864 to −1.832 ns (worst → `st_q[65]`, 47
  cells; logic depth 59 at `st_q[63]`).

`rx_clk` meets both periods; its worst paths are inside the PCS aligner
(`u_phy.u_pcs.u_align`, to `eios_q`; `g_phy.u_phy` since
[#378](https://github.com/amichai-bd/pcie-factory/issues/378)).

**The rollover path** (`ltssm_state_o` → `pkt_tx_valid` → `mac`, see
[Constraints](#constraints)) ends at the `mac` packet-stream registers.
The flattened netlist keeps no `dll` port pins, so it is not reported by
itself, and no `mac` register is among the five worst `clk` endpoints in
either run; its slack is therefore above the fifth-worst `clk` slack:
above −1.30 ns at 10 ns and above −1.83 ns at 8 ns.

Detailed routing stalled on the omarchy runners at 8 ns (no progress past
20%, runs 39080942132 and 39074381425) while every wsl runner finished in
34 to 44 minutes; that is
[#321](https://github.com/amichai-bd/pcie-factory/issues/321).

## Files

| File | Holds |
| --- | --- |
| [`pcie_link.sdc`](../../../../src/asic/link/pcie_link.sdc) | The two clocks, the study opt-in, uncertainty, I/O delays and the crossing exceptions. |
| [`sky130hd.mk`](../../../../src/asic/link/sky130hd.mk) | ORFS design knobs and the SRAM macro views; never a source list. |
| [`pdn.tcl`](../../../../src/asic/link/pdn.tcl) | The power grid: the platform's standard-cell grid. |
