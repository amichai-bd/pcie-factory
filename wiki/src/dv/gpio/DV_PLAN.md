# pcie_gpio DV plan

**Draft for wave 3:** only the shell smoke test exists; the adapter tests
arrive with the RTL
([#378](https://github.com/amichai-bd/pcie-factory/issues/378)).

## Strategy

- **Shell test:** [`tests/test_gpio.py`](../../../../src/dv/gpio/tests/test_gpio.py)
  drives `pcie_gpio` directly (no SV testbench; `gpio.dv.f` is empty). With
  `clk_i` and `gpio_rx_clk_i` running, `rst_i` pulsed twice and every other
  input toggled at random on its own clock, it checks each qualified,
  status and pin output against its
  [reset value](../../arch/gpio/HAS.md#reset-values) at every falling
  `clk_i` edge.
- **Functional coverage:** the `gpio_shell` covergroup bins `rst` (low,
  high) and the `dll` offering a beat, a Hot Reset request, both or
  neither, crossed.
- **Planned (#378):** two `pcie_gpio` instances back to back, each on its
  own clock, joined pin to pin, checked against a reference model written
  from the HAS: the [slots](../../arch/gpio/HAS.md#slots) and
  [idle words](../../arch/gpio/HAS.md#idle-words), the
  [link-up](../../arch/gpio/HAS.md#link-up) handshake with skew and clock
  offset, the [transmit](../../arch/gpio/HAS.md#transmit) ready rule, the
  [receive](../../arch/gpio/HAS.md#receive) table under injected slot
  damage, [Hot Reset](../../arch/gpio/HAS.md#hot-reset), and `pcie_dll`
  over the pair; then the `link` and `core` board-build pair.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../arch/gpio/HAS.md#reset-values) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Pins and transfers](../../arch/gpio/HAS.md#pins-and-transfers), [slots](../../arch/gpio/HAS.md#slots), [link-up](../../arch/gpio/HAS.md#link-up), [transmit](../../arch/gpio/HAS.md#transmit), [receive](../../arch/gpio/HAS.md#receive), [Hot Reset](../../arch/gpio/HAS.md#hot-reset) | [#378](https://github.com/amichai-bd/pcie-factory/issues/378) |
| The `link` [lower layer](../../arch/link/HAS.md#lower-layer) and the `rc` and `core` board-build pair | [#378](https://github.com/amichai-bd/pcie-factory/issues/378) |
| Coverage floors | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |
