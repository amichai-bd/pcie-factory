# pcie_dll MAS

> **Draft for wave 2:** a stub until the `dll` top
> ([#211](https://github.com/amichai-bd/pcie-factory/issues/211)) arrives.

How `pcie_dll` implements its [HAS](../../arch/dll/HAS.md): today a
ports-only shell; in wave 2 the top with DLLP and data link control
([#211](https://github.com/amichai-bd/pcie-factory/issues/211)), the
transmit replay path ([#212](https://github.com/amichai-bd/pcie-factory/issues/212)),
the receive check and Ack/Nak path
([#213](https://github.com/amichai-bd/pcie-factory/issues/213)) and flow
control ([#214](https://github.com/amichai-bd/pcie-factory/issues/214)),
each a file of `dll` with its own section here.

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/dll/HAS.md#reset-values) and folds every input
into one `unused_inputs` net so lint stays clean, and checks each `FC_*`
parameter against its [HAS range](../../arch/dll/HAS.md#parameters) with
`COMMON_ASSERT_PARAM`, so lint and the simulation build reject a bad one. Every input
other than the clock carries its `cdc-domain` annotation. It instantiates
nothing yet.

## Timing

No logic in the shell. The latency budgets are in the
[HAS targets](../../arch/dll/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_dll.sv`](../../../../src/rtl/dll/pcie_dll.sv) | The shell module. |
| [`dll.rtl.f`](../../../../src/rtl/dll/dll.rtl.f) | The shared vocabulary (`common_macros.rtl.f`), then `pcie_dll.sv`. |
