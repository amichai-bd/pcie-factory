# pcie_tl MAS

> **Draft for wave 2:** a stub until the `tl` RTL arrives.

The wave-2 issues are the receive path
([#290](https://github.com/amichai-bd/pcie-factory/issues/290)), the
transmit path with completions and MSI/MSI-X writes
([#291](https://github.com/amichai-bd/pcie-factory/issues/291)), and
completion tracking and timeout
([#292](https://github.com/amichai-bd/pcie-factory/issues/292)).

How `pcie_tl` implements its [HAS](../../arch/tl/HAS.md): today a
ports-only shell. Each wave-2 issue adds its block's section here: the
receive checks, [routing](../../arch/tl/ROUTING.md) and
[receive queues](../../arch/tl/ORDERING.md#receive-queues) (#290); the
transmit queues and arbiter, [completion forming and
splitting](../../arch/tl/COMPLETIONS.md#completer-completions) and the
messages from `cfg` (#291); the [tracker and
timeout](../../arch/tl/COMPLETIONS.md#requester-completions) (#292).

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/tl/HAS.md#reset) (data outputs to 0, a
don't-care value while not qualified) and folds every input and parameter
into one `unused_inputs` net, so lint stays clean. `BAR0_ADDR_W` is used
as the width of `cq_addr_o`. Every input other than `clk_i` carries its
`cdc-domain` annotation. It instantiates nothing yet.

## Timing

No logic in the shell. The throughput targets are in the
[HAS targets](../../arch/tl/HAS.md#targets).

## Files

| File | Holds |
| --- | --- |
| [`pcie_tl.sv`](../../../../src/rtl/tl/pcie_tl.sv) | The shell module. |
| [`tl.rtl.f`](../../../../src/rtl/tl/tl.rtl.f) | `pcie_tl.sv`. |
