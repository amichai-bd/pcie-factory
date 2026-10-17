# pcie_core application interface

The interface between [`pcie_core`](HAS.md) and the user logic above it
(the application): four valid/ready TLP streams with decoded headers
(decision record [0004](../../decisions/0004-m3-grill.md#decisions), Q3),
an interrupt request port and status outputs. It is part of the `core`
HAS: the same sign-off covers it. The `tl` HAS implements the four
streams, the `cfg` HAS the interrupt port and the status outputs. The
names follow the completer and requester roles: `cq` completer requests
(host to application), `cc` completer completions, `rq` requester
requests (the application as bus master), `rc` requester completions.

## Conventions

- Every port is synchronous to `clk_i`. A beat is taken at an edge where
  valid and ready are both 1. Valid, once 1, stays 1 with the beat
  unchanged until taken; it may drop between beats of a packet.
- A packet is `sop` to `eop`. The header fields of a stream are held
  constant from its `sop` beat to its `eop` beat.
- A data beat is one DW. Bits `[7:0]` carry the byte at the lowest address
  of that DW (byte enable bit 0), `[31:24]` the highest. Beat k of a packet
  carries the DW at the packet's address plus 4k.
- Lengths count DWs. Byte enables follow the TLP header rules: with a
  length of 1 the last-DW byte enables are 0; with a length of 2 or more
  both are nonzero. Non-contiguous enables appear only in a 1-DW request,
  or in both fields of a 2-DW request that starts on an 8-byte boundary
  [cite: PCIe Base Spec, rev 3.0, §2.2.5].
- Every stream is idle while `dl_up_o` is 0: `tl` holds its valid and
  ready outputs at 0 ([link down](HAS.md#link-down)).

## Completer requests

Memory reads and writes from the host that hit BAR0, in the order the
[ordering rule](#ordering) gives. A write is `cq_len_o` beats of data; a
read is one beat whose data is don't care (`sop` and `eop` both 1).

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `cq_valid_o` | out | 1 | `clk_i` | A request beat is offered. |
| `cq_ready_i` | in | 1 | `clk_i` | The application takes the beat. It may stall freely ([credits](HAS.md#credits)). |
| `cq_sop_o`, `cq_eop_o` | out | 1, 1 | `clk_i` | First and last beat. |
| `cq_data_o` | out | 32 | `clk_i` | Write data; don't care for a read. |
| `cq_write_o` | out | 1 | `clk_i` | 1 memory write, 0 memory read. |
| `cq_addr_o` | out | `BAR0_ADDR_W` | `clk_i` | Byte offset of the first DW in BAR0; bits `[1:0]` are 0. |
| `cq_len_o` | out | 11 | `clk_i` | Length in DWs: 1 to 32 for a write (128-byte maximum payload), 1 to 1024 for a read. |
| `cq_first_be_o`, `cq_last_be_o` | out | 4, 4 | `clk_i` | First and last DW byte enables. |
| `cq_handle_o` | out | 3 | `clk_i` | A read's handle, 0 to 7, which its completion returns; don't care for a write. |

- `tl` offers a read only with a free handle; at most 8 reads wait for
  their completions. A handle is free from reset and again once the
  `eop` beat of its completion is taken on `cc`.
- A read of length 1 with `cq_first_be_o` 0 is a zero-length read: the
  application answers it with one DW of any value. A write with all byte
  enables 0 writes nothing.
- A request that would run past the end of BAR0 is never offered; the
  `tl` HAS states its outcome
  ([#286](https://github.com/amichai-bd/pcie-factory/issues/286)).

## Completer completions

The application's data for each read, as one packet per read handle.

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `cc_valid_i` | in | 1 | `clk_i` | A completion beat is offered. |
| `cc_ready_o` | out | 1 | `clk_i` | `tl` takes the beat. |
| `cc_sop_i`, `cc_eop_i` | in | 1, 1 | `clk_i` | First and last beat. |
| `cc_data_i` | in | 32 | `clk_i` | Read data. |
| `cc_handle_i` | in | 3 | `clk_i` | The read being answered: a handle `tl` offered and the application has not answered yet. |

- The packet has exactly `cq_len_o` beats of that read, in address order,
  with every byte present; `tl` sends only the bytes the request enabled.
- Reads may be answered in any order, one packet at a time.
- `tl` forms the completions: it splits the data into completions of at
  most 128 bytes, ending on 64-byte read completion boundaries, and fills
  in byte count, lower address, completer ID, requester ID and tag
  [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]. The application never sees
  them. Every read is answered with data: the application has no error
  status ([decisions](HAS.md#decisions)).

## Requester requests

Memory reads and writes the application issues as bus master. A write is
`rq_len_i` beats of data; a read is one beat whose data is don't care.

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `rq_valid_i` | in | 1 | `clk_i` | A request beat is offered. |
| `rq_ready_o` | out | 1 | `clk_i` | `tl` takes the beat. |
| `rq_sop_i`, `rq_eop_i` | in | 1, 1 | `clk_i` | First and last beat. |
| `rq_data_i` | in | 32 | `clk_i` | Write data; don't care for a read. |
| `rq_write_i` | in | 1 | `clk_i` | 1 memory write, 0 memory read. |
| `rq_addr_i` | in | 64 | `clk_i` | Byte address of the first DW; bits `[1:0]` are 0. `tl` uses a 3-DW header when bits `[63:32]` are 0, a 4-DW header otherwise [cite: PCIe Base Spec, rev 3.0, §2.2.4.1]. |
| `rq_len_i` | in | 6 | `clk_i` | Length in DWs, 1 to 32. |
| `rq_first_be_i`, `rq_last_be_i` | in | 4, 4 | `clk_i` | First and last DW byte enables. |
| `rq_tag_i` | in | 3 | `clk_i` | A read's tag, 0 to 7, not in use; don't care for a write. |

- Application obligations, which the RTL checks with assertions: a
  request is at most 128 bytes (the fixed maximum payload and maximum read
  request, decision F2) and never crosses a 4 KiB address boundary
  [cite: PCIe Base Spec, rev 3.0, §2.2.7]; a read's tag is not in use. A
  tag is in use from its read's `sop` until the `rc` beat that frees it.
  Both 128-byte limits hold under any value software programs, which is
  never below 128 bytes.
- `rq_ready_o` is 0 on a `sop` beat while `bus_master_en_o` is 0,
  `d_state_o` is not D0, or `dl_up_o` is 0: a function issues no memory
  request then [cite: PCIe Base Spec, rev 3.0, §7.5.1.1]
  [cite: PCIe Base Spec, rev 3.0, §5.3.1.4]. An offered
  request then waits.
- `tl` sends requests in the order taken; posted writes keep that order
  with the interrupt writes `cfg` asks for ([ordering](TL_CFG.md#messages)).

## Requester completions

The data and status for each of the application's reads.

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `rc_valid_o` | out | 1 | `clk_i` | A completion beat is offered. |
| `rc_ready_i` | in | 1 | `clk_i` | The application takes the beat. It may stall freely. |
| `rc_sop_o`, `rc_eop_o` | out | 1, 1 | `clk_i` | First and last beat of one completion. |
| `rc_data_o` | out | 32 | `clk_i` | Read data; don't care unless `rc_status_o` is 0. |
| `rc_tag_o` | out | 3 | `clk_i` | The read this completion belongs to. |
| `rc_status_o` | out | 2 | `clk_i` | 0 success, 1 Unsupported Request, 2 Completer Abort, 3 failed (completion timeout, or another error `tl` detects; [#286](https://github.com/amichai-bd/pcie-factory/issues/286)). |
| `rc_done_o` | out | 1 | `clk_i` | With `eop`: the read is finished and its tag is free. |

- A read's data may arrive as several completions, each a packet, in
  increasing address order [cite: PCIe Base Spec, rev 3.0, §2.3.1.1];
  together they carry the read's `rq_len_i` DWs, the first and last as
  the request's byte enables give. Completions of different reads may
  interleave packet by packet.
- A status other than 0 is a single beat with `sop`, `eop` and `rc_done_o`
  all 1; any data delivered for that read before it is void.
- `tl` holds every completion until the application takes it, so the
  application may stall: at most 8 reads of at most 128 bytes are
  outstanding, so the buffer is bounded (1 KiB).

## Ordering

The streams keep PCI Express ordering for what the application can see
[cite: PCIe Base Spec, rev 3.0, §2.4.1]:

- On `cq`, writes keep their arrival order, and a read never overtakes a
  write that arrived before it, so an application that applies writes in
  `cq` order answers each read with the data written before it. A write
  may overtake a read that is waiting for a free handle.
- A completion on `rc` never overtakes a write that arrived before it:
  `tl` offers it only once every such write has been taken on `cq`. It
  never waits for an earlier read. The application therefore never makes
  taking a `cq` write depend on taking an `rc` beat.
- On the transmit side, `tl` sends a completion formed from `cc` only
  after every posted write it took on `rq` before that completion's `sop`
  beat, so a host that reads a flag through BAR0 after the application
  wrote data to host memory sees the data. Interrupt writes keep the same
  order behind `rq` writes ([messages](TL_CFG.md#messages)).

## Interrupts

One request at a time, resolved by `cfg` against the MSI and MSI-X state
software has programmed (decision F4,
[resolution](TL_CFG.md#interrupt-resolution)).

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `irq_valid_i` | in | 1 | `clk_i` | An interrupt request. |
| `irq_ready_o` | out | 1 | `clk_i` | `cfg` takes it; 0 from then until its `irq_done_o`. |
| `irq_vector_i` | in | 6 | `clk_i` | MSI-X vector, 0 to `MSIX_TABLE_SIZE` − 1 (an application obligation, checked by an assertion). Ignored under MSI, which has one vector. |
| `irq_done_o` | out | 1 | `clk_i` | One-cycle pulse: the request is resolved. |
| `irq_status_o` | out | 2 | `clk_i` | With `irq_done_o`: 0 sent (handed to `tl`, which sends it after every posted request taken before), 1 pending (held in a pending bit and sent by `cfg` once allowed, with no further report), 2 dropped. |

A request is pending when it may not leave yet: under MSI-X when its
vector or the function is masked, and under MSI or MSI-X when bus
mastering is disabled or the function is not in D0 (decision record
[0004](../../decisions/0004-m3-grill.md#updates), Q11). No interrupt is
lost: a request is dropped only when neither MSI nor MSI-X is enabled.

## Status

Levels from `cfg`, at their reset values while `dl_up_o` is 0.

| Port | Dir | Width | Clock | Description |
| --- | --- | --- | --- | --- |
| `bus_master_en_o` | out | 1 | `clk_i` | Command register Bus Master Enable. |
| `mem_space_en_o` | out | 1 | `clk_i` | Command register Memory Space Enable. |
| `d_state_o` | out | 2 | `clk_i` | Power State: 0 D0, 3 D3hot (decision F5). |
| `max_payload_o` | out | 3 | `clk_i` | Device Control Max_Payload_Size as written (0 is 128 bytes, each step doubles). Informational: the application's limit stays 128 bytes. |
| `max_read_req_o` | out | 3 | `clk_i` | Device Control Max_Read_Request_Size as written, same encoding. Informational, as above. |

The application raises no error events: every error `core` reports is one
`tl` or `link` detects ([decisions](HAS.md#decisions)).
