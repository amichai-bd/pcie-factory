# pcie_pcs DV plan

> **Draft for wave 2:** only the shell smoke test exists; the unit tests
> arrive with the `pcs` top
> ([#85](https://github.com/amichai-bd/pcie-factory/issues/85)).

## Strategy

- **Shell test:** [`tests/test_pcs.py`](../../../../src/dv/pcs/tests/test_pcs.py)
  drives `pcie_pcs` directly (no SV testbench; `pcs.dv.f` is empty). With
  `clk_i` and a slightly offset `ser_rx_clk_i` running and every input
  toggled at random, it checks each output with a defined
  [reset value](../../arch/pcs/HAS.md#reset-values) at every falling `clk_i`
  edge, through two reset pulses.
- **Functional coverage:** the `pcs_shell` covergroup bins `rst` (low,
  high) and `power_down` (P0, P0s, P1, P2), crossed.
- **Planned (wave 2):** each sub-IP against its MAS with the
  [architecture model](../../arch/pcs/HAS.md#architecture-model)'s 8b/10b
  code as a second reference; the `pcs` top against a Python model of the
  [receive path](../../arch/pcs/RECEIVE.md) and
  [PIPE control](../../arch/pcs/PIPE_CONTROL.md), with a channel model
  (±300 ppm per end, bit slips, symbol errors, electrical idle) and
  randomized clock ratios (decision record
  [0002](../../decisions/0002-m1-grill.md), Q12 and Q17).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../arch/pcs/HAS.md#reset-values) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [8b/10b encoder](../../arch/pcs/8B10B.md) in the `pcs` top (unit-tested in the [`pcs_enc8b10b` DV plan](../pcs_enc8b10b/DV_PLAN.md)) | [#85](https://github.com/amichai-bd/pcie-factory/issues/85) |
| [8b/10b decoder](../../arch/pcs/8B10B.md#decoder-and-error-classes) in the `pcs` top (unit-tested in the [`pcs_dec8b10b` DV plan](../pcs_dec8b10b/DV_PLAN.md)) | [#85](https://github.com/amichai-bd/pcie-factory/issues/85) |
| [Comma alignment and symbol lock](../../arch/pcs/RECEIVE.md#comma-alignment-and-symbol-lock) | [#83](https://github.com/amichai-bd/pcie-factory/issues/83) |
| [Elastic buffer](../../arch/pcs/RECEIVE.md#elastic-buffer) | [#84](https://github.com/amichai-bd/pcie-factory/issues/84) |
| [PIPE control](../../arch/pcs/PIPE_CONTROL.md), [RxStatus](../../arch/pcs/RECEIVE.md#decode-and-rxstatus), [targets](../../arch/pcs/HAS.md#targets), crossings | [#85](https://github.com/amichai-bd/pcie-factory/issues/85) |
