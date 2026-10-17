# pcie_tl receive routing

What [`pcie_tl`](HAS.md) does with every TLP the `dll` delivers: the
checks in order, then the outcome. Part of the `tl` HAS; field positions
are on the [TLP page](TLP.md), the queues a request waits in on the
[HAS](ORDERING.md#receive-queues).

`tl` takes every TLP at once ([credits](ORDERING.md#credits)). Before it sees
one, the `dll` has already dropped every TLP with a bad LCRC, a length
that disagrees with its Fmt and Length, or more than 128 bytes of data
([`dll` receive](../dll/HAS.md#receive)), so `tl` never checks those
again.

## Outcomes

| Outcome | What `tl` does | Error event ([tl-cfg](../core/TL_CFG.md#error-events)) |
| --- | --- | --- |
| Served, `cq` | A BAR0 memory read or write, offered to the application. | None. |
| Served, `cfg` | A Type 0 configuration request, or a memory request that hits BAR2, as register accesses. | None. |
| Unsupported Request | Posted: dropped. Non-posted: answered with an [Unsupported Request completion](COMPLETIONS.md#unsupported-request-completions). | Bit 0 (posted) or 1 (non-posted). |
| Dropped | Accepted and discarded with no effect. | None. |
| Malformed | Dropped, never answered. | Bit 2. |
| Poisoned request | Posted: dropped. Non-posted: discarded without effect and answered with an Unsupported Request completion. | Bit 3 only. |
| Completion | To the [completion tracker](COMPLETIONS.md#requester-completions). | As the tracker states. |

Every outcome releases the request's credits when it
[leaves `tl`](ORDERING.md#credits) [cite: PCIe Base Spec, rev 3.0, §2.3.1]
[cite: PCIe Base Spec, rev 3.0, §2.7.2.2].

## Checks

In this order; the first that fails decides, and later ones are not
made [cite: PCIe Base Spec, rev 3.0, §2.3]
[cite: PCIe Base Spec, rev 3.0, §2.3.1].

| Step | Check | Fails |
| --- | --- | --- |
| 1. Kind | Fmt and Type are one of the [kinds](TLP.md#kinds); Fmt is not a prefix. | Malformed. |
| 2. Completion | A completion goes to the tracker; the steps below are for requests and messages. | |
| 3. Memory byte enables | Length 1: Last DW BE 0. Length 2 or more: neither byte-enable field 0, and, unless Length is 2 with address bit 2 at 0 (one aligned 8-byte block), First DW BE one of `1111`, `1110`, `1100`, `1000` and Last DW BE one of `0001`, `0011`, `0111`, `1111` (no gaps) [cite: PCIe Base Spec, rev 3.0, §2.2.5]. A memory read of Length 1 with First DW BE 0 is a valid zero-length read. | Malformed. |
| 4. 4 KiB boundary | A memory request's bytes, from its DW address for Length DWs, stay inside one aligned 4 KiB block [cite: PCIe Base Spec, rev 3.0, §2.2.7]. | Malformed. |
| 5. Configuration and I/O | Length 1, Last DW BE 0, TC 0, Attr bits 1:0 0 [cite: PCIe Base Spec, rev 3.0, §2.2.7]. | Malformed. |
| 6. Poisoned | EP is 0 on a request or message [cite: PCIe Base Spec, rev 3.0, §2.7.2.2]. | Poisoned request. |
| 7. Route | By kind, [below](#routing). | Unsupported Request, or a message outcome. |

The Length check against the maximum payload (128 bytes, fixed, decision
F2) is the `dll`'s, so a payload above 128 bytes never reaches `tl`. A
read's Length is not limited: the host's maximum read request size
applies to the host, and `tl` answers any read of up to 4 KiB (step 4
bounds it). TC and Attr of a memory request are not checked; `tl` keeps
them for the completion.

## Routing

| Request | Outcome |
| --- | --- |
| Memory read or write, AT 0, [hitting BAR0](#bar-decode) | Served, `cq`. |
| Memory read or write, AT 0, hitting BAR2 | Served, `cfg`: one access per DW ([register access](../core/TL_CFG.md#register-access)). |
| Memory read or write hitting no BAR, with AT not 0, or while the BAR decode is off | Unsupported Request [cite: PCIe Base Spec, rev 3.0, §5.3.1.4]. |
| Configuration Type 0, Function Number 0 | Served, `cfg`, one access; a write also carries the request's Bus and Device Number. |
| Configuration Type 0, Function Number not 0 | Unsupported Request (one function). |
| Configuration Type 1 | Unsupported Request (no bridge, decision record [0004](../../decisions/0004-m3-grill.md#decisions), F1). |
| I/O read or write | Unsupported Request (no I/O space, F1). |
| Locked memory read | Unsupported Request, answered with a locked completion (an Endpoint supports no locked access, F1) [cite: PCIe Base Spec, rev 3.0, §6.5.1]. |
| Atomic operation | Unsupported Request (no atomic completer). |
| Message | [Messages](#messages). |

A request that hits BAR0 can never run past its end: BAR0 is at least
4 KiB, aligned to its size, and step 4 keeps every request inside one
4 KiB block. The same holds for BAR2 (4 KiB).

### BAR decode

A memory request hits BARn when `mem_space_en` is 1, `d_state` is D0,
its address bits `[63:32]` are 0 and its bits `[31:k]` equal those of
`barn_base`, with k = `BAR0_ADDR_W` for BAR0 and 12 for BAR2, as the
[tl-cfg page](../core/TL_CFG.md#register-state) states. If software has
programmed the two BARs to overlap, BAR0 wins. The decode uses the request's
first DW address only; step 4 guarantees the rest of the request lies in
the same BAR. Outside D0 or with memory space disabled every memory
request is an Unsupported Request
[cite: PCIe Base Spec, rev 3.0, §7.5.1.1].

`cq_addr_o` is the request address bits `[BAR0_ADDR_W-1:0]` (bits `[1:0]`
0). A BAR2 access's `reg_req_addr` is address bits `[11:2]`, plus one per
DW of the request.

### Messages

By Message Code; the routing subfield is not checked
[cite: PCIe Base Spec, rev 3.0, §2.2.8].

| Message | Code | Outcome |
| --- | --- | --- |
| Unlock | `0x00` | Dropped: an Endpoint with no locked access ignores it [cite: PCIe Base Spec, rev 3.0, §2.2.8.4]. |
| PM_Active_State_Nak | `0x14` | Dropped: `tl` never requests a link power state, so there is nothing to refuse [cite: PCIe Base Spec, rev 3.0, §2.2.8.2]. |
| PME_Turn_Off | `0x19` | Answered with PME_TO_Ack (`0x1B`, routed gathered to the Root Complex) from the transmit request queue, in any D-state; nothing else changes. `link` stays in L0 ([`link` power states](../link/HAS.md#power-states)) [cite: PCIe Base Spec, rev 3.0, §2.2.8.2] [cite: PCIe Base Spec, rev 3.0, §5.3.3.2.1]. |
| Set_Slot_Power_Limit | `0x50` | Dropped; its value is not captured ([open questions](HAS.md#open-questions)) [cite: PCIe Base Spec, rev 3.0, §2.2.8.5]. |
| Vendor_Defined Type 1 | `0x7F` | Dropped [cite: PCIe Base Spec, rev 3.0, §2.2.8.6]. |
| Ignored messages (former hot-plug indicator and button messages) | `0x40`, `0x41`, `0x43`, `0x44`, `0x45`, `0x47`, `0x48` | Dropped [cite: PCIe Base Spec, rev 3.0, §2.2.8.7]. |
| Every other code: Vendor_Defined Type 0 (`0x7E`), INTx, PM_PME, PME_TO_Ack, ERR_*, LTR, OBFF and undefined codes | | Unsupported Request, posted (bit 0) [cite: PCIe Base Spec, rev 3.0, §2.2.8.6]. |

A poisoned message is a poisoned request (step 6): dropped, bit 3, and a
poisoned PME_Turn_Off is not answered.

## Outside D0

While `d_state` is not D0 ([tl-cfg outside D0](../core/TL_CFG.md#outside-d0)),
receive changes only through the BAR decode: every memory request is an
Unsupported Request, configuration requests are served, messages and
completions are handled as above. Every completion `tl` owes, Unsupported
Request completions included, is still sent.
