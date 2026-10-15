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
[port table](../../arch/pcs/HAS.md#interfaces). It instantiates nothing yet.

## Timing

No logic and no clock-domain crossing in the shell. The latency budgets
are in the [HAS targets](../../arch/pcs/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_pcs.sv`](../../../../src/rtl/pcs/pcie_pcs.sv) | The shell module. |
| [`pcs.rtl.f`](../../../../src/rtl/pcs/pcs.rtl.f) | `-F ../pcs_align/pcs_align.rtl.f` (the [`pcs_align`](../pcs_align/MAS.md) sub-IP, not yet instantiated), then `pcie_pcs.sv`. |
