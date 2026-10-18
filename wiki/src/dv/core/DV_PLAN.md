# pcie_core DV plan

> **Draft for wave 3:** only the shell smoke test exists; the e2e arrives
> with the stack ([#293](https://github.com/amichai-bd/pcie-factory/issues/293)).

## Strategy

- **Shell test:** [`tests/test_core.py`](../../../../src/dv/core/tests/test_core.py)
  drives `pcie_core` directly (no SV testbench; `core.dv.f` is empty).
  With `clk_i` and `ser_rx_clk_i` running, `rst_i` pulsed twice and every
  other input toggled at random, it checks each output with a defined
  [reset value](../../arch/core/HAS.md#reset) at every falling `clk_i`
  edge.
- **Functional coverage:** the `core_shell` covergroup bins `rst` (low,
  high) and the application offering a request, an interrupt, both or
  neither, crossed.
- **Host model:** the [independent host](HOST.md) (root complex, F9),
  written apart from the `tl` and `cfg` arch models
  ([#288](https://github.com/amichai-bd/pcie-factory/issues/288)): a
  pure-Python model,
  [`pcie_core_host_model.py`](../../../../src/dv/core/models/pcie_core_host_model.py),
  with its own unit tests, which `./build ci unit` runs (no regression
  entry).
- **Planned (wave 3):** `core` back to back with that host model, and an
  application model on the [application interface](../../arch/core/APP.md): enumeration,
  BAR0 traffic, bus-master traffic, MSI and MSI-X, Unsupported Request,
  completion timeout and the error cases of goal items 4 to 6.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../arch/core/HAS.md#reset) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Stack wiring](../../arch/core/HAS.md#stack-wiring), [request routing](../../arch/core/HAS.md#request-routing), [credits](../../arch/core/HAS.md#credits), [link down](../../arch/core/HAS.md#link-down) | [#293](https://github.com/amichai-bd/pcie-factory/issues/293) |
| [Application interface](../../arch/core/APP.md), [tl-cfg interface](../../arch/core/TL_CFG.md) end to end | [#293](https://github.com/amichai-bd/pcie-factory/issues/293) |
| Coverage floors | [#294](https://github.com/amichai-bd/pcie-factory/issues/294) |
