# pcie_phy MAS

> **Draft for wave 2:** a stub until the stack integration
> ([#86](https://github.com/amichai-bd/pcie-factory/issues/86)).

How `pcie_phy` implements its [HAS](../../arch/phy/HAS.md): today a
ports-only shell; later the `pcie_mac` and `pcie_pcs` instances joined by
the [PIPE nets](../../arch/phy/PIPE.md#signals), with no logic of its own.

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/phy/HAS.md#clocks-and-resets) and folds every
input, and `TIMEOUT_DIV`, into one `COMMON_UNUSED_BITS` sink `unused_inputs`
so lint stays clean (synthesis sees no sink).
It instantiates nothing yet: `mac` and `pcs` do not exist until
[#72](https://github.com/amichai-bd/pcie-factory/issues/72) and
[#73](https://github.com/amichai-bd/pcie-factory/issues/73), and the `-F`
lines that join them arrive with
[#86](https://github.com/amichai-bd/pcie-factory/issues/86).

## Timing

No logic and no clock-domain crossing in the shell. The stack's clocks and
latency budgets are in the [HAS targets](../../arch/phy/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_phy.sv`](../../../../src/rtl/phy/pcie_phy.sv) | The shell module. |
| [`phy.rtl.f`](../../../../src/rtl/phy/phy.rtl.f) | `-F ../common/common_macros.rtl.f` (the RTL vocabulary), then `pcie_phy.sv`. |
