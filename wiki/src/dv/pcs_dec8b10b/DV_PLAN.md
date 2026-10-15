# pcie_pcs_dec8b10b DV plan

## Strategy

- **Reference model:** [`pcie_pcs_dec8b10b_model.py`](../../../../src/dv/pcs_dec8b10b/models/pcie_pcs_dec8b10b_model.py).
  It is a cycle model of the [MAS](../../rtl/pcs_dec8b10b/MAS.md#behavior).
  It imports the `pcs` architecture model's code
  ([`pcie_pcs_8b10b.py`](../../../../src/arch/pcs/pcie_pcs_8b10b.py):
  `decode()`, `encode()` and its tables, computed from
  [8B10B.md](../../arch/pcs/8B10B.md)) and adds only polarity, two lanes
  and the RD-known rules. Its pytest unit tests run without a simulator
  (`python3 -m pytest src/dv/pcs_dec8b10b/models`). They check K28.5 against
  its published codes, the column sizes (268 codes per RD), and that
  `K_EDB` is `0xFE`, the RTL package's value.
- **Tests:** [`tests/test_pcs_dec8b10b.py`](../../../../src/dv/pcs_dec8b10b/tests/test_pcs_dec8b10b.py)
  drives `pcie_pcs_dec8b10b` directly. There is no SV testbench, and
  `pcs_dec8b10b.dv.f` is empty. Inputs change at the falling edge, and every
  output of both lanes is compared with the model 1 ns later, every cycle,
  in every test. Each mismatch names the cycle, lane, symbol, lock,
  generation, polarity and the RD used.
- **RD observation:** RD is not a port. In the exhaustive test, a probe
  symbol follows every tested code. The probe is D0.0's RD- form, valid
  only at RD-, so its disparity flag shows the RD the tested code left.
- **Random tests** draw from cocotb's seeded `random` and log the seed.
  They fail if any event never happened: a valid gap, a lock drop, a
  generation change, a polarity change, a reset, a code error or a
  disparity error.
- **Functional coverage:**
  - `pcs_dec8b10b_class`: lane × (RD neg, pos or unknown × valid,
    disparity or code error; there is no unknown/disparity bin), and data
    or K.
  - `pcs_dec8b10b_rd_unknown`: why a symbol's RD was unknown (unlocked,
    lane 0 unlocked, generation change, polarity change, after reset, after
    an unlocked word), and the lane.
  - `pcs_dec8b10b_recovery`: error class × the RD it left × carried to the
    next lane or the next word.
- **Floors:** line, toggle and functional at 100% in `level1.yaml` and
  `level2.yaml`. Each level reaches 100% on its own (89 lines, 210 toggle
  points, 50 functional bins).
- **Assertions:** the three RTL `COMMON_ASSERT_NEVER` invariants
  ([MAS](../../rtl/pcs_dec8b10b/MAS.md#behavior)) are active in every
  simulation.
- **Out of scope:** RxValid, RxStatus, the error counter and an
  encoder-to-decoder round trip belong to the `pcs` top
  ([DV plan](../pcs/DV_PLAN.md), [#85](https://github.com/amichai-bd/pcie-factory/issues/85)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Commas at both RDs, disparity error, code error to EDB, reset](../../rtl/pcs_dec8b10b/MAS.md#behavior) | `smoke` | 0 | 1; directed |
| [Every code in both lanes at RD-, RD+ and unknown: byte, K, error class and precedence, and the RD after it (recovery after an error)](../../rtl/pcs_dec8b10b/MAS.md#behavior) | `exhaustive` | 1 | 1; 6 × 1024 codes. The class counts must be 268 valid, 196 disparity, 560 code per known RD, and 464 valid, 560 code at unknown RD. |
| [RD carry across lanes and cycles, valid gaps, lock and generation, polarity, reset](../../rtl/pcs_dec8b10b/MAS.md#behavior) | `random_vs_model` | 1 | 3; 20000 cycles of encoded traffic with bit errors, random codes, transmitter RD slips and every control event |
| The same, long | `long_random` | 2 | 4; 200000 cycles each |

Every [interface](../../rtl/pcs_dec8b10b/MAS.md#interfaces) port is
exercised by every entry. Every feature in the MAS has a row; there are no
gaps.

## Mutation evidence

Each mutation was applied to a scratch copy of `src/` and run with
`./build sim --src-root <copy>`. Every one was caught:

| Mutation | `smoke` | `exhaustive` | `random_vs_model` |
| --- | --- | --- | --- |
| Disparity errors not flagged | FAIL | FAIL | FAIL |
| RD held on an errored symbol instead of following its sub-blocks | pass | FAIL | FAIL |
| Lane 1 starts from the stored RD, not lane 0's (no lane carry) | FAIL | FAIL | FAIL |
| A polarity change keeps the RD known | pass | pass | FAIL |
| A7 rule swapped between RD- and RD+ | pass | FAIL | FAIL |
