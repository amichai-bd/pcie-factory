# pcie_rc_seq DV plan

> **Draft until the interpreter:** only the shell smoke test exists; the
> command tests arrive with
> [#376](https://github.com/amichai-bd/pcie-factory/issues/376).

## Strategy

- **Shell test:** [`tests/test_rc_seq.py`](../../../../src/dv/rc_seq/tests/test_rc_seq.py)
  drives `pcie_rc_seq` directly (no SV testbench; `rc_seq.dv.f` is
  empty). With `clk_i` running, `rst_i` pulsed twice and `start_i`,
  `dl_up_i` and `link_up_i` toggled at random, it checks every output at
  its [reset value](../../rtl/rc_seq/MAS.md#reset) at every falling `clk_i`
  edge.
- **Functional coverage:** the `rc_seq_shell` covergroup bins `rst` and
  `start_i` (low, high), crossed.
- **Planned (#376):** `pcie_rc_seq` with the `SELFTEST` program
  ([programs](../../rtl/rc_seq/PROGRAMS.md#self-test)) against a cocotb
  stand-in for `rc_tl` that answers each command, MSI capture and link
  status as the test chooses, checking every op, failure case and timing
  bound of the [MAS](../../rtl/rc_seq/MAS.md#commands) through `done_o`,
  `pass_o` and `fail_step_o`; and every M4 program run against the
  stand-in with one expected value made wrong, so `rc_seq` fails at that
  step. The M4 programs against `core` are the `rc` e2e's
  ([#377](https://github.com/amichai-bd/pcie-factory/issues/377)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../rtl/rc_seq/MAS.md#reset) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Run](../../rtl/rc_seq/MAS.md#run), [result registers](../../rtl/rc_seq/MAS.md#result-registers), every [command](../../rtl/rc_seq/MAS.md#commands) and its failure, the [timing](../../rtl/rc_seq/MAS.md#timing) bound, `SELFTEST` | [#376](https://github.com/amichai-bd/pcie-factory/issues/376) |
| The M4 [programs](../../rtl/rc_seq/PROGRAMS.md) against `core` | [#377](https://github.com/amichai-bd/pcie-factory/issues/377) |
| Coverage floors | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |
