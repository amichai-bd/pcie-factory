# pcie_cfg MAS

> **Draft for wave 2:** a stub until the register file
> ([#289](https://github.com/amichai-bd/pcie-factory/issues/289)) arrives.

How `pcie_cfg` implements its [HAS](../../arch/cfg/HAS.md): today a
ports-only shell; in wave 2 the register file of
[REGISTERS.md](../../arch/cfg/REGISTERS.md) and
[CAPS.md](../../arch/cfg/CAPS.md), the MSI-X table in a `common` RAM (F4),
the error decisions and the message and interrupt logic
([#289](https://github.com/amichai-bd/pcie-factory/issues/289)).

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/cfg/HAS.md#reset) (data outputs to 0, a
don't-care value while not qualified; `max_read_req_o` to 2) and folds
every input and parameter into one `unused_inputs` net so lint stays
clean. Every input other than the clock carries its `cdc-domain`
annotation. It instantiates nothing yet.

## Timing

No logic in the shell. The latency targets are in the
[HAS targets](../../arch/cfg/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_cfg.sv`](../../../../src/rtl/cfg/pcie_cfg.sv) | The shell module. |
| [`cfg.rtl.f`](../../../../src/rtl/cfg/cfg.rtl.f) | `pcie_cfg.sv`. |
