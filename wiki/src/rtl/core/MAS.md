# pcie_core MAS

> **Draft for wave 3:** a stub until the stack integration
> ([#293](https://github.com/amichai-bd/pcie-factory/issues/293)) arrives.

How `pcie_core` implements its [HAS](../../arch/core/HAS.md): today a
ports-only shell; in wave 3 the integration of `pcie_tl` and `pcie_cfg`
over `pcie_link` by the [stack wiring](../../arch/core/HAS.md#stack-wiring)
and the [tl-cfg interface](../../arch/core/TL_CFG.md)
([#293](https://github.com/amichai-bd/pcie-factory/issues/293)), with
`core.rtl.f` reaching `tl.rtl.f`, `cfg.rtl.f` and `link.rtl.f` through
`-F`. Until then `core.rtl.f` lists the shell only, as the `link` shell's
did.

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/core/HAS.md#reset) (data outputs to 0, a
don't-care value while not qualified; `max_read_req_o` to 2 and
`ser_tx_elec_idle_o` to 1) and folds every input and parameter into two
`unused_*` nets, one per clock domain, so lint stays clean. `BAR0_ADDR_W`
is used as the width of `cq_addr_o`. Every input other than the clocks
carries its `cdc-domain` annotation. It instantiates nothing yet.

## Timing

No logic in the shell. The throughput and clock targets are in the
[HAS targets](../../arch/core/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_core.sv`](../../../../src/rtl/core/pcie_core.sv) | The shell module. |
| [`core.rtl.f`](../../../../src/rtl/core/core.rtl.f) | `pcie_core.sv`. |
