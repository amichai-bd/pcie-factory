# pcie_pcs_enc8b10b DV plan

## Strategy

- **Reference:** the `pcs` [architecture model](../../arch/pcs/HAS.md#architecture-model)'s
  `tx_encode` encodes every word. It is built from the
  [8b/10b code page](../../arch/pcs/8B10B.md) and applies TxCompliance and
  the unsupported-K rule. The cycle model
  [`pcie_pcs_enc8b10b_model.py`](../../../../src/dv/pcs_enc8b10b/models/pcie_pcs_enc8b10b_model.py)
  imports it and adds only the [MAS](../../rtl/pcs_enc8b10b/MAS.md#behavior)
  pipeline, RD hold and reset. Its unit tests
  (`python3 -m pytest src/dv/pcs_enc8b10b/models`) compare five published
  anchors (K28.5, K28.0, D21.5, D10.2, D0.0, both RD) in both lanes and
  check compliance, EDB and the pipeline.
- **Tests:** [`tests/test_pcs_enc8b10b.py`](../../../../src/dv/pcs_enc8b10b/tests/test_pcs_enc8b10b.py)
  drives `pcie_pcs_enc8b10b` directly (`pcs_enc8b10b.dv.f` is empty). It
  drives at the falling edge and, every cycle, compares `valid_o`, and
  `data_o` while valid, with the model.
- **Exhaustive set:** for each of the 512 (byte, K flag) pairs, both lanes
  and both starting RD values, words are built to put that RD before that
  lane: compliance sets RD-, D21.5 keeps RD, K28.5 turns RD- into RD+. The
  set covers all 256 data bytes, the 12 valid K codes and the 244
  unsupported K bytes, which must give EDB. That also checks the package's
  `K_EDB` against the model's. The set runs back to back, then again with
  an idle cycle carrying RD-flipping data after each word.
- **Assertions:** the RTL's `COMMON_ASSERT*` checks (disparity per lane,
  run length) are live in every simulation ([MAS](../../rtl/pcs_enc8b10b/MAS.md#microarchitecture)).
- **Functional coverage:**

  | Covergroup | Contents |
  | --- | --- |
  | `pcs_enc8b10b_codes` | Every valid symbol (268 tuple bins) × RD × lane: 1072 crosses. |
  | `pcs_enc8b10b_controls` | Reset, valid, compliance × the RD held before the word, the RD before the word × the RD between lanes, and EDB substitution per lane. |
  | `pcs_enc8b10b_idle` | RD held across an idle cycle, at each RD. |

  `exhaustive` fails on any hole in the codes cross or the EDB lanes.
  `level1.yaml` sets floors of line 98, toggle 99 and functional 100. The
  one line point and two toggle points left are `run6`, read only by
  `a_run_length`: a correct encoder never sets it.
- **Mutation evidence** (scratch RTL edits, L0 + L1 at the regression
  seeds, then reverted):

  | Mutation | Caught by |
  | --- | --- |
  | A7 rule dropped at RD+ (D11.7, D13.7, D14.7) | `exhaustive`, `random_vs_model` (all seeds) |
  | TxCompliance ignored | `smoke`, `exhaustive`, `random_vs_model` |
  | RD updated on idle cycles | `smoke`, `exhaustive`, `random_vs_model` |
  | K28 balanced 4b sub-block not complemented at RD+ | `smoke`, `exhaustive`, `random_vs_model` |

  Development also hit a real bug: alternating sub-blocks flipped RD.
  `a_lane0_disparity` stopped `exhaustive` on it.
- **Out of scope:** what is sent (loopback select), electrical idle on
  the line and PowerDown belong to the parent
  ([`pcs` DV plan](../pcs/DV_PLAN.md)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset, latency, anchors, compliance, EDB, RD hold](../../rtl/pcs_enc8b10b/MAS.md#behavior) | `smoke` | 0 | 1; directed |
| [Every symbol at both RD in both lanes, EDB for unsupported K, RD hold](../../rtl/pcs_enc8b10b/MAS.md#behavior) | `exhaustive` | 1 | 1; 5120 words, twice; coverage holes fail |
| [RD carry across lanes, cycles and idle, compliance, reset in any mix](../../rtl/pcs_enc8b10b/MAS.md#behavior) | `random_vs_model` | 1 | 3; 5000 random cycles |
| [The same, long runs](../../rtl/pcs_enc8b10b/MAS.md#behavior) | `long_random` | 2 | 8; 100000 random cycles |

Every [interface](../../rtl/pcs_enc8b10b/MAS.md#interfaces) port is
exercised by every entry. Every feature in the MAS has a row; there are no
gaps.
