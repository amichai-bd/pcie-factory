# pcie_rc_tl DV plan

Wave 2 built `rc_tl` in three steps, each verified here: the front end
and the Type 1 header
([#374](https://github.com/amichai-bd/pcie-factory/issues/374)), the
requester ([#373](https://github.com/amichai-bd/pcie-factory/issues/373))
and the inbound side
([#375](https://github.com/amichai-bd/pcie-factory/issues/375)).

## Strategy

- **Testbench:** [`tb/pcie_rc_tl_tb.sv`](../../../../src/dv/rc_tl/tb/pcie_rc_tl_tb.sv)
  wraps `pcie_rc_tl` with every port passed through and `params_o`, the
  parameters the model needs. The cocotb environment
  [`tb/rc_tl_env.py`](../../../../src/dv/rc_tl/tb/rc_tl_env.py) drives
  the ports and, at every falling `clk_i` edge, compares every output with
  the reference model: `cmd_ready_o`, `rsp_valid_o`, `rsp_o`,
  `tlp_tx_valid_o`, `tlp_tx_data_o`, `tlp_tx_sop_o`, `tlp_tx_eop_o`
  (0 while no beat is offered), `msi_valid_o`, `msi_o`, `fc_rel_p_o`,
  `fc_rel_pd_o`, `fc_rel_np_o` and `fc_rel_npd_o` exactly, and
  `tlp_rx_ready_o` 1 exactly when `rst_i` is 0. It also plays the link:
  `tlp_tx_ready_i` always 1 or at random, the credits (completion credits
  included), `dl_up_i`, `msi_ready_i`, and the receive stream with
  optional idle cycles inside a TLP, abandoning a TLP in progress when the
  link goes down. The stream keeps the link's credit contract: a
  non-posted TLP waits while `FC_NPH` of them are unreleased, counted from
  `fc_rel_np_o` and restarted at link down. Completions the DUT sends are
  collected, not answered. Every directed test also states its expected
  results from the specs itself.
- **Endpoint:** [`tb/rc_tl_ep.py`](../../../../src/dv/rc_tl/tb/rc_tl_ep.py),
  a TLP-level endpoint written from the [TLP formats](../../arch/tl/TLP.md)
  and the [completion rules](../../rtl/rc_tl/INBOUND.md#completer):
  it decodes each request the DUT sends and answers a non-posted one with
  Successful completions (configuration reads from a register map, memory
  reads from a memory image, Byte Count and Lower Address per completion);
  a test replaces the answer with any TLPs: other statuses, poisoned or
  malformed completions, splits at chosen 64-byte boundaries, strays,
  duplicates, late ones or none. Its builders make the endpoint's own
  requests and messages for the inbound side, any Fmt and Type with a 3-
  or 4-DW header, data and a digest; `Cpl` decodes the DUT's completions.
- **Reference model:** [`models/pcie_rc_tl_model.py`](../../../../src/dv/rc_tl/models/pcie_rc_tl_model.py),
  written from the [MAS commands](../../rtl/rc_tl/MAS.md#commands) and
  [error record](../../rtl/rc_tl/MAS.md#error-record-and-q2-cases), the
  [requester page](../../rtl/rc_tl/REQUESTER.md), the
  [header table](../../arch/rc/HAS.md#type-1-header), the MAS
  [timing](../../rtl/rc_tl/MAS.md#timing) and
  [link down](../../rtl/rc_tl/MAS.md#reset-and-link-down), and the
  [inbound page](../../rtl/rc_tl/INBOUND.md) with the
  [inbound routing](../../arch/rc/HAS.md#inbound-routing); a cycle model
  of the take, the request, the transmit stage and its two lines, the
  completion beats, the result beats, the receive checks and routing,
  host memory and its port turns, the non-posted queue, the completer,
  the MSI capture and the releases. Its completion checks are stated on
  byte addresses (the read's enabled bytes and the completion's data
  window), not on remaining-byte counters, its timeout on the edge count
  since the `sop`, its routing on the request's byte range and its splits
  on byte addresses. Host memory has no initial contents: the model
  tracks the bytes written, and a byte never written is unknown, so the
  environment compares no `rsp_o` or `tlp_tx_data_o` bits that carry one;
  the inbound tests write host memory with `HOST_WR` (a seeded non-zero
  pattern) before reading it. Its pytest unit tests check hand-worked spec cases with no
  simulator: request header bytes, Byte Count and Lower Address of a
  split, the timing of a request, each status row, a split read, the
  timeout bound, the kinds and classes, each malformed step, the routing
  rows with poisoned, the completer's splits, and host memory's unknown
  bytes.
- **Telling a pass from a refusal.** Check 1 refuses every endpoint
  command while `dl_up_i` is 0, so the front-end tests offer endpoint
  commands with `dl_up_i` 1: a pass is sent and completed `OK` by the
  endpoint, a refusal finishes `LOCAL_UR` at once and sends nothing.
  `HDR_*`, `HOST_*` and `ERR_*` pass with `OK`.
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
  `rc_tl_inb` bins each received request or message by kind (memory
  write, memory read, locked, I/O, configuration, atomic, message, no
  kind), outcome (written, captured, read, Unsupported Request, locked
  Unsupported Request, dropped, dropped through the queue), error bit, a
  4-DW header and TD, crossed kind by outcome (the impossible pairs
  excluded), and each capture as taken or dropped. `rc_tl_cplr` bins each
  completion the completer sends by status, locked, DWs (0, 1, 2 to 16, 17
  to 31, 32), first or later of a read, and Byte Count 4096, crossed
  status by DWs. `rc_tl_host` bins each `HOST_*` by op and its wait for
  host memory (0, 1, 2 to 15, 16 to 33 cycles), crossed.
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
| Check 1: an endpoint command while `dl_up_i` is 0 finishes `LINK_DOWN` whichever of checks 3 to 7 it fails, sending nothing; the same commands pass or finish `LOCAL_UR` with the link up; local commands, `HOST_*` included, unaffected; `dl_up_i` sampled at the take | `check1` | 1 | 1 |
| Result beats: one beat in the cycle after the take, `last` 1; ready 0 until the beat's edge, 1 after it; `rsp_o` 0 between beats; `rst_i` silences them at once; nothing taken in reset | `result_beats` | 1 | 1 |
| Random commands biased to the check edges, random `dl_up_i` (mostly 1, so endpoint commands are sent and completed), idle inputs and resets, against the model | `random_cmds` | 1 | 2; 3000 commands each |
| [Requests](../../rtl/rc_tl/REQUESTER.md#requests) completing `OK`: every field of each TLP (Fmt and Type, Length, byte enables, Requester ID, tag, address, data), the tag rolling over after 32 non-posted requests and not stepped by `MEM_WR`, the Requester ID following the Primary Bus Number, `MEM_RD` of 1 to 32 DWs with legal byte enables and a zero-length read, the [timing](../../rtl/rc_tl/MAS.md#timing) of the request, of a `MEM_WR`'s beat and of data beats; again with the link stalling and receive gaps; `CPL_TIMEOUT_DIV` 1 | `req_success` | 1 | 1 |
| [Completion statuses](../../rtl/rc_tl/REQUESTER.md#completion-matching): UR, CA, CRS for each op (CRS on `MEM_RD` is `BAD`, bit 6), reserved statuses `BAD`, poisoned `BAD` (bit 5, no data), status before poisoned; one beat in the cycle after the `eop`, with TD and gaps; the error record bit of each | `req_status` | 1 | 1 |
| Every malformed row: a configuration read without data or with Length 2, a configuration write with data, a memory read without data, Byte Count off by one, 4096, or with bits 11:8 set, Lower Address wrong, data past the last DW, ending short off a 64-byte boundary; each also poisoned (malformed wins); the legal neighbours, a short completion on a 64-byte boundary (a split) and a zero-length read pass | `req_malformed` | 1 | 1 |
| Injected split completions at every 64-byte boundary, some or none, with and without digest, for aligned and unaligned reads and unaligned byte enables; with gaps and a stalling link; D7: the second of three completions failing seven ways (Byte Count, Lower Address, poisoned, UR, CA, reserved status, short off a boundary) ends the read after the first's data with its status, and the third is unexpected (bit 4) | `req_split` | 1 | 1 |
| Unexpected completions, bit 4 only and dropped: nothing outstanding, a wrong tag (each of the 8 bits), a wrong Requester ID, a locked completion, one before its request is sent, a duplicate, one while a posted write waits; the outstanding request then completes. A completion Type with Fmt `001`, `011`, `100` or `110` never matches: malformed, bit 6, no release | `req_unexpected` | 1 | 1 |
| [Completion timeout](../../rtl/rc_tl/REQUESTER.md#completion-timeout) at N = ⌊2^21 / 1024⌋: `CFG_RD`, `CFG_WR` and `MEM_RD` unanswered end `TIMEOUT` in the cycle after edge S + N, bit 3, a late completion unexpected; a read timing out after the data of one or two of its three completions; a completion whose third header DW is at S + N matches, at S + N + 1 is unexpected; a non-last split across S + N delivers its data, then `TIMEOUT` after its `eop`; a posted write stalled past N never times out; a `CFG_WR` stalled after its `sop` times out, its TLP still goes out whole before the next request's | `req_timeout` | 1 | 1; `CPL_TIMEOUT_DIV` 1024 |
| The same at N = 2097 | `req_timeout_div1000` | 1 | 1; `CPL_TIMEOUT_DIV` 1000 |
| [Link down](../../rtl/rc_tl/MAS.md#reset-and-link-down) with a request outstanding: before the grant, after the `sop`, mid-TLP (`CFG_WR`, `MEM_WR`), mid-completion after 1, 5 and 31 data beats and in a split read's second completion: `LINK_DOWN` on the last beat in the cycle after the first edge with `dl_up_i` 0, abandoned TLPs never finish; a late completion after link-up is unexpected; a completion cut by link down holds no timeout; the tag counts every `sop` taken; the error record and local commands unaffected | `req_link_down` | 1 | 1 |
| [Transmit](../../rtl/rc_tl/REQUESTER.md#transmit) credits: each class's header and data credits at 0 hold exactly the requests of that class, 1 and all ones pass, completion credits and the other class never gate; credits falling after the grant do not stop the TLP | `req_credits` | 1 | 1 |
| [Error record](../../rtl/rc_tl/MAS.md#error-record-and-q2-cases) and `ERR_*`: each requester event (bits 0 to 6) sets its bit; `ERR_RD` returns the record before its edge, bits 31:13 0; `ERR_CLR` clears the bits in `data`, any `first_be`; an event at an `ERR_CLR`'s edge wins; the `ERR_*` refusals (checks 3 and 6) change nothing; `rst_i` clears it | `err_record` | 1 | 1 |
| Random endpoint commands with random answers (success, splits, statuses, poisoned, malformed, strays, duplicates, D7 failures, no answer), link stalls, receive gaps, credit outages, link drops and resets, against the model | `random_req` | 1 | 2; 1500 commands each |
| The [parameter checks](../../rtl/rc_tl/MAS.md#parameters): every `COMMON_ASSERT_PARAM` fires on illegal values, the overlap both ways; the boundaries pass | `param_limits` | 1 | 1 |
| `HOST_RD` and `HOST_WR` ([host memory access](../../arch/rc/TL_SEQ.md#host-memory-access)): every offset bit below `HOST_MEM_ADDR_W` selects its own DW, every `first_be` merges its bytes, a zero `first_be` writes nothing; the refusals finish `LOCAL_UR` and change nothing: check 3 (`len` 0, 2 to 63), check 4 (bits 1:0 and every bit at or above `HOST_MEM_ADDR_W`), check 6 (every `last_be` not 0); `OK` with the link down; the uncontended [timing](../../rtl/rc_tl/MAS.md#timing) | `host_cmds` | 1 | 1 |
| The same with 256 bytes of host memory at `0x300` | `host_cmds_aw8` | 1 | 1; `HOST_MEM_ADDR_W` 8 |
| The same with 64 KiB at `0x1_0000` | `host_cmds_aw16` | 1 | 1; `HOST_MEM_ADDR_W` 16 |
| Posted writes to [host memory](../../rtl/rc_tl/INBOUND.md#host-memory): Length 1 to 32 at random offsets, lanes by First BE, all four, then Last BE, a zero-length write, a digest DW not written, a 4-DW header below 4 GiB; each released in the cycle after its `eop` with its data credits; not written, released: Bus Master Enable 0, the window, past host memory, above 4 GiB (bit 11), poisoned (bit 5 only), poisoned and Unsupported (bit 11 only); the window row first: a window over host memory (the reset values) or over the MSI window refuses a write and a read there, an empty window (Base above Limit) refuses nothing | `inb_write` | 1 | 1 |
| A host memory smaller than 4 KiB: a write and a read ending on its last DW served, one straddling its end an Unsupported Request, nothing written | `inb_host_edge` | 1 | 1; `HOST_MEM_ADDR_W` 8 at `0x300` |
| The [completer](../../rtl/rc_tl/INBOUND.md#completer): reads of 1, 2, 16, 17 and 33 DWs at every DW of a 64-byte block, 64, 1008 and 1024 DWs (Byte Count 4096 sent as 0), zero-length and unaligned byte enables: every completion field, the split on the read completion boundary, the masked data; the Completer ID following the Primary Bus Number; Unsupported Request completions for Bus Master Enable 0, the window, past host memory, above 4 GiB, the MSI window (Byte Count T, Lower Address B), a locked read (CplLk), configuration, I/O and atomic requests (Byte Count 4, Lower Address 0), with bit 11; a poisoned read (bit 5 only) and a poisoned refused one (bit 11 only); each released with its data credits | `inb_read` | 1 | 1 |
| The [MSI capture](../../rtl/rc_tl/INBOUND.md#msi-capture): offsets 0 and 1023, data as received under any byte enables, arrival order, captured at the `eop`; four held, then two dropped with bit 12; a take at the edge a capture arrives full makes room; `msi_valid_o` and `msi_o` unchanged by `msi_ready_i` inside a cycle; kept through link down, emptied by `rst_i`; Bus Master Enable 0, a 2-DW write and a read of the window are Unsupported Requests; the DMA writes before an MSI are in host memory when it is captured | `msi_capture` | 1 | 1 |
| Messages: ERR_COR, ERR_NONFATAL, ERR_FATAL (bits 7 to 9), other codes (bit 10), routing subfields, with data; every TC-0 code with TC 1 and 7 malformed, other codes with TC not 0 not; poisoned bit 5 only; malformed outranks poisoned; Bus Master Enable 0 and 1; each released with its data credits | `inb_msg` | 1 | 1 |
| The [receive checks](../../rtl/rc_tl/INBOUND.md#receive-checks): every Fmt and Type pair that is no kind, each memory byte-enable break, 4 KiB crossings (a 1024-DW read included), configuration and I/O Length, Last BE, TC and Attr, a malformed locked read, malformed and poisoned; each bit 6, dropped, no completion, released by the `dll`'s class (none for a completion kind); the legal neighbours pass | `inb_malformed` | 1 | 1 |
| Ordering and [transmit](../../rtl/rc_tl/REQUESTER.md#transmit) priority: a DMA write lands before a later read's completion and a later `HOST_RD`; a completion held back by a requester posted write waiting for credits, which then goes first; a completion before a non-posted request; a non-posted request without credits does not block a completion; a completion without completion header or data credits lets a non-posted request go; forward progress under back-to-back 32-DW writes and 64-DW reads: every `HOST_*` waits at most 33 cycles, and the `u_mem` assertions hold on every access | `inb_order` | 1 | 1 |
| Inbound [link down](../../rtl/rc_tl/MAS.md#reset-and-link-down): a full non-posted queue and a completion being sent dropped with no completion and no release; a write cut after 5 DWs keeps them; a posted release pulsing when `dl_up_i` falls inside that cycle is cut at once; captures, host memory and the error record kept; served again after link-up | `inb_link_down` | 1 | 1 |
| Host memory filled with a seeded pattern, then random inbound traffic of every kind (writes, reads up to 300 DWs, MSIs, messages, Unsupported Requests, malformed, any Fmt and Type, poisoned, TD, 4-DW headers) mixed with `HOST_*`, local and endpoint commands, link stalls, receive gaps, credit outages (completion credits included), Bus Master Enable changes, `msi_ready_i` at random, link drops and resets, against the model | `random_inb` | 1 | 1; 700 TLPs |
| The same with 256 bytes of host memory at `0xF00` (requests past its end) and `FC_NPH` 1 | `random_inb_aw8` | 1 | 1; 700 TLPs |
| Long random commands | `random_long` | 2 | 2; 30000 commands each |
| Long random endpoint commands, as `random_req` | `random_req_long` | 2 | 2; 20000 commands each |
| The completion timeout at the FPGA value, N = 2^21 cycles | `req_timeout_div1` | 2 | 1; `CPL_TIMEOUT_DIV` 1 |
| Long random inbound traffic, as `random_inb` | `random_inb_long` | 2 | 2; 12000 TLPs each |

**Level placement.** `random_req_long` and `random_inb_long` are long
random tests and `req_timeout_div1` a long one (2^21 cycles of one wait);
`req_timeout` and `req_timeout_div1000` check the same bound at L1. The
inbound L1 entries are short directed tests and two 700-TLP random runs,
about 15 s of CPU together ([#389](https://github.com/amichai-bd/pcie-factory/issues/389)
L1 budget).

## Gaps

None: every feature of the MAS and its pages has a test.

## Coverage holes

Floors in [`level2.yaml`](../../../../src/dv/rc_tl/regress/level2.yaml),
checked by every `--coverage` run that selects level 2 (the nightly runs
levels 0 to 2): line 91.4, toggle 65.5, functional 100. Merged over levels
0 to 2 ([#380](https://github.com/amichai-bd/pcie-factory/issues/380)):
line 91.91% (943 of 1026 points), toggle 66.00% (31793 of 48168),
functional 100% (344 of 344 bins in `rc_tl_cmd`, `rc_tl_req`, `rc_tl_cpl`,
`rc_tl_inb`, `rc_tl_cplr`, `rc_tl_host` and `rc_tl_reset`). The code
floors are the merged values less 0.5 points, rounded down to a tenth
([floor margin](../../../bkm/dv.md#coverage)). Verilator keeps one copy
of each block per parameter set: the default, the IDs of `hdr_reset_ids`,
`CPL_TIMEOUT_DIV` 1024 and 1000, and the host memories of the `*_aw8`,
`*_aw16` and `FC_NPH` 1 entries; most copies run a few entries, which is
why toggle coverage is low while every bin is hit.

The 83 line points not hit, by cause:

| Cause | Points | Where |
| --- | ---: | --- |
| Hit in another parameter copy | 75 | `pcie_rc_tl_rx.sv` 50, `pcie_rc_tl_req.sv` 15, `pcie_rc_tl_mem.sv` 6, `pcie_rc_tl_cmd.sv` 4. |
| Outside the input contract | 5 | The `dll` drops every TLP with more than 128 bytes of data or a length that disagrees with its header ([inbound](../../rtl/rc_tl/INBOUND.md)), so no TLP reaches `rc_tl` with 64 or more beats or with Length 0 and data (1024 DWs): the beat counter's saturation in [`common_tlp_rx_track.sv`](../../../../src/rtl/common/common_tlp_rx_track.sv) (as in the [`tl` holes](../tl/DV_PLAN.md#coverage-holes)) and the Length 0 decode of a received completion's DW count in `pcie_rc_tl_rx.sv` (four copies). |
| Unreachable by construction | 3 | The lane-disable branch of each `common_sdp_ram` copy (`LANES` 1). |

The 16375 toggle points not hit, by cause:

| Cause | Points | Signals |
| --- | ---: | --- |
| Hit in another parameter copy | 12747 | The same signal bit and direction toggles in another copy: `pcie_rc_tl.sv` 7098, `pcie_rc_tl_rx.sv` 3893, `pcie_rc_tl_req.sv` 870, `pcie_rc_tl_cmd.sv` 588, `pcie_rc_tl_mem.sv` 212, `pcie_rc_tl_cpl.sv` 70, the common RAMs 12, `pcie_rc_tl_hdr.sv` 4. |
| Header fields every TLP `rc_tl` builds holds constant | 1750 | The request and completion DWs from `req_dw0`, `req_dw1`, `cfg_dw2` and `cpl_dw*` in the requester, the completer, the transmit stage and the top (`req_tx`, `cpl_tx`, `tx_o`, `req_i`, `cpl_i`, `arb_*`, `dw0_len`, `typ`, `cls`, `dcred`, `rq_*.rid`, `rq_*.addr`): TD, EP, AT, TH and the reserved bits 0; TC and Attr 0 in requests; Length bits 6 to 9 0 (at most 32 DWs); Type 0 configuration requests only ([HAS](../../arch/rc/HAS.md), no Type 1); the IDs' device and function 0; the completer's status `SC` or `UR` and BCM 0; one data credit per request (1-DW writes); DW-aligned addresses. |
| Error vectors: another block's bits | 612 | `err_o`, `err`, `rx_err` and `req_err` in the requester, the receive block and the top: each source drives only its own bits of the 13-bit [error record](../../rtl/rc_tl/MAS.md#error-record-and-q2-cases), and each decode stage a subset of those. |
| Header registers outside the writable mask | 560 | `cmd_*`, `cls_*`, `bus_*` and `mem_*` in `pcie_rc_tl_hdr.sv`, both copies: the bits outside `RW_CMD`, `RW_CLS`, `RW_BUS` and `RW_MEM`, read-only 0. |
| Constant by construction | 341 | The receive decision's `mdata`, never set (`dec` starts at 0), and its `on` and `dbeg`, always 1; the header end (2 or 3 DWs) in `common_tlp_rx_track.sv` and `rp.hdr_end`; the completer's `rest` bit 10 in `cc_q` and `cc_d` (8: a read above 32 DWs first returns 32 DWs less its start offset within 64 bytes (`adw[3:0]`), at least 17, so `rest` is at most 1007 even for a 1024-DW read); the requester's `rem_dw` bits 7 and 8 (12: from the 8-bit `left` it is at most 65). |
| Outside the input contract | 140 | A received completion's DW count above 32: bits 6 to 10 of `cpl_o.dw_n` in the receive block (40), `rx_cpl.dw_n` in the top (70) and `cpl_i.dw_n` in the requester (30). No TLP with more than 128 bytes of data reaches `rc_tl`, as for the line points; a request's Length (the receive block's `dw_n`) does reach 1024. |
| Received fields `rc_tl` ignores | 56 | `dw0_q` bits 8 to 11, 15, 18 and 19 of the receive block (TH, LN, Attr bit 2, T8, T9 and AT, listed in `unused_hdr`): the endpoint never sets them, and `rc_tl` reads none of them. |
| Queues never full; RAM lanes | 31 | The non-posted queue's and the data queue's full flags and top fill bits in `pcie_rc_tl_cpl.sv` and its `common_sdp_ram_fifo`: the link's credit contract (at most `FC_NPH` unreleased non-posted TLPs) keeps them below their depth; the RAMs' lane enable (`LANES` 1), and bit 22 of two entries of one queue RAM, never written 1. |
| Not reached: data patterns | 138 | The MSI capture's entry 2 data bits (96: the bench's MSI data in that slot), the 16-bit host memory copy's request end bits 11 to 15 (40: its requests end in the first 8 KB), the completion timeout counter's top bit falling (2). Each is a value pattern of a register already checked bit for bit against the model; more patterns were rejected as low test value. |

How the points were sorted, so a re-measurement reproduces the counts: a
line point is keyed by file, line, column and kind, a toggle point by
file, signal bit and direction (any copy). A point not hit is "another
copy" when its key is hit anywhere in the run; the rest go by file and
signal name as the rows list them.
