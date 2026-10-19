# common_macros DV plan

## Strategy

- **Top:** [`tb/common_macros_fixture.sv`](../../../../src/dv/common/tb/common_macros_fixture.sv)
  (listed in `common.dv.f`) calls every register, register-array, memory,
  crossing, unused-bit and assertion form once, on documented ports.
  Adapted from nand2anything's macro fixture and test (commit `085ef4b`),
  adapted with the owner's permission.
- **Oracle, from the [BKM contract](../../../bkm/rtl.md#registers-and-memories):**
  [`models/common_macros_model.py`](../../../../src/dv/common/models/common_macros_model.py),
  a cycle model: reset beats enable, arrays copy a whole next-state array,
  reads return the data stored before the same edge's write, lane enables
  merge, a level synchronizer delays two edges and resets to its value
  (at once on the asynchronous reset for `COMMON_METAFLOP_ARST`),
  and the reset synchronizers are high at once on assertion and fall at the
  second (`COMMON_RESET_SYNC`) or third (`COMMON_RESET_SYNC_STAGES` with 3)
  edge after release.
  Unwritten words and never-loaded registers are unknown and not compared.
  Its pytest tests run in `./build ci unit` (`models:common`).
- **Tests:** [`tests/test_macros.py`](../../../../src/dv/common/tests/test_macros.py)
  drives inputs at falling `clk_i` edges and compares every output with the
  model after each rising edge. The assertions in the fixture stay quiet
  under legal stimulus; a firing one stops the simulation and fails the
  test.
- **Assertion forms fire, and compile out:** a regression test cannot pass
  on a simulator stop, so
  [`tools/checks/tests/test_vocabulary_headers.py`](../../../../tools/checks/tests/test_vocabulary_headers.py)
  builds the real headers with Verilator `--assert`: each of
  `COMMON_ASSERT`, `_NO_RESET`, `_NEVER`, `_STABLE_WHEN`, `_CLOCKED`,
  `_CLOCKED_WHEN`, `_CLOCKED_ARST` and `_NEVER_ARST` stops its run when tripped;
  the two `_ARST` forms stay quiet when their trigger bits are high only
  while their reset is (it rises between edges just after the bits, is
  held over two clock edges, and falls after they clear; the release edge
  itself is not exercised); none is present with
  `SYNTHESIS` defined (two builds), a false `COMMON_ASSERT_PARAM` fails
  Verilator lint at elaboration, and only its `initial $fatal` form is left
  under `SYNTHESIS`. The tests carry the `vocabulary` marker, so
  `./build ci unit --affected` runs them when a header changes, and the
  nightly runs them with every suite.
  `COMMON_ASSERT_KNOWN` cannot fail in two-state Verilator; it is only
  compiled.
- **Functional coverage:** the `common_macros` covergroup (random test)
  bins the control at each edge (reset, enable, hold), the memory traffic
  (write, read-enable, same-address read-during-write, idle) and the lane
  enables of each write. No coverage floor yet.
- **Out of scope:** the cells behind the crossing macros (their own plans:
  [sync2](cdc/common_cdc_sync2.md), [pulse](cdc/common_cdc_pulse.md),
  [rst](cdc/common_cdc_rst.md)); here only the wiring, width, latency, reset
  value and reset style.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Registers](../../../bkm/rtl.md#registers-and-memories): reset values, capture, enable hold, asynchronous assertion between edges; register arrays; memory read-during-write and lane merge; `COMMON_METAFLOP` latency, `COMMON_METAFLOP_VAL` reset value, `COMMON_METAFLOP_ARST` cleared before any edge and between edges; five `COMMON_PULSE_SYNC` and five `COMMON_PULSE_SYNC_ARST` events, then a high `COMMON_PULSE_SYNC_ARST` pulse dropped between edges by the asynchronous reset while the synchronous form's stays high; `COMMON_RESET_SYNC` on the asynchronous reset: high before any edge and between edges, released at the second edge after the fall; `COMMON_RESET_SYNC_STAGES` with 3 the same, still high at the second edge and released at the third | `macros_contract` | 0 | 1 |
| Every register, array, memory, level-crossing and reset-synchronizer form under random reset, enable, data and memory traffic, against the model | `macros_random` / `macros_random_sweep` | 1 / 2 | derived seed 0 / derived seeds 1 and 2 (1917233032 and 1800402482), pinned; 2000 cycles |

**Level placement**
([#389](https://github.com/amichai-bd/pcie-factory/issues/389)): the full
level 1 of every IP, which `ci-gate` runs for a change to a shared
package, was over its 1200 s CPU budget. `macros_random` runs one seed in
level 1 (the first, unless the row says otherwise); its further seeds
run pinned and unchanged in the entry's level 2 `_sweep`, each seed with
its coverage instrumentation, so the merged level 0 to 2 runs are the same
runs. Any mutant recorded on this page is still first caught at the seed
it names, or at the entry's first seed, which stays in level 1.
