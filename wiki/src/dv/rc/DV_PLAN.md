# pcie_rc DV plan

> **Draft for wave 3:** only the shell smoke test exists; the e2e arrives
> with the stack ([#377](https://github.com/amichai-bd/pcie-factory/issues/377)).

## Strategy

- **Shell test:** [`tests/test_rc.py`](../../../../src/dv/rc/tests/test_rc.py)
  drives `pcie_rc` directly (no SV testbench; `rc.dv.f` is empty). With
  `clk_i` and `ser_rx_clk_i` running, `rst_i` pulsed twice and every other
  input toggled at random, it checks each output with a defined
  [reset value](../../arch/rc/HAS.md#clocks-and-reset) at every falling
  `clk_i` edge, and `ser_tx_data_o` 0: not qualified while the line is in
  electrical idle, but the shell drives 0 ([MAS](../../rtl/rc/MAS.md#microarchitecture)).
- **Functional coverage:** the `rc_shell` covergroup bins `rst` and
  `start_i` (low, high), crossed.
- **Planned (wave 3):** `rc` back to back with the endpoint `core` over
  the full `phy` stack, `rc_seq`'s M4 programs driving the host side, the
  host model of [#288](https://github.com/amichai-bd/pcie-factory/issues/288)
  in passive shadow mode ([#372](https://github.com/amichai-bd/pcie-factory/issues/372))
  checking both directions, and the existing `core` checks on the endpoint
  side; then the board-build pair with the GPIO adapter
  ([#378](https://github.com/amichai-bd/pcie-factory/issues/378)).

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../arch/rc/HAS.md#clocks-and-reset) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Sequencer run](../../arch/rc/HAS.md#sequencer-run), [Q2 observability](../../arch/rc/HAS.md#q2-observability), [outbound](../../arch/rc/HAS.md#outbound-routing) and [inbound routing](../../arch/rc/HAS.md#inbound-routing), [link down](../../arch/rc/HAS.md#link-down), the [downstream role](../../arch/rc/HAS.md#downstream-role) in the stack | [#377](https://github.com/amichai-bd/pcie-factory/issues/377) |
| The [tl-seq interface](../../arch/rc/TL_SEQ.md) | `rc_tl` and `rc_seq` DV ([#373](https://github.com/amichai-bd/pcie-factory/issues/373) to [#376](https://github.com/amichai-bd/pcie-factory/issues/376)) |
| The host model passive shadow | [#372](https://github.com/amichai-bd/pcie-factory/issues/372) |
| Board build | [#378](https://github.com/amichai-bd/pcie-factory/issues/378) |
| Coverage floors | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |
