# pcie_top DV plan

## Strategy

- **Reference model:** [`pcie_top_model.py`](../../../src/top/dv/models/pcie_top_model.py)
  implements the [HAS](HAS.md#behavior) cycle table. It does not
  re-implement the key: it imports the lfsr sub-IP's model (below). Its
  pytest unit tests run without a simulator:
  `python3 -m pytest src/top/dv/models src/top/lfsr/dv/models`.
- **Tests:** [`tests/test_top.py`](../../../src/top/dv/tests/test_top.py)
  drives `pcie_top` directly (no SV testbench; `top.dv.f` is empty). Inputs
  change and outputs are compared with the model at every falling clock
  edge: `valid_o` always, `data_o` whenever `valid_o` is 1. Random tests
  draw from cocotb's seeded `random`, log the seed, and fail if any of
  bytes, bubbles, re-seeds or resets never occurred.
- **Layers:** LFSR detail (period, key sequence) is covered by the
  [lfsr DV plan](lfsr/DV_PLAN.md); these tests cover the connections and the
  datapath. Regression lists are flat: these entries do not include the
  lfsr's.
- **Out of scope:** 8b/10b, K codes and SKP handling, which this seed does
  not implement ([MAS](MAS.md)).

### Reusing the sub-IP model

`pcie_top_model.py` imports `pcie_top_lfsr_model` from `lfsr/dv/models`
following the [DV BKM](../../bkm/dv.md#reusing-a-sub-ip-model). A change to
the lfsr RTL selects `top` through `top.sim.f`, but a change to only the lfsr
model does not ([regress SPEC known gaps](../../tools/regress/SPEC.md#selection)):
run both IPs after changing it.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Scrambling, bubbles, re-seed after its byte, reset](HAS.md#behavior) | `smoke` | 0 | 1; directed: `00h` stream gives `FF 17 C0`, bubble, re-seed with data `5Ah`, reset |
| [Random stream with all controls](HAS.md#behavior) | `random_vs_model` | 1 | 3; 4000 random cycles vs the model |
| [Long random streams](HAS.md#behavior) | `long_random` | 2 | 8; 150000 random cycles vs the model, rare re-seeds and resets |

Every [interface](HAS.md#interfaces) port is exercised by all three entries.

Every feature in the HAS has a row; there are no gaps.
