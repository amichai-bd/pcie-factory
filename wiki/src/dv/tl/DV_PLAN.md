# pcie_tl DV plan

> **Draft for wave 2:** only the shell smoke test exists.

The unit DV arrives with the RTL
([#290](https://github.com/amichai-bd/pcie-factory/issues/290),
[#291](https://github.com/amichai-bd/pcie-factory/issues/291),
[#292](https://github.com/amichai-bd/pcie-factory/issues/292)).

## Strategy

- **Shell test:** [`tests/test_tl.py`](../../../../src/dv/tl/tests/test_tl.py)
  drives `pcie_tl` directly (no SV testbench; `tl.dv.f` is empty). With
  `clk_i` running, `rst_i` pulsed twice and every other input toggled at
  random, it checks each output with a defined
  [reset value](../../arch/tl/HAS.md#reset) at every falling `clk_i` edge.
- **Functional coverage:** the `tl_shell` covergroup bins `rst` (low,
  high) and `dl_up_i` (down, up), crossed.
- **Planned (wave 2):** `pcie_tl` between a `dll` stream driver and
  monitor, a `cfg` stand-in on the [tl-cfg interface](../../arch/core/TL_CFG.md)
  and an application model on the
  [application interface](../../arch/core/APP.md), judged by the
  independent host model of
  [#288](https://github.com/amichai-bd/pcie-factory/issues/288) (F9):
  every [receive outcome](../../arch/tl/ROUTING.md), completion forming
  and [splitting](../../arch/tl/COMPLETIONS.md#splitting), ordering,
  credit release, the tracker and the timeout.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../arch/tl/HAS.md#reset) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Receive checks and routing](../../arch/tl/ROUTING.md), [receive queues](../../arch/tl/ORDERING.md#receive-queues), [credit release](../../arch/tl/ORDERING.md#credits) | [#290](https://github.com/amichai-bd/pcie-factory/issues/290) |
| [Completer completions](../../arch/tl/COMPLETIONS.md#completer-completions), [ordering](../../arch/tl/ORDERING.md#ordering) and the transmit arbiter, interrupt writes and error messages | [#291](https://github.com/amichai-bd/pcie-factory/issues/291) |
| [Requester completions](../../arch/tl/COMPLETIONS.md#requester-completions) and the [timeout](../../arch/tl/COMPLETIONS.md#completion-timeout) | [#292](https://github.com/amichai-bd/pcie-factory/issues/292) |
| The independent host model | [#288](https://github.com/amichai-bd/pcie-factory/issues/288) |
