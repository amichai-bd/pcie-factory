# pcie_rc_tl DV plan

> **Stub for wave 2:** only the shell smoke test exists; the unit
> testbench comes with [#373](https://github.com/amichai-bd/pcie-factory/issues/373).

## Strategy

- **Shell test:** [`tests/test_rc_tl.py`](../../../../src/dv/rc_tl/tests/test_rc_tl.py)
  drives `pcie_rc_tl` directly (no SV testbench; `rc_tl.dv.f` is empty).
  With `rst_i` pulsed twice and every other input toggled at random, it
  checks at every falling `clk_i` edge that each output holds its
  [reset value](../../rtl/rc_tl/MAS.md#reset-and-link-down) and that
  `tlp_rx_ready_o` is 1 exactly when `rst_i` is 0.
- **Functional coverage:** the `rc_tl_shell` covergroup bins `rst` and
  `dl_up_i` (low, high), crossed.
- **Planned (wave 2):** one unit testbench through the sub-IP's ports: a
  command driver and result checker on the tl-seq side, and on the `link`
  side a TLP-level endpoint model that answers requests (with split,
  failing, poisoned, malformed and late completions), sends DMA writes,
  reads, MSIs and messages, and grants and checks credits. Results,
  host memory and the error record are checked against a Python reference
  of the [MAS](../../rtl/rc_tl/MAS.md) rules.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values of the shell](../../rtl/rc_tl/MAS.md#reset-and-link-down) | `smoke` | 0 | 1; 128 cycles with random inputs |

Levels 1 and 2 have no entries yet (`tests: []`).

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Type 1 header](../../rtl/rc_tl/MAS.md#type-1-header) (every field: reset value, read and write, read-only bits) through `HDR_*`; the [command](../../rtl/rc_tl/MAS.md#commands) take, checks 2 to 7 against the real header state (refusals by [outbound routing](../../arch/rc/HAS.md#outbound-routing) as `LOCAL_UR`) and [byte enables](../../rtl/rc_tl/REQUESTER.md#byte-enables); result beats. Lands first. | [#374](https://github.com/amichai-bd/pcie-factory/issues/374) |
| Endpoint commands past the checks: check 1, [requests](../../rtl/rc_tl/REQUESTER.md#requests) with the Requester ID from the header, [completion matching](../../rtl/rc_tl/REQUESTER.md#completion-matching), [timeout](../../rtl/rc_tl/REQUESTER.md#completion-timeout), [transmit](../../rtl/rc_tl/REQUESTER.md#transmit), [link down](../../rtl/rc_tl/MAS.md#reset-and-link-down), the [error record](../../rtl/rc_tl/MAS.md#error-record-and-q2-cases) and `ERR_*` | [#373](https://github.com/amichai-bd/pcie-factory/issues/373), after #374 and [#385](https://github.com/amichai-bd/pcie-factory/issues/385) |
| [Receive checks](../../rtl/rc_tl/INBOUND.md#receive-checks) for requests and messages, inbound Bus Master Enable and window gating, the [completer](../../rtl/rc_tl/INBOUND.md#completer) (poisoned non-posted requests answered with Unsupported Request included), [host memory](../../rtl/rc_tl/INBOUND.md#host-memory) and `HOST_*`, its forward-progress bound, [MSI capture](../../rtl/rc_tl/INBOUND.md#msi-capture), [credits](../../rtl/rc_tl/INBOUND.md#credits), the remaining error-record bits | [#375](https://github.com/amichai-bd/pcie-factory/issues/375) |
| The [parameter checks](../../rtl/rc_tl/MAS.md#parameters) fire on illegal values (a negative-elaboration check per `COMMON_ASSERT_PARAM`, the overlap check included) | #374 (with its testbench), else #373 |
| Coverage floors | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |
