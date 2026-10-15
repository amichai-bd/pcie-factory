# pcie_top_lfsr DV plan

## Strategy

- **Reference model:** [`pcie_top_lfsr_model.py`](../../../../src/dv/top_lfsr/models/pcie_top_lfsr_model.py),
  a bit-serial model written from the [MAS](../../rtl/top_lfsr/MAS.md#behavior), not from the
  RTL. Its pytest unit tests check it against the published key sequence,
  the 65535 period and the control priorities, without a simulator:
  `python3 -m pytest src/dv/top_lfsr/models`.
- **Tests:** [`tests/test_top_lfsr.py`](../../../../src/dv/top_lfsr/tests/test_top_lfsr.py)
  drives `pcie_top_lfsr` directly (no SV testbench; `top_lfsr.dv.f` is empty).
  Inputs change and `key_o` is compared with the model at every falling
  clock edge. Random tests draw from cocotb's seeded `random`, log the seed,
  and fail if any control (advance, re-seed, reset) never fired.
- **Functional coverage:** the `top_lfsr_controls` covergroup, sampled on
  every driven cycle, bins `rst`, `reseed` and `advance` (low, high) and
  crosses all three (8 combinations). No coverage floor is set yet
  ([DV BKM](../../../bkm/dv.md#coverage)).
- **Out of scope:** data, K codes and the SKP/COM rules belong to the
  parent ([top DV plan](../../dv/top/DV_PLAN.md)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset value and published key sequence](../../rtl/top_lfsr/MAS.md#behavior) | `smoke` | 0 | 1; directed: `FF 17 C0 14 B2`, hold, re-seed over advance, reset over advance |
| [Advance, hold, re-seed and reset in any mix](../../rtl/top_lfsr/MAS.md#behavior) | `random_vs_model` | 1 | 3; 4000 random cycles vs the model |
| [Maximal-length sequence and long random runs](../../rtl/top_lfsr/MAS.md#behavior) | `long_random` | 2 | 8; 65535 advances with the wrap back to `FF` checked, then 100000 random cycles vs the model |

Every [interface](../../rtl/top_lfsr/MAS.md#interfaces) port is exercised by all three entries.

Every feature in the MAS has a row; there are no gaps.
