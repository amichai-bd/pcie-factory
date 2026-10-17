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
block M2.

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
(512 × 34 bits at the default credits) are behavioral
[`common_sdp_ram`](../../rtl/common/ram/common_sdp_ram.md) instances. As
for the `phy` elastic buffer's array, no memory macro is specified (PD BKM: memories
stay behavioral until the MAS specifies one), so synthesis maps them to
flops and read multiplexers, about 25 kbit of storage. They dominate the
cell area; a macro would be its own issue, with its MAS change.

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
stays 0.25 ns and the hold exceptions are unchanged. The gate requires
nonnegative setup slack on both clocks, nonnegative hold slack and zero
route DRCs. Inspect the final `6_final.sdc` as well as `metrics.json`.
Study results never replace the nightly 125 MHz measurements.

## Files

| File | Holds |
| --- | --- |
| [`pcie_link.sdc`](../../../../src/asic/link/pcie_link.sdc) | The two clocks, the study opt-in, uncertainty, I/O delays and the crossing exceptions. |
| [`sky130hd.mk`](../../../../src/asic/link/sky130hd.mk) | ORFS design knobs; never a source list. |
