# pcie_core independent host model

The host (root complex) that the `core` testbench puts opposite the
endpoint ([#293](https://github.com/amichai-bd/pcie-factory/issues/293)): a
pure-Python model,
[`pcie_core_host_model.py`](../../../../src/dv/core/models/pcie_core_host_model.py),
with its TLP codec in
[`pcie_core_host_tlp.py`](../../../../src/dv/core/models/pcie_core_host_tlp.py).
It enumerates the function, sets up MSI and MSI-X, moves memory traffic
both ways and checks every completion it receives. It is the independent
oracle of F9 in decision record
[0004](../../decisions/0004-m3-grill.md#decisions), the second opinion
beside the [architecture model](../../arch/core/HAS.md#architecture-model),
whose host stand-in it does not replace there. Part of the `core` DV
([DV plan](DV_PLAN.md)). In the root-port e2e it runs in
[shadow mode](#shadow-mode) instead: it sends nothing and checks the
root port and the endpoint from both directions of the root port's link
(decision record [0005](../../decisions/0005-m4-grill.md#decisions), P5,
goal item 8).

## Independence

| Rule | How it holds |
| --- | --- |
| A different author from the architecture models | Written by a separate crewmate under [#288](https://github.com/amichai-bd/pcie-factory/issues/288); the PR records it. The shadow mode ([#372](https://github.com/amichai-bd/pcie-factory/issues/372)) is by a crewmate that is not the author of `rc_tl`, `rc_seq` or the `rc` architecture model and touches no `rc` code in M4. The rc e2e ([#377](https://github.com/amichai-bd/pcie-factory/issues/377)), whose author is not the author of `rc_tl`, the `rc_seq` interpreter or the `rc` architecture model but wired `pcie_rc`, added the `INJECT` program and set three architecture-model constants to the programs page's values, changed the shadow in two ways: the alignments the reviews of #391 and #397 asked for (the poisoned and CRS rules from the `rc` HAS, and the bench options below), and three readings the e2e found ([possible disagreements](#possible-disagreements)); each follows a cited HAS, MAS or Base Spec rule, and nothing of `rc` was imported. |
| Written from the spec and the HAS | Sources: the author's reading of the PCIe Base Spec rev 3.0, cited by section and never reproduced, and the HAS pages: [`core`](../../arch/core/HAS.md) with its [application](../../arch/core/APP.md) and [tl-cfg](../../arch/core/TL_CFG.md) pages, [`tl`](../../arch/tl/HAS.md) with [TLP formats](../../arch/tl/TLP.md), [completions](../../arch/tl/COMPLETIONS.md), [ordering](../../arch/tl/ORDERING.md) and [routing](../../arch/tl/ROUTING.md), [`cfg`](../../arch/cfg/HAS.md) with [registers](../../arch/cfg/REGISTERS.md) and [capabilities](../../arch/cfg/CAPS.md), and the [`link`](../../arch/link/HAS.md) and [`dll` upper stream](../../arch/dll/HAS.md#upper-tlp-stream). |
| No shared code | Nothing under `src/arch/`, `src/rtl/` or the `tl`, `cfg`, `dll` and `link` testbench models was read or imported. The host has its own TLP codec. |
| Root-port sources | The shadow mode is written from the [`rc` HAS](../../arch/rc/HAS.md) and its [tl-seq page](../../arch/rc/TL_SEQ.md); the #377 changes also read the `rc_tl` MAS pages (the options a bench sets and two loosened readings), never code: nothing under `src/arch/rc/`, `src/rtl/rc*` or the `rc` testbenches was imported. |
| Checked | `test_independence_no_shared_code` parses the six host files (model, codec and shadow, and their tests). It fails on an import other than the standard library, pytest and the host's own modules, on `importlib` or `runpy`, on a call to `__import__`, `import_module`, `exec`, `eval` or `compile` (by name or as an attribute, such as `builtins.__import__`), and on model code (identifiers and strings other than docstrings) with an `arch` path segment, an architecture module name, another testbench's models or a root-port module (`pcie_rc`, `rc_tl`, `rc_seq`, `dv/rc`). |
| Disagreements stay visible | Points where the host's reading may differ from a HAS are listed under [possible disagreements](#possible-disagreements), never settled by looking at a model. |

## Interface

The host works at the `link` upper TLP stream of its own `link` instance
(downstream, `port_role_i` 0) in a back-to-back bench. A TLP is header and
data as bytes, byte 0 (Fmt and Type) first, no sequence number or LCRC;
on the 32-bit stream byte 0 is bits `[7:0]` of the `sop` beat
([`dll` upper TLP stream](../../arch/dll/HAS.md#upper-tlp-stream)).

| Call | Use |
| --- | --- |
| `pop_tx(avail)`, `pop_tx_beats(avail)` | The next TLP to send, or None. `avail` maps `ph`, `pd`, `nph`, `npd`, `cplh`, `cpld` to the host link's `fc_*_avail_o` (all ones infinite). See [credits and ordering](#credits-and-ordering). |
| `receive(raw)`, `receive_beats(beats)` | Take one TLP the host's link delivered. The host never stalls: it takes every TLP at once. Returns the TLP's credit cost (class, header, data) for a bench that runs the host's link with the [credit release](../../arch/dll/HAS.md#credit-release); with `FC_RELEASE` 0 it is ignored. |
| `tick(cycles)` | Advance the host clock (one call per `clk_i` cycle): request timers. |
| `cfg_read(bdf, reg, be)`, `cfg_write(bdf, reg, value, be)` | One Type 0 (or `type1=True`) configuration request; returns a `Request`. |
| `mem_read(addr, nbytes)` | One memory read of 0 to 4096 bytes inside one 4 KiB block; 0 is a zero-length read. |
| `mem_write(addr, data)`, `mem_write32(addr, value)` | Posted writes, split at the host's maximum payload and at 4 KiB. |
| `send_message(code, routing, data)` | A message to the endpoint (Set_Slot_Power_Limit, PME_Turn_Off). |
| `send_request(tlp)` | Any TLP the bench builds, for negative cases (an I/O read, a configuration read to function 1): a non-posted one gets a tag and waits for one completion. |
| `start(procedure)` | Run a generator procedure (below); returns a `Procedure` with `done`, `result` and `error`. |
| `link_down()` | The host's link went down: drop the queue, outstanding requests (status `link_down`), procedures and enumeration state. |
| `idle`, `outstanding()`, `errors`, `interrupts`, `messages`, `memory`, `devices` | State for the bench's checks and liveness. |

A `Request` holds `tag`, `status` (`SC`, `UR`, `CA`, `CRS`, `poisoned`,
`timeout`, `error`, `link_down`, or None while outstanding), `value` (a
configuration read), `data` (the enabled bytes of a memory read),
`completion_sizes` and its own `errors`.

**Procedures** are generators that yield a `Request` or a list of them and
resume, with them, when all are done; `start` advances them as completions
arrive, so the same flow runs under cocotb (the bench pumps `pop_tx` and
`receive` each cycle and waits for `proc.done`) and under the pure-Python
unit tests. The host provides:

| Procedure | Returns |
| --- | --- |
| `enumerate_device(bdf, bus_master, memory)` | A `DeviceInfo`: IDs, class, header type, capability list, extended capabilities, BARs with size and assigned base, MSI and MSI-X capability fields, PCI Express capability offset, port type and supported payload. |
| `setup_msi(dev, address, data)` | MSI programmed and enabled. |
| `setup_msix(dev, {vector: (address, data)}, unmask)` | MSI-X enabled, table written, vectors unmasked. |
| `read_msix_pba(dev)` | The pending bits as an integer. |
| `read(addr, nbytes, max_read_request)` | The bytes, read with several requests outstanding at once. |

`msix_mask(dev, vector, masked)` and `msix_function_mask(dev, masked)` are
single requests. `strict=True` (the default) raises `HostCheckError` at the
first violation, which fails a cocotb test; `strict=False` records it in
`errors` and carries on, for negative tests.

## Behaviour

### Enumeration

[cite: PCIe Base Spec, rev 3.0, §7.5.1] [cite: PCIe Base Spec, rev 3.0, §7.5.2]

1. A configuration write of Command 0 (bytes 0 and 1): decodes off, and the
   function captures its bus and device numbers from it before it sends any
   completion [cite: PCIe Base Spec, rev 3.0, §2.2.6.2].
2. Reads of the ID, class and revision, header type (Type 0 required) and
   subsystem DWs. Vendor ID FFFFh means no function.
3. The capability walk, then BAR sizing and assignment (below).
4. Command: Memory Space Enable and Bus Master Enable, as asked.

**Capability walk.** If Status bit 4 (Capabilities List) is set, the walk
starts at the Capabilities Pointer with its low 2 bits masked, and follows
each Next pointer until 0. A pointer below 0x40, a pointer seen before or a
49th entry fails the walk. The extended list starts at 0x100; each header
gives the ID in bits 15:0, the version in 19:16 and the Next offset in
31:20 (low 2 bits masked). It ends at a DW of 0 or all ones, or a Next of
0; a Next below 0x100 or seen before fails the walk
[cite: PCIe Base Spec, rev 3.0, §7.9.1]. The host decodes PM, MSI (64-bit
capable, Multiple Message Capable, per-vector masking), MSI-X (table size,
table and PBA BIR and offset) and PCI Express (port type, supported
payload); for the last it writes Device Control Max_Payload_Size to the
smaller of its own and the function's [cite: PCIe Base Spec, rev 3.0, §7.8.4].

**BAR sizing and assignment.** With decodes off, each BAR in turn is
written all ones and read back [cite: PCIe Base Spec, rev 3.0, §7.5.2.1]:
0 is unimplemented; bit 0 set is an I/O BAR (recorded, not assigned);
memory type `10` is 64-bit and takes the next BAR as its upper half; the
size is the two's complement of the address bits and must be a power of two
of at least 16 bytes. Each memory BAR gets the next base in the host's
memory window (from 0x8000_0000 by default) aligned to its size; the host
reads it back and fails on a mismatch. The Expansion ROM BAR is sized the
same way (enable bit 0 written 0) and recorded only.

### MSI and MSI-X

[cite: PCIe Base Spec, rev 3.0, §6.1.4] [cite: PCIe Base Spec, rev 3.0, §7.7]

Software never enables both, so each set-up first disables the other.
**MSI:** Message Address (bits 1:0 0), Upper Address when 64-bit capable,
Message Data (16 bits), then MSI Enable with Multiple Message Enable 0.
**MSI-X:** MSI-X Enable with Function Mask set, each table entry (address,
upper address, data, vector control) written as aligned DWs through the
table BIR's BAR, then Function Mask cleared. An endpoint memory write to a
programmed address is an interrupt: it must be one whole DW (First DW BE
`1111`, Last 0), its data must match a programmed vector at that address,
and its requester must be the function that vector belongs to. The host
logs an `Interrupt` (kind, vector, address, data, requester) and calls
`on_interrupt`.

### Memory reads and completion checks

A read of n bytes at byte address a goes out as one MRd with the byte
enables of its first and last DW; its total T and first byte B are those of
the request [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]. A completion matches
when its Requester ID is the host's and its tag is outstanding; anything
else is an unexpected completion [cite: PCIe Base Spec, rev 3.0, §2.3.2].
On a match the host checks, and fails on any miss:

| Check | Rule |
| --- | --- |
| Completer ID | The target function (configuration), or the function whose BAR holds the address (memory). |
| Kind, TC, Attr | Not a locked completion for an unlocked read; TC and Attr bits 1:0 the request's. |
| Configuration | Byte Count 4, Lower Address 0; one DW of data for a successful read, none for a write or an error status [cite: PCIe Base Spec, rev 3.0, §2.2.9]. |
| Byte Count | The bytes still owed (0 read as 4096). |
| Lower Address | Bits `[6:0]` of the next byte owed. |
| Data | Starts at that byte's DW; never ends past the request's last DW; at most the host's maximum payload. |
| Split | A completion that leaves bytes owed ends on a multiple of the completer's RCB (64 bytes), the finest legal split [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]. |
| Status | Successful needs data. Unsupported Request or Completer Abort ends the read with no data, its Byte Count and Lower Address those of the bytes owed. A reserved status is handled as UR [cite: PCIe Base Spec, rev 3.0, §2.3.2] and also flagged, since the endpoint sends only SC and UR ([TLP formats](../../arch/tl/TLP.md#completions)). Configuration Request Retry is legal only for a configuration request [cite: PCIe Base Spec, rev 3.0, §2.3.1]: on a memory read it is flagged and ends the read. |
| Poisoned | Recorded as an error; the read ends `poisoned`. |

The read keeps the enabled bytes in address order and finishes when no
byte is owed. `tick` fails a request still unanswered `timeout` cycles
after it was sent, if a timeout is set; a completion for it after that is
unexpected.

### Tags

Tags 0 to `num_tags` − 1, 32 by default (no extended tags
[cite: PCIe Base Spec, rev 3.0, §2.2.6.2]); every non-posted request takes
the lowest free one when it is queued. With none free, requests wait in
order and take tags as reads finish; a tag is free again when its request
finishes (last completion, error status or timeout). Completions of
different tags may arrive in any order.

### Credits and ordering

`pop_tx` keeps three classes (posted, non-posted, completion), each in
order, and returns the first TLP the credits allow: a header credit and one
data credit per 16 bytes of payload, all ones being infinite
[cite: PCIe Base Spec, rev 3.0, §2.6.1]. A non-posted request or a
completion never passes an earlier posted request still waiting; a posted
request passes a waiting non-posted request or completion
[cite: PCIe Base Spec, rev 3.0, §2.4.1]. The host's link gates on the same
credits; the host only avoids offering what the link cannot take.

### Endpoint requests

A memory write from the endpoint goes to `memory` with its byte enables; a
memory read is answered from it with completions split on the host's RCB
(`cpl_split` `fewest`: the largest the payload allows, ending on 64-byte
boundaries; `rcb`: one per 64-byte block, to stress the endpoint's
tracker), carrying the request's TC and Attr. `read_status` can make the
host answer Unsupported Request or Completer Abort. Once a function is
enumerated, every endpoint request (interrupts included) must come from it
with Bus Master Enable set as the host last wrote it
[cite: PCIe Base Spec, rev 3.0, §7.5.1.1], must not cross 4 KiB
[cite: PCIe Base Spec, rev 3.0, §2.2.7], and a write carries at most the
maximum payload. A read must also keep to the endpoint's own limits
([`core` parameters](../../arch/core/HAS.md#parameters), F2): a tag of 0
to 7 (extended tags off) and at most 128 bytes (`ep_tags`,
`ep_max_read_request`). Messages are logged (code, routing, requester, data).

### Use in the core e2e

The bench ([#293](https://github.com/amichai-bd/pcie-factory/issues/293))
drives the host link's `tlp_tx_*` from `pop_tx_beats` with the host link's
`fc_*_avail_o`, holds `tlp_rx_ready_i` at 1 and passes each received TLP to
`receive_beats`, calls `tick` every cycle and `link_down` when the host
link's `dl_up_o` falls. The application model on the endpoint side owns the
[write-completion](../../arch/core/APP.md#write-completion) obligation
(finish an `rq` write once its `sop` beat is taken); the host does not.

## Shadow mode

[`pcie_core_host_shadow.py`](../../../../src/dv/core/models/pcie_core_host_shadow.py)
holds `HostShadow`, the host model in passive mode, as the `dll` partner's
[passive mode](../dll/PASSIVE.md) is in the `link` e2e. In the root-port
e2e ([#377](https://github.com/amichai-bd/pcie-factory/issues/377)) the
host side is `rc`; the testbench shows the shadow what crosses the root
port's `link` upper TLP stream and what `rc_tl` reports to `rc_seq`, and
the shadow checks both sides against its own prediction. It generates no
traffic. It reuses the host model's checks and its TLP codec and needs
nothing else.

### Shadow API

| Call | The testbench calls it when |
| --- | --- |
| `HostShadow(vendor_id, device_id, revision_id, host_mem_base, host_mem_addr_w, msi_addr, ...)` | Built, and rebuilt on each `rst_i`, with `rc`'s [parameters](../../arch/rc/HAS.md#parameters). Further arguments below. |
| `observe_dl_up(up)` | `rc`'s `dl_up_o` changes. A fall is a [link down](../../arch/rc/HAS.md#link-down). |
| `observe_command(op, addr, length, first_be, last_be, data)` | `rc_tl` takes a command (`cmd_valid` and `cmd_ready` 1): its [fields](../../arch/rc/TL_SEQ.md#commands). |
| `observe_tx(raw)`, `observe_tx_beats(beats)` | **A TX TLP:** `rc`'s `link` takes the last beat of a TLP on its transmit stream (`rc_tl` to `link`). |
| `observe_rx(raw)`, `observe_rx_beats(beats)` | **An RX TLP:** `rc`'s `link` delivers a TLP on its receive stream. |
| `observe_result(status, data, last)` | **An `rc` result:** one `rsp_valid` beat ([results](../../arch/rc/TL_SEQ.md#results)); `status` by number or name. |
| `observe_msi(offset, data)` | `rc_seq` takes an [MSI capture](../../arch/rc/TL_SEQ.md#msi-capture) (`msi_valid` and `msi_ready` 1). |
| `tick(cycles)` | Every `clk_i` cycle. |
| `finish()` | The test ends: every command has its result and every endpoint request its answer. |
| `errors`, `stats`, `pending_msi`, `devices`, `header(index)` | State for the testbench's checks; `stats` counts what was judged, so a test shows it is not vacuous. |

The other arguments: `num_tags` (32, no extended tags), `latency` (32
cycles that `rc` may take to act on a received TLP: its result, record,
capture or host-memory effect), `timeout_min` and `timeout_max` (the
completion timeout window in cycles from the request going out; the
`rc_tl` MAS sets the value, [#368](https://github.com/amichai-bd/pcie-factory/issues/368)).
A request's send time is the cycle of its `observe_tx` call, the cycle
`link` takes its **last** beat, while `rc_tl` starts its timer when `link`
takes its `sop` beat; the bench gives `timeout_min` less the request's
beats (3 for a read, 4 for a 1-DW write) so the shadow is never stricter.
Then `msi_depth` (4, the least capture depth the tl-seq page allows),
`msi_depth_exact` (false; a bench that knows the depth is exactly
`msi_depth`, as the rc e2e does from the `rc_tl` MAS (D10), sets it, and
an MSI arriving with that many captures held after the cycle's take is
then surely dropped and surely sets bit 12), `zero_be_refused` (none; the
ops whose zero First DW BE the root port surely refuses with `LOCAL_UR`,
`CFG_RD`, `CFG_WR` and `MEM_WR` in the rc e2e from the `rc_tl` MAS's
[byte-enable rule](../../rtl/rc_tl/REQUESTER.md#byte-enables)),
`bar_data` (predict endpoint memory reads from what `rc` wrote there:
true while the endpoint's BAR behaves as memory, as BAR0 does in the
`core` application model) and `strict`.

**Call order.** In each `clk_i` cycle the bench reports what the rising
edge took, in this order: `observe_dl_up(True)` if `dl_up_o` rose;
`observe_tx` for a TLP whose last beat `link` took; `observe_rx` for a TLP
`link` delivered; `observe_command`; `observe_result`; `observe_msi`;
`observe_dl_up(False)` if `dl_up_o` fell; then `tick(1)`. A link down is
thus reported after the TLPs of its own cycle, so a TLP taken as
`dl_up_o` falls still matches its command, and a completion delivered in
the same cycle as its result is seen first.

### What it predicts

The shadow keeps its own copy of the root port's state, from the
commands it observes: the [Type 1 header](../../arch/rc/HAS.md#type-1-header)
(`HDR_WR`), host memory (`HOST_WR` and the endpoint's DMA writes) and the
error record (events and `ERR_CLR`). From the configuration traffic it
also learns, passively, the function, its BAR sizes and bases, its
capability list, its MSI programming and the Command register as `rc`
wrote it, which the host model's endpoint checks then use. A link down
forgets what it learnt of the endpoint, which resets itself.

| Side | Checks |
| --- | --- |
| `rc` requests (TX) | Well formed [cite: PCIe Base Spec, rev 3.0, §2.2.5] [cite: PCIe Base Spec, rev 3.0, §2.2.7]: decodes; no TD or EP; byte enables legal (Last DW BE 0 for 1 DW; both fields non-zero for more; for 3 DW or more and for a 2-DW request that is not QW aligned, only contiguous bytes: First DW BE 1111, 1110, 1100 or 1000 and Last DW BE 0001, 0011, 0111 or 1111); configuration requests TC 0, Attr 0, Length 1; memory requests with 3-DW headers and at most 128 bytes of payload. Each is exactly the TLP the next unsent endpoint command asks for (kind, Requester ID from the Primary Bus Number, address or bus, device, function and register, Length, byte enables, enabled data bytes), in command order, a posted write passing non-posted requests but never the reverse [cite: PCIe Base Spec, rev 3.0, §2.4.1]. A command the [outbound routing](../../arch/rc/HAS.md#outbound-routing) refuses (another bus or device; Memory Space Enable 0; outside the memory window) or the [field checks](../../arch/rc/TL_SEQ.md#commands) refuse is never sent. A non-posted request uses a tag from 0 to `num_tags` − 1 that is not outstanding. |
| `rc` completions (TX) | Each answers an endpoint request still waiting, with `rc`'s ID as Completer ID and the request's TC and Attr, with the status the [inbound routing](../../arch/rc/HAS.md#inbound-routing) predicts (served from host memory, or Unsupported Request), Byte Count, Lower Address, the 64-byte split rule and the data as host memory held it when the read arrived (the host model's completion checks, applied to `rc` as completer). Every endpoint non-posted request is answered, unless a link down drops it. |
| Endpoint completions (RX) | The host model's [completion checks](#memory-reads-and-completion-checks) and [tags](#tags) (a configuration completion before the function's first configuration write may carry bus and device 0, not yet captured [cite: PCIe Base Spec, rev 3.0, §2.2.6.2] ([`core` TL_CFG](../../arch/core/TL_CFG.md), register state)), plus predictions: a BAR read after its base was written returns that base and its type bits; the ID, class and subsystem DWs never change; a memory read returns the bytes `rc` last wrote there (`bar_data`). |
| Endpoint requests (RX) | Routed as the [inbound routing](../../arch/rc/HAS.md#inbound-routing) says, then poisoned: a request or message the routing would serve, capture or record, with EP 1, sets bit 5 only and has no other effect, but a poisoned non-posted one is answered with an Unsupported Request completion; the routing's Unsupported Request outranks poisoned, bit 11 only [cite: PCIe Base Spec, rev 3.0, §2.7.2.2]. The host model's [endpoint request](#endpoint-requests) rules: from the enumerated function with Bus Master Enable as `rc` wrote it, inside 4 KiB, the endpoint's tag and read limits, interrupt writes matching the MSI address and data `rc` programmed. |
| `rc` results | In command order. Local commands: `HDR_RD` the header DW, `HOST_RD` the host memory bytes the shadow knows, writes and `ERR_CLR` `OK` with data 0, a refused command `LOCAL_UR`, an endpoint command while `dl_up_o` is 0 `LINK_DOWN`. Endpoint commands: the status from the endpoint's completion (`OK`, `UR`, `CA`, `CRS`; `BAD` for a poisoned completion, a reserved status or a successful one without data; `TIMEOUT`; `LINK_DOWN`), a `MEM_RD`'s DWs in address order, the enabled bytes as the completions carried them (a beat may come while the completion carrying its DW is still arriving: it is checked once that completion is whole, and fails if no completion carries it), and a configuration read's DW; a failing read may report its first DWs `OK` before its failing last beat, whose data is 0. |
| MSI captures | In arrival order, each the DW offset in the [MSI window](../../arch/rc/HAS.md#msi-window) and the data of a 1-DW endpoint write into it with Bus Master Enable 1. |
| Error record | An `ERR_RD` has every bit whose event was surely recorded before the command and after that bit's last clear, and no bit whose event cannot have been recorded by the result; bits 31:13 are 0. Events: bits 0 to 2 for UR, CA and configuration CRS completions; 3 a `TIMEOUT` result; 4 an unexpected or late completion; 5 a poisoned request or message, or a poisoned successful completion that is not malformed; 6 a TLP the shadow cannot decode, or a completion the root port judges malformed: a reserved status, CRS to a memory read, or a successful completion with no or the wrong data (configuration), or, for a memory read, without data or with a wrong Byte Count or Lower Address, data past the request or a split off 64 bytes (each surely, the request then ending `BAD` at once and later completions for it unexpected, bit 4; a status other than Successful decides first, and EP on it has no effect); 7 to 10 messages; 11 an inbound Unsupported Request; 12 a capture that may have been dropped. |

### Allowances

Each makes a check looser than `rc`'s real knowledge, never stricter,
and each is tied to a rule the HAS leaves to the `rc_tl` MAS or to timing:

- **Byte enables.** A 1-DW command whose First DW BE is 0 may be refused with
  `LOCAL_UR` or sent (a zero-length request is legal; whether `rc_tl`
  sends it is the `rc_tl` MAS's, [commands](../../arch/rc/TL_SEQ.md#commands)).
  A command whose byte enables break the legality rule above (Last DW BE
  set for 1 DW, a zero field for more, or non-contiguous bytes for 3 DW or
  more or a 2-DW request not QW aligned) must be refused with `LOCAL_UR`:
  it is accepted refused, and sent it fails the well-formed check.
- **Latency.** An event counts as surely recorded `latency` cycles after
  its TLP arrived. A host-memory byte is predicted only when the write
  that set it is certain to have landed: a sequencer write, a DMA write
  that arrived before the endpoint read or the MSI the sequencer took, or
  one older than `latency` cycles.
- **Captures.** An MSI arriving with `msi_depth` captures untaken may be
  dropped (bit 12 then may be set), unless `msi_depth_exact`.
- **Timeouts.** `TIMEOUT` is accepted no earlier than `timeout_min`, also
  for a read whose completion arrived after that; the late completion is
  then the record's unexpected completion, not an endpoint error.
- **Unsure outcomes.** Where the endpoint broke a rule the shadow already
  reported (with `strict=False`), the result of that request is not
  judged further, unless the root port judges the completion malformed
  (above): then the result is `BAD` and bit 6 surely set. A CRS
  completion to a memory read is such a case (CRS answers configuration
  requests only): bit 2 never. A rule the root port does not judge (a
  Completer ID, TC or Attr) leaves bit 6 clear.

### Errors it raises

Every finding is a `HostCheckError` (`strict`, the default) or a line in
`errors`, `[host @<cycle>] <side>: <what>`, the side being `rc` or
`endpoint`. For example: `rc: MRd with tag 0, still outstanding`;
`rc: CfgWr0 sent (tag 0) that no pending command asks for` (with the expected
and the sent TLP); `rc: CplD tag 1: host memory byte 0x100 sent as 0x6f,
holds 0xef`; `rc: MEM_RD 0x80f00000: result OK, predicted UR`;
`rc: MSI capture ... with none predicted`; `rc: ERR_RD: ... predicted OK
with bits 0x0880 set and none outside 0x0880`; `endpoint: configuration
read of 1:0.0 reg 0x10: 0x90000000, predicted 0x80000000`;
`endpoint: interrupt write ... matches no programmed vector`.

### Not modelled

MSI-X and multiple MSI messages (`rc` programs one MSI vector), I/O,
Type 1 configuration requests and messages from `rc` (it sends none),
and the order of `rc`'s completions relative to its own requests, which
depends on `rc_tl`'s internal queues.

## Unit tests

`./build ci unit` runs the three test files as `models:core`
([ci SPEC](../../../tools/ci/SPEC.md#build-ci-unit)). The far end is an
endpoint stub written in the test from the `cfg` register and capability
pages and the `tl` completion page; a pump moves TLPs both ways in order,
choosing the side with a seed.

| Behaviour | Tests |
| --- | --- |
| TLP formats, byte enables, beats, credit cost | `test_pcie_core_host_tlp.py` (hand-worked byte vectors) |
| Configuration reads and writes, completer ID, configuration Byte Count | `test_cfg_*` |
| Enumeration: IDs, capability walk, extended list, BARs (default, 1 MiB, 64-bit prefetchable, I/O), Expansion ROM, loop, bad-pointer and read-back failures | `test_enumeration_*`, `test_capability_loop_detected`, `test_extended_capabilities_walked`, `test_extended_capability_bad_pointer_fails`, `test_io_bar_and_rom_sized`, `test_bar_assignment_read_back_checked` |
| MSI and MSI-X set-up, masking, pending bits, Function Mask, wrong data, no bus master | `test_msi_*`, `test_msix_*`, `test_interrupt_without_bus_master_flagged` |
| Memory writes and reads, the [splitting table](../../arch/tl/COMPLETIONS.md#splitting), partial DWs, zero length, UR and CA | `test_write_then_read_back`, `test_split_completions`, `test_unaligned_partial_dw_read`, `test_zero_length_read`, `test_zero_byte_read_procedure`, `test_split_on_rcb_accepted`, `test_unsupported_request_status`, `test_completer_abort_ends_read` |
| Each completion check fails when broken | `test_wrong_byte_count_flagged`, `test_wrong_lower_address_flagged`, `test_split_off_boundary_flagged`, `test_data_past_request_flagged`, `test_poisoned_completion_flagged`, `test_success_without_data_flagged`, `test_crs_on_memory_read_flagged`, `test_reserved_status_read_as_ur_and_flagged`, `test_payload_above_max_flagged`, `test_strict_mode_raises` |
| Tags, unexpected and duplicate completions, timeout | `test_tags_limited_reused_and_out_of_order`, `test_unexpected_completion_flagged`, `test_duplicate_completion_flagged`, `test_completion_timeout` |
| Credits and ordering | `test_credit_gating_and_ordering`, `test_np_never_passes_blocked_posted`, `test_receive_returns_credit_cost` |
| Endpoint requests and their limits, messages, link down, raw requests | `test_endpoint_*`, `test_command_byte_enables_kept`, `test_messages_both_ways`, `test_malformed_tlp_flagged`, `test_link_down_drops_state_then_reenumerates`, `test_send_request_raw_io_read` |
| Shadow mode: recorded active-mode traffic (enumeration, MSI, MMIO with several completions, DMA both ways, endpoint and inbound Unsupported Requests, refused commands, header, host memory, error record, a completion timeout, a link down and re-enumeration) passes with no error | `test_recorded_*`, `test_command_while_link_down_is_link_down`, `test_byte_enables_the_mas_may_refuse` (`test_pcie_core_host_shadow.py`) |
| Shadow mode: each class of seeded mismatch on the recorded stream raises `HostCheckError` (`rc` requests: malformed, poisoned, byte enables, busy or out-of-range tag, wrong fields or Requester ID, unrouted, a read passing a write; `rc` completions: data, status, Byte Count, Completer ID, unrequested, missing; endpoint: Byte Count, Completer ID, BAR read-back, IDs, memory data, interrupt data, requester, unexpected and malformed TLPs; results: data, status, `LOCAL_UR`, missing, extra, beat count, header, host memory, early; MSI captures; error record bits; timeouts; link down) | `test_seeded_mismatch_raises`, `test_non_strict_records_and_carries_on`, `test_timeout_*`, `test_link_down_result_must_be_link_down`, `test_header_survives_link_down`, `test_msi_capture_may_drop_when_full`, `test_inbound_*` (the memory window over host memory), `test_non_contiguous_byte_enables` and `test_qw_aligned_two_dw_any_byte_enables` (byte-enable legality: refused passes, sent raises) |
| Shadow mode, #377 rules: the poisoned rule (served read: Unsupported Request completion, bit 5 only; refused: bit 11 only; poisoned write not stored), CRS to a memory read (`BAD`, bit 6 sure, bit 2 never), the exact capture depth (the fifth MSI surely dropped, a take in its arrival cycle makes room), the zero First DW BE refusal, the Completer ID before the first configuration write | `test_malformed_completion_*`, `test_poisoned_ur_completion_is_ur`, `test_poisoned_malformed_completion_is_bit_6`, `test_completer_id_mismatch_sets_no_bit_6`, `test_poisoned_read`, `test_poisoned_read_served_with_data_raises`, `test_poisoned_write_dropped`, `test_crs_on_memory_read_is_bad_and_malformed`, `test_msi_drop_exact_depth`, `test_msi_take_in_the_arrival_cycle_makes_room`, `test_zero_first_be_refused`, `test_completer_id_before_the_first_configuration_write` |
| Independence | `test_independence_no_shared_code` |

## Possible disagreements

One, found by the core e2e ([#293](https://github.com/amichai-bd/pcie-factory/issues/293)):
the Completer ID of an Unsupported Request completion to a Type 0
configuration request for a function other than 0. The host expects the
addressed function; `tl` sends this function's ID, function 0
([`tl` TLP fields](../../arch/tl/TLP.md)). The e2e sends that request with
`send_request` and checks only its status; the point is an open question
on the [`core` HAS](../../arch/core/HAS.md#open-questions).

The shadow mode adds two readings where the `rc` HAS wording is looser
than the shadow, both confirmed by the `rc_tl` MAS in review
([#387](https://github.com/amichai-bd/pcie-factory/pull/387)):

| Point | Shadow | `rc` HAS | `rc_tl` MAS (#387) |
| --- | --- | --- | --- |
| Completion matching | By Requester ID and Tag; any other completion is unexpected (error bit 4) [cite: PCIe Base Spec, rev 3.0, §2.3.2]. | "Matched to its outstanding request by tag", the matching left to the `rc_tl` MAS ([inbound routing](../../arch/rc/HAS.md#inbound-routing)). | Requester ID and Tag; anything else unexpected, bit 4. |
| A `MEM_WR` sent just before a link down | `OK`, not `LINK_DOWN`: a posted write is done once `link` took its last beat. | `OK` means a `MEM_WR` sent ([results](../../arch/rc/TL_SEQ.md#results)). | `OK` the cycle after `link` takes `eop`. |

The rc e2e ([#377](https://github.com/amichai-bd/pcie-factory/issues/377))
found three readings the shadow had stricter than the pages; each is
loosened to what the pages state:

| Point | Was | Now, and why |
| --- | --- | --- |
| Completer ID before the first configuration write | The addressed function's ID. | Also bus and device 0: the function captures its numbers from configuration writes only, and its `bus_num` is 0 until the first ([`core` TL_CFG](../../arch/core/TL_CFG.md)); the `ENUM` program reads before it writes ([programs](../../rtl/rc_seq/PROGRAMS.md#enumeration)). |
| A read's result beat before its completion is whole | An error. | Checked when the completion arrives: `rc_tl` gives each DW in the cycle after the link delivers it ([`rc_tl` timing](../../rtl/rc_tl/MAS.md#timing)), and TL_SEQ only requires the DWs in address order. |
| A completion that breaks a completion check (Byte Count, Lower Address, data past the request, a split off 64 bytes, no data) | Not judged: bit 6 not allowed. | Predicted from the rows of [`rc_tl` completion matching](../../rtl/rc_tl/REQUESTER.md#completion-matching): `BAD`, bit 6 surely set, the request ended and later completions for it unexpected (bit 4, and still reported as the endpoint's); a poisoned one that is also malformed sets bit 6, not 5; EP on a completion with a status other than Successful has no effect. Other host checks (Completer ID, TC, Attr) are not the root port's, so bit 6 stays clear (#377 audit by the shadow's author). |

On every other
point the host checks, its reading of the Base Spec agrees with the HAS
pages listed above. The Base Spec is not in the local
cache (`./build specs list`), so the cited sections here, like those of the
[`core` HAS](../../arch/core/HAS.md#open-questions), still await a check
against rev 3.0 by the captain with a local copy.
