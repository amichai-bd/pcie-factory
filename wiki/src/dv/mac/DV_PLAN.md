# pcie_mac DV plan

> **Draft for wave 2:** only the shell smoke test exists; partner tests
> arrive with
> [#78](https://github.com/amichai-bd/pcie-factory/issues/78).

## Strategy

- **Shell test:** [`tests/test_mac.py`](../../../../src/dv/mac/tests/test_mac.py)
  drives `pcie_mac` directly (no SV testbench; `mac.dv.f` is empty). With
  every input toggled at random, it checks each output against its
  [reset value](../../arch/mac/HAS.md#reset-values) at every falling
  `clk_i` edge, in both port roles. It imports the
  [architecture model](../../arch/mac/HAS.md#architecture-model) for the
  Detect.Quiet and P1 codes, which proves the import path the partner
  tests will use.
- **Functional coverage:** the `mac_shell` covergroup bins `port_role`
  (downstream, upstream) and `rst` (low, high), crossed.
- **Planned (wave 2):** the RTL against the Python link partner
  (`pcie_mac_arch.Mac` through `Link`) in both port roles, with the
  injection hooks; one covergroup, `mac_ltssm_arcs`, whose bins are the
  [arc list](../../arch/mac/LTSSM.md#arcs), sampled on every main and
  sub-machine change, with a 100% floor at level 2
  ([#90](https://github.com/amichai-bd/pcie-factory/issues/90)). The
  [arc sweep](../../../../src/arch/mac/pcie_mac_sweep.py) gives a recipe
  per arc.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell, both roles](../../arch/mac/HAS.md#reset-values) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Scrambling](../../arch/mac/SYMBOLS.md#scrambling) | [#74](https://github.com/amichai-bd/pcie-factory/issues/74) |
| [Ordered sets and TS fields](../../arch/mac/SYMBOLS.md#ordered-sets) | [#75](https://github.com/amichai-bd/pcie-factory/issues/75) |
| [Framing](../../arch/mac/SYMBOLS.md#framing), [transmit](../../arch/mac/HAS.md#transmit) and [receive](../../arch/mac/HAS.md#receive) streams | [#76](https://github.com/amichai-bd/pcie-factory/issues/76) |
| LTSSM arcs: Detect, Polling, Configuration, L0, Recovery | [#77](https://github.com/amichai-bd/pcie-factory/issues/77) |
| The `mac` top against the link partner, [targets](../../arch/mac/HAS.md#targets) | [#78](https://github.com/amichai-bd/pcie-factory/issues/78) |
| LTSSM arcs: L0s, L1, L2 | [#79](https://github.com/amichai-bd/pcie-factory/issues/79) |
| LTSSM arcs: Hot Reset, Disabled, Loopback, Polling.Compliance | [#80](https://github.com/amichai-bd/pcie-factory/issues/80) |
| 100% arc floor and code-coverage floors | [#90](https://github.com/amichai-bd/pcie-factory/issues/90) |
