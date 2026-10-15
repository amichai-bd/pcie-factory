# pcie_pcs MAS

> **Draft for wave 2:** a stub until the `pcs` top
> ([#85](https://github.com/amichai-bd/pcie-factory/issues/85)) arrives.

How `pcie_pcs` implements its [HAS](../../arch/pcs/HAS.md): today a
ports-only shell; later the four sub-IPs, PIPE control and the crossing
cells of the [HAS crossing table](../../arch/pcs/HAS.md#clock-domain-crossings).

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/pcs/HAS.md#reset-values). Every input other than
the two clocks carries its `cdc-domain` annotation from the HAS
[port table](../../arch/pcs/HAS.md#interfaces). It instantiates one
sub-IP, the [elastic buffer](../pcs_elastic/MAS.md) `u_elastic`, with its
write side idle (`ser_rx_valid_i` 0) and its outputs unused, so lint and
the CDC check see its crossings until the top integrates it
([#85](https://github.com/amichai-bd/pcie-factory/issues/85)). The unused
inputs and outputs go to one `COMMON_UNUSED_BITS` sink.

## Timing

No logic in the shell; its only crossings are the elastic buffer's
([timing](../pcs_elastic/MAS.md#timing)). The latency budgets
are in the [HAS targets](../../arch/pcs/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_pcs.sv`](../../../../src/rtl/pcs/pcie_pcs.sv) | The shell module. |
| [`pcs.rtl.f`](../../../../src/rtl/pcs/pcs.rtl.f) | `-F ../pcs_elastic/pcs_elastic.rtl.f`, then `pcie_pcs.sv`. |
