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
  [architecture model](../../arch/mac/MODEL.md) for the
  Detect.Quiet and P1 codes, which proves the import path the partner
  tests will use.
- **Functional coverage:** the `mac_shell` covergroup bins `port_role`
  (downstream, upstream) and `rst` (low, high), crossed.
- **Partner qualification:** `partner_arc_sweep` (level 2,
  [`tests/test_partner.py`](../../../../src/dv/mac/tests/test_partner.py))
  runs the model's full [arc sweep](../../arch/mac/MODEL.md#arc-sweep) and
  fails unless the partner drives all 64 arcs of the device model in each
  port role. The shell is only clocked; the entry runs at level 2 so the
  `ci-gate` perf run stays short.
- **Planned (wave 2):** the RTL against the Python link partner
  (`pcie_mac_arch.Mac` through `Link`) in both port roles. One covergroup,
  `mac_ltssm_arcs`, has a coverpoint whose bins are the
  [arc list](../../arch/mac/ARCS.md), sampled on every main and
  sub-machine change, a `port_role` coverpoint (downstream, upstream), and
  their **cross**, with a 100% floor at level 2 so every arc is reached in
  each role ([#90](https://github.com/amichai-bd/pcie-factory/issues/90)).
  The tests drive only the partner and the DUT's own inputs: the
  [partner-only recipes](../../arch/mac/MODEL.md#partner-only-recipes) give
  one per arc, including the 255-timeout loop for the two
  `idle_to_rlock` arcs, which the model sweep shortcuts with a device-side
  preset ([#77](https://github.com/amichai-bd/pcie-factory/issues/77),
  [#78](https://github.com/amichai-bd/pcie-factory/issues/78)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell, both roles](../../arch/mac/HAS.md#reset-values) | `smoke` | 0 | 1; 128 cycles with random inputs |
| [The link partner reaches every arc in each role](../../arch/mac/MODEL.md#arc-sweep) | `partner_arc_sweep` | 2 | 1; directed, the seed is unused |

Level 1 has no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Scrambling](../../arch/mac/SYMBOLS.md#scrambling) | [#74](https://github.com/amichai-bd/pcie-factory/issues/74) |
| [Ordered sets and TS fields](../../arch/mac/SYMBOLS.md#ordered-sets) | [#75](https://github.com/amichai-bd/pcie-factory/issues/75) |
| [Framing](../../arch/mac/SYMBOLS.md#framing), [transmit](../../arch/mac/HAS.md#transmit) and [receive](../../arch/mac/HAS.md#receive) streams | [#76](https://github.com/amichai-bd/pcie-factory/issues/76) |
| LTSSM arcs: Detect, Polling, Configuration, L0, Recovery, on the `mac` top (the sub-IP alone: [`mac_ltssm` DV plan](../mac_ltssm/DV_PLAN.md)) | [#78](https://github.com/amichai-bd/pcie-factory/issues/78) |
| The `mac` top against the link partner, [targets](../../arch/mac/HAS.md#targets) | [#78](https://github.com/amichai-bd/pcie-factory/issues/78) |
| LTSSM arcs: L0s, L1, L2 | [#79](https://github.com/amichai-bd/pcie-factory/issues/79) |
| LTSSM arcs: Hot Reset, Disabled, Loopback, Polling.Compliance | [#80](https://github.com/amichai-bd/pcie-factory/issues/80) |
| 100% arc x `port_role` floor and code-coverage floors | [#90](https://github.com/amichai-bd/pcie-factory/issues/90) |
