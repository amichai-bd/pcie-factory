# pcie_rc_tl DV plan

Wave 2 builds `rc_tl` in three steps: the front end and the Type 1
header ([#374](https://github.com/amichai-bd/pcie-factory/issues/374),
verified here), then the requester
([#373](https://github.com/amichai-bd/pcie-factory/issues/373)) and the
inbound side ([#375](https://github.com/amichai-bd/pcie-factory/issues/375)).

## Strategy

- **Testbench:** [`tb/pcie_rc_tl_tb.sv`](../../../../src/dv/rc_tl/tb/pcie_rc_tl_tb.sv)
  wraps `pcie_rc_tl` with every port passed through and `params_o`, the
  parameters the model needs. The cocotb environment
  [`tb/rc_tl_env.py`](../../../../src/dv/rc_tl/tb/rc_tl_env.py) drives
  the ports and, at every falling `clk_i` edge, compares every output with
  the reference model: `cmd_ready_o`, `rsp_valid_o` and `rsp_o` exactly,
  the outputs of the blocks not built yet at their
  [reset values](../../rtl/rc_tl/MAS.md#reset-and-link-down) (nothing
  sent, released or captured), and `tlp_rx_ready_o` 1 exactly when
  `rst_i` is 0. Every directed test also states its expected results from
  the specs itself.
- **Reference model:** [`models/pcie_rc_tl_model.py`](../../../../src/dv/rc_tl/models/pcie_rc_tl_model.py),
  written from the [MAS commands](../../rtl/rc_tl/MAS.md#commands), the
  [byte-enable rule](../../rtl/rc_tl/REQUESTER.md#byte-enables), the
  [header table](../../arch/rc/HAS.md#type-1-header) and the
  [interim rule](../../rtl/rc_tl/MAS.md#leaf-blocks); a cycle model of the
  take and the beat. Its pytest unit tests check hand-worked spec cases
  with no simulator.
- **Telling a pass from a refusal.** Until #373 an endpoint command that
  passes checks 2 to 7 finishes `LINK_DOWN` with `dl_up_i` 0 and
  `LOCAL_UR` with it 1, so the tests offer endpoint commands with
  `dl_up_i` 0: `LINK_DOWN` is a pass and `LOCAL_UR` a refusal. `HDR_*`
  pass with `OK`.
- **Parameter checks:** `param_limits` lints `pcie_rc_tl` with Verilator
  from `rc_tl.rtl.f` with each illegal value (both ends of each range, a
  misaligned base, the overlap both ways) and expects the
  `COMMON_ASSERT_PARAM` message; the legal boundaries lint clean.
- **Functional coverage:** `rc_tl_cmd` bins the op (0 to 9 and undefined),
  the first failing check (none, 2 to 7), the status (`OK`, `LOCAL_UR`,
  `LINK_DOWN`) and `dl_up_i` of an endpoint command past the checks, with
  the crosses op by status, op by check and status by `dl_up_i` (the
  impossible combinations excluded); and each `HDR_WR` by DW (each RW DW,
  read-only) crossed with its byte-enable count. `rc_tl_reset` bins
  `rst_i` and `dl_up_i`, crossed.
- **Planned (#373, #375):** on the `link` side a TLP-level endpoint model
  that answers requests (with split, failing, poisoned, malformed and late
  completions), sends DMA writes, reads, MSIs and messages, and grants and
  checks credits; results, host memory and the error record checked
  against the model, extended.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../rtl/rc_tl/MAS.md#reset-and-link-down) and the front end under random inputs and commands, two resets | `smoke` | 0 | 1; 128 cycles |
| [Type 1 header](../../rtl/rc_tl/MAS.md#type-1-header) reset values, every DW 0 to 1023, again after a reset that follows writes | `hdr_reset` | 1 | 1; default IDs |
| The same with `VENDOR_ID`, `DEVICE_ID` and `REVISION_ID` set | `hdr_reset_ids` | 1 | 1 |
| Header read and write: every DW written all ones and zeros; each RW field with two patterns under every `first_be`; every RW bit alone; read-only bits and DWs 16 to 1023 ignore writes | `hdr_rw` | 1 | 1 |
| [Commands](../../rtl/rc_tl/MAS.md#commands) checks 2 to 5, each with refusals and the passing neighbours: undefined ops, `len` per op, every `addr` bit per op class, the 4 KiB edge per `len` | `cmd_checks` | 1 | 1 |
| Check 6, [byte enables](../../rtl/rc_tl/REQUESTER.md#byte-enables): every `first_be` and `last_be` for every op with `len` 1, and for `MEM_RD` with `len` 2 (both `addr[2]`), 3 and 32 | `byte_enables` | 1 | 1 |
| Check 7, [outbound routing](../../arch/rc/HAS.md#outbound-routing) by the header state: Secondary Bus Number (every bit), device, function; Memory Space Enable; the window edges for several windows, an empty one included; an `HDR_WR` routes the very next command | `routing` | 1 | 1 |
| The [interim rule](../../rtl/rc_tl/MAS.md#leaf-blocks): endpoint commands past the checks `LOCAL_UR` or `LINK_DOWN` by `dl_up_i` at the take, refusals `LOCAL_UR` either way, `HOST_*` and `ERR_*` `LOCAL_UR`; nothing sent | `interim` | 1 | 1 |
| Result beats: one beat in the cycle after the take, `last` 1; ready 0 until the beat's edge, 1 after it; `rsp_o` 0 between beats; `rst_i` silences them at once; nothing taken in reset | `result_beats` | 1 | 1 |
| Random commands biased to the check edges, random `dl_up_i`, idle inputs and resets, against the model | `random_cmds` | 1 | 2; 3000 commands each |
| The [parameter checks](../../rtl/rc_tl/MAS.md#parameters): every `COMMON_ASSERT_PARAM` fires on illegal values, the overlap both ways; the boundaries pass | `param_limits` | 1 | 1 |
| Long random commands | `random_long` | 2 | 2; 30000 commands each |

## Gaps

| Feature | Owning issue |
| --- | --- |
| Endpoint commands past the checks: check 1, [requests](../../rtl/rc_tl/REQUESTER.md#requests) with the Requester ID from the header, [completion matching](../../rtl/rc_tl/REQUESTER.md#completion-matching), [timeout](../../rtl/rc_tl/REQUESTER.md#completion-timeout), [transmit](../../rtl/rc_tl/REQUESTER.md#transmit), [link down](../../rtl/rc_tl/MAS.md#reset-and-link-down), the [error record](../../rtl/rc_tl/MAS.md#error-record-and-q2-cases) and `ERR_*`, with the `ERR_*` refusals (checks 3 and 6). When check 1 lands, the refusal tests that run with `dl_up_i` 0 (`cmd_checks`, `byte_enables`, `routing`, `interim`) expect `LINK_DOWN` instead of `LOCAL_UR`, and telling a pass from a refusal needs `dl_up_i` 1 and the requester's accepted path | [#373](https://github.com/amichai-bd/pcie-factory/issues/373), after [#385](https://github.com/amichai-bd/pcie-factory/issues/385) |
| [Receive checks](../../rtl/rc_tl/INBOUND.md#receive-checks) for requests and messages, inbound Bus Master Enable and window gating, the [completer](../../rtl/rc_tl/INBOUND.md#completer) (poisoned non-posted requests answered with Unsupported Request included), [host memory](../../rtl/rc_tl/INBOUND.md#host-memory) and `HOST_*` with the `HOST_*` refusals (checks 3, 4 by `HOST_MEM_ADDR_W`, and 6), its forward-progress bound, [MSI capture](../../rtl/rc_tl/INBOUND.md#msi-capture), [credits](../../rtl/rc_tl/INBOUND.md#credits), the remaining error-record bits | [#375](https://github.com/amichai-bd/pcie-factory/issues/375) |
| Coverage floors | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |
