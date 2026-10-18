# pcie_tl TLP formats

The TLP headers [`pcie_tl`](HAS.md) sends and reads: for each field, its
position, what `tl` sends and how it reads it on receive. Part of the
`tl` HAS. What `tl` does with each received TLP is on the
[routing page](ROUTING.md); how completions are formed and matched, on the
[completions page](COMPLETIONS.md).

Positions count header bytes from 0, the byte in bits `[7:0]` of the
`sop` beat on the `dll` stream ([upper TLP stream](../dll/HAS.md#upper-tlp-stream));
bit 7 is a byte's most significant bit. Every TLP `tl` sends is whole DWs,
with no prefix and no digest, and at most 128 bytes of data. Its ID
(Requester ID of a request or message, Completer ID of a completion) is
`bus_num` and `dev_num` as its `sop` beat is first offered to the link. A reserved
field is sent as 0 and ignored on receive
[cite: PCIe Base Spec, rev 3.0, §2.2.1].

## Kinds

Fmt is byte 0 bits `[7:5]`, Type byte 0 bits `[4:0]`
[cite: PCIe Base Spec, rev 3.0, §2.2.1]. Fmt bit 5 is a 4-DW header, bit 6
"with data"; Fmt `100` is a TLP prefix.

| Kind | Fmt, Type | `tl` sends | `tl` receives |
| --- | --- | --- | --- |
| Memory read | `000` or `001`, `00000` | Requester reads (`rq`) | Host reads |
| Memory write | `010` or `011`, `00000` | Requester writes, interrupt writes | Host writes |
| Locked memory read | `000` or `001`, `00001` | Never | Unsupported Request |
| I/O read, write | `000`, `010`; `00010` | Never | Unsupported Request |
| Configuration Type 0 read, write | `000`, `010`; `00100` | Never | Served by `cfg` |
| Configuration Type 1 read, write | `000`, `010`; `00101` | Never | Unsupported Request |
| Atomic operations (FetchAdd, Swap, CAS) | `010` or `011`; `01100`, `01101`, `01110` | Never | Unsupported Request |
| Message, without and with data | `001`, `011`; `10rrr` | Error messages, PME_TO_Ack | [Message table](ROUTING.md#messages) |
| Completion, without and with data | `000`, `010`; `01010` | Completions | Matched by tag |
| Locked completion | `000`, `010`; `01011` | The Unsupported Request completion of a locked read | Unexpected |

Any other Fmt and Type pair, a TLP prefix included, is malformed
([format checks](ROUTING.md#checks)).

## Common fields

Byte 0 to 3 of every TLP [cite: PCIe Base Spec, rev 3.0, §2.2.1]
[cite: PCIe Base Spec, rev 3.0, §2.2.6.6].

| Field | Position | Sent | On receive |
| --- | --- | --- | --- |
| Fmt, Type | byte 0 `[7:5]`, `[4:0]` | As [kinds](#kinds). | Selects the kind; undefined pairs are malformed. |
| TC | byte 1 `[6:4]` | 0 on requests and messages; on a completion, the request's TC. | Copied into the completion. Not 0 on a configuration or I/O request, or on a message whose code requires TC 0: malformed. |
| Attr bit 2 (ID-based ordering) | byte 1 `[2]` | 0 | Ignored. |
| TH (processing hints) | byte 1 `[0]` | 0 | Ignored, with the hint bits it would qualify. |
| TD (digest present) | byte 2 `[7]` | 0: no ECRC. | Not checked: no ECRC. A TLP with TD 1 arrives with one more DW after its data, the digest, which the `dll` has counted in its length check and delivers unchanged ([`dll` receive](../dll/HAS.md#receive)); `tl` ignores that DW and never stores it. |
| EP (poisoned) | byte 2 `[6]` | 0 | [Poisoned requests](ROUTING.md#checks) (after the Unsupported Request check), [poisoned completions](COMPLETIONS.md#checks). |
| Attr bits 1:0 (Relaxed Ordering, No Snoop) | byte 2 `[5:4]` | 0 on requests and messages; on a completion, the request's bits. | Copied into the completion; ordering ignores Relaxed Ordering. Not 0 on a configuration or I/O request: malformed. |
| AT (address type) | byte 2 `[3:2]` | 0: untranslated. | Not 0 on a memory request: Unsupported Request (no address translation). |
| Length | byte 2 `[1:0]`, byte 3 | DWs of data, 1 to 32; 0 on a TLP without data other than a memory read. | DWs, 0 meaning 1024. The `dll` has already checked it against the bytes that arrived and against the 128-byte payload limit ([`dll` receive](../dll/HAS.md#receive)). |

## Memory requests

[cite: PCIe Base Spec, rev 3.0, §2.2.4.1]
[cite: PCIe Base Spec, rev 3.0, §2.2.5]
[cite: PCIe Base Spec, rev 3.0, §2.2.6.2]
[cite: PCIe Base Spec, rev 3.0, §2.2.7]

| Field | Position | Sent (`rq`, interrupt writes) | On receive |
| --- | --- | --- | --- |
| Requester ID | bytes 4, 5 | `bus_num`, `dev_num`, function 0: byte 4 the bus, byte 5 `[7:3]` the device, `[2:0]` 0. | Kept for the completion. |
| Tag | byte 6 | A read: `rq_tag_i` in `[2:0]`, `[7:3]` 0. A write: 0. | Kept for a read's completion, all 8 bits. |
| Last DW BE | byte 7 `[7:4]` | `rq_last_be_i`; 0 for an interrupt write. | [Byte-enable rules](ROUTING.md#checks); passed to `cq` or `cfg`. |
| First DW BE | byte 7 `[3:0]` | `rq_first_be_i`; `1111` for an interrupt write. | As above. |
| Address, 3-DW header | bytes 8 to 11: address bits `[31:2]`, byte 11 `[1:0]` 0 | When address bits `[63:32]` are 0. | Bits `[31:2]`; byte 11 `[1:0]` ignored. |
| Address, 4-DW header | bytes 8 to 11 bits `[63:32]`, bytes 12 to 15 bits `[31:2]` | Otherwise. | Bits `[63:2]`. A 4-DW header with bits `[63:32]` 0 is decoded like a 3-DW one. |

An interrupt write is a 1-DW memory write of `msg_data` to `msg_addr`
([tl-cfg messages](../core/TL_CFG.md#messages)).

## Configuration requests

Received only [cite: PCIe Base Spec, rev 3.0, §2.2.7]. Requester ID,
Tag and byte enables are placed as for a memory request; the Last DW BE
must be 0 and Length 1 ([checks](ROUTING.md#checks)).

| Field | Position | On receive |
| --- | --- | --- |
| Bus Number | byte 8 | To `reg_req_bus` on a Type 0 write. |
| Device Number | byte 9 `[7:3]` | To `reg_req_dev` on a Type 0 write. |
| Function Number | byte 9 `[2:0]` | Not 0: Unsupported Request. |
| Extended Register Number | byte 10 `[3:0]` | `reg_req_addr[9:6]`. |
| Register Number | byte 11 `[7:2]` | `reg_req_addr[5:0]`. |

## Completions

[cite: PCIe Base Spec, rev 3.0, §2.2.9]
[cite: PCIe Base Spec, rev 3.0, §2.3.1.1]

| Field | Position | Sent | On receive |
| --- | --- | --- | --- |
| Completer ID | bytes 4, 5 | `bus_num`, `dev_num`, function 0. | Ignored. |
| Completion Status | byte 6 `[7:5]` | `000` Successful, `001` Unsupported Request. | `000`, `001`, `010` (Configuration Request Retry), `100` (Completer Abort); others read as `001` ([checks](COMPLETIONS.md#checks)). |
| BCM | byte 6 `[4]` | 0 | Ignored. |
| Byte Count | byte 6 `[3:0]`, byte 7 | [Byte count](COMPLETIONS.md#byte-count-and-lower-address); 0 means 4096. | Checked against the read's remaining bytes. |
| Requester ID | bytes 8, 9 | The request's. | Must be this function's (`bus_num`, `dev_num`, 0). |
| Tag | byte 10 | The request's, 8 bits. | Matched to an outstanding read: 0 to 7. |
| Lower Address | byte 11 `[6:0]` | [Lower address](COMPLETIONS.md#byte-count-and-lower-address). | Checked against the read's next byte. |

## Messages

Sent with a 4-DW header, TC 0, Attr 0, bytes 8 to 15 all 0, no data
[cite: PCIe Base Spec, rev 3.0, §2.2.8]
[cite: PCIe Base Spec, rev 3.0, §2.2.8.2]
[cite: PCIe Base Spec, rev 3.0, §2.2.8.3]. The routing subfield `rrr` is
Type `[2:0]`.

| Field | Position | Sent | On receive |
| --- | --- | --- | --- |
| Requester ID | bytes 4, 5 | `bus_num`, `dev_num`, function 0. | Ignored. |
| Tag | byte 6 | 0 | Ignored. |
| Message Code | byte 7 | ERR_COR `0x30`, ERR_NONFATAL `0x31`, ERR_FATAL `0x33` with routing `000` (to the Root Complex); PME_TO_Ack `0x1B` with routing `101` (gathered to the Root Complex). | Selects the [outcome](ROUTING.md#messages); INTx, power-management, error-signalling, Unlock and Set_Slot_Power_Limit codes require TC 0 ([checks](ROUTING.md#checks), step 6). The routing subfield and bytes 8 to 15 are not checked. |
