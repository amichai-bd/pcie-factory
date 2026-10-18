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
([DV plan](DV_PLAN.md)).

## Independence

| Rule | How it holds |
| --- | --- |
| A different author from the architecture models | Written by a separate crewmate under [#288](https://github.com/amichai-bd/pcie-factory/issues/288); the PR records it. |
| Written from the spec and the HAS | Sources: the author's reading of the PCIe Base Spec rev 3.0, cited by section and never reproduced, and the HAS pages: [`core`](../../arch/core/HAS.md) with its [application](../../arch/core/APP.md) and [tl-cfg](../../arch/core/TL_CFG.md) pages, [`tl`](../../arch/tl/HAS.md) with [TLP formats](../../arch/tl/TLP.md), [completions](../../arch/tl/COMPLETIONS.md), [ordering](../../arch/tl/ORDERING.md) and [routing](../../arch/tl/ROUTING.md), [`cfg`](../../arch/cfg/HAS.md) with [registers](../../arch/cfg/REGISTERS.md) and [capabilities](../../arch/cfg/CAPS.md), and the [`link`](../../arch/link/HAS.md) and [`dll` upper stream](../../arch/dll/HAS.md#upper-tlp-stream). |
| No shared code | Nothing under `src/arch/`, `src/rtl/` or the `tl`, `cfg`, `dll` and `link` testbench models was read or imported. The host has its own TLP codec. |
| Checked | `test_independence_no_shared_code` parses the four host files. It fails on an import other than the standard library, pytest and the host's own two modules, on `importlib` or `runpy`, on a call to `__import__`, `import_module`, `exec`, `eval` or `compile` (by name or as an attribute, such as `builtins.__import__`), and on model code (identifiers and strings other than docstrings) with an `arch` path segment, an architecture module name or another testbench's models. |
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

## Unit tests

`./build ci unit` runs both test files as `models:core`
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
| Endpoint requests and their limits, messages, link down, raw requests | `test_endpoint_*`, `test_messages_both_ways`, `test_malformed_tlp_flagged`, `test_link_down_drops_state_then_reenumerates`, `test_send_request_raw_io_read` |
| Independence | `test_independence_no_shared_code` |

## Possible disagreements

None found: on every point the host checks, its reading of the Base Spec
agrees with the HAS pages listed above. The Base Spec is not in the local
cache (`./build specs list`), so the cited sections here, like those of the
[`core` HAS](../../arch/core/HAS.md#open-questions), still await a check
against rev 3.0 by the captain with a local copy.
