# pcie_link DV plan

> **Draft for wave 3:** only the shell smoke test exists; the back-to-back
> e2e arrives with the stack
> ([#215](https://github.com/amichai-bd/pcie-factory/issues/215)).

## Strategy

- **Shell test:** [`tests/test_link.py`](../../../../src/dv/link/tests/test_link.py)
  drives `pcie_link` directly (no SV testbench; `link.dv.f` is empty).
  With `clk_i` and `ser_rx_clk_i` running and every input but the
  `port_role_i` strap toggled at random, it checks each output with a
  defined [reset value](../../arch/link/HAS.md#reset-values) at every
  falling `clk_i` edge, in both port roles.
- **Functional coverage:** the `link_shell` covergroup bins `port_role`
  (downstream, upstream) and `rst` (low, high), crossed.
- **Planned (wave 3):** the [end-to-end plan](../../arch/link/HAS.md#end-to-end-plan)
  of the HAS: two `link` instances back to back with the line injector,
  every error class and credit configuration, the no-loss, no-duplicate
  and no-reorder scoreboard, the independent DLL link partner
  ([#210](https://github.com/amichai-bd/pcie-factory/issues/210)) as a
  passive second opinion, and the throughput measurement.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../arch/link/HAS.md#reset-values) | `smoke` | 0 | 1; 64 cycles per port role with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Stack wiring](../../arch/link/HAS.md#stack-wiring), [power states](../../arch/link/HAS.md#power-states), [errors](../../arch/link/HAS.md#errors) | [#215](https://github.com/amichai-bd/pcie-factory/issues/215) |
| [Error injection](../../arch/link/HAS.md#error-injection), [credit configurations](../../arch/link/HAS.md#credit-configurations), [checks](../../arch/link/HAS.md#checks) | [#215](https://github.com/amichai-bd/pcie-factory/issues/215) |
| [Throughput targets](../../arch/link/HAS.md#throughput) end to end | [#216](https://github.com/amichai-bd/pcie-factory/issues/216) |
| Coverage floors | [#217](https://github.com/amichai-bd/pcie-factory/issues/217) |
