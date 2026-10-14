# pcie_top MAS

How `pcie_top` implements its [HAS](../arch/HAS.md): one register stage
around the key XOR, with the [lfsr sub-IP](../lfsr/rtl/MAS.md) as the key
source.

## Microarchitecture

`pcie_top_lfsr` (instance `u_lfsr`) with `advance_i = valid_i` and
`reseed_i = reseed_i`. One XOR stage, a `valid_q` register with reset, and a
`data_q` register enabled by `valid_i`.

## Decisions

| Choice | Rationale | Rejected |
| --- | --- | --- |
| Registered outputs, one-cycle latency | Keeps the key XOR inside one register-to-register stage. | Combinational output (hands the XOR depth to the consumer). |
| No package of its own | Port types come from `pcie_top_lfsr_pkg`; the top defines no types the sub-IP lacks. | A top package duplicating `t_byte`. |

## Timing

- Latency and throughput: the [HAS targets](../arch/HAS.md#targets), met
  by the single register stage with no stall condition.
- Critical path: `state_q` of the LFSR through the key logic and the XOR
  into `data_q`.
- No clock-domain crossing. Clock targets and constraints are in the
  [FPGA](../fpga/FPGA.md) and [ASIC](../asic/ASIC.md) pages.

## Files

| File | Holds |
| --- | --- |
| [`pcie_top.sv`](../../../../src/top/rtl/pcie_top.sv) | The module. |
| [`top.rtl.f`](../../../../src/top/rtl/top.rtl.f) | `-F ../lfsr/rtl/lfsr.rtl.f`, then `pcie_top.sv`. |
