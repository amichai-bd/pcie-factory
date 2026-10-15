# pcie_phy DV plan

> **Draft for wave 2:** only the shell smoke test exists; the e2e arrives
> with [#86](https://github.com/amichai-bd/pcie-factory/issues/86).

## Strategy

- **Shell test:** [`tests/test_phy.py`](../../../../src/dv/phy/tests/test_phy.py)
  drives `pcie_phy` directly (no SV testbench; `phy.dv.f` is empty). With
  `clk_i` and a slightly offset `ser_rx_clk_i` running and every input
  toggled at random, it checks each output against its
  [reset value](../../arch/phy/HAS.md#clocks-and-resets) at every falling
  `clk_i` edge, in both port roles.
- **Functional coverage:** the `phy_shell` covergroup bins `port_role`
  (downstream, upstream) and `rst` (low, high), crossed.
- **Planned (wave 3):** a back-to-back e2e of a downstream and an upstream
  stack over the channel model (±300 ppm, bit slips, symbol errors,
  electrical idle) with a stub upper layer, checked against the
  [architecture model](../../arch/phy/HAS.md#architecture-model) for
  link-up time and throughput (decision record
  [0002](../../decisions/0002-m1-grill.md), Q4).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell, both roles](../../arch/phy/HAS.md#clocks-and-resets) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Packet stream](../../arch/phy/HAS.md#packet-stream), [link-up and requests](../../arch/phy/HAS.md#link-up-and-requests), [targets](../../arch/phy/HAS.md#targets), end to end | [#86](https://github.com/amichai-bd/pcie-factory/issues/86) |
