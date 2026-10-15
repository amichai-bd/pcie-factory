# pcie_pcs MAS

> **Draft for wave 2:** a stub until the `pcs` top
> ([#85](https://github.com/amichai-bd/pcie-factory/issues/85)) arrives.

How `pcie_pcs` implements its [HAS](../../arch/pcs/HAS.md): today a
ports-only shell; later the four sub-IPs, PIPE control and the crossing
cells of the [HAS crossing table](../../arch/pcs/HAS.md#clock-domain-crossings).

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/pcs/HAS.md#reset-values) and folds every input into
one `unused_inputs` net so lint stays clean. Every input other than the two
clocks carries its `cdc-domain` annotation from the HAS
[port table](../../arch/pcs/HAS.md#interfaces).

Wave-2 sub-IPs are instantiated as they arrive, with their outputs folded
into `unused_inputs` until the top connects them; the shell's outputs do
not change. Today they are the encoder
[`pcs_enc8b10b`](../pcs_enc8b10b/MAS.md) (`u_enc`): reset while `rst_i` is 1
or `pipe_reset_ni` is 0, a word valid in P0 with `pipe_tx_elec_idle_i` 0,
fed from `pipe_tx_data_i`, `pipe_tx_datak_i` and `pipe_tx_compliance_i`;
and the [elastic buffer](../pcs_elastic/MAS.md) (`u_elastic`) with its
write side idle (`ser_rx_valid_i` 0), so lint and the CDC check see its
crossings.

## Timing

The shell's only logic is the encoder and the elastic buffer; the only
clock-domain crossings are the elastic buffer's
([timing](../pcs_elastic/MAS.md#timing)). The latency budgets
are in the [HAS targets](../../arch/pcs/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_pcs.sv`](../../../../src/rtl/pcs/pcie_pcs.sv) | The shell module. |
| [`pcs.rtl.f`](../../../../src/rtl/pcs/pcs.rtl.f) | `-F ../pcs_enc8b10b/pcs_enc8b10b.rtl.f`, `-F ../pcs_elastic/pcs_elastic.rtl.f`, then `pcie_pcs.sv`. |
