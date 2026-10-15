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
  merge, a level synchronizer delays two edges and resets to its value,
  and the reset synchronizer is high at once on assertion and falls at the
  second edge after release.
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
  `COMMON_ASSERT`, `_NO_RESET`, `_NEVER`, `_STABLE_WHEN`, `_CLOCKED` and
  `_CLOCKED_WHEN` stops its run when tripped, none is present with
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
  [sync2](common_cdc_sync2.md), [pulse](common_cdc_pulse.md),
  [rst](common_cdc_rst.md)); here only the wiring, width, latency and reset
  value.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Registers](../../../bkm/rtl.md#registers-and-memories): reset values, capture, enable hold, asynchronous assertion between edges; register arrays; memory read-during-write and lane merge; `COMMON_METAFLOP` latency, `COMMON_METAFLOP_VAL` reset value; five `COMMON_PULSE_SYNC` events; `COMMON_RESET_SYNC` on the asynchronous reset: high before any edge and between edges, released at the second edge after the fall | `macros_contract` | 0 | 1 |
| Every register, array, memory, level-crossing and reset-synchronizer form under random reset, enable, data and memory traffic, against the model | `macros_random` | 1 | 3; 2000 cycles |
