# pcie_gpio MAS

**Draft for wave 3:** a stub until the adapter RTL
([#378](https://github.com/amichai-bd/pcie-factory/issues/378)) arrives.

How `pcie_gpio` implements its [HAS](../../arch/gpio/HAS.md): today a
ports-only shell; with #378 the transmit packet buffer and slot
serializer, the receive front end on `gpio_rx_clk_i` with its
[`common_cdc_fifo`](../common/cdc/common_cdc_fifo.md) crossing and
[`common_cdc_rst`](../common/cdc/common_cdc_rst.md) reset, the slot
decoder and receive rules, and the link-up and Hot Reset state machine.
#378 also sets the transmit buffer depth (at least 75 beats) and the
latencies `L_tx` and `L_rx` the HAS [timing](../../arch/gpio/HAS.md#timing)
names. It must keep the one-way latency `W` at most 4 slots, as the HAS
[link-up](../../arch/gpio/HAS.md#link-up) rules require.

## Microarchitecture

The shell drives every output to its
[reset value](../../arch/gpio/HAS.md#reset-values) (data outputs to 0, a
don't-care value while not qualified) and folds its inputs into two
unused nets, one per clock domain, so lint stays clean. A parameter check
rejects a `CLK_DIV` outside the HAS range. Every input other than the two
clocks carries its `cdc-domain` annotation. It instantiates nothing yet.

## Timing

No logic in the shell.

## Files

| File | Holds |
| --- | --- |
| [`pcie_gpio.sv`](../../../../src/rtl/gpio/pcie_gpio.sv) | The shell module. |
| [`gpio.rtl.f`](../../../../src/rtl/gpio/gpio.rtl.f) | The shared vocabulary, then `pcie_gpio.sv`. |
