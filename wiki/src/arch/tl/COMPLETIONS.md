# pcie_tl completions

How [`pcie_tl`](HAS.md) answers reads as completer and tracks the reads it
issues as requester. Part of the `tl` HAS; field positions are on the
[TLP page](TLP.md), the application ports on the
[application interface page](../core/APP.md).

## Completer completions

`tl` forms every completion it sends: from `cc` for BAR0 reads, from
`reg_cpl_data` for configuration and BAR2 reads, and with no data for
configuration writes and Unsupported Requests. Each carries the
request's Requester ID, all 8 Tag bits, TC and Attr bits 1:0; Completer
ID `bus_num`, `dev_num`, function 0; BCM 0
[cite: PCIe Base Spec, rev 3.0, §2.2.9].

### Read handles

A BAR0 read is offered on `cq` with a free handle 0 to 7 (the lowest
free one when the read is first offered, which it keeps until taken);
`tl` keeps its Requester ID, Tag, TC, Attr, address bits `[6:0]`, Length
and byte enables under that handle. The handle is free
again once the `eop` beat of its `cc` packet is taken. A read waits in
the [non-posted queue](ORDERING.md#receive-queues) while all eight are in
use.

### Byte count and lower address

For a read of Length n DWs at DW address A with byte enables F (first)
and L (last) [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]:

- **Total bytes** T: with n = 1, the span from the lowest to the highest
  enabled byte of F (`1001` gives 4), or 1 when F is 0 (a zero-length
  read); with n ≥ 2, 4n less the disabled bytes below the first enabled
  byte of F, less the disabled bytes above the last enabled byte of L.
- **First byte** B = A plus the index of the lowest enabled byte of F (0
  when F is 0).
- The **first completion** has Byte Count T and Lower Address `B[6:0]`.
  Each later completion starts at a DW address S: its Byte Count is
  B + T − S (the bytes from S to the request's last enabled byte) and its
  Lower Address `S[6:0]`.
- Byte Count 4096 is sent as 0.

Data is whole DWs from A. Bytes the request did not enable (in the first
DW by F, in the last by L) are sent as 0, so only enabled bytes carry
data ([APP completer completions](../core/APP.md#completer-completions)).

### Splitting

A read's data goes out in one or more completions in increasing address
order. With the read completion boundary fixed at 64 bytes (decision F2,
[RCB](../core/TL_CFG.md#register-state)) and the maximum payload at 128
bytes, `tl` sends the fewest completions: each starts where the previous
one ended (the first at A); if the rest of the read fits in 128 bytes it
goes in this one completion, otherwise the completion ends at the highest
64-byte-aligned address that keeps it at most 128 bytes. A single
completion for all that remains is always allowed; only a completion
that leaves data for a later one must end on the boundary
[cite: PCIe Base Spec, rev 3.0, §2.3.1.1].

| Read | Completions (data bytes) |
| --- | --- |
| 128 bytes at a 64-byte-aligned address | 128 |
| 128 bytes at offset 4 | 128 |
| 512 bytes at offset 4 | 124, 128, 128, 128, 4 |
| 4096 bytes, aligned | 32 of 128 |
| 256 bytes at offset 4 | 124, 128, 4 |
| 8 bytes at `0x7C` | 8 |

The completions of one read go out in order. A configuration, BAR2 or
Unsupported Request completion may go out between two completions of a
`cc` packet. A `cc` beat that starts a completion is taken only when the
transmit completion buffer has room for that whole completion;
`cc_ready_o` is 0 otherwise.

### Configuration and BAR2 completions

| Request | Completion |
| --- | --- |
| Configuration read | One completion with data: 1 DW, the bytes of `reg_cpl_data` the request enabled (the others 0), Byte Count 4, Lower Address 0. |
| Configuration write | One completion without data, Successful, Byte Count 4, Lower Address 0, sent once `reg_cpl_valid` pulses. |
| BAR2 read | The DWs from `reg_cpl_data`, in address order, split and counted exactly as a BAR0 read. |
| BAR2 write | None: it is posted. |

The completer ID of a configuration completion is `bus_num` and `dev_num`
after the request: the completion for the first configuration write
already carries the numbers it captured
[cite: PCIe Base Spec, rev 3.0, §2.2.6.2].

### Unsupported Request completions

One completion without data, status Unsupported Request. For a memory
read (locked or not): Byte Count T and Lower Address `B[6:0]` as above;
for a locked read, the locked completion kind. For any other request:
Byte Count 4 and Lower Address 0 [cite: PCIe Base Spec, rev 3.0, §2.2.9].

## Requester requests

`tl` takes a request on `rq` only on a `sop` beat with `bus_master_en`
1, `d_state` D0 and room in its transmit posted line (a write) or
non-posted line (a read)
([APP requester requests](../core/APP.md#requester-requests)). It forms a
memory read or write with this function's Requester ID, TC 0, Attr 0, a
3-DW header when `rq_addr_i[63:32]` is 0 and a 4-DW header otherwise, the
request's byte enables and Length, and Tag `rq_tag_i` for a read or 0 for
a write ([TLP page](TLP.md#memory-requests)). The application's limits
(at most 128 bytes, inside one 4 KiB block, a free tag) are assertions in
the RTL, not checks with an outcome.

### Requester tags

Tags 0 to 7 (extended tags off, F2). A tag is in use from the edge that
takes its read's `sop` beat on `rq` until the edge that takes the `rc`
beat with `rc_done_o` 1; a new read may take the tag at that same edge.
The application chooses it and never reuses a tag in use. With all eight in
use, the application holds further reads.

## Requester completions

### Matching

A received completion matches an outstanding read when its Requester ID
is this function's (`bus_num`, `dev_num`, 0), its Tag is 0 to 7 with a
read in use and already sent to the `dll`, and it is not a locked
completion. Anything else is an **unexpected completion**: dropped,
bit 5, and no read changes [cite: PCIe Base Spec, rev 3.0, §2.3.2].

A completion is matched and checked, and its read changes, in the cycle
its third header DW arrives; a read that times out in that same cycle
has already ended, so the completion is unexpected. Its error event, as
every receive error, pulses in the cycle after its `eop`.

### Checks

On a matching completion, in this order:

| Case | Outcome |
| --- | --- |
| Status Configuration Request Retry | Malformed (bit 2): `tl` sends no configuration requests. Dropped; the read keeps waiting. |
| Status Unsupported Request, Completer Abort or a reserved value | The read ends: one `rc` beat with `rc_status_o` 1 (reserved values too) or 2, `rc_done_o` 1; bit 7 or 8. The tag is free. |
| Successful, with no data, with a Byte Count other than the read's remaining bytes, a Lower Address other than the read's next byte bits `[6:0]`, or data that ends past the read's last DW, or before it but not on a 64-byte boundary | Malformed (bit 2): dropped; the read keeps waiting. |
| Successful, poisoned | Bit 4. Its data is dropped and the read is marked failed; so is the data of the read's later completions. |
| Successful | Its data goes to `rc` as one packet. The read's remaining bytes and next byte advance by the bytes it covers. |

A read finishes when its remaining bytes reach 0. A finished read that
is not marked failed ends with the `rc_done_o` of its last completion; a
failed one ends with one beat, `rc_status_o` 3 and `rc_done_o` 1, in
place of its last completion's data. A 64-byte boundary is the finest a
completer may split on, so every legal split passes
[cite: PCIe Base Spec, rev 3.0, §2.3.1.1].

### Delivery on rc

Each successful completion is one `rc` packet of its Length DWs, in
arrival order; completions of different reads interleave packet by
packet. `tl` buffers them so the application may stall: at most 8 reads
of at most 128 bytes are outstanding, so the data held is at most 1 KiB.
A status beat (an error status, a failed read) takes its place in the
same order. A timeout's beat comes after every packet of its own read,
and may come after packets of other reads that arrived about the same
time.
Ordering against received writes is on the
[HAS](ORDERING.md#ordering).

### Non-posted pending

`tl_np_pending_o` is 1 while any read taken on `rq` is still waiting for
its final completion or its timeout: from the edge its `sop` beat is
taken until the edge at which the tracker ends it (its last completion
matched, an error status, or the timeout), whether or not the
application has taken the `rc` beats yet. It is 0 in reset. `cfg` shows it as the Device Status
Transactions Pending bit
([tl-cfg requester state](../core/TL_CFG.md#requester-state))
[cite: PCIe Base Spec, rev 3.0, §7.8.5].

### Completion timeout

Each sent read has a timer that starts at the edge the `dll` takes the
read's `sop` beat and is not restarted by its completions. A prescaler
pulses every P = 2^21 / (16 × `CPL_TIMEOUT_DIV`) cycles; a read times out
at the 17th pulse after it was sent, so between 16P and 17P cycles:

| `CPL_TIMEOUT_DIV` | Timeout | At 125 MHz |
| --- | --- | --- |
| 1 (FPGA, ASIC) | 2097152 to 2228224 cycles | 16.8 to 17.8 ms |
| 1024 (DV) | 2048 to 2176 cycles | 16.4 to 17.4 µs |

With no programmable ranges, the fixed timeout must lie between 50 µs and
50 ms; with the divider at 1 it does (16.8 to 17.8 ms), and it is also
above the 10 ms below which the spec recommends a timeout never expire
[cite: PCIe Base Spec, rev 3.0, §2.8]. With `CPL_TIMEOUT_DIV` above 1
(simulation only) the bound does not apply.

**Disable.** While `cpl_timeout_dis_i` (Device Control 2 Completion
Timeout Disable, from `cfg`) is 1, no read times out: every outstanding
read's pulse count is held at 0, so it waits for its completions
indefinitely and `tl_np_pending_o` stays 1 while it does. When the input
falls, each read still outstanding gets a full timeout from then
[cite: PCIe Base Spec, rev 3.0, §7.8.16]. `tl` registers the input, so
both take effect one cycle after it changes: the count restarts from
the cycle after the last in which the input was 1.

On a timeout the read ends with one `rc` beat, `rc_status_o` 3 and
`rc_done_o` 1 (data already delivered for it is void), bit 6 pulses in
the cycle it times out (one pulse for all reads timing out together),
and the tag is free once that beat is taken; a completion for it that
arrives after the timeout is unexpected. The timeout value is fixed: `cfg` advertises no programmable
range and supports the disable
([#285](https://github.com/amichai-bd/pcie-factory/issues/285)).
