# pcie_top ASIC

`pcie_top` is hardened on sky130hd by the
[ASIC flow](../../../tools/asic/SPEC.md) as an estimation and quality
signal, following the [PD BKM](../../../bkm/pd.md). The flow reads
`top.rtl.f` and only the files below.

## Clock target

The [HAS clock target](../../arch/top/HAS.md#targets) on `clk_i`, as the period
in [`pcie_top.sdc`](../../../../src/asic/top/pcie_top.sdc). This is the
study clock; a different study clock is a run option, never an edit to this
target.

## Constraints

- Clock uncertainty 0.25 ns before clock-tree synthesis.
- Every input except `clk_i` arrives, and every output is needed, at 20% of
  the period; the neighbouring logic is assumed to use the rest.
- No timing exceptions.

## Flow configuration

`DESIGN_NAME` is `pcie_top`. The RTL references package types in its port
list and uses typed localparams, so the flow reads it with the slang
SystemVerilog frontend (`SYNTH_HDL_FRONTEND = slang`). The core is sized
from the cell area at 30% utilization, with placement-density headroom for
clock-tree and resizing buffers.

## Running

`./build asic --ip top` on the CI host, or `--dry-run` anywhere to write the
generated config. Area and timing results are in `metrics.json` and, from
the nightly run, the tracker.

## Files

| File | Holds |
| --- | --- |
| [`pcie_top.sdc`](../../../../src/asic/top/pcie_top.sdc) | Clock, uncertainty and I/O delays. |
| [`sky130hd.mk`](../../../../src/asic/top/sky130hd.mk) | ORFS design knobs; never a source list. |
