# pcie_rc_tl inbound

The inbound side of [`pcie_rc_tl`](MAS.md): what it does with every TLP
the endpoint sends other than completions, the completer that answers its
reads, host memory, the MSI capture and the credits. Part of the `rc_tl`
MAS and changes with it; the [requester page](REQUESTER.md) holds the
other side.

## Receive checks

Every TLP is taken at once. The `dll` has dropped those with a bad LCRC,
a length that disagrees with the header, or more than 128 bytes of data
([`dll` receive](../../arch/dll/HAS.md#receive)). At the last header DW
the parser applies, in order, the endpoint's own malformed checks, so the
two ends judge a TLP alike [cite: PCIe Base Spec, rev 3.0, §2.3]:

| Step | Malformed (bit 6, dropped) when |
| --- | --- |
| 1. Kind | Fmt and Type are not a memory, locked memory, I/O, Type 0 or 1 configuration, atomic, message or completion kind, or Fmt is a prefix ([kinds](../../arch/tl/TLP.md#kinds)). |
| 2. Completion | Not a check: a completion goes to [completion matching](REQUESTER.md#completion-matching). |
| 3. Byte enables | A memory request breaks the `MEM_RD` rows of the [byte-enable rule](REQUESTER.md#byte-enables), Length for `len`: at Length 1 only Last BE 0 is required, so a 1-DW write or read with any First BE is legal. |
| 4. 4 KiB | A memory request's bytes leave one aligned 4 KiB block. |
| 5. Configuration, I/O | Length not 1, Last BE not 0, TC not 0 or Attr bits 1:0 not 0. |
| 6. Message TC | TC not 0 on an INTx, power-management, error, Unlock or Set_Slot_Power_Limit message [cite: PCIe Base Spec, rev 3.0, §2.2.8]. |

Then the [inbound routing](../../arch/rc/HAS.md#inbound-routing) rows,
first match deciding, on the request's whole DWs, from its address for
Length DWs: a request that passed step 4 lies in one aligned 4 KiB block,
so the 1 MiB-granular window and the 4 KiB MSI window hold it whole or not
at all, and host memory (which may be smaller than 4 KiB) holds it when
its first DW is inside and its DW offset plus Length is at most the host
memory's DWs. Locked, I/O, configuration and atomic requests are
Unsupported Requests whatever their address. Then poisoned: a request or message that routing
would serve, capture or record, with EP 1, records bit 5 only and has no
other effect, except that a poisoned non-posted request is answered with
an Unsupported Request completion, as the endpoint `tl` does
([`tl` outcomes](../../arch/tl/ROUTING.md#outcomes)); routing's
Unsupported Request outranks poisoned, bit 11 only
[cite: PCIe Base Spec, rev 3.0, §2.7.2.2]. A 4-DW address with bits 63:32
not 0 is outside every window and host memory. Every event sets its bit
in the cycle after the TLP's `eop`, and the decision is taken with the
header state at the last header DW's edge: an `HDR_WR` at that edge
counts for the next TLP.

| Received, after the checks | Done |
| --- | --- |
| Posted write to host memory | Each data DW written as it arrives, lanes by First BE, all four, then Last BE; a digest DW skipped. |
| 1-DW write into the MSI window, Bus Master Enable 1 | [Captured](#msi-capture) at its `eop`. |
| Posted Unsupported Request | Dropped, bit 11. |
| Message | ERR_COR, ERR_NONFATAL, ERR_FATAL: bits 7, 8, 9; any other code: bit 10. Dropped. |
| Non-posted: read of host memory; Unsupported Request (configuration, I/O, atomic, locked, or a memory read the routing refuses) | Into the [non-posted queue](#completer); an Unsupported Request sets bit 11. |
| Non-posted, poisoned, that routing would serve | Into the queue as an Unsupported Request entry (answered with an Unsupported Request completion); bit 5 only, not bit 11. |
| Non-posted, malformed | Into the queue as a drop entry, never answered, so its release goes the one way ([credits](#credits)). |

## Completer

The **non-posted queue** holds `t_np_ent` (kind: read, Unsupported
Request, locked Unsupported Request, drop; a memory request or not;
Requester ID, Tag, TC, Attr bits 1:0, address bits 15:2 (the host-memory
DW offset, and the DW within 4 KiB), Length, byte enables, data credits),
2^max(1, ⌈log2 `FC_NPH`⌉) + 1 entries in the shared queue ([reuse](MAS.md#reuse-of-tl-blocks)),
so a credit-correct endpoint never fills it (the queue's RAM needs one
address bit at least). The completer takes the head when idle: a drop leaves at once; an Unsupported Request becomes one
completion without data (the locked kind for a locked read), Byte Count
T and Lower Address B`[6:0]` for a memory read, 4 and 0 otherwise; a read
becomes its completions [cite: PCIe Base Spec, rev 3.0, §2.3.1.1]:

- For a read of n DWs at DW address A, first byte enables F and last L:
  total bytes T (n = 1: lowest to highest enabled byte of F, or 1 when F
  is 0; else 4n less the disabled bytes below F's first and above L's
  last enabled byte) and first byte B (A plus F's lowest enabled byte).
- Each completion starts where the previous ended (the first at A) and
  takes the rest when it is at most 32 DWs, else ends on the highest
  64-byte boundary within 128 bytes (read completion boundary 64 bytes).
  Byte Count is T for the first and B + T − S for one starting at S (4096
  sent as 0); Lower Address B`[6:0]`, then S`[6:0]`.
- Its DWs are read from host memory, one per cycle the port grants, the
  bytes F and L do not enable forced to 0, into the completion data queue
  (32 entries in the shared queue plus its head); when all are there its
  header goes to the transmit arbiter. The next completion starts when
  the link has taken this one's `eop`. No read is asked for while the
  link is down (`lrst`), so none returns to a cleared completer.
- Header: CplD (or Cpl), TC and Attr bits 1:0 of the request, Completer
  ID {Primary Bus Number, 0, 0} as the header holds it at the grant, BCM
  0, TD and EP 0, the request's Requester ID and Tag
  [cite: PCIe Base Spec, rev 3.0, §2.2.9].

## Host memory

One [`common_sync_ram`](../common/ram/common_sync_ram.md), `WIDTH` 32,
`LANES` 4, `ADDR_W` `HOST_MEM_ADDR_W` − 2: one port, one access per
cycle, a read's data in the cycle after its edge. A received write DW,
from the parser's flop, always wins it; the completer's reads and the
front end's `HOST_*` take turns when both wait, the one not granted most
recently first (the front end first after `rst`), so neither starves.
The writes never shut them out: one TLP writes at most 32 DWs (128-byte
payload) in consecutive cycles, and every TLP brings at least 3 header
beats that write nothing, so the port is free within 33 cycles and a
waiting access, losing at most one turn, is granted within 34: `u_mem`
asserts that neither waits 34 cycles (`a_host_progress`,
`a_cpl_progress`). The
endpoint's writes land in arrival order with no queue, so a capture, a
completion or a `HOST_RD` after a write always sees it
([host memory access](../../arch/rc/TL_SEQ.md#host-memory-access)); a
queued read may see a later write, which posted-passes-non-posted allows
[cite: PCIe Base Spec, rev 3.0, §2.4.1]. `HOST_WR` beats in the cycle
after its write edge, `HOST_RD` in the second cycle after its read edge.

## MSI capture

Four `t_rc_msi` entries in flops, in arrival order; `msi_valid_o` while
any is held, `msi_o` the oldest (0 when none is held). A capture arriving
when four are held after the edge's take is dropped and sets bit 12: a
take at the same edge makes room. Data is the DW as received, byte
enables not applied; offset is address bits 11:2. Reset by `rst` only
([MSI capture](../../arch/rc/TL_SEQ.md#msi-capture)).

`msi_valid_o` comes from the flops and `rst_i` only, never from
`msi_ready_i`: `rc_seq` drives `msi_ready_o` from `msi_valid_i`
combinationally, which TL_SEQ allows, so a path back would close a
combinational loop through the two sub-IPs (the
[#396 review](https://github.com/amichai-bd/pcie-factory/issues/375#issuecomment-6272274810)
interface note).

## Credits

Every received TLP is taken at once (`tlp_rx_ready_o` 1 out of reset),
the obligation for infinite completion credits
([`dll` receive](../../arch/dll/HAS.md#receive)). Posted TLPs need no
queue: each leaves at its `eop` (written, captured, recorded or dropped),
and `fc_rel_p_o` pulses in the next cycle with `fc_rel_pd_o` its data
credits. A non-posted TLP leaves when the completer takes it from the
queue; `fc_rel_np_o` pulses in the next cycle with its data credits. At
most one TLP of each class leaves per cycle, so nothing is owed. Releases
stop under `lrst`: the outputs are 0 at once. Completions need no
release. Every received TLP's class and data credits, a malformed one's
included, are those the `dll` counted, from its Fmt, Type and Length:
`rc_tl` takes them from `common_pkg`'s `credit_class` and `data_credits`
([#385](https://github.com/amichai-bd/pcie-factory/issues/385)), never
from a re-implementation.
