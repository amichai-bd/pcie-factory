# pcie_link MAS

> **Draft for wave 3:** a stub until the stack integration
> ([#215](https://github.com/amichai-bd/pcie-factory/issues/215)) arrives.

How `pcie_link` implements its [HAS](../../arch/link/HAS.md): today a
ports-only shell; in wave 3 the thin integration of `pcie_dll` over
`pcie_phy` by the [stack wiring](../../arch/link/HAS.md#stack-wiring)
([#215](https://github.com/amichai-bd/pcie-factory/issues/215)), with
`link.rtl.f` reaching `dll.rtl.f` and `phy.rtl.f` through `-F`.

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/link/HAS.md#reset-values) (`ser_tx_data_o` to 0,
a don't-care value in electrical idle) and folds every input and parameter
into two `unused_*` nets, one per clock domain, so lint stays clean. Every
input other than the clocks carries its `cdc-domain` annotation. It
instantiates nothing yet.

## Timing

No logic in the shell. The latency budgets and clock targets are in the
[HAS targets](../../arch/link/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_link.sv`](../../../../src/rtl/link/pcie_link.sv) | The shell module. |
| [`link.rtl.f`](../../../../src/rtl/link/link.rtl.f) | `pcie_link.sv`. |
