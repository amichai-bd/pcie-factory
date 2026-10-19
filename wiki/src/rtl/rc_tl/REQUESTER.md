# pcie_rc_tl requester

The requester side of [`pcie_rc_tl`](MAS.md): the endpoint commands
become TLPs, their completions are matched and checked, and the transmit
arbiter sends them with the completer's completions. Part of the `rc_tl`
MAS and changes with it; the [inbound page](INBOUND.md) holds the other
side.

## Byte enables

The rule the [`rc` HAS](../../arch/rc/HAS.md#outbound-routing) leaves to `rc_tl`: what `rc_tl` sends is a subset of
what the endpoint's receive check accepts
([`tl` checks](../../arch/tl/ROUTING.md#checks), step 3)
[cite: PCIe Base Spec, rev 3.0, §2.2.5].

| Command | Legal |
| --- | --- |
| Every command with `len` 1 | `last_be` 0. |
| `CFG_RD`, `CFG_WR`, `MEM_WR` | `first_be` not 0: no zero-length write or configuration access. |
| `MEM_RD`, `len` 1 | Any `first_be`; 0 is a zero-length read, answered with one DW. |
| `MEM_RD`, `len` 2 or more | `first_be` and `last_be` not 0; and, unless `len` is 2 and `addr[2]` is 0, `first_be` one of `1111`, `1110`, `1100`, `1000` and `last_be` one of `0001`, `0011`, `0111`, `1111`. |
| `HDR_WR`, `HOST_WR` | Any `first_be`; 0 writes nothing and finishes `OK`. |
| `HDR_RD`, `HOST_RD`, `ERR_RD`, `ERR_CLR` | `first_be` ignored. |

## Requests

The requester forms the TLP in flops and offers it to the
[transmit arbiter](#transmit). Field positions are on the
[TLP formats page](../../arch/tl/TLP.md); `rc_tl` sends a 3-DW header with
TC 0, Attr 0, TD 0, EP 0, AT 0, Requester ID {Primary Bus Number, device
0, function 0}, and:

| Command | Fmt, Type | Length, byte enables | Address DW |
| --- | --- | --- | --- |
| `CFG_RD`, `CFG_WR` | `000`, `010`; `00100` (Type 0) | 1; First `first_be`, Last 0 | Bus `addr[27:20]`, Device `[19:15]`, Function `[14:12]`, Extended Register `[11:8]`, Register `[7:2]` [cite: PCIe Base Spec, rev 3.0, §2.2.7] |
| `MEM_RD` | `000`, `00000` | `len`; `first_be`, `last_be` | `addr[31:2]` |
| `MEM_WR` | `010`, `00000` | 1; `first_be`, 0; then `data` | `addr[31:2]` |

- **Tags.** Byte 6 of a non-posted request is `{3'b000, tag_q}`; the
  5-bit `tag_q` steps at the edge the link takes the request's `sop`
  beat. Five bits are the tags allowed without Extended Tag
  [cite: PCIe Base Spec, rev 3.0, §2.2.6.2]; with one request outstanding
  any tag would do, and the rolling value makes a completion that comes
  after its request timed out unexpected, unless 32 more non-posted
  requests were sent in between.
- **Maximum read request** is 128 bytes: a `MEM_RD` is one TLP of `len`
  at most 32, never split (M2 Q6; [`rc` parameters](../../arch/rc/HAS.md#parameters)).
- **Writes.** A `MEM_WR` finishes `OK` with one beat in the cycle after
  the link takes its `eop` beat. A `CFG_WR` waits for its completion.

## Completion matching

The receive parser hands every completion's header to the requester at its
third header DW. It **matches** when an endpoint non-posted request is
outstanding and sent, the Requester ID and Tag (8 bits) equal the
request's, and it is not a locked completion. Anything else is an
unexpected completion: dropped, bit 4 [cite: PCIe Base Spec, rev 3.0, §2.3.2].
A matching completion is checked in order; the first row that applies
decides:

| Case | Result (last beat) | Error record |
| --- | --- | --- |
| Status Configuration Request Retry, configuration request | `CRS` | bit 2 |
| Status Configuration Request Retry, memory read | `BAD` (only a configuration request may be retried) | bit 6 |
| Status Unsupported Request | `UR` | bit 0 |
| Status Completer Abort | `CA` | bit 1 |
| Any other status but Successful (reserved) | `BAD` | bit 6 |
| Successful, malformed: a configuration read without data or with Length not 1; a configuration write with data; a memory read without data, with a Byte Count other than its remaining bytes, a Lower Address other than its next byte `[6:0]`, data past its last DW, or data ending before it but not on a 64-byte boundary [cite: PCIe Base Spec, rev 3.0, §2.3.1.1] | `BAD` | bit 6 |
| Successful, poisoned (EP 1) | `BAD` | bit 5 |
| Successful | Its data DWs, `OK` | none |

A decided failure ends the request with one beat in the cycle after the
completion's `eop`; its data is not delivered, and later completions for
it are unexpected. A Successful completion's data DWs go out as `OK`
beats, each in the cycle after the DW is taken from the link, a digest DW
never. The read advances its remaining bytes and next byte by what the
completion covers and finishes when none remain: that DW's beat has
`last` 1. A configuration read is one data beat; a configuration write
one `OK` beat with data 0.

## Completion timeout

One counter for the one outstanding request. It starts at the edge the
link takes the request's `sop` beat. If the request has not finished
after N = ⌊2^21 / `CPL_TIMEOUT_DIV`⌋ cycles, it ends with `TIMEOUT` and
bit 3, its beat in the next cycle. The counter does not expire while a
matched completion of the request is being received (third header DW to
`eop`); it then expires at that `eop`'s edge if the completion did not
finish the request. A completion arriving afterwards is unexpected.

| `CPL_TIMEOUT_DIV` | N | At 125 MHz |
| --- | --- | --- |
| 1 (FPGA) | 2097152 | 16.8 ms |
| 1024 (DV) | 2048 | 16.4 µs |

At 1 it lies between 50 µs and 50 ms, the range for a requester with no
programmable timeout, and above the 10 ms below which it should not expire
[cite: PCIe Base Spec, rev 3.0, §2.8]; at 50 MHz it is 41.9 ms, still in
range. The disable and programmable ranges do not exist (no PCI Express
capability in the [header](MAS.md#type-1-header)).

## Transmit

One TLP at a time. A head is eligible when the link shows 1 header
credit and its data credits (Length / 4 rounded up) for its class; all
ones passes. Priority: a requester posted write, then a completion, then
a requester non-posted request. A completion never passes an earlier
posted write, and a non-posted request waiting for credits never blocks
a completion [cite: PCIe Base Spec, rev 3.0, §2.4.1]. The choice holds
from `sop` to `eop`. `tlp_tx_*` come from an output stage of flops with a
skid slot, as in `tl` ([`tl` output stage](../tl/MAS.md#transmit-arbiter)),
so `tlp_tx_ready_i` reaches only the stage.
