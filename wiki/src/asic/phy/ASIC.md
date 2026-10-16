# pcie_phy ASIC

`pcie_phy` is hardened on sky130hd by the
[ASIC flow](../../../tools/asic/SPEC.md) as an estimation and quality
signal, following the [PD BKM](../../../bkm/pd.md). The flow reads
`phy.rtl.f` (the `mac` and `pcs` filelists, then the stack top) and only the
files below.

## Clock target

The [HAS clock target](../../arch/phy/HAS.md#targets): `clk_i` and
`ser_rx_clk_i` both at 125 MHz (8 ns), as the periods in
[`pcie_phy.sdc`](../../../../src/asic/phy/pcie_phy.sdc). These remain the default, nightly and final clocks.
The captain's intermediate checkpoint is **100 MHz (10 ns) on both domains**
([Q27](../../decisions/0002-m1-grill.md#decisions)); it is an explicit study,
not final Gen1 timing closure. [#235](https://github.com/amichai-bd/pcie-factory/issues/235)
continues to own the 125 MHz target.

| SDC clock | Port | Period | Runs |
| --- | --- | --- | --- |
| `clk` | `clk_i` | 8 ns | The MAC, PIPE, the PCS transmit path and the elastic buffer's read side. |
| `rx_clk` | `ser_rx_clk_i` | 8 ns | The PCS receive front end: the aligner and the elastic buffer's write side. |

## Constraints

- Clock uncertainty 0.25 ns on both clocks before clock-tree synthesis.
- I/O budget: each input arrives, and each output is needed, at 20% of the
  period of the clock its port is on; the neighbouring logic is assumed to
  use the rest. Per the [HAS port tables](../../arch/phy/HAS.md#interfaces),
  `ser_rx_data_i` and `ser_rx_elec_idle_i` are on `rx_clk`; every other
  input and every output is on `clk`.
- **Clock relationship: asynchronous.** The two clocks come from different
  references within ±600 ppm of each other, so they have no phase
  relationship ([HAS, Clocks and resets](../../arch/phy/HAS.md#clocks-and-resets)).
  Every crossing is inside `pcie_pcs`, through an approved `common_cdc_*`
  cell ([MAS, Timing](../../rtl/phy/MAS.md#timing)). The SDC therefore
  replaces the same-edge checks between the clocks with two exceptions:

  | Exception | Reason |
  | --- | --- |
  | `set_max_delay -ignore_clock_latency` of one period (8 ns) on every path from `clk` to `rx_clk` and from `rx_clk` to `clk` | Bounds each crossing to one cycle of the capturing clock: the skew between the bits of a synchronized Gray pointer, the elastic-buffer memory read that the pointer guards, and the RX reset's asynchronous assertion from `rst_i`. `-ignore_clock_latency` (an OpenSTA option, as in SDC 2.1) leaves the clock trees out, so the bound is on the data path alone, whatever the skew between the two trees. |
  | `set_false_path -hold` between the clocks, both directions | With no phase relationship a hold check has no meaning; the synchronizers resolve the arrival. |

  Inside each domain every path is timed normally, including the RX
  reset's synchronous release. The exceptions are per clock pair, so they
  would also bound an unsynchronized crossing, if one were added, without
  flagging it. The guard against that is `./build check`: Verilator lint
  and the structural CDC check, which require every crossing to go through
  an approved cell.

## Flow configuration

`DESIGN_NAME` is `pcie_phy`, at its default parameters (`TIMEOUT_DIV` and
`ERR_TIMEOUT_DIV` 1, as the HAS states for ASIC builds). The RTL uses
package types in its port lists and typed parameters, so the flow reads it
with the slang SystemVerilog frontend (`SYNTH_HDL_FRONTEND = slang`). The
core is sized from the cell area at 30% utilization, with placement-density
headroom for the two clock trees and resizing buffers.

## Running

`./build asic --ip phy` on the CI host, or `--dry-run` anywhere to write the
generated config. Nightly runs it with every IP that has `src/asic/<ip>/`.
Results, including the setup WNS and TNS of each clock
(`timing.clocks.clk` and `timing.clocks.rx_clk`), are in `metrics.json` and,
from the nightly run, on the tracker's `phy` page. The per-clock values come
from the flow's separate `clocks` step and are required
([#200](https://github.com/amichai-bd/pcie-factory/issues/200)): if that
step fails, times out or writes no clock, the run fails, and `metrics.json`
still holds the other results.

The intermediate checkpoint runs with
`./build asic --ip phy --study-clock-period 10`. Its results are isolated in
`workdir/phy/asic-study/10ns/`, with both clocks at 10 ns, I/O delays at 2 ns,
and crossing max delays at 10 ns. Uncertainty stays 0.25 ns and the hold
exceptions are unchanged. A checkpoint requires nonnegative setup slack on
both clocks, nonnegative hold slack and zero route DRCs. Inspect the final
`6_final.sdc` as well as `metrics.json`; result evidence belongs on
[#248](https://github.com/amichai-bd/pcie-factory/issues/248), not the default
nightly tracker.

## Files

| File | Holds |
| --- | --- |
| [`pcie_phy.sdc`](../../../../src/asic/phy/pcie_phy.sdc) | The two clocks, uncertainty, I/O delays and the crossing exceptions. |
| [`sky130hd.mk`](../../../../src/asic/phy/sky130hd.mk) | ORFS design knobs; never a source list. |
