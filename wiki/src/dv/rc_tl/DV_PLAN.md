# pcie_rc_tl DV plan

Wave 2 builds `rc_tl` in three steps: the front end and the Type 1
header ([#374](https://github.com/amichai-bd/pcie-factory/issues/374)),
then the requester
([#373](https://github.com/amichai-bd/pcie-factory/issues/373)), both
verified here, and the inbound side
([#375](https://github.com/amichai-bd/pcie-factory/issues/375)).

## Strategy

- **Testbench:** [`tb/pcie_rc_tl_tb.sv`](../../../../src/dv/rc_tl/tb/pcie_rc_tl_tb.sv)
  wraps `pcie_rc_tl` with every port passed through and `params_o`, the
  parameters the model needs. The cocotb environment
  [`tb/rc_tl_env.py`](../../../../src/dv/rc_tl/tb/rc_tl_env.py) drives
  the ports and, at every falling `clk_i` edge, compares every output with
  the reference model: `cmd_ready_o`, `rsp_valid_o`, `rsp_o` and
  `tlp_tx_valid_o`, `tlp_tx_data_o`, `tlp_tx_sop_o`, `tlp_tx_eop_o`
  exactly (0 while no beat is offered), the outputs of the blocks not
  built yet at their [reset values](../../rtl/rc_tl/MAS.md#reset-and-link-down)
  (nothing released or captured), and `tlp_rx_ready_o` 1 exactly when
  `rst_i` is 0. It also plays the link: `tlp_tx_ready_i` always 1 or at
  random, the credits, `dl_up_i`, and the receive stream with optional
  idle cycles inside a TLP, abandoning a TLP in progress when the link
  goes down. Every directed test also states its expected results from
  the specs itself.
- **Endpoint:** [`tb/rc_tl_ep.py`](../../../../src/dv/rc_tl/tb/rc_tl_ep.py),
  a TLP-level endpoint written from the [TLP formats](../../arch/tl/TLP.md)
  and the [completion rules](../../rtl/rc_tl/INBOUND.md#completer):
  it decodes each request the DUT sends and answers a non-posted one with
  Successful completions (configuration reads from a register map, memory
  reads from a memory image, Byte Count and Lower Address per completion);
  a test replaces the answer with any TLPs: other statuses, poisoned or
  malformed completions, splits at chosen 64-byte boundaries, strays,
  duplicates, late ones or none.
- **Reference model:** [`models/pcie_rc_tl_model.py`](../../../../src/dv/rc_tl/models/pcie_rc_tl_model.py),
  written from the [MAS commands](../../rtl/rc_tl/MAS.md#commands) and
  [error record](../../rtl/rc_tl/MAS.md#error-record-and-q2-cases), the
  [requester page](../../rtl/rc_tl/REQUESTER.md), the
  [header table](../../arch/rc/HAS.md#type-1-header), the MAS
  [timing](../../rtl/rc_tl/MAS.md#timing) and
  [link down](../../rtl/rc_tl/MAS.md#reset-and-link-down), and the
  [interim rule](../../rtl/rc_tl/MAS.md#leaf-blocks) for `HOST_*`; a
  cycle model of the take, the request, the transmit stage, the
  completion beats and the result beats. Its completion checks are stated
  on byte addresses (the read's enabled bytes and the completion's data
  window), not on remaining-byte counters, and its timeout on the edge
  count since the `sop`. Its pytest unit tests check hand-worked spec
  cases with no simulator: request header bytes, Byte Count and Lower
  Address of a split, the timing of a request, each status row, a split
  read and the timeout bound.
- **Telling a pass from a refusal.** Check 1 refuses every endpoint
  command while `dl_up_i` is 0, so the front-end tests offer endpoint
  commands with `dl_up_i` 1: a pass is sent and completed `OK` by the
  endpoint, a refusal finishes `LOCAL_UR` at once and sends nothing.
  `HDR_*` and `ERR_*` pass with `OK`.
- **Timeouts:** the requester entries run with `CPL_TIMEOUT_DIV` 1024
  (N = 2048 cycles) and 1000 (N = 2097, a divider that is not a power of
  two); `req_success` runs at the default 1, and the L2 entry
  `req_timeout_div1` times out once at 1, N = 2^21.
- **Parameter checks:** `param_limits` lints `pcie_rc_tl` with Verilator
  from `rc_tl.rtl.f` with each illegal value (both ends of each range, a
  misaligned base, the overlap both ways) and expects the
  `COMMON_ASSERT_PARAM` message; the legal boundaries lint clean.
- **Functional coverage:** `rc_tl_cmd` bins the op (0 to 9 and undefined),
  the first failing check (none, 1 to 7), the status (all eight) and
  `dl_up_i` of an endpoint command at the take, with the crosses op by
  status, op by check and `dl_up_i` by status (the impossible combinations
  excluded); and each `HDR_WR` by DW (each RW DW, read-only) crossed with
  its byte-enable count. `rc_tl_req` bins each endpoint request past the
  checks by op, status, result beats (1, 2 to 8, 9 to 31, 32) and matched
  completions (0, 1, 2, 3 or more), crossed op by status and status by
  completions. `rc_tl_cpl` bins each received completion by its status
  field, the outcome it drew (unexpected, data, `OK`, `UR`, `CA`, `CRS`,
  `BAD`), EP and TD, crossed status by outcome and TD by outcome.
  `rc_tl_reset` bins `rst_i` and `dl_up_i`, crossed.

## Plan

| Feature | Entry `name` | Level | Seeds / note |
| --- | --- | --- | --- |
| [Reset values](../../rtl/rc_tl/MAS.md#reset-and-link-down) and the front end under random inputs and commands, two resets | `smoke` | 0 | 1; 128 cycles |
| [Type 1 header](../../rtl/rc_tl/MAS.md#type-1-header) reset values, every DW 0 to 1023, again after a reset that follows writes | `hdr_reset` | 1 | 1; default IDs |
| The same with `VENDOR_ID`, `DEVICE_ID` and `REVISION_ID` set | `hdr_reset_ids` | 1 | 1 |
| Header read and write: every DW written all ones and zeros; each RW field with two patterns under every `first_be`; every RW bit alone; read-only bits and DWs 16 to 1023 ignore writes | `hdr_rw` | 1 | 1 |
| [Commands](../../rtl/rc_tl/MAS.md#commands) checks 2 to 5, each with refusals and the passing neighbours: undefined ops, `len` per op (`ERR_*` included), every `addr` bit per op class, the 4 KiB edge per `len`; refusals send nothing | `cmd_checks` | 1 | 1 |
| Check 6, [byte enables](../../rtl/rc_tl/REQUESTER.md#byte-enables): every `first_be` and `last_be` for every op with `len` 1 (`ERR_*` included), and for `MEM_RD` with `len` 2 (both `addr[2]`), 3 and 32 | `byte_enables` | 1 | 1 |
| Check 7, [outbound routing](../../arch/rc/HAS.md#outbound-routing) by the header state: Secondary Bus Number (every bit), device, function; Memory Space Enable; the window edges for several windows, an empty one included; an `HDR_WR` routes the very next command | `routing` | 1 | 1 |
| Check 1: an endpoint command while `dl_up_i` is 0 finishes `LINK_DOWN` whichever of checks 3 to 7 it fails, sending nothing; the same commands pass or finish `LOCAL_UR` with the link up; local commands unaffected; `HOST_*` `LOCAL_UR` (interim); `dl_up_i` sampled at the take | `check1` | 1 | 1 |
| Result beats: one beat in the cycle after the take, `last` 1; ready 0 until the beat's edge, 1 after it; `rsp_o` 0 between beats; `rst_i` silences them at once; nothing taken in reset | `result_beats` | 1 | 1 |
| Random commands biased to the check edges, random `dl_up_i` (mostly 1, so endpoint commands are sent and completed), idle inputs and resets, against the model | `random_cmds` | 1 | 2; 3000 commands each |
| [Requests](../../rtl/rc_tl/REQUESTER.md#requests) completing `OK`: every field of each TLP (Fmt and Type, Length, byte enables, Requester ID, tag, address, data), the tag rolling over after 32 non-posted requests and not stepped by `MEM_WR`, the Requester ID following the Primary Bus Number, `MEM_RD` of 1 to 32 DWs with legal byte enables and a zero-length read, the [timing](../../rtl/rc_tl/MAS.md#timing) of the request, of a `MEM_WR`'s beat and of data beats; again with the link stalling and receive gaps; `CPL_TIMEOUT_DIV` 1 | `req_success` | 1 | 1 |
| [Completion statuses](../../rtl/rc_tl/REQUESTER.md#completion-matching): UR, CA, CRS for each op (CRS on `MEM_RD` is `BAD`, bit 6), reserved statuses `BAD`, poisoned `BAD` (bit 5, no data), status before poisoned; one beat in the cycle after the `eop`, with TD and gaps; the error record bit of each | `req_status` | 1 | 1 |
| Every malformed row: a configuration read without data or with Length 2, a configuration write with data, a memory read without data, Byte Count off by one or 4096, Lower Address wrong, data past the last DW, ending short off a 64-byte boundary; each also poisoned (malformed wins); the legal neighbours, a short completion on a 64-byte boundary (a split) and a zero-length read pass | `req_malformed` | 1 | 1 |
| Injected split completions at every 64-byte boundary, some or none, with and without digest, for aligned and unaligned reads and unaligned byte enables; with gaps and a stalling link; D7: the second of three completions failing seven ways (Byte Count, Lower Address, poisoned, UR, CA, reserved status, short off a boundary) ends the read after the first's data with its status, and the third is unexpected (bit 4) | `req_split` | 1 | 1 |
| Unexpected completions, bit 4 only and dropped: nothing outstanding, a wrong tag (each of the 8 bits), a wrong Requester ID, a locked completion, one before its request is sent, a duplicate, one while a posted write waits; the outstanding request then completes. A completion Type with a 4-DW or prefix Fmt never matches (dropped with no event until #375) | `req_unexpected` | 1 | 1 |
| [Completion timeout](../../rtl/rc_tl/REQUESTER.md#completion-timeout) at N = ⌊2^21 / 1024⌋: `CFG_RD`, `CFG_WR` and `MEM_RD` unanswered end `TIMEOUT` in the cycle after edge S + N, bit 3, a late completion unexpected; a read timing out after the data of one or two of its three completions; a completion whose third header DW is at S + N matches, at S + N + 1 is unexpected; a non-last split across S + N delivers its data, then `TIMEOUT` after its `eop`; a posted write stalled past N never times out; a `CFG_WR` stalled after its `sop` times out, its TLP still goes out whole before the next request's | `req_timeout` | 1 | 1; `CPL_TIMEOUT_DIV` 1024 |
| The same at N = 2097 | `req_timeout_div1000` | 1 | 1; `CPL_TIMEOUT_DIV` 1000 |
| [Link down](../../rtl/rc_tl/MAS.md#reset-and-link-down) with a request outstanding: before the grant, after the `sop`, mid-TLP (`CFG_WR`, `MEM_WR`), mid-completion after 1, 5 and 31 data beats and in a split read's second completion: `LINK_DOWN` on the last beat in the cycle after the first edge with `dl_up_i` 0, abandoned TLPs never finish; a late completion after link-up is unexpected; a completion cut by link down holds no timeout; the tag counts every `sop` taken; the error record and local commands unaffected | `req_link_down` | 1 | 1 |
| [Transmit](../../rtl/rc_tl/REQUESTER.md#transmit) credits: each class's header and data credits at 0 hold exactly the requests of that class, 1 and all ones pass, completion credits and the other class never gate; credits falling after the grant do not stop the TLP | `req_credits` | 1 | 1 |
| [Error record](../../rtl/rc_tl/MAS.md#error-record-and-q2-cases) and `ERR_*`: each requester event (bits 0 to 6) sets its bit; `ERR_RD` returns the record before its edge, bits 31:13 0; `ERR_CLR` clears the bits in `data`, any `first_be`; an event at an `ERR_CLR`'s edge wins; the `ERR_*` refusals (checks 3 and 6) change nothing; `rst_i` clears it | `err_record` | 1 | 1 |
| Random endpoint commands with random answers (success, splits, statuses, poisoned, malformed, strays, duplicates, D7 failures, no answer), link stalls, receive gaps, credit outages, link drops and resets, against the model | `random_req` | 1 | 2; 1500 commands each |
| The [parameter checks](../../rtl/rc_tl/MAS.md#parameters): every `COMMON_ASSERT_PARAM` fires on illegal values, the overlap both ways; the boundaries pass | `param_limits` | 1 | 1 |
| Long random commands | `random_long` | 2 | 2; 30000 commands each |
| Long random endpoint commands, as `random_req` | `random_req_long` | 2 | 2; 20000 commands each |
| The completion timeout at the FPGA value, N = 2^21 cycles | `req_timeout_div1` | 2 | 1; `CPL_TIMEOUT_DIV` 1 |

**Level placement.** `random_req_long` is a long random test and
`req_timeout_div1` a long one (2^21 cycles of one wait); `req_timeout`
and `req_timeout_div1000` check the same bound at L1.

## Gaps

| Feature | Owning issue |
| --- | --- |
| [Receive checks](../../rtl/rc_tl/INBOUND.md#receive-checks) for requests and messages (and malformed kinds that are not completions), inbound Bus Master Enable and window gating, the [completer](../../rtl/rc_tl/INBOUND.md#completer) (poisoned non-posted requests answered with Unsupported Request included), the transmit arbiter's completion line and its priority, [host memory](../../rtl/rc_tl/INBOUND.md#host-memory) and `HOST_*` with the `HOST_*` refusals (checks 3, 4 by `HOST_MEM_ADDR_W`, and 6), its forward-progress bound, [MSI capture](../../rtl/rc_tl/INBOUND.md#msi-capture), [credits](../../rtl/rc_tl/INBOUND.md#credits), error-record bits 5 to 12 for received requests and messages | [#375](https://github.com/amichai-bd/pcie-factory/issues/375) |
| Coverage floors | [#380](https://github.com/amichai-bd/pcie-factory/issues/380) |
